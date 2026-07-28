"""
Listed-EQUITY capital-gains tax engine (FY2025-26 / AY2026-27) — the deterministic
brain behind the stock "TaxBot" chatbot. No LLM: every number is computed here so
the chat can be trusted.

Two data sources, deliberately kept separate:

  REALIZED (what you already owe) — from the Zerodha **Console Tax-P&L** XLSX that
      api/stocks/taxpnl.py parses into net short_term / long_term / intraday /
      dividends per client × financial-year. This is Zerodha's own realized sell
      P&L (the live Kite API only returns *today's* trades, never a full year).

  UNREALIZED (how to SAVE) — from the **tradebook** trades in api/stocks/store.py,
      FIFO-matched here into the open lots still held, each priced live (Yahoo).
      Per-lot buy dates give the holding period, which drives the three levers:
        • LT-crossover countdown  (hold past 12m: STCG 20% → LTCG 12.5%)
        • loss harvesting         (book a loss to offset realized gains)
        • ₹1.25L LTCG headroom     (realize long-term gains tax-free)

FY2025-26 rules used (Budget-2024, transfers on/after 23-Jul-2024):
  • STCG on listed equity (Sec 111A, STT-paid, held ≤12m)  = 20% flat
  • LTCG on listed equity (Sec 112A, held >12m)            = 12.5% on gain over ₹1.25L/yr
  • Surcharge on these is capped at 15%; 4% health-&-education cess on top.
  • Set-off: a short-term loss offsets STCG *or* LTCG; a long-term loss offsets
    LTCG only. Unabsorbed losses carry forward 8 years.
  • NRIs (residency='nri') pay the SAME 111A/112A rates and get the ₹1.25L 112A
    exemption, but the broker deducts TDS on the gain and they can't set the
    basic-exemption limit against these special-rate gains.

Estimates for planning — confirm with a chartered accountant before you file/act.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Optional

from . import engine as _te          # reuse gross_up / surcharge / cess

# ── rates & thresholds ────────────────────────────────────────────────────────
STCG_RATE = 0.20                     # Sec 111A, listed equity, post 23-Jul-2024
LTCG_RATE = 0.125                    # Sec 112A
LTCG_EXEMPT = 125_000.0             # ₹1.25 lakh/yr of equity LTCG is tax-free
LT_DAYS = 365                        # held MORE than this ⇒ long-term
CROSSOVER_WINDOW = 90               # flag ST lots within this many days of turning LT
HARVEST_MIN = 5_000.0              # ignore harvest/crossover ideas smaller than this


def _pdate(s) -> Optional[date]:
    if not s:
        return None
    try:
        y, m, d = (int(x) for x in str(s)[:10].split("-"))
        return date(y, m, d)
    except Exception:
        return None


# ── tax on a single realized bucket ───────────────────────────────────────────
def stcg_tax(gain: float, income_proxy: float) -> dict:
    """Tax on net short-term equity gain (Sec 111A, 20% + surcharge-cap-15% + cess)."""
    g = max(0.0, gain)
    out = _te.gross_up(g * STCG_RATE, income_proxy)
    out.update({"taxable": round(g), "rate": STCG_RATE, "rate_label": "20% (Sec 111A)"})
    return out


def ltcg_tax(gain_after_setoff: float, income_proxy: float) -> dict:
    """Tax on net long-term equity gain: exempt the first ₹1.25L, tax the rest at
    12.5% (+ surcharge cap 15% + cess)."""
    g = max(0.0, gain_after_setoff)
    exempt_used = min(g, LTCG_EXEMPT)
    taxable = max(0.0, g - LTCG_EXEMPT)
    out = _te.gross_up(taxable * LTCG_RATE, income_proxy)
    out.update({"gain": round(g), "exempt_used": round(exempt_used),
                "exempt_free_left": round(max(0.0, LTCG_EXEMPT - g)),
                "taxable": round(taxable), "rate": LTCG_RATE,
                "rate_label": "12.5% over ₹1.25L (Sec 112A)"})
    return out


def compute_liability(stcg: float, ltcg: float, other_income: float = 0.0,
                      residency: str = "resident") -> dict:
    """Full equity CG liability for one person for one FY, with loss set-off.

    stcg / ltcg are the NET realized figures (may be negative = a loss).
      • net STCL  → offsets LTCG first, remainder carries forward.
      • net LTCL  → offsets LTCG only (never STCG); carries forward.
    income_proxy for the surcharge band = other_income + the taxable gains.
    """
    stcg = float(stcg or 0.0)
    ltcg = float(ltcg or 0.0)
    other = float(other_income or 0.0)

    carry_stcl = carry_ltcl = 0.0
    ltcg_after = ltcg
    stcg_after = stcg

    if stcg < 0:                      # short-term loss: offset LTCG, then carry
        stcl = -stcg
        used = min(stcl, max(0.0, ltcg_after))
        ltcg_after -= used
        carry_stcl = round(stcl - used, 2)
        stcg_after = 0.0
    if ltcg < 0:                      # long-term loss: carries forward (LTCG-only)
        carry_ltcl = round(-ltcg, 2)
        ltcg_after = 0.0

    income_proxy = other + max(0.0, stcg_after) + max(0.0, ltcg_after)
    st = stcg_tax(stcg_after, income_proxy)
    lt = ltcg_tax(ltcg_after, income_proxy)
    total = st["total"] + lt["total"]

    return {
        "residency": residency,
        "stcg": round(stcg), "ltcg": round(ltcg),
        "stcg_after_setoff": round(stcg_after), "ltcg_after_setoff": round(ltcg_after),
        "carry_forward_stcl": carry_stcl, "carry_forward_ltcl": carry_ltcl,
        "stcg_tax": st, "ltcg_tax": lt,
        "total_tax": round(total),
        "ltcg_exempt_used": lt["exempt_used"],
        "ltcg_free_left": lt["exempt_free_left"],
        "nri_tds_note": ("Broker deducts TDS on these gains for an NRI; file to claim any excess back."
                         if residency == "nri" else None),
    }


# ── FIFO open lots from the tradebook (per-lot buy dates → holding period) ─────
def open_lots(trades: list[dict], today: Optional[date] = None) -> dict[str, list[dict]]:
    """FIFO-match sells against buys; return the still-open lots per symbol, each
    tagged with days held, long-term flag, and days-to-long-term."""
    today = today or date.today()
    by_sym: dict[str, list] = defaultdict(list)
    for t in trades:
        by_sym[t["symbol"]].append(t)

    out: dict[str, list[dict]] = {}
    for sym, ts in by_sym.items():
        ts = sorted(ts, key=lambda t: (str(t.get("trade_date") or ""), str(t.get("order_time") or "")))
        lots: list[list] = []          # [qty, price, date]
        for t in ts:
            try:
                q = float(t["quantity"]); p = float(t["price"])
            except (TypeError, ValueError, KeyError):
                continue
            if (t.get("trade_type") or "").lower() == "buy":
                lots.append([q, p, t.get("trade_date"), t.get("exchange"), t.get("isin")])
            else:
                rem = q
                while rem > 1e-9 and lots:
                    lot = lots[0]
                    take = min(rem, lot[0])
                    lot[0] -= take; rem -= take
                    if lot[0] <= 1e-9:
                        lots.pop(0)
        rows = []
        for qty, price, d, exch, isin in lots:
            if qty <= 1e-9:
                continue
            bd = _pdate(d)
            days = (today - bd).days if bd else None
            is_lt = (days is not None and days > LT_DAYS)
            rows.append({
                "qty": round(qty, 4), "buy_price": round(price, 4),
                "buy_date": d, "exchange": exch, "isin": isin,
                "days_held": days, "is_long_term": is_lt,
                "days_to_lt": (max(0, LT_DAYS + 1 - days) if (days is not None and not is_lt) else 0),
            })
        if rows:
            out[sym] = rows
    return out


def _price_items(lots: dict[str, list[dict]]) -> list[tuple]:
    items = []
    for sym, rows in lots.items():
        exch = next((r.get("exchange") for r in rows if r.get("exchange")), "NSE")
        items.append((sym, exch or "NSE"))
    return items


def unrealized(lots: dict[str, list[dict]], price_map: dict[str, float],
               realized_ltcg: float = 0.0) -> dict:
    """From open lots + live prices, derive the three tax-saving levers.

    realized_ltcg = LTCG already booked this FY (so headroom = ₹1.25L − that).
    """
    crossover: list[dict] = []
    harvest: list[dict] = []
    positions: list[dict] = []
    st_gain = st_loss = lt_gain = lt_loss = 0.0
    unpriced = 0

    for sym, rows in lots.items():
        px = price_map.get(sym)
        # per-symbol loss aggregation for harvesting (net across that symbol's lots)
        sym_st_pnl = sym_lt_pnl = 0.0
        sym_qty = 0.0
        sym_lt_qty = sym_cost = 0.0
        for r in rows:
            sym_lt_qty += r["qty"] if r["is_long_term"] else 0.0
            sym_cost += r["qty"] * r["buy_price"]
            if not px:
                unpriced += 1
                continue
            pnl = (px - r["buy_price"]) * r["qty"]
            sym_qty += r["qty"]
            if r["is_long_term"]:
                sym_lt_pnl += pnl
                if pnl >= 0: lt_gain += pnl
                else:        lt_loss += -pnl
            else:
                sym_st_pnl += pnl
                if pnl >= 0: st_gain += pnl
                else:        st_loss += -pnl
                # crossover: a short-term lot sitting in GAIN, close to turning LT
                if pnl > HARVEST_MIN and 0 < r["days_to_lt"] <= CROSSOVER_WINDOW:
                    saved = pnl * (STCG_RATE - LTCG_RATE)
                    crossover.append({
                        "symbol": sym, "qty": r["qty"], "buy_date": r["buy_date"],
                        "days_to_lt": r["days_to_lt"], "gain": round(pnl),
                        "tax_now": round(pnl * STCG_RATE), "tax_if_wait": round(pnl * LTCG_RATE),
                        "tax_saved": round(saved),
                    })
        if not px:
            continue
        sym_pnl = sym_st_pnl + sym_lt_pnl
        if sym_pnl < -HARVEST_MIN:      # a genuine unrealized loss worth booking
            # a booked ST loss saves at 20% (offsets STCG); an LT loss saves at 12.5%
            rate = STCG_RATE if sym_st_pnl < 0 and abs(sym_st_pnl) >= abs(sym_lt_pnl) else LTCG_RATE
            harvest.append({
                "symbol": sym, "qty": round(sym_qty, 4),
                "loss": round(-sym_pnl), "price": round(px, 2),
                "term": "short" if sym_st_pnl <= sym_lt_pnl else "long",
                "tax_saved": round(-sym_pnl * rate),
            })

        # holdings overview / picker row (only when priced)
        total_qty = round(sum(r["qty"] for r in rows), 4)
        positions.append({
            "symbol": sym, "qty": total_qty,
            "avg_cost": round(sym_cost / total_qty, 2) if total_qty else 0.0,
            "price": round(px, 2), "value": round(px * total_qty),
            "unrealized": round(sym_pnl),
            "lt_qty": round(sym_lt_qty, 4),
            "term": ("long" if sym_lt_qty >= total_qty - 1e-9 else
                     "short" if sym_lt_qty <= 1e-9 else "mixed"),
        })

    positions.sort(key=lambda p: -p["value"])
    crossover.sort(key=lambda x: -x["tax_saved"])
    harvest.sort(key=lambda x: -x["tax_saved"])
    headroom = round(max(0.0, LTCG_EXEMPT - max(0.0, float(realized_ltcg or 0.0))), 2)

    return {
        "unrealized_stcg": round(st_gain - st_loss),
        "unrealized_ltcg": round(lt_gain - lt_loss),
        "st_gain": round(st_gain), "st_loss": round(st_loss),
        "lt_gain": round(lt_gain), "lt_loss": round(lt_loss),
        "crossover": crossover,
        "harvest": harvest,
        "harvest_total_loss": round(sum(h["loss"] for h in harvest)),
        "harvest_total_saved": round(sum(h["tax_saved"] for h in harvest)),
        "crossover_total_saved": round(sum(c["tax_saved"] for c in crossover)),
        "ltcg_headroom": headroom,
        "unpriced_lots": unpriced,
        "positions": positions,
    }


def what_if_sell(lots_for_symbol: list[dict], qty: float, price: float,
                 income_proxy: float = 0.0) -> dict:
    """“If I sell N shares of X today at ₹P, what's the tax?” — FIFO over the open
    lots, splitting the gain into ST (20%) and LT (12.5%) and taxing each."""
    rem = float(qty)
    st_gain = lt_gain = 0.0
    matched = 0.0
    for r in sorted(lots_for_symbol, key=lambda x: str(x.get("buy_date") or "")):
        if rem <= 1e-9:
            break
        take = min(rem, r["qty"])
        pnl = (price - r["buy_price"]) * take
        if r["is_long_term"]:
            lt_gain += pnl
        else:
            st_gain += pnl
        rem -= take; matched += take
    proxy = income_proxy + max(0.0, st_gain) + max(0.0, lt_gain)
    st = stcg_tax(st_gain, proxy)
    lt = ltcg_tax(lt_gain, proxy)
    return {
        "qty_requested": qty, "qty_matched": round(matched, 4),
        "shortfall": round(max(0.0, qty - matched), 4),
        "price": price,
        "st_gain": round(st_gain), "lt_gain": round(lt_gain),
        "st_tax": st, "lt_tax": lt,
        "total_gain": round(st_gain + lt_gain),
        "total_tax": round(st["total"] + lt["total"]),
    }
