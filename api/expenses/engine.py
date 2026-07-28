"""
Turn raw expenses into a dashboard-ready summary: each expense's monthly
₹-equivalent (currency-converted + frequency-normalised), plus splits by
category, person, currency, subscription and essential/discretionary.

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


def monthly_native(expense: dict) -> float:
    """Per-month amount in the expense's own currency (one-time → 0)."""
    amt = float(expense.get("amount") or 0)
    return amt * _MONTHLY_FACTOR.get(expense.get("frequency") or "monthly", 1.0)


def enrich(expense: dict, rates: Optional[dict] = None) -> dict:
    rates = rates or fx.get_rates()
    cur = (expense.get("currency") or "INR").upper()
    m_native = monthly_native(expense)
    m_inr = fx.to_inr(m_native, cur, rates)
    amt = float(expense.get("amount") or 0)
    out = dict(expense)
    out["currency"] = cur
    out["monthly_native"] = round(m_native, 2)
    out["monthly_inr"] = round(m_inr, 2)
    out["annual_inr"] = round(m_inr * 12, 2)
    out["amount_inr"] = round(fx.to_inr(amt, cur, rates), 2)   # raw amount in ₹
    out["inr_per_unit"] = round(fx.inr_per_unit(cur, rates), 4)
    out["is_foreign"] = cur != "INR"
    out["one_time"] = (expense.get("frequency") == "one_time")
    return out


def _region_of(currency: Optional[str]) -> str:
    return "india" if (currency or "INR").upper() == "INR" else "kuwait"


_REGION_LABEL = {"india": "India", "kuwait": "Kuwait"}


def is_recurring(e: dict) -> bool:
    return (e.get("frequency") or "one_time") != "one_time"


def applies_to_month(e: dict, period: str, cur_period: str) -> bool:
    """Does this expense count toward `period` (YYYY-MM)?
      • one-time  → only the month it was logged in.
      • recurring → every month from its start (on_date, or the current month if
        it never had an explicit start) until it's stopped (end_date, exclusive).
    """
    if is_recurring(e):
        start = str(e.get("on_date") or "")[:7] or cur_period
        if period < start:
            return False
        end = str(e.get("end_date") or "")[:7]
        if end and period >= end:          # stopped from `end` onward
            return False
        return True
    return str(e.get("on_date") or "")[:7] == period


def month_summary(expenses: list[dict], period: str, cur_period: str,
                  canon: Optional[Callable[[str], str]] = None) -> dict:
    """Every expense that falls in `period` — one-offs dated in it plus recurring
    ones carried forward — split India vs Kuwait and by category (treemap source).
    Each entry's `month_inr` is what it contributes THIS month (recurring →
    frequency-normalised monthly ₹; one-off → its actual ₹)."""
    rates = fx.get_rates()
    canon = canon or (lambda s: (s or "—").strip().title() or "—")
    entries: list[dict] = []
    total = recurring_total = onetime_total = 0.0
    # region -> {total, categories:{cat: amt}}
    reg: dict[str, dict] = {}
    by_category: dict[str, float] = {}
    by_person: dict[str, dict] = {}       # who spends what (4 person cards)

    for e in expenses:
        if not e.get("active", True):
            continue
        if not applies_to_month(e, period, cur_period):
            continue
        row = enrich(e, rates)
        recurring = is_recurring(e)
        month_inr = row["monthly_inr"] if recurring else row["amount_inr"]
        month_native = row["monthly_native"] if recurring else float(e.get("amount") or 0)
        if month_inr <= 0:
            continue
        region = _region_of(row["currency"])
        row["recurring"] = recurring
        row["region"] = region
        row["region_label"] = _REGION_LABEL[region]
        row["month_inr"] = round(month_inr, 2)
        row["month_native"] = round(month_native, 2)
        row["owner"] = canon(e.get("owner"))
        entries.append(row)

        total += month_inr
        if recurring:
            recurring_total += month_inr
        else:
            onetime_total += month_inr
        cat = (e.get("category") or "Uncategorised").strip() or "Uncategorised"
        by_category[cat] = by_category.get(cat, 0.0) + month_inr
        rb = reg.setdefault(region, {"total": 0.0, "cats": {}})
        rb["total"] += month_inr
        rb["cats"][cat] = rb["cats"].get(cat, 0.0) + month_inr
        # per-person tallies (spent this month, split recurring vs one-off)
        pb = by_person.setdefault(row["owner"], {"person": row["owner"], "spent_inr": 0.0,
                                                 "recurring_inr": 0.0, "onetime_inr": 0.0, "count": 0})
        pb["spent_inr"] += month_inr
        pb["count"] += 1
        if recurring:
            pb["recurring_inr"] += month_inr
        else:
            pb["onetime_inr"] += month_inr

    # ordered region blocks (India first) for the treemap
    regions = []
    for key in ("india", "kuwait"):
        if key not in reg:
            continue
        rb = reg[key]
        cats = sorted(
            ({"category": c, "amount_inr": round(v, 2),
              "pct": (v / rb["total"]) if rb["total"] > 0 else 0}
             for c, v in rb["cats"].items()),
            key=lambda x: x["amount_inr"], reverse=True,
        )
        regions.append({
            "region": key, "label": _REGION_LABEL[key],
            "currency": store_currency(key),
            "total_inr": round(rb["total"], 2),
            "pct": (rb["total"] / total) if total > 0 else 0,
            "categories": cats,
        })

    categories = sorted(
        ({"category": k, "monthly_inr": round(v, 2),
          "pct": (v / total) if total > 0 else 0} for k, v in by_category.items()),
        key=lambda x: x["monthly_inr"], reverse=True,
    )
    entries.sort(key=lambda x: (0 if x["recurring"] else 1, -x["month_inr"]))
    people = sorted(
        ({"person": p["person"], "spent_inr": round(p["spent_inr"], 2),
          "recurring_inr": round(p["recurring_inr"], 2), "onetime_inr": round(p["onetime_inr"], 2),
          "count": p["count"]} for p in by_person.values()),
        key=lambda x: -x["spent_inr"],
    )
    return {
        "period": period,
        "total_inr": round(total, 2),
        "by_person": people,
        "recurring_inr": round(recurring_total, 2),
        "onetime_inr": round(onetime_total, 2),
        "india_inr": round(reg.get("india", {}).get("total", 0.0), 2),
        "kuwait_inr": round(reg.get("kuwait", {}).get("total", 0.0), 2),
        "count": len(entries),
        "recurring_count": sum(1 for e in entries if e["recurring"]),
        "regions": regions,
        "by_category": categories,
        "entries": entries,
        "fx": {"ok": rates.get("ok", False), "stale": rates.get("stale", False),
               "source": rates.get("source"), "updated_at": rates.get("updated_at"),
               "inr_per": rates.get("inr_per", {})},
    }


