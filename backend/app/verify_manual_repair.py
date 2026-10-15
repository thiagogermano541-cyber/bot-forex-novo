"""One explicit retry of a failed manual request; disabled by default and deduplicated."""
import asyncio

import httpx
import structlog

from .config import settings

log = structlog.get_logger()


async def run_once(redis):
    cfg = settings()
    if not cfg.verify_manual_repair_id:
        return
    key = f"manual-repair:{cfg.verify_manual_repair_id}"
    if not await redis.set(key, "STARTED", nx=True):
        return
    origin = cfg.app_origin.rstrip("/")
    async with httpx.AsyncClient(timeout=300, follow_redirects=False) as client:
        try:
            for _ in range(60):
                ready = await client.get(f"{origin}/api/health/ready")
                if ready.status_code == 200:
                    if not client.cookies:
                        login = await client.post(f"{origin}/api/auth/login", headers={"Origin": origin},
                            json={"email": cfg.admin_email, "password": cfg.admin_password.get_secret_value()})
                        login.raise_for_status()
                    config = await client.get(f"{origin}/api/config")
                    if config.status_code == 200 and config.json().get("analysis_protocol_version") == 9:
                        break
                await asyncio.sleep(2)
            else:
                raise RuntimeError("Updated API did not become ready")
            response = await client.post(f"{origin}/api/analyses", headers={"Origin": origin}, json={"asset": "BTC/USD"})
            if response.status_code == 200 and response.json().get("model") == cfg.claude_model:
                result = response.json()
                history = await client.get(f"{origin}/api/analyses")
                history.raise_for_status()
                if not any(row["id"] == result["id"] for row in history.json()):
                    raise RuntimeError("New analysis missing from public history")
                log.info("manual_repair_verified", status="PASS", analysis_id=result["id"],
                         decision=result["decision"], model=result["model"], cost_usd=result["cost_usd"],
                         plan_status=(result["data"].get("trade_plan") or {}).get("status"))
                await redis.set(key, "PASS")
            else:
                log.warning("manual_repair_verified", status="FAIL", http_status=response.status_code,
                            message=response.json().get("detail", "Análise não concluída pelo Claude"))
                await redis.set(key, "FAIL")
        except Exception as exc:
            log.warning("manual_repair_verified", status="FAIL", error_type=type(exc).__name__)
            await redis.set(key, "FAIL")
        finally:
            if client.cookies:
                try:
                    await client.post(f"{origin}/api/auth/logout", headers={"Origin": origin}, json={})
                except httpx.HTTPError:
                    pass
