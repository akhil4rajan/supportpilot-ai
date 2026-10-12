"""In-process pub/sub for WebSocket fan-out.

Single-process by design (fine for one container). For multiple workers, replace `Hub` with a
Redis pub/sub implementation exposing the same two methods.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from contextlib import contextmanager
from typing import Any, Iterator


class Hub:
    def __init__(self) -> None:
        self._conv: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self._agents: dict[str, set[asyncio.Queue]] = defaultdict(set)

    @contextmanager
    def subscribe_conversation(self, conversation_id: str) -> Iterator[asyncio.Queue]:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._conv[conversation_id].add(q)
        try:
            yield q
        finally:
            self._conv[conversation_id].discard(q)

    @contextmanager
    def subscribe_agents(self, tenant_id: str) -> Iterator[asyncio.Queue]:
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._agents[tenant_id].add(q)
        try:
            yield q
        finally:
            self._agents[tenant_id].discard(q)

    @staticmethod
    def _put(queues: set[asyncio.Queue], event: dict[str, Any]) -> None:
        for q in list(queues):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:  # slow consumer: drop; it refetches on reconnect
                pass

    def to_customer(self, conversation_id: str, event: dict[str, Any]) -> None:
        self._put(self._conv.get(conversation_id, set()), event)

    def to_agents(self, tenant_id: str, event: dict[str, Any]) -> None:
        self._put(self._agents.get(tenant_id, set()), event)
