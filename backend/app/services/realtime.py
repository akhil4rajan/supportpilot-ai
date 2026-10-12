"""Pub/sub for WebSocket fan-out.

`Hub`      in-process; correct for a single replica.
`RedisHub` publishes to Redis and every replica forwards what it receives to ITS local sockets, so a customer
           connected to pod A sees a reply an agent sent via pod B. One Redis subscription per replica (not per
           socket): cost is O(replicas), not O(connections).
Both expose the same API; callers never know which is active.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections import defaultdict
from contextlib import contextmanager
from typing import Any, Iterator

log = logging.getLogger(__name__)
CONV_PREFIX, AGENT_PREFIX = "sp:c:", "sp:a:"


class Hub:
    def __init__(self) -> None:
        self._conv: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self._agents: dict[str, set[asyncio.Queue]] = defaultdict(set)

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    @property
    def connection_count(self) -> int:
        return sum(len(v) for v in self._conv.values()) + sum(len(v) for v in self._agents.values())

    @contextmanager
    def subscribe_conversation(self, conversation_id: str) -> Iterator[asyncio.Queue]:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._conv[conversation_id].add(q)
        try:
            yield q
        finally:
            self._conv[conversation_id].discard(q)
            if not self._conv[conversation_id]:
                self._conv.pop(conversation_id, None)

    @contextmanager
    def subscribe_agents(self, tenant_id: str) -> Iterator[asyncio.Queue]:
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._agents[tenant_id].add(q)
        try:
            yield q
        finally:
            self._agents[tenant_id].discard(q)
            if not self._agents[tenant_id]:
                self._agents.pop(tenant_id, None)

    @staticmethod
    def _put(queues: set[asyncio.Queue], event: dict[str, Any]) -> None:
        for q in list(queues):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:  # slow consumer: drop; the client refetches on reconnect
                pass

    def _deliver_local(self, channel: str, event: dict[str, Any]) -> None:
        if channel.startswith(CONV_PREFIX):
            self._put(self._conv.get(channel[len(CONV_PREFIX):], set()), event)
        elif channel.startswith(AGENT_PREFIX):
            self._put(self._agents.get(channel[len(AGENT_PREFIX):], set()), event)

    async def publish(self, channel: str, event: dict[str, Any]) -> None:
        self._deliver_local(channel, event)

    async def to_customer(self, conversation_id: str, event: dict[str, Any]) -> None:
        await self.publish(CONV_PREFIX + conversation_id, event)

    async def to_agents(self, tenant_id: str, event: dict[str, Any]) -> None:
        await self.publish(AGENT_PREFIX + tenant_id, event)


class RedisHub(Hub):
    def __init__(self, redis: Any) -> None:  # redis.asyncio.Redis
        super().__init__()
        self.redis = redis
        self._task: asyncio.Task | None = None
        self.connected = False

    async def start(self) -> None:
        self._task = asyncio.create_task(self._listen(), name="redis-hub-listener")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(BaseException):
                await self._task

    async def publish(self, channel: str, event: dict[str, Any]) -> None:
        try:
            await self.redis.publish(channel, json.dumps(event))
        except Exception as e:  # noqa: BLE001
            # Degrade, don't fail the user's request: sockets on THIS replica still get the event.
            log.warning("redis publish failed (%s); delivering locally only", e)
            self._deliver_local(channel, event)

    async def _listen(self) -> None:
        backoff = 0.5
        while True:
            pubsub = self.redis.pubsub()
            try:
                await pubsub.psubscribe("sp:*")
                self.connected, backoff = True, 0.5
                async for msg in pubsub.listen():
                    if msg.get("type") != "pmessage":
                        continue
                    try:
                        data = msg["data"]
                        self._deliver_local(msg["channel"] if isinstance(msg["channel"], str) else msg["channel"].decode(), json.loads(data))
                    except (ValueError, KeyError):
                        log.warning("dropping malformed hub message")
            except asyncio.CancelledError:
                with contextlib.suppress(Exception):
                    await pubsub.aclose()
                raise
            except Exception as e:  # noqa: BLE001
                self.connected = False
                log.warning("redis hub listener lost connection (%s); retry in %.1fs", e, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 15)
