from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

from app.tenancy.models import HttpOrdersConfig, env

from .base import Order, OrderProviderError


class HttpOrderProvider:
    """Calls the customer's own order API, scoped to the verified customer id.

    Expected: GET {get_path} -> 200 JSON order | 404 if missing OR not owned by that customer.
    `field_map` renames their fields to ours (canonical -> theirs). Dotted paths are supported.
    """

    def __init__(self, cfg: HttpOrdersConfig, client: httpx.AsyncClient | None = None) -> None:
        self.cfg = cfg
        self._client = client or httpx.AsyncClient(timeout=cfg.timeout_seconds)

    def _headers(self) -> dict[str, str]:
        key = env(self.cfg.api_key_env)
        return {"Authorization": f"Bearer {key}"} if key else {}

    def _url(self, template: str, **kw: str) -> str:
        return self.cfg.base_url.rstrip("/") + template.format(**{k: quote(v, safe="") for k, v in kw.items()})

    def _pick(self, data: dict[str, Any], field: str) -> Any:
        cur: Any = data
        for part in self.cfg.field_map.get(field, field).split("."):
            if not isinstance(cur, dict) or part not in cur:
                return None
            cur = cur[part]
        return cur

    def _to_order(self, data: dict[str, Any], customer_id: str) -> Order:
        owner = self._pick(data, "customer_id")
        # Defence in depth: if the upstream tells us who owns the order, it must be this customer.
        if owner is not None and str(owner) != customer_id:
            raise OrderProviderError("upstream returned an order that belongs to another customer")
        return Order(
            id=str(self._pick(data, "id")),
            customer_id=customer_id,
            status=str(self._pick(data, "status") or "unknown"),
            placed_at=self._pick(data, "placed_at"),
            items=self._pick(data, "items") or [],
            total=self._pick(data, "total"),
            currency=self._pick(data, "currency"),
            carrier=self._pick(data, "carrier"),
            tracking_number=self._pick(data, "tracking_number"),
            tracking_url=self._pick(data, "tracking_url"),
            estimated_delivery=self._pick(data, "estimated_delivery"),
            scheduled_for=self._pick(data, "scheduled_for"),
        )

    async def get_order(self, customer_id: str, order_id: str) -> Order | None:
        try:
            r = await self._client.get(
                self._url(self.cfg.get_path, customer_id=customer_id, order_id=order_id), headers=self._headers()
            )
        except httpx.HTTPError as e:
            raise OrderProviderError(str(e)) from e
        if r.status_code in (403, 404):
            return None
        if r.status_code != 200:
            raise OrderProviderError(f"order API returned {r.status_code}")
        return self._to_order(r.json(), customer_id)

    async def list_orders(self, customer_id: str, limit: int = 5) -> list[Order]:
        try:
            r = await self._client.get(
                self._url(self.cfg.list_path, customer_id=customer_id),
                params={"limit": limit},
                headers=self._headers(),
            )
        except httpx.HTTPError as e:
            raise OrderProviderError(str(e)) from e
        if r.status_code == 404:
            return []
        if r.status_code != 200:
            raise OrderProviderError(f"order API returned {r.status_code}")
        body = r.json()
        rows = body if isinstance(body, list) else body.get("orders", [])
        return [self._to_order(row, customer_id) for row in rows[:limit]]
