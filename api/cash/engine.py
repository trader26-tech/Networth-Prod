"""Convert cash/bank balances to INR (live FX via the salary feed) and total
them up by owner and type."""
from __future__ import annotations

from typing import Callable, Optional

from ..salary import fx


def enrich(item: dict, rates: Optional[dict] = None) -> dict:
    rates = rates or fx.get_rates()
    cur = (item.get("currency") or "INR").upper()
    bal = float(item.get("balance") or 0)
    out = dict(item)
    out["currency"] = cur
    out["balance_inr"] = round(fx.to_inr(bal, cur, rates), 2)
    out["inr_per_unit"] = round(fx.inr_per_unit(cur, rates), 4)
    out["is_foreign"] = cur != "INR"
    return out


def build_summary(items: list[dict], canon: Optional[Callable[[str], str]] = None) -> dict:
    rates = fx.get_rates()
    canon = canon or (lambda s: (s or "—").strip().title() or "—")
    enriched: list[dict] = []
    by_owner: dict[str, float] = {}
    by_type = {"cash": 0.0, "bank": 0.0}
    currencies: set[str] = set()
    total = 0.0

    for it in items:
        row = enrich(it, rates)
        person = canon(it.get("owner"))
        row["owner"] = person
        enriched.append(row)
        v = row["balance_inr"]
        total += v
        currencies.add(row["currency"])
        by_owner[person] = by_owner.get(person, 0.0) + v
        t = it.get("type") if it.get("type") in by_type else "bank"
        by_type[t] = by_type.get(t, 0.0) + v

    people = sorted(({"person": k, "balance_inr": round(v, 2)} for k, v in by_owner.items()),
                    key=lambda x: x["balance_inr"], reverse=True)
    enriched.sort(key=lambda x: x["balance_inr"], reverse=True)
    return {
        "count": len(enriched),
        "total": round(total, 2),
        "cash": round(by_type.get("cash", 0.0), 2),
        "bank": round(by_type.get("bank", 0.0), 2),
        "by_person": people,
        "currencies": sorted(currencies),
        "has_foreign": any(c != "INR" for c in currencies),
        "entries": enriched,
        "fx": {"ok": rates.get("ok", False), "stale": rates.get("stale", False),
               "source": rates.get("source"), "updated_at": rates.get("updated_at"),
               "inr_per": rates.get("inr_per", {})},
    }
