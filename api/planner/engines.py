"""
Deterministic planning engines — the MATH the advisor must never do in its head.

Pure functions: projections (goal / income), the per-person capital-gains tax
calculator (rates stamped FY2025-26), and the tax-efficient funding waterfall.
The Claude skill orchestrates + explains; these return the numbers. No I/O here
except reading the live snapshot helpers passed in by the caller.
"""
from __future__ import annotations

from typing import Optional

from . import advisor_profile as prof

CESS = 0.04                       # health & education cess on tax
EQ_LTCG = 0.125                   # equity LTCG rate (>₹1.25L/person), FY2025-26
EQ_STCG = 0.20                    # equity STCG
PROP_LTCG = 0.125                 # property/gold LTCG (>24m)


# ── Phase 2: projections ─────────────────────────────────────────────────────
def future_value(current: float, monthly: float, years: float, annual_return: float) -> float:
    """FV of a lump sum + a monthly SIP compounded at `annual_return`."""
    r = annual_return
    fv_lump = current * (1 + r) ** years
    n = years * 12
    rm = (1 + r) ** (1 / 12) - 1
    fv_sip = monthly * (((1 + rm) ** n - 1) / rm) if rm else monthly * n
    return fv_lump + fv_sip


def required_cagr(current: float, monthly: float, years: float, target: float) -> Optional[float]:
    """Annual return needed to reach `target` in `years`. None if unreachable/absurd."""
    if years <= 0 or target <= 0:
        return None
    lo, hi = -0.5, 1.0
    for _ in range(100):
        mid = (lo + hi) / 2
        if future_value(current, monthly, years, mid) < target:
            lo = mid
        else:
            hi = mid
    return round((lo + hi) / 2, 4)


def required_monthly(current: float, years: float, annual_return: float, target: float) -> float:
    """Monthly SIP needed to reach `target`, holding return fixed."""
    fv_lump = current * (1 + annual_return) ** years
    need = max(0.0, target - fv_lump)
    n = years * 12
    rm = (1 + annual_return) ** (1 / 12) - 1
    factor = (((1 + rm) ** n - 1) / rm) if rm else n
    return round(need / factor, 2) if factor else 0.0


def years_to_target(current: float, monthly: float, annual_return: float, target: float) -> Optional[float]:
    """Years to reach `target` at a fixed return + monthly SIP."""
    if current >= target:
        return 0.0
    for y in range(1, 61):
        if future_value(current, monthly, y, annual_return) >= target:
            # linear-interp inside the year for a smoother number
            prev = future_value(current, monthly, y - 1, annual_return)
            cur = future_value(current, monthly, y, annual_return)
            frac = (target - prev) / (cur - prev) if cur > prev else 0
            return round((y - 1) + frac, 1)
    return None


def income_corpus(monthly_income: float, swr: Optional[float] = None) -> float:
    """Corpus needed to sustainably draw `monthly_income` (inflation-adjusted) at a
    safe real withdrawal rate."""
    swr = swr or prof.ASSUMPTIONS["safe_withdrawal_rate"]
    return round(monthly_income * 12 / swr, 2) if swr else 0.0


