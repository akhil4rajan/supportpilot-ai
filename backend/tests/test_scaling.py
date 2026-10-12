"""Behaviour that only matters once there is more than one replica, one user at a time, or a failing dependency."""
import asyncio
import json

import pytest
import redis.asyncio as aioredis
from conftest import ScriptedLLM, async_app, auth, call, say, user_token

from app.config import Settings
from app.db import TicketOutbox
from app.integrations.tickets.base import TicketError, TicketRequest, TicketResult
from app.llm.base import LLMError, LLMResponse
from app.llm.guard import GuardedLLM
from app.services.outbox import OutboxWorker, backoff
from app.services.ratelimit import RateLimiter, RedisRateLimiter
from app.services.realtime import Hub, RedisHub


# ------------------------------------------------------------------ atomic claim
async def test_two_agents_claiming_at_once_exactly_one_wins(tmp_path):
    async with async_app(tmp_path, ScriptedLLM()) as c:
        s = (await c.post("/v1/widget/sessions", json={"public_key": "pk_acme_demo"})).json()
        await c.post("/v1/widget/messages", json={"content": "get me a human"}, headers=auth(s["token"]))
        cid = s["conversation"]["id"]
        sam = auth((await c.post("/v1/agent/login", json={"tenant_id": "acme", "api_key": "sam-key"})).json()["token"])
        jo = auth((await c.post("/v1/agent/login", json={"tenant_id": "acme", "api_key": "jo-key"})).json()["token"])
        results = await asyncio.gather(*[c.post(f"/v1/agent/conversations/{cid}/claim", headers=h) for h in (sam, jo) * 5])
        codes = sorted(r.status_code for r in results)
        winners = {r.json()["conversation"]["assigned_agent"] for r in results if r.status_code == 200}
        assert len(winners) == 1, winners  # never two owners
        assert codes.count(200) == 5 and codes.count(409) == 5  # the winner's repeat claims are idempotent
        d = (await c.get(f"/v1/agent/conversations/{cid}", headers=sam)).json()
        assert sum(1 for m in d["messages"] if m["meta"].get("event") == "agent_joined") == 1


# ------------------------------------------------------------------ Redis hub / limiter (real redis-server)
async def test_redis_hub_delivers_across_replicas(redis_url):
    a, b = RedisHub(aioredis.from_url(redis_url, decode_responses=True)), RedisHub(aioredis.from_url(redis_url, decode_responses=True))
    await a.start(); await b.start()
    try:
        await asyncio.sleep(0.3)  # let both psubscribe
        with a.subscribe_conversation("c1") as qa, b.subscribe_agents("t1") as qb:
            await b.to_customer("c1", {"type": "message", "n": 1})   # published by replica B...
            await a.to_agents("t1", {"type": "conversation_updated"})  # ...and by A for an agent on B
            assert (await asyncio.wait_for(qa.get(), 2))["n"] == 1     # ...delivered to a socket on replica A
            assert (await asyncio.wait_for(qb.get(), 2))["type"] == "conversation_updated"
        assert a.connection_count == 0
    finally:
        await a.stop(); await b.stop()


async def test_redis_hub_degrades_to_local_delivery_when_redis_is_down():
    dead = aioredis.from_url("redis://127.0.0.1:1/0", decode_responses=True, socket_connect_timeout=0.2)
    hub = RedisHub(dead)  # never started: simulates the listener being down too
    with hub.subscribe_conversation("c1") as q:
        await hub.to_customer("c1", {"type": "message"})
        assert (await asyncio.wait_for(q.get(), 1))["type"] == "message"  # same-replica sockets still get it


async def test_redis_limiter_is_shared_and_fails_open(redis_url):
    r1, r2 = (aioredis.from_url(redis_url, decode_responses=True) for _ in range(2))
    await r1.flushdb()
    l1, l2 = RedisRateLimiter(r1, limit=3, window_seconds=60), RedisRateLimiter(r2, limit=3, window_seconds=60)
    got = [await l1.allow("conv"), await l2.allow("conv"), await l1.allow("conv"), await l2.allow("conv")]
    assert got == [True, True, True, False]  # one budget across replicas
    dead = RedisRateLimiter(aioredis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=0.2), limit=1)
    assert await dead.allow("x") and await dead.allow("x")  # fail open


