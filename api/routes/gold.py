"""Gold / Silver / Other routes: pieces CRUD, documents, and a live-price proxy.

Current value is computed on the frontend from /prices × weight × purity, so
this router just stores the raw facts and serves cached live spot rates.
Storage: Supabase 2NF (gold_items + gold_documents, files in `gold-docs`) with a
JSON-file fallback. See SUPABASE.md → "Gold / Silver tables".
"""
from __future__ import annotations

import io
from typing import Optional

from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import StreamingResponse, RedirectResponse
from pydantic import BaseModel

from ..gold import store
from ..gold import prices as price_feed

router = APIRouter(prefix="/api/gold", tags=["gold"])

MAX_DOC_BYTES = 20 * 1024 * 1024


def _require_ready() -> None:
    if not store.tables_ready():
        raise HTTPException(503, store.MIGRATION_HINT)


class ItemIn(BaseModel):
    name: str
    owner: Optional[str] = None
    metal_type: str = "gold"          # gold | silver | other
    weight_g: Optional[float] = None
    purity_pct: Optional[float] = None
    manual_value: Optional[float] = None
    purchase_date: Optional[str] = None
    purchase_price: Optional[float] = None
    location: Optional[str] = None     # where the piece is kept (e.g. "Locker · HDFC Anna Nagar")
    notes: Optional[str] = None
    sellable_on: Optional[str] = None


@router.get("/prices")
def prices(refresh: bool = False):
    """Live spot rates: INR per gram for 24K gold + silver, cached ~10 min."""
    return price_feed.get_prices(force=refresh)


@router.get("/performance")
def performance(refresh: bool = False):
    """1y/3y/5y CAGR of gold & silver (INR/gram). Cached ~6h."""
    return price_feed.get_performance(force=refresh)


@router.get("/items")
def list_items():
    _require_ready()
    return store.list_items()


@router.get("/items/{iid}")
def get_item(iid: str):
    _require_ready()
    i = store.get_item(iid)
    if not i:
        raise HTTPException(404, "Item not found.")
    return i


@router.post("/items")
def create_item(body: ItemIn):
    _require_ready()
    if not (body.name or "").strip():
        raise HTTPException(400, "Name is required.")
    return store.create_item(body.model_dump())


@router.put("/items/{iid}")
def update_item(iid: str, patch: dict):
    _require_ready()
    updated = store.update_item(iid, patch)
    if not updated:
        raise HTTPException(404, "Item not found.")
    return updated


@router.delete("/items/{iid}")
def delete_item(iid: str):
    _require_ready()
    if not store.delete_item(iid):
        raise HTTPException(404, "Item not found.")
    return {"ok": True}


# ── Documents ─────────────────────────────────────────────────────────────────
@router.post("/items/{iid}/documents")
async def upload_document(iid: str, file: UploadFile = File(...)):
    _require_ready()
    if not store.get_item(iid):
        raise HTTPException(404, "Item not found.")
    content = await file.read()
    if not content:
        raise HTTPException(400, "Empty file.")
    if len(content) > MAX_DOC_BYTES:
        raise HTTPException(400, "File too large (max 20 MB).")
    return store.add_document(
        iid, file.filename or "document",
        content, file.content_type or "application/octet-stream",
    )


@router.get("/documents/{doc_id}")
def download_document(doc_id: str, preview: bool = False):
    resolved = store.open_document(doc_id, preview)
    if not resolved:
        raise HTTPException(404, "Document not found.")
    disposition = f'inline; filename="{resolved.get("filename") or "document"}"'
    return StreamingResponse(
        io.BytesIO(resolved["data"]),
        media_type=resolved.get("mime") or "application/octet-stream",
        headers={"Content-Disposition": disposition},
    )


class DocRename(BaseModel):
    name: str


@router.patch("/documents/{doc_id}")
def rename_document(doc_id: str, body: DocRename):
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(400, "Name cannot be empty.")
    doc = store.rename_document(doc_id, name)
    if not doc:
        raise HTTPException(404, "Document not found.")
    return doc


@router.delete("/documents/{doc_id}")
def delete_document(doc_id: str):
    if not store.delete_document(doc_id):
        raise HTTPException(404, "Document not found.")
    return {"ok": True}
