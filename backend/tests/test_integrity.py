from datetime import timedelta

import pytest
from pydantic import ValidationError

from app.schemas import Candle, Snapshot


def test_complete_snapshot(snapshot):
    assert snapshot.integrity_issues(90) == []


def test_stale_quote_blocks_analysis(snapshot):
    snapshot.quote.timestamp -= timedelta(minutes=4)
    assert "Preço ausente ou desatualizado" in snapshot.integrity_issues(90)


def test_missing_timeframe(snapshot):
    del snapshot.candles["H4"]
    assert any("H4" in issue for issue in snapshot.integrity_issues(90))


def test_gap_detected(snapshot):
    snapshot.candles["M5"].pop(10)
    assert "M5: lacunas no histórico" in snapshot.integrity_issues(90)


def test_future_candle_rejected(snapshot):
    data = snapshot.model_dump(mode="json")
    data["candles"]["M1"][-1]["timestamp"] = snapshot.captured_at.isoformat()
    with pytest.raises(ValidationError):
        Snapshot.model_validate(data)


def test_invalid_ohlc_and_nan_rejected(snapshot):
    data = snapshot.candles["M1"][0].model_dump()
    data["high"] = 90
    with pytest.raises(ValidationError):
        Candle.model_validate(data)
    data["high"] = float("nan")
    with pytest.raises(ValidationError):
        Candle.model_validate(data)


def test_grounding_rejects_invented_source(snapshot, wait_analysis):
    wait_analysis.evidence[0].source_ids = ["invented:institutional-order"]
    with pytest.raises(ValueError, match="unknown evidence"):
        wait_analysis.validate_grounding(snapshot)


def test_valid_wait_is_allowed(snapshot, wait_analysis):
    assert wait_analysis.validate_grounding(snapshot).decision == "AGUARDAR"


def test_no_direction_without_invalidation(snapshot, wait_analysis):
    wait_analysis.decision = "CENÁRIO DE ALTA"
    with pytest.raises(ValueError, match="Directional decision"):
        wait_analysis.validate_grounding(snapshot)
