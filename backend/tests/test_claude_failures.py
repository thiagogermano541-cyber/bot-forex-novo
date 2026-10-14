from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from anthropic import BadRequestError

from app.claude import ClaudeClient, ClaudeProviderFailure, provider_failure
from app.config import Settings


def rejected(message):
    response = httpx.Response(400, request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"))
    return BadRequestError(message, response=response, body={"error": {"type": "invalid_request_error", "message": message}})


async def test_provider_rejection_is_explicit_and_releases_unused_budget(snapshot):
    cfg = Settings(anthropic_api_key="TEST_ONLY", claude_model="TEST_MODEL",
                   claude_input_usd_per_million=3, claude_output_usd_per_million=15)
    client = ClaudeClient(cfg)
    client.client.messages.count_tokens = AsyncMock(return_value=SimpleNamespace(input_tokens=1000))
    client.client.messages.create = AsyncMock(side_effect=rejected("Unsupported schema property."))
    budget = SimpleNamespace(reserve=AsyncMock(), settle=AsyncMock())
    with pytest.raises(ClaudeProviderFailure, match="recusou o formato"):
        await client.analyze(snapshot, [], budget)
    budget.settle.assert_awaited_once_with(0)
    assert client.client.messages.create.call_count == 1
    await client.client.close()


def test_billing_failure_is_distinct_and_secrets_are_not_logged(monkeypatch):
    cfg = Settings(anthropic_api_key="TEST_ONLY_KEY", jwt_secret="TEST_ONLY_JWT", admin_password="TEST_ONLY_PASSWORD")
    logger = SimpleNamespace(warning=Mock())
    monkeypatch.setattr("app.claude.log", logger)
    error = rejected("credit balance too low: TEST_ONLY_KEY TEST_ONLY_JWT TEST_ONLY_PASSWORD")
    failure = provider_failure(error, cfg)
    assert "saldo ou limite de uso" in str(failure)
    reason = logger.warning.call_args.kwargs["reason"]
    assert "TEST_ONLY" not in reason
    assert reason.count("[REDACTED]") == 3


async def test_grammar_rejection_falls_back_to_json_tool_and_still_validates(snapshot, wait_analysis):
    cfg = Settings(anthropic_api_key="TEST_ONLY", claude_model="TEST_MODEL",
                   claude_input_usd_per_million=1, claude_output_usd_per_million=5)
    client = ClaudeClient(cfg)
    raw = wait_analysis.model_dump(mode="json")
    for evidence in raw["evidence"]:
        evidence["source_ids"] = ["M1_C30"]
    for scenario in raw["scenarios"]:
        scenario["evidence_ids"] = ["M1_C30"]
    response = SimpleNamespace(stop_reason="tool_use", usage=SimpleNamespace(input_tokens=1000, output_tokens=100),
        content=[SimpleNamespace(type="tool_use", name="submit_analysis", input=raw)])
    client.client.messages.count_tokens = AsyncMock(return_value=SimpleNamespace(input_tokens=1000))
    client.client.messages.create = AsyncMock(side_effect=[rejected("The compiled grammar is too large."), response])
    budget = SimpleNamespace(reserve=AsyncMock(), settle=AsyncMock())
    result, usage = await client.analyze(snapshot, [], budget)
    assert result.decision == "AGUARDAR"
    assert result.evidence[0].source_ids == [snapshot.candles["M1"][-1].id]
    assert usage["cost_usd"] == pytest.approx(0.0015)
    assert client.strict_schema_unavailable
    assert client.client.messages.create.call_args.kwargs["tools"][0]["strict"] is False
    assert budget.reserve.call_count == 2
    assert budget.settle.await_args_list[0].args == (0,)
    await client.client.close()
