"""Reuse an unchanged waiting context; never turn quotes into trade confirmation."""
from datetime import datetime, timedelta
from .schemas import Decision


def reusable_wait(record, current, original, now):
    if record.mode != "EXPERIMENTAL" or (record.decision != Decision.WAIT and
            record.data.get("setup_quality", {}).get("status") != "REPROVADO"):
        return False
    ref = record.data.get("market_reference", {})
    if ref.get("analysis_version", 0) < 4 or ref.get("recovered_from_audit"):
        return False
    plan = record.data.get("trade_plan")
    expiry = datetime.fromisoformat(ref["valid_until"]) if ref.get("valid_until") else original.captured_at + timedelta(minutes=5)
    if now >= expiry or current.captured_at < original.captured_at:
        return False
    tf = plan["timeframe"] if plan else "M5"
    for period in {tf, "H1", "H4"}:
        old, new = original.candles.get(period, []), current.candles.get(period, [])
        if not old or not new or old[-1].id != new[-1].id:
            return False
    if plan and current.quote:
        price = (current.quote.bid + current.quote.ask)/2
        if (plan["side"] == "COMPRA" and price <= plan["stop_loss"]) or (plan["side"] == "VENDA" and price >= plan["stop_loss"]):
            return False
        # A target reached without the proposed entry changes the opportunity.
        if (plan["side"] == "COMPRA" and price >= plan["targets"][0]["price"]) or (plan["side"] == "VENDA" and price <= plan["targets"][0]["price"]):
            return False
    return True
