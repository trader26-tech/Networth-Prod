"""
Unify every asset module into one portfolio view for the home dashboard:
positions (value · CAGR · monthly income · liquidity), totals, allocation, and
this-month's expected income with receipt status.

Each module is pulled defensively — a missing table or empty module just
contributes nothing rather than breaking the dashboard.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from . import store as receipt_store

# How sellable each class is — the "liquidity database" the cash advisor uses.
# tier: lower = more liquid. days: rough time to realise cash. divisible: can you
# sell part of it (gold/stocks/bonds) or only the whole asset (real estate)?
LIQUIDITY = {
    "cash":       {"tier": 0, "days": 0,   "label": "Instant",       "divisible": True},
    "stocks":     {"tier": 1, "days": 3,   "label": "2–3 days",      "divisible": True},
    "gold":       {"tier": 1, "days": 2,   "label": "1–2 days",      "divisible": True},
    "bonds":      {"tier": 2, "days": 14,  "label": "1–2 weeks",     "divisible": True},
    "fd":         {"tier": 1, "days": 3,   "label": "2–3 days",      "divisible": True},
    "apartments": {"tier": 3, "days": 120, "label": "3–4 months",    "divisible": False},
    "built":      {"tier": 3, "days": 120, "label": "3–4 months",    "divisible": False},
    "land":       {"tier": 4, "days": 180, "label": "~6 months",     "divisible": False},
    "ulip":       {"tier": 2, "days": 30,  "label": "after 5y lock", "divisible": True},
}
CLASS_LABEL = {
    "stocks": "Stocks", "gold": "Gold & Silver", "bonds": "Bonds",
    "apartments": "Apartments", "built": "Land + Build", "land": "Land",
    "cash": "Cash", "ulip": "ULIP", "fd": "Fixed Deposit",
}


def _liq(cls: str) -> dict:
    return LIQUIDITY.get(cls, {"tier": 3, "days": 90, "label": "—", "divisible": False})


# Owner names are entered inconsistently across modules (nicknames / partial
# names). Canonicalise so the per-person view isn't fragmented — the household's
# four members and every historical spelling live in api/people/store.py.
from ..people.store import canon_owner, people_by_name, ALIASES as OWNER_ALIASES  # noqa: E402,F401


def _pos(asset_class, name, owner, value, cagr=None, monthly_income=0.0,
         realisable=None, invested=None, sub=None, sellable_on=None):
    liq = _liq(asset_class)
    return {
        "asset_class": asset_class, "class_label": CLASS_LABEL.get(asset_class, asset_class),
        "name": name, "owner": canon_owner(owner),
        "value": round(value or 0, 2),
        "realisable": round((realisable if realisable is not None else (value or 0)), 2),
        "invested": round(invested, 2) if invested is not None else None,
        "cagr": cagr, "monthly_income": round(monthly_income or 0, 2),
        "liquidity_tier": liq["tier"], "liquidity_days": liq["days"],
        "liquidity_label": liq["label"], "divisible": liq["divisible"],
        "sub": sub,
        # the date the user expects to be able to sell this asset (set on the
        # asset's own page) — drives the buy-planner's "savings" financing.
        "sellable_on": (str(sellable_on)[:10] if sellable_on else None),
    }


def gather_positions(gold24k: Optional[float] = None, silver: Optional[float] = None) -> list[dict]:
    out: list[dict] = []

    # ── Land ──────────────────────────────────────────────────────────────────
    try:
        from ..land import store as land_store
        for p in land_store.list_parcels():
            v = p.get("current_estimated_price")
            if not v:
                continue
            out.append(_pos("land", p.get("name") or "Land parcel", p.get("owner"),
                            v, cagr=p.get("cagr"), realisable=p.get("after_brokerage_price"),
                            invested=p.get("bought_price"), sellable_on=p.get("sellable_on")))
    except Exception:
        pass

    # ── Apartments ────────────────────────────────────────────────────────────
    try:
        from ..apartments import store as apt_store
        for u in apt_store.list_units():
            v = u.get("current_estimated_price")
            if not v:
                continue
            out.append(_pos("apartments", u.get("name") or "Apartment", u.get("owner"),
                            v, cagr=u.get("cagr"),   # price appreciation only (rent counted as income)
                            monthly_income=u.get("monthly_rent"),
                            realisable=u.get("after_brokerage_price"), invested=u.get("bought_price"),
                            sellable_on=u.get("sellable_on")))
    except Exception:
        pass

    # ── Land + Build ──────────────────────────────────────────────────────────
    try:
        from ..built import store as built_store
        for p in built_store.list_properties():
            v = p.get("current_estimated_price")
            if not v:
                continue
            out.append(_pos("built", p.get("name") or "Built property", p.get("owner"),
                            v, cagr=p.get("cagr"),   # two-leg appreciation IRR only
                            monthly_income=p.get("monthly_rent"),
                            realisable=p.get("after_brokerage_price"),
                            sellable_on=p.get("sellable_on")))
    except Exception:
        pass

    # ── Gold & Silver (one row per piece) ─────────────────────────────────────
    try:
        from ..gold import store as gold_store
        from ..gold import prices as gold_prices
        px = gold_prices.get_prices() or {}
        g24 = px.get("gold_24k_per_g") or 0
        sil = px.get("silver_per_g") or 0
        # honour the user's local rate override (from the Gold page) so the
        # dashboard values match what the Gold tab shows.
        if gold24k and float(gold24k) > 0:
            g24 = float(gold24k)
        if silver and float(silver) > 0:
            sil = float(silver)
        # No per-piece purchase dates → no real holding CAGR; gold is excluded
        # from portfolio CAGR (value still counts). Each piece is its OWN row so
        # the user can set a different growth assumption per asset — nothing is
        # combined into a single gold bucket.
        seen: dict[str, int] = {}
        for it in gold_store.list_items():
            mv = it.get("manual_value")
            if mv:
                v = float(mv)
            else:
                w = it.get("weight_g") or 0
                pur = (it.get("purity_pct") or 0) / 100.0
                rate = sil if it.get("metal_type") == "silver" else g24
                v = w * pur * rate
            if v <= 0:
                continue
            name = (it.get("name") or "").strip() or "Gold & jewellery"
            owner = canon_owner(it.get("owner"))
            # Per-asset overrides are keyed by name+owner — keep names distinct
            # per owner so two same-named pieces don't share one growth rate.
            k = f"{name}|{owner}"
            seen[k] = seen.get(k, 0) + 1
            if seen[k] > 1:
                name = f"{name} ({seen[k]})"
            out.append(_pos("gold", name, owner, v, cagr=None, realisable=v * 0.97,
                            sellable_on=it.get("sellable_on")))
    except Exception:
        pass

    # ── ULIP (unit-linked insurance — fund value counts; XIRR over premiums) ──
    try:
        from ..ulip import store as ulip_store
        from ..ulip import engine as ulip_engine
        _u_rows = ulip_store.list_policies()
        _u_sell = {r.get("id"): r.get("sellable_on") for r in _u_rows}
        for p in ulip_engine.build_summary(_u_rows)["policies"]:
            v = p.get("fund_value")
            if not v:
                continue
            name = p.get("plan_name") or "ULIP"
            if p.get("insurer"):
                name = f"{p['insurer']} · {name}"
            out.append(_pos("ulip", name, p.get("owner"), v,
                            cagr=p.get("xirr"), invested=p.get("invested"),
                            sub=p.get("fund_type"),
                            sellable_on=p.get("sellable_on") or _u_sell.get(p.get("id"))))
    except Exception:
        pass

    # ── Fixed Deposits (value to net worth; payout interest = income) ─────────
    try:
        from ..fd import store as fd_store
        from ..fd import engine as fd_engine
        _fd_rows = fd_store.list_fds()
        _fd_sell = {r.get("id"): r.get("sellable_on") for r in _fd_rows}
        for f in fd_engine.build_summary(_fd_rows)["fds"]:
            v = f.get("current_value")
            if not v:
                continue
            name = f.get("bank") or "Fixed Deposit"
            out.append(_pos("fd", name, f.get("owner"), v,
                            cagr=f.get("dashboard_cagr"),
                            monthly_income=f.get("monthly_income"),
                            invested=f.get("principal"),
                            sellable_on=f.get("sellable_on") or _fd_sell.get(f.get("id"))))
    except Exception:
        pass

    # ── Cash & bank balances (most liquid; instant) ──────────────────────────
    try:
        from ..cash import store as cash_store
        from ..cash import engine as cash_engine
        for c in cash_engine.build_summary(cash_store.list_items())["entries"]:
            v = c.get("balance_inr")
            if not v:
                continue
            nm = (c.get("where") or "").strip() or ("Cash in hand" if c.get("type") == "cash" else "Bank balance")
            out.append(_pos("cash", nm, c.get("owner"), v, cagr=None, realisable=v))
    except Exception:
        pass

    # ── Stocks — live multi-account equity portfolio (Kite). Falls back to the
    #    old manual brokerage accounts until live accounts are connected, so net
    #    worth never drops during the transition. ─────────────────────────────────
    _stocks_live = False
    try:
        from ..portfolio import engine as eq_engine
        es = eq_engine.build_summary()
        if (es.get("total_value") or 0) > 0:
            for a in es.get("accounts", []):
                v = a.get("value")
                if not v:
                    continue
                out.append(_pos("stocks", a.get("account_label") or a.get("broker") or "Equity",
                                a.get("person"), v, invested=a.get("invested"),
                                sellable_on=a.get("sellable_on")))
            _stocks_live = True
    except Exception:
        pass

    if not _stocks_live:
        try:
            from ..brokerage import store as bk_store
            from ..brokerage import engine as bk_engine
            _bk_rows = bk_store.list_accounts()
            _bk_sell = {r.get("id"): r.get("sellable_on") for r in _bk_rows}
            s = bk_engine.build_summary(_bk_rows)
            for a in s.get("accounts", []):
                v = a.get("end_amount")
                if not v:
                    continue
                out.append(_pos("stocks", a.get("broker") or "Equity account", a.get("member"),
                                v, cagr=a.get("cagr"), invested=a.get("start_amount"),
                                sellable_on=a.get("sellable_on") or _bk_sell.get(a.get("id"))))
        except Exception:
            pass

    # ── Bonds ─────────────────────────────────────────────────────────────────
    try:
        from ..bonds import store as bond_store
        from ..bonds import engine as bond_engine
        _bd_rows = bond_store.list_bonds()
        _bd_sell = {r.get("id"): r.get("sellable_on") for r in _bd_rows}
        s = bond_engine.build_summary(_bd_rows)
        for b in s.get("bonds", []):
            v = b.get("invested")
            if not v:
                continue
            out.append(_pos("bonds", b.get("issuer") or "Bond", b.get("owner"),
                            v, cagr=b.get("ytm"), monthly_income=b.get("monthly_income"),
                            invested=v, sub=b.get("rating"),
                            sellable_on=b.get("sellable_on") or _bd_sell.get(b.get("id"))))
    except Exception:
        pass

    return out


def salary_block() -> dict:
    """Per-person recurring earned income, converted to INR. Defensive — a
    missing module/table just contributes an empty block."""
    empty = {"monthly_total": 0.0, "annual_total": 0.0, "count": 0, "currencies": [],
             "has_foreign": False, "by_person": [], "entries": [], "fx": {}}
    try:
        from ..salary import store as salary_store
        from ..salary import engine as salary_engine
        return salary_engine.build_summary(salary_store.list_entries(), canon=canon_owner)
    except Exception:
        return empty


def expenses_block(today: Optional[date] = None) -> dict:
    """Recurring monthly spending (carried-forward expenses), converted to INR —
    the baseline that repeats every month. Defensive."""
    today = today or date.today()
    period = f"{today.year:04d}-{today.month:02d}"
    empty = {"monthly_total": 0.0, "annual_total": 0.0, "by_category": [],
             "by_region": [], "india_inr": 0.0, "kuwait_inr": 0.0,
             "entries": [], "fx": {}, "count": 0}
    try:
        from ..expenses import store as exp_store
        from ..expenses import engine as exp_engine
        m = exp_engine.month_summary(exp_store.list_expenses(), period, period, canon=canon_owner)
        return {
            "monthly_total": m["recurring_inr"], "annual_total": round(m["recurring_inr"] * 12, 2),
            "total_this_month": m["total_inr"], "india_inr": m["india_inr"], "kuwait_inr": m["kuwait_inr"],
            "by_category": m["by_category"], "by_region": m["regions"],
            "count": m["recurring_count"], "entries": m["entries"], "fx": m["fx"],
        }
    except Exception:
        return empty


def loans_block() -> dict:
    """Outstanding loan principal (INR) — a liability that reduces net worth —
    plus the monthly EMI outgo that counts against income. Foreign-currency
    loans are converted to INR by the loans engine. Defensive."""
    empty = {"total_outstanding_inr": 0.0, "total_emi_monthly_inr": 0.0,
             "active_count": 0, "count": 0, "by_person": []}
    try:
        from ..loans import store as loan_store
        from ..loans import engine as loan_engine
        s = loan_engine.build_summary(loan_store.list_loans())
        return {"total_outstanding_inr": s["total_outstanding_inr"],
                "total_emi_monthly_inr": s["total_emi_monthly_inr"],
                "active_count": s["active_count"], "count": s["count"],
                "by_person": s["by_person"]}
    except Exception:
        return empty


def advances_block() -> dict:
    """Tenant advances / security deposits that must be repaid when a tenant
    vacates — a liability that reduces net worth. Only ACTIVE tenancies are
    counted (a moved-out tenant has already been settled). Grouped per tenant
    so the dashboard can show exactly who to repay and how much, and per owner
    so it nets out of the right person's net worth. Defensive."""
    empty = {"total": 0.0, "count": 0, "items": [], "by_person": {}}
    try:
        from ..apartments import store as apt_store
        items: list[dict] = []
        by_person: dict[str, float] = {}
        for u in apt_store.list_units():
            owner = canon_owner(u.get("owner")) or "—"
            for t in (u.get("tenants") or []):
                if not t.get("active"):
                    continue
                adv = float(t.get("advance_paid") or 0)
                if adv <= 0:
                    continue
                items.append({
                    "tenant": t.get("name") or "Tenant",
                    "unit": u.get("name") or "Apartment",
                    "owner": owner,
                    "amount": round(adv, 2),
                })
                by_person[owner] = by_person.get(owner, 0.0) + adv
        items.sort(key=lambda x: x["amount"], reverse=True)
        return {
            "total": round(sum(i["amount"] for i in items), 2),
            "count": len(items),
            "items": items,
            "by_person": {k: round(v, 2) for k, v in by_person.items()},
        }
    except Exception:
        return empty


