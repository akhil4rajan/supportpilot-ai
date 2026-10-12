"""Post-deploy smoke test. Works against ANY running deployment (staging/prod canary) over HTTP, or in-process.

Env (remote mode):  SMOKE_BASE_URL, SMOKE_PUBLIC_KEY, SMOKE_USER_SECRET (shared token secret of the smoke tenant),
                    SMOKE_USER_ID + SMOKE_ORDER_ID (a known customer/order), SMOKE_TENANT_ID, SMOKE_AGENT_KEY, SMOKE_ORIGIN.
Never run against real customer accounts: use a dedicated smoke tenant/customer."""
from __future__ import annotations

import os
from typing import Any

from ..harness import aud_token
from ..stats import rate


def run_against(client: Any, cfg: dict[str, str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    hdr = {"origin": cfg["origin"]} if cfg.get("origin") else {}

    def check(cid: str, ok: bool, detail: str = "") -> None:
        out.append({"id": cid, "passed": bool(ok), "detail": detail})

    check("healthz", client.get("/healthz").status_code == 200)
    r = client.get("/readyz")
    check("readyz", r.status_code == 200, r.text[:120])
    r = client.get("/v1/widget/config", params={"public_key": cfg["public_key"]}, headers=hdr)
    check("widget_config", r.status_code == 200 and "branding" in r.json(), f"HTTP {r.status_code}")

    r = client.post("/v1/widget/sessions", json={"public_key": cfg["public_key"]}, headers=hdr)
    ok = r.status_code == 200
    check("guest_session", ok)
    if ok:
        tok = r.json()["token"]
        r = client.post("/v1/widget/messages", json={"content": cfg.get("faq_question", "How long do refunds take?")}, headers={"Authorization": f"Bearer {tok}"})
        reply = " ".join(m["content"] for m in r.json().get("messages", []) if m["role"] == "assistant") if r.status_code == 200 else ""
        check("guest_faq_answer", r.status_code == 200 and len(reply) > 20, reply[:100])

    ut = aud_token(cfg["user_secret"], cfg["user_id"], name="Smoke", email="smoke@example.com")
    r = client.post("/v1/widget/sessions", json={"public_key": cfg["public_key"], "user_token": ut}, headers=hdr)
    check("signed_in_session", r.status_code == 200, f"HTTP {r.status_code}")
    if r.status_code == 200:
        tok = r.json()["token"]
        r = client.post("/v1/widget/messages", json={"content": f"What is the status of order {cfg['order_id']}?"}, headers={"Authorization": f"Bearer {tok}"})
        check("owner_order_lookup", r.status_code == 200 and cfg["order_status"].lower() in str(r.json()).lower(), f"HTTP {r.status_code}")
        # explicit hand-off + agent round trip
        r = client.post("/v1/widget/handoff", headers={"Authorization": f"Bearer {tok}"})
        cid = r.json()["conversation"]["id"] if r.status_code == 200 else ""
        check("handoff_button", r.status_code == 200 and r.json()["conversation"]["status"] == "waiting_human")
        a = client.post("/v1/agent/login", json={"tenant_id": cfg["tenant_id"], "api_key": cfg["agent_key"]})
        check("agent_login", a.status_code == 200)
        if a.status_code == 200 and cid:
            ah = {"Authorization": f"Bearer {a.json()['token']}"}
            q = client.get("/v1/agent/conversations", headers=ah).json()["conversations"]
            check("agent_sees_queue", any(c["id"] == cid for c in q))
            check("agent_claim_reply", client.post(f"/v1/agent/conversations/{cid}/claim", headers=ah).status_code == 200 and
                  client.post(f"/v1/agent/conversations/{cid}/messages", json={"content": "smoke test reply"}, headers=ah).status_code == 200)
            seen = client.get("/v1/widget/conversation", headers={"Authorization": f"Bearer {tok}"}).json()["messages"]
            check("customer_receives_agent_reply", any(m["role"] == "agent" for m in seen))
            check("agent_resolve", client.post(f"/v1/agent/conversations/{cid}/resolve", headers=ah).status_code == 200)
    # negative controls: the deployment must refuse a forged identity
    bad = aud_token("not-the-secret-not-the-secret-123456", cfg["user_id"])
    check("forged_token_rejected", client.post("/v1/widget/sessions", json={"public_key": cfg["public_key"], "user_token": bad}, headers=hdr).status_code == 401)
    return out


def run(ctx: dict[str, Any]) -> dict[str, Any]:
    if os.environ.get("SMOKE_BASE_URL"):
        import httpx
        cfg = {k.lower().removeprefix("smoke_"): v for k, v in os.environ.items() if k.startswith("SMOKE_")}
        cfg.update(public_key=os.environ["SMOKE_PUBLIC_KEY"], user_secret=os.environ["SMOKE_USER_SECRET"], user_id=os.environ["SMOKE_USER_ID"],
                   order_id=os.environ["SMOKE_ORDER_ID"], order_status=os.environ.get("SMOKE_ORDER_STATUS", ""), tenant_id=os.environ["SMOKE_TENANT_ID"], agent_key=os.environ["SMOKE_AGENT_KEY"])
        with httpx.Client(base_url=os.environ["SMOKE_BASE_URL"], timeout=60) as c:
            results = run_against(c, cfg)
        mode = "remote"
    else:  # in-process self-check of the suite itself, with the demo model
        from ..harness import EvalApp
        from app.llm.demo import DemoLLM
        with EvalApp(DemoLLM(), tenants_dir=ctx["tenants_dir"]) as app:
            cfg = dict(public_key="pk_acme_demo", user_secret=os.environ["ACME_USER_TOKEN_SECRET"], user_id="alice", order_id="ACM-1001", order_status="Shipped",
                       tenant_id="acme", agent_key=os.environ["ACME_AGENT_KEY_SAM"], faq_question="How long do refunds take?")
            results = run_against(app.client, cfg)
        mode = "in-process"
    passed = sum(r["passed"] for r in results)
    return {"metrics": {"pass_rate": rate(passed, len(results))}, "failures": [r for r in results if not r["passed"]], "info": {"mode": mode, "cases": len(results)}}
