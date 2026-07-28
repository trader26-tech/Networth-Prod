"""
Turn raw salary entries into a dashboard-ready summary: each entry's monthly
INR-equivalent, per-person totals, and the FX context used. Currency conversion
happens here (via fx.py) so the rest of the app stays currency-agnostic.
"""
from __future__ import annotations

from typing import Callable, Optional

from . import fx


def monthly_native(entry: dict) -> float:
    """Per-month amount in the entry's OWN currency (annual ÷ 12)."""
    amt = float(entry.get("amount") or 0)
    if (entry.get("frequency") or "monthly") == "annual":
        return amt / 12.0
    return amt


def enrich(entry: dict, rates: Optional[dict] = None) -> dict:
    """Decorate one entry with its monthly figures (native + INR)."""
    rates = rates or fx.get_rates()
    cur = (entry.get("currency") or "INR").upper()
    m_native = monthly_native(entry)
    m_inr = fx.to_inr(m_native, cur, rates)
    out = dict(entry)
    out["currency"] = cur
    out["monthly_native"] = round(m_native, 2)
    out["monthly_inr"] = round(m_inr, 2)
    out["annual_inr"] = round(m_inr * 12, 2)
    out["inr_per_unit"] = round(fx.inr_per_unit(cur, rates), 4)
    out["is_foreign"] = cur != "INR"
    return out


def build_summary(entries: list[dict], canon: Optional[Callable[[str], str]] = None) -> dict:
    """Full salary block: enriched entries, per-person totals, FX context."""
    rates = fx.get_rates()
    canon = canon or (lambda s: (s or "—").strip().title() or "—")

    enriched: list[dict] = []
    by_person: dict[str, dict] = {}
    monthly_total = 0.0
    currencies: set[str] = set()

    for e in entries:
        row = enrich(e, rates)
        person = canon(e.get("person"))
        row["person"] = person
        enriched.append(row)
        monthly_total += row["monthly_inr"]
        currencies.add(row["currency"])

        p = by_person.setdefault(person, {
            "person": person, "monthly_inr": 0.0, "count": 0, "_cur": set(),
        })
        p["monthly_inr"] += row["monthly_inr"]
        p["count"] += 1
        p["_cur"].add(row["currency"])

    people = []
    for p in by_person.values():
        people.append({
            "person": p["person"],
            "monthly_inr": round(p["monthly_inr"], 2),
            "annual_inr": round(p["monthly_inr"] * 12, 2),
            "count": p["count"],
            "currencies": sorted(p["_cur"]),
        })
    people.sort(key=lambda x: x["monthly_inr"], reverse=True)

    enriched.sort(key=lambda x: x["monthly_inr"], reverse=True)
    return {
        "monthly_total": round(monthly_total, 2),
        "annual_total": round(monthly_total * 12, 2),
        "count": len(enriched),
        "currencies": sorted(currencies),
        "has_foreign": any(c != "INR" for c in currencies),
        "by_person": people,
        "entries": enriched,
        "fx": {
            "ok": rates.get("ok", False),
            "stale": rates.get("stale", False),
            "source": rates.get("source"),
            "updated_at": rates.get("updated_at"),
            "inr_per": rates.get("inr_per", {}),
        },
    }