def spent_block(today: Optional[date] = None) -> dict:
    """Everything that hits this month — one-off spend PLUS recurring expenses
    carried forward — converted to INR, split India/Kuwait. Feeds the surplus."""
    today = today or date.today()
    period = f"{today.year:04d}-{today.month:02d}"
    empty = {"period": period, "monthly_total": 0.0, "count": 0, "by_category": [],
             "by_region": [], "india_inr": 0.0, "kuwait_inr": 0.0,
             "recurring_inr": 0.0, "onetime_inr": 0.0, "entries": [], "fx": {}}
    try:
        from ..expenses import store as exp_store
        from ..expenses import engine as exp_engine
        m = exp_engine.month_summary(exp_store.list_expenses(), period, period, canon=canon_owner)
        return {
            "period": period, "monthly_total": m["total_inr"], "count": m["count"],
            "by_category": m["by_category"], "by_region": m["regions"],
            "india_inr": m["india_inr"], "kuwait_inr": m["kuwait_inr"],
            "recurring_inr": m["recurring_inr"], "onetime_inr": m["onetime_inr"],
            "entries": m["entries"], "fx": m["fx"],
        }
    except Exception:
        return empty


def _ym(s) -> Optional[tuple]:
    s = str(s or "")[:10]
    parts = s.split("-")
    if len(parts) < 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None


