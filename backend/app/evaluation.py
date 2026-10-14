from datetime import timedelta

from sqlalchemy import select

from .db import AnalysisRecord, Evaluation, MarketSnapshot, PaperTrade
from .schemas import Decision, Snapshot, utcnow


def evaluate_direction(analysis, start: Snapshot, end: Snapshot, horizon_minutes):
    cutoff = start.captured_at + timedelta(minutes=horizon_minutes)
    eligible = [c for c in end.candles.get("M1", []) if cutoff <= c.timestamp+timedelta(minutes=1) < cutoff+timedelta(minutes=1)]
    if not eligible or not start.quote:
        return None
    candle = min(eligible, key=lambda c: c.timestamp)
    reference = (start.quote.bid+start.quote.ask)/2
    change = candle.close/reference-1
    direction = 1 if analysis.decision == Decision.BULL else -1
    return {"kind": "OBSERVATIONAL_DIRECTION", "horizon_minutes": horizon_minutes,
            "reference_price": reference, "outcome_price": candle.close, "outcome_at": candle.timestamp.isoformat(),
            "return_fraction": change, "correct": bool(direction*change > 0),
            "source_candle_id": candle.id, "source_snapshot_at": end.captured_at.isoformat(),
            "caveat": "Direção em horizonte fixo; não mede execução, lucro ou probabilidade de acerto."}


async def evaluate_pending(session, horizon):
    records = (await session.scalars(select(AnalysisRecord).where(AnalysisRecord.asset == "BTC/USD", AnalysisRecord.decision.in_([Decision.BULL, Decision.BEAR])).order_by(AnalysisRecord.created_at).limit(1000))).all()
    for row in records:
        if await session.scalar(select(Evaluation).where(Evaluation.analysis_id == row.id)):
            continue
        start_row = await session.get(MarketSnapshot, row.snapshot_id)
        start = Snapshot.model_validate(start_row.data)
        cutoff = start.captured_at+timedelta(minutes=horizon)
        if utcnow() < cutoff:
            continue
        ends = (await session.scalars(select(MarketSnapshot).where(MarketSnapshot.asset == row.asset,
                    MarketSnapshot.created_at >= cutoff, MarketSnapshot.created_at <= cutoff+timedelta(minutes=65))
                    .order_by(MarketSnapshot.created_at).limit(30))).all()
        result = None
        for end_row in ends:
            result = evaluate_direction(row, start, Snapshot.model_validate(end_row.data), horizon)
            if result:
                result["mode"] = row.mode
                result["outcome_snapshot_id"] = end_row.id
                session.add(Evaluation(analysis_id=row.id, asset=row.asset, data=result))
                break
        paper = await session.scalar(select(PaperTrade).where(PaperTrade.analysis_id == row.id))
        if paper and result and paper.data.get("state") == "OPEN":
            data = dict(paper.data)
            side = 1 if row.decision == Decision.BULL else -1
            exit_price, entry = result["outcome_price"], data["entry_price"]
            gross = side*(exit_price-entry)*data["quantity"]
            fees = (entry+exit_price)*data["quantity"]*data["fee_bps"]/10000
            data.update(state="CLOSED", exit_price=exit_price, hypothetical_pnl=gross-fees,
                        caveat="SIMULAÇÃO: fechamento no horizonte; taxa declarada, sem slippage, funding ou execução real.")
            paper.data = data
    await session.commit()
