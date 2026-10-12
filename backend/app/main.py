from __future__ import annotations

import json
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app import metrics
from app.agent.orchestrator import SupportAgent
from app.api import admin, agent_console, dev, widget
from app.config import Settings, get_settings
from app.db import Database
from app.llm.anthropic_client import AnthropicLLM
from app.llm.base import LLMClient
from app.llm.demo import DemoLLM
from app.llm.fallback import FallbackLLM
from app.llm.guard import GuardedLLM
from app.llm.simulated import SimulatedLLM
from app.services.outbox import OutboxWorker
from app.services.ratelimit import RateLimiter, RedisRateLimiter
from app.services.realtime import Hub, RedisHub
from app.state import AppState
from app.tenancy.registry import TenantRegistry

log = logging.getLogger("supportpilot")


def build_llm(s: Settings) -> LLMClient:
    if s.llm_provider == "demo":
        log.warning("LLM_PROVIDER=demo: using the keyword-based stand-in model (no API key needed)")
        inner: LLMClient = DemoLLM()
    elif s.llm_provider == "simulated":
        log.warning("LLM_PROVIDER=simulated: latency-realistic fake model for load tests")
        inner = SimulatedLLM(float(os.environ.get("SIM_LLM_MEDIAN_SECONDS", "2.5")), tool_rate=float(os.environ.get("SIM_LLM_TOOL_RATE", "0.6")))
    else:
        inner = AnthropicLLM(s.anthropic_model, s.llm_max_tokens, s.anthropic_effort)

    def guard(client: LLMClient, name: str) -> GuardedLLM:
        return GuardedLLM(client, name, s.llm_max_concurrency, s.llm_queue_timeout_seconds, s.llm_call_timeout_seconds)

    if s.llm_provider == "anthropic":
        primary = guard(inner, s.anthropic_model)
        if s.anthropic_fallback_model and s.anthropic_fallback_model != s.anthropic_model:
            # Each model gets its own concurrency budget: a saturated primary must not starve the fallback.
            secondary = guard(AnthropicLLM(s.anthropic_fallback_model, s.llm_max_tokens, s.anthropic_effort), s.anthropic_fallback_model)
            return FallbackLLM(primary, secondary, s.anthropic_model, s.anthropic_fallback_model)
        return primary
    return guard(inner, s.llm_provider)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        d = {"ts": self.formatTime(record), "level": record.levelname, "logger": record.name, "msg": record.getMessage()}
        if record.exc_info:
            d["exc"] = self.formatException(record.exc_info)
        return json.dumps(d)


def create_app(settings: Settings | None = None, llm: LLMClient | None = None) -> FastAPI:
    load_dotenv()  # so tenant YAML `*_env` references resolve from .env too
    load_dotenv("../.env")
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db = Database(settings.database_url, settings.db_pool_size, settings.db_max_overflow)
        await db.create_all()
        redis = None
        if settings.redis_url:
            import redis.asyncio as aioredis

            redis = aioredis.from_url(settings.redis_url, decode_responses=True)
            hub: Hub = RedisHub(redis)
            limiter = RedisRateLimiter(redis, settings.messages_per_minute)
        else:
            hub, limiter = Hub(), RateLimiter(settings.messages_per_minute)
        await hub.start()
        agent = SupportAgent(llm or build_llm(settings), settings, hub)
        registry = TenantRegistry(settings.tenants_dir)
        app.state.sp = AppState(settings, db, registry, hub, agent, limiter, redis)
        worker = OutboxWorker(db, registry, hub, settings.outbox_poll_seconds, settings.outbox_max_attempts)
        if settings.run_outbox_worker:
            await worker.start()
        log.info("SupportPilot ready: %d tenant(s), redis=%s", len(registry.all()), bool(redis))
        yield
        await worker.stop()
        await hub.stop()
        if redis:
            await redis.aclose()
        await db.dispose()

    app = FastAPI(title="SupportPilot", version="0.1.0", lifespan=lifespan)
    # Auth is bearer-token based (no cookies), so reflecting any origin is safe; per-tenant origin
    # allow-lists are enforced when a widget session is created.
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"], allow_credentials=False)

    @app.middleware("http")
    async def observe(request: Request, call_next):
        t0 = time.perf_counter()
        status = 500
        try:
            resp = await call_next(request)
            status = resp.status_code
            return resp
        finally:
            route = request.scope.get("route")
            path = getattr(route, "path", None) or "unmatched"  # template, not the raw path (low cardinality)
            metrics.HTTP_LATENCY.labels(path, request.method, str(status)).observe(time.perf_counter() - t0)

    app.include_router(widget.router)
    app.include_router(agent_console.router)
    app.include_router(admin.router)
    if settings.is_dev:
        app.include_router(dev.router)

    @app.get("/healthz")
    async def healthz():
        """Liveness: the process is up. Never touches dependencies (a DB blip must not restart every pod)."""
        return {"ok": True}

    @app.get("/readyz")
    async def readyz(request: Request):
        """Readiness: can this replica serve traffic right now? Used by the load balancer."""
        st: AppState = request.app.state.sp
        checks: dict[str, str] = {}
        try:
            async with st.db.session() as s:
                await s.execute(text("select 1"))
            checks["database"] = "ok"
        except Exception as e:  # noqa: BLE001
            checks["database"] = f"fail: {type(e).__name__}"
        if st.redis is not None:
            try:
                await st.redis.ping()
                checks["redis"] = "ok"
            except Exception as e:  # noqa: BLE001
                checks["redis"] = f"degraded: {type(e).__name__}"  # degraded, not fatal: we fail open
        checks["tenants"] = str(len(st.registry.all()))
        if checks["database"] != "ok":
            raise HTTPException(503, detail=checks)
        return {"ready": True, "checks": checks}

    @app.get("/metrics", include_in_schema=False)
    async def prom(request: Request, authorization: str | None = Header(default=None)):
        token = request.app.state.sp.settings.metrics_token
        if token and authorization != f"Bearer {token}":
            raise HTTPException(401, "metrics token required")
        return Response(metrics.render(), media_type="text/plain; version=0.0.4")

    static = os.environ.get("STATIC_DIR")
    if static and Path(static).is_dir():  # built frontend (demo site, agent console, widget.js)
        app.mount("/", StaticFiles(directory=static, html=True), name="static")
    return app


def app_factory() -> FastAPI:
    s = get_settings()
    logging.basicConfig(level=logging.INFO)
    if s.log_format == "json":
        for h in logging.getLogger().handlers:
            h.setFormatter(JsonFormatter())
    return create_app(s)
