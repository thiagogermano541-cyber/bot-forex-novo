from copy import deepcopy

import pytest
from conftest import as_wire

from app.analysis_contract import unpack_grouped_analysis
from app.schemas import Analysis


def test_grouped_plan_roundtrip_preserves_every_value(confirmed_buy):
    raw = confirmed_buy.model_dump(mode="json")
    wire = as_wire(raw)
    before = deepcopy(wire)
    decoded = unpack_grouped_analysis(wire)
    assert decoded == raw
    assert wire == before
    assert Analysis.model_validate(decoded) == confirmed_buy


def test_grouped_literal_null_roundtrip(wait_analysis):
    raw = wait_analysis.model_dump(mode="json")
    assert unpack_grouped_analysis(as_wire(raw)) == raw


@pytest.mark.parametrize("change", ["root_extra", "conclusion_extra", "missing_risks", "plan_extra", "plan_wrong_type"])
def test_grouping_never_discards_invalid_fields_or_interprets_plan_text(confirmed_buy, change):
    wire = as_wire(confirmed_buy.model_dump(mode="json"))
    if change == "root_extra":
        wire["decision"] = "AGUARDAR"
    elif change == "conclusion_extra":
        wire["conclusion"]["ignored"] = "must fail"
    elif change == "missing_risks":
        wire["limitations"].pop("risks")
    elif change == "plan_extra":
        wire["trade_plan"]["levels"]["extra_stop"] = 1
    else:
        wire["trade_plan"] = "null"
    with pytest.raises(ValueError):
        unpack_grouped_analysis(wire)
