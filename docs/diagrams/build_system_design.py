"""Generates system-design.svg (target production architecture). Run: python docs/diagrams/build_system_design.py"""
from __future__ import annotations

from html import escape
from pathlib import Path

W, H = 1640, 1060
out: list[str] = []

PALETTE = {  # fill, stroke, text
    "client": ("#eef2ff", "#6366f1", "#1e1b4b"),
    "edge": ("#f1f5f9", "#64748b", "#0f172a"),
    "app": ("#ecfdf5", "#10b981", "#064e3b"),
    "data": ("#fff7ed", "#f97316", "#7c2d12"),
    "ext": ("#fdf2f8", "#ec4899", "#831843"),
    "obs": ("#f0f9ff", "#0ea5e9", "#0c4a6e"),
    "note": ("#fefce8", "#ca8a04", "#713f12"),
}


def box(x, y, w, h, title, lines=(), kind="app", bold=True, dashed=False):
    f, s, t = PALETTE[kind]
    out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="{f}" stroke="{s}" stroke-width="1.6"{" stroke-dasharray=\'6 4\'" if dashed else ""}/>')
    ty = y + 24
    out.append(f'<text x="{x + 12}" y="{ty}" font-size="14.5" font-weight="{700 if bold else 500}" fill="{t}">{escape(title)}</text>')
    for i, ln in enumerate(lines):
        out.append(f'<text x="{x + 12}" y="{ty + 19 * (i + 1)}" font-size="12" fill="{t}" opacity=".85">{escape(ln)}</text>')


def group(x, y, w, h, title, kind):
    f, s, t = PALETTE[kind]
    out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" fill="{f}" fill-opacity=".35" stroke="{s}" stroke-width="1.4" stroke-dasharray="8 5"/>')
    out.append(f'<text x="{x + 14}" y="{y + 22}" font-size="13" font-weight="700" fill="{t}">{escape(title)}</text>')


