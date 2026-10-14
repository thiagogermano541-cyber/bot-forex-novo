import pytest
from pydantic import TypeAdapter, ValidationError

from app.schemas import ACTIVE_ASSETS, Asset


def test_only_btc_is_an_active_asset():
    assert ACTIVE_ASSETS == ("BTC/USD",)
    assert TypeAdapter(Asset).validate_python("BTC/USD") == "BTC/USD"
    with pytest.raises(ValidationError):
        TypeAdapter(Asset).validate_python("XAU/USD")
