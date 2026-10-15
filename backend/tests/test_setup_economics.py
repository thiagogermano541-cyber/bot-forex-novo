from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace

import pytest

from app.analysis_reuse import reusable_wait
from app.setup_quality import assess_setup
from app.market_context import prepare_market
from app.schemas import TIMEFRAMES
from app.target_anchors import resolve_target_anchors


def plan():
    return dict(side="COMPRA",status="PENDENTE",timeframe="M5",entry_low=100,entry_high=101,stop_loss=99,
        targets=[dict(price=105,rationale="Extremo observado.",source_ids=["test"])],valid_for_minutes=20,
        trigger=dict(kind="ROMPIMENTO_ALTA",level=100,description="Fechamento acima do nível.",source_ids=["test"]))


def test_reported_user_plan_is_not_an_opportunity_and_prices_are_never_repaired():
    raw=plan()
    raw.update(entry_low=82388.10,entry_high=82439.60,stop_loss=82065.70,
               targets=[dict(price=82538.40)],trigger=dict(kind="ROMPIMENTO_ALTA",level=82503))
    before=deepcopy(raw)
    report=assess_setup(raw,.1)
    assert report["status"] == "REPROVADO"
    assert report["gross_risk_reward"][0] == pytest.approx(.2642417758759)
    assert len(report["reasons"]) == 2
    assert raw == before


def test_farther_second_target_never_masks_poor_first_target():
    raw=plan(); raw["targets"]=[dict(price=101.5),dict(price=150)]
    assert assess_setup(raw)["status"] == "REPROVADO"


def test_grounded_retest_is_explicit_and_entry_must_contain_level():
    raw=plan();raw["trigger"]["kind"]="RETESTE_ALTA"
    assert assess_setup(raw)["status"] == "APTO"
    raw["trigger"]["level"]=102
    assert assess_setup(raw)["status"] == "REPROVADO"


def test_short_breakout_is_symmetric_and_unbacked_volume_never_qualifies():
    raw=plan();raw.update(side="VENDA",entry_low=99,entry_high=100,stop_loss=101,targets=[dict(price=95)])
    raw["trigger"].update(kind="ROMPIMENTO_BAIXA",level=100)
    assert assess_setup(raw)["status"] == "APTO"
    raw["trigger"].update(volume_multiple=1.5,volume_baseline=None)
    assert assess_setup(raw)["status"] == "REPROVADO"


def test_trigger_binds_to_real_candle_and_volume_uses_real_baseline(snapshot):
    payload=dict(trade_plan=dict(timeframe="M5",targets=[],trigger=dict(kind="ROMPIMENTO_ALTA",
        anchor=dict(candle_id=snapshot.candles["H1"][-1].id,field="high"),description="Fechamento M5 acima do nível.",volume_multiple=1.5)))
    result=resolve_target_anchors(payload,snapshot)["trade_plan"]["trigger"]
    assert result["level"] == 102
    assert result["volume_baseline"] == 5
    assert len(result["volume_source_ids"]) == 20
    payload["trade_plan"]["trigger"]["anchor"]["candle_id"]="FAKE"
    with pytest.raises(ValueError,match="unknown closed candle"):
        resolve_target_anchors(payload,snapshot)


def test_reuse_never_confirms_and_stops_on_new_candle_or_expiry(snapshot):
    record=SimpleNamespace(decision="AGUARDAR",mode="EXPERIMENTAL",data={"trade_plan":None,
        "market_reference":{"analysis_version":4}})
    now=snapshot.captured_at+timedelta(seconds=30)
    assert reusable_wait(record,snapshot,snapshot,now)
    changed=snapshot.model_copy(deep=True);changed.candles["M5"][-1].id="new-closed-candle"
    assert not reusable_wait(record,changed,snapshot,now)
    assert not reusable_wait(record,snapshot,snapshot,now+timedelta(minutes=5))
    record.decision="CENÁRIO DE ALTA"
    assert not reusable_wait(record,snapshot,snapshot,now)


def test_trade_compression_preserves_subseconds_and_drops_stale_memory_plans(snapshot):
    stamp=(snapshot.captured_at-timedelta(seconds=.123)).isoformat()
    snapshot.trades=[dict(id="trade:1",timestamp=stamp,price=100.12,volume=.234,side="b",provider="TEST ONLY")]
    prepared=prepare_market(snapshot,[dict(at="old",decision="AGUARDAR",summary="Comparação.",trade_plan=plan())],dict.fromkeys(TIMEFRAMES,30))
    table=prepared.payload["market_data"]["trades"]
    row=dict(zip(table["columns"],table["rows"][0]))
    assert row["timestamp_utc_seconds"] == (snapshot.captured_at-timedelta(seconds=.123)).timestamp()
    assert row["price"] == 100.12 and row["volume"] == .234
    assert prepared.references[row["id"]] == "trade:1"
    assert "trade_plan" not in prepared.payload["previous_analyses"][0]

async def test_rejected_plan_is_audited_without_alert_and_next_unchanged_request_is_free(monkeypatch,snapshot,confirmed_buy):
    from unittest.mock import AsyncMock
    import httpx
    from sqlalchemy import select,func
    from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
    from app.db import Base,AnalysisRecord,MarketSnapshot
    from app.service import AnalysisService,serialize
    from app.config import Settings
    engine=create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn: await conn.run_sync(Base.metadata.create_all)
    sessions=async_sessionmaker(engine,expire_on_commit=False)
    monkeypatch.setattr("app.service.Session",sessions)
    monkeypatch.setattr("app.service.utcnow",lambda:snapshot.captured_at+timedelta(seconds=5))
    class Lease:
        async def __aenter__(self): return self
        async def __aexit__(self,*args): return False
    redis=SimpleNamespace(lock=lambda *a,**k:Lease(),get=AsyncMock(return_value=None))
    async with sessions() as session:
        session.add(MarketSnapshot(id="original",asset="BTC/USD",created_at=snapshot.captured_at,data=snapshot.model_dump(mode="json")))
        await session.commit()
    async with httpx.AsyncClient() as http:
        service=AnalysisService(redis,http)
        service.config=Settings(anthropic_api_key="TEST_ONLY",claude_model="TEST_MODEL",claude_input_usd_per_million=3,claude_output_usd_per_million=15)
        service.latest_context=AsyncMock(return_value=("original",snapshot))
        usage=dict(input_tokens=1000,output_tokens=100,cost_usd=.0045,latency_ms=20,context_candles=dict.fromkeys(TIMEFRAMES,30))
        service.claude.analyze=AsyncMock(return_value=(confirmed_buy,usage))
        service.update_alerts=AsyncMock()
        try:
            first=await service.run("BTC/USD",actor="test")
            assert first.decision == "CENÁRIO DE ALTA"  # The model's proposal remains intact.
            assert serialize(first)["verdict"] == "AGUARDAR"
            assert first.data["setup_quality"]["status"] == "REPROVADO"
            service.update_alerts.assert_not_awaited()
            second=await service.run("BTC/USD",actor="test")
            assert second.id == first.id
            assert serialize(second)["request_cost_usd"] == 0
            assert serialize(second)["reused"] is True
            service.claude.analyze.assert_awaited_once()
            async with sessions() as session:
                assert await session.scalar(select(func.count(AnalysisRecord.id))) == 1
        finally:
            await service.claude.client.close()
    await engine.dispose()
