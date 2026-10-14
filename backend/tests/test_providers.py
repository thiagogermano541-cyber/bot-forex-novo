import httpx
import pytest
import respx

from app.config import Settings
from app.providers.base import get_json
from app.providers.oanda import Oanda


async def test_missing_oanda_is_explicit():
    async with httpx.AsyncClient() as client:
        result = await Oanda(client, Settings(oanda_token="", oanda_account_id="")).snapshot()
    assert result.issues == ["OANDA NÃO CONFIGURADO"]
    assert not result.candles and result.quote is None


@respx.mock
async def test_rest_failure_retries():
    route = respx.get("https://provider.test/candles").mock(side_effect=[httpx.Response(503), httpx.Response(200, json={"ok": True})])
    async with httpx.AsyncClient() as client:
        assert await get_json(client, "https://provider.test/candles") == {"ok": True}
    assert route.call_count == 2


@respx.mock
async def test_oanda_tick_volume_is_not_real():
    cfg = Settings(oanda_token="TEST_ONLY", oanda_account_id="TEST_ONLY")
    candles = {"candles": [{"time": "2026-10-14T00:00:00Z", "complete": True,
               "mid": {"o": "100", "h": "102", "l": "99", "c": "101"}, "volume": 20}]}
    respx.get("https://api-fxpractice.oanda.com/v3/instruments/XAU_USD/candles").mock(return_value=httpx.Response(200, json=candles))
    respx.get("https://api-fxpractice.oanda.com/v3/accounts/TEST_ONLY/pricing").mock(return_value=httpx.Response(200, json={"prices": [{"time": "2026-10-14T00:00:01Z", "status": "tradeable", "closeoutBid": "100", "closeoutAsk": "101"}]}))
    async with httpx.AsyncClient() as client:
        result = await Oanda(client, cfg).snapshot()
    assert all(c.volume_kind == "TICK" for rows in result.candles.values() for c in rows)
    assert result.order_book is None


@respx.mock
async def test_http_401_is_not_retried():
    route = respx.get("https://provider.test/private").mock(return_value=httpx.Response(401))
    async with httpx.AsyncClient() as client:
        with pytest.raises(httpx.HTTPStatusError):
            await get_json(client, "https://provider.test/private")
    assert route.call_count == 1