async def test_in_memory_limiter_window():
    lim = RateLimiter(2, 60)
    assert [await lim.allow("k"), await lim.allow("k"), await lim.allow("k"), await lim.allow("other")] == [True, True, False, True]


async def test_app_runs_on_redis_end_to_end(tmp_path, redis_url):
    async with async_app(tmp_path, redis_url=redis_url) as c:
        assert (await c.get("/readyz")).json()["checks"]["redis"] == "ok"
        s = (await c.post("/v1/widget/sessions", json={"public_key": "pk_acme_demo"})).json()
        with c.app.state.sp.hub.subscribe_conversation(s["conversation"]["id"]) as q:
            await asyncio.sleep(0.3)
            await c.post("/v1/widget/messages", json={"content": "How long do refunds take?"}, headers=auth(s["token"]))
            ev = await asyncio.wait_for(q.get(), 3)  # event travelled app -> Redis -> hub listener -> socket queue
            assert ev["type"] == "message"


# ------------------------------------------------------------------ LLM guard
class SlowLLM:
    def __init__(self, delay): self.delay, self.active, self.peak = delay, 0, 0
    async def complete(self, **kw):
        self.active += 1; self.peak = max(self.peak, self.active)
        try:
            await asyncio.sleep(self.delay)
        finally:
            self.active -= 1
        return LLMResponse("ok", [], [], "end_turn", input_tokens=100, output_tokens=10, cache_read_tokens=80)


async def test_guard_bounds_concurrency():
    inner = SlowLLM(0.05)
    g = GuardedLLM(inner, "m", max_concurrency=3, queue_timeout=5)
    await asyncio.gather(*[g.complete(system="s", messages=[], tools=[]) for _ in range(12)])
    assert inner.peak == 3


async def test_guard_sheds_load_and_times_out():
    g = GuardedLLM(SlowLLM(0.3), "m", max_concurrency=1, queue_timeout=0.05)
    first = asyncio.create_task(g.complete(system="s", messages=[], tools=[]))
    await asyncio.sleep(0.02)
    with pytest.raises(LLMError) as e:
        await g.complete(system="s", messages=[], tools=[])
    assert e.value.kind == "overload"
    await first
    slow = GuardedLLM(SlowLLM(1), "m", call_timeout=0.05)
    with pytest.raises(LLMError) as e2:
        await slow.complete(system="s", messages=[], tools=[])
    assert e2.value.kind == "timeout"


async def test_overloaded_model_degrades_to_human_not_to_an_error(tmp_path):
    class Overloaded:
        async def complete(self, **kw):
            raise LLMError("busy", "overload")
    async with async_app(tmp_path, Overloaded()) as c:
        s = (await c.post("/v1/widget/sessions", json={"public_key": "pk_acme_demo"})).json()
        r = await c.post("/v1/widget/messages", json={"content": "hello"}, headers=auth(s["token"]))
        assert r.status_code == 200 and r.json()["conversation"]["status"] == "waiting_human"
        assert "overload" in r.json()["conversation"]["handoff_reason"]


# ------------------------------------------------------------------ ticket outbox
class FlakyTracker:
    """Fails with a retryable error `fail_times`, then works. Records idempotency keys it was given."""
    def __init__(self, fail_times, retryable=True):
        self.fail_times, self.retryable, self.calls, self.created = fail_times, retryable, [], []
    async def create_ticket(self, req: TicketRequest) -> TicketResult:
        self.calls.append(req)
        if len(self.calls) <= self.fail_times:
            raise TicketError("Jira unavailable (HTTP 503)", retryable=self.retryable)
        self.created.append(req)
        return TicketResult(f"SUP-{len(self.created)}", f"https://j/browse/SUP-{len(self.created)}", "memory")


TICKET = dict(summary="Zipper broke", description="Zipper split after a week of use", contact_email="a@x.com")


