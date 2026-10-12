"""Render evals/reports/load*.json as markdown (for docs/SCALING.md).  python -m loadtest.render"""
from __future__ import annotations

import json
from pathlib import Path

REPORTS = Path(__file__).resolve().parent.parent / "evals" / "reports"


def render() -> str:
    out: list[str] = []
    ramp = REPORTS / "load.json"
    if ramp.exists():
        r = json.loads(ramp.read_text())
        req = r["requirement"]
        out += [f"**Ramp (open-loop, steady-state window per step)** — measured {r['when']}  \n_{r['note']}_", "",
                "| Offered conv/s | Steady turns/s | Errors | p50 / p95 / p99 total (ms) | p50 / p95 / p99 platform overhead (ms) | Verdict |", "|---|---|---|---|---|---|"]
        for s in r["steps"]:
            ok = s["error_rate"] <= 0.001 and s["overhead_ms"]["p95"] <= 500
            lat, ovh = s["latency_ms"], s["overhead_ms"]
            out.append(f"| {s['conv_per_s_offered']:.1f} | {s['turns_per_s_steady']} | {s['errors']} ({s['error_rate'] * 100:.2f} %) | {lat['p50']} / {lat['p95']} / {lat['p99']} | {ovh['p50']} / {ovh['p95']} / {ovh['p99']} | {'✅ within gates' if ok else '❌ saturated'} |")
        g = r["gates"]
        out += ["", f"* Highest step within gates: **{r['sustained_turns_per_s_per_process']} turns/s per process**; the design point needs **{req['design_turns_per_s']} turns/s** "
                    f"→ capacity ratio **{g['capacity_ratio']}** for one process.",
                f"* `replicas needed at design load ≈ ceil({req['design_turns_per_s']} / {r['sustained_turns_per_s_per_process']}) = {-(-req['design_turns_per_s'] // max(r['sustained_turns_per_s_per_process'], 0.1)):.0f}` (before N+1 headroom; assumes linear scaling until the shared database saturates — to be confirmed on staging).", ""]
    ws = REPORTS / "load_ws.json"
    if ws.exists():
        w = json.loads(ws.read_text())
        d = w["delivery_ms"]
        out += ["**WebSocket soak**", "", "| Open sockets | Delivered | p50 / p95 / p99 / max reply→socket (ms) |", "|---|---|---|",
                f"| {w['sockets_open']} / {w['sockets_requested']} | {w['delivered']} ({w['delivery_rate'] * 100:.1f} %) | {d['p50']} / {d['p95']} / {d['p99']} / {d['max']} |", ""]
    return "\n".join(out)


if __name__ == "__main__":
    print(render())
