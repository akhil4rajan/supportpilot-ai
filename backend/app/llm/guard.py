"""Protects the platform (and the model API quota) from overload: bounded concurrency, per-call timeout,
metrics. Wraps any LLMClient, so it is also what the load tests exercise."""
from __future__ import annotations

import asyncio
import time
from typing import Any

from app import metrics

from .base import LLMClient, LLMError, LLMResponse


class GuardedLLM:
    def __init__(self, inner: LLMClient, model: str, max_concurrency: int = 64, queue_timeout: float = 20.0, call_timeout: float = 60.0) -> None:
        self.inner, self.model = inner, model
        self._sem = asyncio.Semaphore(max_concurrency)
        self.queue_timeout, self.call_timeout = queue_timeout, call_timeout

    async def complete(self, *, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]], static_system_len: int = 0) -> LLMResponse:
        # Admission control: if the model is saturated, fail fast (the turn degrades to a human handoff) instead of
        # piling up requests until every HTTP worker is blocked.
        metrics.LLM_QUEUED.inc()
        try:
            await asyncio.wait_for(self._sem.acquire(), self.queue_timeout)
        except asyncio.TimeoutError:
            metrics.LLM_CALLS.labels(self.model, "overload").inc()
            raise LLMError("model concurrency limit reached", "overload") from None
        finally:
            metrics.LLM_QUEUED.dec()
        metrics.LLM_INFLIGHT.inc()
        t0 = time.perf_counter()
        try:
            resp = await asyncio.wait_for(
                self.inner.complete(system=system, messages=messages, tools=tools, static_system_len=static_system_len), self.call_timeout
            )
        except asyncio.TimeoutError:
            metrics.LLM_CALLS.labels(self.model, "timeout").inc()
            raise LLMError("model call timed out", "timeout") from None
        except LLMError as e:
            metrics.LLM_CALLS.labels(self.model, e.kind).inc()
            raise
        finally:
            metrics.LLM_INFLIGHT.dec()
            self._sem.release()
            metrics.LLM_LATENCY.labels(self.model).observe(time.perf_counter() - t0)
        metrics.LLM_CALLS.labels(self.model, "refusal" if resp.stop_reason == "refusal" else "ok").inc()
        for kind, n in (("input", resp.input_tokens), ("output", resp.output_tokens), ("cache_read", resp.cache_read_tokens), ("cache_write", resp.cache_write_tokens)):
            if n:
                metrics.LLM_TOKENS.labels(self.model, kind).inc(n)
        return resp
