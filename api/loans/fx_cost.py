"""
Real (₹-adjusted) cost of a foreign-currency loan.

A Kuwaiti-Dinar loan at 7.25% *feels* cheap — but you earn and measure wealth in
rupees, and the rupee keeps depreciating against the dinar. Every KWD you repay
costs more rupees than the one you borrowed, so the effective interest rate *in ₹
terms* is the nominal rate PLUS the annualised appreciation of the loan currency
against the rupee.

We reconstruct that currency path from USD/INR history (Frankfurter — deep, free,
no key) scaled by the loan currency's live peg to the dollar. Gulf currencies
(KWD, AED, SAR, QAR, OMR, BHD) are USD-basket-pegged, so USD/INR × peg is an
accurate KWD/INR series; for USD itself the peg is 1. Cached ~6h; degrades to
`available=False` if the feed can't be reached (the UI then hides the graph).
"""
from __future__ import annotations

import json
import time
import urllib.request
from datetime import date, datetime
from typing import Optional

from ..salary import fx as fx_mod

_FRANKFURTER = "https://api.frankfurter.dev/v1/{start}..{end}?base=USD&symbols=INR"
_TTL = 6 * 3600
_cache: dict = {"ts": 0.0, "series": None}   # monthly USD/INR: {"YYYY-MM": rate}


def _parse(d: Optional[str]) -> Optional[date]:
    if not d:
        return None
    s = str(d)[:10]
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _fetch_usd_inr_monthly(start: date) -> dict:
    """Month-end USD/INR from `start` to today, as {'YYYY-MM': rate}. Cached ~6h."""
    now = time.time()
    if _cache["series"] and (now - _cache["ts"]) < _TTL:
        return _cache["series"]
    url = _FRANKFURTER.format(start=start.isoformat(), end=date.today().isoformat())
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=12) as r:
        raw = json.loads(r.read().decode("utf-8"))
    monthly: dict[str, float] = {}
    for day, obj in sorted((raw.get("rates") or {}).items()):
        inr = obj.get("INR")
        if inr:
            monthly[day[:7]] = float(inr)     # later day in a month overwrites → month-end
    _cache["series"] = monthly
    _cache["ts"] = now
    return monthly


def fx_cost(loan: dict) -> dict:
    """The ₹-adjusted effective rate for one foreign loan, plus the month-by-month
    series that drives the graph. Returns {available: False} when it can't apply
    (INR loan, no start date / rate, or the FX feed is unreachable)."""
    cur = (loan.get("currency") or "INR").upper()
    nominal = loan.get("interest_rate")
    start = _parse(loan.get("start_date"))
    out = {"available": False, "currency": cur, "nominal_rate": nominal}
    if cur == "INR" or nominal is None or not start:
        return out

    try:
        usd_inr = _fetch_usd_inr_monthly(start)
    except Exception as e:
        out["error"] = str(e)[:120]
        return out
    if not usd_inr:
        return out

    rates = fx_mod.get_rates()
    live_cur = fx_mod.inr_per_unit(cur, rates)
    live_usd = fx_mod.inr_per_unit("USD", rates)
    peg = (live_cur / live_usd) if live_usd else 1.0     # e.g. KWD/USD ≈ 3.25

    months = sorted(usd_inr.keys())
    fx_start = usd_inr[months[0]] * peg
    maturity = _parse(loan.get("maturity_date"))

    series = []
    for i, mk in enumerate(months):
        fx = usd_inr[mk] * peg
        yrs = i / 12.0
        # annualised appreciation of the loan currency vs ₹ since the loan began.
        # A full year of runway is needed before the annualised figure is stable
        # (short windows blow tiny FX wiggles up into wild rates), so hold at the
        # nominal rate for the first 12 months, then let the real rate emerge.
        if yrs >= 1.0 and fx_start > 0:
            ann = (fx / fx_start) ** (1.0 / yrs) - 1.0
            eff = nominal + ann * 100.0
        else:
            ann, eff = 0.0, nominal
        series.append({"month": mk, "fx": round(fx, 2),
                       "nominal": nominal, "effective": round(eff, 2)})

    fx_now = series[-1]["fx"]
    yrs_total = max((date.today() - start).days / 365.25, 0.01)
    ann_total = (fx_now / fx_start) ** (1.0 / yrs_total) - 1.0 if fx_start > 0 else 0.0
    effective = round(nominal + ann_total * 100.0, 2)

    # optional dashed projection to maturity if the current drift holds
    projection = []
    if maturity and maturity > date.today():
        m_year, m_month = int(months[-1][:4]), int(months[-1][5:7])
        proj_months = (maturity.year - m_year) * 12 + (maturity.month - m_month)
        fx = fx_now
        for k in range(1, min(proj_months, 240) + 1):
            m_month += 1
            if m_month > 12:
                m_month = 1; m_year += 1
            fx = fx * (1.0 + ann_total) ** (1 / 12)
            projection.append({"month": f"{m_year:04d}-{m_month:02d}",
                               "fx": round(fx, 2), "nominal": nominal, "effective": effective})

    return {
        "available": True,
        "currency": cur,
        "nominal_rate": nominal,
        "effective_rate": effective,
        "currency_drag": round(effective - nominal, 2),      # extra %/yr from ₹ fall
        "fx_start": round(fx_start, 2),
        "fx_now": round(fx_now, 2),
        "fx_change_pct": round((fx_now / fx_start - 1.0) * 100.0, 2) if fx_start else 0.0,
        "start_month": months[0],
        "series": series,
        "projection": projection,
        "peg": round(peg, 4),
        "source": "Frankfurter USD/INR × live peg",
    }
