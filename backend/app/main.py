import asyncio
import logging
from contextlib import asynccontextmanager

import httpx
import structlog
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from redis.asyncio import Redis
from sqlalchemy import func, select, text

from .auth import DUMMY_HASH, admin, current_user, hasher, seed_admin, token_for
from .claude import ClaudeUnavailable, InvalidClaudeResponse
from .config import settings
from .db import Alert, AnalysisRecord, Audit, Delivery, Evaluation, MarketSnapshot, PaperTrade, Session, User, engine, init_db
from .evaluation import evaluate_pending
from .schemas import ACTIVE_ASSETS, Asset, Decision, Snapshot, utcnow
from .service import AnalysisService, serialize
from .telegram import allowed_chats, enqueue

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
structlog.configure(processors=[structlog.processors.TimeStamper(fmt="iso"), structlog.processors.JSONRenderer()])
log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app):
    await init_db()
    await seed_admin()
    app.state.redis = Redis.from_url(settings().redis_url, decode_responses=True)
    app.state.http = httpx.AsyncClient(timeout=20)
    app.state.service = AnalysisService(app.state.redis, app.state.http)
    yield
    await app.state.service.claude.client.close()
    await app.state.http.aclose()
    await app.state.redis.aclose()
    await engine.dispose()


app = FastAPI(title="JARVIS AI", version="0.1.0", lifespan=lifespan)
RATE_SCRIPT = "local n=redis.call('INCR',KEYS[1]); if n==1 then redis.call('EXPIRE',KEYS[1],ARGV[1]) end; return n"


@app.middleware("http")
async def protections(request: Request, call_next):
    if request.method in ("POST", "PUT", "PATCH", "DELETE") and request.headers.get("origin") != settings().app_origin:
        return JSONResponse({"detail": "Origem não autorizada"}, status_code=403)
    if request.url.path not in ("/health/live", "/health/ready"):
        category = "login" if request.url.path == "/auth/login" else "api"
        key = f"ratelimit:{request.client.host}:{category}"
        try:
            count = await request.app.state.redis.eval(RATE_SCRIPT, 1, key, 60)
        except Exception:
            return JSONResponse({"detail": "Rate limiter indisponível"}, status_code=503)
        if count > (10 if category == "login" else 120):
            return JSONResponse({"detail": "Limite de requisições"}, status_code=429)
    try:
        response = await call_next(request)
    except Exception as exc:
        log.error("request_failed", path=request.url.path, error_type=type(exc).__name__)
        return JSONResponse({"detail": "Serviço temporariamente indisponível"}, status_code=503)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/health/live")
async def live():
    return {"status": "alive"}


@app.get("/health/ready")
async def ready(request: Request):
    try:
        async with Session() as session:
            await session.execute(text("SELECT 1"))
        await request.app.state.redis.ping()
    except Exception:
        raise HTTPException(503, "Database/cache unavailable") from None
    return {"status": "ready"}


class Login(BaseModel):
    email: str = Field(max_length=200)
    password: str = Field(max_length=256)


@app.post("/auth/login")
async def login(body: Login, response: Response):
    async with Session() as session:
        user = await session.scalar(select(User).where(User.email == body.email))
        valid = await asyncio.to_thread(hasher.verify, body.password, user.password_hash if user else DUMMY_HASH)
        if not user or not valid:
            raise HTTPException(401, "Credenciais inválidas")
        session.add(Audit(action="LOGIN", actor=user.id, details={}))
        await session.commit()
    response.set_cookie("jarvis_session", token_for(user), httponly=True, secure=settings().cookie_secure, samesite="strict", max_age=28800, path="/")
    return {"email": user.email, "role": user.role}


@app.post("/auth/logout")
async def logout(response: Response):
    response.delete_cookie("jarvis_session", path="/")
    return {"ok": True}


@app.get("/auth/me")
async def me(user=Depends(current_user)):
    return {"email": user.email, "role": user.role}


class NewUser(Login):
    role: str = Field(pattern="^(viewer|admin)$")


@app.post("/users")
async def create_user(body: NewUser, actor=Depends(admin)):
    if len(body.password) < 12:
        raise HTTPException(422, "Senha deve ter ao menos 12 caracteres")
    async with Session() as session:
        if await session.scalar(select(User).where(User.email == body.email)):
            raise HTTPException(409, "Usuário existente")
        user = User(email=body.email, password_hash=await asyncio.to_thread(hasher.hash, body.password), role=body.role)
        session.add(user)
        session.add(Audit(action="CREATE_USER", actor=actor.id, details={"role": body.role}))
        await session.commit()
    return {"id": user.id, "role": user.role}


