"""Ticket outbox: durable retry for tickets the tracker could not accept right away."""
from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import timedelta
from typing import Any

from sqlalchemy import select, update

from app import metrics
from app.db import Database, Ticket, TicketOutbox, utcnow
from app.integrations.tickets.base import TicketError, TicketRequest
from app.services import conversations as convs
from app.services.handoff import start_handoff
from app.services.realtime import Hub
from app.tenancy.registry import TenantRegistry

log = logging.getLogger(__name__)


def backoff(attempts: int) -> timedelta:
    return timedelta(seconds=min(30 * 2 ** max(attempts - 1, 0), 3600))  # 30s, 1m, 2m ... capped at 1h


class OutboxWorker:
    def __init__(self, db: Database, registry: TenantRegistry, hub: Hub, poll_seconds: float = 5.0, max_attempts: int = 8) -> None:
        self.db, self.registry, self.hub = db, registry, hub
        self.poll, self.max_attempts = poll_seconds, max_attempts
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="ticket-outbox")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(BaseException):
                await self._task

    async def _run(self) -> None:
        while True:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("outbox iteration failed")
            await asyncio.sleep(self.poll)

    async def run_once(self, batch: int = 20) -> int:
        async with self.db.session() as s:
            pending = (await s.execute(select(func_count()).where(TicketOutbox.status == "pending"))).scalar() or 0
            metrics.OUTBOX_DEPTH.set(pending)
            ids = list((await s.execute(
                select(TicketOutbox.id).where(TicketOutbox.status == "pending", TicketOutbox.next_attempt_at <= utcnow())
                .order_by(TicketOutbox.next_attempt_at).limit(batch)
            )).scalars())
        done = 0
        for oid in ids:
            done += await self._process(oid)
        return done

    async def _process(self, oid: int) -> int:
        async with self.db.session() as s:
            # Atomic claim: with several replicas only one wins this UPDATE.
            res = await s.execute(update(TicketOutbox).where(TicketOutbox.id == oid, TicketOutbox.status == "pending").values(status="processing"))
            await s.commit()
            if res.rowcount != 1:
                return 0
            row = await s.get(TicketOutbox, oid)
            assert row is not None
            rt = self.registry.by_id(row.tenant_id)
            conv = await convs.get_conversation(s, row.tenant_id, row.conversation_id)
            if not rt or not rt.tickets or not conv:
                row.status, row.last_error = "dead", "tenant/provider/conversation gone"
                await s.commit()
                return 0
            req = TicketRequest(**{**row.payload, "attempt": row.attempts + 1})
            try:
                res_ = await rt.tickets.create_ticket(req)
            except TicketError as e:
                row.attempts += 1
                row.last_error = str(e)[:500]
                if e.retryable and row.attempts < self.max_attempts:
                    row.status, row.next_attempt_at = "pending", utcnow() + backoff(row.attempts)
                    await s.commit()
                    metrics.TICKETS.labels(row.tenant_id, rt.tenant.tickets.provider, "retry").inc()
                    return 0
                row.status = "dead"
                await s.commit()
                metrics.TICKETS.labels(row.tenant_id, rt.tenant.tickets.provider, "failed").inc()
                log.error("ticket outbox %s dead after %d attempts: %s", oid, row.attempts, e)
                await convs.add_message(s, self.hub, conv, "system", "We couldn't file your ticket automatically; a person will follow up.", {"event": "ticket_failed"})
                await start_handoff(s, self.hub, rt, conv, "Ticket could not be filed", row.payload.get("summary", ""), trigger="ticket_failed")
                return 0
            row.status, row.ticket_key, row.attempts = "done", res_.key, row.attempts + 1
            s.add(Ticket(tenant_id=row.tenant_id, conversation_id=conv.id, key=res_.key, url=res_.url, summary=str(row.payload.get("summary", ""))[:300], provider=res_.provider))
            await s.commit()
            metrics.TICKETS.labels(row.tenant_id, res_.provider, "created").inc()
            await convs.add_message(s, self.hub, conv, "system", f"Your ticket {res_.key} has been created.", {"event": "ticket_created", "tickets": [{"key": res_.key, "url": res_.url}]})
            return 1


def func_count() -> Any:
    from sqlalchemy import func

    return func.count(TicketOutbox.id)