def _months_between(a, b) -> int:
    ya = _ym(a); yb = _ym(b)
    if not ya or not yb:
        return 0
    return (yb[0] - ya[0]) * 12 + (yb[1] - ya[1])


def planner_committed(positions: Optional[list] = None, today: Optional[date] = None) -> float:
    """₹/mo you need to set aside across active buy-planner items to hit their
    buy-by dates — the gap not already covered by funds saved or assets you've
    linked to sell. Counts as a monthly expense against your surplus. Defensive."""
    import json
    try:
        from ..planner import store as p_store
        items = p_store.list_wishlist()
    except Exception:
        return 0.0
    today = today or date.today()
    cur = f"{today.year:04d}-{today.month:02d}"
    realis = {}
    for p in (positions or []):
        realis[f"{p['asset_class']}|{p['name']}|{p['owner']}"] = p.get("realisable") or 0

    def _keys(v):
        try:
            a = json.loads(v) if v else []
            return a if isinstance(a, list) else []
        except Exception:
            return []

    total = 0.0
    for it in items:
        if it.get("bought"):
            continue
        # explicit SIP amount wins
        try:
            mc = float(it.get("monthly_contribution") or 0)
        except (TypeError, ValueError):
            mc = 0
        if mc > 0:
            total += mc
            continue
        try:
            price = float(it.get("price") or 0)
        except (TypeError, ValueError):
            price = 0
        if price <= 0 or not it.get("target_date"):
            continue
        saved = 0.0
        try:
            saved = float(it.get("saved") or 0)
        except (TypeError, ValueError):
            pass
        linked = _keys(it.get("finance_assets")); sold = set(_keys(it.get("sold_assets")))
        asset_saved = sum(realis.get(k, 0) for k in linked if k in sold)
        unsold_val = sum(realis.get(k, 0) for k in linked if k not in sold)
        gap = max(0.0, price - saved - asset_saved - unsold_val)
        # SIP horizon runs from when the item was added to its target date
        months = max(1, _months_between(it.get("created_at") or cur, it.get("target_date")))
        total += gap / months
    return round(total, 2)


