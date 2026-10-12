"""Deterministic stand-ins for the model, so security/resilience suites test OUR code paths, not the model."""
from __future__ import annotations

import uuid
from typing import Any

from app.llm.base import LLMError, LLMResponse, ToolCall


class ScriptedLLM:
    def __init__(self, *steps: LLMResponse | Exception) -> None:
        self.steps = list(steps)

    async def complete(self, *, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]], static_system_len: int = 0) -> LLMResponse:
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


def call(name: str, **inp: Any) -> LLMResponse:
    tc = ToolCall(f"toolu_{uuid.uuid4().hex[:8]}", name, inp)
    return LLMResponse("", [tc], [{"type": "tool_use", "id": tc.id, "name": name, "input": inp}], "tool_use")


def say(text: str) -> LLMResponse:
    return LLMResponse(text, [], [{"type": "text", "text": text}], "end_turn")


def down(kind: str = "error") -> LLMError:
    return LLMError("simulated outage", kind)
