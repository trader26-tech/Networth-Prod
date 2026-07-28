"""
Tax routes — person tax profiles + a deterministic LAND capital-gains analysis that
grounds the (non-LLM) land tax chatbot in the family's real parcels.

  GET  /api/tax/meta                 residency / relation options
  GET  /api/tax/profiles             per-person tax profiles (residency, relations…)
  PUT  /api/tax/profiles/{name}      set a person's tax details (used by the chatbot)
  GET  /api/tax/land                 every land parcel: sell-now tax + restructure scenarios
                                     + a portfolio roll-up (current vs best-case tax).

All numbers come from api/tax/engine.py (FY2025-26). See that file for the rules & the
"estimates — confirm with a CA" caveat.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..tax import engine, profiles, house

router = APIRouter(prefix="/api/tax", tags=["tax"])


def _canon(name) -> str:
    try:
        from ..people import store as people
        return people.canon_owner(name)
    except Exception:
        return (name or "").strip().title()


# ── house counts (for Sec 54F eligibility) from the apartments book ────────────
def _house_counts() -> dict[str, int]:
    counts: dict[str, int] = {}
    try:
        from ..apartments import store as apt
        try:
            from ..people import store as people
            canon = people.canon_owner
        except Exception:
            canon = lambda x: (x or "").strip().title()
        for u in apt.list_units():
            owner = canon(u.get("owner"))
            if owner:
                counts[owner] = counts.get(owner, 0) + 1
    except Exception:
        pass
    return counts


def _land_parcels() -> list[dict]:
    try:
        from ..land import store as land
        return land.list_parcels()
    except Exception:
        return []


class ProfilePatch(BaseModel):
    residency: Optional[str] = None
    relation: Optional[str] = None
    spouse: Optional[str] = None
    dob: Optional[str] = None
    pan: Optional[str] = None
    other_income: Optional[float] = None
    note: Optional[str] = None


@router.get("/meta")
def meta():
    return {"residency": list(profiles.RESIDENCY), "relations": list(profiles.RELATIONS),
            "cii_sale_year": engine.CII_SALE, "sec54EC_cap": engine.SEC_54EC_CAP,
            "ltcg_flat_rate": engine.LTCG_FLAT_RATE, "ltcg_indexed_rate": engine.LTCG_INDEXED_RATE}


@router.get("/profiles")
def list_profiles():
    hc = _house_counts()
    out = []
    for p in profiles.list_profiles():
        p = dict(p)
        p["house_count"] = hc.get(p["name"], 0)
        out.append(p)
    return {"profiles": out, "house_counts": hc}


@router.put("/profiles/{name}")
def set_profile(name: str, patch: ProfilePatch):
    return profiles.upsert_profile(name, patch.model_dump(exclude_none=True))


class PlanIn(BaseModel):
    selections: list[dict]          # [{name, registered_value?}]
    prefs: dict = {}                # {cash_timing, transfer_ok, reinvest_ok, sell_fast}
    save: bool = True


@router.get("/land/prefs")
def get_prefs():
    return profiles.get_prefs()


@router.put("/land/prefs")
def set_prefs(body: dict):
    return profiles.set_prefs(body)


def _items_for(selections: list[dict]) -> tuple[list[dict], bool]:
    """Build the optimizer inputs for the picked parcels, and say whether a
    family-transfer could actually help (owner is an NRI + a resident adult child exists)."""
    hc = _house_counts()
    profs = {p["name"].lower(): p for p in profiles.list_profiles()}
    by_name = {(a.get("name") or "").strip().lower(): a for a in _land_parcels()}

    def prof_for(owner_name: str) -> dict:
        return profs.get((owner_name or "").strip().lower(),
                         {"name": owner_name, "residency": "resident", "relation": "self", "spouse": None})

    resident_child = any(
        (p.get("residency") or "resident") == "resident"
        and (p.get("relation") in ("son", "daughter")) and not p.get("is_minor")
        for p in profs.values())

    items, owner_is_nri = [], False
    for sel in selections:
        asset = by_name.get((sel.get("name") or "").strip().lower())
        if not asset:
            continue
        owner = asset.get("owner"); op = prof_for(owner)
        if (op.get("residency") == "nri"):
            owner_is_nri = True
        targets = []
        for p in profs.values():
            if p["name"].strip().lower() == (owner or "").strip().lower():
                continue
            if profiles.clubbed_between(op, p):        # skip spouse/minor (clubbing)
                continue
            if (p.get("residency") or "resident") != "resident":
                continue
            targets.append({**p, "house_count": hc.get(p["name"], 0)})
        items.append({"asset": asset, "owner_profile": op, "owner_house_count": hc.get(owner, 0),
                      "registered_value": sel.get("registered_value"), "gift_targets": targets})
    return items, (owner_is_nri and resident_child)


@router.post("/land/plan")
def land_plan(body: PlanIn):
    """Deep sale-plan optimisation for the picked parcels + the user's chosen amounts."""
    items, _ = _items_for(body.selections)
    result = engine.optimize_plan(items, body.prefs)
    if body.save:
        profiles.set_prefs({"selections": body.selections, "prefs": body.prefs})
    return result