@app.get("/config")
async def config(request: Request, user=Depends(current_user)):
    cfg = settings()
    claude_state = "PAUSADO" if not cfg.claude_enabled else ("CONFIGURADO" if cfg.claude_ready else "NÃO CONFIGURADO")
    return {"claude": claude_state, "claude_model": cfg.claude_model, "claude_pause_reason": cfg.claude_pause_reason,
            "kraken": "HABILITADO" if cfg.kraken_enabled else "NÃO CONFIGURADO", "assets": list(ACTIVE_ASSETS),
            "market_refresh_seconds": cfg.market_refresh_seconds,
            "telegram": "CONFIGURADO" if cfg.telegram_enabled and cfg.telegram_bot_token.get_secret_value() and allowed_chats(cfg) else "NÃO CONFIGURADO",
            "analysis_interval_seconds": cfg.analysis_interval_seconds, "daily_budget_usd": cfg.daily_ai_budget_usd,
            "stale_after_seconds": cfg.stale_after_seconds, "worker": "ONLINE" if await request.app.state.redis.get("worker:heartbeat") else "OFFLINE",
            "pricing": {"input_per_million": cfg.claude_input_usd_per_million, "output_per_million": cfg.claude_output_usd_per_million}}


@app.get("/markets")
async def markets(request: Request, user=Depends(current_user)):
    result = []
    async with Session() as session:
        for asset in ACTIVE_ASSETS:
            row = await session.scalar(select(MarketSnapshot).where(MarketSnapshot.asset == asset).order_by(MarketSnapshot.created_at.desc()).limit(1))
            data = Snapshot.model_validate(row.data) if row else None
            issues = data.integrity_issues(settings().stale_after_seconds) if data else ["SEM DADOS"]
            if data and (utcnow()-data.captured_at).total_seconds() > settings().stale_after_seconds:
                issues.append("Snapshot desatualizado")
            result.append({"asset": asset, "snapshot_id": row.id if row else None, "snapshot": row.data if row else None,
                           "status": "DADOS INSUFICIENTES" if issues else "ATUALIZADO", "issues": issues,
                           "stream": await request.app.state.redis.get(f"stream:{asset}"),
                           "error": await request.app.state.redis.get(f"error:{asset}")})
    return result


@app.get("/analyses")
async def analyses(asset: Asset | None = None, user=Depends(current_user)):
    async with Session() as session:
        query = select(AnalysisRecord).where(AnalysisRecord.asset.in_(ACTIVE_ASSETS)).order_by(AnalysisRecord.created_at.desc()).limit(100)
        if asset:
            query = query.where(AnalysisRecord.asset == asset)
        return [serialize(r) for r in (await session.scalars(query)).all()]


class RunRequest(BaseModel):
    asset: Asset


@app.post("/analyses")
async def run(body: RunRequest, request: Request, user=Depends(admin)):
    try:
        return serialize(await request.app.state.service.run(body.asset, actor=user.id))
    except (ClaudeUnavailable, InvalidClaudeResponse) as exc:
        raise HTTPException(503, str(exc)) from None


@app.get("/alerts")
async def alerts(user=Depends(current_user)):
    async with Session() as session:
        return [serialize(r) for r in (await session.scalars(select(Alert).where(Alert.asset.in_(ACTIVE_ASSETS)).order_by(Alert.created_at.desc()).limit(100))).all()]


@app.get("/deliveries")
async def deliveries(user=Depends(admin)):
    async with Session() as session:
        rows = (await session.scalars(select(Delivery).order_by(Delivery.next_attempt.desc()).limit(100))).all()
        return [{"id": r.id, "state": r.state, "attempts": r.attempts, "error": r.error} for r in rows]


@app.get("/snapshots")
async def snapshots(user=Depends(current_user)):
    async with Session() as session:
        rows = (await session.scalars(select(MarketSnapshot).where(MarketSnapshot.asset.in_(ACTIVE_ASSETS)).order_by(MarketSnapshot.created_at.desc()).limit(100))).all()
        return [{"id": r.id, "asset": r.asset, "created_at": r.created_at.isoformat()} for r in rows]


@app.get("/snapshots/{snapshot_id}")
async def snapshot_detail(snapshot_id: str, user=Depends(current_user)):
    async with Session() as session:
        row = await session.get(MarketSnapshot, snapshot_id)
        if not row or row.asset not in ACTIVE_ASSETS:
            raise HTTPException(404, "Snapshot não encontrado")
        return serialize(row)


class ReplayRequest(BaseModel):
    snapshot_id: str
    asset: Asset


@app.post("/backtest/replay")
async def replay(body: ReplayRequest, request: Request, user=Depends(admin)):
    try:
        return serialize(await request.app.state.service.run(body.asset, actor=user.id, replay_id=body.snapshot_id))
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from None
    except (ClaudeUnavailable, InvalidClaudeResponse) as exc:
        raise HTTPException(503, str(exc)) from None


class PaperRequest(BaseModel):
    analysis_id: str
    quantity: float = Field(gt=0, le=100000)
    fee_bps: float = Field(ge=0, le=1000)


