"""Tools the model can call. Every tool runs server-side with a ToolContext the model cannot influence:
the customer's identity comes from the verified token, never from tool arguments."""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import VerifiedUser
from app import metrics
from app.db import AuditLog, Conversation, Ticket, TicketOutbox, utcnow
from app.integrations.orders.base import OrderProviderError, normalize_order_id
from app.integrations.tickets.base import TicketError, TicketRequest
from app.services import conversations as convs
from app.tenancy.registry import TenantRuntime

log = logging.getLogger(__name__)
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,}$")
MAX_TICKETS_PER_CONVERSATION = 3


@dataclass
class ToolContext:
    db: AsyncSession
    rt: TenantRuntime
    conv: Conversation
    user: VerifiedUser | None
    # --- outputs collected during a turn ---
    sources: list[dict[str, str]] = field(default_factory=list)
    tickets: list[dict[str, str | None]] = field(default_factory=list)
    tools_used: list[str] = field(default_factory=list)
    kb_searched: bool = False
    kb_confident: bool = False
    kb_none: bool = False
    task_succeeded: bool = False  # an order/ticket tool did real work this turn
    escalation: tuple[str, str] | None = None
    escalation_trigger: str = "model"
    llm_ms: float = 0.0  # time spent waiting on the model this turn (the rest of the turn is platform overhead)
    llm_calls: int = 0
    queue_ms: float = 0.0  # waiting for a model slot (platform backpressure), excluded from llm_ms


async def _audit(ctx: ToolContext, action: str, detail: dict[str, Any]) -> None:
    ctx.db.add(
        AuditLog(
            tenant_id=ctx.rt.tenant.id,
            conversation_id=ctx.conv.id,
            actor=f"user:{ctx.user.id}" if ctx.user else "anonymous",
            action=action,
            detail=detail,
        )
    )
    await ctx.db.commit()


# --------------------------------------------------------------------------- tool implementations


async def search_knowledge_base(ctx: ToolContext, query: str = "") -> dict[str, Any]:
    """Three bands. confident: answer from it. possible: low-confidence matches the model must check actually answer
    the question. none: nothing relevant - say so (and count a miss toward automatic escalation)."""
    cfg = ctx.rt.tenant.knowledge
    hits = ctx.rt.kb.search(str(query)[:500], cfg.top_k, cfg.synonyms)
    ctx.kb_searched = True
    best = hits[0].coverage if hits else 0.0
    band = "confident" if best >= cfg.min_coverage else "possible" if best >= cfg.weak_coverage else "none"
    metrics.KB_SEARCHES.labels(ctx.rt.tenant.id, band).inc()
    if band == "none":
        ctx.kb_none = True
        return {"confident": False, "match": "none", "passages": [], "message": "Nothing relevant in the knowledge base."}
    ctx.kb_confident = ctx.kb_confident or band == "confident"
    floor = max(cfg.weak_coverage, best * 0.6)  # keep passages near the top hit
    relevant = [h for h in hits if h.coverage >= floor]
    for h in relevant:  # only cite what the model was actually given as relevant
        ref = {"source": h.chunk.source, "title": h.chunk.title}
        if ref not in ctx.sources:
            ctx.sources.append(ref)
    out: dict[str, Any] = {
        "confident": band == "confident",
        "match": band,
        "passages": [{"source": h.chunk.source, "title": h.chunk.title, "text": h.chunk.text, "relevance": round(h.coverage, 2)} for h in relevant],
    }
    if band == "possible":
        out["message"] = ("Low-confidence matches. Use them ONLY if they directly answer the customer's question; "
                          "otherwise say you don't have that information.")
    return out


def _label(ctx: ToolContext) -> str:
    return ctx.rt.tenant.orders.label


