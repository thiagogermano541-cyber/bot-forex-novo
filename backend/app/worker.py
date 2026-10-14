import asyncio
import logging
import random

import httpx
import structlog
from pydantic import ValidationError
from redis.asyncio import Redis
from sqlalchemy import text

from .config import settings
from .db import Session
from .evaluation import evaluate_pending
from .providers.kraken import Kraken
from .schemas import ACTIVE_ASSETS, utcnow
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


async def collect_loop(service):
    while True:
        for asset in ACTIVE_ASSETS:
            try:
                log.info("market_collection_started", asset=asset)
                async with asyncio.timeout(75):
                    await service.collect(asset)
                await service.redis.delete(f"error:{asset}")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await service.redis.set(f"error:{asset}", type(exc).__name__, ex=3600)
                log.error("market_collection_failed", asset=asset, error_type=type(exc).__name__,
                          validation_errors=[{"field": str(e["loc"]), "message": e["msg"]} for e in exc.errors(include_input=False)] if isinstance(exc, ValidationError) else [])
        await asyncio.sleep(service.config.market_refresh_seconds)


async def analysis_loop(service):
    while True:
        if service.config.claude_ready:
            for asset in ACTIVE_ASSETS:
                try:
                    await service.run(asset)
                    await service.redis.delete(f"analysis_error:{asset}")
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    await service.redis.set(f"analysis_error:{asset}", type(exc).__name__, ex=3600)
                    log.error("analysis_failed", asset=asset, error_type=type(exc).__name__)
        await asyncio.sleep(service.config.analysis_interval_seconds)


async def verify_public_once():
    from .verify import main as verify
    try:
        await verify()
    except Exception as exc:
        log.error("public_verification_failed", error_type=type(exc).__name__)


async def main():
    cfg = settings()
    redis = Redis.from_url(cfg.redis_url, decode_responses=True)
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
    async with httpx.AsyncClient(timeout=httpx.Timeout(10, connect=5)) as client:
        service = AnalysisService(redis, client)
        tasks = [asyncio.create_task(heartbeat(redis)), asyncio.create_task(collect_loop(service))]
        if cfg.kraken_enabled:
            tasks.append(asyncio.create_task(stream_loop(Kraken(client), "BTC/USD", redis)))
        log.info("worker_started", assets=list(ACTIVE_ASSETS), claude_enabled=cfg.claude_ready)
        if cfg.verify_btc_on_start:
            from .diagnose_market import main as diagnose
            tasks.append(asyncio.create_task(diagnose()))
        try:
            for _ in range(30):
                cached = await redis.get("snapshot:BTC/USD")
                if cached:
                    import json
                    from .schemas import Snapshot
                    snapshot = Snapshot.model_validate(json.loads(cached)["data"])
                    if (utcnow()-snapshot.captured_at).total_seconds() <= cfg.stale_after_seconds:
                        break
                await asyncio.sleep(2)
            tasks.append(asyncio.create_task(analysis_loop(service)))
            if cfg.verify_btc_on_start:
                tasks.append(asyncio.create_task(verify_public_once()))
            while True:
                try:
                    async with redis.lock("jarvis:outbox", timeout=180, blocking_timeout=1):
                        async with Session() as session:
                            await deliver(session, client, cfg)
                            await evaluate_pending(session, cfg.evaluation_horizon_minutes)
                except Exception as exc:
                    log.error("outbox_failed", error_type=type(exc).__name__)
                for task in tasks:
                    if task.done() and not task.cancelled() and task.exception():
                        raise task.exception()
                await asyncio.sleep(5)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await service.claude.client.close()
    await redis.aclose()


if __name__ == "__main__":
    asyncio.run(main())
