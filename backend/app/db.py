from datetime import datetime
from uuid import uuid4

from sqlalchemy import JSON, DateTime, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .config import settings
from .schemas import utcnow


class Base(DeclarativeBase):
    pass


def uid():
    return str(uuid4())


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    email: Mapped[str] = mapped_column(String, unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String, default="viewer")


class MarketSnapshot(Base):
    __tablename__ = "snapshots"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    asset: Mapped[str] = mapped_column(String, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    data: Mapped[dict] = mapped_column(JSON)


class AnalysisRecord(Base):
    __tablename__ = "analyses"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    snapshot_id: Mapped[str] = mapped_column(String, index=True)
    asset: Mapped[str] = mapped_column(String, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    decision: Mapped[str] = mapped_column(String)
    data: Mapped[dict] = mapped_column(JSON)
    model: Mapped[str] = mapped_column(String)
    mode: Mapped[str] = mapped_column(String, default="EXPERIMENTAL")
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)


class Alert(Base):
    __tablename__ = "alerts"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    analysis_id: Mapped[str] = mapped_column(String, unique=True)
    asset: Mapped[str] = mapped_column(String, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    state: Mapped[str] = mapped_column(String, default="PENDENTE DE CONFIRMAÇÃO")
    data: Mapped[dict] = mapped_column(JSON)


class Delivery(Base):
    __tablename__ = "deliveries"
    __table_args__ = (UniqueConstraint("event_key", "chat_id"),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    event_key: Mapped[str] = mapped_column(String)
    chat_id: Mapped[str] = mapped_column(String)
    text: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String, default="PENDING")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    error: Mapped[str | None] = mapped_column(String, nullable=True)


class Evaluation(Base):
    __tablename__ = "evaluations"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    analysis_id: Mapped[str] = mapped_column(String, unique=True)
    asset: Mapped[str] = mapped_column(String)
    timeframe: Mapped[str] = mapped_column(String, default="M1")
    data: Mapped[dict] = mapped_column(JSON)


class PaperTrade(Base):
    __tablename__ = "paper_trades"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    analysis_id: Mapped[str] = mapped_column(String, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    data: Mapped[dict] = mapped_column(JSON)


class Audit(Base):
    __tablename__ = "audit"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    action: Mapped[str] = mapped_column(String)
    actor: Mapped[str] = mapped_column(String)
    details: Mapped[dict] = mapped_column(JSON)


engine = create_async_engine(settings().database_url, pool_pre_ping=True)
Session = async_sessionmaker(engine, expire_on_commit=False)


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
