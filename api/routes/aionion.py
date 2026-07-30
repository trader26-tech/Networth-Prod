"""
Aionion holdings-import routes.

    POST /api/aionion/preview   file(.xlsx)  -> parsed equities/MF/bonds draft
    POST /api/aionion/commit    preview      -> writes stock_holdings + bonds

Two-step so nothing is written until the user reviews. Values are re-priced live
by the equity engine (by symbol) after import; the statement figures are only a
fallback until the first live refresh.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from ..aionion import importer
from ..aionion.parser import AionionParseError, parse_workbook

router = APIRouter(prefix="/api/aionion", tags=["aionion"])

_MAX_BYTES = 25 * 1024 * 1024


@router.post("/preview")
async def preview(file: UploadFile = File(...), owner: Optional[str] = Form(None)):
    name = (file.filename or "").lower()
    if not name.endswith((".xlsx", ".xlsm")):
        raise HTTPException(400, "Please upload the Aionion holdings .xlsx file.")
    content = await file.read()
    if not content:
        raise HTTPException(400, "Empty file.")
    if len(content) > _MAX_BYTES:
        raise HTTPException(400, "That file is larger than 25 MB.")
    try:
        parsed = parse_workbook(content)
    except AionionParseError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(400, f"Could not read that workbook: {e}")
    return importer.build_preview(parsed, owner=owner)


class CommitIn(BaseModel):
    preview: dict[str, Any]
    sections: Optional[list[str]] = None


@router.post("/commit")
def commit(body: CommitIn):
    pv = body.preview or {}
    if not pv.get("holdings") and not pv.get("bonds"):
        raise HTTPException(400, "Nothing to import — the preview is empty.")
    what = set(body.sections or ["holdings", "bonds"])
    unknown = what - {"holdings", "bonds"}
    if unknown:
        raise HTTPException(400, f"Unknown section(s): {', '.join(sorted(unknown))}")
    return importer.commit(pv, what=what)
