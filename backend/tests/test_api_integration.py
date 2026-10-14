from unittest.mock import AsyncMock

import httpx

from app import db
from app.main import app
from app.claude import ClaudeProviderFailure


async def test_auth_integrity_and_persistence(monkeypatch):
    # SQLite is a real test database; only Redis/network dependencies are mocked.
    async with db.engine.begin() as conn:
        await conn.run_sync(db.Base.metadata.drop_all)
    fake_redis = AsyncMock()
    fake_redis.eval.return_value = 1
    fake_redis.get.return_value = None
    monkeypatch.setattr("app.main.Redis.from_url", lambda *a, **kw: fake_redis)
    origin = {"Origin": "http://localhost:3000"}
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get("/markets")).status_code == 401
            assert (await client.post("/auth/login", json={"email": "admin@jarvis.local", "password": "test-only-admin-password"})).status_code == 403
            response = await client.post("/auth/login", headers=origin, json={"email": "admin@jarvis.local", "password": "test-only-admin-password"})
            assert response.status_code == 200
            assert "HttpOnly" in response.headers["set-cookie"]
            config = (await client.get("/config")).json()
            assert config["claude"] == "NÃO CONFIGURADO"
            assert config["analysis_mode"] == "MANUAL"
            assert config["daily_budget_enabled"] is False
            assert "anthropic_api_key" not in config
            assert (await client.post("/analyses", headers=origin, json={"asset": "ETH/USD"})).status_code == 422
            # Lock is only a test stand-in; production uses Redis distributed leases.
            class Lease:
                async def __aenter__(self):
                    return self
                async def __aexit__(self, *args):
                    return False
            fake_redis.lock = lambda *a, **kw: Lease()
            assert (await client.post("/analyses", headers=origin, json={"asset": "XAU/USD"})).status_code == 422
            response = await client.post("/analyses", headers=origin, json={"asset": "BTC/USD"})
            assert response.status_code == 200
            assert response.json()["decision"] == "DADOS INSUFICIENTES"
            assert response.json()["verdict"] == "AGUARDAR"
            assert response.json()["model"] == "integrity-gate"
            assert len((await client.get("/analyses")).json()) == 1
            assert (await client.get("/alerts")).json() == []
            real_run = app.state.service.run
            monkeypatch.setattr(app.state.service, "run", AsyncMock(side_effect=ClaudeProviderFailure("Limite da Anthropic", 429)))
            limited = await client.post("/analyses", headers=origin, json={"asset": "BTC/USD"})
            assert limited.status_code == 429
            assert limited.json()["detail"] == "Limite da Anthropic"
            assert limited.headers["retry-after"] == "30"
            monkeypatch.setattr(app.state.service, "run", real_run)
            await client.post("/users", headers=origin, json={"email": "viewer@test.local", "password": "test-only-viewer-password", "role": "viewer"})
            await client.post("/auth/logout", headers=origin, json={})
            await client.post("/auth/login", headers=origin, json={"email": "viewer@test.local", "password": "test-only-viewer-password"})
            assert (await client.post("/analyses", headers=origin, json={"asset": "BTC/USD"})).status_code == 403
