from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.claude import ClaudeClient, InvalidClaudeResponse
from app.config import Settings
from app.structured_schema import analysis_tool_schema
from app.target_anchors import resolve_target_anchors


def anchored_payload(analysis, candle_id):
    raw = analysis.model_dump(mode="json")
    raw["trade_plan"]["targets"] = [{"anchor": {"candle_id": candle_id, "field": "high"},
                                     "rationale": "Alvo na máxima do candle observado."}]
    return raw


def test_target_contract_requests_an_anchor_instead_of_an_unbound_price():
    schema = analysis_tool_schema({"H1_C30", "Q_CURRENT"})
    target = schema["$defs"]["SignalTarget"]
    assert set(target["properties"]) == {"anchor", "rationale"}
    assert schema["$defs"]["TargetAnchor"]["properties"]["field"]["enum"] == ["high", "low"]


@pytest.mark.parametrize("field", ["high", "low"])
def test_binding_copies_the_exact_selected_price_without_mutation(snapshot, confirmed_buy, field):
    candle = snapshot.candles["H1"][-1]
    raw = anchored_payload(confirmed_buy, candle.id)
    raw["trade_plan"]["targets"][0]["anchor"]["field"] = field
    before = deepcopy(raw)
    result = resolve_target_anchors(raw, snapshot)
    assert result["trade_plan"]["targets"][0]["price"] == getattr(candle, field)
    assert result["trade_plan"]["targets"][0]["source_ids"] == [candle.id]
    assert result["decision"] == raw["decision"]
    assert raw == before


@pytest.mark.parametrize("candle_id", ["H1_C99", "previous-analysis:C01", "test:quote"])
def test_binding_rejects_unknown_and_non_candle_sources(snapshot, confirmed_buy, candle_id):
    with pytest.raises(ValueError, match="unknown closed candle"):
        resolve_target_anchors(anchored_payload(confirmed_buy, candle_id), snapshot)


def test_binding_rejects_a_forged_numeric_price(snapshot, confirmed_buy):
    raw = anchored_payload(confirmed_buy, snapshot.candles["H1"][-1].id)
    raw["trade_plan"]["targets"][0]["price"] = 999999
    with pytest.raises(ValueError, match="Extra inputs"):
        resolve_target_anchors(raw, snapshot)


def test_validation_reports_independent_issues_together(snapshot, confirmed_buy):
    confirmed_buy.trade_plan.status = "PENDENTE"
    confirmed_buy.trade_plan.entry_high = 100.25
    confirmed_buy.trade_plan.stop_loss = 98
    confirmed_buy.trade_plan.targets[0].price = 200
    with pytest.raises(ValueError) as error:
        confirmed_buy.validate_grounding(snapshot)
    reason = str(error.value)
    assert "matching CONFIRMADO" in reason
    assert "outside entry region" in reason
    assert "match the selected scenario" in reason
    assert "targets[0]=200" in reason


def test_missing_directional_scenario_returns_validation_error(snapshot, confirmed_buy):
    confirmed_buy.scenarios = confirmed_buy.scenarios[1:]
    with pytest.raises(ValueError, match="All three scenarios"):
        confirmed_buy.validate_grounding(snapshot)


async def test_one_review_can_repair_all_issues_and_keep_a_grounded_pending_plan(snapshot, confirmed_buy):
    client = ClaudeClient(Settings(anthropic_api_key="TEST_ONLY", claude_model="TEST_MODEL",
        claude_input_usd_per_million=1, claude_output_usd_per_million=5))
    aliases = {c.id: f"{tf}_C{index:02d}" for tf, rows in snapshot.candles.items()
               for index, c in enumerate(rows, 1)}
    raw = anchored_payload(confirmed_buy, "H1_C30")
    for field, refs in (("evidence", "source_ids"), ("zones", "source_ids"), ("scenarios", "evidence_ids")):
        for item in raw[field]:
            item[refs] = [aliases[i] for i in item[refs]]
    raw["trade_plan"].update(status="PENDENTE", entry_high=100.25, stop_loss=98,
                              confirmation_source_ids=["M1_C30"])
    corrected = deepcopy(raw)
    corrected["decision"] = "AGUARDAR"  # Comes from the model's second response.
    responses = [SimpleNamespace(stop_reason="tool_use", usage=SimpleNamespace(input_tokens=1000, output_tokens=100),
        content=[SimpleNamespace(type="tool_use", id=f"test_{i}", name="submit_analysis", input=value)])
        for i, value in enumerate((raw, corrected))]
    client.client.messages.count_tokens = AsyncMock(return_value=SimpleNamespace(input_tokens=1000))
    client.client.messages.create = AsyncMock(side_effect=responses)
    try:
        result, usage = await client.analyze(snapshot, [])
        assert result.decision == "AGUARDAR"
        assert result.trade_plan.status == "PENDENTE"
        assert result.trade_plan.targets[0].price == snapshot.candles["H1"][-1].high
        assert result.trade_plan.targets[0].source_ids == [snapshot.candles["H1"][-1].id]
        feedback = json.loads(client.client.messages.create.call_args.kwargs["messages"][-1]["content"])["validation_review"]["validation_errors"]
        assert "matching CONFIRMADO" in feedback
        assert "outside entry region" in feedback
        assert "match the selected scenario" in feedback
        assert usage["input_tokens"] == 2000
        assert client.client.messages.create.call_count == 2
    finally:
        await client.client.close()


async def test_unrepaired_response_is_never_relabelled_or_published(snapshot, wait_analysis):
    client = ClaudeClient(Settings(anthropic_api_key="TEST_ONLY", claude_model="TEST_MODEL",
        claude_input_usd_per_million=1, claude_output_usd_per_million=5))
    raw = wait_analysis.model_dump(mode="json")
    raw["evidence"][0]["source_ids"] = ["M1_C99"]
    for scenario in raw["scenarios"]:
        scenario["evidence_ids"] = ["M1_C30"]
    response = SimpleNamespace(stop_reason="tool_use", usage=SimpleNamespace(input_tokens=1000, output_tokens=100),
        content=[SimpleNamespace(type="tool_use", id="bad_response", name="submit_analysis", input=raw)])
    client.client.messages.count_tokens = AsyncMock(return_value=SimpleNamespace(input_tokens=1000))
    client.client.messages.create = AsyncMock(return_value=response)
    try:
        with pytest.raises(InvalidClaudeResponse, match="análise consistente"):
            await client.analyze(snapshot, [])
        assert client.client.messages.create.call_count == 2
    finally:
        await client.client.close()
