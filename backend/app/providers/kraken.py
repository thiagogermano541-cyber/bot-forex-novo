import asyncio
import json
from datetime import datetime, timezone

import httpx
from websockets.asyncio.client import connect

from ..schemas import Candle, Quote, Snapshot, TIMEFRAMES, utcnow
from .base import get_json


class Kraken:
    base = "https://api.kraken.com/0/public"

    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def request(self, path, **params):
        data = await get_json(self.client, f"{self.base}/{path}", params=params)
        if data.get("error"):
            raise ValueError("Kraken rejected request")
        return data["result"]

    async def candles(self, tf):
        result = await self.request("OHLC", pair="XBTUSD", interval=TIMEFRAMES[tf] // 60)
        rows = next(v for k, v in result.items() if k != "last")
        cutoff = utcnow().timestamp()
        return [Candle(id=f"kraken:{tf}:{r[0]}", timestamp=datetime.fromtimestamp(r[0], timezone.utc),
                       timeframe=tf, open=float(r[1]), high=float(r[2]), low=float(r[3]), close=float(r[4]),
                       volume=float(r[6]), volume_kind="REAL", provider="Kraken spot BTC/USD")
                for r in rows if r[0] + TIMEFRAMES[tf] <= cutoff][-60:]

    async def snapshot(self):
        # Sequential provider calls avoid bursting Kraken's public endpoint limit.
        candles = {}
        issues = []
        for tf in TIMEFRAMES:
            try:
                candles[tf] = await self.candles(tf)
            except (httpx.HTTPError, ValueError, KeyError):
                issues.append(f"Kraken: falha OHLC {tf}")
            await asyncio.sleep(0.15)
        quote = None
        trades = []
        book = None
        try:
            result = await self.request("Trades", pair="XBTUSD", count=40)
            rows = next(v for k, v in result.items() if k != "last")[-40:]
            trades = [dict(id=f"kraken:trade:{r[6] if len(r)>6 else r[2]}", price=float(r[0]),
                           volume=float(r[1]), timestamp=datetime.fromtimestamp(float(r[2]), timezone.utc).isoformat(),
                           side=r[3], provider="Kraken") for r in rows]
            result = await self.request("Depth", pair="XBTUSD", count=25)
            data = next(iter(result.values()))
            received = utcnow()
            stamp = max(float(r[2]) for rows in (data["bids"], data["asks"]) for r in rows)
            quote = Quote(id=f"kraken:quote:{stamp}", timestamp=datetime.fromtimestamp(stamp, timezone.utc),
                          bid=float(data["bids"][0][0]), ask=float(data["asks"][0][0]), provider="Kraken")
            book = dict(id=f"kraken:book:{received.isoformat()}", provider="Kraken", received_at=received.isoformat(),
                        scope="Livro local da exchange; não representa todo o mercado BTC",
                        bids=[[float(r[0]), float(r[1])] for r in data["bids"]],
                        asks=[[float(r[0]), float(r[1])] for r in data["asks"]])
        except (httpx.HTTPError, ValueError, KeyError):
            issues.append("Kraken: falha em trades ou profundidade")
        return Snapshot(asset="BTC/USD", captured_at=utcnow(), candles=candles, quote=quote,
                        trades=trades, order_book=book, issues=issues,
                        limitations=["Liquidações e derivativos não disponíveis neste conector spot.",
                                     "Volumes e livro limitados à Kraken; ordens ocultas não observáveis."])

    async def stream(self):
        async with connect("wss://ws.kraken.com/v2", open_timeout=20, ping_interval=20) as socket:
            await socket.send(json.dumps({"method": "subscribe", "params": {"channel": "ticker", "symbol": ["BTC/USD"], "event_trigger": "bbo"}}))
            async for raw in socket:
                message = json.loads(raw)
                if message.get("channel") != "ticker":
                    continue
                for row in message.get("data", []):
                    # Never substitute local receipt time for a missing source timestamp.
                    if row.get("timestamp"):
                        stamp = datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00"))
                        yield Quote(id=f"kraken:ws:{stamp.isoformat()}", timestamp=stamp, bid=row["bid"], ask=row["ask"], provider="Kraken WebSocket")
