"""Bind model-selected candle extremes to observed prices, without choosing a trade."""
from copy import deepcopy
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
    return result