def other_income_block() -> dict:
    """Recurring non-salary income (dividends, rent, interest…), monthly-
    normalised & currency-converted to INR. Defensive."""
    empty = {"monthly_total": 0.0, "annual_total": 0.0, "one_time_total": 0.0,
             "by_category": [], "by_person": [], "by_currency": [], "currencies": [],
             "has_foreign": False, "entries": [], "fx": {}, "count": 0}
    try:
        from ..other_income import store as oi_store
        from ..other_income import engine as oi_engine
        return oi_engine.build_summary(oi_store.list_templates(), canon=canon_owner)
    except Exception:
        return empty


def other_income_received_block(today: Optional[date] = None) -> dict:
    """Actual logged other-income for the current month (the real receipts)."""
    today = today or date.today()
    period = f"{today.year:04d}-{today.month:02d}"
    empty = {"period": period, "monthly_total": 0.0, "count": 0, "by_category": [],
             "by_person": [], "by_currency": [], "currencies": [], "has_foreign": False,
             "entries": [], "fx": {}}
    try:
        from ..other_income import store as oi_store
        from ..other_income import engine as oi_engine
        summ = oi_engine.log_summary(oi_store.list_log(period), canon=canon_owner)
        summ["period"] = period
        return summ
    except Exception:
        return empty


