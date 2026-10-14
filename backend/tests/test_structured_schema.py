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
            for value in node.values():
                verify(value)

    verify(schema)
    timeframes = schema["properties"]["timeframe_context"]
    assert set(timeframes["required"]) == set(TIMEFRAMES)
    assert all(item["type"] == "string" for item in timeframes["properties"].values())
    assert schema["$defs"]["SourceReference"]["enum"] == ["M1_C30", "Q_CURRENT"]
    assert schema["properties"]["conflicts"]["items"]["type"] == "string"
    # API grammar compatibility does not relax backend business validation.
    assert Analysis.model_json_schema()["properties"]["summary"]["minLength"] == 10
    assert Analysis.model_json_schema()["$defs"]["Zone"]["properties"]["low"]["exclusiveMinimum"] == 0
