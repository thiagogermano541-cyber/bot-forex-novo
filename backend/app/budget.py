"""Reserve each actual request, then settle its usage. Unknown usage stays held."""
from sqlalchemy import func, select

from .claude import ClaudeUnavailable
from .db import Audit
from .schemas import utcnow


async def reserved_today(session):
    day = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    # Older completed calls retained their ceiling even after usage was known.
    return await session.scalar(select(func.coalesce(func.sum(func.coalesce(
        Audit.details["known_cost_usd"].as_float(), Audit.details["reserved_usd"].as_float())), 0))
        .where(Audit.action == "AI_BUDGET", Audit.created_at >= day))


class BudgetLedger:
    def __init__(self, session, config, actor, snapshot_id):
        self.session, self.config = session, config
        self.actor, self.snapshot_id = actor, snapshot_id
        self.current = None

    async def reserve(self):
        cfg = self.config
        ceiling = ((cfg.claude_input_token_limit + 256) * cfg.claude_input_usd_per_million
                   + cfg.claude_max_tokens * cfg.claude_output_usd_per_million) / 1_000_000
        if cfg.enforce_daily_budget and await reserved_today(self.session) + ceiling > cfg.daily_ai_budget_usd:
            raise ClaudeUnavailable("Limite diário de IA alcançado. A coleta de BTC continua; ajuste o limite nas configurações do servidor.")
        self.current = Audit(action="AI_BUDGET", actor=self.actor, details={
            "snapshot_id": self.snapshot_id, "reserved_usd": ceiling,
            "status": "RESERVED", "ledger_version": 2})
        self.session.add(self.current)
        await self.session.commit()

    async def settle(self, cost_usd):
        if self.current is None:
            return
        self.current.details = {**self.current.details, "status": "COMPLETE",
                                "reserved_usd": cost_usd, "known_cost_usd": cost_usd}
        await self.session.commit()
        self.current = None
