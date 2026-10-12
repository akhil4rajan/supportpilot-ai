"""Keyword-driven stand-in for the model: lets you run and click through the whole product with no API key.

It is deliberately dumb. It exists to exercise the real tool pipeline (auth, KB, tickets, handoff);
it is NOT a quality reference. Use LLM_PROVIDER=anthropic for the real thing.
"""
from __future__ import annotations

import json
import re
import uuid
from typing import Any

from .base import LLMResponse, ToolCall

_ORDER = re.compile(r"\b([A-Za-z]{2,5}-\d{3,})\b")


def _tool(name: str, **inp: Any) -> LLMResponse:
    tc = ToolCall(f"toolu_{uuid.uuid4().hex[:12]}", name, inp)
    return LLMResponse(
        text="",
        tool_calls=[tc],
        content=[{"type": "tool_use", "id": tc.id, "name": tc.name, "input": inp}],
        stop_reason="tool_use",
    )


def _say(text: str) -> LLMResponse:
    return LLMResponse(text=text, content=[{"type": "text", "text": text}])


class DemoLLM:
    async def complete(self, *, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]], static_system_len: int = 0) -> LLMResponse:
        last = messages[-1]
        names = {t["name"] for t in tools}
        if isinstance(last["content"], list) and last["content"] and last["content"][0].get("type") == "tool_result":
            return self._summarize(last["content"], messages)

        text = str(last["content"])
        low = text.lower()
        if re.search(r"\b(human|real person|representative|live agent)\b", low) and "escalate_to_human" in names:
            return _tool("escalate_to_human", reason="Customer asked for a human", summary=text[:300])
        if ("ticket" in low or "report a problem" in low) and "create_support_ticket" in names:
            body = text.split(":", 1)[1].strip() if ":" in text else text
            return _tool("create_support_ticket", summary=body[:120] or "Support request", description=body or text)
        m = _ORDER.search(text)
        if ("order" in low or m) and "get_order_status" in names:
            if m:
                return _tool("get_order_status", order_id=m.group(1))
            return _say("Sure - what's your order number? (for example ACM-1001)")
        if "search_knowledge_base" in names:
            return _tool("search_knowledge_base", query=text)
        return _say("I can help with questions, order status and support tickets.")

    @staticmethod
    def _summarize(results: list[dict[str, Any]], messages: list[dict[str, Any]]) -> LLMResponse:
        raw = results[0].get("content", "")
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError:
            return _say(str(raw))
        if "passages" in data:
            if not data["passages"]:
                return _say("I couldn't find that in our help articles. Would you like me to open a ticket or connect you with a person?")
            p = data["passages"][0]
            return _say(f"{p['text'][:500]}\n\n(Source: {p['source']})")
        if "order" in data:
            o = data["order"]
            eta = f" Estimated delivery: {o['estimated_delivery']}." if o.get("estimated_delivery") else ""
            return _say(f"Order {o['order_id']} is **{o['status']}**.{eta}")
        if "ticket" in data:
            return _say(f"I've opened ticket {data['ticket']['key']} for you. Our team will follow up.")
        if data.get("handoff"):
            return _say("I'm connecting you with a human agent now.")
        return _say(data.get("message", "Done."))