class ConfigsIn(BaseModel):
    selections: list[dict]


@router.post("/land/configs")
def land_configs(body: ConfigsIn):
    """Three internally-consistent ways to sell the picked plots — from lowest tax to
    most cash — each a full plan with EXACT numbers (so the estimate == the result)."""
    items, can_transfer = _items_for(body.selections)
    BIG = 10 ** 12

    def run(prefs):
        return engine.optimize_plan(items, prefs)

    # B — cut the tax, keep the cash: max out ₹50L/plot 54EC bonds, no house (most practical)
    b = run({"transfer_ok": can_transfer, "sell_fast": False, "bonds_amount": BIG, "house_amount": 0})
    # M — bonds + a house: ₹50L/plot in bonds, the rest into one house (Sec 54F) → ₹0, money split
    m = run({"transfer_ok": can_transfer, "sell_fast": False, "bonds_amount": BIG, "house_amount": BIG})
    # A — everything into a house (Sec 54F) → ₹0, no cash locked in bonds
    a = run({"transfer_ok": can_transfer, "sell_fast": False, "bonds_amount": 0, "house_amount": BIG})
    # C — all cash now: sell as-is, no shelter
    c = run({"transfer_ok": False, "sell_fast": False, "bonds_amount": 0, "house_amount": 0})

    sell_now = c["totals"]["tax_naive"]
    gift = ("Register each plot in a resident son’s name first — a tax-free gift that unlocks these shelters. "
            if can_transfer else "")
    configs = [
        {"key": "balanced", "label": "Cut the tax, keep most cash", "badge": "recommended",
         "why": (gift + "Park up to ₹50 lakh of each plot’s gain in 54EC bonds (locked 5 years, ~5% interest). "
                 "A big tax cut while most of the money stays as cash in your hand."),
         "totals": b["totals"], "parcels": b["parcels"],
         "prefs": {"transfer_ok": can_transfer, "sell_fast": False,
                   "bonds_amount": b["totals"]["bonds"], "house_amount": 0}},
        {"key": "mix", "label": "Zero tax — bonds + a house", "badge": "no tax",
         "why": (gift + "Put ₹50 lakh of each plot into 54EC bonds and the rest into one house (Sec 54F). "
                 "No tax at all, and your money is split between bonds and property rather than all in one place."),
         "totals": m["totals"], "parcels": m["parcels"],
         "prefs": {"transfer_ok": can_transfer, "sell_fast": False,
                   "bonds_amount": m["totals"]["bonds"], "house_amount": m["totals"]["house"]}},
        {"key": "zero", "label": "Zero tax — all into a house", "badge": "no tax",
         "why": (gift + "Reinvest the whole proceeds into one residential house under Sec 54F — the entire gain "
                 "becomes tax-free. Best if you’d rather own a house than hold cash."),
         "totals": a["totals"], "parcels": a["parcels"],
         "prefs": {"transfer_ok": can_transfer, "sell_fast": False,
                   "bonds_amount": 0, "house_amount": a["totals"]["house"]}},
        {"key": "cash", "label": "Take all the cash now", "badge": "most cash",
         "why": "Sell as-is and take everything in hand immediately — you pay the full long-term tax, nothing is locked away.",
         "totals": c["totals"], "parcels": c["parcels"],
         "prefs": {"transfer_ok": False, "sell_fast": False, "bonds_amount": 0, "house_amount": 0}},
    ]
    return {"sell_now_tax": sell_now, "max_saving": sell_now - a["totals"]["tax_opt"],
            "can_transfer": can_transfer, "configs": configs}


