"""Resilience suite: inject the failures production will eventually produce and assert graceful degradation.
Principles checked: the customer is never stranded (worst case = a human), nothing is silently lost, errors never leak
internals, and the platform's own health endpoints tell the truth."""
from __future__ import annotations

import asyncio
import time
from typing import Any

from app.integrations.orders.base import OrderProviderError
from app.integrations.tickets.base import TicketError, TicketResult
from app.services.outbox import OutboxWorker

from ..doubles import ScriptedLLM, call, down, say
from ..harness import EvalApp
from ..stats import rate

TICKET = dict(summary="Zipper broke after a week", description="The zipper split after one week of normal use", contact_email="alice@example.com")


class FlakyTracker:
    def __init__(self, fail: int, retryable: bool = True) -> None:
        self.fail, self.retryable, self.calls, self.ok = fail, retryable, 0, 0

    async def create_ticket(self, req: Any) -> TicketResult:
        self.calls += 1
        if self.calls <= self.fail:
            raise TicketError("Jira unavailable (HTTP 503)", retryable=self.retryable)
        self.ok += 1
        return TicketResult(f"SUP-{self.ok}", f"https://jira.example/browse/SUP-{self.ok}", "memory")


class BrokenOrders:
    async def get_order(self, *a: Any, **k: Any) -> None:
        raise OrderProviderError("upstream 500")

    async def list_orders(self, *a: Any, **k: Any) -> list:
        raise OrderProviderError("upstream 500")


