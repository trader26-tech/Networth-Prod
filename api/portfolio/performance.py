"""
Portfolio-vs-Nifty backtest.

Reconstructs how **your current holdings** would have moved over the past
month / year by valuing them against historical daily closes (free Yahoo spark),
and lines them up against the Nifty-50 for comparison. It's a "what if I'd held
today's portfolio" curve — holdings that changed over time aren't reflected.

Result is cached per period (history barely moves intraday).
"""
from __future__ import annotations

import time
import threading
from datetime import date, timedelta
from typing import Optional

from . import store
from ..stocks import prices as price_feed
from ..salary import fx as fx_mod

_PERIODS = {"1mo": 31, "6mo": 186, "1y": 366}
_cache: dict[str, tuple[dict, float]] = {}
_TTL = 3600.0
_lock = threading.Lock()


def _agg_holdings(account_ids: Optional[set] = None) -> dict[tuple, float]:
    """Aggregate quantity per (symbol, exchange, currency), optionally limited to
    a set of account ids (None = all accounts)."""
    m: dict[tuple, float] = {}
    for h in store.list_holdings():
        if account_ids is not None and h.get("account_id") not in account_ids:
            continue
        s = h.get("symbol")
        if not s:
            continue
        key = (s, h.get("exchange"), (h.get("currency") or "INR").upper())
        try:
            m[key] = m.get(key, 0.0) + float(h.get("quantity") or 0)
        except (TypeError, ValueError):
            pass
    return {k: v for k, v in m.items() if v > 0}


def _scoped_values(account_ids: Optional[set]) -> tuple[dict, float]:
    """Per-symbol current ₹ value and total, for the selected accounts (live)."""
    by_sym: dict[str, float] = {}
    total = 0.0
    try:
        from . import engine
        for d in engine.build_summary().get("holdings_detail", []):
            if account_ids is not None and d.get("account_id") not in account_ids:
                continue
            by_sym[d.get("symbol")] = by_sym.get(d.get("symbol"), 0.0) + (d.get("value") or 0.0)
            total += d.get("value") or 0.0
    except Exception:
        pass
    return by_sym, total