@router.get("/land")
def land_analysis():
    """For each land parcel: sell-now tax for the owner + the restructuring scenarios,
    plus a portfolio roll-up. This is the chatbot's single source of truth."""
    hc = _house_counts()
    profs = {p["name"].lower(): p for p in profiles.list_profiles()}
    parcels = _land_parcels()

    def prof_for(owner_name: str) -> dict:
        return profs.get((owner_name or "").strip().lower(),
                         {"name": owner_name, "residency": "resident", "relation": "self", "spouse": None})

    rows = []
    tot_gain = tot_now = tot_cash = tot_best = 0
    for a in parcels:
        owner = a.get("owner")
        op = prof_for(owner)
        owner_hc = hc.get(owner, 0)
        # gift targets = every OTHER profile (sons unblocked; spouse shown as clubbed/blocked)
        targets = []
        for p in profs.values():
            if p["name"].strip().lower() == (owner or "").strip().lower():
                continue
            clubbed = profiles.clubbed_between(op, p)
            # describe the relationship RELATIVE TO THE OWNER (not the household role):
            # a spouse gift is what triggers clubbing, so label it "spouse".
            is_spouse = ((op.get("spouse") or "").strip().lower() == p["name"].strip().lower()
                         or (p.get("spouse") or "").strip().lower() == (owner or "").strip().lower())
            rel = "spouse" if is_spouse else ("minor child" if (clubbed and not is_spouse) else p.get("relation"))
            targets.append({**p, "relation": rel, "house_count": hc.get(p["name"], 0),
                            "clubbed": clubbed})
        sc = engine.scenarios(a, op, owner_hc, targets)
        base = sc["base"]
        rows.append(sc)
        if base.get("term") == "long" and base.get("tax") is not None:
            tot_gain += base["gain"]
            tot_now += base["tax"]
            tot_cash += (sc.get("best_cash") or base)["tax"]
            tot_best += (sc.get("best") or base)["tax"]

    return {
        "parcels": rows,
        "portfolio": {
            "count": len(parcels),
            "total_gain": round(tot_gain),
            "tax_now": round(tot_now),
            "tax_best_cash": round(tot_cash),       # lowest tax while still taking cash
            "tax_best": round(tot_best),            # lowest tax overall (some via reinvest-in-house)
            "saving_cash": round(tot_now - tot_cash),
            "saving_max": round(tot_now - tot_best),
        },
        "assumptions": {
            "fy": "2025-26 (AY 2026-27)",
            "cii_sale": engine.CII_SALE,
            "notes": [
                "Sale value = your recorded current market estimate (edit a parcel to change it).",
                "Surcharge is taken from the gain itself and capped at 15% for capital-gains tax.",
                "Older land (bought before 1-Apr-2001) can use its 1-Apr-2001 fair value as cost — usually lowers tax further; get a registered valuation.",
                "Estimates for planning — confirm with a chartered accountant before you act.",
            ],
        },
    }


# ════════════════════════════════════════════════════════════════════════════════
#  APARTMENTS / FLATS — capital gains (Sec 54) + rental income ("Ashish" chatbot)
# ════════════════════════════════════════════════════════════════════════════════
def _apartment_units() -> list[dict]:
    try:
        from ..apartments import store as apt
        return apt.list_units()
    except Exception:
        return []


def _prof_for(profs: dict, owner_name: str) -> dict:
    return profs.get((owner_name or "").strip().lower(),
                     {"name": owner_name, "residency": "resident", "relation": "self", "spouse": None})


def _gift_targets(profs: dict, owner: str, op: dict) -> list[dict]:
    """Every OTHER resident adult profile a flat could be gifted to (spouse/minor flagged
    as clubbed so the chat can show why it won't help)."""
    targets = []
    for p in profs.values():
        if p["name"].strip().lower() == (owner or "").strip().lower():
            continue
        clubbed = profiles.clubbed_between(op, p)
        is_spouse = ((op.get("spouse") or "").strip().lower() == p["name"].strip().lower()
                     or (p.get("spouse") or "").strip().lower() == (owner or "").strip().lower())
        rel = "spouse" if is_spouse else ("minor child" if (clubbed and not is_spouse) else p.get("relation"))
        # only a resident, non-clubbed adult actually saves tax on a gift
        if not clubbed and (p.get("residency") or "resident") != "resident":
            continue
        targets.append({**p, "relation": rel, "clubbed": clubbed})
    return targets


