"""Loads tenant YAML files and builds each tenant's runtime (KB + order/ticket providers)."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import yaml

from app.integrations.orders.base import OrderProvider
from app.integrations.orders.demo import DemoOrderProvider
from app.integrations.orders.http import HttpOrderProvider
from app.integrations.tickets.base import TicketProvider
from app.integrations.tickets.jira_provider import JiraTicketProvider
from app.integrations.tickets.memory import MemoryTicketProvider
from app.rag.index import KnowledgeBase

from .models import Tenant, env

log = logging.getLogger(__name__)


@dataclass
class TenantRuntime:
    tenant: Tenant
    kb: KnowledgeBase
    orders: OrderProvider | None
    tickets: TicketProvider | None

    @property
    def knowledge_dir(self) -> Path:
        return Path(self.tenant.root) / self.tenant.knowledge.path

    def reindex(self) -> int:
        self.kb = KnowledgeBase.from_directory(self.knowledge_dir)
        return self.kb.size


def build_runtime(t: Tenant) -> TenantRuntime:
    root = Path(t.root)
    orders: OrderProvider | None = None
    if t.orders.provider == "demo":
        orders = DemoOrderProvider(root / t.orders.demo_file)
    elif t.orders.provider == "http":
        if not t.orders.http:
            raise ValueError(f"tenant {t.id}: orders.http config required")
        orders = HttpOrderProvider(t.orders.http)

    tickets: TicketProvider | None = None
    if t.tickets.provider == "memory":
        tickets = MemoryTicketProvider()
    elif t.tickets.provider == "jira":
        if not t.tickets.jira:
            raise ValueError(f"tenant {t.id}: tickets.jira config required")
        tickets = JiraTicketProvider(t.tickets.jira)

    return TenantRuntime(t, KnowledgeBase.from_directory(root / t.knowledge.path), orders, tickets)


class TenantRegistry:
    def __init__(self, tenants_dir: Path) -> None:
        self.dir = tenants_dir
        self._by_id: dict[str, TenantRuntime] = {}
        self._by_key: dict[str, TenantRuntime] = {}
        self.reload()

    def reload(self) -> int:
        by_id: dict[str, TenantRuntime] = {}
        by_key: dict[str, TenantRuntime] = {}
        for f in sorted(self.dir.glob("*/tenant.yaml")):
            try:
                data = yaml.safe_load(f.read_text("utf-8")) or {}
                tenant = Tenant(**data)
                tenant.root = f.parent
                if tenant.id in by_id or tenant.public_key in by_key:
                    raise ValueError("duplicate tenant id or public_key")
                rt = build_runtime(tenant)
            except Exception:  # one broken tenant must not take the others down
                log.exception("skipping invalid tenant config %s", f)
                continue
            by_id[tenant.id] = rt
            by_key[tenant.public_key] = rt
            log.info("loaded tenant %s (%d KB chunks)", tenant.id, rt.kb.size)
        # Keep runtime state (e.g. in-memory ticket stores) of tenants that are unchanged? No: reload is
        # an admin operation that rebuilds everything.
        self._by_id, self._by_key = by_id, by_key
        return len(by_id)

    def by_id(self, tenant_id: str) -> TenantRuntime | None:
        return self._by_id.get(tenant_id)

    def by_public_key(self, key: str) -> TenantRuntime | None:
        return self._by_key.get(key)

    def all(self) -> list[TenantRuntime]:
        return list(self._by_id.values())


__all__ = ["TenantRegistry", "TenantRuntime", "env"]
