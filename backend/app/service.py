import hashlib
import json
from datetime import datetime, timezone

import httpx
import structlog
from redis.asyncio import Redis
from sqlalchemy import func, select

from .claude import ClaudeClient, ClaudeUnavailable, InvalidClaudeResponse
from .config import settings
from .db import Alert, AnalysisRecord, Audit, MarketSnapshot, Session
from .providers.kraken import Kraken
from .providers.macro import fred_observations
from .providers.oanda import Oanda
from .schemas import Analysis, Decision, Quote, Snapshot, utcnow
from .telegram import enqueue

log = structlog.get_logger()


class BusyError(Exception):
    pass


def insufficient(issues):
    return Analysis(decision=Decision.INSUFFICIENT, summary="Não há dados íntegros e atuais suficientes para interpretar o mercado.",
                    justification="O backend interrompeu a análise na verificação de integridade; não houve decisão direcional da IA.",
                    evidence=[], zones=[], scenarios=[], timeframe_context={}, conflicts=[], risks=["Não emitir alerta direcional."],
                    missing_data=issues, change_from_previous="Não comparável por ausência de dados válidos.")


class AnalysisService:
    def __init__(self, redis: Redis, client: httpx.AsyncClient):
        self.config = settings()
        self.redis, self.http = redis, client
        self.claude = ClaudeClient(self.config)

    async def collect(self, asset):
        if asset == "BTC/USD" and self.config.kraken_enabled:
            snapshot = await Kraken(self.http).snapshot()
        elif asset == "XAU/USD":
            snapshot = await Oanda(self.http, self.config).snapshot()
            if self.config.fred_api_key.get_secret_value():
                try:
                    snapshot.macro = await fred_observations(self.http, self.config.fred_api_key.get_secret_value())
                except Exception:
                    snapshot.limitations.append("FRED temporariamente indisponível")
            else:
                snapshot.limitations.append("Dados macro FRED NÃO CONFIGURADO")
        else:
            snapshot = Snapshot(asset=asset, captured_at=utcnow(), candles={}, issues=["Provedor NÃO CONFIGURADO"])
        cached_quote = await self.redis.get(f"quote:{asset}")
        if cached_quote:
            quote = Quote.model_validate_json(cached_quote)
            if quote.timestamp <= snapshot.captured_at and (not snapshot.quote or quote.timestamp > snapshot.quote.timestamp):
                snapshot.quote = quote
        async with Session() as session:
            row = MarketSnapshot(asset=asset, created_at=snapshot.captured_at, data=snapshot.model_dump(mode="json"))
            session.add(row)
            await session.commit()
        await self.redis.set(f"snapshot:{asset}", json.dumps({"id": row.id, "data": row.data}), ex=86400)
        return row.id, snapshot

    async def run(self, asset, actor="worker", replay_id=None):
        # One global lock serializes budgets across API, worker and replays.
        async with self.redis.lock("jarvis:analysis", timeout=300, blocking_timeout=1):
            mode = "BACKTEST" if replay_id else "EXPERIMENTAL"
            if replay_id:
                async with Session() as session:
                    snapshot_row = await session.get(MarketSnapshot, replay_id)
                    if not snapshot_row or snapshot_row.asset != asset:
                        raise ValueError("Snapshot não encontrado para este ativo")
                    snapshot_id, snapshot = snapshot_row.id, Snapshot.model_validate(snapshot_row.data)
            else:
                snapshot_id, snapshot = await self.collect(asset)
            issues = snapshot.integrity_issues(self.config.stale_after_seconds)
            metadata = dict(input_tokens=0, output_tokens=0, cost_usd=0, latency_ms=0)
            model = "integrity-gate"
            async with Session() as session:
                cutoff = snapshot.captured_at
                past = (await session.scalars(select(AnalysisRecord).where(AnalysisRecord.asset == asset,
                         AnalysisRecord.created_at < cutoff, AnalysisRecord.mode == "EXPERIMENTAL")
                         .order_by(AnalysisRecord.created_at.desc()).limit(3))).all()
                memory = [{"id": r.id, "at": r.created_at.isoformat(), "decision": r.decision,
                           "summary": r.data["summary"], "scenarios": r.data["scenarios"]} for r in reversed(past)]
                if issues:
                    result = insufficient(issues)
                else:
                    if not self.config.claude_ready:
                        if not self.config.claude_enabled:
                            raise ClaudeUnavailable(f"Claude PAUSADO: {self.config.claude_pause_reason or 'Análises desativadas pelo operador'}")
                        raise ClaudeUnavailable("Claude NÃO CONFIGURADO: chave, modelo ou tarifas ausentes")
                    day = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
                    reserved = await session.scalar(select(func.coalesce(func.sum(Audit.details["reserved_usd"].as_float()), 0)).where(Audit.action == "AI_BUDGET", Audit.created_at >= day))
                    worst = (self.config.claude_input_token_limit * self.config.claude_input_usd_per_million
                             + self.config.claude_max_tokens * self.config.claude_output_usd_per_million) / 1_000_000
                    if reserved + worst * 3 > self.config.daily_ai_budget_usd:
                        raise ClaudeUnavailable("Orçamento diário insuficiente para uma chamada com retries")
                    # Persist a conservative ceiling before external I/O. Unknown timeout usage
                    # remains charged against the daily quota, never reported as actual spend.
                    reservation = Audit(action="AI_BUDGET", actor=actor, details={"snapshot_id": snapshot_id, "reserved_usd": worst*3, "status": "RESERVED"})
                    session.add(reservation)
                    await session.commit()
                    try:
                        result, metadata = await self.claude.analyze(snapshot, memory)
                    except InvalidClaudeResponse as exc:
                        session.add(Audit(action="CLAUDE_INVALID", actor=actor, details={"snapshot_id": snapshot_id, **exc.usage}))
                        await session.commit()
                        raise
                    reservation.details = {**reservation.details, "status": "COMPLETE", "known_cost_usd": metadata["cost_usd"]}
                    model = self.config.claude_model
                record = AnalysisRecord(snapshot_id=snapshot_id, asset=asset, decision=result.decision,
                                        data=result.model_dump(mode="json"), mode=mode, model=model, **metadata)
                session.add(record)
                await session.flush()
                session.add(Audit(action="ANALYSIS", actor=actor, details={"analysis_id": record.id, "snapshot_id": snapshot_id, "mode": mode}))
                if mode == "EXPERIMENTAL":
                    await self.update_alerts(session, asset, result, snapshot, record.id)
                await session.commit()
            return record

    async def update_alerts(self, session, asset, result, snapshot, analysis_id):
        if snapshot.integrity_issues(self.config.stale_after_seconds):
            return
        midpoint = (snapshot.quote.bid + snapshot.quote.ask) / 2
        open_alerts = (await session.scalars(select(Alert).where(Alert.asset == asset,
                       Alert.state == "PENDENTE DE CONFIRMAÇÃO"))).all()
        for alert in open_alerts:
            invalid = alert.data["invalidation_price"]
            crossed = midpoint <= invalid if alert.data["direction"] == "ALTA" else midpoint >= invalid
            if crossed:
                alert.state = "INVALIDADO"
                await enqueue(session, self.config, f"invalidate:{alert.id}", f"JARVIS • {asset}\nCenário INVALIDADO por preço observado {midpoint}.\nAnálise: {alert.analysis_id}\nExperimental.")
            elif result.decision == Decision.WAIT:
                await enqueue(session, self.config, f"update:{alert.id}:{utcnow().date()}",
                              f"JARVIS • {asset}\nAtualização: AGUARDAR.\n{result.summary}\nAnálise: {analysis_id}")
        if result.decision not in (Decision.BULL, Decision.BEAR):
            return
        direction = "ALTA" if result.decision == Decision.BULL else "BAIXA"
        scenario = next(s for s in result.scenarios if s.direction == direction)
        fingerprint = hashlib.sha256(json.dumps({"asset": asset, "direction": direction,
                       "zones": [[z.low, z.high] for z in result.zones], "invalid": scenario.invalidation_price}, sort_keys=True).encode()).hexdigest()
        if any(a.data.get("fingerprint") == fingerprint for a in open_alerts if a.state != "INVALIDADO"):
            return
        data = dict(direction=direction, reference_price=midpoint, zones=[z.model_dump() for z in result.zones],
                    evidence=[e.model_dump() for e in result.evidence], confirmation=scenario.confirmation,
                    invalidation=scenario.invalidation, invalidation_price=scenario.invalidation_price,
                    risks=result.risks, fingerprint=fingerprint, evaluation="EXPERIMENTAL")
        alert = Alert(analysis_id=analysis_id, asset=asset, data=data)
        session.add(alert)
        await session.flush()
        text = (f"JARVIS • {asset} • {direction}\nUTC: {utcnow().isoformat()}\nReferência: {midpoint}\n"
                f"Regiões: {', '.join(f'{z.low}–{z.high}' for z in result.zones)}\n"
                f"Evidências: {'; '.join(e.description for e in result.evidence)}\n"
                f"Confirmação: {'; '.join(scenario.confirmation)}\nInvalidação: {scenario.invalidation_price} — {scenario.invalidation}\n"
                f"Riscos: {'; '.join(result.risks)}\nEstado: PENDENTE DE CONFIRMAÇÃO\nAnálise: {analysis_id}\nExperimental; sem execução automática.")
        await enqueue(session, self.config, f"alert:{alert.id}", text)


def serialize(record):
    return {c.name: (v.isoformat() if isinstance(v, datetime) else v)
            for c in record.__table__.columns if (v := getattr(record, c.name)) is not None}
