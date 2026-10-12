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
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    queue_ms: float = 0.0  # time spent waiting for a concurrency slot (set by GuardedLLM); NOT model time


class LLMError(Exception):
    """Model call failed. `kind` feeds metrics/alerts: error | timeout | overload."""

    def __init__(self, message: str, kind: str = "error") -> None:
        super().__init__(message)
        self.kind = kind


class LLMClient(Protocol):
    async def complete(
        self, *, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]], static_system_len: int = 0
    ) -> LLMResponse:
        """`system[:static_system_len]` is identical for every conversation of a tenant (cacheable prefix)."""
        ...
