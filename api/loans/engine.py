"""
Derived metrics for loans, computed on read.

Foreign-currency loans (e.g. a Kuwaiti Dinar loan) are converted to INR at the
live FX rate so they subtract correctly from net worth. Everything here is pure
math over the stored rows — nothing is persisted.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from ..salary import fx as fx_mod


def _f(v, d: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


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


# monthly-equivalent factor for an EMI frequency (how many EMIs per month)
_PER_MONTH = {"monthly": 1.0, "quarterly": 1 / 3, "half_yearly": 1 / 6, "yearly": 1 / 12}


def enrich(row: dict, rates: Optional[dict] = None) -> dict:
    rates = rates if rates is not None else fx_mod.get_rates()
    cur = (row.get("currency") or "INR").upper()
    fx = fx_mod.inr_per_unit(cur, rates)

    closed = (row.get("status") or "active") == "closed"
    outstanding = 0.0 if closed else _f(row.get("outstanding_principal"))
    original = _f(row.get("original_amount"))
    emi = _f(row.get("emi_amount"))
    freq = row.get("emi_frequency") or "monthly"
    emi_monthly = 0.0 if closed else emi * _PER_MONTH.get(freq, 1.0)

    paid_principal = max(0.0, original - _f(row.get("outstanding_principal")))
    progress = (paid_principal / original) if original > 0 else (1.0 if closed else 0.0)
    if closed:
        progress = 1.0

    return {
        **row,
        "currency": cur,
        "fx_rate": round(fx, 4),
        "is_foreign": cur != "INR",
        "closed": closed,
        # native-currency figures kept as-is; *_inr are the net-worth numbers
        "outstanding_inr": round(outstanding * fx, 2),
        "original_inr": round(original * fx, 2),
        "emi_inr": round(emi * fx, 2),
        "emi_monthly_inr": round(emi_monthly * fx, 2),
        "paid_principal": round(paid_principal, 2),
        "paid_principal_inr": round(paid_principal * fx, 2),
        "progress": round(min(1.0, max(0.0, progress)), 4),
    }


def build_summary(rows: list[dict]) -> dict:
    rates = fx_mod.get_rates()
    loans = [enrich(r, rates) for r in rows]
    # sort: active first (largest outstanding), then closed
    loans.sort(key=lambda x: (x["closed"], -x["outstanding_inr"]))

    active = [l for l in loans if not l["closed"]]
    total_outstanding = sum(l["outstanding_inr"] for l in active)
    total_emi_monthly = sum(l["emi_monthly_inr"] for l in active)
    total_original = sum(l["original_inr"] for l in loans)
    total_paid = sum(l["paid_principal_inr"] for l in loans)

    # per-person outstanding + EMI (drives the dashboard's per-person carve-out)
    by_person: dict[str, dict] = {}
    for l in active:
        p = by_person.setdefault(l.get("owner") or "—",
                                 {"person": l.get("owner") or "—", "outstanding_inr": 0.0, "emi_monthly_inr": 0.0, "count": 0})
        p["outstanding_inr"] += l["outstanding_inr"]
        p["emi_monthly_inr"] += l["emi_monthly_inr"]
        p["count"] += 1

    return {
        "loans": loans,
        "count": len(loans),
        "active_count": len(active),
        "total_outstanding_inr": round(total_outstanding, 2),
        "total_emi_monthly_inr": round(total_emi_monthly, 2),
        "total_original_inr": round(total_original, 2),
        "total_repaid_inr": round(total_paid, 2),
        "repaid_pct": round(total_paid / (total_paid + total_outstanding), 4) if (total_paid + total_outstanding) > 0 else None,
        "by_person": sorted(by_person.values(), key=lambda x: -x["outstanding_inr"]),
        "has_foreign": any(l["is_foreign"] for l in loans),
    }