def store_currency(region: str) -> str:
    return "INR" if region == "india" else "KWD"


def month_view(entries: list[dict], period: str, paid_keys, canon: Optional[Callable[[str], str]] = None) -> dict:
    """The expense occurrences for one month (recurring + one-offs dated in it),
    each with paid/not status. `entries` are enriched; `paid_keys` is a set of
    receipt keys already ticked for `period`. Powers the reminders + month view.
    """
    canon = canon or (lambda s: (s or "—").strip().title() or "—")
    items: list[dict] = []
    for e in entries:
        if not e.get("active", True):
            continue
        if e.get("one_time") or e.get("frequency") == "one_time":
            od = str(e.get("on_date") or "")[:7]
            if not od or od != period:
                continue
            amount = float(e.get("amount_inr") or 0)
        else:
            amount = float(e.get("monthly_inr") or 0)
        if amount <= 0:
            continue
        key = f"expense:{e.get('id')}:{period}"
        one_time = bool(e.get("one_time") or e.get("frequency") == "one_time")
        receipt = paid_keys.get(key) if hasattr(paid_keys, "get") else None
        paid = (key in paid_keys)
        paid_on = (receipt or {}).get("on_date") if receipt else None
        # the date this expense falls/was paid: one-off → its date; else → when ticked
        on_date = e.get("on_date") if one_time else paid_on
        cur = (e.get("currency") or "INR").upper()
        native = float(e.get("amount") or 0) if one_time else float(e.get("monthly_native") or 0)
        items.append({
            "key": key, "id": e.get("id"), "label": e.get("name") or "Expense",
            "kind": e.get("category") or "Expense", "owner": canon(e.get("owner")),
            "amount": round(amount, 2), "paid": paid, "paid_on": paid_on,
            "one_time": one_time, "on_date": on_date,
            # region + native amount so the money calendar can show KWD for Kuwait
            "region": "india" if cur == "INR" else "kuwait", "currency": cur,
            "native": round(native, 2),
        })
    expected = paid = 0.0
    for it in items:
        expected += it["amount"]
        if it["paid"]:
            paid += it["amount"]
    items.sort(key=lambda x: (x["paid"], -x["amount"]))
    return {
        "period": period, "items": items,
        "expected": round(expected, 2), "paid": round(paid, 2),
        "pending": round(expected - paid, 2),
        "pending_count": sum(1 for i in items if not i["paid"]),
        "paid_count": sum(1 for i in items if i["paid"]),
    }


def log_summary(entries: list[dict], canon: Optional[Callable[[str], str]] = None) -> dict:
    """Actual logged expenses (each a single dated occurrence) → totals + splits.
    Uses the full converted amount (not a monthly-normalised figure)."""
    rates = fx.get_rates()
    canon = canon or (lambda s: (s or "—").strip().title() or "—")
    enriched: list[dict] = []
    by_category: dict[str, float] = {}
    by_person: dict[str, float] = {}
    by_currency: dict[str, float] = {}
    currencies: set[str] = set()
    total = essential = discretionary = subs = 0.0
    subs_count = 0

    for e in entries:
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
        if row.get("essential", True):
            essential += amt
        else:
            discretionary += amt
        if row.get("is_subscription"):
            subs += amt; subs_count += 1

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
        "monthly_total": round(total, 2),     # "total spent" for the period
        "annual_total": round(total, 2),
        "essential": round(essential, 2),
        "discretionary": round(discretionary, 2),
        "subscriptions_total": round(subs, 2),
        "subscriptions_count": subs_count,
        "one_time_total": 0.0,
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


def build_summary(expenses: list[dict], canon: Optional[Callable[[str], str]] = None) -> dict:
    rates = fx.get_rates()
    canon = canon or (lambda s: (s or "—").strip().title() or "—")

    enriched: list[dict] = []
    by_category: dict[str, float] = {}
    by_person: dict[str, float] = {}
    by_currency: dict[str, float] = {}
    currencies: set[str] = set()
    monthly_total = 0.0
    essential_total = 0.0
    discretionary_total = 0.0
    subs_total = 0.0
    subs_count = 0
    one_time_total = 0.0

    for e in expenses:
        row = enrich(e, rates)
        person = canon(e.get("person") or e.get("owner"))
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
        if row.get("essential", True):
            essential_total += m
        else:
            discretionary_total += m
        if row.get("is_subscription"):
            subs_total += m
            subs_count += 1

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
        "essential": round(essential_total, 2),
        "discretionary": round(discretionary_total, 2),
        "subscriptions_total": round(subs_total, 2),
        "subscriptions_count": subs_count,
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
