"""
Smart-money dashboard API.

Tracks bulk-deal / block-deal / SAST disclosures over time. User uploads
CSV chunks (up to 5,000 rows each), the backend dedupes against a master
file, and serves aggregated views: hottest stocks, top parties, category
splits.
"""
from __future__ import annotations

from typing import Optional

import json

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile

from api.fundamentals.smart_money import analytics, ingest

router = APIRouter(prefix="/api/smart-money", tags=["smart-money"])


def _parse_override(raw: str) -> dict:
    """Decode the mapping_override form field (JSON string → dict)."""
    if not raw:
        return {}
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict):
            # Keep only string→string entries (defensive)
            return {str(k): str(v) for k, v in obj.items() if v}
    except json.JSONDecodeError:
        pass
    return {}


@router.post("/preview")
async def preview_trades(
    file: UploadFile = File(...),
    mapping_override: str = Form("{}"),
):
    """Parse the uploaded CSV WITHOUT saving and return a diagnostic preview.
    Optionally accepts a `mapping_override` JSON form field of the shape
    {"stock": "Symbol", "value": "Total"} that overrides the auto-detected
    column matching — used by the manual-column-mapping UI."""
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(400, "Upload a .csv file")
    content = await file.read()
    if not content:
        raise HTTPException(400, "Empty file")
    try:
        return ingest.preview_upload(content, mapping_override=_parse_override(mapping_override))
    except Exception as e:
        raise HTTPException(500, f"Preview failed: {e}")


@router.post("/upload")
async def upload_trades(
    file: UploadFile = File(...),
    mapping_override: str = Form("{}"),
):
    """Accept a CSV chunk, save it into raw/, rebuild the master.
    If `mapping_override` is provided, the saved file is rewritten with
    canonical headers so future rebuilds don't need the override again."""
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(400, "Upload a .csv file")
    content = await file.read()
    if not content:
        raise HTTPException(400, "Empty file")
    try:
        stats = ingest.save_upload(
            file.filename, content,
            mapping_override=_parse_override(mapping_override),
        )
    except Exception as e:
        raise HTTPException(500, f"Ingest failed: {e}")
    return stats


@router.post("/rebuild")
def rebuild():
    """Force re-scan of raw/ and rewrite master_trades.csv."""
    return ingest.rebuild_master()


def _rows():
    return ingest.load_master()


@router.get("/summary")
def summary():
    """Header stats: total trades, unique parties, date range, category split."""
    return analytics.overall_summary(_rows())


@router.get("/hottest")
def hottest(
    days: Optional[int] = Query(90, description="Lookback window in days; 0 = all"),
    limit: int = Query(20, ge=1, le=100),
    sort_by: str = Query("net_flow",
                         description="net_flow | buy_value | net_breadth | deal_count | net_flow_neg"),
):
    """Top stocks ranked by chosen metric in the lookback window."""
    rows = _rows()
    return {
        "rows":    analytics.hottest_stocks(rows, days=days or None, limit=limit, sort_by=sort_by),
        "days":    days,
        "sort_by": sort_by,
    }


@router.get("/categories")
def categories(days: Optional[int] = Query(90)):
    """Breakdown of buy/sell flows by Category column (FII/DII/MF/Promoter/Other)."""
    return analytics.category_breakdown(_rows(), days=days or None)


@router.get("/parties")
def parties(
    days: Optional[int] = Query(90),
    limit: int = Query(20, ge=1, le=200),
):
    """Top parties by ₹ activity in the window. Foundation for the full
    'who's most profitable' leaderboard once we accumulate enough history."""
    return {"rows": analytics.top_parties(_rows(), days=days or None, limit=limit)}


@router.get("/stock/{ticker}")
def stock(ticker: str, days: Optional[int] = Query(None)):
    """Drill-down: all trades + aggregates for one stock."""
    detail = analytics.stock_detail(ticker, _rows(), days=days or None)
    if not detail.get("found"):
        raise HTTPException(404, f"No smart-money trades found for {ticker!r}")
    return detail