async def get_order_status(ctx: ToolContext, order_id: str = "") -> dict[str, Any]:
    label = _label(ctx)
    if ctx.user is None:
        metrics.ORDER_LOOKUPS.labels(ctx.rt.tenant.id, "denied").inc()
        return {"error": "login_required", "message": f"The customer must be signed in to check a {label}."}
    oid = normalize_order_id(str(order_id))
    if oid is None:
        return {"error": "invalid_order_id", "message": f"That doesn't look like a valid {label} number."}
    try:
        order = await ctx.rt.orders.get_order(ctx.user.id, oid)  # type: ignore[union-attr]
    except OrderProviderError as e:
        log.warning("order provider failure tenant=%s: %s", ctx.rt.tenant.id, e)
        metrics.ORDER_LOOKUPS.labels(ctx.rt.tenant.id, "error").inc()
        await _audit(ctx, "order_lookup_error", {"order_id": oid})
        return {"error": "order_system_unavailable", "message": f"The {label} system is temporarily unavailable."}
    # Ownership assertion independent of the provider implementation (defence in depth).
    if order is not None and order.customer_id != ctx.user.id:
        log.error("OWNERSHIP VIOLATION tenant=%s user=%s order=%s", ctx.rt.tenant.id, ctx.user.id, oid)
        metrics.AUTHZ_VIOLATIONS.inc()
        await _audit(ctx, "order_ownership_violation", {"order_id": oid})
        order = None
    metrics.ORDER_LOOKUPS.labels(ctx.rt.tenant.id, "found" if order is not None else "not_found").inc()
    await _audit(ctx, "order_lookup", {"order_id": oid, "found": order is not None})
    if order is None:
        # Identical response for "does not exist" and "belongs to someone else".
        return {"found": False, "message": f"No {label} {oid} was found on this customer's account."}
    ctx.task_succeeded = True
    return {"found": True, "order": {**order.public_view(), "label": label}}


async def list_my_orders(ctx: ToolContext) -> dict[str, Any]:
    if ctx.user is None:
        return {"error": "login_required", "message": f"The customer must be signed in to see their {_label(ctx)}s."}
    try:
        orders = await ctx.rt.orders.list_orders(ctx.user.id, limit=5)  # type: ignore[union-attr]
    except OrderProviderError as e:
        log.warning("order provider failure tenant=%s: %s", ctx.rt.tenant.id, e)
        return {"error": "order_system_unavailable", "message": f"The {_label(ctx)} system is temporarily unavailable."}
    orders = [o for o in orders if o.customer_id == ctx.user.id]
    await _audit(ctx, "order_list", {"count": len(orders)})
    ctx.task_succeeded = True
    return {"orders": [{**o.public_view(), "label": _label(ctx)} for o in orders]}


async def create_support_ticket(
    ctx: ToolContext,
    summary: str = "",
    description: str = "",
    priority: str = "normal",
    category: str = "",
    contact_email: str = "",
) -> dict[str, Any]:
    summary, description = str(summary).strip(), str(description).strip()
    if len(summary) < 5 or len(description) < 10:
        return {"error": "invalid", "message": "A clear summary and description are required."}
    email = (ctx.user.email if ctx.user and ctx.user.email else str(contact_email).strip()) or None
    if email and not EMAIL_RE.match(email):
        return {"error": "invalid_email", "message": "That email address doesn't look valid; ask the customer to re-enter it."}
    if not email:
        return {"error": "email_required", "message": "Ask the customer for a contact email first."}

    existing = await convs.tickets_for(ctx.db, ctx.conv.id)
    for t in existing:
        if t.summary == summary[:300]:  # idempotency: a retry must not create a duplicate
            metrics.TICKETS.labels(ctx.rt.tenant.id, ctx.rt.tenant.tickets.provider, "duplicate").inc()
            return {"ticket": {"key": t.key, "url": t.url}, "duplicate": True}
    if len(existing) >= MAX_TICKETS_PER_CONVERSATION:
        return {"error": "limit", "message": "Ticket limit reached for this conversation; offer a human agent instead."}

    msgs = await convs.history(ctx.db, ctx.conv.id, limit=30)
    transcript = "\n".join(f"[{m.role}] {m.content}" for m in msgs if m.role != "system")
    req = TicketRequest(
        summary=summary,
        description=description,
        priority=priority if priority in ("low", "normal", "high", "urgent") else "normal",
        category=str(category) or None,
        reporter_name=ctx.user.name if ctx.user else None,
        reporter_email=email,
        reporter_id=ctx.user.id if ctx.user else None,
        conversation_id=ctx.conv.id,
        transcript=transcript,
    )
    # Stable across retries: lets the provider recognise "this one was already created".
    await ctx.db.commit()  # release the connection before calling the (possibly slow) tracker
    req.idempotency_key = hashlib.sha1(f"{ctx.conv.id}|{summary}".encode()).hexdigest()[:16]
    prov = ctx.rt.tenant.tickets.provider
    try:
        res = await ctx.rt.tickets.create_ticket(req)  # type: ignore[union-attr]
    except TicketError as e:
        if e.retryable:
            # Tracker is slow/down/rate-limited: park the ticket durably and let the worker retry; never lose it.
            payload = {k: v for k, v in req.__dict__.items() if k != "attempt"}
            row = TicketOutbox(tenant_id=ctx.rt.tenant.id, conversation_id=ctx.conv.id, payload=payload, last_error=str(e)[:500], next_attempt_at=utcnow())
            ctx.db.add(row)
            await ctx.db.commit()
            metrics.TICKETS.labels(ctx.rt.tenant.id, prov, "queued").inc()
            await _audit(ctx, "ticket_queued", {"outbox_id": row.id})
            ctx.task_succeeded = True
            return {"queued": True, "reference": f"REQ-{row.id}",
                    "message": "The ticketing system is busy. The request is saved and will be filed automatically; "
                               "tell the customer their reference is REQ-%d and they will be updated in this chat." % row.id}
        log.warning("ticket creation failed tenant=%s: %s", ctx.rt.tenant.id, e)
        metrics.TICKETS.labels(ctx.rt.tenant.id, prov, "failed").inc()
        return {"error": "ticket_system_unavailable", "message": "The ticketing system rejected the request; offer a human agent instead."}
    ctx.db.add(
        Ticket(tenant_id=ctx.rt.tenant.id, conversation_id=ctx.conv.id, key=res.key, url=res.url, summary=summary[:300], provider=res.provider)
    )
    await ctx.db.commit()
    await _audit(ctx, "ticket_created", {"key": res.key})
    metrics.TICKETS.labels(ctx.rt.tenant.id, prov, "created").inc()
    ctx.tickets.append({"key": res.key, "url": res.url})
    ctx.task_succeeded = True
    return {"ticket": {"key": res.key, "url": res.url}}


