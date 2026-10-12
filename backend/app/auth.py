"""Token handling.

Three kinds of JWT, three audiences, so one can never be replayed as another:
  * user token   (aud=supportpilot)  minted by the CUSTOMER'S backend with the tenant's shared secret.
                                     This is the ONLY source of "who is this user" - it is what makes
                                     order lookups safe. The model/browser can never assert identity.
  * widget token (aud=widget)        minted by us after validating the user token; binds tenant+conversation.
  * agent token  (aud=agent)         minted by us after a human agent logs in to the console.
"""
from __future__ import annotations

import hmac
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import jwt

from .db import utcnow
from .tenancy.models import Tenant, env

USER_AUD = "supportpilot"
ALGS = ["HS256"]


class AuthError(Exception):
    pass


@dataclass(frozen=True)
class VerifiedUser:
    id: str
    name: str | None = None
    email: str | None = None


def verify_user_token(tenant: Tenant, token: str) -> VerifiedUser:
    secret = env(tenant.user_token_secret_env)
    if not secret:
        raise AuthError("this tenant does not accept user tokens")
    try:
        claims = jwt.decode(
            token, secret, algorithms=ALGS, audience=USER_AUD, options={"require": ["exp", "sub"]}
        )
    except jwt.PyJWTError as e:
        raise AuthError(f"invalid user token: {e}") from e
    # Reject tokens that live too long: they are bearer credentials handed to a browser.
    if "iat" in claims and claims["exp"] - claims["iat"] > 3600:
        raise AuthError("user token lifetime must be <= 1 hour")
    sub = str(claims["sub"]).strip()
    if not sub:
        raise AuthError("empty subject")
    return VerifiedUser(sub, claims.get("name"), claims.get("email"))


@dataclass(frozen=True)
class WidgetClaims:
    tenant_id: str
    conversation_id: str


def issue_widget_token(secret: str, tenant_id: str, conversation_id: str, ttl_minutes: int) -> str:
    return jwt.encode(
        {"aud": "widget", "tid": tenant_id, "cid": conversation_id, "exp": utcnow() + timedelta(minutes=ttl_minutes)},
        secret,
        algorithm="HS256",
    )


def verify_widget_token(secret: str, token: str) -> WidgetClaims:
    try:
        c = jwt.decode(token, secret, algorithms=ALGS, audience="widget", options={"require": ["exp"]})
        return WidgetClaims(c["tid"], c["cid"])
    except (jwt.PyJWTError, KeyError) as e:
        raise AuthError("invalid session") from e


@dataclass(frozen=True)
class AgentClaims:
    tenant_id: str
    agent_id: str
    name: str


def issue_agent_token(secret: str, tenant_id: str, agent_id: str, name: str, ttl_minutes: int) -> str:
    return jwt.encode(
        {"aud": "agent", "tid": tenant_id, "aid": agent_id, "name": name, "exp": utcnow() + timedelta(minutes=ttl_minutes)},
        secret,
        algorithm="HS256",
    )


def verify_agent_token(secret: str, token: str) -> AgentClaims:
    try:
        c: dict[str, Any] = jwt.decode(token, secret, algorithms=ALGS, audience="agent", options={"require": ["exp"]})
        return AgentClaims(c["tid"], c["aid"], c["name"])
    except (jwt.PyJWTError, KeyError) as e:
        raise AuthError("invalid agent session") from e


def check_agent_key(tenant: Tenant, agent_id: str | None, api_key: str) -> tuple[str, str] | None:
    """Constant-time match of a presented key against the tenant's configured agent keys."""
    found: tuple[str, str] | None = None
    for a in tenant.agents:
        expected = env(a.api_key_env)
        if expected and hmac.compare_digest(expected.encode(), api_key.encode()):
            if agent_id in (None, "", a.id):
                found = (a.id, a.name)
    return found
