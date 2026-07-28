"""
Hedges API — first-class management of long protective puts.

Endpoints:
  GET    /api/hedges                    list (optional ?status=open|closed)
  GET    /api/hedges/{hid}              get a single hedge with live P&L
  POST   /api/hedges                    create a new hedge
  PUT    /api/hedges/{hid}              update fields
  DELETE /api/hedges/{hid}              hard-delete (rare — usually use close)
  POST   /api/hedges/{hid}/close        mark closed at a given price
  POST   /api/hedges/{hid}/roll         close current + create new in one shot
  POST   /api/hedges/{hid}/tag          tag a CC strategy to this hedge
  POST   /api/hedges/{hid}/untag        untag a CC strategy
  GET    /api/hedges/scan               scan chain for best hedge candidates

Time-weighted cost allocation across tagged strategies is computed in
allocated_cost_for_cc(hedge, cc_position).
"""
from __future__ import annotations
import math as _math
import datetime as _dt
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from api.core.chain import spot_for
from api import options_engine as opt_eng, state, hedges_store as _store
from api.routes.protected_wheel import (
    _bs_put,
    _bs_put_greeks,
    _fetch_hedge_chain,
    _rank_hedge_puts,
)
from api.routes.covered_call import _fetch_vix, _niftybees_price, NIFTYBEES_RATIO

router = APIRouter(prefix="/api/hedges", tags=["hedges"])


# ── Pydantic models ─────────────────────────────────────────────────────────

class HedgeIn(BaseModel):
    strike:       float
    expiry:       str
    lots:         int = 1
    lot_size:     int = 75
    premium_paid: float
    symbol:       str = ""
    notes:        str = ""
    tagged_strategies: list[str] = Field(default_factory=list)


class HedgeUpdate(BaseModel):
    notes:             str | None = None
    tagged_strategies: list[str] | None = None


class HedgeClose(BaseModel):
    close_price: float
    kind:        str = "closed"     # "closed" or "rolled"


class HedgeRoll(BaseModel):
    """Close existing hedge and open a new one in a single action."""
    close_price:    float
    new_strike:     float
    new_expiry:     str
    new_lots:       int = 1
    new_lot_size:   int = 75
    new_premium:    float
    new_symbol:     str = ""
    transfer_tags:  bool = True   # auto-transfer tagged_strategies to new hedge


class TagBody(BaseModel):
    strategy_id: str


# ── Live P&L enrichment ─────────────────────────────────────────────────────

def _live_put_price(strike: float, expiry: str, spot: float) -> float | None:
    """Best-effort live price for a put. Tries Kite quote first, falls back to BS."""
    kite = state.get_kite()
    if kite:
        try:
            from api.core.chain import _get_nfo_instruments
            instruments = _get_nfo_instruments()
            inst = next(
                (i for i in instruments
                 if str(i.get("name", "")).upper() == "NIFTY"
                 and i.get("instrument_type") == "PE"
                 and float(i["strike"]) == strike
                 and (i["expiry"].isoformat() if hasattr(i["expiry"], "isoformat")
                      else str(i["expiry"])[:10]) == expiry),
                None
            )
            if inst:
                q = kite.ltp([f"NFO:{inst['tradingsymbol']}"])
                p = q.get(f"NFO:{inst['tradingsymbol']}", {}).get("last_price")
                if p and p > 0:
                    return float(p)
        except Exception:
            pass
    # BS fallback
    T = max(opt_eng.days_to_expiry(expiry), 0) / 365.0
    if T <= 0:
        return max(strike - spot, 0.0)
    return _bs_put(spot, strike, T, iv=0.16)


def _enrich(h: dict, spot: float) -> dict:
    """Add live price, unrealized P&L, DTE, and roll-flag to a hedge dict."""
    out = dict(h)
    if h.get("status") == "open":
        try:
            current_price = _live_put_price(float(h["strike"]), h["expiry"], spot)
        except Exception:
            current_price = None
        out["current_price"] = round(current_price, 2) if current_price is not None else None
        qty = int(h.get("lots", 1)) * int(h.get("lot_size", 75))
        if current_price is not None:
            unrealized = (current_price - float(h["premium_paid"])) * qty
            out["unrealized_pnl"] = round(unrealized, 2)
        else:
            out["unrealized_pnl"] = None

        # DTE + roll status (Phase 2C — graded urgency)
        try:
            dte = max(opt_eng.days_to_expiry(h["expiry"]), 0)
        except Exception:
            dte = 0
        out["dte"] = dte
        out["should_roll"] = dte <= 30
        # Three-tier roll status — drives the colour pill on each hedge card
        if dte <= 30:
            out["roll_status"] = "now"      # 🔴 ROLL NOW
            out["roll_message"] = (
                f"DTE {dte} — roll IMMEDIATELY. Below 30 DTE, gamma + theta both accelerate. "
                f"Close this hedge and buy a fresh 75-100 DTE put at 5-8% OTM."
            )
        elif dte <= 45:
            out["roll_status"] = "soon"     # 🟡 plan a roll
            out["roll_message"] = (
                f"DTE {dte} — plan to roll within {dte - 30}d. Watch the next few sessions; "
                f"start scanning new candidates so you have a target ready."
            )
        else:
            out["roll_status"] = "hold"     # 🟢 hold
            out["roll_message"] = (
                f"DTE {dte} — hold. Theta is slow at this duration; nothing to do until DTE ≤ 45."
            )
    else:
        # Already closed; show stored realized P&L
        out["current_price"] = h.get("close_price")
        out["unrealized_pnl"] = h.get("realized_pnl")
        out["dte"] = 0
        out["should_roll"] = False
    return out


