"""The shipped fictional hospital demo tenant: knowledge answers, per-patient appointment isolation, 000 guidance."""
import os
import shutil

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.llm.demo import DemoLLM
from app.main import create_app

from conftest import ROOT, chat, user_token

WB_SECRET = "wattlebay-test-secret-with-enough-length-12"


@pytest.fixture
def wb(tmp_path, monkeypatch):
    monkeypatch.setitem(os.environ, "WATTLEBAY_USER_TOKEN_SECRET", WB_SECRET)
    monkeypatch.setitem(os.environ, "WATTLEBAY_AGENT_KEY_SAM", "wb-test-sam")
    tdir = tmp_path / "tenants"
    shutil.copytree(ROOT / "tenants" / "wattlebay", tdir / "wattlebay")
    s = Settings(env="dev", database_url=f"sqlite+aiosqlite:///{tmp_path}/wb.db", tenants_dir=tdir,
                 server_secret="server-secret-for-tests-0123456789abcdef", admin_api_key="admin-key", run_outbox_worker=False)
    with TestClient(create_app(s, DemoLLM())) as c:
        yield c


def session(c: TestClient, user: str | None) -> str:
    body = {"public_key": "pk_wattlebay_demo"}
    if user:
        body["user_token"] = user_token(user, secret=WB_SECRET)
    r = c.post("/v1/widget/sessions", json=body)
    assert r.status_code == 200, r.text
    return r.json()["token"]


def replies(c: TestClient, tok: str, text: str) -> str:
    return " ".join(m["content"] for m in chat(c, tok, text)["messages"] if m["role"] == "assistant")


def test_knowledge_base_answers_with_a_source(wb):
    out = chat(wb, session(wb, None), "What are the visiting hours?")["messages"][-1]
    assert "10:00am to 8:00pm" in out["content"]
    assert out["meta"]["sources"]


def test_patient_sees_own_appointment_in_appointment_words(wb):
    text = replies(wb, session(wb, "peter"), "Where is appointment APT-1001?")
    assert "Appointment APT-1001 is **Confirmed**" in text and "20/10/2026" in text
    assert "delivery" not in text.lower() and "order" not in text.lower()


def test_another_patient_gets_the_same_answer_as_for_a_missing_appointment(wb):
    mary = replies(wb, session(wb, "mary"), "Where is appointment APT-1001?")
    missing = replies(wb, session(wb, "mary"), "Where is appointment APT-9999?")
    assert "APT-1001" in mary and "Confirmed" not in mary and "Cardiology" not in mary
    assert mary.replace("APT-1001", "X") == missing.replace("APT-9999", "X")  # no oracle


def test_guest_cannot_look_up_appointments(wb):
    assert "Confirmed" not in replies(wb, session(wb, None), "Where is appointment APT-1001?")


def test_system_prompt_forbids_medical_advice_and_mentions_000(wb):
    rt = wb.app.state.sp.registry.by_id("wattlebay")
    from app.agent.prompts import build_system_prompt
    static, _ = build_system_prompt(rt, None)
    assert "NOT a clinician" in static and "000" in static and "Appointments:" in static
