from copy import deepcopy

import pytest
from conftest import as_wire

from app.analysis_contract import unpack_analysis
from app.schemas import Analysis
from app.target_anchors import resolve_target_anchors


def test_typed_rows_preserve_every_value_and_selected_candle(snapshot, confirmed_buy):
    raw = confirmed_buy.model_dump(mode="json")
    raw["trade_plan"]["targets"] = [{"anchor": {"candle_id": snapshot.candles["H1"][-1].id, "field": "high"},
                                     "rationale": confirmed_buy.trade_plan.targets[0].rationale}]
    wire = as_wire(raw)
    before = deepcopy(wire)
    decoded = unpack_analysis(wire)
    assert decoded == raw
    assert wire == before
    assert Analysis.model_validate(resolve_target_anchors(decoded, snapshot)) == confirmed_buy


def test_absent_plan_is_explicit_wire_choice_and_does_not_change_decision(wait_analysis):
    raw = wait_analysis.model_dump(mode="json")
    wire = as_wire(raw)
    assert not any(row["section"] in ("PLAN", "TARGET", "CANCEL") for row in wire["rows"])
    assert unpack_analysis(wire) == raw


@pytest.mark.parametrize("change", ["extra", "duplicate", "missing", "orphan", "badcolumns"])
def test_transport_never_discards_invalid_rows_or_interprets_plan_text(wait_analysis, change):
    wire = as_wire(wait_analysis.model_dump(mode="json"))
    if change == "extra":
        wire["trade_plan"] = "null"
    elif change == "duplicate":
        wire["rows"].append(deepcopy(wire["rows"][0]))
    elif change == "missing":
        wire["rows"] = [row for row in wire["rows"] if row["section"] != "RISKS"]
    elif change == "orphan":
        wire["rows"].append(dict(section="TARGET", strings=["high", "Região observada."], numbers=[], sources=["H1_C30"]))
    else:
        wire["rows"][0]["numbers"] = [1]
    with pytest.raises(ValueError):
        unpack_analysis(wire)
