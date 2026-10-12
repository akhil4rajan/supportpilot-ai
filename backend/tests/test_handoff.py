import asyncio

from conftest import ScriptedLLM, agent_login, auth, call, chat, make_client, open_session, say  # noqa: F401


def test_explicit_request_for_human_bypasses_the_model(make_client):
    llm = ScriptedLLM()  # no scripted steps: calling the model would raise
    c = make_client(llm)
    s = open_session(c, user="alice").json()
    r = chat(c, s["token"], "I want to talk to a real person")
    assert r["conversation"]["status"] == "waiting_human" and llm.calls == []
    assert r["messages"][-1]["role"] == "system"


def test_model_initiated_escalation_carries_summary(make_client):
    llm = ScriptedLLM(call("escalate_to_human", reason="refund dispute", summary="Wants refund for ACM-1002, says damaged"), say("A human will join."))
    c = make_client(llm)
    s = open_session(c, user="alice").json()
    r = chat(c, s["token"], "this arrived broken and I'm furious")
    assert r["conversation"]["status"] == "waiting_human"
    h = agent_login(c)
    detail = c.get(f"/v1/agent/conversations/{s['conversation']['id']}", headers=h).json()
    assert detail["conversation"]["handoff_summary"].startswith("Wants refund")
    assert detail["conversation"]["handoff_reason"] == "refund dispute"


def test_button_handoff_and_bot_goes_silent(make_client):
    llm = ScriptedLLM()
    c = make_client(llm)
    tok = open_session(c, user=None).json()["token"]
    assert c.post("/v1/widget/handoff", headers=auth(tok)).json()["conversation"]["status"] == "waiting_human"
    r = chat(c, tok, "hello? anyone?")
    assert r["messages"] == [] and llm.calls == []  # stored, not answered by the bot


def test_auto_escalation_after_repeated_unanswerable_questions(make_client):
    c = make_client()  # DemoLLM + max_unresolved_turns=2
    tok = open_session(c, user=None).json()["token"]
    r1 = chat(c, tok, "tell me about quantum chromodynamics")
    assert r1["conversation"]["status"] == "bot"
    r2 = chat(c, tok, "and what about string theory landscapes")
    assert r2["conversation"]["status"] == "waiting_human"
    assert "Automatic" in r2["conversation"]["handoff_reason"]


def test_answered_question_resets_the_counter(make_client):
    c = make_client()
    tok = open_session(c, user=None).json()["token"]
    chat(c, tok, "tell me about quantum chromodynamics")
    chat(c, tok, "how long do refunds take?")
    r = chat(c, tok, "string theory landscapes please")
    assert r["conversation"]["status"] == "bot"


def test_llm_failure_degrades_to_human(make_client):
    from app.llm.base import LLMError

    class Boom:
        async def complete(self, **kw):
            raise LLMError("down")

    c = make_client(Boom())
    tok = open_session(c, user=None).json()["token"]
    r = chat(c, tok, "hello")
    assert r["conversation"]["status"] == "waiting_human"
    assert any("trouble" in m["content"] for m in r["messages"])


