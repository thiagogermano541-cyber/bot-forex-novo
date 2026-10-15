"""Revalidate one audited Claude answer without any Anthropic/API invocation."""
from datetime import datetime, timedelta

import httpx
from sqlalchemy import select
import structlog

from .analysis_contract import unpack_analysis
from .config import settings
from .db import AnalysisRecord, Audit, MarketSnapshot, Session, User
from .market_context import prepare_market
from .schemas import Analysis, Snapshot, utcnow
from .target_anchors import resolve_target_anchors

log = structlog.get_logger()


def revalidate(payload, snapshot, limits):
    prepared = prepare_market(snapshot, [], limits)
    result = Analysis.model_validate(resolve_target_anchors(unpack_analysis(payload), prepared.supplied))
    result.validate_grounding(prepared.supplied)
    refs = prepared.references
    for item in result.evidence + result.zones:
        item.source_ids = [refs.get(i, i) for i in item.source_ids]
    for item in result.scenarios:
        item.evidence_ids = [refs.get(i, i) for i in item.evidence_ids]
    result.narrative_sources = {key: [refs.get(i, i) for i in ids] for key, ids in result.narrative_sources.items()}
    if result.trade_plan:
        result.trade_plan.confirmation_source_ids = [refs.get(i, i) for i in result.trade_plan.confirmation_source_ids]
        for item in result.trade_plan.targets:
            item.source_ids = [refs.get(i, i) for i in item.source_ids]
    result.validate_grounding(prepared.original)
    return result


async def recover_record(session, audit, snapshot_row, model):
    details = audit.details
    if details.get("recovered_analysis_id"):
        return await session.get(AnalysisRecord, details["recovered_analysis_id"])
    if audit.action != "CLAUDE_INVALID" or not details.get("response_payload"):
        raise ValueError("An audited, rejected complete Claude response is required")
    snapshot = Snapshot.model_validate(snapshot_row.data)
    limits = details["context_candles"]
    result = revalidate(details["response_payload"], snapshot, limits)
    data = result.model_dump(mode="json")
    ref = dict(data_cutoff_utc=snapshot.captured_at.isoformat(),
               price=(snapshot.quote.bid + snapshot.quote.ask)/2 if snapshot.quote else None,
               spread_usd=snapshot.quote.ask-snapshot.quote.bid if snapshot.quote else None,
               valid_until=(snapshot.captured_at+timedelta(minutes=result.trade_plan.valid_for_minutes)).isoformat() if result.trade_plan else None,
               context_candles=limits, analysis_version=3, recovered_from_audit=audit.id, recovered_at=utcnow().isoformat())
    if result.trade_plan:
        plan = result.trade_plan
        entry = plan.entry_high if plan.side == "COMPRA" else plan.entry_low
        ref["gross_risk_reward"] = [abs(target.price-entry)/abs(entry-plan.stop_loss) for target in plan.targets]
    data["market_reference"] = ref
    # Keep the ORIGINAL request time and data. Never emit current-market alerts
    # or run paper trades for a response recovered after its market window.
    record = AnalysisRecord(snapshot_id=snapshot_row.id, asset=snapshot.asset, created_at=audit.created_at,
        decision=result.decision, data=data, mode="EXPERIMENTAL", model=details.get("model") or model,
        **{key: details[key] for key in ("input_tokens", "output_tokens", "cost_usd", "latency_ms")})
    session.add(record)
    await session.flush()
    audit.action = "CLAUDE_RECOVERED"
    audit.details = {**details, "recovered_analysis_id": record.id, "recovered_at": utcnow().isoformat()}
    session.add(Audit(action="RECOVER_ANALYSIS", actor=audit.actor,
        details={"analysis_id": record.id, "source_audit_id": audit.id, "snapshot_id": snapshot_row.id, "new_api_cost_usd": 0}))
    await session.commit()
    return record


async def run_once(redis):
    cfg = settings()
    if not cfg.verify_cached_response_at:
        return
    key = f"recover-response:{cfg.verify_cached_response_at}"
    if not await redis.set(key, "STARTED", nx=True):
        return
    try:
        start = datetime.fromisoformat(cfg.verify_cached_response_at)
        if start.tzinfo is None:
            raise ValueError("Recovery timestamp must have an explicit timezone")
        async with Session() as session:
            actor = await session.scalar(select(User).where(User.email == cfg.admin_email))
            if actor is None or actor.role != "admin":
                raise ValueError("Authorized administrator not found")
            rows = (await session.scalars(select(Audit).where(Audit.actor == actor.id,
                Audit.action.in_(["CLAUDE_INVALID", "CLAUDE_RECOVERED"]),
                Audit.created_at >= start, Audit.created_at < start+timedelta(minutes=1)))).all()
            if len(rows) != 1:
                raise ValueError(f"Expected one audited response in the authorized minute, found {len(rows)}")
            audit = rows[0]
            payload = audit.details.get("response_payload") or {}
            kinds = [r["strings"][0] for r in payload.get("rows", []) if r.get("section") == "ZONE" and r.get("strings")]
            log.info("cached_response_diagnostic", zone_labels=kinds, decision=payload.get("decision"),
                     rows=[{"section": r.get("section"), "strings": len(r.get("strings", [])),
                            "numbers": len(r.get("numbers", [])), "sources": len(r.get("sources", []))}
                           for r in payload.get("rows", [])])
            snapshot = await session.get(MarketSnapshot, audit.details["snapshot_id"])
            if not snapshot or snapshot.asset != "BTC/USD":
                raise ValueError("Original BTC snapshot not available")
            record = await recover_record(session, audit, snapshot, cfg.claude_model)
            origin = cfg.app_origin.rstrip("/")
            if not origin.startswith("https://"):
                raise ValueError("Public HTTPS origin required")
            async with httpx.AsyncClient(timeout=20) as client:
                login = await client.post(f"{origin}/api/auth/login", headers={"Origin": origin},
                    json={"email": cfg.admin_email, "password": cfg.admin_password.get_secret_value()})
                login.raise_for_status()
                try:
                    history = await client.get(f"{origin}/api/analyses")
                    history.raise_for_status()
                    if not any(item["id"] == record.id for item in history.json()):
                        raise ValueError("Recovered response missing from public history")
                finally:
                    await client.post(f"{origin}/api/auth/logout", headers={"Origin": origin}, json={})
            log.info("cached_response_recovered", status="PASS", public_history_verified=True, analysis_id=record.id, decision=record.decision,
                     original_request_at=record.created_at.isoformat(), new_api_cost_usd=0,
                     original_api_cost_usd=record.cost_usd, alerts_created=0,
                     plan_status=(record.data.get("trade_plan") or {}).get("status"))
        await redis.set(key, "PASS")
    except Exception as exc:
        log.warning("cached_response_recovered", status="FAIL", error_type=type(exc).__name__, reason=str(exc)[:2000])
        await redis.set(key, "FAIL")
