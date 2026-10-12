from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app import metrics
from app.auth import VerifiedUser
from app.config import Settings
from app.db import Conversation
from app.llm.base import LLMClient, LLMError, LLMResponse
from app.services import conversations as convs
from app.services.handoff import start_handoff
from app.services.realtime import Hub
from app.tenancy.registry import TenantRuntime

from .prompts import build_system_prompt
from .tools import IMPLEMENTATIONS, ToolContext, tool_schemas

log = logging.getLogger(__name__)

# Deterministic safety net: an explicit request for a person must never depend on model behaviour.
_PERSON = r"(human|person|people|agent|representative|rep|someone|somebody|manager|supervisor|support team)"
_WANTS_HUMAN = re.compile(
    rf"\b(talk|speak|chat|connect|transfer|escalate|put me|get|give|need|want|let me|can i|could i|i'?d like|i would like|prefer)\b.{{0,30}}\b{_PERSON}\b"
    r"|\b(real|live|actual) (person|human|agent)\b|\bhuman agent\b|\bhuman (please|support)\b",
    re.IGNORECASE,
)
APOLOGY = "Sorry - I'm having trouble right now. I'm bringing in a human teammate to help you."
EMPTY_FALLBACK = "I'm not able to help with that one. Let me bring in a human teammate."
HISTORY_LIMIT = 30
# Handoff lifecycle events are shown to the model as bracketed notes on the assistant side, so turns keep
# alternating and the model can see that an earlier "I want a human" request has since been dealt with.
EVENT_NOTES = {
    "handoff_requested": "[Conversation was transferred to a human agent]",
    "agent_joined": "[A human agent joined the chat]",
    "returned_to_bot": "[The human agent handed the conversation back to the virtual assistant; any earlier request for a human has been handled]",
    "resolved": "[Conversation was marked resolved]",
}