@router.get("/apartments")
def apartment_analysis():
    """Single source of truth for the apartment tax chatbot:
      • per-flat sell-now tax + Sec 54 / 54EC / gift scenarios
      • rental income (house property) per owner, with the baseline tax
      • a portfolio roll-up (sale + rent)."""
    profs = {p["name"].lower(): p for p in profiles.list_profiles()}
    units = _apartment_units()

    flats, tot_gain = [], 0
    tot_now = tot_cash = tot_best = 0
    for u in units:
        owner = u.get("owner")
        op = _prof_for(profs, owner)
        sc = house.sale_scenarios(u, op, _gift_targets(profs, owner, op))
        base = sc["base"]
        flats.append(sc)
        if base.get("term") == "long" and base.get("tax") is not None:
            tot_gain += base["gain"]
            tot_now += base["tax"]
            tot_cash += (sc.get("best_cash") or base)["tax"]
            tot_best += (sc.get("best") or base)["tax"]

    # ── rental income grouped by owner (only let-out flats) ──────────────────────
    rent_by_owner: dict[str, dict] = {}
    for u in units:
        rent = float(u.get("monthly_rent") or 0)
        if rent <= 0:
            continue
        owner = u.get("owner") or "—"
        g = rent_by_owner.setdefault(owner, {"owner": owner, "monthly_rent": 0.0, "units": []})
        g["monthly_rent"] += rent
        g["units"].append({"name": u.get("name"), "monthly_rent": round(rent),
                           "location": u.get("location")})

    rentals = []
    total_monthly = total_rent_tax = 0.0
    for owner, g in rent_by_owner.items():
        op = _prof_for(profs, owner)
        annual = g["monthly_rent"] * 12
        rt = house.rental_tax(annual, op, regime="new")        # baseline: no loan/municipal, new regime
        total_monthly += g["monthly_rent"]
        total_rent_tax += rt["total_tax"]
        rentals.append({
            "owner": owner, "residency": op.get("residency") or "resident",
            "monthly_rent": round(g["monthly_rent"]), "annual_rent": round(annual),
            "units": g["units"], "hp": rt["owner_hp"], "baseline_tax": rt["total_tax"],
            "marginal_rate": rt["marginal_rate"], "other_income": rt["other_income"],
        })
    rentals.sort(key=lambda r: -r["annual_rent"])

    return {
        "flats": flats,
        "portfolio": {
            "count": len(units),
            "let_out": sum(1 for u in units if float(u.get("monthly_rent") or 0) > 0),
            "total_gain": round(tot_gain),
            "tax_now": round(tot_now), "tax_best_cash": round(tot_cash), "tax_best": round(tot_best),
            "saving_cash": round(tot_now - tot_cash), "saving_max": round(tot_now - tot_best),
            "monthly_rent": round(total_monthly), "annual_rent": round(total_monthly * 12),
            "rent_tax_now": round(total_rent_tax),
        },
        "rentals": rentals,
        "assumptions": {
            "fy": "2025-26 (AY 2026-27)", "cii_sale": engine.CII_SALE,
            "notes": [
                "Selling a flat: only the GAIN need be reinvested (Sec 54) — no limit on other houses you own.",
                "Rent: 30% standard deduction (Sec 24a) is automatic; full home-loan interest (Sec 24b) is deductible on a let-out flat.",
                "Rent tax is the extra tax on top of the owner's other income, at FY 2025-26 slabs — set 'other income' for accuracy.",
                "Estimates for planning — confirm with a chartered accountant before you act.",
            ],
        },
    }


class AptSalePlanIn(BaseModel):
    selections: list[dict]                # [{name, registered_value?}]
    prefs: dict = {}


@router.post("/apartments/sale-plan")
def apartment_sale_plan(body: AptSalePlanIn):
    """Live sale-plan optimiser for the picked flats (Sec 54 gain-reinvest + 54EC + gift)."""
    profs = {p["name"].lower(): p for p in profiles.list_profiles()}
    by_name = {(u.get("name") or "").strip().lower(): u for u in _apartment_units()}
    resident_child = any(
        (p.get("residency") or "resident") == "resident"
        and (p.get("relation") in ("son", "daughter")) and not p.get("is_minor")
        for p in profs.values())

    items, owner_is_nri = [], False
    for sel in body.selections:
        u = by_name.get((sel.get("name") or "").strip().lower())
        if not u:
            continue
        owner = u.get("owner"); op = _prof_for(profs, owner)
        if op.get("residency") == "nri":
            owner_is_nri = True
        targets = [t for t in _gift_targets(profs, owner, op) if not t.get("clubbed")]
        items.append({"asset": u, "owner_profile": op,
                      "registered_value": sel.get("registered_value"), "gift_targets": targets})

    prefs = dict(body.prefs or {})
    prefs.setdefault("transfer_ok", owner_is_nri and resident_child)
    return house.optimize_sale(items, prefs)