def test_agent_workflow_end_to_end_with_live_delivery(make_client):
    c = make_client(ScriptedLLM(say("Standard shipping takes 3-7 business days.")))
    s = open_session(c, user="alice").json()
    cid = s["conversation"]["id"]
    chat(c, s["token"], "speak to a human please")

    # agent login: wrong key / wrong tenant
    assert c.post("/v1/agent/login", json={"tenant_id": "acme", "api_key": "nope"}).status_code == 401
    assert c.post("/v1/agent/login", json={"tenant_id": "acme", "api_key": "globex-key"}).status_code == 401
    sam = agent_login(c)
    queue = c.get("/v1/agent/conversations", headers=sam).json()["conversations"]
    assert [q["id"] for q in queue] == [cid] and queue[0]["status"] == "waiting_human"

    with c.websocket_connect(f"/v1/widget/ws?token={s['token']}") as customer_ws:
        assert c.post(f"/v1/agent/conversations/{cid}/claim", headers=sam).status_code == 200
        joined = customer_ws.receive_json()
        assert joined["type"] in ("message", "conversation_updated")
        # a second agent cannot hijack it
        jo = agent_login(c, key="jo-key")
        assert c.post(f"/v1/agent/conversations/{cid}/messages", json={"content": "mine"}, headers=jo).status_code == 409
        assert c.post(f"/v1/agent/conversations/{cid}/messages", json={"content": "Hi Alice, Sam here."}, headers=sam).status_code == 200
        seen = []
        for _ in range(4):
            ev = customer_ws.receive_json()
            seen.append(ev)
            if ev["type"] == "message" and ev["message"]["role"] == "agent":
                break
        assert any(e["type"] == "message" and e["message"]["content"] == "Hi Alice, Sam here." for e in seen)

    # customer reply reaches the agent socket
    with c.websocket_connect(f"/v1/agent/ws?token={sam['Authorization'][7:]}") as agent_ws:
        chat(c, s["token"], "thanks Sam, my order ACM-1002 arrived broken")
        evs = [agent_ws.receive_json() for _ in range(1)]
        assert evs[0]["message"]["role"] == "user"

    assert c.post(f"/v1/agent/conversations/{cid}/resolve", headers=sam).json()["conversation"]["status"] == "resolved"
    # customer returns -> reopens with the bot
    r = chat(c, s["token"], "how long is standard shipping?")  # customer comes back -> bot answers again
    assert r["conversation"]["status"] == "bot" and "3-7" in r["messages"][-1]["content"]


def test_return_to_bot_resumes_automation(make_client):
    c = make_client()
    s = open_session(c, user=None).json()
    chat(c, s["token"], "I need a human")
    sam = agent_login(c)
    cid = s["conversation"]["id"]
    c.post(f"/v1/agent/conversations/{cid}/claim", headers=sam)
    assert c.post(f"/v1/agent/conversations/{cid}/return-to-bot", headers=sam).json()["conversation"]["status"] == "bot"
    r = chat(c, s["token"], "how long do refunds take?")
    assert "5-7 business days" in r["messages"][-1]["content"]


def test_tenant_isolation_for_agents(make_client):
    c = make_client(ScriptedLLM())
    s = open_session(c, user="alice").json()
    chat(c, s["token"], "get me a human")
    globex = agent_login(c, tenant="globex", key="globex-key")
    cid = s["conversation"]["id"]
    assert c.get("/v1/agent/conversations", headers=globex).json()["conversations"] == []
    for method, path in (("get", ""), ("post", "/claim"), ("post", "/resolve")):
        assert getattr(c, method)(f"/v1/agent/conversations/{cid}{path}", headers=globex).status_code == 404
    assert c.post(f"/v1/agent/conversations/{cid}/messages", json={"content": "x"}, headers=globex).status_code == 404


def test_websocket_rejects_bad_tokens(make_client):
    import pytest
    from starlette.websockets import WebSocketDisconnect

    c = make_client()
    for path in ("/v1/widget/ws?token=bad", "/v1/agent/ws?token=bad", "/v1/widget/ws"):
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect(path):
                pass


import pytest
from app.agent.orchestrator import _WANTS_HUMAN


@pytest.mark.parametrize("text", [
    "get me a human", "I want to talk to a person", "can I speak with someone", "transfer me to an agent",
    "I need a real person", "human please", "i'd like to chat with a representative", "Let me talk to your manager",
])
def test_human_request_phrases_match(text):
    assert _WANTS_HUMAN.search(text)


@pytest.mark.parametrize("text", [
    "how long does shipping take?", "my order was handled by a shipping agent", "what is your return policy",
    "I am a person who ordered a jacket", "where is order ACM-1001",
])
def test_ordinary_phrases_do_not_trigger_handoff(text):
    assert not _WANTS_HUMAN.search(text)
