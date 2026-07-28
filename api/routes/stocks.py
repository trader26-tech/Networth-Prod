"""Stocks routes: upload a Zerodha tradebook, then get computed holdings, P&L,
XIRR and a Nifty comparison. Holdings are derived from the tradebook (FIFO);
current prices come from Kite (if connected) else Yahoo. See SUPABASE.md →
"Stocks tradebook table".
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import date
from typing import Optional

from fastapi import APIRouter, UploadFile, File, Form, HTTPException

from ..stocks import store
from ..stocks import tradebook_parser as parser
from ..stocks import engine
from ..stocks import prices as price_feed
from ..stocks import corp_actions as corp_actions_mod

router = APIRouter(prefix="/api/stocks", tags=["stocks"])


def _require_ready() -> None:
    if not store.tables_ready():
        raise HTTPException(503, store.MIGRATION_HINT)


@router.post("/upload")
async def upload(file: UploadFile = File(...), owner: str = Form("default")):
    _require_ready()
    name = (file.filename or "").lower()
    if not name.endswith((".xlsx", ".xlsm")):
        raise HTTPException(400, "Please upload a Zerodha tradebook .xlsx file.")
    content = await file.read()
    if not content:
        raise HTTPException(400, "Empty file.")
    tmp = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
    try:
        tmp.write(content); tmp.close()
        try:
            parsed = parser.parse_workbook(tmp.name)
        except Exception as e:
            raise HTTPException(400, f"Could not read tradebook: {e}")
    finally:
        try:
            os.remove(tmp.name)
        except OSError:
            pass

    if not parsed["trades"]:
        raise HTTPException(400, "No trades found — is this a Zerodha tradebook export?")
    account = parsed.get("client_id") or "unknown"
    res = store.add_trades(parsed["trades"], owner=owner, account=account)
    res.update({"period_from": parsed.get("period_from"), "period_to": parsed.get("period_to"),
                "parsed": len(parsed["trades"])})
    return res


@router.get("/owners")
def owners():
    _require_ready()
    return store.owners()


@router.get("/summary")
def summary(owner: Optional[str] = None, overrides: Optional[str] = None,
            corp_actions: Optional[str] = None, transfers: Optional[str] = None):
    """Holdings + P&L + XIRR + Nifty comparison for an owner (or all trades).

    `overrides` is an optional JSON object {SYMBOL: price} of manual prices the
    user typed for stocks the feeds couldn't match — these win over fetched.

    `corp_actions` maps a split/demerged symbol to the live tickers it became:
    {"TATAMOTORS": [{"symbol":"TMPV","mult":1},{"symbol":"TMCV","mult":1}]}.
    The original's effective price = Σ(mult × live price of each leg), so the
    held quantity & cost basis stay on the original row and value/P&L/XIRR
    remain correct. A 1:1 split is just {"RELIANCE":[{"symbol":"RELIANCE","mult":2}]}.
    """
    _require_ready()
    trades = store.list_trades(owner)
    if not trades:
        return {"empty": True, "owner": owner}

    # Layer 1: auto-adjust the tradebook for splits/bonuses before FIFO so the
    # present-day quantity (and avg cost) come out right.
    trades, auto_splits = corp_actions_mod.adjust_trades_for_splits(trades)

    holdings = engine.fifo_holdings(trades)
    items = [(s, h["exchange"]) for s, h in holdings.items() if h["open_qty"] > 1e-9]

    # Parse the user's manual demerger mappings, then merge the built-in table
    # (Layer 2) on top — the user's mapping always wins per symbol.
    user_ca: dict = {}
    if corp_actions:
        try:
            user_ca = json.loads(corp_actions) or {}
        except (ValueError, TypeError):
            user_ca = {}
    ca = corp_actions_mod.merge_demergers(user_ca)
    leg_items = []
    for legs in ca.values():
        for leg in (legs or []):
            sym = (leg or {}).get("symbol")
            if sym:
                leg_items.append((sym, (leg.get("exchange") or "NSE")))

    price_map, source = price_feed.get_current_prices(items + leg_items)

    # Derive each split/demerged original's effective price from its legs.
    split_adj = 0
    for old, legs in ca.items():
        total = 0.0
        ok = bool(legs)
        for leg in (legs or []):
            sym = (leg or {}).get("symbol")
            try:
                mult = float((leg or {}).get("mult") or 0)
            except (ValueError, TypeError):
                mult = 0.0
            px = price_map.get(sym) if sym else None
            if px is None or mult <= 0:
                ok = False
                break
            total += px * mult
        if ok and total > 0:
            price_map[old] = total
            split_adj += 1

    manual = 0
    if overrides:
        try:
            for s, v in (json.loads(overrides) or {}).items():
                fv = float(v)
                if fv > 0:
                    price_map[s] = fv
                    manual += 1
        except (ValueError, TypeError):
            pass
    if split_adj:
        source = f"{source} + {split_adj} split-adj"
    if manual:
        source = f"{source} + {manual} manual"

    tmap: dict = {}
    if transfers:
        try:
            tmap = {k: float(v) for k, v in (json.loads(transfers) or {}).items() if float(v) > 0}
        except (ValueError, TypeError):
            tmap = {}

    result = engine.build_portfolio(trades, price_map, today=date.today(), transfers=tmap)

    dates = [t["trade_date"] for t in trades if t.get("trade_date")]
    start = min(dates) if dates else None
    nifty = price_feed.nifty_summary(start)

    result.update({
        "owner": owner,
        "trade_count": len(trades),
        "period_from": start,
        "period_to": max(dates) if dates else None,
        "price_source": source,
        "nifty": nifty,
        "auto_splits": [{"symbol": s, "factor": round(f, 4)} for s, f in auto_splits.items()],
    })
    return result


@router.delete("/owner/{owner}")
def delete_owner(owner: str):
    _require_ready()
    return {"deleted": store.delete_owner(owner)}
