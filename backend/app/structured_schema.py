"""Anthropic strict-tool schema; business constraints remain in Pydantic."""
from copy import deepcopy

from .schemas import Analysis, TIMEFRAMES
from .target_anchors import AnchoredTarget


UNSUPPORTED_CONSTRAINTS = {
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
    "minLength", "maxLength", "minItems", "maxItems", "uniqueItems", "minProperties", "maxProperties",
}


def analysis_tool_schema(source_ids: set[str]) -> dict:
    schema = deepcopy(Analysis.model_json_schema())
    # Claude selects a candle extreme; the backend copies its exact observed
    # price. This prevents a numeric target and its citation from disagreeing.
    target_schema = AnchoredTarget.model_json_schema()
    schema["$defs"].update(target_schema.pop("$defs"))
    schema["$defs"]["SignalTarget"] = target_schema
    schema["properties"]["timeframe_context"] = {
        "type": "object",
        "properties": {tf: {"type": "string"} for tf in TIMEFRAMES},
        "required": list(TIMEFRAMES),
        "additionalProperties": False,
    }
    # Keep a stable, small grammar for ALL snapshots. Repeated source enums,
    # regexes and array bounds explode the provider's compiled grammar. Exact
    # source whitelists and every business constraint stay enforced in Python.
    schema["$defs"]["SourceReference"] = {"type": "string"}
    for name, field in (("Evidence", "source_ids"), ("Zone", "source_ids"), ("Scenario", "evidence_ids"),
                        ("TradePlan", "confirmation_source_ids")):
        schema["$defs"][name]["properties"][field]["items"] = {"$ref": "#/$defs/SourceReference"}
    schema["$defs"]["TargetAnchor"]["properties"]["candle_id"] = {"$ref": "#/$defs/SourceReference"}

    def transform(node):
        if isinstance(node, list):
            return [transform(value) for value in node]
        if not isinstance(node, dict):
            return node
        removed = {key: value for key, value in node.items()
                   if key in UNSUPPORTED_CONSTRAINTS or key in {"title", "default"}}
        result = {key: transform(value) for key, value in node.items() if key not in removed}
        if result.get("type") == "object":
            result["additionalProperties"] = False
            result["required"] = list(result.get("properties", {}))
        return result

    return transform(schema)
