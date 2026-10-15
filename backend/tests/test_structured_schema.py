from app.schemas import Analysis
from app.structured_schema import analysis_tool_schema, UNSUPPORTED_CONSTRAINTS


def test_strict_tool_schema_is_small_typed_and_has_no_nested_plan_grammar():
    schema = analysis_tool_schema({"Q_CURRENT", "M1_C30"})
    def verify(node):
        if isinstance(node, list):
            for value in node:
                verify(value)
        elif isinstance(node, dict):
            assert not (UNSUPPORTED_CONSTRAINTS & node.keys())
            assert "$ref" not in node and "$defs" not in node and "anyOf" not in node
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert len(node.get("properties", {})) <= 4
                assert set(node["required"]) == set(node["properties"])
            for value in node.values():
                verify(value)
    verify(schema)
    assert set(schema["properties"]) == {"decision", "rows"}
    row = schema["properties"]["rows"]["items"]["properties"]
    assert row["numbers"]["items"]["type"] == "number"
    assert row["sources"]["items"]["type"] == "string"
    assert Analysis.model_json_schema()["properties"]["summary"]["minLength"] == 10
    assert Analysis.model_json_schema()["$defs"]["Zone"]["properties"]["low"]["exclusiveMinimum"] == 0


def test_compact_transport_does_not_weaken_exact_snapshot_grounding(snapshot, wait_analysis):
    import pytest
    ids = {f"H4_C{i:02d}" for i in range(60)}
    assert analysis_tool_schema(ids) == analysis_tool_schema({"Q_CURRENT"})
    wait_analysis.evidence[0].source_ids = ["M1_C99"]
    with pytest.raises(ValueError, match="unknown evidence"):
        wait_analysis.validate_grounding(snapshot)
