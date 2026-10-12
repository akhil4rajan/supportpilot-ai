"""Prometheus metrics. One module so names/labels stay consistent across dashboards and alerts.

Labels are deliberately low-cardinality (tenant, tool, outcome) - never conversation or user ids.
"""
from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest

REGISTRY = CollectorRegistry()
_LAT = (0.05, 0.1, 0.25, 0.5, 1, 2, 4, 8, 16, 30, 60)

HTTP_LATENCY = Histogram("sp_http_request_seconds", "HTTP request latency", ["route", "method", "status"], buckets=_LAT, registry=REGISTRY)
TURN_LATENCY = Histogram("sp_turn_seconds", "End-to-end bot turn (user message -> reply persisted)", ["tenant", "outcome"], buckets=_LAT, registry=REGISTRY)
TURNS = Counter("sp_turns_total", "Bot turns", ["tenant", "outcome"], registry=REGISTRY)  # answered|handoff|error

LLM_LATENCY = Histogram("sp_llm_call_seconds", "Model API call latency", ["model"], buckets=_LAT, registry=REGISTRY)
LLM_CALLS = Counter("sp_llm_calls_total", "Model API calls", ["model", "result"], registry=REGISTRY)  # ok|error|timeout|overload|refusal
LLM_TOKENS = Counter("sp_llm_tokens_total", "Model tokens", ["model", "kind"], registry=REGISTRY)  # input|output|cache_read|cache_write
LLM_FALLBACKS = Counter("sp_llm_fallbacks_total", "Calls rerouted to the fallback model", ["primary", "fallback", "reason"], registry=REGISTRY)
LLM_INFLIGHT = Gauge("sp_llm_inflight", "Model calls currently in flight", registry=REGISTRY)
LLM_QUEUED = Gauge("sp_llm_waiting", "Turns waiting for a model slot", registry=REGISTRY)

TOOL_CALLS = Counter("sp_tool_calls_total", "Tool invocations", ["tool", "outcome"], registry=REGISTRY)  # ok|error
HANDOFFS = Counter("sp_handoffs_total", "Conversations moved to the human queue", ["tenant", "trigger"], registry=REGISTRY)
TICKETS = Counter("sp_tickets_total", "Ticket creation attempts", ["tenant", "provider", "result"], registry=REGISTRY)  # created|queued|failed|duplicate
OUTBOX_DEPTH = Gauge("sp_ticket_outbox_pending", "Tickets waiting in the retry outbox", registry=REGISTRY)
ORDER_LOOKUPS = Counter("sp_order_lookups_total", "Order lookups", ["tenant", "result"], registry=REGISTRY)  # found|not_found|error|denied
AUTHZ_VIOLATIONS = Counter("sp_authz_violations_total", "Ownership assertion failures (must stay 0)", registry=REGISTRY)
WS_CONNECTIONS = Gauge("sp_ws_connections", "Open WebSocket connections", ["kind"], registry=REGISTRY)
KB_SEARCHES = Counter("sp_kb_searches_total", "Knowledge-base searches by match band (confident|possible|none)", ["tenant", "match"], registry=REGISTRY)


def render() -> bytes:
    return generate_latest(REGISTRY)
