"""Read-only production diagnostics. Credentials and response bodies are not logged."""
import asyncio
import json
import logging

import httpx
from redis.asyncio import Redis
from sqlalchemy import select

from .config import settings
from .db import MarketSnapshot, Session

logging.getLogger("httpx").setLevel(logging.WARNING)


async def main():
    cfg = settings()
    redis = Redis.from_url(cfg.redis_url, decode_responses=True)
    async with Session() as session:
        row = await session.scalar(select(MarketSnapshot).where(MarketSnapshot.asset == "BTC/USD").order_by(MarketSnapshot.created_at.desc()).limit(1))
    print(json.dumps({"event": "market_diagnostic", "worker_heartbeat_present": bool(await redis.get("worker:heartbeat")),
                      "kraken_enabled": cfg.kraken_enabled, "snapshot_present": row is not None,
                      "candles": {tf: len(rows) for tf, rows in row.data.get("candles", {}).items()} if row else {},
                      "quote_present": bool(row and row.data.get("quote")), "issues": row.data.get("issues", []) if row else []}), flush=True)
    async with httpx.AsyncClient(timeout=5) as client:
        for endpoint, params in [("Time", {}), ("OHLC", {"pair": "XBTUSD", "interval": 1}),
                                 ("Depth", {"pair": "XBTUSD", "count": 25}), ("Trades", {"pair": "XBTUSD", "count": 40})]:
            try:
                response = await client.get(f"https://api.kraken.com/0/public/{endpoint}", params=params)
                body = response.json() if response.status_code == 200 else {}
                print(json.dumps({"event": "kraken_endpoint_diagnostic", "endpoint": endpoint,
                                  "http_status": response.status_code, "provider_errors": body.get("error", []),
                                  "result_present": bool(body.get("result"))}), flush=True)
            except Exception as exc:
                print(json.dumps({"event": "kraken_endpoint_diagnostic", "endpoint": endpoint,
                                  "error_type": type(exc).__name__}), flush=True)
    await redis.aclose()


if __name__ == "__main__":
    asyncio.run(main())
