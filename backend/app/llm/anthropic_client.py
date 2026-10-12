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

    async def complete(self, *, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> LLMResponse:
        try:
            resp = await self._client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system,
                messages=messages,
                tools=tools,
                output_config={"effort": self.effort},
            )
        except anthropic.APIStatusError as e:
            log.error("anthropic API error %s: %s", e.status_code, e.message)
            raise LLMError(f"model API error {e.status_code}") from e
        except anthropic.APIConnectionError as e:
            raise LLMError("cannot reach model API") from e

        text = "".join(b.text for b in resp.content if b.type == "text").strip()
        calls = [ToolCall(b.id, b.name, dict(b.input)) for b in resp.content if b.type == "tool_use"]
        return LLMResponse(text=text, tool_calls=calls, content=list(resp.content), stop_reason=resp.stop_reason or "end_turn")
