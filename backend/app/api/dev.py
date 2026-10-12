"""DEV ONLY. Plays the role of the customer's own backend so the demo page can sign users in.

In production the customer's server mints this token (see examples/host_token_server.py). This router is
only mounted when ENV=dev.
"""
from __future__ import annotations

from datetime import timedelta

import jwt
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth import USER_AUD
from app.db import utcnow
from app.state import AppState
from app.tenancy.models import env

from .deps import get_state

router = APIRouter(prefix="/v1/dev", tags=["dev-only"])


class DevTokenIn(BaseModel):
    tenant_id: str
    user_id: str
    name: str | None = None
    email: str | None = None


@router.post("/user-token")
async def mint(body: DevTokenIn, st: AppState = Depends(get_state)):
    rt = st.registry.by_id(body.tenant_id)
    secret = env(rt.tenant.user_token_secret_env) if rt else None
    if not rt or not secret:
        raise HTTPException(404, "tenant not found or has no user token secret")
    now = utcnow()
    claims = {"sub": body.user_id, "name": body.name, "email": body.email, "aud": USER_AUD, "iat": now, "exp": now + timedelta(minutes=10)}
    return {"user_token": jwt.encode({k: v for k, v in claims.items() if v is not None}, secret, algorithm="HS256")}