# ── Cost allocation (time-weighted) ─────────────────────────────────────────

def allocated_cost_for_cc(hedge: dict, cc_start_iso: str, cc_end_iso: str | None = None) -> float:
    """Time-weighted hedge cost allocated to a CC cycle.

    allocation = total_hedge_cost × (overlap_days / hedge_total_days)
    """
    qty = int(hedge.get("lots", 1)) * int(hedge.get("lot_size", 75))
    total_cost = float(hedge["premium_paid"]) * qty

    try:
        h_start = _dt.datetime.fromisoformat(hedge["created_at"][:19])
    except Exception:
        return 0.0
    try:
        h_end = _dt.datetime.fromisoformat(hedge["expiry"]) if isinstance(hedge["expiry"], str) else hedge["expiry"]
    except Exception:
        # Try parsing as date
        try:
            h_end = _dt.datetime.fromisoformat(hedge["expiry"][:10])
        except Exception:
            return 0.0
    hedge_days = max((h_end - h_start).days, 1)

    try:
        cc_start = _dt.datetime.fromisoformat(cc_start_iso[:19])
    except Exception:
        return 0.0
    cc_end = _dt.datetime.now()
    if cc_end_iso:
        try:
            cc_end = _dt.datetime.fromisoformat(cc_end_iso[:19])
        except Exception:
            pass

    overlap_start = max(h_start, cc_start)
    overlap_end   = min(h_end, cc_end)
    overlap_days  = max((overlap_end - overlap_start).days, 0)
    return round(total_cost * overlap_days / hedge_days, 2)


# ── Endpoints ───────────────────────────────────────────────────────────────

@router.get("")
def list_hedges(status: str = ""):
    spot = spot_for("NIFTY")
    items = _store.list_hedges(status or None)
    return {
        "spot":     round(spot, 2),
        "nb_price": round(_niftybees_price(spot) or spot / NIFTYBEES_RATIO, 2),
        "hedges":   [_enrich(h, spot) for h in items],
    }


@router.get("/{hid}")
def get_hedge(hid: str):
    h = _store.get_hedge(hid)
    if not h:
        raise HTTPException(404, "Hedge not found")
    spot = spot_for("NIFTY")
    return _enrich(h, spot)


@router.post("")
def create_hedge(body: HedgeIn):
    return _store.create_hedge(body.model_dump())


@router.put("/{hid}")
def update_hedge(hid: str, body: HedgeUpdate):
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    h = _store.update_hedge(hid, updates)
    if not h:
        raise HTTPException(404, "Hedge not found")
    return h


@router.delete("/{hid}")
def delete_hedge(hid: str):
    if not _store.delete_hedge(hid):
        raise HTTPException(404, "Hedge not found")
    return {"deleted": hid}


@router.post("/{hid}/close")
def close_hedge(hid: str, body: HedgeClose):
    h = _store.close_hedge(hid, body.close_price, body.kind)
    if not h:
        raise HTTPException(404, "Hedge not found")
    return h


@router.post("/{hid}/roll")
def roll_hedge(hid: str, body: HedgeRoll):
    """Close the existing hedge AND open a new one (transferring tags). Single transaction."""
    old = _store.get_hedge(hid)
    if not old:
        raise HTTPException(404, "Hedge not found")
    if old.get("status") != "open":
        raise HTTPException(400, "Hedge is not open")

    # Close old
    closed = _store.close_hedge(hid, body.close_price, kind="rolled")

    # Create new
    new_data = {
        "strike":       body.new_strike,
        "expiry":       body.new_expiry,
        "lots":         body.new_lots,
        "lot_size":     body.new_lot_size,
        "premium_paid": body.new_premium,
        "symbol":       body.new_symbol,
        "notes":        f"Rolled from {hid}",
        "tagged_strategies": list(old.get("tagged_strategies", [])) if body.transfer_tags else [],
        "rolled_from":  hid,
    }
    new_hedge = _store.create_hedge(new_data)
    return {"closed": closed, "new": new_hedge}


