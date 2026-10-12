from __future__ import annotations

import os
import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import jwt
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.llm.base import LLMResponse, ToolCall
from app.llm.demo import DemoLLM
from app.main import create_app

ROOT = Path(__file__).resolve().parent.parent
SECRET = "test-shared-secret-with-enough-length-123"
os.environ.update(
    ACME_USER_TOKEN_SECRET=SECRET, ACME_AGENT_KEY_SAM="sam-key", ACME_AGENT_KEY_JO="jo-key",
    GLOBEX_USER_TOKEN_SECRET="globex-secret-with-enough-length-1234", GLOBEX_AGENT_KEY="globex-key",
)

GLOBEX = """
id: globex
name: Globex
public_key: pk_globex
allowed_origins: ["https://globex.example"]
user_token_secret_env: GLOBEX_USER_TOKEN_SECRET
tickets: {provider: memory}
agents: [{id: gg, name: Gina, api_key_env: GLOBEX_AGENT_KEY}]
"""


class ScriptedLLM:
    """Replays prepared responses; records what the app sent so tests can assert on it."""

    def __init__(self, *steps: LLMResponse) -> None:
        self.steps, self.calls = list(steps), []

    async def complete(self, *, system, messages, tools):
        self.calls.append({"system": system, "messages": [dict(m) for m in messages], "tools": tools})
        return self.steps.pop(0)


def call(name: str, **inp: Any) -> LLMResponse:
    tc = ToolCall(f"toolu_{uuid.uuid4().hex[:8]}", name, inp)
    return LLMResponse("", [tc], [{"type": "tool_use", "id": tc.id, "name": name, "input": inp}], "tool_use")


def say(text: str) -> LLMResponse:
    return LLMResponse(text, [], [{"type": "text", "text": text}], "end_turn")


def user_token(sub: str = "alice", *, secret: str = SECRET, aud: str = "supportpilot", ttl: int = 600, **extra) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode({"sub": sub, "aud": aud, "iat": now, "exp": now + timedelta(seconds=ttl), **extra}, secret, algorithm="HS256")


@pytest.fixture
def make_client(tmp_path):
    clients: list[TestClient] = []

    def _make(llm=None, env: str = "dev") -> TestClient:
        tdir = tmp_path / f"tenants{len(clients)}"
        shutil.copytree(ROOT / "tenants" / "acme", tdir / "acme")
        (tdir / "globex").mkdir()
        (tdir / "globex" / "tenant.yaml").write_text(GLOBEX)
        settings = Settings(
            env=env, database_url=f"sqlite+aiosqlite:///{tmp_path}/t{len(clients)}.db", tenants_dir=tdir,
            server_secret="server-secret-for-tests-0123456789abcdef", admin_api_key="admin-key",
        )
        c = TestClient(create_app(settings, llm or DemoLLM()))
        c.__enter__()
        clients.append(c)
        return c

    yield _make
    for c in clients:
        c.__exit__(None, None, None)


def open_session(c: TestClient, *, user: str | None = "alice", key: str = "pk_acme_demo", token: str | None = None, **hdr):
    body: dict[str, Any] = {"public_key": key}
    if user:
        body["user_token"] = user_token(user)
    if token:
        body["user_token"] = token
    r = c.post("/v1/widget/sessions", json=body, headers=hdr)
    return r


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def chat(c: TestClient, token: str, text: str) -> dict:
    r = c.post("/v1/widget/messages", json={"content": text}, headers=auth(token))
    assert r.status_code == 200, r.text
    return r.json()


def agent_login(c: TestClient, tenant="acme", key="sam-key") -> dict[str, str]:
    r = c.post("/v1/agent/login", json={"tenant_id": tenant, "api_key": key})
    assert r.status_code == 200, r.text
    return auth(r.json()["token"])
