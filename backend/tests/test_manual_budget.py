import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.budget import BudgetLedger, reserved_today
from app.claude import ClaudeUnavailable
from app.config import Settings
from app.db import Audit, Base
from app.worker import analysis_loop


async def test_manual_worker_never_requests_ai(monkeypatch):
    service = SimpleNamespace(config=SimpleNamespace(analysis_interval_seconds=300,
                              claude_ready=True, analysis_on_demand_only=True), run=AsyncMock())
    monkeypatch.setattr("app.worker.asyncio.sleep", AsyncMock(side_effect=asyncio.CancelledError))
    with pytest.raises(asyncio.CancelledError):
        await analysis_loop(service)
    service.run.assert_not_awaited()


async def test_each_call_reserves_and_settles_without_blocking_for_unused_revision():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    cfg = Settings(daily_ai_budget_enabled=True, daily_ai_budget_usd=0.4,
                   claude_input_usd_per_million=3, claude_output_usd_per_million=15, claude_max_tokens=6000)
    async with session_factory() as session:
        session.add_all([
            Audit(action="AI_BUDGET", actor="test", details={"reserved_usd": 0.7, "known_cost_usd": 0.03, "status": "COMPLETE"}),
            Audit(action="AI_BUDGET", actor="test", details={"reserved_usd": 0.2, "status": "RESERVED"}),
        ])
        await session.commit()
        assert await reserved_today(session) == pytest.approx(0.23)
        ledger = BudgetLedger(session, cfg, "test", "test-snapshot")
        await ledger.reserve()  # One call fits, although two ceilings do not.
        assert ledger.current.details["status"] == "RESERVED"
        await ledger.settle(0.09)
        assert await reserved_today(session) == pytest.approx(0.32)
        with pytest.raises(ClaudeUnavailable, match="Limite diário"):
            await ledger.reserve()  # An optional revision now exceeds the cap.
        assert await reserved_today(session) == pytest.approx(0.32)
    await engine.dispose()


async def test_explicit_manual_mode_tracks_cost_without_an_artificial_daily_block():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    cfg = Settings(analysis_on_demand_only=True, daily_ai_budget_enabled=False, daily_ai_budget_usd=0,
                   claude_input_usd_per_million=3, claude_output_usd_per_million=15)
    async with session_factory() as session:
        ledger = BudgetLedger(session, cfg, "test", "test-snapshot")
        await ledger.reserve()
        await ledger.settle(0.08)
        assert await reserved_today(session) == pytest.approx(0.08)
    await engine.dispose()


def test_automatic_operation_always_enforces_a_daily_limit():
    assert Settings(analysis_on_demand_only=False, daily_ai_budget_enabled=False).enforce_daily_budget
