import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.claude import ClaudeClient, InvalidClaudeResponse
from app.config import Settings
from app.schemas import TradePlan


def wire_plan(snapshot, confirmed_buy):
    aliases = {c.id: f"{tf}_C{i:02d}" for tf, rows in snapshot.candles.items() for i, c in enumerate(rows, 1)}
    raw = confirmed_buy.model_dump(mode="json")
    for field, refs in (("evidence", "source_ids"), ("zones", "source_ids"), ("scenarios", "evidence_ids")):
        for item in raw[field]:
            item[refs] = [aliases[i] for i in item[refs]]
    raw["trade_plan"]["confirmation_source_ids"] = ["M1_C30"]
    raw["trade_plan"]["targets"] = [{"anchor": {"candle_id": "H1_C30", "field": "low"},
                                     "rationale": "Região citada pelo modelo no primeiro rascunho."}]
    return raw


def response(raw):
    return SimpleNamespace(stop_reason="tool_use", usage=SimpleNamespace(input_tokens=1000, output_tokens=100),
        content=[SimpleNamespace(type="tool_use", id="test_plan", name="submit_analysis", input=raw)])


@pytest.mark.parametrize("remove_plan", [False, True])
async def test_real_failure_geometry_gets_exact_prices_and_model_can_repair_or_omit_plan(snapshot, confirmed_buy, remove_plan):
    raw = wire_plan(snapshot, confirmed_buy)
    corrected = deepcopy(raw)
    if remove_plan:
        corrected["trade_plan"] = None
        corrected["decision"] = "AGUARDAR"
    else:
        corrected["trade_plan"]["targets"][0]["anchor"]["field"] = "high"
    client = ClaudeClient(Settings(anthropic_api_key="TEST_ONLY", claude_model="TEST_MODEL",
        claude_input_usd_per_million=1, claude_output_usd_per_million=5))
    client.client.messages.count_tokens = AsyncMock(return_value=SimpleNamespace(input_tokens=1000))
    client.client.messages.create = AsyncMock(side_effect=[response(raw), response(corrected)])
    try:
        result, usage = await client.analyze(snapshot, [])
        messages = client.client.messages.create.call_args.kwargs["messages"]
        review = json.loads(messages[-1]["content"])["validation_review"]
        assert "targets[0] resolves to 99.0" in review["validation_errors"]
        assert "H1_C30" in review["validation_errors"]
        assert "must be > entry_high=101.0" in review["validation_errors"]
        assert review["rejected_plan"]["targets"][0]["anchor"]["field"] == "low"
        assert len(messages) == 2
        assert "summary" not in messages[-1]["content"]
        assert all(call.kwargs["tools"][0]["strict"] is True for call in client.client.messages.create.call_args_list)
        if remove_plan:
            assert result.decision == "AGUARDAR" and result.trade_plan is None
        else:
            assert result.decision == "CENÁRIO DE ALTA" and result.trade_plan.targets[0].price == 102
        assert usage["input_tokens"] == 2000
    finally:
        await client.client.close()


@pytest.mark.parametrize("bad_plan", ["null", [], {}, 42])
async def test_wrong_plan_types_never_become_a_valid_analysis(snapshot, confirmed_buy, bad_plan):
    raw = wire_plan(snapshot, confirmed_buy)
    raw["trade_plan"] = bad_plan
    client = ClaudeClient(Settings(anthropic_api_key="TEST_ONLY", claude_model="TEST_MODEL",
        claude_input_usd_per_million=1, claude_output_usd_per_million=5))
    client.client.messages.count_tokens = AsyncMock(return_value=SimpleNamespace(input_tokens=1000))
    client.client.messages.create = AsyncMock(return_value=response(raw))
    try:
        with pytest.raises(InvalidClaudeResponse):
            await client.analyze(snapshot, [])
        assert client.client.messages.create.call_count == 2
    finally:
        await client.client.close()


def test_duplicate_anchor_prices_report_every_geometry_issue(confirmed_buy):
    raw = confirmed_buy.trade_plan.model_dump()
    raw.update(stop_loss=101, targets=[{"price": 100, "source_ids": ["H1_C30"],
        "rationale": "Duas fontes citadas que representam o mesmo preço."}] * 2)
    with pytest.raises(ValueError) as error:
        TradePlan.model_validate(raw)
    reason = str(error.value)
    assert "stop_loss < entry_low" in reason
    assert "targets[0] resolves to 100.0" in reason
    assert "targets[1] resolves to 100.0" in reason
    assert "duplicate prices" in reason