def arrow(pts, label="", dashed=False, color="#334155", lx=None, ly=None, both=False, anchor="middle"):
    d = "M " + " L ".join(f"{px},{py}" for px, py in pts)
    marker = ' marker-end="url(#a)"' + (' marker-start="url(#b)"' if both else "")
    out.append(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="1.7"{" stroke-dasharray=\'6 4\'" if dashed else ""}{marker}/>')
    if label:
        mx = lx if lx is not None else (pts[0][0] + pts[-1][0]) / 2
        my = ly if ly is not None else (pts[0][1] + pts[-1][1]) / 2 - 6
        out.append(f'<text x="{mx}" y="{my}" font-size="11.5" font-weight="600" fill="{color}" text-anchor="{anchor}" paint-order="stroke" stroke="#fff" stroke-width="4">{escape(label)}</text>')


def badge(x, y, n):
    out.append(f'<circle cx="{x}" cy="{y}" r="11" fill="#0f172a"/><text x="{x}" y="{y + 4.5}" font-size="12" font-weight="700" fill="#fff" text-anchor="middle">{n}</text>')


out.append(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" font-family="Inter,system-ui,-apple-system,Segoe UI,Roboto,sans-serif" role="img" aria-labelledby="t d">')
out.append('<title id="t">SupportPilot production system design</title><desc id="d">Clients, edge, stateless API tier, data tier, external services and observability, sized for 30,000 conversations per day.</desc>')
out.append('<defs><marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#334155"/></marker>'
           '<marker id="b" viewBox="0 0 10 10" refX="1" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M10,0 L0,5 L10,10 z" fill="#334155"/></marker></defs>')
out.append(f'<rect width="{W}" height="{H}" fill="#ffffff"/>')
out.append(f'<text x="30" y="40" font-size="24" font-weight="800" fill="#0f172a">SupportPilot · production system design</text>')
out.append(f'<text x="30" y="64" font-size="13.5" fill="#475569">Target architecture for ≈30,000 support conversations/day (design point = 2× the busiest minute). Numbers from capacity/model.py; see docs/SCALING.md.</text>')

# column headers
for x, t in ((30, "1 · CLIENTS"), (330, "2 · EDGE"), (640, "3 · STATELESS API TIER"), (1090, "4 · DATA TIER"), (1390, "5 · EXTERNAL SERVICES")):
    out.append(f'<text x="{x}" y="100" font-size="12" font-weight="800" letter-spacing="1.5" fill="#94a3b8">{t}</text>')

# ---------------- 1. clients
box(30, 118, 250, 112, "Customer website / mobile app", ["Embeds widget.js (React, Shadow DOM)", "REST for turns · WebSocket for live", "agent replies & status"], "client")
box(30, 262, 250, 92, "Customer's own backend", ["Authenticates the shopper, mints a", "short-lived signed user token", "(HS256, aud=supportpilot, exp ≤ 1h)"], "client")
box(30, 386, 250, 96, "Human support agents", ["Agent console (React): queue, claim,", "reply, resolve, return-to-bot", "Per-tenant agent keys → SSO (roadmap)"], "client")

# ---------------- 2. edge
box(330, 118, 250, 76, "CDN", ["widget.js · console · demo site", "immutable, versioned assets"], "edge")
box(330, 222, 250, 76, "WAF · DDoS · bot filter", ["per-IP limits, geo / ASN rules,", "request size caps"], "edge")
box(330, 326, 250, 156, "L7 load balancer", ["TLS termination, HTTP/2, WebSocket upgrade", "readiness → /readyz (drains bad pods)", "no sticky sessions needed: the", "Redis hub fans events across pods", "idle timeout > WS ping (25 s)"], "edge")

# ---------------- 3. app tier
group(640, 112, 410, 458, "Stateless API Deployment  ·  min 3 pods across 3 AZs  ·  HPA: CPU + in-flight turns", "app")
box(656, 142, 378, 66, "REST + WebSocket handlers (FastAPI/uvicorn)", ["widget · agent console · admin · /metrics /healthz /readyz"], "app")
box(656, 222, 378, 96, "Orchestrator (tool-use loop)", ["history → model → tools → model … (≤ 6 steps)", "deterministic handoff phrases · auto-escalation", "ownership enforced server-side on every tool call"], "app")
box(656, 332, 378, 82, "LLM guard", ["per-pod concurrency cap · queue timeout (20 s) · call timeout", "overload ⇒ fallback model, else hand off to a human"], "app")
box(656, 428, 378, 128, "Tenant runtime (per customer config)", ["KB index (BM25, in memory, reloaded on upload)", "order provider (demo | HTTP) · ticket provider (Jira)", "persona, branding, allowed origins, agent accounts", "→ vector/hybrid index for very large KBs (roadmap)"], "app")
box(640, 596, 410, 98, "Outbox worker Deployment  ·  2+ pods", ["drains ticket_outbox with exponential backoff (30 s → 1 h)", "atomic claim (UPDATE … WHERE pending) · Jira idempotency", "label · dead-letter ⇒ human handoff"], "app")

# ---------------- 4. data tier
box(1090, 118, 250, 62, "PgBouncer", ["transaction pooling (pods × pool ≪ max_conn)"], "data")
box(1090, 206, 250, 124, "PostgreSQL primary", ["conversations · messages · tickets ·", "ticket_outbox · audit_log", "sync standby in 2nd AZ · PITR backups", "monthly partitions for messages/audit (recommended)"], "data")
box(1090, 350, 250, 74, "Read replica(s)", ["agent queue lists · analytics · exports"], "data")
box(1090, 452, 250, 98, "Redis (HA, managed)", ["pub/sub: cross-pod WebSocket fan-out", "shared rate limits (fixed window)", "fail-open: outage ⇒ local delivery only"], "data")
box(1090, 574, 250, 78, "Object storage", ["KB source docs · backups ·", "transcript archive (retention policy)"], "data")
box(1090, 676, 250, 62, "Vector / search index (optional)", ["pgvector or OpenSearch for large KBs"], "data", dashed=True)

# ---------------- 5. external
box(1390, 118, 230, 114, "Model API (Anthropic)", ["primary model + optional fallback model", "(ANTHROPIC_FALLBACK_MODEL) on overload", "prompt caching on tenant prefix", "quota ≥ 2× design peak"], "ext")
box(1390, 258, 230, 92, "Jira Cloud", ["created via outbox only", "global cap ≈ 5 req/s,", "respects 429 / Retry-After"], "ext")
box(1390, 376, 230, 92, "Customer order APIs", ["per-tenant HTTP, scoped by", "verified customer id", "timeouts + circuit breaker"], "ext")
box(1390, 494, 230, 76, "Slack · PagerDuty", ["handoff pings · alerts"], "ext")
box(1390, 596, 230, 76, "Secret manager / KMS", ["token secrets, agent keys, API keys", "rotation without redeploy"], "ext")

# ---------------- observability band
group(30, 780, 1590, 170, "Observability & operations", "obs")
box(46, 808, 380, 120, "Metrics (Prometheus / OTel)", ["sp_turns_total · sp_turn_seconds · sp_llm_inflight", "sp_llm_tokens_total{cache_read} · sp_handoffs_total", "sp_ticket_outbox_pending · sp_authz_violations_total (=0)", "sp_ws_connections · sp_http_request_seconds"], "obs")
box(444, 808, 380, 120, "Dashboards & alerts (SLOs)", ["turn p95 < 12 s · error rate < 0.1 % · availability 99.9 %", "model overload / timeout rate · outbox age & depth", "authz violation ≥ 1 ⇒ page · handoff rate drift", "KB 'none' band rate · cache-hit ratio"], "obs")
box(842, 808, 380, 120, "Logs & traces", ["JSON logs (LOG_FORMAT=json), no tokens/PII", "OpenTelemetry traces across pod → worker (roadmap)", "audit_log: every order lookup and ticket", "ship to SIEM; retention per privacy review"], "obs")
box(1240, 808, 364, 120, "Release safety", ["eval gate in CI (python -m evals.run)", "post-deploy smoke test on canary", "canary 5 → 25 → 100 % with auto-rollback", "per-tenant model/prompt canary (roadmap)"], "obs")

# ---------------- design-load note
box(640, 712, 410, 52, "Design point (2× peak)", ["5 conv/s · ≈33 turns/s · ≈205 model calls in flight · ≈855 open chats"], "note", bold=True)

# ---------------- arrows
# client -> CDN / WAF
arrow([(280, 150), (330, 156)], "script", lx=305, ly=140)
arrow([(280, 190), (305, 190), (305, 260), (330, 260)], "HTTPS + WSS", lx=292, ly=232, anchor="middle")
arrow([(280, 304), (300, 304), (300, 210), (280, 210)], "user token", dashed=True, color="#6366f1", lx=255, ly=290, anchor="end") if False else None
arrow([(155, 262), (155, 232)], "signed token", dashed=True, color="#6366f1", lx=155, ly=252)
arrow([(280, 440), (315, 440), (315, 270), (330, 270)], "HTTPS + WSS", lx=300, ly=412, anchor="middle")
# edge chain
arrow([(455, 194), (455, 222)])
arrow([(455, 298), (455, 326)])
# LB -> API
arrow([(580, 400), (656, 400)], "", lx=618)
arrow([(580, 372), (640, 372), (640, 200), (656, 175)], "", lx=640)
# API -> data
arrow([(1034, 175), (1090, 150)], "SQL", lx=1062, ly=150)
arrow([(1090, 180), (1090, 206)])
arrow([(1215, 330), (1215, 350)], "stream", lx=1245, ly=344)
arrow([(1034, 480), (1090, 500)], "pub/sub", lx=1062, ly=476)
arrow([(1034, 530), (1062, 530), (1062, 610), (1090, 610)], "", lx=1062)
# workers
arrow([(1050, 640), (1070, 640), (1070, 230), (1090, 230)], "SQL", lx=1080, ly=620, anchor="start") if False else None
arrow([(1050, 650), (1080, 650), (1080, 270), (1090, 270)], "claim/update", lx=1078, ly=420, anchor="end")
# API -> external
arrow([(1034, 190), (1062, 190), (1062, 100), (1370, 100), (1370, 150), (1390, 150)], "model calls (tool-use loop)", lx=1210, ly=94)
arrow([(1050, 664), (1370, 664), (1370, 300), (1390, 300)], "create issue (idempotent)", lx=1280, ly=658)
arrow([(1034, 520), (1050, 520), (1050, 462), (1370, 462), (1370, 440), (1390, 440)], "GET /customers/{id}/orders/{id}", lx=1200, ly=456)
arrow([(1034, 275), (1056, 275), (1056, 536), (1390, 536)], "handoff / alert webhooks", lx=1220, ly=530, anchor="middle")
arrow([(1215, 652), (1215, 676)], "")
# secrets dashed
arrow([(1390, 634), (1352, 634), (1352, 585)], "", dashed=True, color="#ec4899")
# observability dashed
arrow([(845, 570), (845, 780)], "scrape / export", dashed=True, color="#0ea5e9", lx=845, ly=690) if False else None

# numbered flow
badge(300, 154, 1); badge(318, 240, 2); badge(612, 388, 3); badge(1074, 168, 4); badge(1380, 140, 5)
out.append('<text x="30" y="990" font-size="12.5" fill="#334155" font-weight="700">Request flow</text>')
out.append('<text x="30" y="1010" font-size="12" fill="#475569">① widget loads from CDN → ② customer\'s backend mints user token; widget opens a session via WAF/LB → ③ pod verifies token, runs the orchestrator turn → ④ state persisted (Postgres) and events published (Redis) → ⑤ model / order / ticket calls as needed; reply returned and pushed to sockets on any pod.</text>')
out.append('<text x="30" y="1030" font-size="12" fill="#475569">Humans: handoff ⇒ conversation enters the Postgres-backed queue; agent console receives it live over WebSocket; claim is an atomic compare-and-set so only one agent wins.</text>')
out.append("</svg>")

Path(__file__).with_name("system-design.svg").write_text("\n".join(out))
print("wrote system-design.svg")
