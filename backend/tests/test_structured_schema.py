from app.schemas import Analysis, TIMEFRAMES
from app.structured_schema import analysis_tool_schema, UNSUPPORTED_CONSTRAINTS


def test_strict_tool_schema_uses_only_supported_constraints():
    schema = analysis_tool_schema({"Q_CURRENT", "M1_C30"})

    def verify(node):
        if isinstance(node, list):
            for value in node:
                verify(value)
        elif isinstance(node, dict):
            assert not (UNSUPPORTED_CONSTRAINTS & node.keys())
            assert node.get("minItems", 0) in (0, 1)
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node.get("properties", {}))
            for value in node.values():
                verify(value)

    verify(schema)
    timeframes = schema["properties"]["timeframe_context"]
    assert set(timeframes["required"]) == set(TIMEFRAMES)
    assert all(item["type"] == "string" for item in timeframes["properties"].values())
    assert schema["$defs"]["SourceReference"] == {"type": "string"}
    assert schema["properties"]["conflicts"]["items"]["type"] == "string"
    # API grammar compatibility does not relax backend business validation.
    assert Analysis.model_json_schema()["properties"]["summary"]["minLength"] == 10
    assert Analysis.model_json_schema()["$defs"]["Zone"]["properties"]["low"]["exclusiveMinimum"] == 0


def test_large_context_uses_compact_grammar_without_weakening_grounding(snapshot, wait_analysis):
    import pytest
    ids = {f"{tf}_C{i:02d}" for tf in TIMEFRAMES for i in range(1, 61)} | {"Q_CURRENT", "BOOK_CURRENT"}
    reference = analysis_tool_schema(ids)["$defs"]["SourceReference"]
    assert reference == {"type": "string"}
    assert analysis_tool_schema(ids) == analysis_tool_schema({"Q_CURRENT"})
    wait_analysis.evidence[0].source_ids = ["M1_C99"]
    with pytest.raises(ValueError, match="unknown evidence"):
        wait_analysis.validate_grounding(snapshot)