@router.post("/{hid}/tag")
def tag_strategy(hid: str, body: TagBody):
    h = _store.tag_strategy(hid, body.strategy_id)
    if not h:
        raise HTTPException(404, "Hedge not found")
    return h


@router.post("/{hid}/untag")
def untag_strategy(hid: str, body: TagBody):
    h = _store.untag_strategy(hid, body.strategy_id)
    if not h:
        raise HTTPException(404, "Hedge not found")
    return h


@router.get("/scan/candidates")
def scan_hedge_candidates(underlying: str = "NIFTY"):
    """Scan available expiries for ranked hedge candidates.

    Strategy:
      1. Try 60-180 DTE first (ideal: 3-month protocol)
      2. If empty, fall back to 45-180 DTE with a note flagging it
      3. If still empty, return diagnostic info so the user knows why

    Iterates ALL expiries (not just 5) so we don't miss listed-far-month
    expiries that NSE may publish irregularly.
    """
    from api.routes.options import _get_expiries
    spot     = spot_for(underlying)
    expiries = _get_expiries(underlying) or []

    # Track everything we tried, for the diagnostic panel.
    all_attempted: list[dict] = []

    def _scan_band(min_dte: int, max_dte: int = 200) -> list[dict]:
        """Scan ALL expiries (not [1:6]) and return chains in the DTE band."""
        out: list[dict] = []
        for exp in expiries:
            try:
                cd = _fetch_hedge_chain(underlying, exp, spot)
            except Exception as e:
                all_attempted.append({"expiry": exp, "dte": None, "strikes": 0,
                                       "skipped_reason": f"fetch error: {e}"})
                continue
            if not cd:
                all_attempted.append({"expiry": exp, "dte": None, "strikes": 0,
                                       "skipped_reason": "no chain (instruments missing or strikes outside 84-98% spot)"})
                continue
            dte = cd["dte"]
            if not cd["chain"]:
                all_attempted.append({"expiry": exp, "dte": dte, "strikes": 0,
                                       "skipped_reason": "empty chain after wide-PE filter"})
                continue
            if dte < min_dte or dte > max_dte:
                all_attempted.append({"expiry": exp, "dte": dte, "strikes": len(cd["chain"]),
                                       "skipped_reason": f"dte outside band {min_dte}-{max_dte}"})
                continue
            out.append({"expiry": exp, "dte": dte, "chain": cd["chain"]})
        return out

    # ── Pass 1: ideal 3-month band ─────────────────────────────────────────
    hedge_chains_raw = _scan_band(60, 200)
    fallback_used = False
    fallback_message = None

    # ── Pass 2: graceful fallback to 45 DTE if pass 1 empty ────────────────
    if not hedge_chains_raw:
        all_attempted = []  # reset, retry with looser band
        hedge_chains_raw = _scan_band(45, 200)
        if hedge_chains_raw:
            fallback_used = True
            fallback_message = (
                "⚠ No 3-month (≥60 DTE) hedge candidates available right now — "
                "showing 45-60 DTE alternatives instead. Re-scan in a few weeks "
                "when farther-month expiries get listed by NSE."
            )

    ranked = _rank_hedge_puts(spot, hedge_chains_raw)

    # If still empty, build a clear diagnostic message for the UI
    diagnostic = None
    if not ranked:
        diagnostic = (
            f"No hedge candidates found across {len(expiries)} listed expiries. "
            f"Likely cause: NSE hasn't published far-month deep-OTM put strikes "
            f"in the 84-98% spot range yet. Check the 'attempted' list below."
        )

    return {
        "spot":            round(spot, 2),
        "candidates":      ranked,
        "best":            ranked[0] if ranked else None,
        "scan_timestamp":  _dt.datetime.now().isoformat(timespec="seconds"),
        "expiries_scanned": [{"expiry": ec["expiry"], "dte": ec["dte"], "strikes": len(ec["chain"])}
                             for ec in hedge_chains_raw],
        "expiries_attempted": all_attempted,
        "fallback_used":     fallback_used,
        "fallback_message":  fallback_message,
        "diagnostic":        diagnostic,
        "min_dte_used":      45 if fallback_used else 60,
    }


@router.get("/for-strategy/{strategy_id}")
def hedges_for_strategy(strategy_id: str):
    """All hedges (open or closed) tagged to a given CC strategy."""
    spot = spot_for("NIFTY")
    items = _store.hedges_for_strategy(strategy_id)
    enriched = [_enrich(h, spot) for h in items]
    # Compute allocated cost per active hedge
    for h in enriched:
        if h.get("status") == "open":
            # Use hedge created_at as cc_start_iso; allocation up to NOW
            h["allocated_cost_to_strategy"] = allocated_cost_for_cc(h, h["created_at"])
        else:
            h["allocated_cost_to_strategy"] = h.get("realized_pnl") or 0.0
    return {"strategy_id": strategy_id, "hedges": enriched}
