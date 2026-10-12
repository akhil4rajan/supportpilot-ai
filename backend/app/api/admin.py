"""Operator API: onboard/refresh customers and their knowledge bases. Protected by ADMIN_API_KEY."""
from __future__ import annotations

import hmac
import re

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from app.rag.index import SUPPORTED_SUFFIXES
from app.state import AppState

from .deps import get_state

router = APIRouter(prefix="/v1/admin", tags=["admin"])
SAFE_NAME = re.compile(r"^[\w][\w \-.]{0,99}$")
MAX_DOC_BYTES = 1_000_000


def require_admin(x_admin_key: str | None = Header(default=None), st: AppState = Depends(get_state)) -> None:
    key = st.settings.admin_api_key
    if not key or not x_admin_key or not hmac.compare_digest(key.encode(), x_admin_key.encode()):
        raise HTTPException(401, "admin key required")


class DocIn(BaseModel):
    filename: str
    content: str


@router.get("/tenants", dependencies=[Depends(require_admin)])
async def list_tenants(st: AppState = Depends(get_state)):
    return {
        "tenants": [
            {"id": r.tenant.id, "name": r.tenant.name, "kb_chunks": r.kb.size, "kb_sources": r.kb.sources,
             "orders": r.tenant.orders.provider, "tickets": r.tenant.tickets.provider}
            for r in st.registry.all()
        ]
    }


@router.post("/tenants/reload", dependencies=[Depends(require_admin)])
async def reload_tenants(st: AppState = Depends(get_state)):
    return {"loaded": st.registry.reload()}


@router.post("/tenants/{tenant_id}/knowledge", dependencies=[Depends(require_admin)])
async def upload_document(tenant_id: str, doc: DocIn, st: AppState = Depends(get_state)):
    rt = st.registry.by_id(tenant_id)
    if not rt:
        raise HTTPException(404, "unknown tenant")
    name = doc.filename.strip()
    if not SAFE_NAME.match(name) or ".." in name or not any(name.lower().endswith(s) for s in SUPPORTED_SUFFIXES):
        raise HTTPException(422, f"filename must be a plain name ending in {sorted(SUPPORTED_SUFFIXES)}")
    data = doc.content.encode()
    if len(data) > MAX_DOC_BYTES:
        raise HTTPException(413, "document too large (1 MB max)")
    rt.knowledge_dir.mkdir(parents=True, exist_ok=True)
    (rt.knowledge_dir / name).write_bytes(data)
    return {"saved": name, "kb_chunks": rt.reindex()}


@router.delete("/tenants/{tenant_id}/knowledge/{filename}", dependencies=[Depends(require_admin)])
async def delete_document(tenant_id: str, filename: str, st: AppState = Depends(get_state)):
    rt = st.registry.by_id(tenant_id)
    if not rt:
        raise HTTPException(404, "unknown tenant")
    path = (rt.knowledge_dir / filename).resolve()
    if rt.knowledge_dir.resolve() not in path.parents or not path.is_file():
        raise HTTPException(404, "no such document")
    path.unlink()
    return {"deleted": filename, "kb_chunks": rt.reindex()}
