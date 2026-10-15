from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from conftest import as_wire
from app.claude import ClaudeClient, InvalidClaudeResponse
from app.config import Settings
from app.db import AnalysisRecord, Audit, Base
from app.schemas import Analysis, Evidence, TradePlan
from app.service import AnalysisService, BusyError
from app.structured_schema import analysis_tool_schema


def test_four_real_references_and_cancel_conditions_preserve_complete_plan(snapshot, confirmed_buy):
    raw = confirmed_buy.model_dump(mode="json")
    refs = [snapshot.candles[tf][-1].id for tf in ("M1", "M5", "H1", "H4")]
    conditions = ["Perda do suporte.", "Expiração do prazo.", "Spread deteriorado.", "Falha da reação proposta."]
    raw["trade_plan"].update(confirmation_source_ids=refs, cancel_if=conditions)
    result = Analysis.model_validate(raw).validate_grounding(snapshot)
    assert result.trade_plan.confirmation_source_ids == refs
    assert result.trade_plan.cancel_if == conditions
    raw["trade_plan"]["confirmation_source_ids"].append("MADE_UP")
    with pytest.raises(ValueError, match="unknown evidence"):
        Analysis.model_validate(raw).validate_grounding(snapshot)


def test_category_is_constrained_in_new_wire_schema():
    choices = analysis_tool_schema(set())["properties"]["rows"]["items"]["properties"]["section"]["enum"]
    assert {"OBSERVADO", "INFERÊNCIA", "HIPÓTESE"} <= set(choices)
    assert "EVIDENCE" not in choices


@pytest.mark.parametrize("label,expected", [("inferência", "INFERÊNCIA"), ("INFERENCIA", "INFERÊNCIA"),
    ("HIPOTESE", "HIPÓTESE"), (" OBSERVADO ", "OBSERVADO")])
def test_legacy_category_normalizes_spelling_only(label, expected):
    result = Evidence(category=label, description="Descrição da evidência de teste.", source_ids=["test"])
    assert result.category == expected


def test_ambiguous_category_and_missing_scenario_confirmation_remain_invalid(wait_analysis):
    with pytest.raises(ValueError):
        Evidence(category="COMPRA CERTA", description="Não é uma categoria de evidência.", source_ids=["test"])
    raw = wait_analysis.model_dump(mode="json")
    raw["scenarios"][0]["confirmation"] = []
    with pytest.raises(ValueError):
        Analysis.model_validate(raw)


async def test_default_invalid_answer_never_makes_a_second_paid_call(snapshot, wait_analysis):
    cfg = Settings(anthropic_api_key="TEST_ONLY", claude_model="TEST_MODEL",
                   claude_input_usd_per_million=3, claude_output_usd_per_million=15)
    assert cfg.claude_max_analysis_attempts == 1
    raw = wait_analysis.model_dump(mode="json")
    raw["evidence"][0]["source_ids"] = ["M1_C99"]
    for scenario in raw["scenarios"]:
        scenario["evidence_ids"] = ["M1_C30"]
    client = ClaudeClient(cfg)
    client.client.messages.count_tokens = AsyncMock(return_value=SimpleNamespace(input_tokens=1000))
    client.client.messages.create = AsyncMock(return_value=SimpleNamespace(stop_reason="tool_use",
        usage=SimpleNamespace(input_tokens=1000, output_tokens=100),
        content=[SimpleNamespace(type="tool_use", name="submit_analysis", input=as_wire(raw))]))
    budget = SimpleNamespace(reserve=AsyncMock(), settle=AsyncMock())
    try:
        with pytest.raises(InvalidClaudeResponse) as error:
            await client.analyze(snapshot, [], budget)
        assert client.client.messages.create.call_count == 1
        budget.reserve.assert_awaited_once()
        budget.settle.assert_awaited_once_with(pytest.approx(.0045))
        assert error.value.usage["cost_usd"] == pytest.approx(.0045)
        assert error.value.payload == as_wire(raw)
    finally:
        await client.client.close()


async def test_rejected_answer_cooldown_blocks_another_call_and_keeps_audit(monkeypatch, snapshot):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr("app.service.Session", sessions)
    class Lease:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
    redis = SimpleNamespace(lock=lambda *a, **k: Lease(), get=AsyncMock(side_effect=[None, "INVALID_RESPONSE"]), set=AsyncMock())
    async with httpx.AsyncClient() as http:
        service = AnalysisService(redis, http)
        service.config = Settings(anthropic_api_key="TEST_ONLY", claude_model="TEST_MODEL",
            claude_input_usd_per_million=3, claude_output_usd_per_million=15)
        service.latest_context = AsyncMock(return_value=("test-snapshot", snapshot))
        usage = dict(input_tokens=1000, output_tokens=100, cost_usd=.0045, latency_ms=20,
                     context_candles={tf: 30 for tf in snapshot.candles})
        service.claude.analyze = AsyncMock(side_effect=InvalidClaudeResponse(usage, reason="Invalid source", payload={"test": True}))
        try:
            with pytest.raises(InvalidClaudeResponse):
                await service.run("BTC/USD", actor="test")
            redis.set.assert_awaited_once_with("jarvis:analysis-failure:BTC/USD", "INVALID_RESPONSE", ex=120)
            with pytest.raises(BusyError, match="não chamou o Claude"):
                await service.run("BTC/USD", actor="test")
            service.claude.analyze.assert_awaited_once()
            service.latest_context.assert_awaited_once()
            async with sessions() as session:
                audit = await session.scalar(select(Audit).where(Audit.action == "CLAUDE_INVALID"))
                assert audit.details["cost_usd"] == .0045
                assert audit.details["response_payload"] == {"test": True}
                assert await session.scalar(select(func.count(AnalysisRecord.id))) == 0
        finally:
            await service.claude.client.close()
    await engine.dispose()
