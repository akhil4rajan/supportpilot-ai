"""Shared plumbing for eval suites: an isolated in-process app (temp DB, temp copy of the tenants), a recording
wrapper around whichever LLM is under test, and helpers that drive conversations the way the widget does."""
from __future__ import annotations

import contextlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

import jwt
from fastapi.testclient import TestClient

from app.config import Settings
from app.llm.base import LLMClient, LLMResponse
from app.main import create_app

BACKEND = Path(__file__).resolve().parent.parent
# Eval-only secrets (never used outside this process). setdefault so a real staging run can override them.
EVAL_ENV = {
    "ACME_USER_TOKEN_SECRET": "eval-acme-user-token-secret-0123456789",
    "ACME_AGENT_KEY_SAM": "eval-sam-key",
    "ACME_AGENT_KEY_JO": "eval-jo-key",
    # Every tenant shipped in backend/tenants needs non-demo secrets, or ENV=prod apps refuse to start
    # on a machine whose .env was copied from .env.example.
    "WATTLEBAY_USER_TOKEN_SECRET": "eval-wattlebay-user-token-secret-0123456",
    "WATTLEBAY_AGENT_KEY_SAM": "eval-wb-sam-key",
    "WATTLEBAY_AGENT_KEY_JO": "eval-wb-jo-key",
}


def aud_token(secret: str, sub: str, *, aud: str = "supportpilot", ttl: int = 600, **extra: Any) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode({"sub": sub, "aud": aud, "iat": now, "exp": now + timedelta(seconds=ttl), **extra}, secret, algorithm="HS256")


class RecordingLLM:
    """Wraps the model under test and keeps every request/response so graders can see exactly what the model saw."""

    def __init__(self, inner: LLMClient) -> None:
        self.inner = inner
        self.calls: list[dict[str, Any]] = []

    async def complete(self, *, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]], static_system_len: int = 0) -> LLMResponse:
        resp = await self.inner.complete(system=system, messages=messages, tools=tools, static_system_len=static_system_len)
        self.calls.append({"system": system, "messages": json.loads(json.dumps(messages, default=str)), "tools": [t["name"] for t in tools],
                           "tool_calls": [(c.name, c.input) for c in resp.tool_calls], "text": resp.text, "usage": (resp.input_tokens, resp.output_tokens)})
        return resp


@dataclass
class Turn:
    user: str
    reply: str
    messages: list[dict[str, Any]]
    status: str
    tools: list[str]
    tickets: list[dict[str, Any]]
    sources: list[dict[str, Any]]
    tool_results: list[dict[str, Any]] = field(default_factory=list)  # what the tools returned to the model this turn
    llm_calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0


@dataclass
class Session:
    token: str
    conversation_id: str
    user: str | None


class EvalApp:
    def __init__(self, llm: LLMClient, tenants_dir: Path | None = None, env: str = "dev", extra_tenants: dict[str, str] | None = None, **settings: Any) -> None:
        for k, v in EVAL_ENV.items():
            os.environ.setdefault(k, v)
        self._tmp = Path(tempfile.mkdtemp(prefix="sp-eval-"))
        src = Path(tenants_dir or BACKEND / "tenants")
        shutil.copytree(src, self._tmp / "tenants")
        for tid, yaml_text in (extra_tenants or {}).items():  # e.g. a second customer, to test isolation
            (self._tmp / "tenants" / tid).mkdir(exist_ok=True)
            (self._tmp / "tenants" / tid / "tenant.yaml").write_text(yaml_text)
        self.llm = RecordingLLM(llm)
        kw: dict[str, Any] = dict(env=env, database_url=f"sqlite+aiosqlite:///{self._tmp}/eval.db", tenants_dir=self._tmp / "tenants",
                                  server_secret="eval-server-secret-0123456789abcdef0123", admin_api_key="eval-admin", run_outbox_worker=False)
        kw.update(settings)
        self.app = create_app(Settings(**kw), self.llm)
        self.client = TestClient(self.app)

    def __enter__(self) -> "EvalApp":
        self.client.__enter__()
        return self

    def __exit__(self, *a: Any) -> None:
        self.client.__exit__(*a)
        shutil.rmtree(self._tmp, ignore_errors=True)

    # ---- identities
    def user_token(self, user: str, secret_env: str = "ACME_USER_TOKEN_SECRET", **extra: Any) -> str:
        return aud_token(os.environ[secret_env], user, name=user.title(), email=f"{user}@example.com", **extra)

    def open_session(self, user: str | None = None, key: str = "pk_acme_demo", origin: str | None = None, token: str | None = None) -> Session:
        body: dict[str, Any] = {"public_key": key}
        if token or user:
            body["user_token"] = token or self.user_token(user)  # type: ignore[arg-type]
        r = self.client.post("/v1/widget/sessions", json=body, headers={"origin": origin} if origin else {})
        r.raise_for_status()
        j = r.json()
        return Session(j["token"], j["conversation"]["id"], user)

    def agent_headers(self, tenant: str = "acme", key: str | None = None) -> dict[str, str]:
        r = self.client.post("/v1/agent/login", json={"tenant_id": tenant, "api_key": key or os.environ["ACME_AGENT_KEY_SAM"]})
        r.raise_for_status()
        return {"Authorization": f"Bearer {r.json()['token']}"}

    # ---- conversation
    def say(self, s: Session, text: str) -> Turn:
        start = len(self.llm.calls)
        r = self.client.post("/v1/widget/messages", json={"content": text}, headers={"Authorization": f"Bearer {s.token}"})
        r.raise_for_status()
        j = r.json()
        msgs = j["messages"]
        bot = [m for m in msgs if m["role"] in ("assistant", "agent")]
        tools: list[str] = []
        tickets: list[dict[str, Any]] = []
        sources: list[dict[str, Any]] = []
        for m in bot:
            tools += m["meta"].get("tools", [])
            tickets += m["meta"].get("tickets", [])
            sources += m["meta"].get("sources", [])
        results: list[dict[str, Any]] = []
        calls = self.llm.calls[start:]
        for c in calls[1:]:  # tool_results appear in the follow-up request of each loop step
            last = c["messages"][-1]
            if isinstance(last["content"], list):
                for b in last["content"]:
                    if b.get("type") == "tool_result":
                        with contextlib.suppress(Exception):
                            results.append(json.loads(b["content"]))
        return Turn(text, "\n".join(m["content"] for m in bot), msgs, j["conversation"]["status"], tools, tickets, sources, results,
                    len(calls), sum(c["usage"][0] for c in calls), sum(c["usage"][1] for c in calls))


@contextlib.contextmanager
def eval_app(llm: LLMClient, **kw: Any) -> Iterator[EvalApp]:
    with EvalApp(llm, **kw) as a:
        yield a


def make_llm(mode: str, settings_model: str | None = None) -> LLMClient:
    if mode == "demo":
        from app.llm.demo import DemoLLM
        return DemoLLM()
    if mode == "anthropic":
        from app.llm.anthropic_client import AnthropicLLM
        return AnthropicLLM(settings_model or os.environ.get("ANTHROPIC_MODEL", "claude-opus-5-5"), 2048, os.environ.get("ANTHROPIC_EFFORT", "medium"))
    raise ValueError(f"unknown llm mode {mode!r}")