class RentPlanIn(BaseModel):
    owner: Optional[str] = None
    annual_rent: Optional[float] = None
    other_income: Optional[float] = None
    municipal_tax: Optional[float] = 0
    loan_interest: Optional[float] = 0
    regime: str = "new"
    co_owner: Optional[str] = None
    co_owner_share: Optional[float] = 0
    co_owner_other_income: Optional[float] = None


@router.post("/apartments/rent-plan")
def apartment_rent_plan(body: RentPlanIn):
    """Rental-income tax for one owner with the levers applied (loan interest, municipal
    tax, regime, and an optional split to a resident co-owner). Returns the full
    house-property computation + the tax, so the chat can show before/after."""
    profs = {p["name"].lower(): p for p in profiles.list_profiles()}
    op = _prof_for(profs, body.owner or "")

    annual = body.annual_rent
    if annual is None:
        monthly = sum(float(u.get("monthly_rent") or 0) for u in _apartment_units()
                      if (u.get("owner") or "").strip().lower() == (body.owner or "").strip().lower())
        annual = monthly * 12

    co_prof = _prof_for(profs, body.co_owner) if body.co_owner else None
    return house.rental_tax(
        float(annual or 0), op,
        other_income=body.other_income,
        municipal_tax=float(body.municipal_tax or 0),
        loan_interest=float(body.loan_interest or 0),
        regime=body.regime,
        co_owner_share=float(body.co_owner_share or 0),
        co_owner_profile=co_prof,
        co_owner_other_income=body.co_owner_other_income,
    )


# ════════════════════════════════════════════════════════════════════════════════
#  LISTED EQUITY — capital gains ("TaxBot" stock chatbot)
#  Realized (Zerodha Console Tax-P&L) + unrealized levers (tradebook FIFO + live px)
# ════════════════════════════════════════════════════════════════════════════════
def _statements_by_person(fy: Optional[str]) -> tuple[dict, list, str]:
    """Sum every uploaded Tax-P&L statement per person for a FY. Returns
    ({person: {stcg, ltcg, intraday, dividends, accounts}}, sorted list of FYs seen)."""
    try:
        from ..stocks import taxpnl
        stmts = taxpnl.list_statements()
    except Exception:
        stmts = []
    fys = sorted({s.get("fy") for s in stmts if s.get("fy")}, reverse=True)
    target = fy or (fys[0] if fys else "")
    agg: dict[str, dict] = {}
    for s in stmts:
        if target and s.get("fy") != target:
            continue
        person = _canon(s.get("person") or s.get("client_name") or s.get("client_id"))
        a = agg.setdefault(person, {"stcg": 0.0, "ltcg": 0.0, "intraday": 0.0,
                                    "dividends": 0.0, "accounts": []})
        a["stcg"] += float(s.get("short_term") or 0)
        a["ltcg"] += float(s.get("long_term") or 0)
        a["intraday"] += float(s.get("intraday") or 0)
        a["dividends"] += float(s.get("dividends") or 0)
        if s.get("client_id"):
            a["accounts"].append(s["client_id"])
    return agg, fys, target


def _trades_by_person() -> dict[str, list[dict]]:
    """Every tradebook trade grouped by the canonical holder name."""
    try:
        from ..stocks import store as stock_store
        rows = stock_store.list_trades()
    except Exception:
        rows = []
    by_person: dict[str, list[dict]] = {}
    for t in rows:
        by_person.setdefault(_canon(t.get("owner")), []).append(t)
    return by_person


