"""
Turn raw other-income rows into a dashboard-ready summary: each row's monthly
₹-equivalent (currency-converted + frequency-normalised) for recurring
projections, the full converted amount for logged receipts, plus splits by
category and person.

Currency conversion reuses the salary FX feed (live KWD→INR etc.).
"""
from __future__ import annotations

from typing import Callable, Optional

from ..salary import fx

# multiplier to turn an amount at a given frequency into a per-MONTH figure
_MONTHLY_FACTOR = {
    "weekly": 52.0 / 12.0,
    "monthly": 1.0,
    "quarterly": 1.0 / 3.0,
    "half_yearly": 1.0 / 6.0,
    "yearly": 1.0 / 12.0,
    "one_time": 0.0,   # not recurring — excluded from the monthly figure
}


def monthly_native(row: dict) -> float:
    """Per-month amount in the row's own currency (one-time → 0)."""
    amt = float(row.get("amount") or 0)
    return amt * _MONTHLY_FACTOR.get(row.get("frequency") or "monthly", 1.0)


def enrich(row: dict, rates: Optional[dict] = None) -> dict:
    rates = rates or fx.get_rates()
    cur = (row.get("currency") or "INR").upper()
    m_native = monthly_native(row)
    m_inr = fx.to_inr(m_native, cur, rates)
    amt = float(row.get("amount") or 0)
    out = dict(row)
    out["currency"] = cur
    out["monthly_native"] = round(m_native, 2)
    out["monthly_inr"] = round(m_inr, 2)
    out["annual_inr"] = round(m_inr * 12, 2)
    out["amount_inr"] = round(fx.to_inr(amt, cur, rates), 2)   # raw amount in ₹
    out["inr_per_unit"] = round(fx.inr_per_unit(cur, rates), 4)
    out["is_foreign"] = cur != "INR"
    out["one_time"] = (row.get("frequency") == "one_time")
    return out


def log_summary(rows: list[dict], canon: Optional[Callable[[str], str]] = None) -> dict:
    """Actual logged receipts (each a single dated occurrence) → totals + splits.
    Uses the full converted amount (not a monthly-normalised figure)."""
    rates = fx.get_rates()
    canon = canon or (lambda s: (s or "—").strip().title() or "—")
    enriched: list[dict] = []
    by_category: dict[str, float] = {}
    by_person: dict[str, float] = {}
    by_currency: dict[str, float] = {}
    currencies: set[str] = set()
    total = 0.0

    for e in rows:
        row = enrich(e, rates)
        person = canon(e.get("owner"))
        row["owner"] = person
        enriched.append(row)
        amt = row["amount_inr"]
        currencies.add(row["currency"])
        total += amt
        cat = (e.get("category") or "Uncategorised").strip() or "Uncategorised"
        by_category[cat] = by_category.get(cat, 0.0) + amt
        by_person[person] = by_person.get(person, 0.0) + amt
        by_currency[row["currency"]] = by_currency.get(row["currency"], 0.0) + amt

    categories = sorted(
        ({"category": k, "monthly_inr": round(v, 2),
          "pct": (v / total) if total > 0 else 0} for k, v in by_category.items()),
        key=lambda x: x["monthly_inr"], reverse=True,
    )
    people = sorted(
        ({"person": k, "monthly_inr": round(v, 2)} for k, v in by_person.items()),
        key=lambda x: x["monthly_inr"], reverse=True,
    )
    enriched.sort(key=lambda x: (x.get("on_date") or "", x.get("created_at") or ""), reverse=True)
    return {
        "count": len(enriched),
        "monthly_total": round(total, 2),     # "total received" for the period
        "annual_total": round(total, 2),
        "by_category": categories,
        "by_person": people,
        "by_currency": [{"currency": k, "monthly_inr": round(v, 2)} for k, v in
                        sorted(by_currency.items(), key=lambda kv: kv[1], reverse=True)],
        "currencies": sorted(currencies),
        "has_foreign": any(c != "INR" for c in currencies),
        "entries": enriched,
        "fx": {"ok": rates.get("ok", False), "stale": rates.get("stale", False),
               "source": rates.get("source"), "updated_at": rates.get("updated_at"),
               "inr_per": rates.get("inr_per", {})},
    }


def build_summary(rows: list[dict], canon: Optional[Callable[[str], str]] = None) -> dict:
    """Recurring-template projection (monthly-normalised) — feeds dashboard income."""
    rates = fx.get_rates()
    canon = canon or (lambda s: (s or "—").strip().title() or "—")

    enriched: list[dict] = []
    by_category: dict[str, float] = {}
    by_person: dict[str, float] = {}
    by_currency: dict[str, float] = {}
    currencies: set[str] = set()
    monthly_total = 0.0
    one_time_total = 0.0

    for e in rows:
        row = enrich(e, rates)
        person = canon(e.get("owner"))
        row["owner"] = person
        enriched.append(row)
        currencies.add(row["currency"])

        if not row.get("active", True):
            continue  # paused — tracked but excluded from totals

        if row["one_time"]:
            one_time_total += row["amount_inr"]
            continue  # one-offs don't inflate the recurring monthly figure

        m = row["monthly_inr"]
        monthly_total += m
        cat = (e.get("category") or "Uncategorised").strip() or "Uncategorised"
        by_category[cat] = by_category.get(cat, 0.0) + m
        by_person[person] = by_person.get(person, 0.0) + m
        by_currency[row["currency"]] = by_currency.get(row["currency"], 0.0) + m

    categories = sorted(
        ({"category": k, "monthly_inr": round(v, 2),
          "pct": (v / monthly_total) if monthly_total > 0 else 0} for k, v in by_category.items()),
        key=lambda x: x["monthly_inr"], reverse=True,
    )
    people = sorted(
        ({"person": k, "monthly_inr": round(v, 2)} for k, v in by_person.items()),
        key=lambda x: x["monthly_inr"], reverse=True,
    )

    enriched.sort(key=lambda x: x["monthly_inr"], reverse=True)
    return {
        "count": len([e for e in enriched if e.get("active", True) and not e["one_time"]]),
        "monthly_total": round(monthly_total, 2),
        "annual_total": round(monthly_total * 12, 2),
        "one_time_total": round(one_time_total, 2),
        "by_category": categories,
        "by_person": people,
        "by_currency": [{"currency": k, "monthly_inr": round(v, 2)} for k, v in
                        sorted(by_currency.items(), key=lambda kv: kv[1], reverse=True)],
        "currencies": sorted(currencies),
        "has_foreign": any(c != "INR" for c in currencies),
        "entries": enriched,
        "fx": {
            "ok": rates.get("ok", False), "stale": rates.get("stale", False),
            "source": rates.get("source"), "updated_at": rates.get("updated_at"),
            "inr_per": rates.get("inr_per", {}),
        },
    }
