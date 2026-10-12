"""Eval runner / release gate.

  python -m evals.run                                   # fast, no-API suites (CI default)
  python -m evals.run --llm anthropic --trials 3        # full: also behavior, model-in-loop security, judge calibration
  python -m evals.run --llm anthropic --require-ready   # exit 1 unless EVERY gate + manual item is satisfied

Exit code: 1 if any automated gate FAILS (always), or if --require-ready and the verdict is not READY.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import report
from .harness import BACKEND
from .judge import Judge
from .suites import behavior, calibration, resilience, retrieval, security, security_llm, smoke

SUITES = {
    "retrieval": lambda c: retrieval.run({**c, "retrieval_file": "retrieval.jsonl"}),
    "retrieval_holdout": lambda c: retrieval.run({**c, "retrieval_file": "retrieval_holdout.jsonl"}),
    "security": security.run,
    "resilience": resilience.run,
    "smoke": smoke.run,
    "security_llm": security_llm.run,
    "behavior": behavior.run,
    "calibration": calibration.run,
}
FAST = ["retrieval", "retrieval_holdout", "security", "resilience", "smoke"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="evals.run")
    ap.add_argument("--suites", default="auto", help=f"comma list or 'auto' (fast suites, plus LLM suites when --llm anthropic). choices: {','.join(SUITES)}")
    ap.add_argument("--llm", choices=["none", "demo", "anthropic"], default="none")
    ap.add_argument("--model", default=None, help="model under test (default $ANTHROPIC_MODEL or claude-opus-5-5)")
    ap.add_argument("--judge-model", default=None)
    ap.add_argument("--trials", type=int, default=3)
    ap.add_argument("--tenant", default="acme")
    ap.add_argument("--only", default=None, help="behavior: scenario id substring or category")
    ap.add_argument("--out", default=str(BACKEND / "evals" / "reports"))
    ap.add_argument("--coverage-json", default=str(BACKEND / "coverage.json"))
    ap.add_argument("--require-ready", action="store_true")
    a = ap.parse_args(argv)

    logging.disable(logging.CRITICAL)  # fault-injection suites make the app log errors on purpose
    if a.llm == "anthropic" and not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        print("--llm anthropic needs ANTHROPIC_API_KEY (or an authenticated profile).", file=sys.stderr)
        return 2
    wanted = (FAST + (["security_llm", "behavior", "calibration"] if a.llm in ("anthropic", "demo") else [])) if a.suites == "auto" else a.suites.split(",")
    judge = Judge(model=a.judge_model) if a.llm == "anthropic" else None
    model = a.model or os.environ.get("ANTHROPIC_MODEL", "claude-opus-5-5")
    ctx: dict[str, Any] = {"tenants_dir": BACKEND / "tenants", "datasets": BACKEND / "evals" / "datasets", "tenant": a.tenant, "llm": a.llm,
                           "trials": a.trials, "judge": judge, "model": model, "only": a.only}
    results: dict[str, Any] = {}
    for name in wanted:
        t0 = time.time()
        print(f"▶ {name} ...", flush=True)
        try:
            results[name] = SUITES[name](ctx)
        except Exception as e:  # a crashing suite is a failed release gate, not a silent skip
            traceback.print_exc()
            results[name] = {"status": "error", "reason": f"{type(e).__name__}: {e}"}
        results[name]["seconds"] = round(time.time() - t0, 1)
        r = results[name]
        print(f"  {'skipped: ' + r['reason'] if r.get('status') in ('skipped', 'error') else 'done'} ({r['seconds']}s)", flush=True)

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    thresholds = report.load_yaml(BACKEND / "evals" / "thresholds.yaml")
    manual = report.load_yaml(BACKEND / "evals" / "readiness_manual.yaml")["items"]
    external = report.load_external(out, Path(a.coverage_json))
    gates = report.evaluate_gates(thresholds, results, external, a.llm)
    v = report.verdict(gates, manual)
    meta = {"when": datetime.now(timezone.utc).isoformat(timespec="seconds"), "tenant": a.tenant, "model": model if a.llm == "anthropic" else f"({a.llm})",
            "trials": a.trials, "llm": a.llm}
    for r in results.values():
        r.pop("raw", None)
    (out / "report.json").write_text(json.dumps({"meta": meta, "verdict": v, "gates": gates, "suites": results}, indent=1, default=str))
    (out / "report.md").write_text(report.render_md(meta, gates, v, results, manual))

    print("\n" + "=" * 78)
    for g in gates:
        print(f"{g['status']:8} {g['id']:5} {g['metric']:46} {report.fmt(g['actual']):>8}  ({g['rule']})")
    print("=" * 78)
    print(f"VERDICT: {'READY' if v['ready'] else 'NOT READY'}  pass={v['pass']} fail={v['fail']} skipped={v['skipped']} missing={v['missing']} invalid={v['invalid']} manual_open={v['manual_open']}/{v['manual_total']}")
    print(f"Report: {out / 'report.md'}")
    if v["fail"]:
        return 1
    return 1 if (a.require_ready and not v["ready"]) else 0


if __name__ == "__main__":
    sys.exit(main())