def build(period: str = "1y", account_ids: Optional[list] = None, refresh: bool = False) -> dict:
    period = period if period in _PERIODS else "1y"
    ids = set(account_ids) if account_ids else None
    ckey = f"{period}|{','.join(sorted(ids)) if ids else 'all'}"
    # refresh=True bypasses the cache — used after you add capital / trades so the
    # curve is rebuilt from your CURRENT holdings instead of the ≤1h-old snapshot.
    with _lock:
        c = _cache.get(ckey)
        if c and not refresh and (time.time() - c[1]) < _TTL:
            return c[0]

    agg = _agg_holdings(ids)
    nifty = price_feed.nifty_series()
    if not agg or not nifty:
        out = {"period": period, "points": [], "coverage": 0}
        with _lock:
            _cache[ckey] = (out, time.time())
        return out

    cutoff = nifty[-1][0] - timedelta(days=_PERIODS[period])
    axis = [(d, lvl) for d, lvl in nifty if d >= cutoff]

    rng = period if period in ("1mo", "6mo", "1y") else "1y"
    items = [(s, e) for (s, e, _c) in agg.keys()]
    hist = price_feed.history(items, rng)
    # the long-range feed lags 1–2 days for many symbols; overlay the accurate
    # 5-day closes so the recent tail (and the day-over-day at the tip) is correct.
    try:
        recent = price_feed.history(items, "5d")
        for sym in list(set(hist) | set(recent)):
            merged = dict(hist.get(sym, []))
            merged.update(dict(recent.get(sym, [])))
            hist[sym] = sorted(merged.items())
    except Exception:
        pass
    rates = fx_mod.get_rates()

    # forward-fill each symbol's last close along the (ascending) Nifty date axis.
    # BACK-FILL the seed with each symbol's EARLIEST known close so a holding whose
    # history only covers part of the window (e.g. the feed returns 5-day data but
    # no 1-year series) is valued as if held at that earliest price the whole time —
    # NOT 0 then a phantom step-up when its history begins (that's the "spike when I
    # added capital" bug). "Consider I held this position in the past" = held flat
    # at the first price we actually have.
    idx = {k: 0 for k in agg}
    last = {k: (hist.get(k[0])[0][1] if hist.get(k[0]) else None) for k in agg}
    fxr = {k: fx_mod.inr_per_unit(k[2], rates) for k in agg}
    covered = set()
    points = []
    for d, lvl in axis:
        val = 0.0
        for k, qty in agg.items():
            ser = hist.get(k[0]) or []
            i = idx[k]
            while i < len(ser) and ser[i][0] <= d:
                last[k] = ser[i][1]; i += 1
            idx[k] = i
            c = last[k]
            if c is None:
                continue
            covered.add(k[0])
            val += qty * c * fxr[k]
        points.append({"date": d.isoformat(), "value": round(val, 2), "nifty": round(lvl, 2)})

    # drop leading dates before any holding has a historical close (value still 0)
    first = next((i for i, p in enumerate(points) if p["value"] > 0), len(points))
    points = points[first:]

    # NOTE: we deliberately do NOT append a synthetic live "today" point. The
    # chart is a record of daily closes; the last point is the last *published*
    # close. Adding a live current-day tip while the daily-close feed lagged a
    # trading day showed "today" with the prior day missing — confusing and
    # inconsistent. The page's live LTP covers "right now"; this chart shows
    # closed days only, each at its real date/value.

    # coverage by *value* (which holdings the line actually represents), scoped to
    # the selected accounts — so we can list exactly what was excluded.
    by_sym, total_value = _scoped_values(ids)
    # count DISTINCT symbols (not symbol×exchange×currency tuples) so the
    # covered / excluded / total figures all reconcile (covered + excluded = total).
    all_syms = {k[0] for k in agg}
    not_covered = all_syms - covered
    missing = [{"symbol": s, "value": round(by_sym.get(s, 0.0), 2)} for s in not_covered]
    missing.sort(key=lambda x: -x["value"])
    missing_value = round(sum(m["value"] for m in missing), 2)
    covered_value = round(max(0.0, total_value - missing_value), 2)

    # Add the holdings the line can't track (no price history) at their current
    # value, held FLAT across the period — so the chart's level is in the right
    # ballpark without distorting the day-to-day shape. We add only this untracked
    # constant; we do NOT peg the last point to live net worth (that would make the
    # last close display today's value). Each point keeps its own date's value, so
    # the final point is genuinely the last close — not "today".
    if missing_value and points:
        points = [{**p, "value": round(p["value"] + missing_value, 2)} for p in points]

    out = {
        "period": period,
        "points": points,
        "symbols": len(all_syms),                 # distinct stocks held
        "coverage": len(covered),                 # distinct stocks with price history
        "total_value": round(total_value, 2),
        "covered_value": covered_value,
        "missing_value": missing_value,
        "coverage_value_pct": round(covered_value / total_value, 4) if total_value else 0,
        "missing": missing[:80],
        "missing_count": len(not_covered),
        "start": points[0] if points else None,
        "end": points[-1] if points else None,
        "note": "Your current holdings valued at historical prices; stocks without price history are held flat at today's value.",
    }
    with _lock:
        _cache[ckey] = (out, time.time())
    return out


def holdings_on(date_iso: str, period: str = "1y", account_ids: Optional[list] = None) -> dict:
    """Per-stock valuation used by the backtest *on a specific date* — so the user
    can audit exactly which holdings and prices built the line."""
    period = period if period in _PERIODS else "1y"
    agg = _agg_holdings(set(account_ids) if account_ids else None)
    try:
        y, m, d = (int(x) for x in str(date_iso)[:10].split("-"))
        target = date(y, m, d)
    except Exception:
        return {"date": date_iso, "rows": [], "total": 0, "count": 0, "missing": [], "missing_count": 0}
    if not agg:
        return {"date": date_iso, "rows": [], "total": 0, "count": 0, "missing": [], "missing_count": 0}

    rng = period if period in ("1mo", "6mo", "1y") else "1y"
    hist = price_feed.history([(s, e) for (s, e, _c) in agg.keys()], rng)
    rates = fx_mod.get_rates()
    rows, total, missing = [], 0.0, []
    for (s, e, cur), qty in agg.items():
        ser = hist.get(s) or []
        close = None
        for dt, c in ser:                       # ascending → last close on/before target
            if dt <= target:
                close = c
            else:
                break
        if close is None:
            missing.append(s)
            continue
        fxr = fx_mod.inr_per_unit(cur, rates)
        val = qty * close * fxr
        total += val
        rows.append({"symbol": s, "exchange": e or "NSE", "currency": cur,
                     "qty": round(qty, 2), "close": round(close, 2), "value": round(val, 2)})
    rows.sort(key=lambda x: -x["value"])
    miss = sorted(set(missing))
    return {"date": date_iso, "rows": rows, "total": round(total, 2), "count": len(rows),
            "missing": miss, "missing_count": len(miss)}
