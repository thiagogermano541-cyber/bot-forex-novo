from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.claude import ClaudeClient, InvalidClaudeResponse
from app.config import Settings
from app.evaluation import evaluate_direction
from app.schemas import Snapshot


async def test_claude_uses_official_contract_and_grounding(snapshot, wait_analysis):
    client = ClaudeClient(Settings(anthropic_api_key="TEST_ONLY", claude_model="TEST_MODEL",
                           claude_input_usd_per_million=1, claude_output_usd_per_million=5))
    count = AsyncMock(return_value=SimpleNamespace(input_tokens=1000))
    aliased = wait_analysis.model_copy(deep=True)
    for evidence in aliased.evidence:
        evidence.source_ids = ["M1_C30"]
    for scenario in aliased.scenarios:
        scenario.evidence_ids = ["M1_C30"]
    create = AsyncMock(return_value=SimpleNamespace(stop_reason="tool_use", usage=SimpleNamespace(input_tokens=1000, output_tokens=100),
                       content=[SimpleNamespace(type="tool_use", name="submit_analysis", input=aliased.model_dump(mode="json"))]))
    client.client.messages.count_tokens = count
    client.client.messages.create = create
    result, usage = await client.analyze(snapshot, [])
    assert result.decision == "AGUARDAR"
    assert result.evidence[0].source_ids == [snapshot.candles["M1"][-1].id]
    assert usage["cost_usd"] == pytest.approx(0.0015)
    assert create.call_args.kwargs["tool_choice"]["name"] == "submit_analysis"
    assert create.call_args.kwargs["tools"][0]["strict"] is True
    await client.client.close()


async def test_truncated_response_is_never_published(snapshot):
    client = ClaudeClient(Settings(anthropic_api_key="TEST_ONLY", claude_model="TEST_MODEL",
                           claude_input_usd_per_million=1, claude_output_usd_per_million=5))
    client.client.messages.count_tokens = AsyncMock(return_value=SimpleNamespace(input_tokens=100))
    client.client.messages.create = AsyncMock(return_value=SimpleNamespace(stop_reason="max_tokens", content=[], usage=SimpleNamespace(input_tokens=100, output_tokens=3000)))
    with pytest.raises(InvalidClaudeResponse) as error:
        await client.analyze(snapshot, [])
    assert error.value.usage["output_tokens"] == 3000
    await client.client.close()


async def test_claude_revises_rejected_decision_and_accounts_both_calls(snapshot, wait_analysis):
    client = ClaudeClient(Settings(anthropic_api_key="TEST_ONLY", claude_model="TEST_MODEL",
                           claude_input_usd_per_million=1, claude_output_usd_per_million=5))
    accepted = wait_analysis.model_copy(deep=True)
    for evidence in accepted.evidence:
        evidence.source_ids = ["M1_C30"]
    for scenario in accepted.scenarios:
        scenario.evidence_ids = ["M1_C30"]
    rejected = accepted.model_dump(mode="json")
    rejected["decision"] = "CENÁRIO DE ALTA"
    rejected["missing_data"] = ["Confirmação ausente"]
    responses = [SimpleNamespace(stop_reason="tool_use", usage=SimpleNamespace(input_tokens=1000, output_tokens=100),
                 content=[SimpleNamespace(type="tool_use", id=f"tool_test_{index}", name="submit_analysis", input=value)])
                 for index, value in enumerate((rejected, accepted.model_dump(mode="json")))]
    client.client.messages.count_tokens = AsyncMock(return_value=SimpleNamespace(input_tokens=1000))
    client.client.messages.create = AsyncMock(side_effect=responses)
    result, usage = await client.analyze(snapshot, [])
    assert result.decision == "AGUARDAR"
    assert client.client.messages.create.call_count == 2
    assert usage["input_tokens"] == 2000
    assert usage["cost_usd"] == pytest.approx(0.003)
    feedback = client.client.messages.create.call_args.kwargs["messages"][-1]["content"][0]
    assert feedback["type"] == "tool_result" and feedback["is_error"] is True
    assert "empty missing_data" in feedback["content"]
    await client.client.close()


def test_fixed_horizon_does_not_use_later_price(snapshot):
    end = snapshot.model_copy(deep=True)
    end.captured_at += timedelta(minutes=61)
    candle = snapshot.candles["M1"][-1].model_copy(deep=True)
    candle.timestamp = snapshot.captured_at+timedelta(minutes=59)
    candle.id = "test:outcome"
    candle.close = 101
    later = candle.model_copy(deep=True)
    later.timestamp += timedelta(minutes=10)
    later.close = 10000
    later.id = "test:future"
    end.candles["M1"] = [candle, later]
    result = evaluate_direction(SimpleNamespace(decision="CENÁRIO DE ALTA"), snapshot, end, 60)
    assert result["outcome_price"] == 101
    assert result["source_candle_id"] == "test:outcome"


def test_replay_rejects_future_macro(snapshot):
    data = snapshot.model_dump(mode="json")
    data["macro"] = [{"id": "test:fred", "observation_date": "2026-10-15", "vintage": "2026-10-15", "value": 1}]
    with pytest.raises(ValueError, match="Future macro"):
        Snapshot.model_validate(data)
