"""Small typed rows for Claude transport; public Analysis remains unchanged."""
from typing import Literal

from .schemas import Decision, StrictModel


SECTIONS = ("SUMMARY", "JUSTIFICATION", "CHANGE", "EVIDENCE", "ZONE", "SCENARIO", "TIMEFRAME",
            "CONFLICTS", "RISKS", "MISSING_DATA", "PLAN", "TARGET", "CANCEL")


class AnalysisRow(StrictModel):
    section: Literal["SUMMARY", "JUSTIFICATION", "CHANGE", "EVIDENCE", "ZONE", "SCENARIO", "TIMEFRAME",
                     "CONFLICTS", "RISKS", "MISSING_DATA", "PLAN", "TARGET", "CANCEL"]
    strings: list[str]
    numbers: list[float]
    sources: list[str]


class ToolAnalysis(StrictModel):
    decision: Decision
    rows: list[AnalysisRow]


def unpack_analysis(payload: dict) -> dict:
    """Decode declared columns only; never infer a decision or repair prices."""
    wire = ToolAnalysis.model_validate(payload)
    result = dict(decision=wire.decision, evidence=[], zones=[], scenarios=[], timeframe_context={})
    single = {"SUMMARY": "summary", "JUSTIFICATION": "justification", "CHANGE": "change_from_previous",
              "CONFLICTS": "conflicts", "RISKS": "risks", "MISSING_DATA": "missing_data"}
    seen, plan, targets, cancel = set(), None, [], None
    for index, row in enumerate(wire.rows):
        s, n, refs = row.strings, row.numbers, row.sources
        kind = row.section

        def shape(strings=None, numbers=0, sources=None):
            if ((strings is not None and len(s) != strings) or len(n) != numbers
                    or (sources is not None and len(refs) != sources)):
                raise ValueError(f"rows[{index}] {kind}: wrong column counts strings={len(s)}, numbers={len(n)}, sources={len(refs)}")

        if kind in single:
            if kind in seen:
                raise ValueError(f"Duplicate singleton row {kind}")
            seen.add(kind)
            scalar = kind in ("SUMMARY", "JUSTIFICATION", "CHANGE")
            shape(strings=1 if scalar else None, sources=0)
            result[single[kind]] = s[0] if scalar else s
        elif kind == "EVIDENCE":
            shape(strings=2)
            result["evidence"].append(dict(category=s[0], description=s[1], source_ids=refs))
        elif kind == "ZONE":
            shape(strings=2, numbers=2)
            result["zones"].append(dict(kind=s[0], explanation=s[1], low=n[0], high=n[1], source_ids=refs))
        elif kind == "SCENARIO":
            if len(s) < 4 or len(n) > 1:
                raise ValueError(f"rows[{index}] SCENARIO requires direction, thesis, invalidation, confirmations; zero/one invalidation price")
            result["scenarios"].append(dict(direction=s[0], thesis=s[1], invalidation=s[2], confirmation=s[3:],
                                             invalidation_price=n[0] if n else None, evidence_ids=refs))
        elif kind == "TIMEFRAME":
            shape(strings=2, sources=0)
            if s[0] in result["timeframe_context"]:
                raise ValueError(f"Duplicate timeframe row {s[0]}")
            result["timeframe_context"][s[0]] = s[1]
        elif kind == "PLAN":
            shape(strings=5, numbers=4)
            if plan is not None:
                raise ValueError("Only one PLAN row is allowed")
            plan = dict(side=s[0], status=s[1], setup_type=s[2], timeframe=s[3], confirmation=s[4],
                        entry_low=n[0], entry_high=n[1], stop_loss=n[2], valid_for_minutes=n[3], confirmation_source_ids=refs)
        elif kind == "TARGET":
            shape(strings=2, sources=1)
            targets.append(dict(anchor=dict(candle_id=refs[0], field=s[0]), rationale=s[1]))
        elif kind == "CANCEL":
            shape(sources=0)
            if cancel is not None:
                raise ValueError("Only one CANCEL row is allowed")
            cancel = s
    if set(single) - seen:
        raise ValueError(f"Missing required singleton rows: {sorted(set(single)-seen)}")
    if plan is None and (targets or cancel is not None):
        raise ValueError("TARGET/CANCEL rows require a PLAN row; never discard orphan plan fields")
    if plan is not None:
        if not targets or cancel is None:
            raise ValueError("PLAN requires TARGET and CANCEL rows")
        plan.update(targets=targets, cancel_if=cancel)
    result["trade_plan"] = plan
    return result
