"""Bind model-selected candle extremes to observed prices, without choosing a trade."""
from copy import deepcopy
from statistics import median
from typing import Literal

from pydantic import Field

from .schemas import Snapshot, StrictModel


class TargetAnchor(StrictModel):
    candle_id: str
    field: Literal["high", "low"]


class AnchoredTarget(StrictModel):
    anchor: TargetAnchor
    rationale: str = Field(min_length=8, max_length=500)


def resolve_target_anchors(payload: dict, snapshot: Snapshot) -> dict:
    """Copy only the exact field Claude selected; unknown sources remain errors.

    The persisted/public Analysis contract still contains price and source_ids.
    There is no rounding, nearest-candle search, plan removal or decision change.
    """
    if not isinstance(payload, dict):
        raise ValueError("submit_analysis must return a JSON object")
    result = deepcopy(payload)
    plan = result.get("trade_plan")
    if not isinstance(plan, dict) or not isinstance(plan.get("targets"), list):
        return result  # Pydantic reports the malformed analysis shape.
    candles = {c.id: c for rows in snapshot.candles.values() for c in rows}
    for index, raw in enumerate(plan["targets"]):
        target = AnchoredTarget.model_validate(raw)
        candle = candles.get(target.anchor.candle_id)
        if candle is None:
            raise ValueError(f"trade_plan.targets[{index}].anchor.candle_id: unknown closed candle {target.anchor.candle_id}")
        plan["targets"][index] = {"price": getattr(candle, target.anchor.field),
                                   "rationale": target.rationale, "source_ids": [candle.id]}
    trigger = plan.get("trigger")
    if trigger is not None:
        if not isinstance(trigger, dict) or set(trigger) - {"kind", "anchor", "description", "volume_multiple"}:
            raise ValueError("Invalid anchored trigger")
        anchor = TargetAnchor.model_validate(trigger["anchor"])
        candle = candles.get(anchor.candle_id)
        if candle is None:
            raise ValueError(f"trigger references unknown closed candle {anchor.candle_id}")
        baseline_rows = [c for c in snapshot.candles.get(plan.get("timeframe"), [])[-20:]
                         if c.volume_kind == "REAL" and c.volume is not None]
        baseline = median([c.volume for c in baseline_rows]) if baseline_rows else None
        plan["trigger"] = dict(kind=trigger["kind"], level=getattr(candle, anchor.field),
            description=trigger["description"], source_ids=[candle.id],
            volume_multiple=trigger.get("volume_multiple"), volume_baseline=baseline if baseline and baseline > 0 else None,
            volume_source_ids=[c.id for c in baseline_rows] if trigger.get("volume_multiple") is not None else [])
    return result
