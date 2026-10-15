from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol

ORDER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]{2,39}$")


def normalize_order_id(raw: str) -> str | None:
    """'#ab-1001 ' -> 'AB-1001'. Returns None if it is not a plausible id (also blocks path injection)."""
    cand = (raw or "").strip().lstrip("#").strip().upper()
    return cand if ORDER_ID_RE.match(cand) else None


@dataclass
class Order:
    id: str
    customer_id: str  # owner. Used for the ownership assertion; never shown to the model.
    status: str
    placed_at: str | None = None
    items: list[dict[str, Any]] = field(default_factory=list)
    total: float | None = None
    currency: str | None = None
    carrier: str | None = None
    tracking_number: str | None = None
    tracking_url: str | None = None
    estimated_delivery: str | None = None
    scheduled_for: str | None = None  # appointment / booking date-time, when the record is not a shipment

    def public_view(self) -> dict[str, Any]:
        """Allow-list of fields the model may see. No address, payment, or owner info."""
        return {
            "order_id": self.id,
            "status": self.status,
            "placed_at": self.placed_at,
            "items": [{"name": i.get("name"), "quantity": i.get("quantity", 1)} for i in self.items],
            "total": self.total,
            "currency": self.currency,
            "carrier": self.carrier,
            "tracking_number": self.tracking_number,
            "tracking_url": self.tracking_url,
            "estimated_delivery": self.estimated_delivery,
            "scheduled_for": self.scheduled_for,
        }


class OrderProviderError(Exception):
    """Upstream order system failed (as opposed to 'order not found')."""


class OrderProvider(Protocol):
    """Contract for any order backend.

    `customer_id` is ALWAYS the identity verified from the host site's signed token. A provider
    must only ever return orders owned by that customer.
    """

    async def get_order(self, customer_id: str, order_id: str) -> Order | None: ...

    async def list_orders(self, customer_id: str, limit: int = 5) -> list[Order]: ...
