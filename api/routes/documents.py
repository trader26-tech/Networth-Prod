"""
Document vault routes — nested folders + arbitrary files. The Land/Apartments/
Gold "linked" folders are assembled on the frontend from those domains' own
documents, so this router only owns the user's own folder tree.
"""
from __future__ import annotations

import io
from typing import Optional

from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..vault import store

router = APIRouter(prefix="/api/documents", tags=["documents"])

MAX_BYTES = 20 * 1024 * 1024   # 20 MB per file


def _guard():
    if not store.tables_ready():
        raise HTTPException(503, store.MIGRATION_HINT)


# ── folders ───────────────────────────────────────────────────────────────────
class FolderIn(BaseModel):
    name: str
    parent_id: Optional[str] = None


class RenameIn(BaseModel):
    name: str


@router.get("/folders")
def list_folders():
    _guard()
    return store.list_folders()


@router.post("/folders")
def create_folder(body: FolderIn):
    _guard()
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(400, "Folder name cannot be empty.")
    return store.create_folder(name, body.parent_id)


@router.patch("/folders/{folder_id}")
def rename_folder(folder_id: str, body: RenameIn):
    _guard()
    folder = store.rename_folder(folder_id, body.name)
    if not folder:
        raise HTTPException(404, "Folder not found.")
    return folder


@router.delete("/folders/{folder_id}")
def delete_folder(folder_id: str):
    _guard()
    if not store.delete_folder(folder_id):
        raise HTTPException(404, "Folder not found.")
    return {"ok": True}


# ── files ─────────────────────────────────────────────────────────────────────
@router.get("/folders/{folder_id}/files")
def list_files(folder_id: str):
    _guard()
    return store.list_files(folder_id)


@router.post("/folders/{folder_id}/files")
async def upload_file(folder_id: str, file: UploadFile = File(...)):
    _guard()
    content = await file.read()
    if len(content) > MAX_BYTES:
        raise HTTPException(413, "File too large (max 20 MB).")
    return store.add_file(folder_id, file.filename or "document", content,
                          file.content_type or "application/octet-stream")


@router.get("/files/{doc_id}")
def download_file(doc_id: str, preview: bool = False):
    _guard()
    resolved = store.open_file(doc_id, preview)
    if not resolved:
        raise HTTPException(404, "Document not found.")
    disposition = f'inline; filename="{resolved.get("filename") or "document"}"'
    return StreamingResponse(
        io.BytesIO(resolved["data"]),
        media_type=resolved.get("mime") or "application/octet-stream",
        headers={"Content-Disposition": disposition},
    )


@router.patch("/files/{doc_id}")
def rename_file(doc_id: str, body: RenameIn):
    _guard()
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(400, "Name cannot be empty.")
    doc = store.rename_file(doc_id, name)
    if not doc:
        raise HTTPException(404, "Document not found.")
    return doc


@router.delete("/files/{doc_id}")
def delete_file(doc_id: str):
    _guard()
    if not store.delete_file(doc_id):
        raise HTTPException(404, "Document not found.")
    return {"ok": True}
