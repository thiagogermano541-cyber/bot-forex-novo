"""Anthropic strict-tool schema; business constraints remain in Pydantic."""
from copy import deepcopy

from .schemas import Analysis, TIMEFRAMES


UNSUPPORTED_CONSTRAINTS = {
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
    "minLength", "maxLength", "maxItems", "uniqueItems", "minProperties", "maxProperties",
}


def analysis_tool_schema(source_ids: set[str]) -> dict:
    schema = deepcopy(Analysis.model_json_schema())
    schema["properties"]["timeframe_context"] = {
        "type": "object",
        "properties": {tf: {"type": "string"} for tf in TIMEFRAMES},
        "required": list(TIMEFRAMES),
        "additionalProperties": False,
    }
    schema["$defs"]["SourceReference"] = {"type": "string", "enum": sorted(source_ids)}
    for name, field in (("Evidence", "source_ids"), ("Zone", "source_ids"), ("Scenario", "evidence_ids"),
                        ("SignalTarget", "source_ids"), ("TradePlan", "confirmation_source_ids")):
        schema["$defs"][name]["properties"][field]["items"] = {"$ref": "#/$defs/SourceReference"}

    def transform(node):
        if isinstance(node, list):
            return [transform(value) for value in node]
        if not isinstance(node, dict):
            return node
        removed = {key: value for key, value in node.items() if key in UNSUPPORTED_CONSTRAINTS
                   or (key == "minItems" and value not in (0, 1))}
        result = {key: transform(value) for key, value in node.items() if key not in removed}
        if removed:
            constraints = "; ".join(f"{key}={value}" for key, value in removed.items())
            result["description"] = (result.get("description", "") + f" Backend validation: {constraints}.").strip()
        if result.get("type") == "object":
            result["additionalProperties"] = False
        return result

    return transform(schema)
