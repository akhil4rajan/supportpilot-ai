from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import VerifiedUser
from app.db import Conversation, Message, Ticket, utcnow
from app.services.realtime import Hub

HANDOFF_STATES = ("waiting_human", "with_human")


def msg_out(m: Message) -> dict[str, Any]:
    return {"id": m.id, "role": m.role, "content": m.content, "meta": m.meta or {}, "created_at": m.created_at.isoformat()}


def conv_out(c: Conversation) -> dict[str, Any]:
    return {
        "id": c.id,
        "status": c.status,
        "user": {"id": c.user_id, "name": c.user_name, "email": c.user_email, "authenticated": c.authenticated},
        "handoff_reason": c.handoff_reason,
        "handoff_summary": c.handoff_summary,
        "handoff_at": c.handoff_at.isoformat() if c.handoff_at else None,
        "assigned_agent": c.assigned_agent_name,
        "created_at": c.created_at.isoformat(),
        "updated_at": c.updated_at.isoformat(),
    }


async def create_conversation(db: AsyncSession, tenant_id: str, user: VerifiedUser | None) -> Conversation:
    c = Conversation(
        tenant_id=tenant_id,
        user_id=user.id if user else None,
        user_name=user.name if user else None,
        user_email=user.email if user else None,
        authenticated=user is not None,
    )
    db.add(c)
    await db.commit()
    return c


async def find_resumable(db: AsyncSession, tenant_id: str, user_id: str) -> Conversation | None:
    """Verified users get their latest unresolved conversation back, on any device."""
    q = (
        select(Conversation)
        .where(Conversation.tenant_id == tenant_id, Conversation.user_id == user_id, Conversation.status != "resolved")
        .order_by(Conversation.updated_at.desc())
        .limit(1)
    )
    return (await db.execute(q)).scalar_one_or_none()


async def get_conversation(db: AsyncSession, tenant_id: str, conversation_id: str) -> Conversation | None:
    c = await db.get(Conversation, conversation_id)
    return c if c and c.tenant_id == tenant_id else None  # tenant isolation on every lookup


async def history(db: AsyncSession, conversation_id: str, limit: int = 200) -> list[Message]:
    q = select(Message).where(Message.conversation_id == conversation_id).order_by(Message.id.desc()).limit(limit)
    return list(reversed((await db.execute(q)).scalars().all()))


async def add_message(
    db: AsyncSession, hub: Hub, conv: Conversation, role: str, content: str, meta: dict[str, Any] | None = None
) -> Message:
    m = Message(conversation_id=conv.id, role=role, content=content, meta=meta or {})
    db.add(m)
    conv.updated_at = utcnow()
    await db.commit()
    event = {"type": "message", "conversation_id": conv.id, "message": msg_out(m)}
    await hub.to_customer(conv.id, event)
    if conv.status in HANDOFF_STATES or role == "system":
        await hub.to_agents(conv.tenant_id, event)
    return m


async def publish_update(hub: Hub, conv: Conversation) -> None:
    event = {"type": "conversation_updated", "conversation": conv_out(conv)}
    await hub.to_customer(conv.id, event)
    await hub.to_agents(conv.tenant_id, event)


async def tickets_for(db: AsyncSession, conversation_id: str) -> list[Ticket]:
    return list((await db.execute(select(Ticket).where(Ticket.conversation_id == conversation_id))).scalars())
