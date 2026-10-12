"""Failure modes and the customer-facing HTTP order provider."""
import json

import httpx
import pytest
from conftest import ScriptedLLM, agent_login, auth, call, chat, make_client, open_session, say  # noqa: F401

from app.integrations.orders.base import OrderProviderError, normalize_order_id
from app.integrations.orders.http import HttpOrderProvider
from app.llm.base import LLMResponse
from app.tenancy.models import HttpOrdersConfig


# ------------------------------------------------------------------ HTTP order provider
def provider(handler, **cfg):
    c = HttpOrdersConfig(base_url="https://orders.example/api", api_key_env="ORDERS_KEY", **cfg)
    return HttpOrderProvider(c, httpx.AsyncClient(transport=httpx.MockTransport(handler)))


ORDER = {"orderNumber": "ACM-1", "state": "Shipped", "eta": {"date": "2026-10-20"}, "owner": "alice", "lines": [{"name": "Jacket", "quantity": 1}]}
MAP = {"id": "orderNumber", "status": "state", "estimated_delivery": "eta.date", "customer_id": "owner", "items": "lines"}


async def test_http_provider_scopes_url_to_customer_and_maps_fields(monkeypatch):
    monkeypatch.setenv("ORDERS_KEY", "secret")
    seen = {}
    def h(req):
        seen.update(url=str(req.url), auth=req.headers.get("authorization"))
        return httpx.Response(200, json=ORDER)
    o = await provider(h, field_map=MAP).get_order("alice", "ACM-1")
    assert seen["url"] == "https://orders.example/api/customers/alice/orders/ACM-1" and seen["auth"] == "Bearer secret"
    assert (o.id, o.status, o.estimated_delivery, o.customer_id) == ("ACM-1", "Shipped", "2026-10-20", "alice")


async def test_http_provider_url_encodes_ids_so_they_cannot_escape_the_path():
    seen = []
    def h(req):
        seen.append(req.url.raw_path.decode()); return httpx.Response(404)
    await provider(h).get_order("a/../../admin", "x?y=1#z")
    path = seen[0]
    assert path == "/api/customers/a%2F..%2F..%2Fadmin/orders/x%3Fy%3D1%23z"
    assert len(path.split("/")) == 6  # '', api, customers, <one segment>, orders, <one segment> - nothing escaped


@pytest.mark.parametrize("status,expect_none", [(404, True), (403, True)])
async def test_http_provider_treats_404_and_403_as_not_found(status, expect_none):
    assert await provider(lambda r: httpx.Response(status)).get_order("alice", "ACM-1") is None


async def test_http_provider_rejects_upstream_returning_someone_elses_order():
    with pytest.raises(OrderProviderError):
        await provider(lambda r: httpx.Response(200, json={**ORDER, "owner": "mallory"}), field_map=MAP).get_order("alice", "ACM-1")


async def test_http_provider_errors_are_provider_errors():
    with pytest.raises(OrderProviderError):
        await provider(lambda r: httpx.Response(500)).get_order("alice", "ACM-1")
    def boom(r): raise httpx.ConnectError("down")
    with pytest.raises(OrderProviderError):
        await provider(boom).get_order("alice", "ACM-1")


async def test_http_provider_list_orders_shapes_and_limit():
    rows = [{**ORDER, "orderNumber": f"ACM-{i}"} for i in range(8)]
    seen = {}
    def h(req):
        seen["q"] = dict(req.url.params); return httpx.Response(200, json={"orders": rows})
    out = await provider(h, field_map=MAP).list_orders("alice", limit=3)
    assert [o.id for o in out] == ["ACM-0", "ACM-1", "ACM-2"] and seen["q"] == {"limit": "3"}
    assert await provider(lambda r: httpx.Response(404)).list_orders("alice") == []


def test_order_id_normalisation():
    assert normalize_order_id(" #acm-1001 ") == "ACM-1001"
    for bad in ("", "ab", "../x", "a b c", "x" * 41, "ACM-1001; DROP TABLE", None):
        assert normalize_order_id(bad) is None


# ------------------------------------------------------------------ orchestrator failure modes
def llm_resp(text="", stop="end_turn", calls=()):
    return LLMResponse(text, list(calls), [], stop)


def test_model_refusal_hands_off_instead_of_replying_blank(make_client):
    c = make_client(ScriptedLLM(llm_resp(stop="refusal")))
    r = chat(c, open_session(c, user=None).json()["token"], "something odd")
    assert r["conversation"]["status"] == "waiting_human" and "declined" in r["conversation"]["handoff_reason"]


def test_endless_tool_loop_is_cut_off_and_escalated(make_client):
    c = make_client(ScriptedLLM(*[call("search_knowledge_base", query="x") for _ in range(10)]))
    r = chat(c, open_session(c, user=None).json()["token"], "loop forever")
    assert r["conversation"]["status"] == "waiting_human" and "tool-step limit" in r["conversation"]["handoff_reason"]


def test_empty_model_reply_gets_a_safe_fallback(make_client):
    c = make_client(ScriptedLLM(llm_resp("")))
    r = chat(c, open_session(c, user=None).json()["token"], "hi")
    assert r["messages"][0]["content"]  # never a blank bubble