@app.post("/paper")
async def paper(body: PaperRequest, user=Depends(admin)):
    async with Session() as session:
        record = await session.get(AnalysisRecord, body.analysis_id)
        if not record or record.asset not in ACTIVE_ASSETS or record.decision not in (Decision.BULL, Decision.BEAR) or record.mode != "EXPERIMENTAL":
            raise HTTPException(422, "Selecione uma análise direcional experimental")
        snapshot_row = await session.get(MarketSnapshot, record.snapshot_id)
        snapshot = Snapshot.model_validate(snapshot_row.data)
        if (utcnow()-snapshot.captured_at).total_seconds() > settings().stale_after_seconds:
            raise HTTPException(422, "Análise desatualizada")
        if await session.scalar(select(PaperTrade).where(PaperTrade.analysis_id == record.id)):
            raise HTTPException(409, "Paper trade já cadastrado")
        entry = snapshot.quote.ask if record.decision == Decision.BULL else snapshot.quote.bid
        row = PaperTrade(analysis_id=record.id, data={"state": "OPEN", "kind": "SIMULAÇÃO", "entry_price": entry,
                         "quantity": body.quantity, "fee_bps": body.fee_bps, "asset": record.asset,
                         "horizon_minutes": settings().evaluation_horizon_minutes})
        session.add(row)
        session.add(Audit(action="PAPER_TRADE", actor=user.id, details={"analysis_id": record.id}))
        await session.commit()
        return serialize(row)


@app.get("/paper")
async def paper_history(user=Depends(current_user)):
    async with Session() as session:
        return [serialize(r) for r in (await session.scalars(select(PaperTrade).where(PaperTrade.data["asset"].as_string() == "BTC/USD").order_by(PaperTrade.created_at.desc()).limit(100))).all()]


@app.post("/evaluations")
async def evaluate(user=Depends(admin)):
    async with Session() as session:
        await evaluate_pending(session, settings().evaluation_horizon_minutes)
    return {"ok": True}


@app.get("/metrics")
async def metrics(user=Depends(current_user)):
    async with Session() as session:
        count, cost, latency = (await session.execute(select(func.count(AnalysisRecord.id), func.coalesce(func.sum(AnalysisRecord.cost_usd), 0), func.coalesce(func.avg(AnalysisRecord.latency_ms), 0)))).one()
        failed_cost = await session.scalar(select(func.coalesce(func.sum(Audit.details["cost_usd"].as_float()), 0)).where(Audit.action.in_(["CLAUDE_INVALID", "CLAUDE_HEALTHCHECK"])))
        budget_reserved = await session.scalar(select(func.coalesce(func.sum(Audit.details["reserved_usd"].as_float()), 0)).where(Audit.action == "AI_BUDGET", Audit.created_at >= utcnow().replace(hour=0, minute=0, second=0, microsecond=0)))
        alerts_count = await session.scalar(select(func.count(Alert.id)))
        invalidated = await session.scalar(select(func.count(Alert.id)).where(Alert.state == "INVALIDADO"))
        evaluated = (await session.scalars(select(Evaluation).where(Evaluation.asset.in_(ACTIVE_ASSETS)))).all()
        groups = []
        for asset in ACTIVE_ASSETS:
            for mode in ("EXPERIMENTAL", "BACKTEST"):
                rows = [r for r in evaluated if r.asset == asset and r.data["mode"] == mode]
                groups.append({"asset": asset, "timeframe": "M1", "mode": mode, "samples": len(rows),
                               "directional_accuracy": sum(r.data["correct"] for r in rows)/len(rows) if rows else None})
        return {"analyses": count, "alerts": alerts_count, "invalidated": invalidated, "cost_usd": cost+failed_cost,
                "latency_ms": latency, "evaluated": len(evaluated), "groups": groups, "budget_reserved_today_usd": budget_reserved,
                "evaluations": [serialize(r) for r in evaluated[-100:]], "validated_strategy": False}


@app.post("/telegram/summary")
async def summary(user=Depends(admin)):
    async with Session() as session:
        rows = []
        for asset in ACTIVE_ASSETS:
            row = await session.scalar(select(AnalysisRecord).where(AnalysisRecord.asset == asset, AnalysisRecord.mode == "EXPERIMENTAL").order_by(AnalysisRecord.created_at.desc()).limit(1))
            if row:
                rows.append(row)
        await enqueue(session, settings(), f"summary:{utcnow().strftime('%Y%m%d%H')}", "JARVIS • Resumo experimental\n"+"\n".join(f"{r.asset}: {r.decision}\n{r.data['summary']}\nUTC: {r.created_at.isoformat()}" for r in rows))
        await session.commit()
    return {"status": "ENFILEIRADO" if settings().telegram_enabled and settings().telegram_bot_token.get_secret_value() and allowed_chats(settings()) else "NÃO CONFIGURADO"}
