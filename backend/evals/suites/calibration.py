"""Judge calibration: does the LLM judge agree with human labels? Gate BEFORE trusting any judged metric."""
from __future__ import annotations

from typing import Any

import yaml

from ..judge import Judge
from ..stats import cohen_kappa, rate


def run(ctx: dict[str, Any]) -> dict[str, Any]:
    judge: Judge | None = ctx.get("judge")
    if judge is None:
        return {"status": "skipped", "reason": "needs a judge model: run with --llm anthropic (ANTHROPIC_API_KEY) or inject a Judge"}
    data = yaml.safe_load((ctx["datasets"] / ctx["tenant"] / "judge_calibration.yaml").read_text())["examples"]
    human, model, rows = [], [], []
    for ex in data:
        g = judge.grade("grounded", conversation=f"Customer: {ex['q']}", sources=ex["sources"], reply=ex["reply"])
        human.append(ex["label"] == "pass"); model.append(g["pass"])
        rows.append({"id": ex["id"], "human": ex["label"], "judge": "pass" if g["pass"] else "fail", "agree": (ex["label"] == "pass") == g["pass"], "reason": g["reason"][:160]})
    fp = sum(m and not h for h, m in zip(human, model))   # judge passes a bad answer: the dangerous direction
    fn = sum(h and not m for h, m in zip(human, model))
    neg = sum(not h for h in human) or 1
    return {
        "metrics": {
            "agreement": rate(sum(h == m for h, m in zip(human, model)), len(human)),
            "cohen_kappa": {"value": cohen_kappa(human, model), "n": len(human)},
            "false_pass_rate": {"value": fp / neg, "n": neg},  # P(judge passes | human says fail)
            "false_fail_count": {"value": fn, "n": len(human)},
        },
        "failures": [r for r in rows if not r["agree"]],
        "info": {"examples": len(data), "judge_model": judge.model},
    }
