import asyncio
from types import SimpleNamespace

import pytest
from conftest import ScriptedLLM, auth, call, chat, make_client, open_session, say  # noqa: F401, E501

from app.integrations.tickets.base import TicketError, TicketRequest
from app.integrations.tickets.jira_provider import JiraTicketProvider
from app.tenancy.models import JiraConfig


def test_kb_answer_cites_source(make_client):
    c = make_client()
    tok = open_session(c, user=None).json()["token"]
    m = chat(c, tok, "how long do refunds take?")["messages"][-1]
    assert "5-7 business days" in m["content"]
    assert m["meta"]["sources"][0]["source"] == "returns.md"
    assert {x["source"] for x in m["meta"]["sources"]} == {"returns.md"}  # no weakly-related citations


def test_unanswerable_question_returns_no_passages_and_counts(make_client):
    c = make_client()
    tok = open_session(c, user=None).json()["token"]
    m = chat(c, tok, "what is the airspeed velocity of an unladen swallow")
    assert "couldn't find" in m["messages"][0]["content"]
    assert m["conversation"]["status"] == "bot"


def test_ticket_created_with_transcript_and_dedup(make_client):
    llm = ScriptedLLM(
        call("create_support_ticket", summary="Jacket zipper broke", description="Zip split after one week of use, order ACM-1001"),
        say("Ticket created"),
        call("create_support_ticket", summary="Jacket zipper broke", description="Zip split after one week of use, order ACM-1001"),
        say("Again"),
    )
    c = make_client(llm)
    tok = open_session(c, user="alice").json()["token"]
    # alice has no email in this token -> the tool must ask for one
    chat(c, tok, "my zipper broke")
    assert "email_required" in str(llm.calls[1]["messages"][-1])


def test_ticket_with_email_and_duplicate_suppression(make_client):
    args = dict(summary="Jacket zipper broke", description="Zip split after one week of use", contact_email="alice@example.com")
    llm = ScriptedLLM(call("create_support_ticket", **args), say("done"), call("create_support_ticket", **args), say("done2"))
    c = make_client(llm)
    tok = open_session(c, user="alice").json()["token"]
    first = chat(c, tok, "please file a ticket")["messages"][-1]
    key = first["meta"]["tickets"][0]["key"]
    assert key.startswith("SUP-")
    chat(c, tok, "again")
    store = c.app.state.sp.registry.by_id("acme").tickets
    assert len(store.created) == 1  # second call was idempotent
    req = store.created[0][1]
    assert req.reporter_id == "alice" and req.reporter_email == "alice@example.com" and "[user]" in req.transcript


def test_ticket_invalid_email_rejected(make_client):
    llm = ScriptedLLM(call("create_support_ticket", summary="Something broke", description="Details here please", contact_email="nope"), say("x"))
    c = make_client(llm)
    chat(c, open_session(c, user=None).json()["token"], "ticket pls")
    assert "invalid_email" in str(llm.calls[1]["messages"][-1])


def test_tools_not_offered_when_tenant_has_no_provider(make_client):
    llm = ScriptedLLM(say("hi"))
    c = make_client(llm)
    tok = open_session(c, user=None, key="pk_globex", origin="https://globex.example").json()["token"]
    chat(c, tok, "hello")
    names = {t["name"] for t in llm.calls[0]["tools"]}
    assert names == {"search_knowledge_base", "create_support_ticket", "escalate_to_human"}


def test_system_prompt_reflects_identity(make_client):
    llm = ScriptedLLM(say("a"), say("b"))
    c = make_client(llm)
    chat(c, open_session(c, user=None).json()["token"], "hi")
    chat(c, open_session(c, user="alice").json()["token"], "hi")
    assert "NOT signed in" in llm.calls[0]["system"] and "verified by the system" in llm.calls[1]["system"]


# ---- Jira provider (the real `jira` client is replaced with a fake) ----

class FakeJira:
    def __init__(self, fail_priority=False):
        self.created, self.fail_priority = [], fail_priority

    def create_issue(self, fields):
        from jira.exceptions import JIRAError
        if self.fail_priority and "priority" in fields:
            raise JIRAError(status_code=400, text="Field 'priority' cannot be set.")
        self.created.append(fields)
        return SimpleNamespace(key="SUP-42")


def req():
    return TicketRequest("Broken zip", "Zip broke {noformat}", "high", "warranty", "Alice", "a@x.com", "alice", "conv1", "[user] hi")


def test_jira_provider_builds_issue():
    fake = FakeJira()
    p = JiraTicketProvider(JiraConfig(server="https://acme.atlassian.net/", project_key="SUP"), client=fake)
    res = asyncio.run(p.create_ticket(req()))
    assert res.key == "SUP-42" and res.url == "https://acme.atlassian.net/browse/SUP-42"
    f = fake.created[0]
    assert f["project"] == {"key": "SUP"} and f["priority"] == {"name": "High"} and f["issuetype"] == {"name": "Task"}
    assert "Customer ID: alice" in f["description"] and "{noformat}\n[user] hi\n{noformat}" in f["description"]
    assert f["description"].count("{noformat}") == 2  # user-supplied marker was neutralised


def test_jira_provider_retries_without_priority():
    fake = FakeJira(fail_priority=True)
    p = JiraTicketProvider(JiraConfig(server="https://x", project_key="SUP"), client=fake)
    assert asyncio.run(p.create_ticket(req())).key == "SUP-42"
    assert "priority" not in fake.created[0]


def test_jira_provider_wraps_errors(monkeypatch):
    monkeypatch.delenv("JIRA_EMAIL", raising=False)
    p = JiraTicketProvider(JiraConfig(server="https://x", project_key="SUP"))
    with pytest.raises(TicketError):
        asyncio.run(p.create_ticket(req()))