@router.get("/equity")
def equity_analysis(fy: Optional[str] = None):
    """Single source of truth for the stock TaxBot: per-person realized CG liability
    (from the Console Tax-P&L) + the three tax-saving levers computed from the live
    tradebook, plus a whole-family roll-up."""
    from ..tax import equity as eq
    from ..stocks import prices as stock_prices
    try:
        from ..stocks import corp_actions as corp_actions_mod
    except Exception:
        corp_actions_mod = None

    stmt_agg, fys, target_fy = _statements_by_person(fy)
    trades_agg = _trades_by_person()
    profs = {p["name"].lower(): p for p in profiles.list_profiles()}

    # every person that appears anywhere
    names = set(stmt_agg) | set(trades_agg) | {p["name"] for p in profs.values()}

    # ── build FIFO open lots per person, collect all symbols, price once ──────────
    lots_by_person: dict[str, dict] = {}
    price_items: list[tuple] = []
    for person in names:
        trs = trades_agg.get(person) or []
        if not trs:
            continue
        if corp_actions_mod:
            try:
                trs, _ = corp_actions_mod.adjust_trades_for_splits(trs)
            except Exception:
                pass
        lots = eq.open_lots(trs)
        lots_by_person[person] = lots
        price_items.extend(eq._price_items(lots))

    price_map, price_source = ({}, "no holdings")
    if price_items:
        try:
            price_map, price_source = stock_prices.get_current_prices(list(set(price_items)))
        except Exception as e:
            price_source = f"price feed error: {e}"

    people_out: list[dict] = []
    fam = {"total_tax": 0.0, "realized_stcg": 0.0, "realized_ltcg": 0.0,
           "harvest_saved": 0.0, "crossover_saved": 0.0, "ltcg_headroom": 0.0,
           "intraday": 0.0, "dividends": 0.0}

    for person in sorted(names):
        prof = profs.get(person.lower(), {})
        residency = prof.get("residency") or "resident"
        other_income = float(prof.get("other_income") or 0)
        st = stmt_agg.get(person, {})
        realized_stcg = float(st.get("stcg") or 0)
        realized_ltcg = float(st.get("ltcg") or 0)
        has_statement = person in stmt_agg

        liability = eq.compute_liability(realized_stcg, realized_ltcg,
                                         other_income=other_income, residency=residency)
        lots = lots_by_person.get(person, {})
        unreal = eq.unrealized(lots, price_map, realized_ltcg=realized_ltcg) if lots else None

        # skip phantom profiles with nothing at all
        if not has_statement and not lots:
            continue

        people_out.append({
            "person": person, "residency": residency, "other_income": other_income,
            "accounts": st.get("accounts", []),
            "has_statement": has_statement,
            "realized": {
                "stcg": round(realized_stcg), "ltcg": round(realized_ltcg),
                "intraday": round(float(st.get("intraday") or 0)),
                "dividends": round(float(st.get("dividends") or 0)),
            },
            "liability": liability,
            "unrealized": unreal,
            "holdings": len(lots),
        })

        fam["total_tax"] += liability["total_tax"]
        fam["realized_stcg"] += realized_stcg
        fam["realized_ltcg"] += realized_ltcg
        fam["intraday"] += float(st.get("intraday") or 0)
        fam["dividends"] += float(st.get("dividends") or 0)
        if unreal:
            fam["harvest_saved"] += unreal["harvest_total_saved"]
            fam["crossover_saved"] += unreal["crossover_total_saved"]
            fam["ltcg_headroom"] += unreal["ltcg_headroom"]

    people_out.sort(key=lambda p: -p["liability"]["total_tax"])
    fam = {k: round(v) for k, v in fam.items()}
    fam["total_saveable"] = fam["harvest_saved"] + fam["crossover_saved"]
    fam["people"] = len(people_out)

    return {
        "fy": target_fy or "2025-2026",
        "fys_available": fys,
        "people": people_out,
        "family": fam,
        "price_source": price_source,
        "assumptions": {
            "stcg_rate": eq.STCG_RATE, "ltcg_rate": eq.LTCG_RATE,
            "ltcg_exempt": eq.LTCG_EXEMPT, "lt_days": eq.LT_DAYS,
            "notes": [
                "Realized STCG/LTCG come from your uploaded Zerodha Console Tax-P&L (Kite's API can't backfill a full year).",
                "STCG on listed equity = 20% (Sec 111A); LTCG = 12.5% on the gain above ₹1.25L (Sec 112A). Surcharge capped 15% + 4% cess.",
                "Short-term loss offsets STCG or LTCG; long-term loss offsets LTCG only; unabsorbed losses carry forward 8 years.",
                "Tax-saving levers (crossover / harvest / headroom) are computed live from your tradebook holdings + Yahoo prices.",
                "Intraday & dividends are taxed at slab (shown for context, not added to the CG tax here).",
                "Estimates for planning — confirm with a chartered accountant before you file or act.",
            ],
        },
    }


