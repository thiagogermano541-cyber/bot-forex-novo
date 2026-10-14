from datetime import datetime, timezone
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

TIMEFRAMES = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400}
Asset = Literal["BTC/USD"]
StoredAsset = Literal["BTC/USD", "XAU/USD"]
ACTIVE_ASSETS = ("BTC/USD",)


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
    asset: StoredAsset
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
            # A closed candle's OPEN can be almost two periods old while the
            # next candle is still forming. Measure that natural window before
            # applying the provider's late-data tolerance.
            elif self.captured_at.timestamp() - rows[-1].timestamp.timestamp() > 2 * seconds + stale_seconds:
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


class SignalTarget(StrictModel):
    price: float = Field(gt=0)
    rationale: str = Field(min_length=8, max_length=500)
    source_ids: list[str] = Field(min_length=1, max_length=3)


class TradePlan(StrictModel):
    side: Literal["COMPRA", "VENDA"]
    status: Literal["CONFIRMADO", "PENDENTE"]
    setup_type: str = Field(min_length=5, max_length=200)
    timeframe: Literal["M1", "M5", "M15", "M30", "H1", "H4"]
    entry_low: float = Field(gt=0)
    entry_high: float = Field(gt=0)
    stop_loss: float = Field(gt=0)
    confirmation: str = Field(min_length=10, max_length=800)
    confirmation_source_ids: list[str] = Field(max_length=3)
    targets: list[SignalTarget] = Field(min_length=1, max_length=3)
    valid_for_minutes: int = Field(ge=1, le=60)
    cancel_if: list[str] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def geometry(self):
        if self.entry_high < self.entry_low:
            raise ValueError("Inverted entry region")
        prices = [target.price for target in self.targets]
        if self.side == "COMPRA":
            if self.stop_loss >= self.entry_low or any(p <= self.entry_high for p in prices) or prices != sorted(set(prices)):
                raise ValueError("Buy plan requires stop below entry and increasing targets above entry")
        elif self.stop_loss <= self.entry_high or any(p >= self.entry_low for p in prices) or prices != sorted(set(prices), reverse=True):
            raise ValueError("Sell plan requires stop above entry and decreasing targets below entry")
        if self.status == "CONFIRMADO" and not self.confirmation_source_ids:
            raise ValueError("Confirmed setup requires observed confirmation sources")
        return self


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
    trade_plan: TradePlan | None = None

    def validate_grounding(self, snapshot: Snapshot):
        known = snapshot.evidence_ids()
        references = [i for e in self.evidence for i in e.source_ids]
        references += [i for z in self.zones for i in z.source_ids]
        references += [i for s in self.scenarios for i in s.evidence_ids]
        if self.trade_plan:
            references += self.trade_plan.confirmation_source_ids
            references += [i for target in self.trade_plan.targets for i in target.source_ids]
        unknown = sorted(set(references) - known)
        if unknown:
            raise ValueError(f"Claude cited unknown evidence: {unknown[:8]}")
        if self.decision != Decision.INSUFFICIENT:
            if {s.direction for s in self.scenarios} != {"ALTA", "BAIXA", "LATERAL"}:
                raise ValueError("All three scenarios are required")
            if set(self.timeframe_context) != set(TIMEFRAMES):
                raise ValueError("All timeframes are required")
        if self.decision in (Decision.BULL, Decision.BEAR):
            direction = "ALTA" if self.decision == Decision.BULL else "BAIXA"
            scenario = next(s for s in self.scenarios if s.direction == direction)
            if not self.evidence or self.missing_data or not self.zones or not scenario.invalidation_price:
                raise ValueError(f"Directional decision requires evidence, zones, empty missing_data and numeric invalidation: "
                                 f"evidence={len(self.evidence)}, zones={len(self.zones)}, missing_data={self.missing_data}, "
                                 f"invalidation_price={scenario.invalidation_price}")
            midpoint = (snapshot.quote.bid + snapshot.quote.ask) / 2 if snapshot.quote else 0
            if (direction == "ALTA" and scenario.invalidation_price >= midpoint) or (direction == "BAIXA" and scenario.invalidation_price <= midpoint):
                raise ValueError("Invalidation is inconsistent with reference price")
            plan = self.trade_plan
            if not plan or plan.status != "CONFIRMADO" or plan.side != ("COMPRA" if direction == "ALTA" else "VENDA"):
                raise ValueError("Directional decision requires a matching CONFIRMADO trade_plan; pending setups must AGUARDAR")
            candles_by_id = {c.id: c for rows in snapshot.candles.values() for c in rows}
            observed_tfs = {candles_by_id[i].timeframe for e in self.evidence if e.category == "OBSERVADO"
                            for i in e.source_ids if i in candles_by_id}
            if not observed_tfs.intersection({"H1", "H4"}) or not observed_tfs.intersection({"M1", "M5", "M15", "M30"}):
                raise ValueError("Directional decision requires observed candle evidence from a higher and a lower timeframe")
            if snapshot.candles[plan.timeframe][-1].id not in plan.confirmation_source_ids:
                raise ValueError("Confirmation must cite the latest closed candle in the plan timeframe")
            if not plan.entry_low <= midpoint <= plan.entry_high:
                raise ValueError("Current reference price is outside entry region; wait rather than chase price")
            if abs(plan.stop_loss - scenario.invalidation_price) > 0.01:
                raise ValueError("Stop loss must match the selected scenario invalidation_price")
        elif self.trade_plan and self.trade_plan.status == "CONFIRMADO":
            raise ValueError("AGUARDAR or insufficient data cannot describe a confirmed entry")
        if self.trade_plan:
            candles_by_id = {c.id: c for rows in snapshot.candles.values() for c in rows}
            for target in self.trade_plan.targets:
                if not any(i in candles_by_id and candles_by_id[i].low <= target.price <= candles_by_id[i].high
                           for i in target.source_ids):
                    raise ValueError("Each target must lie within the observed price region of a cited closed candle")
        return self
