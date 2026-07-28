"""
Scorecard API — 6-category ranked leaderboard of stocks against their
sub-sector peers. User uploads Tickertape CSVs (per-sector); the master
file accumulates over time. Weights are adjustable per request so the UI
can let the user re-rank without re-uploading.
"""
from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile

from api.fundamentals.scorecard import ingest, scorer

router = APIRouter(prefix="/api/scorecard", tags=["scorecard"])


def _parse_weights(raw: str) -> Optional[dict]:
    if not raw:
        return None
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict):
            return {str(k): float(v) for k, v in obj.items()}
    except (json.JSONDecodeError, ValueError, TypeError):
        pass
    return None


@router.post("/preview")
async def preview(file: UploadFile = File(...)):
    """Diagnostic preview — show column mapping + warnings before save."""
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(400, "Upload a .csv file")
    content = await file.read()
    return ingest.preview_upload(content)


@router.post("/upload")
async def upload(file: UploadFile = File(...)):
    """Save CSV → raw/ + merge into master_scorecard.csv (dedup on Ticker)."""
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(400, "Upload a .csv file")
    content = await file.read()
    if not content:
        raise HTTPException(400, "Empty file")
    try:
        return ingest.save_upload(file.filename, content)
    except Exception as e:
        raise HTTPException(500, f"Upload failed: {e}")


@router.post("/rebuild")
def rebuild():
    return ingest.rebuild_master()


@router.delete("/clear")
def clear():
    """Wipe everything — raw uploads and master file."""
    return ingest.clear_all()


@router.get("/files")
def list_files():
    """List all persisted raw uploads with timestamp + row count."""
    return {"files": ingest.list_files()}


@router.delete("/files/{name}")
def delete_file(name: str):
    """Delete one raw upload, then rebuild master from the remaining files."""
    return ingest.delete_file(name)


@router.get("/summary")
def summary():
    return scorer.summary(ingest.load_master())


@router.get("/presets")
def presets():
    """Built-in weight preset modes."""
    return {
        "default_weights": scorer.DEFAULT_WEIGHTS,
        "presets":         scorer.WEIGHT_PRESETS,
        "categories":      list(scorer.CATEGORIES.keys()),
        "category_labels": scorer.CATEGORY_LABELS,
        "metrics":         scorer.CATEGORIES,
    }


@router.get("/leaderboard")
def leaderboard(
    weights:    str = Query("",  description="JSON weights override, e.g. {\"valuation\":30,...}"),
    sub_sector: str = Query("",  description="Filter by exact sub-sector (optional)"),
    cap_tier:   str = Query("",  description="large|mid|small (optional)"),
    search:     str = Query("",  description="Substring match on name/ticker"),
    limit:      int = Query(0, ge=0, le=10_000, description="0 = no limit"),
):
    rows = ingest.load_master()
    if not rows:
        return {"rows": [], "available": False, "weights_used": scorer.DEFAULT_WEIGHTS}
    parsed_weights = _parse_weights(weights)
    scored = scorer.score_universe(rows, parsed_weights)

    if sub_sector:
        scored = [s for s in scored if s["sub_sector"].lower() == sub_sector.lower()]
    if cap_tier:
        scored = [s for s in scored if s["cap_tier"] == cap_tier.lower()]
    if search:
        q = search.lower().strip()
        scored = [s for s in scored
                  if q in (s.get("name") or "").lower()
                  or q in (s.get("ticker") or "").lower()]

    # Re-rank after filters (composite already sorted desc by score_universe)
    for i, s in enumerate(scored):
        s["rank"] = i + 1

    if limit:
        scored = scored[:limit]
    return {
        "rows": scored, "available": True,
        "count": len(scored),
        "weights_used": scorer._normalize_weights(parsed_weights),
    }


@router.get("/stock/{ticker}")
def stock(ticker: str, weights: str = Query("")):
    rows = ingest.load_master()
    if not rows:
        raise HTTPException(404, "No scorecard data uploaded yet")
    parsed = _parse_weights(weights)
    target = (ticker or "").upper().strip()
    # Match by Ticker
    match = next((r for r in rows if (r.get("Ticker") or "").upper().strip() == target), None)
    if not match:
        raise HTTPException(404, f"No data for {ticker!r}")
    peers = scorer.compute_sector_peers(rows)
    w = scorer._normalize_weights(parsed)
    return scorer.score_stock(match, peers, w)
