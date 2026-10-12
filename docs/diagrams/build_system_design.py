"""Generates system-design.svg (target production architecture). Run: python docs/diagrams/build_system_design.py
The box() helper warns when a text line is estimated to overflow its box, so edits stay legible."""
from __future__ import annotations

from html import escape
from pathlib import Path

W, H = 1700, 1090
out: list[str] = []
warnings: list[str] = []

PALETTE = {  # fill, stroke, text
    "client": ("#eef2ff", "#6366f1", "#1e1b4b"),
    "edge": ("#f1f5f9", "#64748b", "#0f172a"),
    "app": ("#ecfdf5", "#10b981", "#064e3b"),
    "data": ("#fff7ed", "#f97316", "#7c2d12"),
    "ext": ("#fdf2f8", "#ec4899", "#831843"),
    "obs": ("#f0f9ff", "#0ea5e9", "#0c4a6e"),
    "note": ("#fefce8", "#ca8a04", "#713f12"),
}
CHAR_W = {"title": 8.4, "line": 6.5}


def box(x, y, w, h, title, lines=(), kind="app", dashed=False):
    f, s, t = PALETTE[kind]
    dash = ' stroke-dasharray="6 4"' if dashed else ""
    out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="{f}" stroke="{s}" stroke-width="1.6"{dash}/>')
    out.append(f'<text x="{x + 12}" y="{y + 24}" font-size="14.5" font-weight="700" fill="{t}">{escape(title)}</text>')
    if len(title) * CHAR_W["title"] > w - 20:
        warnings.append(f"TITLE overflow: {title!r}")
    for i, ln in enumerate(lines):
        out.append(f'<text x="{x + 12}" y="{y + 24 + 19 * (i + 1)}" font-size="12" fill="{t}" opacity=".88">{escape(ln)}</text>')
        if len(ln) * CHAR_W["line"] > w - 20:
            warnings.append(f"LINE overflow ({int(len(ln) * CHAR_W['line'])}>{w - 20}): {ln!r}")
    if 24 + 19 * len(lines) + 12 > h:
        warnings.append(f"HEIGHT overflow: {title!r} needs {24 + 19 * len(lines) + 12} > {h}")


def group(x, y, w, h, title, kind):
    f, s, t = PALETTE[kind]
    out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" fill="{f}" fill-opacity=".35" stroke="{s}" stroke-width="1.4" stroke-dasharray="8 5"/>')
    out.append(f'<text x="{x + 14}" y="{y + 22}" font-size="13" font-weight="700" fill="{t}">{escape(title)}</text>')
    if len(title) * 7.6 > w - 24:
        warnings.append(f"GROUP title overflow: {title!r}")


