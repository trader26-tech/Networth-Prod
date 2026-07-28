"""Land net-worth routes: parcels CRUD, document upload/download, summary.

CAGR (gross + net of brokerage) is computed server-side per parcel and in the
summary — never stored. Storage is Supabase (2NF tables `land_parcels` +
`land_documents`, files in the `land-docs` bucket) with a JSON-file fallback.
See SUPABASE.md → "Land net-worth tables" for the migration SQL.
"""
from __future__ import annotations

import io
from typing import Optional

from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import StreamingResponse, RedirectResponse
from pydantic import BaseModel

from ..land import store

router = APIRouter(prefix="/api/land", tags=["land"])

# Per-document upload cap (20 MB) — sale deeds / scans comfortably fit.
MAX_DOC_BYTES = 20 * 1024 * 1024


def _require_ready() -> None:
    """503 with the migration hint if Supabase is on but the tables are absent."""
    if not store.tables_ready():
        raise HTTPException(503, store.MIGRATION_HINT)


# ── Parcels ───────────────────────────────────────────────────────────────────
class ParcelIn(BaseModel):
    name: str
    owner: Optional[str] = None
    location: Optional[str] = None
    area_sqft: Optional[float] = None
    bought_date: Optional[str] = None
    bought_price: Optional[float] = None
    current_estimated_price: Optional[float] = None
    after_brokerage_price: Optional[float] = None
    notes: Optional[str] = None
    sellable_on: Optional[str] = None


@router.get("/summary")
def summary():
    _require_ready()
    return store.compute_summary()


@router.get("/parcels")
def list_parcels():
    _require_ready()
    return store.list_parcels()


@router.get("/parcels/{pid}")
def get_parcel(pid: str):
    _require_ready()
    p = store.get_parcel(pid)
    if not p:
        raise HTTPException(404, "Parcel not found.")
    return p


@router.post("/parcels")
def create_parcel(body: ParcelIn):
    _require_ready()
    if not (body.name or "").strip():
        raise HTTPException(400, "Name is required.")
    return store.create_parcel(body.model_dump())


@router.put("/parcels/{pid}")
def update_parcel(pid: str, patch: dict):
    _require_ready()
    updated = store.update_parcel(pid, patch)
    if not updated:
        raise HTTPException(404, "Parcel not found.")
    return updated


@router.delete("/parcels/{pid}")
def delete_parcel(pid: str):
    _require_ready()
    if not store.delete_parcel(pid):
        raise HTTPException(404, "Parcel not found.")
    return {"ok": True}


# ── Documents ─────────────────────────────────────────────────────────────────
@router.post("/parcels/{pid}/documents")
async def upload_document(pid: str, file: UploadFile = File(...)):
    _require_ready()
    if not store.get_parcel(pid):
        raise HTTPException(404, "Parcel not found.")
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
