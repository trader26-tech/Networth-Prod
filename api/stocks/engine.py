"""
Turn a flat list of trades into holdings, realised P&L, and money-weighted
returns (XIRR) — the core analytics for the stocks page.

Holdings are FIFO: sells consume the oldest buy lots first; whatever remains is
the current open position (with weighted-avg cost + earliest open-lot date for
per-stock CAGR). XIRR treats every buy as a dated outflow, every sell as an
inflow, and today's holdings value as a final inflow.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any, Optional


def _pdate(s: Any) -> Optional[date]:
    if not s:
        return None
    try:
        y, m, d = (int(x) for x in str(s)[:10].split("-"))
        return date(y, m, d)
    except Exception:
        return None


def fifo_holdings(trades: list[dict]) -> dict[str, dict]:
    """Per-symbol open position + realised P&L, FIFO-matched."""
    by_sym: dict[str, list] = defaultdict(list)
    for t in trades:
        by_sym[t["symbol"]].append(t)

    out: dict[str, dict] = {}
    for sym, ts in by_sym.items():
        ts = sorted(ts, key=lambda t: (t.get("trade_date") or "", t.get("order_time") or ""))
        lots: list[list] = []   # [qty, price, date]
        realized = 0.0
        buy_qty = sell_qty = 0.0
        for t in ts:
            q = float(t["quantity"]); p = float(t["price"])
            if t["trade_type"] == "buy":
                lots.append([q, p, t.get("trade_date")])
                buy_qty += q
            else:
                sell_qty += q
                rem = q
                while rem > 1e-9 and lots:
                    lot = lots[0]
                    take = min(rem, lot[0])
                    realized += take * (p - lot[1])
                    lot[0] -= take; rem -= take
                    if lot[0] <= 1e-9:
                        lots.pop(0)
        open_qty = sum(l[0] for l in lots)
        cost = sum(l[0] * l[1] for l in lots)
        first_date = next((l[2] for l in lots if l[2]), None)
        out[sym] = {
            "open_qty": round(open_qty, 4),
            "avg_cost": round(cost / open_qty, 4) if open_qty > 1e-9 else 0.0,
            "invested": round(cost, 2),
            "first_date": first_date,
            "realized": round(realized, 2),
            "buy_qty": buy_qty,
            "sell_qty": sell_qty,
            "isin": ts[0].get("isin"),
            "exchange": ts[-1].get("exchange"),
        }
    return out


def xirr(flows: list[tuple[date, float]]) -> Optional[float]:
    """Money-weighted annualised return: r where Σ amount/(1+r)^years = 0."""
    flows = [(d, a) for d, a in flows if d]
    if len(flows) < 2:
        return None
    d0 = min(d for d, _ in flows)
    yrs = [(d - d0).days / 365.0 for d, _ in flows]
    amts = [a for _, a in flows]

    def npv(r: float) -> float:
        s = 0.0
        for y, a in zip(yrs, amts):
            try:
                s += a / ((1.0 + r) ** y)
            except (OverflowError, ZeroDivisionError, ValueError):
                return float("inf")
        return s

    lo, hi = -0.9999, 10.0
    flo, fhi = npv(lo), npv(hi)
    if flo * fhi > 0:
        hi = 1000.0
        fhi = npv(hi)
        if flo * fhi > 0:
            return None
    for _ in range(200):
        mid = (lo + hi) / 2.0
        fm = npv(mid)
        if abs(fm) < 1e-4:
            return round(mid, 6)
        if flo * fm < 0:
            hi = mid
        else:
            lo = mid; flo = fm
    return round((lo + hi) / 2.0, 6)


def build_portfolio(trades: list[dict], price_map: dict[str, float],
                    today: Optional[date] = None,
                    transfers: Optional[dict] = None) -> dict:
    """Holdings + per-stock metrics + portfolio totals + XIRR.

    price_map: symbol -> current price (₹). Missing symbols leave value as None.
    transfers: symbol -> quantity moved OUT to another demat (off-market). Those
        shares leave this account at cost (no P&L), so qty/value/invested shrink
        but the move is treated as neutral for XIRR (valued at cost).
    """
    today = today or date.today()
    transfers = transfers or {}
    hold = fifo_holdings(trades)

    # cashflows for XIRR: buys out, sells in
    flows: list[tuple[date, float]] = []
    for t in trades:
        amt = -(t["quantity"] * t["price"]) if t["trade_type"] == "buy" else (t["quantity"] * t["price"])
        d = _pdate(t.get("trade_date"))
        if d:
            flows.append((d, amt))

    positions: list[dict] = []
    invested_all = invested_priced = current_total = realized_total = 0.0
    transferred_cost = 0.0
    priced = missing = 0

    for sym, h in hold.items():
        realized_total += h["realized"]
        if h["open_qty"] <= 1e-9:
            continue   # fully closed — realised P&L already counted, no holding row

        # Remove any quantity transferred out to another account (at cost).
        orig_qty = h["open_qty"]
        try:
            tq = max(0.0, min(float(transfers.get(sym) or 0), orig_qty))
        except (ValueError, TypeError):
            tq = 0.0
        qty = orig_qty - tq
        invested = h["invested"] * (qty / orig_qty) if orig_qty > 0 else 0.0
        transferred_cost += h["invested"] * (tq / orig_qty) if orig_qty > 0 else 0.0
        if qty <= 1e-9:
            continue   # entirely transferred away — no longer held here

        px = price_map.get(sym)
        invested_all += invested
        cur = (px * qty) if px else None
        if cur is not None:
            current_total += cur; invested_priced += invested; priced += 1
        else:
            missing += 1
        pnl = (cur - invested) if cur is not None else None
        fd = _pdate(h["first_date"])
        yrs = ((today - fd).days / 365.0) if fd else None
        cagr = None
        if cur and invested > 0 and yrs and yrs > 0.04:
            try:
                cagr = round((cur / invested) ** (1.0 / yrs) - 1.0, 6)
            except (ValueError, OverflowError):
                cagr = None
        positions.append({
            "symbol": sym, "isin": h["isin"], "exchange": h["exchange"],
            "qty": round(qty, 4), "avg_cost": h["avg_cost"], "invested": round(invested, 2),
            "transferred": round(tq, 4) if tq > 0 else 0,
            "price": round(px, 2) if px else None,
            "value": round(cur, 2) if cur is not None else None,
            "pnl": round(pnl, 2) if pnl is not None else None,
            "pnl_pct": (pnl / invested) if (pnl is not None and invested > 0) else None,
            "cagr": cagr,
            "first_date": h["first_date"],
            "holding_years": round(yrs, 2) if yrs else None,
        })

    invested_unpriced = invested_all - invested_priced
    # XIRR: value unpriced holdings AND transferred-out shares at cost (neutral)
    # so neither distorts the money-weighted return.
    final_value = current_total + invested_unpriced + transferred_cost
    if final_value > 0:
        flows.append((today, final_value))
    port_xirr = xirr(flows)

    # Compare like-for-like: gain is on the priced holdings only.
    gain = current_total - invested_priced
    positions.sort(key=lambda p: (p["value"] or 0), reverse=True)
    return {
        "positions": positions,
        "holdings_count": len(positions),
        "invested": round(invested_priced, 2),          # comparable basis for current_value
        "invested_all": round(invested_all, 2),          # cost of every open holding
        "invested_unpriced": round(invested_unpriced, 2),
        "current_value": round(current_total, 2),
        "unrealized_pnl": round(gain, 2),
        "unrealized_pct": (gain / invested_priced) if invested_priced > 0 else None,
        "realized_pnl": round(realized_total, 2),
        "xirr": port_xirr,
        "priced": priced, "missing_price": missing,
        "transferred_cost": round(transferred_cost, 2),
        "timeline": _invest_timeline(trades),
    }


def _invest_timeline(trades: list[dict]) -> list[dict]:
    """Per-month buy & sell value plus cumulative net capital deployed — a
    'when did I buy / sell and how much capital is carried' picture.

    Returns [{month, buys, sells, net, invested}] where `invested` is the
    running cumulative net (buys − sells, cost terms)."""
    monthly: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])   # month -> [buys, sells]
    for t in trades:
        d = _pdate(t.get("trade_date"))
        if not d:
            continue
        amt = float(t["quantity"]) * float(t["price"])
        key = f"{d.year:04d}-{d.month:02d}"
        if t["trade_type"] == "buy":
            monthly[key][0] += amt
        else:
            monthly[key][1] += amt
    series: list[dict] = []
    run = 0.0
    for k in sorted(monthly):
        b, s = monthly[k]
        run += b - s
        series.append({"month": k, "buys": round(b, 2), "sells": round(s, 2),
                       "net": round(b - s, 2), "invested": round(run, 2)})
    return series