async def _session_and_ticket(c, tracker):
    c.app.state.sp.registry.by_id("acme").tickets = tracker
    s = (await c.post("/v1/widget/sessions", json={"public_key": "pk_acme_demo"})).json()
    r = await c.post("/v1/widget/messages", json={"content": "file it"}, headers=auth(s["token"]))
    return s, r.json()


async def test_tracker_outage_queues_ticket_then_worker_files_it(tmp_path):
    llm = ScriptedLLM(call("create_support_ticket", **TICKET), say("Saved, reference below."))
    async with async_app(tmp_path, llm) as c:
        tracker = FlakyTracker(fail_times=2)
        s, r = await _session_and_ticket(c, tracker)
        assert "REQ-1" in json.dumps(llm.calls[1]["messages"][-1]) and "queued" in json.dumps(llm.calls[1]["messages"][-1])
        st = c.app.state.sp
        w = OutboxWorker(st.db, st.registry, st.hub)
        async with st.db.session() as db:
            row = await db.get(TicketOutbox, 1)
            assert row.status == "pending"
        assert await w.run_once() == 0  # attempt 2 fails again -> stays pending with backoff
        async with st.db.session() as db:
            row = await db.get(TicketOutbox, 1); assert row.attempts == 1 and row.status == "pending" and row.next_attempt_at is not None
            row.next_attempt_at = row.created_at; await db.commit()   # fast-forward the backoff
        assert await w.run_once() == 1  # recovered
        conv = (await c.get("/v1/widget/conversation", headers=auth(s["token"]))).json()
        assert any("SUP-1" in m["content"] for m in conv["messages"] if m["role"] == "system")
        # every attempt carried the same idempotency key and a rising attempt counter
        assert len({t.idempotency_key for t in tracker.calls}) == 1 and [t.attempt for t in tracker.calls] == [0, 1, 2]


async def test_non_retryable_error_is_not_queued(tmp_path):
    llm = ScriptedLLM(call("create_support_ticket", **TICKET), say("Sorry."))
    async with async_app(tmp_path, llm) as c:
        await _session_and_ticket(c, FlakyTracker(fail_times=9, retryable=False))
        assert "ticket_system_unavailable" in json.dumps(llm.calls[1]["messages"][-1])
        async with c.app.state.sp.db.session() as db:
            assert await db.get(TicketOutbox, 1) is None


async def test_ticket_that_never_files_ends_up_with_a_human(tmp_path):
    llm = ScriptedLLM(call("create_support_ticket", **TICKET), say("Saved."))
    async with async_app(tmp_path, llm, outbox_max_attempts=2) as c:
        s, _ = await _session_and_ticket(c, FlakyTracker(fail_times=99))
        st = c.app.state.sp
        w = OutboxWorker(st.db, st.registry, st.hub, max_attempts=2)
        for _ in range(2):
            await w.run_once()
            async with st.db.session() as db:
                row = await db.get(TicketOutbox, 1); row.next_attempt_at = row.created_at; await db.commit()
        conv = (await c.get("/v1/widget/conversation", headers=auth(s["token"]))).json()["conversation"]
        assert (conv["status"], conv["handoff_reason"]) == ("waiting_human", "Ticket could not be filed")


async def test_two_workers_never_file_the_same_ticket_twice(tmp_path):
    llm = ScriptedLLM(call("create_support_ticket", **TICKET), say("Saved."))
    async with async_app(tmp_path, llm) as c:
        tracker = FlakyTracker(fail_times=1)
        await _session_and_ticket(c, tracker)
        st = c.app.state.sp
        ws = [OutboxWorker(st.db, st.registry, st.hub) for _ in range(4)]
        await asyncio.gather(*[w._process(1) for w in ws])
        assert len(tracker.calls) == 2  # 1 initial failure + exactly one worker's attempt


def test_backoff_grows_and_caps():
    assert [backoff(n).total_seconds() for n in (1, 2, 3, 8, 20)] == [30, 60, 120, 3600, 3600]


