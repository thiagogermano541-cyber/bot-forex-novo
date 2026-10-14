from datetime import datetime, timezone
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

TIMEFRAMES = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400}
Asset = Literal["BTC/USD", "XAU/USD"]


def utcnow():
    return datetime.now(timezone.utc)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Candle(StrictModel):
    id: str
    timestamp: datetime
    timeframe: str
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: float | None = Field(default=None, ge=0)
    volume_kind: Literal["REAL", "TICK", "UNAVAILABLE"]
    provider: str

    @model_validator(mode="after")
    def valid_ohlc(self):
        if self.timeframe not in TIMEFRAMES or self.timestamp.tzinfo is None:
            raise ValueError("Invalid timeframe or non-UTC timestamp")
        if self.low > min(self.open, self.close) or self.high < max(self.open, self.close) or self.low > self.high:
            raise ValueError("Inconsistent OHLC")
        return self


class Quote(StrictModel):
    id: str
    timestamp: datetime
    bid: float = Field(gt=0)
    ask: float = Field(gt=0)
    provider: str

    @model_validator(mode="after")
    def validate_quote(self):
        if self.ask < self.bid or self.timestamp.tzinfo is None:
            raise ValueError("Invalid quote")
        return self


class Snapshot(StrictModel):
    asset: Asset
    captured_at: datetime
    candles: dict[str, list[Candle]]
    quote: Quote | None = None
    trades: list[dict] = Field(default_factory=list)
    order_book: dict | None = None
    macro: list[dict] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def chronology(self):
        if self.captured_at.tzinfo is None:
            raise ValueError("Timezone required")
        for tf, rows in self.candles.items():
            if tf not in TIMEFRAMES:
                raise ValueError("Unsupported timeframe")
            if any(c.timeframe != tf or c.timestamp.timestamp() + TIMEFRAMES[tf] > self.captured_at.timestamp() for c in rows):
                raise ValueError("Only closed candles at cutoff are allowed")
            if any(a.timestamp >= b.timestamp for a, b in zip(rows, rows[1:])):
                raise ValueError("Candles must be unique and ordered")
        if self.quote and self.quote.timestamp > self.captured_at:
            raise ValueError("Future quote")
        for trade in self.trades:
            if datetime.fromisoformat(trade["timestamp"]) > self.captured_at:
                raise ValueError("Future trade")
        for row in self.macro:
            if datetime.fromisoformat(row["observation_date"]).date() > self.captured_at.date() or datetime.fromisoformat(row["vintage"]).date() > self.captured_at.date():
                raise ValueError("Future macro observation or vintage")
        if self.order_book and datetime.fromisoformat(self.order_book["received_at"]) > self.captured_at:
            raise ValueError("Future order book")
        return self

    def integrity_issues(self, stale_seconds: int):
        issues = list(self.issues)
        if not self.quote or (self.captured_at - self.quote.timestamp).total_seconds() > stale_seconds:
            issues.append("Preço ausente ou desatualizado")
        if self.quote and (self.captured_at - self.quote.timestamp).total_seconds() < -5:
            issues.append("Timestamp de preço futuro")
        for tf, seconds in TIMEFRAMES.items():
            rows = self.candles.get(tf, [])
            if len(rows) < 20:
                issues.append(f"{tf}: menos de 20 candles fechados")
            elif self.captured_at.timestamp() - rows[-1].timestamp.timestamp() > seconds + stale_seconds:
                issues.append(f"{tf}: candles desatualizados")
            if any((b.timestamp-a.timestamp).total_seconds() != seconds for a, b in zip(rows, rows[1:])):
                issues.append(f"{tf}: lacunas no histórico")
        return list(dict.fromkeys(issues))

    def evidence_ids(self):
        ids = {c.id for rows in self.candles.values() for c in rows}
        ids.update(t["id"] for t in self.trades)
        ids.update(m["id"] for m in self.macro)
        if self.quote:
            ids.add(self.quote.id)
        if self.order_book:
            ids.add(self.order_book["id"])
        return ids


class Decision(StrEnum):
    BULL = "CENÁRIO DE ALTA"
    BEAR = "CENÁRIO DE BAIXA"
    WAIT = "AGUARDAR"
    INSUFFICIENT = "DADOS INSUFICIENTES"


class Evidence(StrictModel):
    category: Literal["OBSERVADO", "INFERÊNCIA", "HIPÓTESE"]
    description: str = Field(min_length=4, max_length=1200)
    source_ids: list[str] = Field(min_length=1, max_length=20)


class Zone(StrictModel):
    kind: Literal["COMPRADORES", "VENDEDORES", "LIQUIDEZ POTENCIAL"]
    low: float = Field(gt=0)
    high: float = Field(gt=0)
    explanation: str
    source_ids: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def bounds(self):
        if self.high < self.low:
            raise ValueError("Inverted zone")
        return self


class Scenario(StrictModel):
    direction: Literal["ALTA", "BAIXA", "LATERAL"]
    thesis: str
    confirmation: list[str] = Field(min_length=1)
    invalidation: str
    invalidation_price: float | None = Field(default=None, gt=0)
    evidence_ids: list[str] = Field(min_length=1)


class Analysis(StrictModel):
    decision: Decision
    summary: str = Field(min_length=10, max_length=2500)
    justification: str = Field(min_length=10, max_length=3000)
    evidence: list[Evidence] = Field(max_length=18)
    zones: list[Zone] = Field(max_length=10)
    scenarios: list[Scenario] = Field(max_length=3)
    timeframe_context: dict[str, str]
    conflicts: list[str]
    risks: list[str] = Field(min_length=1)
    missing_data: list[str]
    change_from_previous: str

    def validate_grounding(self, snapshot: Snapshot):
        known = snapshot.evidence_ids()
        references = [i for e in self.evidence for i in e.source_ids]
        references += [i for z in self.zones for i in z.source_ids]
        references += [i for s in self.scenarios for i in s.evidence_ids]
        if set(references) - known:
            raise ValueError("Claude cited unknown evidence")
        if self.decision != Decision.INSUFFICIENT:
            if {s.direction for s in self.scenarios} != {"ALTA", "BAIXA", "LATERAL"}:
                raise ValueError("All three scenarios are required")
            if set(self.timeframe_context) != set(TIMEFRAMES):
                raise ValueError("All timeframes are required")
        if self.decision in (Decision.BULL, Decision.BEAR):
            direction = "ALTA" if self.decision == Decision.BULL else "BAIXA"
            scenario = next(s for s in self.scenarios if s.direction == direction)
            if not self.evidence or self.missing_data or not self.zones or not scenario.invalidation_price:
                raise ValueError("Directional decision requires evidence, zones and invalidation")
            midpoint = (snapshot.quote.bid + snapshot.quote.ask) / 2 if snapshot.quote else 0
            if (direction == "ALTA" and scenario.invalidation_price >= midpoint) or (direction == "BAIXA" and scenario.invalidation_price <= midpoint):
                raise ValueError("Invalidation is inconsistent with reference price")
        return self