def arrow(pts, label="", dashed=False, color="#334155", lx=None, ly=None, anchor="middle", both=False):
    d = "M " + " L ".join(f"{px},{py}" for px, py in pts)
    marker = ' marker-end="url(#a)"' + (' marker-start="url(#b)"' if both else "")
    dash = ' stroke-dasharray="6 4"' if dashed else ""
    out.append(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="1.7"{dash}{marker}/>')
    if label:
        mx = lx if lx is not None else (pts[0][0] + pts[-1][0]) / 2
        my = ly if ly is not None else (pts[0][1] + pts[-1][1]) / 2 - 6
        out.append(f'<text x="{mx}" y="{my}" font-size="11.5" font-weight="600" fill="{color}" text-anchor="{anchor}" paint-order="stroke" stroke="#fff" stroke-width="4">{escape(label)}</text>')


def badge(x, y, n):
    out.append(f'<circle cx="{x}" cy="{y}" r="11" fill="#0f172a"/><text x="{x}" y="{y + 4.5}" font-size="12" font-weight="700" fill="#fff" text-anchor="middle">{n}</text>')


out.append(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" font-family="Inter,system-ui,-apple-system,Segoe UI,Roboto,sans-serif" role="img" aria-labelledby="t d">')
out.append('<title id="t">SupportPilot production system design</title><desc id="d">Clients, edge, stateless API tier with outbox workers, data tier, external services and observability, sized for 30,000 conversations per day.</desc>')
out.append('<defs><marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#334155"/></marker>'
           '<marker id="b" viewBox="0 0 10 10" refX="1" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M10,0 L0,5 L10,10 z" fill="#334155"/></marker></defs>')
out.append(f'<rect width="{W}" height="{H}" fill="#ffffff"/>')
out.append('<text x="30" y="40" font-size="24" font-weight="800" fill="#0f172a">SupportPilot · production system design</text>')
out.append('<text x="30" y="64" font-size="13.5" fill="#475569">Target architecture for ≈30,000 support conversations/day. Design point = 2× the busiest minute. Numbers: capacity/model.py · measurements: docs/SCALING.md §9.</text>')
for x, t in ((30, "1 · CLIENTS"), (350, "2 · EDGE"), (680, "3 · STATELESS API TIER + WORKERS"), (1190, "4 · DATA TIER")):
    out.append(f'<text x="{x}" y="100" font-size="12" font-weight="800" letter-spacing="1.5" fill="#94a3b8">{t}</text>')
out.append('<text x="1510" y="708" font-size="12" font-weight="800" letter-spacing="1.5" fill="#94a3b8">5 · EXTERNAL</text>')

# ---------------- 1. clients (x 30..310)
box(30, 118, 280, 112, "Customer website / app", ["Embeds widget.js (React, Shadow DOM)", "REST for turns; WebSocket for live", "agent replies and status"], "client")
box(30, 262, 280, 96, "Customer's own backend", ["Authenticates the shopper and mints a", "short-lived signed user token", "(HS256, aud=supportpilot, exp ≤ 1 h)"], "client")
box(30, 392, 280, 96, "Human support agents", ["Agent console (React): queue, claim,", "reply, resolve, return-to-bot", "Per-agent keys today; SSO on roadmap"], "client")

# ---------------- 2. edge (x 350..630)
box(350, 118, 280, 76, "CDN", ["widget.js · console · demo site", "immutable, versioned assets"], "edge")
box(350, 218, 280, 76, "WAF · DDoS · bot filter", ["per-IP limits, geo/ASN rules,", "request-size caps"], "edge")
box(350, 318, 280, 170, "L7 load balancer", ["TLS termination, HTTP/2, WebSocket", "readiness → /readyz (drains bad pods)", "no sticky sessions: Redis fans events", "out across pods", "idle timeout > client WS ping (25 s)"], "edge")

# ---------------- 3. API tier + workers (x 680..1150)
group(680, 112, 470, 560, "API Deployment (≥3 pods, 3 AZs) + Outbox workers", "app")
box(696, 140, 438, 66, "REST + WebSocket handlers (FastAPI)", ["widget · agent console · admin · /metrics /healthz /readyz"], "app")
box(696, 218, 438, 96, "Orchestrator (tool-use loop)", ["history → model → tools → model … (≤ 6 steps)", "deterministic handoff phrases · auto-escalation", "ownership enforced server-side on every tool"], "app")
box(696, 326, 438, 80, "LLM guard", ["per-pod concurrency cap · queue and call timeouts", "overload ⇒ fallback model, else hand off to human"], "app")
box(696, 418, 438, 116, "Tenant runtime (per-customer config)", ["KB index (BM25, in memory) · persona · branding", "order provider (demo|HTTP) · ticket provider (Jira)", "allowed origins · agent accounts", "vector/hybrid index for huge KBs: roadmap"], "app")
box(696, 546, 438, 112, "Outbox worker Deployment (≥2 pods)", ["drains ticket_outbox: backoff 30 s → 1 h", "atomic claim (UPDATE … WHERE pending)", "Jira idempotency label · dead-letter ⇒ human", "stops being needed only if Jira never fails"], "app")

# ---------------- 4. data tier (x 1190..1670)
group(1190, 112, 480, 560, "Data tier (managed services)", "data")
box(1206, 140, 448, 62, "PgBouncer", ["transaction pooling: pods × pool ≪ max_connections"], "data")
box(1206, 214, 448, 126, "PostgreSQL primary (HA)", ["conversations · messages · tickets · ticket_outbox · audit_log", "synchronous standby in 2nd AZ · PITR backups", "monthly partitions for messages/audit (recommended)"], "data")
box(1206, 352, 448, 62, "Read replica(s)", ["agent queue lists · analytics · exports"], "data")
box(1206, 426, 448, 96, "Redis (HA, managed)", ["pub/sub: cross-pod WebSocket fan-out · shared rate limits", "outage ⇒ same-pod delivery only; limiter fails open"], "data")
box(1206, 534, 448, 62, "Object storage", ["KB source docs · backups · transcript archive (retention policy)"], "data")
box(1206, 608, 448, 50, "Vector / search index (optional)", [], "data", dashed=True)

# ---------------- 5. external services (row, x 680..1670)
ey, eh = 722, 128
box(680, ey, 215, eh, "Model API (Anthropic)", ["primary + optional fallback", "(ANTHROPIC_FALLBACK_MODEL)", "prompt caching on tenant", "prefix · quota ≥ 2× peak"], "ext")
box(905, ey, 200, eh, "Jira Cloud", ["created via outbox only", "global cap ≈ 5 req/s", "honours 429 / Retry-After"], "ext")
box(1115, ey, 215, eh, "Customer order APIs", ["per-tenant HTTP, scoped by", "the verified customer id", "timeouts + circuit breaker"], "ext")
box(1340, ey, 160, eh, "Slack · paging", ["handoff pings", "alerts"], "ext")
box(1510, ey, 160, eh, "Secrets / KMS", ["token secrets, keys,", "rotation w/o redeploy"], "ext")

# ---------------- observability band
group(30, 880, 1640, 150, "Observability & release safety", "obs")
box(46, 906, 392, 112, "Metrics (Prometheus / OTel)", ["sp_turns_total · sp_turn_seconds · sp_llm_inflight", "sp_llm_tokens_total{cache_read} · sp_handoffs_total", "sp_ticket_outbox_pending · sp_authz_violations_total"], "obs")
box(450, 906, 392, 112, "Dashboards & alerts (SLOs)", ["turn p95 < 12 s · error rate < 0.1 % · 99.9 % avail.", "model overload rate · outbox age and depth", "authz violation ≥ 1 ⇒ page · handoff-rate drift"], "obs")
box(854, 906, 392, 112, "Logs & traces", ["JSON logs, no tokens/PII · audit_log in Postgres", "(every order lookup and ticket)", "OpenTelemetry traces: roadmap"], "obs")
box(1258, 906, 396, 112, "Release safety", ["CI eval gate (python -m evals.run) · smoke test", "canary 5 → 25 → 100 % with auto-rollback", "per-tenant model/prompt canary: roadmap"], "obs")

# ---------------- arrows (kept few and non-crossing)
arrow([(310, 150), (350, 150)], "script", lx=330, ly=130)
arrow([(310, 190), (330, 190), (330, 253), (350, 253)], "", )
out.append('<text x="322" y="214" font-size="11.5" font-weight="600" fill="#334155" text-anchor="end" paint-order="stroke" stroke="#fff" stroke-width="4">HTTPS+WSS</text>')
arrow([(170, 262), (170, 230)], "signed token", dashed=True, color="#6366f1", lx=240, ly=250)
arrow([(310, 440), (330, 440), (330, 270), (350, 270)], "")
arrow([(490, 194), (490, 218)])
arrow([(490, 294), (490, 318)])
arrow([(630, 400), (680, 400)], "")
arrow([(1150, 170), (1190, 170)], "SQL", lx=1170, ly=162)
arrow([(1150, 470), (1190, 470)], "pub/sub", lx=1170, ly=462)
arrow([(1150, 565), (1190, 565)], "docs", lx=1170, ly=557)
# API -> external: short vertical drops from the group's bottom edge
arrow([(787, 672), (787, 722)], "model calls", lx=800, ly=700, anchor="start")
arrow([(1005, 672), (1005, 722)], "create issue", lx=1018, ly=700, anchor="start")
arrow([(1130, 672), (1130, 722)], "GET orders", lx=1140, ly=712, anchor="start")
arrow([(1150, 610), (1170, 610), (1170, 692), (1420, 692), (1420, 722)], "webhooks", lx=1300, ly=686)
out.append('<path d="M 1420 672 L 1420 640 L 1150 640" fill="none" stroke="#334155" stroke-width="1.7" stroke-dasharray="6 4" marker-end="url(#a)" transform="translate(0,0)"/>') if False else None

badge(340, 150, 1); badge(340, 253, 2); badge(655, 388, 3); badge(1170, 190, 4); badge(760, 700, 5)
out.append('<text x="30" y="1060" font-size="12" fill="#475569">① widget loads from the CDN  ② customer backend mints a user token; widget opens a session via WAF/LB  ③ any pod verifies it and runs the turn  ④ state → Postgres, events → Redis  ⑤ model / order / ticket calls as needed.</text>')
out.append('<text x="30" y="1078" font-size="12" fill="#475569">Design point (2× peak): 5 conv/s · ≈33 turns/s · ≈205 model calls in flight · ≈855 open chats. Pods = max(throughput, model slots) + 1: 5 at the default 64-call cap.</text>')
out.append("</svg>")

Path(__file__).with_name("system-design.svg").write_text("\n".join(x for x in out if x))
print("wrote system-design.svg")
for w in warnings:
    print("WARNING:", w)
