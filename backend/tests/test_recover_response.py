from datetime import timedelta

import pytest
from conftest import as_wire
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.db import Base, AnalysisRecord, Alert, Audit, MarketSnapshot
from app.recover_response import recover_record, revalidate
from app.schemas import TIMEFRAMES


def cached_answer(wait_analysis):
    raw = wait_analysis.model_dump(mode="json")
    raw["evidence"][0]["source_ids"] = ["M1_C30"]
    for scenario in raw["scenarios"]:
        scenario["evidence_ids"] = ["M1_C30"]
    raw["zones"] = [{"kind": "RESISTÊNCIA", "low": 100, "high": 102,
        "explanation": "Região de resistência analisada pelo modelo.", "source_ids": ["H1_C30"]}]
    raw["narrative_sources"] = {"summary": ["M1_C30"], "timeframe:H1": ["H1_C30"]}
    return as_wire(raw)


def test_cached_answer_retains_zone_interpretation_and_exact_provider_sources(snapshot, wait_analysis):
    result = revalidate(cached_answer(wait_analysis), snapshot, dict.fromkeys(TIMEFRAMES, 30))
    assert result.decision == "AGUARDAR"
    assert result.zones[0].kind == "RESISTÊNCIA"
    assert result.zones[0].source_ids == [snapshot.candles["H1"][-1].id]
    assert result.narrative_sources["summary"] == [snapshot.candles["M1"][-1].id]


def test_cached_revalidation_still_rejects_invented_sources(snapshot, wait_analysis):
    wire = cached_answer(wait_analysis)
    next(row for row in wire["rows"] if row["section"] == "ZONE")["sources"] = ["H1_C99"]
    with pytest.raises(ValueError, match="unknown evidence"):
        revalidate(wire, snapshot, dict.fromkeys(TIMEFRAMES, 30))


async def test_recovery_is_idempotent_keeps_original_time_and_cost_and_creates_no_alerts(snapshot, wait_analysis):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as session:
            saved = MarketSnapshot(id="cached-snapshot", asset="BTC/USD", created_at=snapshot.captured_at,
                                   data=snapshot.model_dump(mode="json"))
            original_time = snapshot.captured_at + timedelta(minutes=2)
            audit = Audit(id="cached-audit", action="CLAUDE_INVALID", actor="test-admin", created_at=original_time,
                details={"snapshot_id": saved.id, "response_payload": cached_answer(wait_analysis),
                         "context_candles": dict.fromkeys(TIMEFRAMES, 30), "input_tokens": 1000,
                         "output_tokens": 100, "cost_usd": 0.0015, "latency_ms": 1234})
            session.add_all([saved, audit])
            await session.commit()
            recovered = await recover_record(session, audit, saved, "TEST_MODEL")
            again = await recover_record(session, audit, saved, "TEST_MODEL")
            assert again.id == recovered.id
            assert recovered.created_at == original_time
            assert recovered.cost_usd == 0.0015 and recovered.input_tokens == 1000
            assert recovered.data["market_reference"]["data_cutoff_utc"] == snapshot.captured_at.isoformat()
            assert recovered.data["market_reference"]["recovered_from_audit"] == audit.id
            assert audit.action == "CLAUDE_RECOVERED"  # Excluded from failed-call cost sum; record now owns that cost.
            assert await session.scalar(select(func.count(AnalysisRecord.id))) == 1
            assert await session.scalar(select(func.count(Alert.id))) == 0
    finally:
        await engine.dispose()
