"""
CAS import routes — upload a CDSL/NSDL eCAS PDF, unlock it with the PAN,
preview what was found, then commit it into stocks + bonds.

Flow (deliberately two-step so nothing is written until you confirm):

    POST /api/cas/preview   file + pan  -> parsed holdings, reconciliation report
    POST /api/cas/commit    preview     -> writes stock_accounts/stock_holdings + bonds

The PAN is used only as the PDF password. It is never persisted and never
logged; the uploaded bytes are held in memory for the request only.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from ..cas import importer
from ..cas.parser import CasParseError, CasPasswordError, parse_cas

router = APIRouter(prefix="/api/cas", tags=["cas"])

_PAN_FMT = re.compile(r"^[A-Z]{5}\d{4}[A-Z]$")
_MAX_BYTES = 25 * 1024 * 1024  # eCAS PDFs are well under 25 MB


@router.post("/preview")
async def preview(
    file: UploadFile = File(...),
    pan: str = Form(...),
    owner: Optional[str] = Form(None),
):
    """Unlock + parse a CAS and return a draft import for review."""
    name = (file.filename or "").lower()
    if not name.endswith(".pdf"):
        raise HTTPException(400, "Please upload the CAS as a .pdf file.")

    pan_clean = (pan or "").strip().upper()
    if not pan_clean:
        raise HTTPException(400, "Enter the PAN — it is the CAS PDF password.")
    if not _PAN_FMT.match(pan_clean):
        raise HTTPException(
            400,
            "That does not look like a PAN. A PAN is 10 characters — "
            "5 letters, 4 digits, then 1 letter (e.g. ABCDE1234F).",
        )

    content = await file.read()
    if not content:
        raise HTTPException(400, "Empty file.")
    if len(content) > _MAX_BYTES:
        raise HTTPException(400, "That file is larger than 25 MB — is it really a CAS?")

    try:
        parsed = parse_cas(content, pan_clean)
    except CasPasswordError as e:
        raise HTTPException(400, str(e))
    except CasParseError as e:
        raise HTTPException(400, str(e))
    except Exception as e:  # unexpected — surface rather than 500 silently
        raise HTTPException(400, f"Could not read that CAS: {e}")

    result = importer.build_preview(parsed, owner=owner)

    # Never echo the PAN back to the client.
    inv = dict(result.get("investor") or {})
    if inv.get("pan"):
        inv["pan"] = f"{inv['pan'][:5]}****{inv['pan'][-1]}"
    result["investor"] = inv

    return result


class CommitIn(BaseModel):
    preview: dict[str, Any]
    sections: Optional[list[str]] = None   # ["holdings", "bonds"]


@router.post("/commit")
def commit(body: CommitIn):
    """Persist a reviewed (optionally edited) preview."""
    pv = body.preview or {}
    if not pv.get("holdings") and not pv.get("bonds"):
        raise HTTPException(400, "Nothing to import — the preview is empty.")

    what = set(body.sections or ["holdings", "bonds"])
    unknown = what - {"holdings", "bonds"}
    if unknown:
        raise HTTPException(400, f"Unknown section(s): {', '.join(sorted(unknown))}")

    report = importer.commit(pv, what=what)
    return report