def income_due(today: Optional[date] = None) -> dict:
    """This month's expected income items + receipt status."""
    today = today or date.today()
    period = f"{today.year:04d}-{today.month:02d}"
    items: list[dict] = []

    # rents (monthly) from apartments + built
    for mod, cls in (("apartments", "apartments"), ("built", "built")):
        try:
            if mod == "apartments":
                from ..apartments import store as s
                rows = s.list_units()
            else:
                from ..built import store as s
                rows = s.list_properties()
            for r in rows:
                rent = r.get("monthly_rent")
                if rent:
                    items.append({"key": f"rent:{mod}:{r.get('id')}", "kind": "Rent",
                                  "label": r.get("name") or "Property", "owner": r.get("owner") or "—",
                                  "amount": round(float(rent), 2)})
        except Exception:
            pass

    # bond coupons falling in this month
    try:
        from ..bonds import store as bond_store
        from ..bonds import engine as bond_engine
        for b in bond_store.list_bonds():
            for p in bond_engine.schedule(b):
                d = str(p.get("date") or "")[:7]
                if d == period and p.get("total"):
                    items.append({"key": f"coupon:{b.get('id')}:{p['date']}", "kind": "Coupon",
                                  "label": b.get("issuer") or "Bond", "owner": b.get("owner") or "—",
                                  "amount": round(float(p["total"]), 2)})
    except Exception:
        pass

    # FD interest payouts (non-cumulative) landing in this month
    try:
        from ..fd import store as fd_store
        from ..fd import engine as fd_engine
        for f in fd_store.list_fds():
            for p in fd_engine.payout_schedule(f):
                if str(p.get("date") or "")[:7] == period and p.get("amount"):
                    items.append({"key": f"fd:{f.get('id')}:{p['date']}", "kind": "FD interest",
                                  "label": f.get("bank") or "Fixed Deposit",
                                  "owner": f.get("owner") or "—",
                                  "amount": round(float(p["amount"]), 2)})
    except Exception:
        pass

    # recurring salary / earned income (monthly INR-equivalent, every month)
    try:
        for e in salary_block()["entries"]:
            if e.get("monthly_inr"):
                # lead with how it's earned (source/employer), then bank, then generic
                label = (e.get("note") or "").strip() or (e.get("bank_account") or "").strip() or "Monthly salary"
                items.append({"key": f"salary:{e.get('id')}:{period}", "kind": "Salary",
                              "label": label,
                              "owner": e.get("person") or "—",
                              "amount": round(float(e["monthly_inr"]), 2)})
    except Exception:
        pass

    # other income — recurring templates (expected, tick to confirm) + actual
    # one-off receipts logged this month (dividends, bonuses… already received)
    try:
        from ..other_income import store as oi_store
        from ..other_income import engine as oi_engine
        for t in oi_engine.build_summary(oi_store.list_templates(), canon=canon_owner)["entries"]:
            if not t.get("active", True) or t.get("one_time") or not t.get("monthly_inr"):
                continue
            label = (t.get("source") or "").strip() or (t.get("category") or "Other income")
            items.append({"key": f"otherincome:{t.get('id')}:{period}",
                          "kind": t.get("category") or "Other income", "label": label,
                          "owner": t.get("owner") or "—", "amount": round(float(t["monthly_inr"]), 2)})
        for e in oi_engine.log_summary(oi_store.list_log(period), canon=canon_owner)["entries"]:
            if e.get("template_id") or not e.get("amount_inr"):
                continue  # template-linked logs are covered by the template item above
            label = (e.get("source") or "").strip() or (e.get("category") or "Other income")
            items.append({"key": f"otherincome-log:{e.get('id')}",
                          "kind": e.get("category") or "Other income", "label": label,
                          "owner": e.get("owner") or "—",
                          "amount": round(float(e["amount_inr"]), 2), "received": True})
    except Exception:
        pass

    receipts = receipt_store.received_keys(period)
    expected = received = 0.0
    for it in items:
        # logged one-offs carry their own received=True; everything else is
        # confirmed via the receipt store (the tick).
        it["received"] = it.get("received", False) or (it["key"] in receipts)
        expected += it["amount"]
        if it["received"]:
            received += it["amount"]
    items.sort(key=lambda x: (x["received"], -x["amount"]))
    return {
        "period": period,
        "items": items,
        "expected": round(expected, 2),
        "received": round(received, 2),
        "pending": round(expected - received, 2),
        "pending_count": sum(1 for i in items if not i["received"]),
    }


def expenses_due(today: Optional[date] = None, entries: Optional[list] = None) -> dict:
    """This month's expense occurrences (recurring + one-offs dated this month)
    with paid/not status. Same receipt store as income (`expense:` keys)."""
    today = today or date.today()
    period = f"{today.year:04d}-{today.month:02d}"
    empty = {"period": period, "items": [], "expected": 0.0, "paid": 0.0,
             "pending": 0.0, "pending_count": 0, "paid_count": 0}
    try:
        from ..expenses import store as exp_store
        from ..expenses import engine as exp_engine
        if entries is None:
            entries = [exp_engine.enrich(e) for e in exp_store.list_expenses()]
        paid_keys = receipt_store.received_keys(period)
        return exp_engine.month_view(entries, period, paid_keys, canon=canon_owner)
    except Exception:
        return empty


def dividends_block(today: Optional[date] = None) -> dict:
    """Dividends actually received (logged on the Stocks tab, stored in
    stock_dividends). Surfaced as realised passive income — totals, top payers,
    and a per-person split (attributed by who currently holds each stock).
    `monthly` = trailing-12-month average, used as recurring income."""
    today = today or date.today()
    # "this_year" = the current INDIAN FINANCIAL YEAR (Apr 1 → Mar 31), not the
    # calendar year — dividends are reported/taxed by FY.
    fy_start_year = today.year if today.month >= 4 else today.year - 1
    fy_from = f"{fy_start_year}-04-01"
    fy_to = f"{fy_start_year + 1}-03-31"
    fy_label = f"FY {fy_start_year}-{str(fy_start_year + 1)[2:]}"
    empty = {"lifetime": 0.0, "this_year": 0.0, "this_month": 0.0, "ttm": 0.0, "monthly": 0.0,
             "count": 0, "year": fy_label, "top": [], "by_person": []}
    try:
        from ..portfolio import dividends as div_store
        rows = div_store.list_dividends()
    except Exception:
        return empty
    if not rows:
        return empty

    # who holds each stock now → split a stock's dividend across people by qty.
    # Normalise the symbol (uppercase, drop NSE series suffix like -BE/-BZ) so a
    # holding "CHEMBONDCH-BE" still matches a dividend logged as "CHEMBONDCH".
    import re as _re
    def _base(sym: str) -> str:
        return _re.sub(r"-[A-Z]{1,2}$", "", (sym or "").upper())
    hbs: dict[str, list] = {}
    try:
        from ..portfolio import store as pf_store
        for h in pf_store.list_holdings():
            s = h.get("symbol")
            if not s:
                continue
            hbs.setdefault(_base(s), []).append((canon_owner(h.get("person")), float(h.get("quantity") or 0)))
    except Exception:
        pass

    ym = f"{today.year}-{today.month:02d}"
    ttm_cut = date(today.year - 1, today.month, 1).isoformat()
    life = yr = mo = ttm = 0.0
    by_sym: dict[str, float] = {}
    per_year: dict[str, float] = {}
    per_ttm: dict[str, float] = {}
    for d in rows:
        amt = float(d.get("amount") or 0); dt = (d.get("date") or "")[:10]; sym = d.get("symbol") or "—"
        life += amt
        in_year = fy_from <= dt <= fy_to; in_ttm = dt >= ttm_cut
        if in_year: yr += amt
        if dt.startswith(ym): mo += amt
        if in_ttm: ttm += amt
        by_sym[sym] = by_sym.get(sym, 0) + amt
        accs = hbs.get(_base(sym)) or []
        tq = sum(q for _, q in accs)
        # attribute to holders by qty; if truly unmatched, use the stored person
        # (skip entirely if none — never invent an "Unassigned" person)
        if tq:
            parts = [(p, amt * q / tq) for p, q in accs]
        elif d.get("person"):
            parts = [(d.get("person"), amt)]
        else:
            parts = []
        for person, share in parts:
            if in_year: per_year[person] = per_year.get(person, 0) + share
            if in_ttm: per_ttm[person] = per_ttm.get(person, 0) + share

    top = sorted(({"symbol": s, "amount": round(a, 2)} for s, a in by_sym.items()),
                 key=lambda x: -x["amount"])[:5]
    by_person = sorted(
        ({"person": p, "this_year": round(per_year.get(p, 0), 2),
          "ttm": round(per_ttm.get(p, 0), 2), "monthly": round(per_ttm.get(p, 0) / 12.0, 2)}
         for p in set(per_year) | set(per_ttm)),
        key=lambda x: -x["ttm"])
    return {"lifetime": round(life, 2), "this_year": round(yr, 2), "this_month": round(mo, 2),
            "ttm": round(ttm, 2), "monthly": round(ttm / 12.0, 2),
            "count": len(rows), "year": fy_label, "top": top, "by_person": by_person}


