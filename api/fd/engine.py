"""
Derive everything useful from a raw FD: maturity date/amount, current value,
interest earned, monthly income, the payout calendar and progress through the
tenure.

Two flavours:
  • payout (non-cumulative): interest paid out every period → regular income;
    principal stays put and is returned at maturity.
  • cumulative: interest compounded and paid as a lump sum at maturity → the
    value accrues over time.

Pure stdlib. Mirrors the style of api/ulip/engine.py.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

_PER_YEAR = {"monthly": 12, "quarterly": 4, "half_yearly": 2, "yearly": 1}


def _pdate(s) -> Optional[date]:
    if not s:
        return None
    try:
        return date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return None


def _add_months(d: date, months: int) -> date:
    m = d.month - 1 + months
    y = d.year + m // 12
    mo = m % 12 + 1
    for day in (d.day, 31, 30, 29, 28):
        try:
            return date(y, mo, min(day, d.day))
        except ValueError:
            continue
    return date(y, mo, 28)


def _years_between(a: date, b: date) -> float:
    return (b - a).days / 365.25


def payout_schedule(fd: dict) -> list[dict]:
    """Interest-payout dates + amount for a non-cumulative FD (empty otherwise)."""
    if (fd.get("payout_type") or "payout") == "cumulative":
        return []
    start = _pdate(fd.get("start_date"))
    principal = float(fd.get("principal") or 0)
    rate = float(fd.get("interest_rate") or 0)
    tenure_m = int(fd.get("tenure_months") or 0)
    if not start or principal <= 0 or rate <= 0 or tenure_m <= 0:
        return []
    per_year = _PER_YEAR.get(fd.get("payout_frequency") or "quarterly", 4)
    step = max(1, 12 // per_year)
    maturity = _add_months(start, tenure_m)
    amount = round(principal * rate / 100.0 / per_year, 2)
    out: list[dict] = []
    i = 1
    while i <= 1200:
        d = _add_months(start, i * step)
        if d > maturity:
            break
        out.append({"date": d.isoformat(), "amount": amount})
        i += 1
    return out


def enrich(fd: dict, today: Optional[date] = None) -> dict:
    """Decorate one FD with maturity, value, income and progress."""
    today = today or date.today()
    out = dict(fd)

    start = _pdate(fd.get("start_date"))
    principal = float(fd.get("principal") or 0)
    rate = float(fd.get("interest_rate") or 0)
    tenure_m = int(fd.get("tenure_months") or 0)
    ptype = fd.get("payout_type") or "payout"
    pfreq = fd.get("payout_frequency") or "quarterly"
    cfreq = fd.get("compounding_frequency") or "quarterly"

    tenure_years = tenure_m / 12.0
    maturity = _add_months(start, tenure_m) if (start and tenure_m) else None
    matured = bool(maturity and today >= maturity)
    out["maturity_date"] = maturity.isoformat() if maturity else None
    out["matured"] = matured
    out["years_to_maturity"] = round(max(0.0, _years_between(today, maturity)), 2) if maturity else None
    if start and tenure_years > 0:
        elapsed = max(0.0, _years_between(start, min(today, maturity) if maturity else today))
        out["progress"] = round(min(1.0, elapsed / tenure_years), 4)
    else:
        elapsed = 0.0
        out["progress"] = 0.0

    annual_interest = principal * rate / 100.0
    out["principal"] = round(principal, 2)
    out["annual_interest"] = round(annual_interest, 2)
    out["effective_yield"] = round(rate / 100.0, 4)
    out["tenure_years"] = round(tenure_years, 2)

    if ptype == "cumulative":
        n = _PER_YEAR.get(cfreq, 4)
        rpp = rate / 100.0 / n
        maturity_amount = principal * (1 + rpp) ** (n * tenure_years) if (rate and tenure_years) else principal
        eff_years = min(elapsed, tenure_years)
        cur = principal * (1 + rpp) ** (n * eff_years) if rate else principal
        if matured:
            cur = maturity_amount
        out["maturity_amount"] = round(maturity_amount, 2)
        out["current_value"] = round(cur, 2)
        out["interest_earned"] = round(cur - principal, 2)
        out["monthly_income"] = 0.0
        out["payout_per_period"] = 0.0
        out["payouts_per_year"] = 0
        out["dashboard_cagr"] = round(rate / 100.0, 4)   # value grows at the rate
    else:  # payout / non-cumulative
        per_year = _PER_YEAR.get(pfreq, 4)
        out["maturity_amount"] = round(principal, 2)     # principal returned; interest already paid
        out["current_value"] = round(principal, 2)       # principal is yours throughout
        out["interest_earned"] = round(annual_interest * min(elapsed, tenure_years), 2)
        out["monthly_income"] = 0.0 if matured else round(annual_interest / 12.0, 2)
        out["payout_per_period"] = round(annual_interest / per_year, 2)
        out["payouts_per_year"] = per_year
        out["dashboard_cagr"] = 0.0                      # principal is flat; return is income

    return out


def build_summary(fds: list[dict], today: Optional[date] = None) -> dict:
    today = today or date.today()
    rows = [enrich(f, today) for f in fds]
    value_total = sum(r["current_value"] for r in rows)
    principal_total = sum(r["principal"] for r in rows)
    annual_interest = sum(r["annual_interest"] for r in rows if not r["matured"])
    monthly_income = sum(r["monthly_income"] for r in rows)

    # value-weighted average rate (active FDs)
    w = c = 0.0
    for r in rows:
        if not r["matured"] and r["current_value"] > 0:
            w += r["current_value"]; c += r["current_value"] * float(r.get("interest_rate") or 0)
    avg_rate = (c / w) if w > 0 else None

    # nearest upcoming maturity
    upcoming = [r for r in rows if r.get("maturity_date") and not r["matured"]]
    upcoming.sort(key=lambda r: r["maturity_date"])
    next_maturity = upcoming[0]["maturity_date"] if upcoming else None

    rows.sort(key=lambda r: r["current_value"], reverse=True)
    return {
        "count": len(rows),
        "value": round(value_total, 2),
        "principal": round(principal_total, 2),
        "annual_interest": round(annual_interest, 2),
        "monthly_income": round(monthly_income, 2),
        "avg_rate": round(avg_rate, 2) if avg_rate is not None else None,
        "next_maturity": next_maturity,
        "fds": rows,
    }
