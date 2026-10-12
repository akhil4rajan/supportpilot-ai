from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app import metrics
from app.db import Conversation, utcnow
from app.integrations.notify import post_webhook
from app.services import conversations as convs
from app.services.realtime import Hub
from app.tenancy.models import env
from app.tenancy.registry import TenantRuntime

log = logging.getLogger(__name__)


async def start_handoff(
    db: AsyncSession, hub: Hub, rt: TenantRuntime, conv: Conversation, reason: str, summary: str, trigger: str = "model"
) -> bool:
    """Move a conversation into the human queue. Idempotent: returns False if already queued.

    `trigger` (model | button | phrase | auto_unanswered | llm_error | refusal | loop_limit | ticket_failed) is a metric
    label so we can see WHY people reach humans.
    """
    if conv.status in convs.HANDOFF_STATES:
        return False
    conv.status = "waiting_human"
    conv.handoff_reason = reason[:1000]
    conv.handoff_summary = summary[:2000]
    conv.handoff_at = utcnow()
    conv.assigned_agent_id = conv.assigned_agent_name = None
    await db.commit()
    metrics.HANDOFFS.labels(rt.tenant.id, trigger).inc()
    await convs.add_message(db, hub, conv, "system", rt.tenant.handoff.waiting_message, {"event": "handoff_requested"})
    await convs.publish_update(hub, conv)
    who = conv.user_name or conv.user_id or "anonymous visitor"
    await post_webhook(
        env(rt.tenant.handoff.webhook_url_env),
        f":raising_hand: *{rt.tenant.name}* - {who} needs a human.\n*Reason:* {reason}\n*Summary:* {summary}",
    )
    log.info("handoff tenant=%s conv=%s trigger=%s", rt.tenant.id, conv.id, trigger)
    return True