def _fno_live_open() -> dict:
    """{account_id: live unrealised P&L} read from the ENGINE'S CACHED live state —
    the daemon marks every second (and the cron every minute) with whatever Kite
    token is currently valid, so a token you just refreshed flows through within a
    tick. Instant: no Kite call on the dashboard path. Empty when nothing is live
    (market shut / feed token expired) → the caller falls back to the last cached
    minute-mark."""
    by_acc: dict = {}
    try:
        from ..fno import engine as feng
        live = feng.latest() or {}
        if live.get("market_open"):
            for acc in live.get("accounts", []):
                s = sum(float(p.get("unrealised") or 0) for p in acc.get("positions", []) if p.get("quantity"))
                if abs(s) > 0.005:
                    by_acc[acc.get("id")] = round(s, 2)
    except Exception:
        pass
    return by_acc


_fno_booked_cache: dict = {"at": 0.0, "booked": None, "today": None}


def _fno_booked(accts: dict) -> tuple:
    """(booked_by_acc, today_by_acc) from the fno_daily_pnl rows — the only slow
    read (all daily rows). Booked realised P&L changes rarely intraday, so cache
    it a few minutes; the live open is added fresh on top by the caller."""
    import time
    now = time.time()
    c = _fno_booked_cache
    if c["booked"] is not None and (now - c["at"]) < 300.0:
        return c["booked"], c["today"]
    from ..fno import store as fstore
    today = fstore.today_ist()
    booked_by_acc: dict = {}
    today_by_acc: dict = {}
    try:
        for r in fstore.list_daily():
            aid = r.get("account_id")
            if aid not in accts:
                continue
            v = float(r.get("realized") or 0)
            booked_by_acc[aid] = booked_by_acc.get(aid, 0.0) + v
            if (r.get("date") or "")[:10] == today:
                today_by_acc[aid] = today_by_acc.get(aid, 0.0) + v
    except Exception:
        pass
    c["booked"], c["today"], c["at"] = booked_by_acc, today_by_acc, now
    return booked_by_acc, today_by_acc


def fno_block() -> dict:
    """F&O P&L for the hero KPI — realized (booked, all-time) + live open
    unrealized, per person. Booked from the fno_daily_pnl rows (cached a few
    minutes); open read fresh from the engine's cached live mark each call
    (instant — no Kite call), so it tracks a token refresh on the next ~60s poll.
    A KPI, NOT folded into net worth (trading cash accrues into the bank balance,
    tracked separately)."""
    try:
        from ..fno import store as fstore
        accts = {a["id"]: a for a in fstore.list_accounts()}
    except Exception:
        return {}
    if not accts:
        return {}
    today = fstore.today_ist()
    booked_by_acc, today_by_acc = _fno_booked(accts)
    # open unrealized = the engine's cached LIVE mark (instant, no Kite call) so a
    # freshly-refreshed token shows live unrealised P&L within a tick; falls back
    # to the last per-minute mark when nothing is live.
    open_by_acc = _fno_live_open()
    if not open_by_acc:
        try:
            pts = fstore.get_open_series(today)
            for aid, v in ((pts[-1].get("a") or {}) if pts else {}).items():
                open_by_acc[aid] = float(v or 0)
        except Exception:
            pass
    by_person: dict = {}
    for aid, acc in accts.items():
        person = acc.get("person") or acc.get("account_label") or "—"
        e = by_person.setdefault(person, {"person": person, "booked": 0.0, "open": 0.0, "today": 0.0})
        e["booked"] += booked_by_acc.get(aid, 0.0)
        e["open"] += open_by_acc.get(aid, 0.0)
        e["today"] += today_by_acc.get(aid, 0.0)
    people = []
    for e in by_person.values():
        e["booked"], e["open"], e["today"] = round(e["booked"], 2), round(e["open"], 2), round(e["today"], 2)
        e["total"] = round(e["booked"] + e["open"], 2)
        people.append(e)
    people.sort(key=lambda x: -abs(x["total"]))
    booked = round(sum(booked_by_acc.values()), 2)
    openv = round(sum(open_by_acc.values()), 2)
    return {
        "total": round(booked + openv, 2), "booked": booked, "open": openv,
        "today": round(sum(today_by_acc.values()), 2),
        "accounts": len(accts), "by_person": people,
    }


