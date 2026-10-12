"""Behavior suite: scripted customer conversations against the real model + our real tools and prompt.

Scoring: a scenario-trial passes iff every check in every turn passes. Each scenario runs `k` trials because model
output varies; we report pooled pass rates with Wilson intervals, and CONSISTENCY (share of scenarios that pass ALL
k trials) - a bot that passes 4/5 times is not production-ready for a policy question."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from ..harness import EvalApp, Turn, make_llm
from ..judge import Judge, sources_text
from ..stats import rate


def check_turn(t: Turn, expect: dict[str, Any], judge: Judge | None, history: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    low = t.reply.lower()

    def add(name: str, ok: bool, detail: str = "") -> None:
        out.append({"check": name, "passed": bool(ok), "detail": detail})

    for k in ("tools_any", "tools_all", "tools_none", "contains_any", "contains_all", "not_contains", "regex_not", "status", "tickets", "sources_any", "judge"):
        if k not in expect:
            continue
        v = expect[k]
        if k == "tools_any":
            add(k, any(x in t.tools for x in v), f"used={t.tools}")
        elif k == "tools_all":
            add(k, all(x in t.tools for x in v), f"used={t.tools}")
        elif k == "tools_none":
            add(k, not any(x in t.tools for x in v), f"used={t.tools}")
        elif k == "contains_any":
            add(k, any(x.lower() in low for x in v), f"want one of {v}")
        elif k == "contains_all":
            add(k, all(x.lower() in low for x in v), f"want all of {v}")
        elif k == "not_contains":
            bad = [x for x in v if x.lower() in low]
            add(k, not bad, f"found forbidden {bad}")
        elif k == "regex_not":
            bad = [p for p in v if re.search(p, t.reply)]
            add(k, not bad, f"matched {bad}")
        elif k == "status":
            add(k, t.status == v, f"status={t.status}")
        elif k == "tickets":
            add(k, len(t.tickets) == v, f"tickets={len(t.tickets)}")
        elif k == "sources_any":
            add(k, any(s["source"] in v for s in t.sources), f"sources={[s['source'] for s in t.sources]}")
        elif k == "judge":
            for rubric in v:
                if judge is None:
                    add(f"judge:{rubric}", True, "SKIPPED (no judge)")
                    out[-1]["skipped"] = True
                    continue
                g = judge.grade(rubric, conversation=history, sources=sources_text(t.tool_results), reply=t.reply)
                add(f"judge:{rubric}", g["pass"], g["reason"][:200] + (f" unsupported={g['unsupported']}" if g["unsupported"] else ""))
    return out


def run(ctx: dict[str, Any]) -> dict[str, Any]:
    mode = ctx.get("llm", "none")
    if mode == "none":
        return {"status": "skipped", "reason": "behavior evals need a real model: run with --llm anthropic (ANTHROPIC_API_KEY)"}
    cases = yaml.safe_load((ctx["datasets"] / ctx["tenant"] / "behavior.yaml").read_text())["scenarios"]
    only = ctx.get("only")
    if only:
        cases = [c for c in cases if only in c["id"] or only == c["category"]]
    k = int(ctx.get("trials", 3))
    judge = ctx.get("judge")
    llm = ctx.get("llm_client") or make_llm(mode, ctx.get("model"))

    per_trial: list[dict[str, Any]] = []
    for sc in cases:
        for trial in range(k):
            with EvalApp(llm, tenants_dir=ctx["tenants_dir"], messages_per_minute=10_000) as app:
                sess = app.open_session(None if sc["user"] == "guest" else sc["user"])
                checks: list[dict[str, Any]] = []
                history: list[str] = []
                final: Turn | None = None
                tin = tout = calls = 0
                for step in sc["turns"]:
                    t = app.say(sess, step["say"])
                    history += [f"Customer: {step['say']}", f"Assistant: {t.reply}"]
                    checks += check_turn(t, step.get("expect", {}), judge, "\n".join(history))
                    final = t
                    tin += t.tokens_in; tout += t.tokens_out; calls += t.llm_calls
                per_trial.append({"id": sc["id"], "category": sc["category"], "trial": trial, "passed": all(c["passed"] for c in checks),
                                  "checks": checks, "final_status": final.status if final else None, "must_handoff": sc.get("must_handoff", False),
                                  "reply": final.reply if final else "", "tokens_in": tin, "tokens_out": tout, "llm_calls": calls,
                                  "expects_bot": any(s.get("expect", {}).get("status") == "bot" for s in sc["turns"])})

    def pooled(rows: list[dict[str, Any]]) -> dict[str, Any]:
        return rate(sum(r["passed"] for r in rows), len(rows))

    cats: dict[str, list[dict[str, Any]]] = {}
    for r in per_trial:
        cats.setdefault(r["category"], []).append(r)
    by_scn: dict[str, list[bool]] = {}
    for r in per_trial:
        by_scn.setdefault(r["id"], []).append(r["passed"])
    must = [r for r in per_trial if r["must_handoff"]]
    stay = [r for r in per_trial if r["expects_bot"]]
    judged = [c for r in per_trial for c in r["checks"] if c["check"] == "judge:grounded" and not c.get("skipped")]
    una = [r for r in cats.get("unanswerable", [])]
    metrics: dict[str, Any] = {
        "overall_pass_rate": pooled(per_trial),
        "consistency_all_trials_pass": rate(sum(all(v) for v in by_scn.values()), len(by_scn)),
        "handoff_recall": rate(sum(r["final_status"] == "waiting_human" for r in must), len(must)),
        "handoff_false_positive_rate": rate(sum(r["final_status"] == "waiting_human" for r in stay), len(stay)),
        "grounded_rate": rate(sum(c["passed"] for c in judged), len(judged)),
        "hallucination_rate": rate(sum(not r["passed"] for r in una), len(una)),
        "order_access_violations": {"value": sum(not r["passed"] for r in cats.get("order", []) if r["id"] in ("ord-foreign", "ord-foreign-social", "ord-guest")), "n": 0},
        "ticket_flow_pass_rate": pooled(cats.get("ticket", [])) if cats.get("ticket") else {"value": 0.0, "n": 0},
        "avg_llm_calls_per_scenario": {"value": sum(r["llm_calls"] for r in per_trial) / max(len(per_trial), 1)},
        "avg_input_tokens_per_scenario": {"value": sum(r["tokens_in"] for r in per_trial) / max(len(per_trial), 1)},
        "avg_output_tokens_per_scenario": {"value": sum(r["tokens_out"] for r in per_trial) / max(len(per_trial), 1)},
    }
    for c, rows in cats.items():
        metrics[f"cat_{c}_pass_rate"] = pooled(rows)
    fails = [{"id": r["id"], "trial": r["trial"], "failed_checks": [c for c in r["checks"] if not c["passed"]], "reply": r["reply"][:300]} for r in per_trial if not r["passed"]]
    return {"metrics": metrics, "failures": fails, "info": {"scenarios": len(cases), "trials": k, "model": getattr(llm, "model", mode), "judge": bool(judge)}, "raw": per_trial}
