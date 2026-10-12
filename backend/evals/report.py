"""Gate evaluation and report rendering."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml


def lookup(results: dict[str, Any], external: dict[str, Any], metric: str) -> tuple[str, dict[str, Any] | None, str]:
    """-> (state, metric dict, note). state: ok | skipped | missing"""
    suite, _, name = metric.partition(".")
    if suite in external:
        m = external[suite].get(name)
        return ("ok", m, "") if m is not None else ("missing", None, f"{suite} report has no '{name}'")
    r = results.get(suite)
    if r is None:
        return "missing", None, f"suite '{suite}' was not run"
    if r.get("status") == "skipped":
        return "skipped", None, r.get("reason", "skipped")
    if r.get("status") == "error":
        return "missing", None, "suite crashed: " + r.get("reason", "")
    m = r.get("metrics", {}).get(name)
    return ("ok", m, "") if m is not None else ("missing", None, f"suite '{suite}' has no metric '{name}'")


def evaluate_gates(thresholds: dict[str, Any], results: dict[str, Any], external: dict[str, Any], llm_mode: str) -> list[dict[str, Any]]:
    rows = []
    for g in thresholds["gates"]:
        state, m, note = lookup(results, external, g["metric"])
        row = {"id": g["id"], "name": g["name"], "metric": g["metric"], "zero_tolerance": g.get("zero_tolerance", False),
               "rule": " and ".join(x for x in (f">= {g['min']}" if "min" in g else "", f"<= {g['max']}" if "max" in g else "",
                                                f"CI-low >= {g['ci_low_min']}" if "ci_low_min" in g else "") if x),
               "actual": None, "ci_low": None, "n": None, "status": "", "note": note}
        if g.get("requires_llm") and llm_mode == "demo":
            row.update(status="INVALID", note="demo model output is not valid evidence")
        elif state == "skipped" or (state == "missing" and g.get("requires_llm") and llm_mode == "none"):
            row.update(status="SKIPPED", note=note if state == "skipped" else "needs a real model: run with --llm anthropic")
        elif state == "missing":
            row.update(status="MISSING")
        else:
            v = m["value"]
            row.update(actual=v, ci_low=m.get("ci_low"), n=m.get("n"))
            ok = (v >= g["min"] if "min" in g else True) and (v <= g["max"] if "max" in g else True)
            if ok and "ci_low_min" in g:
                ok = (m.get("ci_low") or 0) >= g["ci_low_min"]
                if not ok:
                    row["note"] = "point estimate passes but the 95% lower bound is below the floor: sample too small/noisy to trust"
            row["status"] = "PASS" if ok else "FAIL"
        rows.append(row)
    return rows


def verdict(gates: list[dict[str, Any]], manual: list[dict[str, Any]]) -> dict[str, Any]:
    count = lambda s: sum(g["status"] == s for g in gates)  # noqa: E731
    open_manual = [m for m in manual if m.get("status") != "done"]
    failing = [g for g in gates if g["status"] == "FAIL"]
    ready = not failing and count("PASS") == len(gates) and not open_manual
    return {"ready": ready, "pass": count("PASS"), "fail": count("FAIL"), "skipped": count("SKIPPED"), "missing": count("MISSING"),
            "invalid": count("INVALID"), "manual_open": len(open_manual), "manual_total": len(manual),
            "zero_tolerance_failures": [g["id"] for g in failing if g["zero_tolerance"]]}


def fmt(v: Any) -> str:
    if v is None:
        return "-"
    return f"{v:.3f}" if isinstance(v, float) and abs(v) < 1000 and v != int(v) else str(round(v, 2) if isinstance(v, float) else v)


def render_md(meta: dict[str, Any], gates: list[dict[str, Any]], v: dict[str, Any], results: dict[str, Any], manual: list[dict[str, Any]]) -> str:
    banner = "## ✅ READY FOR PRODUCTION" if v["ready"] else "## ⛔ NOT READY FOR PRODUCTION"
    lines = [f"# SupportPilot readiness report", "", banner, "",
             f"- Generated: `{meta['when']}`  ·  tenant `{meta['tenant']}`  ·  model-under-test `{meta['model']}`  ·  trials `{meta['trials']}`  ·  llm mode `{meta['llm']}`",
             f"- Automated gates: **{v['pass']} pass**, **{v['fail']} fail**, {v['skipped']} skipped, {v['missing']} missing, {v['invalid']} invalid  ·  manual items open: **{v['manual_open']}/{v['manual_total']}**"]
    if v["zero_tolerance_failures"]:
        lines.append(f"- 🚨 **Zero-tolerance failures: {', '.join(v['zero_tolerance_failures'])}**")
    lines += ["", "READY requires every automated gate to PASS (skipped/missing/invalid do not count as pass) and every manual item to be `done`.", "",
              "### Automated gates", "", "| Gate | Requirement | Rule | Actual | 95% CI low | n | Status |", "|---|---|---|---|---|---|---|"]
    icon = {"PASS": "✅ PASS", "FAIL": "❌ FAIL", "SKIPPED": "⏭️ SKIPPED", "MISSING": "❔ MISSING", "INVALID": "🚫 INVALID"}
    for g in gates:
        lines.append(f"| {g['id']}{' 🔒' if g['zero_tolerance'] else ''} | {g['name']} | `{g['metric']}` {g['rule']} | {fmt(g['actual'])} | {fmt(g['ci_low'])} | {fmt(g['n'])} | {icon[g['status']]} |")
    notes = [g for g in gates if g["note"]]
    if notes:
        lines += ["", "<details><summary>Notes on gates</summary>", ""] + [f"- **{g['id']}**: {g['note']}" for g in notes] + ["", "</details>"]
    for name, r in results.items():
        lines += ["", f"### Suite: {name}", ""]
        if r.get("status") == "skipped":
            lines.append(f"_Skipped: {r['reason']}_"); continue
        if r.get("status") == "error":
            lines.append(f"**Suite crashed:** `{r['reason']}`"); continue
        lines += ["| Metric | Value | 95% CI | n |", "|---|---|---|---|"]
        for k, m in r["metrics"].items():
            ci = f"{m['ci_low']:.2f}-{m['ci_high']:.2f}" if "ci_low" in m else "-"
            lines.append(f"| {k} | {fmt(m['value'])} | {ci} | {fmt(m.get('n'))} |")
        if r.get("info"):
            lines += ["", "```json", json.dumps(r["info"], indent=1, default=str), "```"]
        if r.get("failures"):
            lines += ["", f"<details><summary>{len(r['failures'])} failing case(s)</summary>", "", "```json", json.dumps(r["failures"][:40], indent=1, default=str)[:6000], "```", "</details>"]
    lines += ["", "### Manual readiness items", "", "| ID | Area | Item | Status | Owner | Evidence |", "|---|---|---|---|---|---|"]
    for m in manual:
        lines.append(f"| {m['id']} | {m['area']} | {m['item']} | {'✅ done' if m.get('status') == 'done' else '⬜ open'} | {m.get('owner') or '-'} | {m.get('evidence') or '-'} |")
    return "\n".join(lines) + "\n"


def load_external(reports_dir: Path, coverage_json: Path | None) -> dict[str, Any]:
    ext: dict[str, Any] = {}
    lp = reports_dir / "load.json"
    if lp.exists():
        ext["load"] = {k: {"value": v} for k, v in json.loads(lp.read_text()).get("gates", {}).items()}
    if coverage_json and coverage_json.exists():
        ext["coverage"] = {"percent": {"value": json.loads(coverage_json.read_text())["totals"]["percent_covered"]}}
    return ext


def load_yaml(p: Path) -> Any:
    return yaml.safe_load(p.read_text())
