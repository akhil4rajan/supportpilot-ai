"""Secondary model for provider overload/outage. Used by the app only when the primary raises a retryable LLMError."""
from __future__ import annotations

import logging
from typing import Any

from app import metrics

from .base import LLMClient, LLMError, LLMResponse

log = logging.getLogger(__name__)
FALLBACK_ON = {"overload", "timeout", "error"}


def strip_thinking(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Thinking blocks are bound to the model that produced them. When a tool-use turn is continued on a DIFFERENT model,
    send only the portable blocks (text, tool_use, tool_result)."""
    def kind(b: Any) -> str | None:
        return b.get("type") if isinstance(b, dict) else getattr(b, "type", None)

    out = []
    for m in messages:
        c = m["content"]
        if isinstance(c, list):
            c = [b for b in c if kind(b) not in ("thinking", "redacted_thinking")]
        out.append({**m, "content": c})
    return out


class FallbackLLM:
    def __init__(self, primary: LLMClient, secondary: LLMClient, primary_name: str, secondary_name: str) -> None:
        self.primary, self.secondary = primary, secondary
        self.names = (primary_name, secondary_name)

    async def complete(self, *, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]], static_system_len: int = 0) -> LLMResponse:
        try:
            return await self.primary.complete(system=system, messages=messages, tools=tools, static_system_len=static_system_len)
        except LLMError as e:
            if e.kind not in FALLBACK_ON:
                raise
            metrics.LLM_FALLBACKS.labels(self.names[0], self.names[1], e.kind).inc()
            log.warning("primary model %s failed (%s); falling back to %s", self.names[0], e.kind, self.names[1])
            return await self.secondary.complete(system=system, messages=strip_thinking(messages), tools=tools, static_system_len=static_system_len)
