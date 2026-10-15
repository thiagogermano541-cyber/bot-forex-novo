import json
from datetime import timedelta

import pytest

from app.market_context import prepare_market
from app.schemas import TradePlan, TIMEFRAMES


def test_confirmed_buy_is_grounded(snapshot, confirmed_buy):
    assert confirmed_buy.validate_grounding(snapshot).decision == "CENÁRIO DE ALTA"


def test_confirmed_sell_has_coherent_geometry(snapshot, confirmed_buy):
    analysis = confirmed_buy
    analysis.decision = "CENÁRIO DE BAIXA"
    analysis.scenarios[1].invalidation_price = 102
    data = analysis.trade_plan.model_dump()
    data.update(side="VENDA", stop_loss=102, targets=[{"price": 99, "rationale": "Mínima observada no candle citado.",
                 "source_ids": [snapshot.candles["H1"][-1].id]}])
    analysis.trade_plan = TradePlan.model_validate(data)
    assert analysis.validate_grounding(snapshot).decision == "CENÁRIO DE BAIXA"


def test_pending_setup_cannot_issue_direction(snapshot, confirmed_buy):
    confirmed_buy.trade_plan.status = "PENDENTE"
    with pytest.raises(ValueError, match="matching CONFIRMADO"):
        confirmed_buy.validate_grounding(snapshot)
    confirmed_buy.decision = "AGUARDAR"
    assert confirmed_buy.validate_grounding(snapshot).decision == "AGUARDAR"


def test_direction_requires_higher_timeframe_observation(snapshot, confirmed_buy):
    confirmed_buy.evidence = confirmed_buy.evidence[:1]
    with pytest.raises(ValueError, match="higher and a lower timeframe"):
        confirmed_buy.validate_grounding(snapshot)


def test_confirmation_must_be_recent(snapshot, confirmed_buy):
    confirmed_buy.trade_plan.confirmation_source_ids = [snapshot.candles["M1"][-2].id]
    with pytest.raises(ValueError, match="latest closed candle"):
        confirmed_buy.validate_grounding(snapshot)


def test_no_chasing_outside_entry(snapshot, confirmed_buy):
    confirmed_buy.trade_plan.entry_high = 100.25
    with pytest.raises(ValueError, match="outside entry region"):
        confirmed_buy.validate_grounding(snapshot)


def test_target_cannot_invent_an_unobserved_region(snapshot, confirmed_buy):
    confirmed_buy.trade_plan.targets[0].price = 200
    with pytest.raises(ValueError, match="observed price region"):
        confirmed_buy.validate_grounding(snapshot)


def test_stop_must_match_structural_invalidation(snapshot, confirmed_buy):
    confirmed_buy.trade_plan.stop_loss = 98
    with pytest.raises(ValueError, match="match the selected scenario"):
        confirmed_buy.validate_grounding(snapshot)


@pytest.mark.parametrize("change", [dict(stop_loss=101), dict(entry_low=103),
    dict(targets=[{"price": 100, "rationale": "Alvo incoerente de teste.", "source_ids": ["test"]}])])
def test_wrong_plan_geometry_is_rejected(confirmed_buy, change):
    data = confirmed_buy.trade_plan.model_dump()
    data.update(change)
    with pytest.raises(ValueError):
        TradePlan.model_validate(data)


def test_compact_context_preserves_all_prices_and_original_sources(snapshot):
    for tf, rows in snapshot.candles.items():
        earlier = []
        for i in range(30):
            row = rows[0].model_copy(deep=True)
            row.timestamp -= timedelta(seconds=TIMEFRAMES[tf] * (30-i))
            row.id = f"test:{tf}:earlier:{i}"
            earlier.append(row)
        snapshot.candles[tf] = earlier + rows
    prepared = prepare_market(snapshot, [], dict.fromkeys(TIMEFRAMES, 60))
    assert all(len(table["rows"]) == 60 for table in prepared.payload["market_data"]["candles"].values())
    table = prepared.payload["market_data"]["candles"]["M1"]
    last = dict(zip(table["columns"], table["rows"][-1]))
    assert (last["open"], last["high"], last["low"], last["close"], last["volume"]) == (100, 102, 99, 101, 5)
    assert last["timestamp_utc_seconds"] == int(snapshot.candles["M1"][-1].timestamp.timestamp())
    assert prepared.references["M1_C60"] == snapshot.candles["M1"][-1].id
    assert snapshot.candles["M1"][-1].id != "M1_C60"
    assert prepared.payload["observed_facts"]["timeframes"]["M1"]["real_volume_sum"] == 300
    json.dumps(prepared.payload, allow_nan=False)


def test_tick_volume_is_kept_separate_and_context_never_uses_withheld_candles(snapshot):
    snapshot.candles["M1"][-1].volume_kind = "TICK"
    prepared = prepare_market(snapshot, [], dict.fromkeys(TIMEFRAMES, 20))
    facts = prepared.payload["observed_facts"]["timeframes"]["M1"]
    assert facts["real_volume_sum"] == 95
    assert facts["tick_volume_sum"] == 5
    assert len(prepared.supplied.candles["M1"]) == 20
    assert snapshot.candles["M1"][0].id not in prepared.references.values()
    table = prepared.payload["market_data"]["candles"]["M1"]
    assert "volume_kind" in table["columns"]


@pytest.mark.parametrize("side,low,high,stop,target", [
    ("COMPRA", 99.5, 100, 99, 102),
    ("VENDA", 101, 101.5, 102, 99),
])
def test_pending_future_entry_keeps_complete_plan_without_issuing_direction(
    snapshot, confirmed_buy, side, low, high, stop, target
):
    analysis = confirmed_buy
    analysis.decision = "AGUARDAR"
    plan = analysis.trade_plan.model_dump()
    plan.update(side=side, status="PENDENTE", entry_low=low, entry_high=high, stop_loss=stop,
        targets=[{"price": target, "rationale": "Extremo observado como alvo do setup pendente.",
                  "source_ids": [snapshot.candles["H1"][-1].id]}])
    analysis.trade_plan = TradePlan.model_validate(plan)
    result = analysis.validate_grounding(snapshot)
    assert result.decision == "AGUARDAR"
    assert result.trade_plan.side == side
    assert result.trade_plan.entry_low == low
    assert result.trade_plan.targets[0].price == target
