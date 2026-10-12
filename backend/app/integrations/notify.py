from __future__ import annotations

import logging

import httpx

log = logging.getLogger(__name__)


async def post_webhook(url: str | None, text: str) -> None:
    """Best-effort Slack-compatible notification. Never raises."""
    if not url:
        return
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            await c.post(url, json={"text": text})
    except Exception as e:  # noqa: BLE001
        log.warning("handoff webhook failed: %s", e)
