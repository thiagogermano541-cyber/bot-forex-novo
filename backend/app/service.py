import hashlib
import asyncio
import json
from datetime import datetime, timedelta

import httpx
import structlog
from redis.asyncio import Redis
from sqlalchemy import select

from .claude import ClaudeClient, ClaudeUnavailable, InvalidClaudeResponse
from .budget import BudgetLedger
from .analysis_reuse import reusable_wait
from .setup_quality import assess_setup
from .config import settings
from .db import Alert, AnalysisRecord, Audit, MarketSnapshot, Session
from .providers.kraken import Kraken
from .schemas import ACTIVE_ASSETS, Analysis, Decision, Quote, Snapshot, utcnow
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
        if asset not in ACTIVE_ASSETS:
            raise ValueError("Somente BTC/USD está habilitado")
        async with self.redis.lock(f"jarvis:collect:{asset}", timeout=120, blocking_timeout=1):
            return await self._collect(asset)

    async def _collect(self, asset):
        if asset == "BTC/USD" and self.config.kraken_enabled:
            snapshot = await Kraken(self.http).snapshot()
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
        log.info("market_collected", asset=asset, snapshot_id=row.id, provider=snapshot.quote.provider if snapshot.quote else "Kraken",
                 candles={tf: len(rows) for tf, rows in snapshot.candles.items()}, quote_available=snapshot.quote is not None,
                 issues=snapshot.integrity_issues(self.config.stale_after_seconds))
        return row.id, snapshot

    async def latest_context(self, asset):
        async with Session() as session:
            row = await session.scalar(select(MarketSnapshot).where(MarketSnapshot.asset == asset)
                                       .order_by(MarketSnapshot.created_at.desc()).limit(1))
            if row:
                snapshot = Snapshot.model_validate(row.data)
                age = (utcnow()-snapshot.captured_at).total_seconds()
                if 0 <= age <= self.config.stale_after_seconds:
                    return row.id, snapshot
        return await self.collect(asset)

    async def run(self, asset, actor="worker", replay_id=None):
        if asset not in ACTIVE_ASSETS:
            raise ValueError("Somente BTC/USD está habilitado")
        # One global lock serializes budgets across API, worker and replays.
        async with self.redis.lock("jarvis:analysis", timeout=300, blocking_timeout=1):
            if await self.redis.get(f"jarvis:analysis-failure:{asset}"):
                raise BusyError("A última resposta foi recusada. Novas chamadas estão bloqueadas por até 2 minutos para proteger seus créditos; esta tentativa não chamou o Claude.")
            mode = "BACKTEST" if replay_id else "EXPERIMENTAL"
            if replay_id:
                async with Session() as session:
                    snapshot_row = await session.get(MarketSnapshot, replay_id)
                    if not snapshot_row or snapshot_row.asset != asset:
                        raise ValueError("Snapshot não encontrado para este ativo")
                    snapshot_id, snapshot = snapshot_row.id, Snapshot.model_validate(snapshot_row.data)
            else:
                snapshot_id, snapshot = await self.latest_context(asset)
            issues = snapshot.integrity_issues(self.config.stale_after_seconds)
            metadata = dict(input_tokens=0, output_tokens=0, cost_usd=0, latency_ms=0)
            model = "integrity-gate"
            async with Session() as session:
                if not replay_id and not issues:
                    previous = await session.scalar(select(AnalysisRecord).where(AnalysisRecord.asset == asset,
                        AnalysisRecord.mode == "EXPERIMENTAL").order_by(AnalysisRecord.created_at.desc()).limit(1))
                    if previous:
                        original = await session.get(MarketSnapshot, previous.snapshot_id)
                        if original and reusable_wait(previous, snapshot, Snapshot.model_validate(original.data), utcnow()):
                            previous.reuse_reason = "Contexto de confirmação sem novo candle; análise anterior mantida sem chamar o Claude. Custo desta solicitação: US$ 0."
                            log.info("analysis_reused", analysis_id=previous.id, asset=asset, new_api_cost_usd=0)
                            return previous
                cutoff = snapshot.captured_at
                past = (await session.scalars(select(AnalysisRecord).where(AnalysisRecord.asset == asset,
                         AnalysisRecord.created_at < cutoff, AnalysisRecord.mode == "EXPERIMENTAL")
                         .order_by(AnalysisRecord.created_at.desc()).limit(3))).all()
                memory = [{"id": r.id, "at": r.created_at.isoformat(), "decision": r.decision,
                           "summary": r.data["summary"], "scenarios": r.data["scenarios"],
                           "trade_plan": r.data.get("trade_plan")} for r in reversed(past)]
                if issues:
                    result = insufficient(issues)
                else:
                    if not self.config.claude_ready:
                        if not self.config.claude_enabled:
                            raise ClaudeUnavailable(f"Claude PAUSADO: {self.config.claude_pause_reason or 'Análises desativadas pelo operador'}")
                        raise ClaudeUnavailable("Claude NÃO CONFIGURADO: chave, modelo ou tarifas ausentes")
                    budget = BudgetLedger(session, self.config, actor, snapshot_id)
                    try:
                        async with asyncio.timeout(270):
                            result, metadata = await self.claude.analyze(snapshot, memory, budget=budget)
                    except InvalidClaudeResponse as exc:
                        log.warning("claude_response_rejected", asset=asset, reason=exc.reason,
                                    input_tokens=exc.usage["input_tokens"], output_tokens=exc.usage["output_tokens"])
                        session.add(Audit(action="CLAUDE_INVALID", actor=actor,
                                          details={"snapshot_id": snapshot_id, "reason": exc.reason, "model": self.config.claude_model,
                                                   "response_payload": exc.payload, **exc.usage}))
                        await session.commit()
                        await self.redis.set(f"jarvis:analysis-failure:{asset}", "INVALID_RESPONSE", ex=120)
                        raise
                    model = self.config.claude_model
                result_data = result.model_dump(mode="json")
                result_data["market_reference"] = {
                    "data_cutoff_utc": snapshot.captured_at.isoformat(),
                    "price": (snapshot.quote.bid + snapshot.quote.ask) / 2 if snapshot.quote else None,
                    "valid_until": (snapshot.captured_at + timedelta(minutes=result.trade_plan.valid_for_minutes)).isoformat() if result.trade_plan else None,
                    "context_candles": metadata.pop("context_candles", {}), "analysis_version": 4,
                    "spread_usd": snapshot.quote.ask - snapshot.quote.bid if snapshot.quote else None,
                }
                if result.trade_plan:
                    plan = result.trade_plan
                    worst_entry = plan.entry_high if plan.side == "COMPRA" else plan.entry_low
                    risk = abs(worst_entry - plan.stop_loss)
                    result_data["market_reference"]["gross_risk_reward"] = [abs(target.price - worst_entry) / risk for target in plan.targets]
                quality = assess_setup(result_data.get("trade_plan"), result_data["market_reference"]["spread_usd"], self.config.minimum_setup_rr)
                result_data["setup_quality"] = quality
                record = AnalysisRecord(snapshot_id=snapshot_id, asset=asset, decision=result.decision,
                                        data=result_data, mode=mode, model=model, **metadata)
                session.add(record)
                await session.flush()
                session.add(Audit(action="ANALYSIS", actor=actor, details={"analysis_id": record.id, "snapshot_id": snapshot_id, "mode": mode}))
                if mode == "EXPERIMENTAL" and quality["status"] != "REPROVADO":
                    await self.update_alerts(session, asset, result, snapshot, record.id)
                await session.commit()
            log.info("analysis_recorded", asset=asset, analysis_id=record.id, decision=result.decision,
                     model=model, mode=mode, cost_usd=record.cost_usd)
            return record

    async def update_alerts(self, session, asset, result, snapshot, analysis_id):
        if snapshot.integrity_issues(self.config.stale_after_seconds) or (utcnow()-snapshot.captured_at).total_seconds() > self.config.stale_after_seconds:
            return
        midpoint = (snapshot.quote.bid + snapshot.quote.ask) / 2
        open_alerts = (await session.scalars(select(Alert).where(Alert.asset == asset,
                       Alert.state.in_(["PENDENTE DE CONFIRMAÇÃO", "CONFIRMADO"])))).all()
        for alert in open_alerts:
            if alert.data.get("valid_until") and datetime.fromisoformat(alert.data["valid_until"]) <= utcnow():
                alert.state = "EXPIRADO"
                await enqueue(session, self.config, f"expire:{alert.id}", f"JARVIS • {asset}\nPlano EXPIRADO. Solicite uma nova análise.\nAnálise: {alert.analysis_id}")
                continue
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
        plan = result.trade_plan
        if not plan or snapshot.captured_at + timedelta(minutes=plan.valid_for_minutes) <= utcnow():
            return
        current = await self.redis.get(f"quote:{asset}")
        if current:
            quote = Quote.model_validate_json(current)
            age = (utcnow() - quote.timestamp).total_seconds()
            if age < 0 or age > self.config.stale_after_seconds:
                return
            midpoint = (quote.bid + quote.ask) / 2
        if not plan.entry_low <= midpoint <= plan.entry_high:
            return
        direction = "ALTA" if result.decision == Decision.BULL else "BAIXA"
        scenario = next(s for s in result.scenarios if s.direction == direction)
        fingerprint = hashlib.sha256(json.dumps({"asset": asset, "direction": direction,
                       "zones": [[z.low, z.high] for z in result.zones], "invalid": scenario.invalidation_price}, sort_keys=True).encode()).hexdigest()
        if any(a.data.get("fingerprint") == fingerprint for a in open_alerts if a.state not in ("INVALIDADO", "EXPIRADO")):
            return
        data = dict(verdict="COMPRA" if direction == "ALTA" else "VENDA", direction=direction, reference_price=midpoint, zones=[z.model_dump() for z in result.zones],
                    evidence=[e.model_dump() for e in result.evidence], confirmation=scenario.confirmation,
                    invalidation=scenario.invalidation, invalidation_price=scenario.invalidation_price,
                    risks=result.risks, fingerprint=fingerprint, evaluation="EXPERIMENTAL",
                    trade_plan=plan.model_dump(mode="json"),
                    valid_until=(snapshot.captured_at + timedelta(minutes=plan.valid_for_minutes)).isoformat())
        alert = Alert(analysis_id=analysis_id, asset=asset, data=data, state="CONFIRMADO")
        session.add(alert)
        await session.flush()
        text = (f"JARVIS • {asset} • Veredito final: {data['verdict']}\nCenário: {direction}\nUTC: {utcnow().isoformat()}\nReferência: {midpoint}\n"
                f"Regiões: {', '.join(f'{z.low}–{z.high}' for z in result.zones)}\n"
                f"Evidências: {'; '.join(e.description for e in result.evidence)}\n"
                f"Confirmação: {'; '.join(scenario.confirmation)}\nInvalidação: {scenario.invalidation_price} — {scenario.invalidation}\n"
                f"Entrada: {plan.entry_low}–{plan.entry_high}\nAlvos propostos: {', '.join(str(t.price) for t in plan.targets)}\n"
                f"Validade UTC: {data['valid_until']}\n"
                f"Riscos: {'; '.join(result.risks)}\nEstado: CONFIRMADO no snapshot; reavalie cotação e validade.\nAnálise: {analysis_id}\nExperimental; sem execução automática.")
        await enqueue(session, self.config, f"alert:{alert.id}", text)


def serialize(record):
    data = {c.name: (v.isoformat() if isinstance(v, datetime) else v)
            for c in record.__table__.columns if (v := getattr(record, c.name)) is not None}

    if isinstance(record, AnalysisRecord):
        quality = assess_setup(record.data.get("trade_plan"), record.data.get("market_reference", {}).get("spread_usd"), settings().minimum_setup_rr)
        data["data"] = {**record.data, "setup_quality": quality}
        data["model_verdict"] = {Decision.BULL: "COMPRA", Decision.BEAR: "VENDA"}.get(record.decision, "AGUARDAR")
        if getattr(record, "reuse_reason", None):
            data.update(reused=True, request_cost_usd=0, status=record.reuse_reason)
        data["verdict"] = {Decision.BULL: "COMPRA", Decision.BEAR: "VENDA"}.get(record.decision, "AGUARDAR")
        if quality["status"] == "REPROVADO":
            data["verdict"] = "AGUARDAR"
    return data
