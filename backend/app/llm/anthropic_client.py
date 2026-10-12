from __future__ import annotations

import logging
from typing import Any

import anthropic

from .base import LLMError, LLMResponse, ToolCall

log = logging.getLogger(__name__)


class AnthropicLLM:
    """Claude via the official SDK (reads ANTHROPIC_API_KEY from the environment)."""

    def __init__(self, model: str, max_tokens: int, effort: str = "medium", client: anthropic.AsyncAnthropic | None = None) -> None:
        self.model, self.max_tokens, self.effort = model, max_tokens, effort
        self._client = client or anthropic.AsyncAnthropic(timeout=60.0, max_retries=2)

    async def complete(
        self, *, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]], static_system_len: int = 0
    ) -> LLMResponse:
        # Prompt caching: render order is tools -> system -> messages, so the breakpoint goes at the end of the
        # per-tenant static prompt; per-conversation facts (who is signed in, today's date) come after it.
        system_blocks: str | list[dict[str, Any]] = system
        if 0 < static_system_len < len(system):
            system_blocks = [
                {"type": "text", "text": system[:static_system_len], "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": system[static_system_len:]},
            ]
        try:
            resp = await self._client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system_blocks,
                messages=messages,
                tools=tools,
                output_config={"effort": self.effort},
            )
        except anthropic.RateLimitError as e:
            raise LLMError("model API rate limited", "overload") from e
        except anthropic.APIStatusError as e:
            log.error("anthropic API error %s: %s", e.status_code, e.message)
            raise LLMError(f"model API error {e.status_code}", "overload" if e.status_code in (429, 529) else "error") from e
        except anthropic.APITimeoutError as e:
            raise LLMError("model API timeout", "timeout") from e
        except anthropic.APIConnectionError as e:
            raise LLMError("cannot reach model API", "error") from e

        text = "".join(b.text for b in resp.content if b.type == "text").strip()
        calls = [ToolCall(b.id, b.name, dict(b.input)) for b in resp.content if b.type == "tool_use"]
        u = getattr(resp, "usage", None)
        return LLMResponse(
            text=text, tool_calls=calls, content=list(resp.content), stop_reason=resp.stop_reason or "end_turn",
            input_tokens=getattr(u, "input_tokens", 0) or 0, output_tokens=getattr(u, "output_tokens", 0) or 0,
            cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
        )
