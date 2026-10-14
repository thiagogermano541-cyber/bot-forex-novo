import json
from datetime import datetime

import httpx

from ..config import Settings
from ..schemas import Candle, Quote, Snapshot, TIMEFRAMES, utcnow
from .base import get_json


class Oanda:
    def __init__(self, client: httpx.AsyncClient, config: Settings):
        self.client, self.config = client, config
        suffix = "fxpractice" if config.oanda_environment == "practice" else "fxtrade"
        if config.oanda_environment not in ("practice", "live"):
            raise ValueError("Invalid OANDA environment")
        self.base = f"https://api-{suffix}.oanda.com/v3"
        self.stream_base = f"https://stream-{suffix}.oanda.com/v3"
        self.headers = {"Authorization": f"Bearer {config.oanda_token.get_secret_value()}"}

    async def snapshot(self):
        if not self.config.oanda_ready:
            return Snapshot(asset="XAU/USD", captured_at=utcnow(), candles={}, issues=["OANDA NÃO CONFIGURADO"])
        candles, issues = {}, []
        for tf in TIMEFRAMES:
            try:
                data = await get_json(self.client, f"{self.base}/instruments/XAU_USD/candles", headers=self.headers,
                                      params={"count": 61, "granularity": tf, "price": "M"})
                candles[tf] = [Candle(id=f"oanda:{tf}:{r['time']}", timestamp=datetime.fromisoformat(r["time"].replace("Z", "+00:00")),
                                      timeframe=tf, open=float(r["mid"]["o"]), high=float(r["mid"]["h"]), low=float(r["mid"]["l"]),
                                      close=float(r["mid"]["c"]), volume=r["volume"], volume_kind="TICK", provider="OANDA XAU_USD midpoint")
                               for r in data["candles"] if r["complete"]][-60:]
            except (httpx.HTTPError, ValueError, KeyError):
                issues.append(f"OANDA: falha OHLC {tf}")
        quote = None
        try:
            data = await get_json(self.client, f"{self.base}/accounts/{self.config.oanda_account_id}/pricing", headers=self.headers, params={"instruments": "XAU_USD"})
            row = data["prices"][0]
            if row["status"] != "tradeable":
                issues.append("OANDA: mercado não negociável")
            quote = self.parse_quote(row)
        except (httpx.HTTPError, ValueError, KeyError, IndexError):
            issues.append("OANDA: preço indisponível")
        return Snapshot(asset="XAU/USD", captured_at=utcnow(), candles=candles, quote=quote, issues=issues,
                        limitations=["Volume OANDA é tick volume, não volume negociado real.",
                                     "Forex/ouro OTC não possui livro de ofertas centralizado global.",
                                     "Não há dados observáveis de absorção ou ordens institucionais neste conector."])

    def parse_quote(self, row):
        return Quote(id=f"oanda:quote:{row['time']}", timestamp=datetime.fromisoformat(row["time"].replace("Z", "+00:00")),
                     bid=float(row["closeoutBid"]), ask=float(row["closeoutAsk"]), provider="OANDA")

    async def stream(self):
        async with self.client.stream("GET", f"{self.stream_base}/accounts/{self.config.oanda_account_id}/pricing/stream",
                                      headers=self.headers, params={"instruments": "XAU_USD"}, timeout=httpx.Timeout(45, read=45)) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if line:
                    row = json.loads(line)
                    if row.get("type") == "PRICE":
                        yield self.parse_quote(row)
