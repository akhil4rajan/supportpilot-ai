"""Endpoints used by the embeddable chat widget (browser)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app import metrics
from app.auth import AuthError, VerifiedUser, issue_widget_token, verify_user_token, verify_widget_token
from app.services import conversations as convs
from app.services.handoff import start_handoff
from app.state import AppState
from app.tenancy.registry import TenantRuntime

from .deps import WidgetCtx, get_db, get_state, resolve_widget, widget_ctx, ws_token_or_close
from .ws import pump

router = APIRouter(prefix="/v1/widget", tags=["widget"])


class SessionIn(BaseModel):
    public_key: str
    user_token: str | None = None  # signed by the customer's backend; proves who the user is
    resume_token: str | None = None  # previous widget token (anonymous visitors)


class MessageIn(BaseModel):
    content: str = Field(min_length=1)


def _origin_ok(rt: TenantRuntime, request: Request, is_dev: bool) -> bool:
    allowed = rt.tenant.allowed_origins
    origin = request.headers.get("origin")
    if "*" in allowed:
        return is_dev  # wildcard honoured in dev only
    if origin is None:
        return is_dev
    return origin in allowed


def _public_config(rt: TenantRuntime) -> dict:
    t = rt.tenant
    return {
        "tenant_name": t.name,
        "assistant_name": t.persona.assistant_name,
        "branding": t.branding.model_dump(),
        "features": {"orders": rt.orders is not None, "tickets": rt.tickets is not None},
    }


async def _state_payload(db: AsyncSession, ctx_conv, token: str, rt: TenantRuntime) -> dict:
    msgs = await convs.history(db, ctx_conv.id)
    return {
        "token": token,
        "conversation": convs.conv_out(ctx_conv),
        "messages": [convs.msg_out(m) for m in msgs],
        "config": _public_config(rt),
    }


@router.get("/config")
async def public_config(public_key: str, request: Request, st: AppState = Depends(get_state)):
    """Branding for the launcher button; no conversation is created until the visitor opens the chat."""
    rt = st.registry.by_public_key(public_key)
    if not rt or not _origin_ok(rt, request, st.settings.is_dev):
        raise HTTPException(404, "unknown tenant")
    return _public_config(rt)


@router.post("/sessions")
async def create_session(
    body: SessionIn, request: Request, st: AppState = Depends(get_state), db: AsyncSession = Depends(get_db)
):
    rt = st.registry.by_public_key(body.public_key)
    if not rt:
        raise HTTPException(404, "unknown tenant")
    if not _origin_ok(rt, request, st.settings.is_dev):
        raise HTTPException(403, "origin not allowed for this tenant")

    user: VerifiedUser | None = None
    if body.user_token:
        try:
            user = verify_user_token(rt.tenant, body.user_token)
        except AuthError as e:
            raise HTTPException(401, str(e)) from e

    conv = None
    if body.resume_token:
        try:
            claims = verify_widget_token(st.settings.server_secret, body.resume_token)
            cand = await convs.get_conversation(db, rt.tenant.id, claims.conversation_id)
            # A signed-in conversation may only be resumed by the same verified user.
            if cand and cand.status != "resolved" and (not cand.authenticated or (user and cand.user_id == user.id)):
                conv = cand
        except AuthError:
            pass
    if conv is None and user:
        conv = await convs.find_resumable(db, rt.tenant.id, user.id)
    if conv is None:
        conv = await convs.create_conversation(db, rt.tenant.id, user)

    token = issue_widget_token(st.settings.server_secret, rt.tenant.id, conv.id, st.settings.session_ttl_minutes)
    return await _state_payload(db, conv, token, rt)


@router.get("/conversation")
async def get_conversation(ctx: WidgetCtx = Depends(widget_ctx)):
    msgs = await convs.history(ctx.db, ctx.conv.id)
    return {"conversation": convs.conv_out(ctx.conv), "messages": [convs.msg_out(m) for m in msgs]}


@router.post("/messages")
async def send_message(body: MessageIn, ctx: WidgetCtx = Depends(widget_ctx)):
    st = ctx.st
    text = body.content.strip()
    if not text:
        raise HTTPException(422, "empty message")
    if len(text) > st.settings.max_message_chars:
        raise HTTPException(413, f"message too long (max {st.settings.max_message_chars} characters)")
    if not await st.limiter.allow(ctx.conv.id):
        raise HTTPException(429, "you're sending messages too quickly")
    new = await st.agent.handle_user_message(ctx.db, ctx.rt, ctx.conv, ctx.user, text)
    return {"messages": new, "conversation": convs.conv_out(ctx.conv)}


@router.post("/handoff")
async def request_human(ctx: WidgetCtx = Depends(widget_ctx)):
    """The widget's explicit 'Talk to a human' button. No model involved."""
    msgs = await convs.history(ctx.db, ctx.conv.id, limit=6)
    summary = " | ".join(f"{m.role}: {m.content[:120]}" for m in msgs if m.role != "system") or "(no messages yet)"
    await start_handoff(ctx.db, ctx.st.hub, ctx.rt, ctx.conv, "Customer pressed 'talk to a human'", summary, trigger="button")
    return {"conversation": convs.conv_out(ctx.conv)}


@router.websocket("/ws")
async def widget_ws(ws: WebSocket):
    token = await ws_token_or_close(ws)
    if not token:
        return
    st: AppState = ws.app.state.sp
    async with st.db.session() as db:
        try:
            ctx = await resolve_widget(st, db, token)
        except Exception:  # noqa: BLE001
            await ws.close(code=4401)
            return
    await ws.accept()
    metrics.WS_CONNECTIONS.labels("customer").inc()
    try:
        with st.hub.subscribe_conversation(ctx.conv.id) as q:
            try:
                await pump(ws, q)
            except WebSocketDisconnect:
                pass
    finally:
        metrics.WS_CONNECTIONS.labels("customer").dec()