def run(ctx: dict[str, Any]) -> dict[str, Any]:
    td = ctx["tenants_dir"]
    results: list[dict[str, Any]] = []

    def check(cid: str, ok: bool, detail: str = "") -> None:
        results.append({"id": cid, "passed": bool(ok), "detail": detail})

    def msg_text(t: Any) -> str:
        return t.reply.lower()

    # 1. model provider hard-down / overloaded / timing out -> customer lands with a human, no 500
    for kind in ("error", "overload", "timeout"):
        with EvalApp(ScriptedLLM(down(kind)), tenants_dir=td) as app:
            s = app.open_session("alice")
            t = app.say(s, "where is my order?")
            check(f"llm_{kind}_hands_off", t.status == "waiting_human" and t.reply != "", f"status={t.status}")
            check(f"llm_{kind}_no_internal_leak", not any(w in msg_text(t) for w in ("traceback", "exception", "anthropic", "llmerror")))

    # 2. tracker down: ticket parked, customer gets a reference, worker files it once the tracker recovers
    llm = ScriptedLLM(call("create_support_ticket", **TICKET), say("Saved - your reference is REQ-1."))
    with EvalApp(llm, tenants_dir=td) as app:
        tracker = FlakyTracker(fail=2)
        app.app.state.sp.registry.by_id(ctx["tenant"]).tickets = tracker
        s = app.open_session("alice")
        t = app.say(s, "please file a ticket")
        check("tracker_down_ticket_queued_not_lost", "REQ-" in t.reply or any(r.get("queued") for r in t.tool_results), f"{t.tool_results}")
        sp = app.app.state.sp
        worker = OutboxWorker(sp.db, sp.registry, sp.hub)

        async def drain() -> int:
            from sqlalchemy import update
            from app.db import TicketOutbox, utcnow
            n = 0
            for _ in range(4):  # fast-forward the backoff timer each round
                async with sp.db.session() as db:
                    await db.execute(update(TicketOutbox).where(TicketOutbox.status == "pending").values(next_attempt_at=utcnow()))
                    await db.commit()
                n += await worker.run_once()
            return n
        filed = asyncio.new_event_loop().run_until_complete(drain())
        check("tracker_recovers_ticket_eventually_filed", filed == 1 and tracker.ok == 1, f"filed={filed} ok={tracker.ok}")
        check("tracker_retries_never_duplicate", tracker.ok == 1)

    # 3. tracker rejects permanently (bad config) -> not queued forever; customer is told honestly
    llm = ScriptedLLM(call("create_support_ticket", **TICKET), say("Sorry, I couldn't file that - a person will help."))
    with EvalApp(llm, tenants_dir=td) as app:
        app.app.state.sp.registry.by_id(ctx["tenant"]).tickets = FlakyTracker(fail=99, retryable=False)
        t = app.say(app.open_session("alice"), "file it")
        check("tracker_permanent_error_not_claimed_as_success", not t.tickets and "SUP-" not in t.reply)

    # 4. order system down -> graceful message, no crash, no fabricated status
    with EvalApp(ScriptedLLM(call("get_order_status", order_id="ACM-1001"), say("I can't reach the order system right now.")), tenants_dir=td) as app:
        app.app.state.sp.registry.by_id(ctx["tenant"]).orders = BrokenOrders()
        t = app.say(app.open_session("alice"), "status of ACM-1001")
        check("order_api_down_graceful", t.status == "bot" and any(r.get("error") == "order_system_unavailable" for r in t.tool_results))

    # 5. a bug in a tool must not kill the conversation
    from app.agent import tools as tools_mod
    orig = tools_mod.IMPLEMENTATIONS["search_knowledge_base"]

    async def crash(*a: Any, **k: Any) -> None:
        raise RuntimeError("bug")
    tools_mod.IMPLEMENTATIONS["search_knowledge_base"] = crash
    try:
        with EvalApp(ScriptedLLM(call("search_knowledge_base", query="x"), say("Let me get a person for you.")), tenants_dir=td) as app:
            t = app.say(app.open_session(None), "anything")
            check("tool_crash_contained", bool(t.reply))
    finally:
        tools_mod.IMPLEMENTATIONS["search_knowledge_base"] = orig

    # 6. runaway model (tool loop) is cut off and escalated
    with EvalApp(ScriptedLLM(*[call("search_knowledge_base", query="x") for _ in range(12)]), tenants_dir=td) as app:
        t = app.say(app.open_session(None), "loop")
        check("runaway_tool_loop_escalates", t.status == "waiting_human")

    # 7. health endpoints tell the truth
    with EvalApp(ScriptedLLM(), tenants_dir=td) as app:
        check("liveness_ok", app.client.get("/healthz").status_code == 200)
        r = app.client.get("/readyz")
        check("readiness_ok_when_healthy", r.status_code == 200 and r.json()["checks"]["database"] == "ok")
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        app.app.state.sp.db.sessionmaker = async_sessionmaker(create_async_engine("sqlite+aiosqlite:////nonexistent/x.db"))
        check("readiness_red_when_db_down_liveness_stays_green", app.client.get("/readyz").status_code == 503 and app.client.get("/healthz").status_code == 200)

    # 8. one broken tenant config must not take the others down
    with EvalApp(ScriptedLLM(say("ok")), tenants_dir=td, extra_tenants={"broken": "id: broken\nname: [this is not valid"}) as app:
        loaded = {rt.tenant.id for rt in app.app.state.sp.registry.all()}
        check("bad_tenant_config_isolated", ctx["tenant"] in loaded and "broken" not in loaded, str(loaded))

    # 9. platform latency (excluding the model): a scripted model returns instantly, so this is OUR overhead
    with EvalApp(ScriptedLLM(*[say("ok")] * 30), tenants_dir=td, messages_per_minute=1000) as app:
        s = app.open_session("alice")
        lat = []
        for i in range(25):
            t0 = time.perf_counter(); app.say(s, f"hi {i}"); lat.append((time.perf_counter() - t0) * 1000)
        lat.sort()
        p95 = lat[int(len(lat) * 0.95) - 1]
        check("platform_overhead_p95_under_250ms", p95 < 250, f"p95={p95:.0f}ms")

    passed = sum(r["passed"] for r in results)
    return {"metrics": {"pass_rate": rate(passed, len(results))}, "failures": [r for r in results if not r["passed"]], "info": {"cases": len(results)}}
