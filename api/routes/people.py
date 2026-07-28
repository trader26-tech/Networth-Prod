"""People directory routes — the household's four canonical members + avatars.

  GET    /api/people                 → [{name, color, risk_profile, photo}]
  POST   /api/people/{name}/photo     → upload/replace a member's avatar
  DELETE /api/people/{name}/photo     → remove a member's avatar

Photos are stored inline as small JPEG data-URLs (see api/people/store.py), so
no object-storage bucket or extra table is needed.
"""
from __future__ import annotations

from fastapi import APIRouter, UploadFile, File, HTTPException

from ..people import store

router = APIRouter(prefix="/api/people", tags=["people"])

MAX_PHOTO_BYTES = 15 * 1024 * 1024


@router.get("")
def list_people():
    return store.get_people()


@router.post("/{name}/photo")
async def upload_photo(name: str, file: UploadFile = File(...)):
    content = await file.read()
    if not content:
        raise HTTPException(400, "Empty file.")
    if len(content) > MAX_PHOTO_BYTES:
        raise HTTPException(400, "Image too large (max 15 MB).")
    try:
        return store.set_photo(name, content, file.content_type)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.delete("/{name}/photo")
def delete_photo(name: str):
    try:
        return store.clear_photo(name)
    except ValueError as e:
        raise HTTPException(404, str(e))
