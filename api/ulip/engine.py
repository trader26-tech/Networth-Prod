"""
Derive everything useful from a raw ULIP policy: lock-in end (start + 5y),
maturity (start + term), premiums paid / remaining, total invested, gain, and a
money-weighted XIRR over the staggered premiums → today's fund value.

Pure stdlib — no external date libs — so it runs anywhere the rest of the app
does. Mirrors the math style of api/bonds/engine.py.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

LOCK_IN_YEARS = 5

_PER_YEAR = {"monthly": 12, "quarterly": 4, "half_yearly": 2, "yearly": 1, "single": 1}


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
    # clamp day to month length
    for day in (d.day, 28, 29, 30, 31):
        try:
            return date(y, mo, min(day, d.day))
        except ValueError:
            continue
    return date(y, mo, 28)


def _years_between(a: date, b: date) -> float:
    return (b - a).days / 365.25


def _xirr(flows: list[tuple[date, float]]) -> Optional[float]:
    """Money-weighted IRR for dated cashflows. Bisection on NPV; None if it
    can't bracket a root (e.g. all same sign)."""
    flows = [(d, f) for d, f in flows if d and f]
    if len(flows) < 2:
        return None
    t0 = min(d for d, _ in flows)
    if not (any(f < 0 for _, f in flows) and any(f > 0 for _, f in flows)):
        return None

    def npv(r: float) -> float:
        return sum(f / (1.0 + r) ** (_years_between(t0, d)) for d, f in flows)

    lo, hi = -0.9499, 5.0
    flo, fhi = npv(lo), npv(hi)
    if flo * fhi > 0:
        return None
    for _ in range(100):
        mid = (lo + hi) / 2
        fm = npv(mid)
        if abs(fm) < 1e-6:
            return mid
        if flo * fm < 0:
            hi = mid
        else:
            lo, flo = mid, fm
    return (lo + hi) / 2


def _premium_dates(start: date, per_year: int, total_installments: int) -> list[date]:
    if total_installments <= 0:
        return []
    step = 12 // per_year if per_year else 12
    return [_add_months(start, i * step) for i in range(total_installments)]


def enrich(policy: dict, today: Optional[date] = None) -> dict:
    """Decorate one policy with derived dates, premium progress, gain and XIRR."""
    today = today or date.today()
    out = dict(policy)

    start = _pdate(policy.get("start_date"))
    term = policy.get("policy_term_years") or 0
    pay_term = policy.get("premium_paying_term_years") or 0
    premium = float(policy.get("premium_amount") or 0)
    freq = (policy.get("premium_frequency") or "yearly")
    per_year = _PER_YEAR.get(freq, 1)
    fund_value = float(policy.get("fund_value") or 0)

    # dates
    lock_in_end = _add_months(start, LOCK_IN_YEARS * 12) if start else None
    maturity = _add_months(start, int(round(term * 12))) if (start and term) else None
    out["lock_in_end"] = lock_in_end.isoformat() if lock_in_end else None
    out["maturity_date"] = maturity.isoformat() if maturity else None
    out["locked"] = bool(lock_in_end and today < lock_in_end)
    out["years_to_maturity"] = round(max(0.0, _years_between(today, maturity)), 1) if maturity else None
    out["years_to_lock_in_end"] = round(max(0.0, _years_between(today, lock_in_end)), 1) if lock_in_end else None

    # premium schedule
    if freq == "single":
        total_installments = 1
    else:
        total_installments = int(round(per_year * pay_term)) if pay_term else 0
    sched = _premium_dates(start, per_year, total_installments) if start else []
    paid_dates = [d for d in sched if d <= today]
    paid_count = len(paid_dates)
    total_count = len(sched)

    invested = round(premium * paid_count, 2)
    total_to_pay = round(premium * total_count, 2)
    out["premiums_paid_count"] = paid_count
    out["premiums_total_count"] = total_count
    out["invested"] = invested
    out["total_premiums"] = total_to_pay
    out["remaining_premiums"] = round(max(0.0, total_to_pay - invested), 2)
    out["remaining_premiums_count"] = max(0, total_count - paid_count)
    out["fully_paid"] = total_count > 0 and paid_count >= total_count
    # what you still commit to pay each year, if any
    out["annual_outflow"] = round(premium * per_year, 2) if (not out["fully_paid"] and freq != "single") else 0.0
    out["premiums_per_year"] = per_year

    # gain + return
    out["fund_value"] = round(fund_value, 2)
    out["gain"] = round(fund_value - invested, 2) if invested else None
    out["gain_pct"] = round((fund_value - invested) / invested, 4) if invested else None

    flows: list[tuple[date, float]] = [(d, -premium) for d in paid_dates]
    if fund_value > 0:
        flows.append((today, fund_value))
    out["xirr"] = _xirr(flows)

    return out


def build_summary(policies: list[dict], today: Optional[date] = None) -> dict:
    today = today or date.today()
    rows = [enrich(p, today) for p in policies]
    fund_total = sum(r["fund_value"] for r in rows)
    invested_total = sum(r["invested"] for r in rows)
    remaining_total = sum(r["remaining_premiums"] for r in rows)
    annual_outflow = sum(r["annual_outflow"] for r in rows)
    sum_assured_total = sum(float(r.get("sum_assured") or 0) for r in rows)

    # value-weighted XIRR across policies that have one
    w = c = 0.0
    for r in rows:
        if r.get("xirr") is not None and r["fund_value"] > 0:
            w += r["fund_value"]; c += r["fund_value"] * r["xirr"]
    blended_xirr = (c / w) if w > 0 else None

    rows.sort(key=lambda r: r["fund_value"], reverse=True)
    return {
        "count": len(rows),
        "fund_value": round(fund_total, 2),
        "invested": round(invested_total, 2),
        "gain": round(fund_total - invested_total, 2) if invested_total else None,
        "gain_pct": round((fund_total - invested_total) / invested_total, 4) if invested_total else None,
        "remaining_premiums": round(remaining_total, 2),
        "annual_outflow": round(annual_outflow, 2),
        "sum_assured": round(sum_assured_total, 2),
        "xirr": blended_xirr,
        "policies": rows,
    }
