"""Apartment net-worth routes: units CRUD, document upload/download, summary.

Returns blend appreciation + rental yield into an overall CAGR (computed
server-side, never stored). Storage is Supabase (2NF tables `apartment_units` +
`apartment_documents`, files in the `apartment-docs` bucket) with a JSON-file
fallback. See SUPABASE.md → "Apartment net-worth tables" for the migration SQL.
"""
from __future__ import annotations

import io
from typing import Optional

from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import StreamingResponse, RedirectResponse
from pydantic import BaseModel

from ..apartments import store

router = APIRouter(prefix="/api/apartments", tags=["apartments"])

MAX_DOC_BYTES = 20 * 1024 * 1024


def _require_ready() -> None:
    if not store.tables_ready():
        raise HTTPException(503, store.MIGRATION_HINT)


class UnitIn(BaseModel):
    name: str
    owner: Optional[str] = None
    location: Optional[str] = None
    area_sqft: Optional[float] = None
    bought_date: Optional[str] = None
    bought_price: Optional[float] = None
    current_estimated_price: Optional[float] = None
    after_brokerage_price: Optional[float] = None
    monthly_rent: Optional[float] = None
    notes: Optional[str] = None
    sellable_on: Optional[str] = None


class TenantIn(BaseModel):
    name: str
    phone: Optional[str] = None
    advance_paid: Optional[float] = None
    move_in_date: Optional[str] = None
    move_out_date: Optional[str] = None
    notes: Optional[str] = None


@router.get("/summary")
def summary():
    _require_ready()
    return store.compute_summary()


@router.get("/units")
def list_units():
    _require_ready()
    return store.list_units()


@router.get("/units/{uid}")
def get_unit(uid: str):
    _require_ready()
    u = store.get_unit(uid)
    if not u:
        raise HTTPException(404, "Unit not found.")
    return u


@router.post("/units")
def create_unit(body: UnitIn):
    _require_ready()
    if not (body.name or "").strip():
        raise HTTPException(400, "Name is required.")
    return store.create_unit(body.model_dump())


@router.put("/units/{uid}")
def update_unit(uid: str, patch: dict):
    _require_ready()
    updated = store.update_unit(uid, patch)
    if not updated:
        raise HTTPException(404, "Unit not found.")
    return updated


@router.delete("/units/{uid}")
def delete_unit(uid: str):
    _require_ready()
    if not store.delete_unit(uid):
        raise HTTPException(404, "Unit not found.")
    return {"ok": True}


# ── Tenants ─────────────────────────────────────────────────────────────────
@router.post("/units/{uid}/tenants")
def add_tenant(uid: str, body: TenantIn):
    _require_ready()
    if not store.get_unit(uid):
        raise HTTPException(404, "Unit not found.")
    if not (body.name or "").strip():
        raise HTTPException(400, "Tenant name is required.")
    return store.add_tenant(uid, body.model_dump())


@router.put("/tenants/{tid}")
def update_tenant(tid: str, patch: dict):
    _require_ready()
    updated = store.update_tenant(tid, patch)
    if not updated:
        raise HTTPException(404, "Tenant not found.")
    return updated


@router.delete("/tenants/{tid}")
def delete_tenant(tid: str):
    _require_ready()
    if not store.delete_tenant(tid):
        raise HTTPException(404, "Tenant not found.")
    return {"ok": True}


# ── Documents ─────────────────────────────────────────────────────────────────
@router.post("/units/{uid}/documents")
async def upload_document(uid: str, file: UploadFile = File(...)):
    _require_ready()
    if not store.get_unit(uid):
        raise HTTPException(404, "Unit not found.")
    content = await file.read()
    if not content:
        raise HTTPException(400, "Empty file.")
    if len(content) > MAX_DOC_BYTES:
        raise HTTPException(400, "File too large (max 20 MB).")
    return store.add_document(
        uid, file.filename or "document",
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
