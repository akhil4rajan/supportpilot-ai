from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator

from fastapi import Depends, Header, HTTPException, Request, WebSocket
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import AgentClaims, AuthError, VerifiedUser, verify_agent_token, verify_widget_token
from app.db import Conversation
from app.services import conversations as convs
from app.state import AppState
from app.tenancy.registry import TenantRuntime


def get_state(request: Request) -> AppState:
    return request.app.state.sp


async def get_db(st: AppState = Depends(get_state)) -> AsyncIterator[AsyncSession]:
    async with st.db.session() as s:
        yield s


def _bearer(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "missing bearer token")
    return authorization[7:].strip()


@dataclass
class WidgetCtx:
    st: AppState
    db: AsyncSession
    rt: TenantRuntime
    conv: Conversation

    @property
    def user(self) -> VerifiedUser | None:
        # Identity was verified once, at session creation, and stored on the conversation.
        c = self.conv
        return VerifiedUser(c.user_id, c.user_name, c.user_email) if c.authenticated and c.user_id else None


async def resolve_widget(st: AppState, db: AsyncSession, token: str) -> WidgetCtx:
    try:
        claims = verify_widget_token(st.settings.server_secret, token)
    except AuthError as e:
        raise HTTPException(401, str(e)) from e
    rt = st.registry.by_id(claims.tenant_id)
    conv = await convs.get_conversation(db, claims.tenant_id, claims.conversation_id) if rt else None
    if not rt or not conv:
        raise HTTPException(401, "session no longer valid")
    return WidgetCtx(st, db, rt, conv)


async def widget_ctx(
    authorization: str | None = Header(default=None),
    st: AppState = Depends(get_state),
    db: AsyncSession = Depends(get_db),
) -> WidgetCtx:
    return await resolve_widget(st, db, _bearer(authorization))


async def agent_claims(authorization: str | None = Header(default=None), st: AppState = Depends(get_state)) -> AgentClaims:
    try:
        claims = verify_agent_token(st.settings.server_secret, _bearer(authorization))
    except AuthError as e:
        raise HTTPException(401, str(e)) from e
    if not st.registry.by_id(claims.tenant_id):
        raise HTTPException(401, "tenant no longer exists")
    return claims


async def ws_token_or_close(ws: WebSocket) -> str | None:
    token = ws.query_params.get("token")
    if not token:
        await ws.close(code=4401)
    return token