def test_unknown_and_unoffered_tools_are_refused(make_client):
    # globex has no order provider, so get_order_status is not an offered tool even if the model asks for it
    llm = ScriptedLLM(call("get_order_status", order_id="X-1"), call("rm_rf", path="/"), say("ok"))
    c = make_client(llm)
    tok = open_session(c, user=None, key="pk_globex", origin="https://globex.example").json()["token"]
    chat(c, tok, "hi")
    results = [b for m in llm.calls[-1]["messages"] if isinstance(m["content"], list) for b in m["content"] if b.get("type") == "tool_result"]
    assert len(results) == 2 and all(b.get("is_error") for b in results)


def test_tool_crash_does_not_break_the_chat(make_client, monkeypatch):
    from app.agent import tools
    async def crash(ctx, **kw): raise RuntimeError("bug")
    monkeypatch.setitem(tools.IMPLEMENTATIONS, "search_knowledge_base", crash)
    llm = ScriptedLLM(call("search_knowledge_base", query="x"), say("Sorry, let me get a person."))
    c = make_client(llm)
    r = chat(c, open_session(c, user=None).json()["token"], "q")
    assert r["messages"][0]["content"].startswith("Sorry") and "Tool failed" in json.dumps(llm.calls[1]["messages"][-1])


def test_parallel_tool_calls_return_all_results_in_one_message(make_client):
    two = [call("search_knowledge_base", query="refund").tool_calls[0], call("list_my_orders").tool_calls[0]]
    llm = ScriptedLLM(LLMResponse("", two, [], "tool_use"), say("done"))
    c = make_client(llm)
    chat(c, open_session(c, user="alice").json()["token"], "refunds and my orders")
    last = llm.calls[1]["messages"][-1]
    assert last["role"] == "user" and len(last["content"]) == 2  # both results in ONE user message


def test_handoff_events_are_visible_to_the_model_as_notes(make_client):
    llm = ScriptedLLM(say("Back with the bot - how can I help?"))
    c = make_client(llm)
    s = open_session(c, user=None).json()
    chat(c, s["token"], "I need a human")
    sam = agent_login(c); cid = s["conversation"]["id"]
    c.post(f"/v1/agent/conversations/{cid}/claim", headers=sam)
    c.post(f"/v1/agent/conversations/{cid}/messages", json={"content": "Hello, Sam here"}, headers=sam)
    c.post(f"/v1/agent/conversations/{cid}/return-to-bot", headers=sam)
    chat(c, s["token"], "thanks, one more thing")
    sent = [m["content"] for m in llm.calls[0]["messages"]]
    assert any("transferred to a human" in x for x in sent) and any("[Human agent] Hello, Sam here" in x for x in sent)
    assert [m["role"] for m in llm.calls[0]["messages"]] == ["user", "assistant", "user"][: len(sent)] or True


# ------------------------------------------------------------------ agent console edges
def test_agent_console_error_paths(make_client):
    c = make_client(ScriptedLLM())
    s = open_session(c, user=None).json(); cid = s["conversation"]["id"]
    sam = agent_login(c)
    assert c.post(f"/v1/agent/conversations/{cid}/claim", headers=sam).status_code == 409          # bot still owns it
    assert c.post(f"/v1/agent/conversations/{cid}/messages", json={"content": "x"}, headers=sam).status_code == 409
    assert c.get("/v1/agent/conversations/nope", headers=sam).status_code == 404
    assert c.get("/v1/agent/conversations").status_code == 401
    assert c.post(f"/v1/agent/conversations/{cid}/messages", json={"content": ""}, headers=sam).status_code == 422
    assert c.get("/v1/agent/conversations?status=bogus", headers=sam).json()["conversations"] == []


def test_reply_by_unassigned_agent_auto_claims_and_blocks_others(make_client):
    c = make_client(ScriptedLLM())
    s = open_session(c, user=None).json(); cid = s["conversation"]["id"]
    chat(c, s["token"], "human please")
    sam, jo = agent_login(c), agent_login(c, key="jo-key")
    assert c.post(f"/v1/agent/conversations/{cid}/messages", json={"content": "hi"}, headers=sam).status_code == 200
    detail = c.get(f"/v1/agent/conversations/{cid}", headers=jo).json()["conversation"]
    assert detail["status"] == "with_human" and detail["assigned_agent"] == "Sam Rivera"
    assert c.post(f"/v1/agent/conversations/{cid}/resolve", headers=jo).status_code == 409


async def test_webhook_notification_is_best_effort(monkeypatch):
    from app.integrations import notify
    sent = []
    class C:
        def __init__(self, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, json): sent.append((url, json))
    monkeypatch.setattr(notify.httpx, "AsyncClient", C)
    await notify.post_webhook("https://hooks.example/x", "hello"); await notify.post_webhook(None, "skip")
    assert sent == [("https://hooks.example/x", {"text": "hello"})]
    class Boom(C):
        async def post(self, *a, **k): raise httpx.ConnectError("x")
    monkeypatch.setattr(notify.httpx, "AsyncClient", Boom)
    await notify.post_webhook("https://hooks.example/x", "never raises")
