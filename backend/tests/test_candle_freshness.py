from datetime import datetime, timedelta, timezone

import pytest

from app.schemas import TIMEFRAMES


@pytest.mark.parametrize("tf,seconds", TIMEFRAMES.items())
def test_last_closed_candle_is_fresh_while_current_candle_is_forming(snapshot, tf, seconds):
    boundary = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)
    snapshot.captured_at = boundary+timedelta(seconds=seconds-1)
    snapshot.quote.timestamp = snapshot.captured_at-timedelta(seconds=1)
    for i, candle in enumerate(snapshot.candles[tf]):
        candle.timestamp = boundary-timedelta(seconds=seconds*(30-i))
    assert f"{tf}: candles desatualizados" not in snapshot.integrity_issues(90)


@pytest.mark.parametrize("tf,seconds", TIMEFRAMES.items())
def test_missing_closed_candle_becomes_stale_after_tolerance(snapshot, tf, seconds):
    boundary = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)
    snapshot.captured_at = boundary+timedelta(seconds=91)
    snapshot.quote.timestamp = snapshot.captured_at-timedelta(seconds=1)
    for i, candle in enumerate(snapshot.candles[tf]):
        candle.timestamp = boundary-timedelta(seconds=seconds*(31-i))
    assert f"{tf}: candles desatualizados" in snapshot.integrity_issues(90)