class EquityWhatIf(BaseModel):
    person: str
    symbol: str
    qty: float
    price: Optional[float] = None       # default = live price


@router.post("/equity/what-if")
def equity_what_if(body: EquityWhatIf):
    """“If <person> sells <qty> <symbol> today, what's the tax?” — FIFO over their
    open lots, split ST (20%) vs LT (12.5%)."""
    from ..tax import equity as eq
    from ..stocks import prices as stock_prices
    try:
        from ..stocks import corp_actions as corp_actions_mod
    except Exception:
        corp_actions_mod = None

    person = _canon(body.person)
    trs = _trades_by_person().get(person) or []
    if not trs:
        raise HTTPException(404, f"No tradebook holdings found for {person}.")
    if corp_actions_mod:
        try:
            trs, _ = corp_actions_mod.adjust_trades_for_splits(trs)
        except Exception:
            pass
    lots = eq.open_lots(trs)
    sym = body.symbol.strip().upper()
    sym_lots = lots.get(sym)
    if not sym_lots:
        raise HTTPException(404, f"{person} holds no open lots of {sym}.")

    price = body.price
    if not price:
        exch = next((r.get("exchange") for r in sym_lots if r.get("exchange")), "NSE")
        pm, _src = stock_prices.get_current_prices([(sym, exch or "NSE")])
        price = pm.get(sym)
        if not price:
            raise HTTPException(400, f"Couldn't fetch a live price for {sym}; pass an explicit price.")

    profs = {p["name"].lower(): p for p in profiles.list_profiles()}
    other_income = float((profs.get(person.lower(), {}) or {}).get("other_income") or 0)
    result = eq.what_if_sell(sym_lots, float(body.qty), float(price), income_proxy=other_income)
    result.update({"person": person, "symbol": sym})
    return result


# ════════════════════════════════════════════════════════════════════════════════
#  BONDS / FIXED INCOME — coupon-interest tax + how to pay less ("Bandhan" chatbot)
# ════════════════════════════════════════════════════════════════════════════════
def _bond_rows() -> list[dict]:
    """Enriched bond rows (owner, tax_free, annual_income, invested…) from the real book."""
    try:
        from ..bonds import store as bond_store, engine as bond_engine
        return bond_engine.build_summary(bond_store.list_bonds()).get("bonds", [])
    except Exception:
        return []


@router.get("/bonds")
def bonds_analysis():
    """Single source of truth for the bonds TaxBot ("Bandhan"): per-holder coupon-interest
    tax + TDS, the tax-free split, and the best achievable tax if the levers are applied."""
    from ..tax import bonds as bond_tax
    profs = {p["name"].lower(): p for p in profiles.list_profiles()}
    return bond_tax.portfolio(_bond_rows(), profs)


class BondIncomePlanIn(BaseModel):
    owner: Optional[str] = None
    taxable_interest: Optional[float] = None      # default = the owner's real taxable coupon
    other_income: Optional[float] = None
    regime: str = "new"
    taxfree_switch: Optional[float] = 0           # ₹ of taxable interest moved to tax-free bonds
    co_owner: Optional[str] = None
    co_owner_share: Optional[float] = 0
    co_owner_other_income: Optional[float] = None


@router.post("/bonds/income-plan")
def bonds_income_plan(body: BondIncomePlanIn):
    """Live interest-tax optimiser for one holder with the levers applied (regime, split to a
    resident co-holder, and moving a slice into tax-free bonds). Returns before/after."""
    from ..tax import bonds as bond_tax
    profs = {p["name"].lower(): p for p in profiles.list_profiles()}
    op = _prof_for(profs, body.owner or "")

    taxable = body.taxable_interest
    if taxable is None:                            # fall back to the owner's real taxable coupon
        taxable = sum(float(b.get("annual_income") or 0) for b in _bond_rows()
                      if (b.get("owner") or "").strip().lower() == (body.owner or "").strip().lower()
                      and not bond_tax._looks_tax_free(b))

    co_prof = _prof_for(profs, body.co_owner) if body.co_owner else None
    return bond_tax.income_plan(
        op, float(taxable or 0),
        other_income=body.other_income, regime=body.regime,
        taxfree_switch=float(body.taxfree_switch or 0),
        co_owner_profile=co_prof, co_owner_share=float(body.co_owner_share or 0),
        co_owner_other_income=body.co_owner_other_income,
    )