# ── Phase 3: capital-gains tax ───────────────────────────────────────────────
def tax_on_sale(items: list[dict], headroom: Optional[dict] = None) -> dict:
    """Per-person capital-gains tax for a set of proposed sells. Each item:
      {person, asset_class ('equity'|'property'|'gold'|'debt'|'bond'),
       gain_inr, term ('long'|'short')}
    `headroom` = {person: remaining ₹1.25L equity-LTCG headroom} (else full 1.25L).
    Rates FY2025-26; returns per-person + total (incl. 4% cess). NRI note added but
    the *liability* is the same — TDS is only withholding/timing."""
    headroom = dict(headroom or {})
    by_person: dict[str, dict] = {}
    for it in items:
        p = it.get("person") or "—"
        ac = (it.get("asset_class") or "equity").lower()
        gain = float(it.get("gain_inr") or 0)
        term = (it.get("term") or "long").lower()
        b = by_person.setdefault(p, {"person": p, "residency": prof.residency_of(p),
                                     "taxable_gain_inr": 0.0, "tax_inr": 0.0, "detail": []})
        tax = 0.0
        if gain <= 0:
            note = "loss — offsets other gains"
        elif ac in ("equity", "mf_equity", "stocks"):
            if term == "long":
                hr = headroom.get(p, prof.LTCG_EXEMPT_PER_PERSON_INR)
                taxable = max(0.0, gain - hr)
                headroom[p] = max(0.0, hr - gain)
                tax = taxable * EQ_LTCG
                note = f"equity LTCG 12.5% on ₹{round(taxable)} above headroom"
            else:
                tax = gain * EQ_STCG; note = "equity STCG 20%"
        elif ac in ("property", "apartments", "land", "gold"):
            tax = gain * PROP_LTCG if term == "long" else gain * 0.30
            note = f"{ac} {'LTCG 12.5%' if term=='long' else 'STCG ~slab'}"
        else:  # debt / bond / other → slab (approx at 30% top; advisor refines by regime)
            tax = gain * 0.30; note = f"{ac} at slab (approx 30%)"
        b["taxable_gain_inr"] += max(0.0, gain)
        b["tax_inr"] += tax
        b["detail"].append({"asset_class": ac, "gain_inr": round(gain, 2), "term": term,
                             "tax_inr": round(tax, 2), "note": note})
    for b in by_person.values():
        b["tax_inr"] = round(b["tax_inr"] * (1 + CESS), 2)   # add 4% cess
        b["taxable_gain_inr"] = round(b["taxable_gain_inr"], 2)
    total = round(sum(b["tax_inr"] for b in by_person.values()), 2)
    return {"currency": "INR", "by_person": list(by_person.values()), "total_tax_inr": total,
            "rates": {"equity_ltcg": "12.5% >₹1.25L", "equity_stcg": "20%", "property_ltcg": "12.5% >24m",
                      "cess": "4%"},
            "note": "FY2025-26 rates + 4% cess. Ram (NRI): same liability, but TDS is withheld at source "
                    "(cashflow) — use §197 for property. Slab items depend on each person's regime."}


# ── Phase 3: funding waterfall ───────────────────────────────────────────────
def funding_waterfall(amount: float, sources: dict) -> dict:
    """Allocate `amount` from cheapest → costliest source. `sources` (all ₹):
      {cash, tax_free_harvest, pledge_capacity, sellable_lt_equity, loan_against_property}
    Returns the mix, the tax + interest cost, and the shortfall (if any)."""
    need = float(amount)
    plan, tax, interest = [], 0.0, 0.0

    def take(name, avail, cost_note, tax_rate=0.0, interest_rate=0.0):
        nonlocal need, tax, interest
        if need <= 0 or avail <= 0:
            return
        use = min(need, avail)
        need -= use
        t = use * tax_rate            # tax on the GAIN portion is applied by the caller for equity; here 0 for these tiers
        i = use * interest_rate
        tax += t; interest += i
        plan.append({"source": name, "amount_inr": round(use, 2), "tax_inr": round(t, 2),
                     "annual_interest_inr": round(i, 2), "note": cost_note})

    # cheapest → costliest: no-tax sources (cash, headroom, pledges) BEFORE any
    # taxable sale. Selling long-term equity (tax + lost compounding) is the last
    # resort before it — a no-tax loan beats a taxable sale.
    take("idle_cash", sources.get("cash", 0), "Zero cost. Use first.")
    take("tax_free_ltcg_harvest", sources.get("tax_free_harvest", 0),
         "Sell winners within each person's ₹1.25L headroom → 0 tax.")
    take("loan_against_securities", sources.get("pledge_capacity", 0),
         "Pledge equity — no sale, no tax, keeps compounding.", interest_rate=0.095)
    take("loan_against_property", sources.get("loan_against_property", 0),
         "No tax; ~9–10% interest; avoids §197 / a big property LTCG event.", interest_rate=0.095)
    take("sell_long_term_equity", sources.get("sellable_lt_equity", 0),
         "Last resort: LTCG 12.5% on the gain above headroom + gives up compounding (compute via tax_impact).")

    return {"currency": "INR", "amount_needed_inr": round(amount, 2), "plan": plan,
            "estimated_annual_interest_inr": round(interest, 2),
            "shortfall_inr": round(max(0.0, need), 2),
            "note": "Cheapest-first mix. Exact equity tax on the 'sell_long_term_equity' tier comes from "
                    "tax_impact on the specific lots. A home loan for a house purchase is often better than "
                    "selling compounders — model both."}
