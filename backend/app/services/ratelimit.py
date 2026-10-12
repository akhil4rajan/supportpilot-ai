from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from typing import Any

log = logging.getLogger(__name__)


class RateLimiter:
    """Sliding window, in-memory (per replica)."""

    def __init__(self, limit: int, window_seconds: int = 60) -> None:
        self.limit, self.window = limit, window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    async def allow(self, key: str) -> bool:
        now = time.monotonic()
        q = self._hits[key]
        while q and now - q[0] > self.window:
            q.popleft()
        if len(q) >= self.limit:
            return False
        q.append(now)
        return True


class RedisRateLimiter:
    """Fixed-window counter shared by all replicas. Fails OPEN: a Redis outage must not stop customers chatting."""

    def __init__(self, redis: Any, limit: int, window_seconds: int = 60) -> None:
        self.redis, self.limit, self.window = redis, limit, window_seconds

    async def allow(self, key: str) -> bool:
        bucket = int(time.time() // self.window)
        k = f"sp:rl:{key}:{bucket}"
        try:
            pipe = self.redis.pipeline()
            pipe.incr(k)
            pipe.expire(k, self.window * 2)
            count, _ = await pipe.execute()
            return int(count) <= self.limit
        except Exception as e:  # noqa: BLE001
            log.warning("redis rate limiter unavailable (%s); failing open", e)
            return True
