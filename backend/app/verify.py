"""Deployment smoke check against the public dashboard, without logging secrets."""
import asyncio
import json
import logging

import httpx

from .config import settings

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


async def main():
    cfg = settings()
    origin = cfg.app_origin.rstrip("/")
    if not origin.startswith("https://"):
        raise RuntimeError("Public HTTPS APP_ORIGIN required")
    async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
        for attempt in range(60):
            try:
                response = await client.get(f"{origin}/api/health/ready")
                if response.status_code == 200 and response.json().get("status") == "ready":
                    break
            except (httpx.HTTPError, ValueError):
                pass
            await asyncio.sleep(3)
        else:
            raise RuntimeError("Public API readiness check did not pass")
        response = await client.get(origin)
        response.raise_for_status()
        if "JARVIS" not in response.text:
            raise RuntimeError("Unexpected dashboard content")
        response = await client.post(f"{origin}/api/auth/login", headers={"Origin": origin},
                                     json={"email": cfg.admin_email, "password": cfg.admin_password.get_secret_value()})
        response.raise_for_status()
        if response.json().get("email") != cfg.admin_email:
            raise RuntimeError("Unexpected authenticated user")
        cookie_header = response.headers.get("set-cookie", "").lower()
        if not all(flag in cookie_header for flag in ("httponly", "secure", "samesite=strict")):
            raise RuntimeError("Session cookie protection missing")
        me = await client.get(f"{origin}/api/auth/me")
        me.raise_for_status()
        if me.json().get("role") != "admin":
            raise RuntimeError("Unexpected user permissions")
        config = await client.get(f"{origin}/api/config")
        config.raise_for_status()
        if any(key in config.json() for key in ("jwt_secret", "anthropic_api_key", "admin_password")):
            raise RuntimeError("Sensitive configuration exposed")
        for path in ("markets", "analyses", "alerts", "metrics"):
            result = await client.get(f"{origin}/api/{path}")
            result.raise_for_status()
        await client.post(f"{origin}/api/auth/logout", headers={"Origin": origin}, json={})
        print(json.dumps({"event": "public_login_verified", "status": "PASS", "domain": origin,
                          "claude_status": config.json().get("claude"),
                          "checks": ["dashboard", "readiness", "login", "secure_cookie", "permissions", "markets", "history", "alerts", "metrics", "no_exposed_secrets"]}), flush=True)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        print(json.dumps({"event": "public_login_verified", "status": "FAIL", "error_type": type(exc).__name__}), flush=True)
        raise SystemExit(1) from None