def build_dashboard(today: Optional[date] = None, gold24k: Optional[float] = None, silver: Optional[float] = None) -> dict:
    today = today or date.today()
    positions = gather_positions(gold24k, silver)
    salary = salary_block()
    other_income = other_income_block()       # recurring non-salary income (projection)
    dividends = dividends_block(today)        # realised dividends (trailing-12m as recurring)
    fno = fno_block()                         # F&O booked + open P&L (hero KPI, not in net worth)
    expenses = expenses_block()       # recurring projection (for surplus)
    spent = spent_block(today)        # actual logged spend this month
    loans = loans_block()             # outstanding liabilities + EMI outgo
    advances = advances_block()       # tenant advances owed back — a liability

    # `total_value` stays GROSS assets (drives allocation % + weightings below);
    # net worth nets out loan + advance liabilities separately in the return.
    total_value = sum(p["value"] for p in positions)
    net_worth = total_value - loans["total_outstanding_inr"] - advances["total"]
    total_realisable = sum(p["realisable"] for p in positions)
    # earned salary + recurring other income + dividends count toward income, not
    # net worth (they accrue into the bank balance, tracked separately).
    monthly_income = (sum(p["monthly_income"] for p in positions)
                      + salary["monthly_total"] + other_income["monthly_total"] + dividends["monthly"])
    invested = sum(p["invested"] or 0 for p in positions if p["invested"])

    # value-weighted portfolio CAGR (only assets with a known CAGR)
    wsum = csum = 0.0
    for p in positions:
        if p["cagr"] is not None:
            wsum += p["value"]; csum += p["value"] * p["cagr"]
    portfolio_cagr = (csum / wsum) if wsum > 0 else None

    # allocation by class
    by_class: dict[str, dict] = {}
    for p in positions:
        c = by_class.setdefault(p["asset_class"], {
            "asset_class": p["asset_class"], "label": p["class_label"],
            "value": 0.0, "monthly_income": 0.0, "count": 0,
            "liquidity_tier": p["liquidity_tier"], "liquidity_label": p["liquidity_label"],
            "_w": 0.0, "_c": 0.0,
        })
        c["value"] += p["value"]; c["monthly_income"] += p["monthly_income"]; c["count"] += 1
        if p["cagr"] is not None:
            c["_w"] += p["value"]; c["_c"] += p["value"] * p["cagr"]
    classes = []
    for c in by_class.values():
        c["cagr"] = (c["_c"] / c["_w"]) if c["_w"] > 0 else None
        c["pct"] = (c["value"] / total_value) if total_value > 0 else 0
        c.pop("_w"); c.pop("_c")
        classes.append({k: (round(v, 2) if isinstance(v, float) and k in ("value", "monthly_income") else v)
                        for k, v in c.items()})
    classes.sort(key=lambda x: x["value"], reverse=True)

    # per-person breakdown (value-weighted CAGR over dated assets only)
    by_person: dict[str, dict] = {}
    for p in positions:
        per = by_person.setdefault(p["owner"], {
            "person": p["owner"], "value": 0.0, "monthly_income": 0.0, "count": 0,
            "_w": 0.0, "_c": 0.0, "_classes": set(),
        })
        per["value"] += p["value"]; per["monthly_income"] += p["monthly_income"]; per["count"] += 1
        per["_classes"].add(p["asset_class"])
        if p["cagr"] is not None:
            per["_w"] += p["value"]; per["_c"] += p["value"] * p["cagr"]
    # fold recurring salary into each earner's monthly income — and surface a
    # salary-only person (e.g. someone abroad) even if they hold no assets here.
    # Every folded-in person key is canonicalised too, so an income/loan entered
    # under a nickname still lands on the same card as their assets.
    for sp in salary["by_person"]:
        key = canon_owner(sp["person"])
        per = by_person.setdefault(key, {
            "person": key, "value": 0.0, "monthly_income": 0.0, "count": 0,
            "_w": 0.0, "_c": 0.0, "_classes": set(),
        })
        per["monthly_income"] += sp["monthly_inr"]
    # fold recurring other income (rent, dividends…) into each earner too
    for sp in other_income["by_person"]:
        key = canon_owner(sp["person"])
        per = by_person.setdefault(key, {
            "person": key, "value": 0.0, "monthly_income": 0.0, "count": 0,
            "_w": 0.0, "_c": 0.0, "_classes": set(),
        })
        per["monthly_income"] += sp["monthly_inr"]
    # fold realised dividends (trailing-12m average) into each holder's income
    for sp in dividends["by_person"]:
        key = canon_owner(sp["person"])
        per = by_person.setdefault(key, {
            "person": key, "value": 0.0, "monthly_income": 0.0, "count": 0,
            "_w": 0.0, "_c": 0.0, "_classes": set(),
        })
        per["monthly_income"] += sp["monthly"]
    people = []
    for per in by_person.values():
        gross = round(per["value"], 2)
        people.append({
            "person": per["person"],
            "gross_value": gross,                       # assets only (before loans)
            "value": gross,                             # net worth — loans netted below
            "monthly_income": round(per["monthly_income"], 2),   # gross earned income
            "loan_outstanding": 0.0, "loan_emi": 0.0,   # filled from the loans block below
            "advance_repay": 0.0,                        # tenant advances owed back (filled below)
            "count": per["count"], "class_count": len(per["_classes"]),
            "cagr": (per["_c"] / per["_w"]) if per["_w"] > 0 else None,
            "pct": (per["value"] / total_value) if total_value > 0 else 0,
        })
    people.sort(key=lambda x: x["value"], reverse=True)

    # liquidity buckets (how much could become cash, how fast)
    liq: dict[int, dict] = {}
    for p in positions:
        t = p["liquidity_tier"]
        b = liq.setdefault(t, {"tier": t, "label": p["liquidity_label"], "value": 0.0,
                               "realisable": 0.0, "days": p["liquidity_days"]})
        b["value"] += p["value"]; b["realisable"] += p["realisable"]
    liquidity = [{**b, "value": round(b["value"], 2), "realisable": round(b["realisable"], 2)}
                 for b in sorted(liq.values(), key=lambda x: x["tier"])]

    # Surplus = total income − what actually left your pocket this month: the
    # logged spend PLUS the amounts you're setting aside for planned purchases
    # (buy-planner monthly contributions). Uses ACTUAL spend, not the recurring
    # templates, so it reflects reality.
    spent_exp = spent["monthly_total"]
    committed = planner_committed(positions, today)
    emi_outgo = loans["total_emi_monthly_inr"]        # loan EMIs are a monthly expense
    eff_expenses = spent_exp + committed + emi_outgo

    # attribute each borrower's outstanding balance + EMI to their person row.
    # `value` becomes NET worth (assets − loans); income stays gross and the EMI
    # is surfaced separately so the card can show it as an outflow. A borrower who
    # holds no assets here still gets a row so their loan is never hidden.
    _pindex = {p["person"]: p for p in people}
    for lp in loans["by_person"]:
        # canonicalise the borrower name so a loan owned by "Ram Prasad" folds
        # into that person's asset row instead of spawning a phantom card.
        lperson = canon_owner(lp["person"])
        per = _pindex.get(lperson)
        if per is None:
            per = {"person": lperson, "gross_value": 0.0, "value": 0.0,
                   "monthly_income": 0.0, "loan_outstanding": 0.0, "loan_emi": 0.0,
                   "advance_repay": 0.0, "count": 0, "class_count": 0, "cagr": None, "pct": 0.0}
            people.append(per); _pindex[lperson] = per
        per["loan_outstanding"] = round(lp["outstanding_inr"], 2)
        per["loan_emi"] = round(lp["emi_monthly_inr"], 2)
        per["value"] = round(per["gross_value"] - lp["outstanding_inr"], 2)
    # attribute each tenant advance owed back to the apartment owner's row, so
    # their net worth reflects the deposit they're still holding for a tenant.
    for person, amt in advances["by_person"].items():
        person = canon_owner(person)
        per = _pindex.get(person)
        if per is None:
            per = {"person": person, "gross_value": 0.0, "value": 0.0,
                   "monthly_income": 0.0, "loan_outstanding": 0.0, "loan_emi": 0.0,
                   "advance_repay": 0.0, "count": 0, "class_count": 0, "cagr": None, "pct": 0.0}
            people.append(per); _pindex[person] = per
        per["advance_repay"] = round(per.get("advance_repay", 0.0) + amt, 2)
        per["value"] = round(per["value"] - amt, 2)
    # re-base each person's share on household NET worth so the cards sum to 100%,
    # and attach each member's avatar + colour from the canonical people directory
    _directory = people_by_name()
    for per in people:
        per["pct"] = (per["value"] / net_worth) if net_worth > 0 else 0
        info = _directory.get(per["person"], {})
        per["photo"] = info.get("photo")
        per["color"] = info.get("color")
    people.sort(key=lambda x: x["value"], reverse=True)

    return {
        "net_worth": round(net_worth, 2),
        "gross_assets": round(total_value, 2),
        "liabilities": {
            "outstanding": round(loans["total_outstanding_inr"], 2),
            "monthly_emi": round(loans["total_emi_monthly_inr"], 2),
            "count": loans["count"], "active_count": loans["active_count"],
            "by_person": loans["by_person"],
        },
        "advances": {
            "total": advances["total"],
            "count": advances["count"],
            "items": advances["items"],
        },
        "realisable_value": round(total_realisable, 2),
        "invested": round(invested, 2),
        "total_gain": round(total_value - invested, 2) if invested else None,
        "portfolio_cagr": portfolio_cagr,
        "monthly_income": round(monthly_income, 2),
        "annual_income": round(monthly_income * 12, 2),
        "monthly_expenses": round(expenses["monthly_total"], 2),
        "annual_expenses": round(expenses["annual_total"], 2),
        "planner_committed": round(committed, 2),
        "surplus_expenses": round(eff_expenses, 2),
        "monthly_surplus": round(monthly_income - eff_expenses, 2),
        "savings_rate": round((monthly_income - eff_expenses) / monthly_income, 4) if monthly_income > 0 else None,
        "spent_this_month": round(spent["monthly_total"], 2),
        "expenses": expenses,
        "spent": spent,
        "position_count": len(positions),
        "positions": positions,
        "by_class": classes,
        "by_person": people,
        "liquidity": liquidity,
        "income_due": income_due(today),
        "salary": salary,
        "other_income": other_income,
        "dividends": dividends,
        "fno": fno,
    }
