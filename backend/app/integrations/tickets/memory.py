from __future__ import annotations

import itertools

from .base import TicketRequest, TicketResult


class MemoryTicketProvider:
    """In-process provider for demos/tests."""

    def __init__(self, prefix: str = "SUP") -> None:
        self.prefix = prefix
        self._seq = itertools.count(1)
        self.created: list[tuple[TicketResult, TicketRequest]] = []

    async def create_ticket(self, req: TicketRequest) -> TicketResult:
        res = TicketResult(key=f"{self.prefix}-{next(self._seq)}", url=None, provider="memory")
        self.created.append((res, req))
        return res
