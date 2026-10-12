from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    pass


class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)
    user_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    user_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    authenticated: Mapped[bool] = mapped_column(Boolean, default=False)
    # bot | waiting_human | with_human | resolved
    status: Mapped[str] = mapped_column(String(20), default="bot", index=True)
    handoff_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    handoff_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    handoff_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    assigned_agent_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    assigned_agent_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    unanswered_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    __table_args__ = (
        Index("ix_conv_queue", "tenant_id", "status", "handoff_at"),  # agent queue listing
        Index("ix_conv_user", "tenant_id", "user_id", "status", "updated_at"),  # resume a signed-in user's chat
    )


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    # user | assistant | agent | system
    role: Mapped[str] = mapped_column(String(12))
    content: Mapped[str] = mapped_column(Text)
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Ticket(Base):
    __tablename__ = "tickets"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    key: Mapped[str] = mapped_column(String(64))
    url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    summary: Mapped[str] = mapped_column(String(300))
    provider: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TicketOutbox(Base):
    """Tickets that could not be filed right now (tracker down/rate-limited). A worker retries with backoff, so a
    Jira outage delays tickets instead of losing them."""

    __tablename__ = "ticket_outbox"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(12), default="pending")  # pending | processing | done | dead
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    ticket_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (Index("ix_outbox_due", "status", "next_attempt_at"),)


class AuditLog(Base):
    """Who looked at what. Order lookups (found or not) are recorded for security review."""

    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    conversation_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    actor: Mapped[str] = mapped_column(String(160))
    action: Mapped[str] = mapped_column(String(64))
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (Index("ix_audit_tenant_created", "tenant_id", "created_at"),)  # investigations: "what happened in this window?"


class Database:
    def __init__(self, url: str, pool_size: int = 10, max_overflow: int = 20) -> None:
        kwargs: dict[str, Any] = {}
        if not url.startswith("sqlite"):  # pool sizing applies to server databases (Postgres), not SQLite
            kwargs = {"pool_size": pool_size, "max_overflow": max_overflow, "pool_pre_ping": True, "pool_recycle": 1800}
        self.engine = create_async_engine(url, future=True, **kwargs)
        self.sessionmaker = async_sessionmaker(self.engine, expire_on_commit=False)

    async def create_all(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def dispose(self) -> None:
        await self.engine.dispose()

    def session(self) -> AsyncSession:
        return self.sessionmaker()
