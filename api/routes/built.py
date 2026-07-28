"""Land + Build routes: self-built properties (land leg + construction leg),
documents, and a summary. Returns are a two-leg money-weighted IRR (computed
server-side, never stored). Storage: Supabase 2NF (built_properties +
built_documents, files in `built-docs`) with a JSON-file fallback. See
SUPABASE.md → "Land + Build tables".
"""
from __future__ import annotations

import io
from typing import Optional

from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import StreamingResponse, RedirectResponse
from pydantic import BaseModel

from ..built import store

router = APIRouter(prefix="/api/built", tags=["built"])

MAX_DOC_BYTES = 20 * 1024 * 1024


def _require_ready() -> None:
    if not store.tables_ready():
        raise HTTPException(503, store.MIGRATION_HINT)


class PropertyIn(BaseModel):
    name: str
    owner: Optional[str] = None
    location: Optional[str] = None
    area_sqft: Optional[float] = None
    land_cost: Optional[float] = None
    land_date: Optional[str] = None
    construction_cost: Optional[float] = None
    construction_date: Optional[str] = None
    current_estimated_price: Optional[float] = None
    after_brokerage_price: Optional[float] = None
    monthly_rent: Optional[float] = None
    notes: Optional[str] = None
    sellable_on: Optional[str] = None


@router.get("/summary")
def summary():
    _require_ready()
    return store.compute_summary()


@router.get("/properties")
def list_properties():
    _require_ready()
    return store.list_properties()


@router.get("/properties/{pid}")
def get_property(pid: str):
    _require_ready()
    p = store.get_property(pid)
    if not p:
        raise HTTPException(404, "Property not found.")
    return p


@router.post("/properties")
def create_property(body: PropertyIn):
    _require_ready()
    if not (body.name or "").strip():
        raise HTTPException(400, "Name is required.")
    return store.create_property(body.model_dump())


@router.put("/properties/{pid}")
def update_property(pid: str, patch: dict):
    _require_ready()
    updated = store.update_property(pid, patch)
    if not updated:
        raise HTTPException(404, "Property not found.")
    return updated


@router.delete("/properties/{pid}")
def delete_property(pid: str):
    _require_ready()
    if not store.delete_property(pid):
        raise HTTPException(404, "Property not found.")
    return {"ok": True}


# ── Documents ─────────────────────────────────────────────────────────────────
@router.post("/properties/{pid}/documents")
async def upload_document(pid: str, file: UploadFile = File(...)):
    _require_ready()
    if not store.get_property(pid):
        raise HTTPException(404, "Property not found.")
    content = await file.read()
    if not content:
        raise HTTPException(400, "Empty file.")
    if len(content) > MAX_DOC_BYTES:
        raise HTTPException(400, "File too large (max 20 MB).")
    return store.add_document(
        pid, file.filename or "document",
        content, file.content_type or "application/octet-stream",
    )


@router.get("/documents/{doc_id}")
def download_document(doc_id: str):
    resolved = store.open_document(doc_id)
    if not resolved:
        raise HTTPException(404, "Document not found.")
    if "redirect" in resolved:
        return RedirectResponse(resolved["redirect"])
    with open(resolved["path"], "rb") as f:
        data = f.read()
    disposition = f'inline; filename="{resolved.get("filename") or "document"}"'
    return StreamingResponse(
        io.BytesIO(data),
        media_type=resolved.get("mime") or "application/octet-stream",
        headers={"Content-Disposition": disposition},
    )


@router.delete("/documents/{doc_id}")
def delete_document(doc_id: str):
    if not store.delete_document(doc_id):
        raise HTTPException(404, "Document not found.")
    return {"ok": True}
