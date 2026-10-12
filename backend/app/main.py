from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.agent.orchestrator import SupportAgent
from app.api import admin, agent_console, dev, widget
from app.config import Settings, get_settings
from app.db import Database
from app.llm.anthropic_client import AnthropicLLM
from app.llm.base import LLMClient
from app.llm.demo import DemoLLM
from app.services.ratelimit import RateLimiter
from app.services.realtime import Hub
from app.state import AppState
from app.tenancy.registry import TenantRegistry

log = logging.getLogger("supportpilot")


def build_llm(s: Settings) -> LLMClient:
    if s.llm_provider == "demo":
        log.warning("LLM_PROVIDER=demo: using the keyword-based stand-in model (no API key needed)")
        return DemoLLM()
    return AnthropicLLM(s.anthropic_model, s.llm_max_tokens, s.anthropic_effort)


def create_app(settings: Settings | None = None, llm: LLMClient | None = None) -> FastAPI:
    load_dotenv()  # so tenant YAML `*_env` references resolve from .env too
    load_dotenv("../.env")
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db = Database(settings.database_url)
        await db.create_all()
        hub = Hub()
        agent = SupportAgent(llm or build_llm(settings), settings, hub)
        registry = TenantRegistry(settings.tenants_dir)
        app.state.sp = AppState(settings, db, registry, hub, agent, RateLimiter(settings.messages_per_minute))
        log.info("SupportPilot ready: %d tenant(s)", len(registry.all()))
        yield
        await db.dispose()

    app = FastAPI(title="SupportPilot", version="0.1.0", lifespan=lifespan)
    # Auth is bearer-token based (no cookies), so reflecting any origin is safe; per-tenant origin
    # allow-lists are enforced when a widget session is created.
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"], allow_credentials=False)

    app.include_router(widget.router)
    app.include_router(agent_console.router)
    app.include_router(admin.router)
    if settings.is_dev:
        app.include_router(dev.router)

    @app.get("/healthz")
    async def healthz():
        return {"ok": True}

    static = os.environ.get("STATIC_DIR")
    if static and Path(static).is_dir():  # built frontend (demo site, agent console, widget.js)
        app.mount("/", StaticFiles(directory=static, html=True), name="static")
    return app


def app_factory() -> FastAPI:
    logging.basicConfig(level=logging.INFO)
    return create_app()