# ------------------------------------------------------------------ jira idempotency
def test_jira_retry_finds_existing_issue_instead_of_duplicating():
    from types import SimpleNamespace
    from app.integrations.tickets.jira_provider import JiraTicketProvider
    from app.tenancy.models import JiraConfig

    class J:
        created = []
        def search_issues(self, jql, maxResults): self.jql = jql; return [SimpleNamespace(key="SUP-7")]
        def create_issue(self, fields): self.created.append(fields)
    j = J()
    p = JiraTicketProvider(JiraConfig(server="https://x", project_key="SUP"), client=j)
    res = asyncio.run(p.create_ticket(TicketRequest("s", "d", idempotency_key="abc", attempt=1)))
    assert res.key == "SUP-7" and not j.created and 'labels = "sp-abc"' in j.jql
    j2 = J(); j2.search_issues = lambda *a, **k: []
    j2.create_issue = lambda fields: (j2.created.append(fields) or SimpleNamespace(key="SUP-8"))
    asyncio.run(JiraTicketProvider(JiraConfig(server="https://x", project_key="SUP"), client=j2).create_ticket(TicketRequest("s", "d", idempotency_key="abc", attempt=0)))
    assert "sp-abc" in j2.created[0]["labels"]


def test_jira_error_classification():
    from jira.exceptions import JIRAError
    from app.integrations.tickets.jira_provider import JiraTicketProvider
    c = JiraTicketProvider._classify
    assert c(JIRAError(status_code=503, text="x")).retryable and c(JIRAError(status_code=429, text="x")).retryable
    assert not c(JIRAError(status_code=400, text="x")).retryable and not c(JIRAError(status_code=401, text="x")).retryable
    assert c(ConnectionError("boom")).retryable


# ------------------------------------------------------------------ observability + prompt caching
async def test_readyz_metrics_and_dependency_failure(tmp_path):
    async with async_app(tmp_path, metrics_token="m-token") as c:
        assert (await c.get("/healthz")).status_code == 200
        r = await c.get("/readyz"); assert r.status_code == 200 and r.json()["checks"]["database"] == "ok"
        s = (await c.post("/v1/widget/sessions", json={"public_key": "pk_acme_demo"})).json()
        await c.post("/v1/widget/messages", json={"content": "How long do refunds take?"}, headers=auth(s["token"]))
        assert (await c.get("/metrics")).status_code == 401
        body = (await c.get("/metrics", headers={"Authorization": "Bearer m-token"})).text
        for needle in ('sp_turns_total{outcome="answered",tenant="acme"}', 'sp_tool_calls_total{outcome="ok",tool="search_knowledge_base"}',
                       'sp_kb_searches_total{match="', 'sp_http_request_seconds_count{method="POST",route="/v1/widget/messages",status="200"}'):
            assert needle in body, needle
        await c.app.state.sp.db.dispose()
        # break the DB: readiness must go red so the LB drains this replica (liveness stays green)
        from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
        c.app.state.sp.db.sessionmaker = async_sessionmaker(create_async_engine("sqlite+aiosqlite:////nonexistent/dir/x.db"))
        assert (await c.get("/readyz")).status_code == 503 and (await c.get("/healthz")).status_code == 200


def test_prompt_is_split_so_the_tenant_prefix_is_cacheable():
    from app.agent.prompts import build_system_prompt
    from app.auth import VerifiedUser
    from app.tenancy.registry import TenantRegistry
    from conftest import ROOT
    rt = TenantRegistry(ROOT / "tenants").by_id("acme")
    s1, d1 = build_system_prompt(rt, None)
    s2, d2 = build_system_prompt(rt, VerifiedUser("alice", "Alice"))
    assert s1 == s2 and d1 != d2          # identical cacheable prefix for everyone
    assert "alice" not in s1.lower() and "Today is" not in s1