async def escalate_to_human(ctx: ToolContext, reason: str = "", summary: str = "") -> dict[str, Any]:
    ctx.escalation = (str(reason) or "Requested by assistant", str(summary) or "(no summary)")
    return {"handoff": True, "message": "A human agent has been requested. Tell the customer they will join shortly."}


# --------------------------------------------------------------------------- registry + schemas

ToolFn = Callable[..., Awaitable[dict[str, Any]]]
IMPLEMENTATIONS: dict[str, ToolFn] = {
    "search_knowledge_base": search_knowledge_base,
    "get_order_status": get_order_status,
    "list_my_orders": list_my_orders,
    "create_support_ticket": create_support_ticket,
    "escalate_to_human": escalate_to_human,
}

_S = {"type": "string"}


def tool_schemas(rt: TenantRuntime) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = [
        {
            "name": "search_knowledge_base",
            "description": "Search the company's help articles/policies. Use for ANY question about products, shipping, returns, billing, accounts or how-to. Pass a short, self-contained search query.",
            "input_schema": {"type": "object", "properties": {"query": _S}, "required": ["query"]},
        }
    ]
    if rt.orders:
        lbl, eg = rt.tenant.orders.label, rt.tenant.orders.id_example
        tools += [
            {
                "name": "get_order_status",
                "description": f"Look up one {lbl} belonging to the signed-in customer (status, items, tracking, ETA). The customer's identity is applied automatically; only their own {lbl}s are visible.",
                "input_schema": {"type": "object", "properties": {"order_id": {**_S, "description": f"{lbl.capitalize()} number as given by the customer, e.g. {eg}"}}, "required": ["order_id"]},
            },
            {
                "name": "list_my_orders",
                "description": f"List the signed-in customer's most recent {lbl}s. Use when they don't know their {lbl} number.",
                "input_schema": {"type": "object", "properties": {}},
            },
        ]
    if rt.tickets:
        tools.append(
            {
                "name": "create_support_ticket",
                "description": "File a support ticket with the support team. Only call after the customer has agreed to the summary you proposed.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "summary": {**_S, "description": "One-line title"},
                        "description": {**_S, "description": "Full details: what happened, what was tried, order numbers, expected outcome"},
                        "priority": {"type": "string", "enum": ["low", "normal", "high", "urgent"]},
                        "category": {**_S, "description": "e.g. billing, shipping, bug, account"},
                        "contact_email": {**_S, "description": "Only needed when the customer is not signed in"},
                    },
                    "required": ["summary", "description"],
                },
            }
        )
    tools.append(
        {
            "name": "escalate_to_human",
            "description": "Hand the conversation to a human support agent. Include a summary so the agent doesn't have to re-read the chat.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "reason": {**_S, "description": "Why a human is needed"},
                    "summary": {**_S, "description": "What the customer wants, what was tried, relevant order/ticket ids"},
                },
                "required": ["reason", "summary"],
            },
        }
    )
    return tools
