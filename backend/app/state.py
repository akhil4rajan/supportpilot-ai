from __future__ import annotations

from dataclasses import dataclass

from app.agent.orchestrator import SupportAgent
from app.config import Settings
from app.db import Database
from app.services.ratelimit import RateLimiter
from app.services.realtime import Hub
from app.tenancy.registry import TenantRegistry


@dataclass
class AppState:
    settings: Settings
    db: Database
    registry: TenantRegistry
    hub: Hub
    agent: SupportAgent
    limiter: RateLimiter