class SupportAgent:
    def __init__(self, llm: LLMClient, settings: Settings, hub: Hub) -> None:
        self.llm, self.settings, self.hub = llm, settings, hub

    # ------------------------------------------------------------------ public entry point
    async def handle_user_message(
        self, db: AsyncSession, rt: TenantRuntime, conv: Conversation, user: VerifiedUser | None, text: str
    ) -> list[dict[str, Any]]:
        """Persist the customer's message and return the new messages produced in response."""
        t0 = time.perf_counter()
        out: list[dict[str, Any]] = []
        outcome = "answered"
        try:
            out = await self._handle(db, rt, conv, user, text)
            if conv.status in convs.HANDOFF_STATES:
                outcome = "handoff"
            return out
        except Exception:
            outcome = "error"
            raise
        finally:
            metrics.TURNS.labels(rt.tenant.id, outcome).inc()
            metrics.TURN_LATENCY.labels(rt.tenant.id, outcome).observe(time.perf_counter() - t0)

    async def _handle(
        self, db: AsyncSession, rt: TenantRuntime, conv: Conversation, user: VerifiedUser | None, text: str
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if conv.status == "resolved":  # customer came back: reopen with the bot
            conv.status = "bot"
            conv.unanswered_count = 0
            await db.commit()
            await convs.publish_update(self.hub, conv)

        await convs.add_message(db, self.hub, conv, "user", text)
        if conv.status in convs.HANDOFF_STATES:
            return out  # a human owns the conversation now; the bot stays silent

        if _WANTS_HUMAN.search(text):
            await start_handoff(db, self.hub, rt, conv, "Customer asked for a human", text[:500], trigger="phrase")
            return await self._new_since_user(db, conv)

        ctx = ToolContext(db=db, rt=rt, conv=conv, user=user)
        try:
            reply = await self._run_model(ctx, db, conv, user)
        except LLMError as e:
            log.error("LLM failure kind=%s; escalating conv=%s", e.kind, conv.id)
            await convs.add_message(db, self.hub, conv, "assistant", APOLOGY, {"error": "llm_unavailable", "kind": e.kind})
            await start_handoff(db, self.hub, rt, conv, f"Assistant unavailable ({e.kind})", text[:500], trigger="llm_error")
            return await self._new_since_user(db, conv)

        meta = {"sources": ctx.sources, "tickets": ctx.tickets, "tools": ctx.tools_used,
                "timing": {"llm_ms": round(ctx.llm_ms), "llm_calls": ctx.llm_calls}}
        await convs.add_message(db, self.hub, conv, "assistant", reply, meta)

        # Unresolved-turn accounting -> automatic escalation.
        if ctx.kb_none and not ctx.kb_confident and not ctx.task_succeeded and ctx.escalation is None:
            conv.unanswered_count += 1  # clear miss: nothing relevant in the KB
        elif ctx.kb_searched or ctx.task_succeeded:
            conv.unanswered_count = 0
        await db.commit()

        if ctx.escalation:
            await start_handoff(db, self.hub, rt, conv, *ctx.escalation, trigger=ctx.escalation_trigger)
        elif conv.unanswered_count >= rt.tenant.handoff.max_unresolved_turns:
            await start_handoff(
                db, self.hub, rt, conv,
                f"Automatic: assistant could not answer {conv.unanswered_count} questions in a row", text[:500],
                trigger="auto_unanswered",
            )
        return await self._new_since_user(db, conv)

    async def _new_since_user(self, db: AsyncSession, conv: Conversation) -> list[dict[str, Any]]:
        msgs = await convs.history(db, conv.id, limit=10)
        idx = max((i for i, m in enumerate(msgs) if m.role == "user"), default=-1)
        return [convs.msg_out(m) for m in msgs[idx + 1 :]]

    # ------------------------------------------------------------------ model loop
    async def _history_for_model(self, db: AsyncSession, conv: Conversation) -> list[dict[str, Any]]:
        rows = await convs.history(db, conv.id, limit=HISTORY_LIMIT)
        msgs: list[dict[str, Any]] = []
        for m in rows:
            if m.role == "system":
                note = EVENT_NOTES.get((m.meta or {}).get("event", ""))
                if not note:
                    continue
                role, text = "assistant", note
            else:
                role = "user" if m.role == "user" else "assistant"
                text = f"[Human agent] {m.content}" if m.role == "agent" else m.content
            if msgs and msgs[-1]["role"] == role:
                msgs[-1]["content"] += "\n\n" + text
            else:
                msgs.append({"role": role, "content": text})
        while msgs and msgs[0]["role"] != "user":
            msgs.pop(0)
        return msgs

    async def _run_model(self, ctx: ToolContext, db: AsyncSession, conv: Conversation, user: VerifiedUser | None) -> str:
        static, dynamic = build_system_prompt(ctx.rt, user)
        system, static_len = static + "\n\n" + dynamic, len(static)
        tools = tool_schemas(ctx.rt)
        messages = await self._history_for_model(db, conv)

        resp: LLMResponse | None = None
        for _ in range(self.settings.llm_max_tool_steps):
            # Never hold a DB connection while waiting on the model: a turn spends seconds there, and a held connection
            # caps concurrency at the pool size. commit() ends the open read transaction and returns the connection.
            await db.commit()
            t_llm = time.perf_counter()
            try:
                resp = await self.llm.complete(system=system, messages=messages, tools=tools, static_system_len=static_len)
            finally:
                ctx.llm_ms += (time.perf_counter() - t_llm) * 1000
                ctx.llm_calls += 1
            if resp.stop_reason == "refusal":
                ctx.escalation = ctx.escalation or ("Model declined to respond", "The assistant could not process this request.")
                ctx.escalation_trigger = "refusal"
                return EMPTY_FALLBACK
            if not resp.tool_calls:
                break
            messages.append({"role": "assistant", "content": resp.content})
            results = []
            for call in resp.tool_calls:  # all results go back in ONE user message
                results.append(await self._execute(ctx, call.id, call.name, call.input))
            messages.append({"role": "user", "content": results})
        else:
            log.warning("tool loop exhausted conv=%s", conv.id)
            ctx.escalation = ctx.escalation or ("Assistant could not finish within the tool-step limit", "Loop limit reached.")
            ctx.escalation_trigger = "loop_limit"
            return EMPTY_FALLBACK

        text = (resp.text if resp else "").strip()
        if resp and resp.stop_reason == "max_tokens":
            log.warning("response truncated conv=%s", conv.id)
        return text or EMPTY_FALLBACK

    async def _execute(self, ctx: ToolContext, call_id: str, name: str, args: dict[str, Any]) -> dict[str, Any]:
        allowed = {t["name"] for t in tool_schemas(ctx.rt)}
        fn = IMPLEMENTATIONS.get(name)
        if fn is None or name not in allowed:
            metrics.TOOL_CALLS.labels("unknown", "error").inc()
            return {"type": "tool_result", "tool_use_id": call_id, "is_error": True, "content": f"Unknown tool {name}"}
        ctx.tools_used.append(name)
        try:
            # Only schema-declared params are forwarded; the model cannot inject e.g. a user_id.
            declared = next(t for t in tool_schemas(ctx.rt) if t["name"] == name)["input_schema"]["properties"]
            clean = {k: v for k, v in (args or {}).items() if k in declared}
            result = await fn(ctx, **clean)
            metrics.TOOL_CALLS.labels(name, "error" if "error" in result else "ok").inc()
            return {"type": "tool_result", "tool_use_id": call_id, "content": json.dumps(result, default=str)}
        except Exception:  # noqa: BLE001 - a tool bug must not kill the chat
            log.exception("tool %s failed", name)
            metrics.TOOL_CALLS.labels(name, "error").inc()
            return {"type": "tool_result", "tool_use_id": call_id, "is_error": True, "content": "Tool failed unexpectedly."}