async def test_anthropic_client_marks_cache_breakpoint_and_reads_usage():
    from types import SimpleNamespace
    from app.llm.anthropic_client import AnthropicLLM

    class M:
        async def create(self, **kw):
            self.kw = kw
            return SimpleNamespace(content=[SimpleNamespace(type="text", text="hi")], stop_reason="end_turn",
                                   usage=SimpleNamespace(input_tokens=50, output_tokens=7, cache_read_input_tokens=1200, cache_creation_input_tokens=0))
    m = M()
    r = await AnthropicLLM("claude-opus-5-5", 512, client=SimpleNamespace(messages=m)).complete(system="STATIC\n\nDYN", messages=[], tools=[], static_system_len=6)
    assert m.kw["system"] == [{"type": "text", "text": "STATIC", "cache_control": {"type": "ephemeral"}}, {"type": "text", "text": "\n\nDYN"}]
    assert (r.cache_read_tokens, r.input_tokens, r.output_tokens) == (1200, 50, 7)


async def test_simulated_llm_shape():
    from app.llm.simulated import SimulatedLLM
    llm = SimulatedLLM(median_seconds=0.001, tool_rate=1.0, seed=1)
    a = await llm.complete(system="s", messages=[{"role": "user", "content": "q"}], tools=[])
    assert a.tool_calls and a.stop_reason == "tool_use"
    b = await llm.complete(system="s", messages=[{"role": "user", "content": [{"type": "tool_result"}]}], tools=[])
    assert not b.tool_calls and b.text


# ------------------------------------------------------------------ connection hygiene (found by the load test)
class PoolProbe:
    """Records how many DB connections are checked out WHILE the app is waiting on the model / an external system."""

    def __init__(self, steps):
        self.steps, self.checked_out = list(steps), []
        self.engine = None

    async def complete(self, **kw):
        self.checked_out.append(self.engine.pool.checkedout())
        return self.steps.pop(0)


async def test_no_db_connection_is_held_while_waiting_for_the_model(tmp_path):
    """A turn spends seconds inside the model call. If it keeps a connection through that wait, concurrency is capped at the
    pool size (15 by default) and the app collapses under load - exactly what the load test showed."""
    probe = PoolProbe([call("search_knowledge_base", query="refund"), call("get_order_status", order_id="ACM-1001"), say("done")])
    async with async_app(tmp_path, probe) as c:
        probe.engine = c.app.state.sp.db.engine
        token = user_token("alice")
        s = (await c.post("/v1/widget/sessions", json={"public_key": "pk_acme_demo", "user_token": token})).json()
        await c.post("/v1/widget/messages", json={"content": "refunds and order"}, headers=auth(s["token"]))
        assert probe.checked_out == [0, 0, 0], probe.checked_out


async def test_no_db_connection_is_held_while_waiting_for_the_ticket_tracker(tmp_path):
    seen = []

    class SlowTracker:
        async def create_ticket(self, req):
            seen.append(probe.engine.pool.checkedout())
            return TicketResult("SUP-1", None, "memory")
    probe = PoolProbe([call("create_support_ticket", **TICKET), say("filed")])
    async with async_app(tmp_path, probe) as c:
        probe.engine = c.app.state.sp.db.engine
        c.app.state.sp.registry.by_id("acme").tickets = SlowTracker()
        s = (await c.post("/v1/widget/sessions", json={"public_key": "pk_acme_demo"})).json()
        await c.post("/v1/widget/messages", json={"content": "file it"}, headers=auth(s["token"]))
        assert seen == [0], seen


async def test_no_db_connection_is_held_while_waiting_for_the_order_api(tmp_path):
    seen = []

    class SlowOrders:
        async def get_order(self, *a, **k):
            seen.append(probe.engine.pool.checkedout())
            return None
    probe = PoolProbe([call("get_order_status", order_id="ACM-1001"), say("done")])
    async with async_app(tmp_path, probe) as c:
        probe.engine = c.app.state.sp.db.engine
        c.app.state.sp.registry.by_id("acme").orders = SlowOrders()
        s = (await c.post("/v1/widget/sessions", json={"public_key": "pk_acme_demo", "user_token": user_token("alice")})).json()
        await c.post("/v1/widget/messages", json={"content": "status"}, headers=auth(s["token"]))
        assert seen == [0], seen
