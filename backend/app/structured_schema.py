"""A small, stable strict grammar; business constraints stay in Pydantic."""
from copy import deepcopy

from .analysis_contract import ToolAnalysis

UNSUPPORTED_CONSTRAINTS = {
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
    "minLength", "maxLength", "minItems", "maxItems", "uniqueItems", "minProperties", "maxProperties",
}


def analysis_tool_schema(source_ids: set[str]) -> dict:
    schema = deepcopy(ToolAnalysis.model_json_schema())
    definitions = schema.pop("$defs")
    # Inline the TWO small definitions. No nested plan objects, nullable object
    # branches, repeated regexes, or large citation enums enter the compiler.
    schema["properties"]["decision"] = definitions["Decision"]
    schema["properties"]["rows"]["items"] = definitions["AnalysisRow"]
    # Legacy EVIDENCE rows remain decodable for audited answers, but new output
    # uses the strict section enum for its category, avoiding free-text drift.
    sections = schema["properties"]["rows"]["items"]["properties"]["section"]["enum"]
    sections.remove("EVIDENCE")

    def transform(node):
        if isinstance(node, list):
            return [transform(value) for value in node]
        if not isinstance(node, dict):
            return node
        result = {key: transform(value) for key, value in node.items()
                  if key not in UNSUPPORTED_CONSTRAINTS and key not in {"title", "default"}}
        if result.get("type") == "object":
            result["additionalProperties"] = False
            result["required"] = list(result.get("properties", {}))
        return result

    return transform(schema)
