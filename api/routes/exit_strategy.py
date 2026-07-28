"""
Exit Strategy API.

Two endpoints:
  • POST /analyze — already-parsed holdings (frontend-side parse)
  • POST /upload  — raw xlsx/xls/csv portfolio file (server parses)

In both cases the server matches each holding against the Scorecard
master + computes an exit conviction per holding.
"""
from __future__ import annotations

import io
import re
from typing import Optional

from fastapi import APIRouter, Body, File, HTTPException, UploadFile

from api.exit_strategy import engine

router = APIRouter(prefix="/api/exit-strategy", tags=["exit-strategy"])


# Map fuzzy column names from broker exports → our canonical holding fields.
_COL_ALIASES: dict[str, list[str]] = {
    "share":          ["share", "name", "stock", "scrip", "instrument", "symbol"],
    "qty":            ["total qty", "quantity", "qty", "shares", "holding"],
    "avg_cost":       ["avg cmp", "avg cost", "average cost", "avg buy price",
                       "buy avg", "avg price", "avg. cost", "avg. price"],
    "current_value":  ["current value", "value", "market value", "cur. val.",
                       "current val"],
    "total_purchase": ["total purchase value", "investment", "invested",
                       "total invested"],
    "pl_pct":         ["p/l in %", "p/l %", "pnl %", "pnl in %", "p&l %",
                       "p&l in %", "net pl %", "p/l (%)", "pnl(%)"],
    "price":          ["price per share", "ltp", "cmp", "current price", "price"],
    "weight_pct":     ["weight", "wt", "weight %", "weight (%)", "% of portfolio"],
}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _to_num(v) -> Optional[float]:
    if v is None:
        return None
    s = str(v).strip()
    if not s or s in {"—", "-", "NA", "N/A", "null", "nan"}:
        return None
    s = re.sub(r"[,₹\s%]", "", s)
    try:
        return float(s)
    except ValueError:
        return None


def _map_portfolio_headers(headers: list[str]) -> dict[str, str]:
    """{canonical: original_header}"""
    out: dict[str, str] = {}
    for canonical, aliases in _COL_ALIASES.items():
        for alias in aliases:
            for h in headers:
                if _norm(h) == alias or alias in _norm(h):
                    out[canonical] = h
                    break
            if canonical in out:
                break
    return out


def _parse_holdings_file(content: bytes, filename: str) -> tuple[list[dict], dict]:
    """Parse an xlsx/xls/csv portfolio export into our holdings shape.
    Returns (holdings, debug_info)."""
    import pandas as pd
    fn = filename.lower()
    try:
        if fn.endswith(".csv"):
            df = pd.read_csv(io.BytesIO(content), dtype=object, keep_default_na=False)
        else:
            df = pd.read_excel(io.BytesIO(content), engine="openpyxl", dtype=object)
    except Exception as e:
        raise HTTPException(400, f"Could not parse file: {e}")

    # If the first row isn't headers (pandas might have promoted them), check
    # by looking for "SHARE" anywhere in df.iloc[0]; otherwise rerun with header=None.
    headers = [str(c).strip() for c in df.columns]
    mapping = _map_portfolio_headers(headers)

    # If we didn't even find a "share" column, the headers are probably in row 0
    if "share" not in mapping:
        if fn.endswith(".csv"):
            df = pd.read_csv(io.BytesIO(content), dtype=object, keep_default_na=False, header=None)
        else:
            df = pd.read_excel(io.BytesIO(content), engine="openpyxl", dtype=object, header=None)
        # promote row 0 to header
        df.columns = [str(c).strip() for c in df.iloc[0].tolist()]
        df = df.iloc[1:].reset_index(drop=True)
        headers = list(df.columns)
        mapping = _map_portfolio_headers(headers)

    if "share" not in mapping:
        raise HTTPException(400,
            f"Could not find a stock-name column in {headers!r}. "
            f"Expected something like SHARE / Stock / Symbol.")

    holdings: list[dict] = []
    for _, raw in df.iterrows():
        share = str(raw.get(mapping["share"]) or "").strip()
        if not share or share.lower() in {"share", "total", "grand total"}:
            continue
        h: dict = {"share": share}
        if "qty"            in mapping: h["qty"]            = _to_num(raw.get(mapping["qty"]))
        if "avg_cost"       in mapping: h["avg_cost"]       = _to_num(raw.get(mapping["avg_cost"]))
        if "current_value"  in mapping: h["current_value"]  = _to_num(raw.get(mapping["current_value"]))
        if "total_purchase" in mapping: h["total_purchase"] = _to_num(raw.get(mapping["total_purchase"]))
        if "pl_pct"         in mapping: h["pl_pct"]         = _to_num(raw.get(mapping["pl_pct"]))
        if "price"          in mapping: h["price"]          = _to_num(raw.get(mapping["price"]))
        if "weight_pct"     in mapping: h["weight_pct"]     = _to_num(raw.get(mapping["weight_pct"]))
        holdings.append(h)

    # If weight column wasn't found but we have current_value, derive it.
    if not any("weight_pct" in h and h["weight_pct"] is not None for h in holdings):
        total_val = sum((h.get("current_value") or 0) for h in holdings)
        if total_val > 0:
            for h in holdings:
                cv = h.get("current_value") or 0
                h["weight_pct"] = round((cv / total_val) * 100, 4)

    return holdings, {"detected_columns": mapping, "total_in_file": len(holdings)}


@router.post("/analyze")
def analyze(payload: dict = Body(...)):
    """JSON path — body: {"holdings": [...]} where each holding has
    share, qty, avg_cost, current_value, pl_pct, weight_pct."""
    holdings = payload.get("holdings") or []
    if not isinstance(holdings, list):
        raise HTTPException(400, "Expected `holdings` to be a list")
    return engine.analyze_holdings(holdings)


@router.post("/upload")
async def upload(file: UploadFile = File(...)):
    """File path — upload broker holdings export (xlsx/xls/csv).
    We parse it server-side, map columns, then run the same analysis."""
    if not file.filename or not file.filename.lower().endswith((".xlsx", ".xls", ".csv")):
        raise HTTPException(400, "Upload an .xlsx, .xls or .csv file")
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "Empty file")
    holdings, debug = _parse_holdings_file(raw, file.filename)
    result = engine.analyze_holdings(holdings)
    result["debug"] = debug
    result["filename"] = file.filename
    return result
