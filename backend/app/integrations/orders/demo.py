from __future__ import annotations

import json
from pathlib import Path

from .base import Order


class DemoOrderProvider:
    """File-backed provider for demos/tests. Orders live in a JSON list with a `customer_id` each."""

    def __init__(self, path: Path) -> None:
        raw = json.loads(path.read_text("utf-8")) if path.exists() else []
        self._orders = [Order(**o) for o in raw]

    async def get_order(self, customer_id: str, order_id: str) -> Order | None:
        # Ownership is part of the lookup key, so a foreign order is indistinguishable from a missing one.
        return next((o for o in self._orders if o.id == order_id and o.customer_id == customer_id), None)

    async def list_orders(self, customer_id: str, limit: int = 5) -> list[Order]:
        mine = [o for o in self._orders if o.customer_id == customer_id]
        return sorted(mine, key=lambda o: o.placed_at or "", reverse=True)[:limit]
