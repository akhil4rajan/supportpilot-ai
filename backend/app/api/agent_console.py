"""Endpoints for human support agents (the console)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import AgentClaims, AuthError, check_agent_key, issue_agent_token, verify_agent_token
from app.db import Conversation
from app.services import conversations as convs
from app.state import AppState

from .deps import agent_claims, get_db, get_state, ws_token_or_close
from .ws import pump

router = APIRouter(prefix="/v1/agent", tags=["agent-console"])


class LoginIn(BaseModel):
    tenant_id: str
    api_key: str
    agent_id: str | None = None


class AgentMessageIn(BaseModel):
    content: str = Field(min_length=1, max_length=4000)


@router.post("/login")
async def login(body: LoginIn, st: AppState = Depends(get_state)):
    rt = st.registry.by_id(body.tenant_id)
    found = check_agent_key(rt.tenant, body.agent_id, body.api_key) if rt else None
    if not rt or not found:
        raise HTTPException(401, "invalid credentials")  # same answer for unknown tenant and bad key
    agent_id, name = found
    token = issue_agent_token(st.settings.server_secret, rt.tenant.id, agent_id, name, st.settings.agent_token_ttl_minutes)
    return {"token": token, "agent": {"id": agent_id, "name": name}, "tenant": {"id": rt.tenant.id, "name": rt.tenant.name}}


async def _load(db: AsyncSession, claims: AgentClaims, cid: str) -> Conversation:
    conv = await convs.get_conversation(db, claims.tenant_id, cid)
    if not conv:
        raise HTTPException(404, "conversation not found")
    return conv


@router.get("/conversations")
async def list_conversations(
    status: str | None = Query(None, description="comma-separated; default = waiting_human,with_human"),
    claims: AgentClaims = Depends(agent_claims),
    db: AsyncSession = Depends(get_db),
):
    wanted = [s for s in (status or "waiting_human,with_human").split(",") if s in ("bot", "waiting_human", "with_human", "resolved")]
    q = (
        select(Conversation)
        .where(Conversation.tenant_id == claims.tenant_id, Conversation.status.in_(wanted))
        .order_by(Conversation.handoff_at.asc().nulls_last(), Conversation.updated_at.desc())
        .limit(100)
    )
    out = []
    for c in (await db.execute(q)).scalars():
        last = (await convs.history(db, c.id, limit=1))
        out.append({**convs.conv_out(c), "last_message": last[0].content[:140] if last else ""})
    return {"conversations": out}


@router.get("/conversations/{cid}")
async def get_conversation(cid: str, claims: AgentClaims = Depends(agent_claims), db: AsyncSession = Depends(get_db)):
    conv = await _load(db, claims, cid)
    tickets = await convs.tickets_for(db, cid)
    return {
        "conversation": convs.conv_out(conv),
        "messages": [convs.msg_out(m) for m in await convs.history(db, cid)],
        "tickets": [{"key": t.key, "url": t.url, "summary": t.summary} for t in tickets],
    }


@router.post("/conversations/{cid}/claim")
async def claim(cid: str, claims: AgentClaims = Depends(agent_claims), st: AppState = Depends(get_state), db: AsyncSession = Depends(get_db)):
    conv = await _load(db, claims, cid)
    if conv.status not in convs.HANDOFF_STATES:
        raise HTTPException(409, "conversation is not in the human queue")
    if conv.assigned_agent_id and conv.assigned_agent_id != claims.agent_id:
        raise HTTPException(409, f"already handled by {conv.assigned_agent_name}")
    first = conv.assigned_agent_id is None
    conv.status, conv.assigned_agent_id, conv.assigned_agent_name = "with_human", claims.agent_id, claims.name
    await db.commit()
    if first:
        await convs.add_message(db, st.hub, conv, "system", f"{claims.name} joined the conversation.", {"event": "agent_joined"})
    await convs.publish_update(st.hub, conv)
    return {"conversation": convs.conv_out(conv)}


@router.post("/conversations/{cid}/messages")
async def reply(
    cid: str, body: AgentMessageIn, claims: AgentClaims = Depends(agent_claims),
    st: AppState = Depends(get_state), db: AsyncSession = Depends(get_db),
):
    conv = await _load(db, claims, cid)
    if conv.status not in convs.HANDOFF_STATES:
        raise HTTPException(409, "conversation is not with a human; claim it from the queue first")
    if conv.assigned_agent_id and conv.assigned_agent_id != claims.agent_id:
        raise HTTPException(409, f"already handled by {conv.assigned_agent_name}")
    if conv.assigned_agent_id is None:
        await claim(cid, claims, st, db)
    m = await convs.add_message(db, st.hub, conv, "agent", body.content.strip(), {"agent": claims.name})
    return {"message": convs.msg_out(m)}


async def _close(cid: str, claims: AgentClaims, st: AppState, db: AsyncSession, status: str, note: str, event: str):
    conv = await _load(db, claims, cid)
    if conv.assigned_agent_id and conv.assigned_agent_id != claims.agent_id:
        raise HTTPException(409, f"already handled by {conv.assigned_agent_name}")
    conv.status = status
    conv.unanswered_count = 0
    if status == "bot":
        conv.assigned_agent_id = conv.assigned_agent_name = None
    await db.commit()
    await convs.add_message(db, st.hub, conv, "system", note, {"event": event})
    await convs.publish_update(st.hub, conv)
    return {"conversation": convs.conv_out(conv)}


@router.post("/conversations/{cid}/resolve")
async def resolve(cid: str, claims: AgentClaims = Depends(agent_claims), st: AppState = Depends(get_state), db: AsyncSession = Depends(get_db)):
    return await _close(cid, claims, st, db, "resolved", "This conversation was marked resolved. Send a message to start again.", "resolved")


@router.post("/conversations/{cid}/return-to-bot")
async def return_to_bot(cid: str, claims: AgentClaims = Depends(agent_claims), st: AppState = Depends(get_state), db: AsyncSession = Depends(get_db)):
    return await _close(cid, claims, st, db, "bot", "You're back with the virtual assistant.", "returned_to_bot")


@router.websocket("/ws")
async def agent_ws(ws: WebSocket):
    token = await ws_token_or_close(ws)
    if not token:
        return
    st: AppState = ws.app.state.sp
    try:
        claims = verify_agent_token(st.settings.server_secret, token)
    except AuthError:
        await ws.close(code=4401)
        return
    await ws.accept()
    with st.hub.subscribe_agents(claims.tenant_id) as q:
        try:
            await pump(ws, q)
        except WebSocketDisconnect:
            pass
