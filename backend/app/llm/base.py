from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]


@dataclass
class LLMResponse:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    # Provider-native assistant content, echoed back verbatim on the next loop step (this keeps
    # thinking blocks intact, which the API requires when continuing a tool-use turn).
    content: list[Any] = field(default_factory=list)
    stop_reason: str = "end_turn"  # end_turn | tool_use | max_tokens | refusal | ...


class LLMError(Exception):
    pass


class LLMClient(Protocol):
    async def complete(self, *, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> LLMResponse: ...
