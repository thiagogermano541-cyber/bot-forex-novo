"""Compact, lossless OHLCV and observed facts. No signal decisions are made here."""
from dataclasses import dataclass
from datetime import timedelta

from .schemas import Snapshot, TIMEFRAMES


@dataclass
class PreparedMarket:
    payload: dict
    supplied: Snapshot
    original: Snapshot
    references: dict[str, str]


def prepare_market(snapshot: Snapshot, memory: list[dict], limits: dict[str, int]) -> PreparedMarket:
    original = snapshot.model_copy(deep=True)
    original.candles = {tf: rows[-limits[tf]:] for tf, rows in original.candles.items()}
    original.trades = original.trades[-40:]
    supplied = original.model_copy(deep=True)
    references = {}
    for tf, rows in supplied.candles.items():
        for index, candle in enumerate(rows, 1):
            alias = f"{tf}_C{index:02d}"
            references[alias], candle.id = candle.id, alias
    for index, trade in enumerate(supplied.trades, 1):
        alias = f"TRADE_{index:02d}"
        references[alias], trade["id"] = trade["id"], alias
    if supplied.quote:
        references["Q_CURRENT"], supplied.quote.id = supplied.quote.id, "Q_CURRENT"
    if supplied.order_book:
        references["BOOK_CURRENT"], supplied.order_book["id"] = supplied.order_book["id"], "BOOK_CURRENT"
    context = supplied.model_dump(mode="json")
    context["candles"] = {
        tf: {"columns": ["id", "timestamp_utc_seconds", "open", "high", "low", "close", "volume"],
             "providers": sorted({row.provider for row in rows}),
             "volume_kinds": sorted({row.volume_kind for row in rows}),
             "rows": [[row.id, int(row.timestamp.timestamp()), row.open, row.high, row.low, row.close, row.volume]
                      for row in rows]}
        for tf, rows in supplied.candles.items()
    }
    for tf, rows in supplied.candles.items():
        table = context["candles"][tf]
        if len(table["providers"]) > 1 or len(table["volume_kinds"]) > 1:
            table["columns"] += ["provider", "volume_kind"]
            for compact, original_row in zip(table["rows"], rows):
                compact += [original_row.provider, original_row.volume_kind]
    facts = {}
    for tf, rows in supplied.candles.items():
        if not rows:
            continue
        high = max(rows, key=lambda c: c.high)
        low = min(rows, key=lambda c: c.low)
        facts[tf] = {"closed_candles": len(rows), "window_start_utc": rows[0].timestamp.isoformat(),
                     "last_closed_at_utc": (rows[-1].timestamp + timedelta(seconds=TIMEFRAMES[tf])).isoformat(),
                     "last_close": {"price": rows[-1].close, "source_id": rows[-1].id},
                     "observed_high": {"price": high.high, "source_id": high.id},
                     "observed_low": {"price": low.low, "source_id": low.id},
                     "real_volume_sum": sum(row.volume for row in rows if row.volume_kind == "REAL" and row.volume is not None),
                     "tick_volume_sum": sum(row.volume for row in rows if row.volume_kind == "TICK" and row.volume is not None)}
    observed = {"timeframes": facts, "scope": "Somente a janela enviada e volumes locais ao provedor; extremos não comprovam ordens ocultas."}
    if supplied.quote:
        midpoint = (supplied.quote.bid + supplied.quote.ask) / 2
        observed["quote"] = {"source_id": supplied.quote.id, "reference_price": midpoint,
                             "spread_usd": supplied.quote.ask - supplied.quote.bid,
                             "spread_bps": (supplied.quote.ask - supplied.quote.bid) / midpoint * 10000}
    previous = [{**row, "scenarios": [{k: v for k, v in scenario.items() if k != "evidence_ids"}
                                      for scenario in row.get("scenarios", [])],
                 "trade_plan": {k: v for k, v in (row.get("trade_plan") or {}).items()
                                if k not in ("confirmation_source_ids", "targets")}} for row in memory]
    return PreparedMarket({"market_data": context, "observed_facts": observed,
                           "previous_analyses": previous}, supplied, original, references)
