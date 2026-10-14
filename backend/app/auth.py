import asyncio
from datetime import timedelta

import jwt
from fastapi import Depends, HTTPException, Request
from pwdlib import PasswordHash
from sqlalchemy import select

from .config import settings
from .db import Session, User
from .schemas import utcnow

hasher = PasswordHash.recommended()
DUMMY_HASH = hasher.hash("unused-authentication-placeholder")


async def seed_admin():
    cfg = settings()
    if len(cfg.jwt_secret.get_secret_value()) < 32 or len(cfg.admin_password.get_secret_value()) < 12:
        raise RuntimeError("Configure JWT_SECRET (32+ caracteres) e ADMIN_PASSWORD (12+ caracteres)")
    async with Session() as session:
        if not await session.scalar(select(User).where(User.email == cfg.admin_email)):
            session.add(User(email=cfg.admin_email, password_hash=await asyncio.to_thread(hasher.hash, cfg.admin_password.get_secret_value()), role="admin"))
            await session.commit()


def token_for(user):
    return jwt.encode({"sub": user.id, "exp": utcnow()+timedelta(hours=8), "iat": utcnow(), "iss": "jarvis-ai", "aud": "jarvis-dashboard"}, settings().jwt_secret.get_secret_value(), algorithm="HS256")


async def current_user(request: Request):
    try:
        payload = jwt.decode(request.cookies.get("jarvis_session", ""), settings().jwt_secret.get_secret_value(), algorithms=["HS256"], issuer="jarvis-ai", audience="jarvis-dashboard")
        async with Session() as session:
            user = await session.get(User, payload["sub"])
        if not user:
            raise ValueError("Missing user")
        return user
    except (jwt.InvalidTokenError, ValueError, KeyError):
        raise HTTPException(401, "Autenticação necessária") from None


async def admin(user=Depends(current_user)):
    if user.role != "admin":
        raise HTTPException(403, "Permissão de administrador necessária")
    return user
