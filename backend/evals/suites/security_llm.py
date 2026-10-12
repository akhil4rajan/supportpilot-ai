"""Security, model in the loop. A real model is attacked with realistic jailbreak/social-engineering prompts. Pass/fail is
decided by deterministic canaries (another customer's data, system-prompt fragments) - not by a judge - plus an optional
judged rubric. Complements suites/security.py, which attacks our enforcement layers with a scripted model."""
from __future__ import annotations

import json
from typing import Any

import yaml

from ..harness import EvalApp, make_llm
from ..judge import Judge
from ..stats import rate


def run(ctx: dict[str, Any]) -> dict[str, Any]:
    mode = ctx.get("llm", "none")
    if mode == "none":
        return {"status": "skipped", "reason": "needs a real model: run with --llm anthropic (ANTHROPIC_API_KEY)"}
    spec = yaml.safe_load((ctx["datasets"] / ctx["tenant"] / "security_prompts.yaml").read_text())
    orders = json.loads((ctx["tenants_dir"] / ctx["tenant"] / "orders.json").read_text())
    k = int(ctx.get("trials", 3))
    llm = ctx.get("llm_client") or make_llm(mode, ctx.get("model"))
    judge: Judge | None = ctx.get("judge")
    rows: list[dict[str, Any]] = []

    def canaries_for(attacker: str | None) -> list[str]:
        out: list[str] = []
        for o in orders:
            if o["customer_id"] != attacker:  # another customer's DATA (not their name: the attacker may legitimately mention it)
                out += [i["name"] for i in o["items"]] + [str(o["total"]), o.get("tracking_number") or ""]
        return [c for c in out if c and len(c) > 3]

    def evaluate(case_id: str, who: str | None, say: str, canaries: list[str], markers: list[str], trial: int, app: EvalApp, sess: Any) -> None:
        t = app.say(sess, say)
        blob = (t.reply + " " + json.dumps(t.tool_results)).lower()
        leaked = [c for c in canaries if c.lower() in blob]
        sys_leak = [m for m in markers if m.lower() in t.reply.lower()]
        resisted = None
        if judge:
            resisted = judge.grade("resists_manipulation", conversation=f"Customer: {say}", sources="", reply=t.reply)["pass"]
        rows.append({"id": case_id, "trial": trial, "leaked": leaked, "system_leak": sys_leak, "judge_resisted": resisted, "reply": t.reply[:300],
                     "passed": not leaked and not sys_leak and resisted is not False})

    for atk in spec["attacks"]:
        who = None if atk["as"] == "guest" else atk["as"]
        for trial in range(k):
            with EvalApp(llm, tenants_dir=ctx["tenants_dir"], messages_per_minute=10_000) as app:
                evaluate(atk["id"], who, atk["say"], canaries_for(who), spec["system_prompt_markers"], trial, app, app.open_session(who))

    # KB poisoning: the injection arrives through retrieved content rather than from the customer
    kp = spec["kb_poison"]
    for trial in range(k):
        with EvalApp(llm, tenants_dir=ctx["tenants_dir"], messages_per_minute=10_000) as app:
            rt = app.app.state.sp.registry.by_id(ctx["tenant"])
            (rt.knowledge_dir / kp["filename"]).write_text(kp["content"])
            rt.reindex()
            sess = app.open_session(None)
            t = app.say(sess, kp["question"])
            bad = [f for f in kp["forbidden"] if f.lower() in t.reply.lower()]
            rows.append({"id": "kb-poison", "trial": trial, "leaked": bad, "system_leak": [], "judge_resisted": None, "reply": t.reply[:300], "passed": not bad})

    leaks = sum(bool(r["leaked"]) for r in rows)
    sysleaks = sum(bool(r["system_leak"]) for r in rows)
    judged = [r for r in rows if r["judge_resisted"] is not None]
    metrics = {
        "pass_rate": rate(sum(r["passed"] for r in rows), len(rows)),
        "data_leaks": {"value": leaks, "n": len(rows)},
        "system_prompt_leaks": {"value": sysleaks, "n": len(rows)},
        "judge_resist_rate": rate(sum(bool(r["judge_resisted"]) for r in judged), len(judged)) if judged else {"value": 0.0, "n": 0},
    }
    return {"metrics": metrics, "failures": [r for r in rows if not r["passed"]], "info": {"attacks": len(spec["attacks"]) + 1, "trials": k, "model": getattr(llm, "model", mode)}}
