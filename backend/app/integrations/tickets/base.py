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


@dataclass
class TicketResult:
    key: str
    url: str | None
    provider: str


class TicketError(Exception):
    pass


class TicketProvider(Protocol):
    async def create_ticket(self, req: TicketRequest) -> TicketResult: ...
