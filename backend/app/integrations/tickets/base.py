from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class TicketRequest:
    summary: str
    description: str
    priority: str = "normal"  # low | normal | high | urgent
    category: str | None = None
    reporter_name: str | None = None
    reporter_email: str | None = None
    reporter_id: str | None = None
    conversation_id: str | None = None
    transcript: str | None = None
    idempotency_key: str | None = None  # stable across retries; lets a provider detect "already created"
    attempt: int = 0


@dataclass
class TicketResult:
    key: str
    url: str | None
    provider: str


class TicketError(Exception):
    """`retryable` = transient (timeout, 429, 5xx): worth queueing. Otherwise (bad config/auth/validation) it is not."""

    def __init__(self, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class TicketProvider(Protocol):
    async def create_ticket(self, req: TicketRequest) -> TicketResult: ...
