import asyncio
import logging
import random

import httpx
import structlog
from redis.asyncio import Redis
from sqlalchemy import text

from .config import settings
from .db import Session
from .evaluation import evaluate_pending
from .providers.kraken import Kraken
from .providers.oanda import Oanda
from .schemas import utcnow
from .service import AnalysisService
from .telegram import deliver

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
structlog.configure(processors=[structlog.processors.TimeStamper(fmt="iso"), structlog.processors.JSONRenderer()])
log = structlog.get_logger()


async def stream_loop(provider, asset, redis):
    backoff = 1
    while True:
        try:
            async for quote in provider.stream():
                await redis.set(f"quote:{asset}", quote.model_dump_json(), ex=120)
                await redis.set(f"stream:{asset}", "CONECTADO", ex=90)
                backoff = 1
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await redis.set(f"stream:{asset}", "RECONECTANDO", ex=90)
            log.warning("stream_disconnected", asset=asset, error_type=type(exc).__name__)
        await asyncio.sleep(min(backoff, 60)+random.random())
        backoff = min(backoff*2, 60)


async def heartbeat(redis):
    while True:
        await redis.set("worker:heartbeat", utcnow().isoformat(), ex=45)
        await asyncio.sleep(10)


async def main():
    cfg = settings()
    redis = Redis.from_url(cfg.redis_url, decode_responses=True)
    # Railway starts services independently. Wait for API-created schema and cache.
    for attempt in range(36):
        try:
            await redis.ping()
            async with Session() as session:
                await session.execute(text("SELECT id FROM users LIMIT 1"))
            break
        except Exception as exc:
            if attempt == 35:
                raise RuntimeError("Database/cache/schema not ready for worker") from None
            log.warning("worker_waiting_for_dependencies", error_type=type(exc).__name__)
            await asyncio.sleep(5)
    async with httpx.AsyncClient(timeout=20) as client:
        service = AnalysisService(redis, client)
        tasks = [asyncio.create_task(heartbeat(redis))]
        if cfg.kraken_enabled:
            tasks.append(asyncio.create_task(stream_loop(Kraken(client), "BTC/USD", redis)))
        if cfg.oanda_ready:
            tasks.append(asyncio.create_task(stream_loop(Oanda(client, cfg), "XAU/USD", redis)))
        try:
            last_analysis, last_collect = -float("inf"), -float("inf")
            while True:
                now = asyncio.get_running_loop().time()
                analyze = now-last_analysis >= max(cfg.analysis_interval_seconds, 60) and cfg.claude_ready
                collect = now-last_collect >= 60
                if analyze or collect:
                    if analyze:
                        last_analysis = now
                    last_collect = now
                    for asset in ("BTC/USD", "XAU/USD"):
                        try:
                            if analyze:
                                await service.run(asset)
                            else:
                                await service.collect(asset)
                            await redis.delete(f"error:{asset}")
                        except Exception as exc:
                            await redis.set(f"error:{asset}", type(exc).__name__, ex=3600)
                            log.error("analysis_failed", asset=asset, error_type=type(exc).__name__)
                try:
                    # One worker owns the outbox/evaluation lease across replicas.
                    async with redis.lock("jarvis:outbox", timeout=180, blocking_timeout=1):
                        async with Session() as session:
                            await deliver(session, client, cfg)
                            await evaluate_pending(session, cfg.evaluation_horizon_minutes)
                except Exception as exc:
                    log.error("outbox_failed", error_type=type(exc).__name__)
                await asyncio.sleep(5)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await service.claude.client.close()
    await redis.aclose()


if __name__ == "__main__":
    asyncio.run(main())
