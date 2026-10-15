"""Small strict-tool objects; lossless transport to the public Analysis model."""
from copy import deepcopy


ANALYSIS_GROUPS = {
    "conclusion": ("decision", "summary", "justification", "change_from_previous"),
    "observations": ("evidence", "zones", "timeframe_context"),
    "limitations": ("conflicts", "risks", "missing_data"),
}
PLAN_GROUPS = {
    "position": ("side", "status", "setup_type", "timeframe"),
    "levels": ("entry_low", "entry_high", "stop_loss"),
    "trigger": ("confirmation", "confirmation_source_ids"),
    "expiry": ("valid_for_minutes", "cancel_if"),
}


def group_properties(properties: dict, groups: dict, direct: tuple) -> dict:
    """Build the provider schema without wide objects that exceed its grammar limit."""
    return {**{name: {"type": "object", "properties": {key: properties[key] for key in keys}}
               for name, keys in groups.items()}, **{key: properties[key] for key in direct}}


def checked_object(value, keys, path):
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected JSON object, received {type(value).__name__}")
    missing, extra = set(keys) - value.keys(), value.keys() - set(keys)
    if missing or extra:
        raise ValueError(f"{path}: missing fields={sorted(missing)}, unexpected fields={sorted(extra)}")
    return value


def unpack_grouped_analysis(payload: dict) -> dict:
    """Move fields only. No coercion, price edits, omitted plans or decision changes."""
    source = checked_object(payload, (*ANALYSIS_GROUPS, "scenarios", "trade_plan"), "analysis")
    result = {"scenarios": deepcopy(source["scenarios"])}
    for group, keys in ANALYSIS_GROUPS.items():
        result.update(deepcopy(checked_object(source[group], keys, group)))
    plan = source["trade_plan"]
    if plan is not None:
        plan = checked_object(plan, (*PLAN_GROUPS, "targets"), "trade_plan")
        decoded = {"targets": deepcopy(plan["targets"])}
        for group, keys in PLAN_GROUPS.items():
            decoded.update(deepcopy(checked_object(plan[group], keys, f"trade_plan.{group}")))
        plan = decoded
    result["trade_plan"] = plan
    return result
