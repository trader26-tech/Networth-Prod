"""
Apartment / residential-house tax engine — FY 2025-26 (AY 2026-27), India.

DETERMINISTIC. No LLM. Every number the apartment chatbot ("Ashish") shows comes from
here. It reuses the shared capital-gains machinery in engine.py (CII, indexation,
surcharge/cess, the 12.5%-vs-20% option) and adds the two things that make a FLAT
different from bare LAND:

────────────────────────────────────────────────────────────────────────────────
A) SELLING A FLAT — capital gains (Sec 45/48/112), FY 2025-26
────────────────────────────────────────────────────────────────────────────────
Same holding test (> 24 months = long-term) and same rate as land:
  • 12.5% WITHOUT indexation (default, transfers on/after 23-Jul-2024), OR
  • a RESIDENT (property acquired before 23-Jul-2024) may instead pay 20% WITH
    indexation if lower — NOT available to a non-resident.
  • Surcharge on the CG tax capped at 15%; 4% cess.

The EXEMPTION is where a flat beats land:
  • **Sec 54** — sell a residential house, reinvest only the CAPITAL GAIN into ANOTHER
    residential house (buy −1/+2 yrs or build 3 yrs) → that much gain is exempt.
    ✦ No "own ≤ 1 house" condition (that's Sec 54F, for land). You can already own
      several houses and still claim Sec 54. This is the big difference.
    ✦ Up to TWO houses allowed once in a lifetime if the gain ≤ ₹2 crore.
    ✦ New-house cost counted for exemption is capped at ₹10 crore.
  • **Sec 54EC** — put the gain (≤ ₹50 lakh per FY) into NHAI/REC/PFC/IRFC bonds within
    6 months → exempt. 5-yr lock-in. Residents AND NRIs.

Gift-to-family lever (identical to land): a tax-free gift (Sec 56(2)(x)) to a RESIDENT
ADULT child unlocks the 20%-indexation option, Sec 54, and drops TDS from the heavy
NRI rate (Sec 195) to just 1% (Sec 194-IA). A gift to a SPOUSE or MINOR is clubbed
back (Sec 64) → saves nothing.

────────────────────────────────────────────────────────────────────────────────
B) RENTAL INCOME — "Income from House Property" (Sec 22-27), FY 2025-26
────────────────────────────────────────────────────────────────────────────────
For a let-out flat:
    Gross Annual Value (GAV)            = annual rent received
  − Municipal taxes actually PAID       (Sec 23) ................ by the owner
  = Net Annual Value (NAV)
  − 30% Standard Deduction on NAV       (Sec 24a) .............. flat, no bills needed
  − Home-loan Interest                  (Sec 24b) .............. FULL for a let-out flat
  = Income from House Property          (may be a loss)
Then taxed at the owner's slab. A loss can be set off up to ₹2 L against other income
(carried forward 8 yrs) — but ONLY under the OLD regime; the new (default) regime does
not allow that set-off against other heads.

Levers to pay less rent-tax (most impactful first):
  1. 30% standard deduction — automatic, needs no proof.
  2. Home-loan interest (Sec 24b) — deduct the full interest on a let-out flat.
  3. Municipal/property tax actually paid — deductible.
  4. Split ownership — put a share in a RESIDENT low-income adult's name so the rent is
     taxed in a lower slab / uses their basic exemption. (Spouse/minor → clubbed.)
  5. Old vs New regime — pick whichever is cheaper for the whole return.
  6. NRI owner — tenant must deduct TDS u/s 195 (≈30% + cess) unless a lower-deduction
     certificate (Form 13) is obtained; the NRI still gets the 30% deduction & interest
     and claims the balance as a refund by filing a return.

⚠ Estimates for planning. Confirm with a chartered accountant — actual tax depends on
exact dates, valuations, other income, deductions claimed and the finance act in force.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from . import engine
from .engine import inr, gross_up, SEC_54EC_CAP, LTCG_FLAT_RATE

# Sec 54 specifics
SEC54_TWO_HOUSE_GAIN_CAP = 2_00_00_000     # ₹2 cr — up to two houses allowed if gain ≤ this
SEC54_NEW_HOUSE_CAP = 10_00_00_000         # ₹10 cr cap on the new-house cost counted
STD_DEDUCTION_HP = 0.30                     # Sec 24(a) 30% standard deduction
CESS = 0.04


# ══════════════════════════════════════════════════════════════════════════════
#  Slab tax — FY 2025-26 (AY 2026-27), individual, both regimes
# ══════════════════════════════════════════════════════════════════════════════
# New regime (default, Budget 2025 slabs)
_NEW_SLABS = [
    (400_000, 0.0), (800_000, 0.05), (1_200_000, 0.10), (1_600_000, 0.15),
    (2_000_000, 0.20), (2_400_000, 0.25), (float("inf"), 0.30),
]
_NEW_REBATE_LIMIT = 1_200_000              # 87A: taxable ≤ ₹12 L → tax nil (new regime)


def _old_slabs(age: Optional[int]) -> list[tuple[float, float]]:
    exempt = 250_000
    if age is not None and age >= 80:
        exempt = 500_000
    elif age is not None and age >= 60:
        exempt = 300_000
    return [(exempt, 0.0), (500_000, 0.05), (1_000_000, 0.20), (float("inf"), 0.30)]


_OLD_REBATE_LIMIT = 500_000                # 87A: taxable ≤ ₹5 L → tax nil (old regime)


def _slab_base_tax(ti: float, slabs: list[tuple[float, float]]) -> float:
    tax, lower = 0.0, 0.0
    for upper, rate in slabs:
        if ti > lower:
            tax += (min(ti, upper) - lower) * rate
            lower = upper
        else:
            break
    return tax


def _surcharge_income(tax: float, ti: float, regime: str) -> float:
    """Standard surcharge by TOTAL income (not the 15% CG cap)."""
    if ti > 5_00_00_000:   rate = 0.37
    elif ti > 2_00_00_000: rate = 0.25
    elif ti > 1_00_00_000: rate = 0.15
    elif ti > 50_00_000:   rate = 0.10
    else:                  rate = 0.0
    if regime == "new":
        rate = min(rate, 0.25)             # new regime caps surcharge at 25%
    return tax * rate


def slab_tax(taxable_income: float, regime: str = "new", age: Optional[int] = None) -> float:
    """Total income-tax (base + surcharge + 4% cess) on `taxable_income` for FY 2025-26."""
    ti = max(0.0, float(taxable_income or 0))
    if regime == "old":
        base = _slab_base_tax(ti, _old_slabs(age))
        if ti <= _OLD_REBATE_LIMIT:
            base = 0.0                     # Sec 87A rebate
    else:
        base = _slab_base_tax(ti, _NEW_SLABS)
        if ti <= _NEW_REBATE_LIMIT:
            base = 0.0                     # Sec 87A rebate (new regime, up to ₹12 L)
    surcharge = _surcharge_income(base, ti, regime)
    return round(base + surcharge + (base + surcharge) * CESS)


def marginal_rate(other_income: float, regime: str = "new", age: Optional[int] = None) -> float:
    """Effective tax rate on the next ₹1 of income stacked on `other_income`."""
    step = 100_000
    t0 = slab_tax(other_income, regime, age)
    t1 = slab_tax(other_income + step, regime, age)
    return round((t1 - t0) / step, 4)


# ══════════════════════════════════════════════════════════════════════════════
#  A) Selling a flat — capital gains + Sec 54 / 54EC scenarios
# ══════════════════════════════════════════════════════════════════════════════
def compute_flat_sale(unit: dict, seller: dict, sale_price: Optional[float] = None,
                      today: Optional[date] = None) -> dict:
    """LTCG picture for selling one flat as `seller`. Reuses the land engine's base
    computation (holding, indexation option, TDS) — identical for immovable property."""
    base = engine.compute_sale(unit, seller, house_count=0, sale_price=sale_price, today=today)
    # Sec 54F fields from the land engine don't apply to a house; replace with Sec 54.
    base.pop("sec54F_eligible", None)
    if base.get("term") == "long" and base.get("tax") is not None:
        gain = base["gain"]
        base["sec54_gain"] = max(0, gain)                      # whole gain can be sheltered by Sec 54
        base["sec54_two_house"] = gain <= SEC54_TWO_HOUSE_GAIN_CAP
        base["sec54EC_shield"] = min(SEC_54EC_CAP, max(0, gain))
    return base


def sale_scenarios(unit: dict, owner_profile: dict, gift_targets: list[dict],
                   today: Optional[date] = None) -> dict:
    """Sell-as-is vs the levers for one flat: Sec 54 (→ ₹0), 54EC bonds, gift-to-son."""
    today = today or date.today()
    base = compute_flat_sale(unit, owner_profile, today=today)
    result = {"base": base, "scenarios": []}
    if base.get("term") != "long" or base.get("tax") is None:
        return result

    gain = base["gain"]

    # Sec 54 — reinvest the GAIN into another house → ₹0 (no house-count limit)
    result["scenarios"].append({
        "key": "sec54", "type": "reinvest", "for_owner": True,
        "title": "Reinvest the gain in another flat (Sec 54) → ₹0 tax",
        "tax": 0, "saved": base["tax"],
        "detail": f"Put the {inr(gain)} capital gain into another residential house "
                  f"(buy within 2 years / build within 3) and the whole gain is exempt. "
                  f"Unlike land, there's NO limit on how many houses you already own.",
        "caveats": ["Only the gain must be reinvested — not the whole sale value.",
                    "Up to two houses allowed once, if the gain ≤ ₹2 crore.",
                    "Hold the new house 3 years or the exemption reverses."],
    })

    # Sec 54EC — cash-friendly, keep the sale money
    shield = min(SEC_54EC_CAP, gain)
    taxable_after = gain - shield
    ec_tax = gross_up(taxable_after * LTCG_FLAT_RATE, taxable_after)["total"]
    result["scenarios"].append({
        "key": "sec54EC", "type": "cash", "for_owner": True,
        "title": "Park ₹50 L of the gain in 54EC bonds",
        "tax": ec_tax, "saved": base["tax"] - ec_tax,
        "detail": f"Invest {inr(shield)} of the gain in NHAI/REC bonds within 6 months. "
                  f"Only {inr(taxable_after)} of the gain stays taxable — you keep the rest as cash.",
        "caveats": ["₹50 lakh is the per-year cap.",
                    "Money locked 5 years at ~5% (taxable) interest."],
    })

    # Gift to a resident adult child, then they sell (unlocks indexation + Sec 54 + 1% TDS)
    for t in gift_targets:
        rel, name = t.get("relation"), t["name"]
        if t.get("clubbed"):
            result["scenarios"].append({
                "key": f"gift_{name}", "type": "blocked", "for_owner": False,
                "title": f"Gift to {name} ({rel}) then sell",
                "tax": base["tax"], "saved": 0,
                "detail": f"❌ {name} is your {rel} — caught by clubbing (Sec 64): the gain is taxed "
                          f"back to you, so it saves nothing.",
                "caveats": [],
            })
            continue
        sub = compute_flat_sale(unit, t, today=today)          # receiver's cost/date carry over
        plain = sub["tax"]
        result["scenarios"].append({
            "key": f"gift_{name}", "type": "cash", "for_owner": False,
            "title": f"Gift to {name} ({rel}, resident) then sell for cash",
            "tax": plain, "saved": base["tax"] - plain,
            "detail": (f"A gift to an adult child is tax-free & not clubbed. As a resident, {name} can "
                       f"use 20%-with-indexation → {inr(plain)}"
                       + (f" vs {inr(base['tax'])} as an NRI" if plain < base["tax"] else
                          f" (here the flat 12.5% still wins, so it matches, but")
                       + f" only 1% TDS applies instead of the heavy NRI TDS u/s 195."),
            "caveats": ["Gift-deed stamp duty applies (state-specific).",
                        "Gift genuinely & well before the sale.",
                        "Cost & purchase date carry over — nothing resets."],
        })
        result["scenarios"].append({
            "key": f"gift54_{name}", "type": "reinvest", "for_owner": False,
            "title": f"Gift to {name}, they reinvest the gain (Sec 54) → ₹0",
            "tax": 0, "saved": base["tax"],
            "detail": f"After the gift, {name} reinvests the gain in another house under Sec 54 → ₹0 tax.",
            "caveats": ["Proceeds' gain becomes a house, not cash.", "New house held 3 years."],
        })

    live = [s for s in result["scenarios"] if s.get("type") != "blocked"]
    cash = [s for s in live if s.get("type") == "cash"]
    if cash:
        result["best_cash"] = min(cash, key=lambda s: s["tax"])
    if live:
        result["best"] = min(live, key=lambda s: s["tax"])
    return result


def optimize_sale(items: list[dict], prefs: dict, today: Optional[date] = None) -> dict:
    """Sale-plan optimiser for the picked flats + chosen amounts.

    prefs: {transfer_ok, sell_fast, bonds_amount, house_amount}
    For a flat, Sec 54 shelters the GAIN reinvested into a new house (not proportion of
    consideration), so ₹X of gain into a new house exempts ₹X of gain — simpler & more
    powerful than land's Sec 54F.
    """
    today = today or date.today()
    transfer_ok = bool(prefs.get("transfer_ok"))
    sell_fast = bool(prefs.get("sell_fast"))
    bonds_budget = max(0.0, float(prefs.get("bonds_amount") or 0))
    house_budget = max(0.0, float(prefs.get("house_amount") or 0))

    prepared = []
    for it in items:
        unit = it["asset"]; op = it["owner_profile"]
        sale_value = it.get("registered_value")
        if not sale_value:
            sale_value = float(unit.get("after_brokerage_price") or unit.get("current_estimated_price") or 0)
        sale_value = round(float(sale_value))

        naive = compute_flat_sale(unit, op, sale_price=sale_value, today=today)
        if naive.get("term") != "long" or naive.get("tax") is None:
            continue

        seller_name, via_gift = op.get("name"), False
        if transfer_ok:
            best, best_key = None, None
            for t in it.get("gift_targets", []):
                key = (0,)                                     # any resident adult son helps equally (Sec 54 has no house limit)
                if best is None:
                    best, best_key = t, key
            if best is not None:
                seller_name, via_gift = best["name"], True
        prepared.append({"name": unit.get("name"), "owner": unit.get("owner"), "sale_value": sale_value,
                         "gain": naive["gain"], "tax_naive": naive["tax"], "seller": seller_name,
                         "via_gift": via_gift, "bonds": 0, "house": 0})

    # allocate 54EC bonds (₹50L/flat cap), then Sec-54 new-house money against the gain
    for p in sorted(prepared, key=lambda p: -p["gain"]):
        b = min(SEC_54EC_CAP, p["gain"], bonds_budget)
        p["bonds"] = round(b); bonds_budget -= b
    for p in sorted(prepared, key=lambda p: -p["gain"]):
        rem_gain = max(0.0, p["gain"] - p["bonds"])
        inv = min(rem_gain, house_budget)                      # Sec 54: gain-for-gain shelter
        p["house"] = round(inv); house_budget -= inv

    rows = []
    tot = {"sale": 0, "gain": 0, "tax_naive": 0, "tax_opt": 0, "saved": 0, "cash": 0, "bonds": 0, "house": 0}
    for p in prepared:
        taxable = max(0.0, p["gain"] - p["bonds"] - p["house"])
        tax = gross_up(taxable * LTCG_FLAT_RATE, taxable)["total"]
        cash = round(p["sale_value"] - tax - p["bonds"] - p["house"])

        steps = []
        if p["via_gift"]:
            steps.append(f"Register it in {p['seller']}'s name first (resident son) — a tax-free gift; only 1% TDS.")
        if p["bonds"]:
            steps.append(f"Put {inr(p['bonds'])} of the gain into 54EC bonds (5-yr lock, ~5% interest).")
        if p["house"]:
            steps.append(f"Reinvest {inr(p['house'])} of the gain into another house (Sec 54) — that much gain is exempt.")
        if not steps:
            steps.append("Sell as-is — no shelter chosen, so the full long-term tax applies.")
        if sell_fast and p["via_gift"]:
            steps.append("⚠ Selling fast: allow ~2–4 weeks to register the gift before the sale.")

        rows.append({
            "asset": p["name"], "owner": p["owner"], "sale_value": p["sale_value"], "gain": p["gain"],
            "seller": p["seller"], "via_gift": p["via_gift"],
            "tax_naive": p["tax_naive"], "tax_opt": tax, "saved": p["tax_naive"] - tax,
            "bonds": p["bonds"], "house": p["house"], "cash_in_hand": cash, "used_54": bool(p["house"]),
            "rate_label": "12.5%", "steps": steps,
            "effective_rate": round(tax / p["gain"] * 100, 1) if p["gain"] else 0,
        })
        tot["sale"] += p["sale_value"]; tot["gain"] += p["gain"]
        tot["tax_naive"] += p["tax_naive"]; tot["tax_opt"] += tax; tot["saved"] += p["tax_naive"] - tax
        tot["cash"] += cash; tot["bonds"] += p["bonds"]; tot["house"] += p["house"]

    for k in tot:
        tot[k] = round(tot[k])
    return {"parcels": rows, "totals": tot, "prefs": prefs}


# ══════════════════════════════════════════════════════════════════════════════
#  B) Rental income — house-property computation + how to save it
# ══════════════════════════════════════════════════════════════════════════════
def house_property_income(annual_rent: float, municipal_tax: float = 0.0,
                          loan_interest: float = 0.0) -> dict:
    """The Sec 22-24 computation for a let-out flat (or a group of flats)."""
    gav = max(0.0, float(annual_rent or 0))
    muni = max(0.0, float(municipal_tax or 0))
    nav = max(0.0, gav - muni)
    std = round(nav * STD_DEDUCTION_HP)
    interest = max(0.0, float(loan_interest or 0))
    income = round(nav - std - interest)
    return {
        "gav": round(gav), "municipal_tax": round(muni), "nav": round(nav),
        "std_deduction": std, "loan_interest": round(interest), "income": income,
    }


def rental_tax(annual_rent: float, owner_profile: dict, *, other_income: Optional[float] = None,
               municipal_tax: float = 0.0, loan_interest: float = 0.0,
               regime: str = "new", co_owner_share: float = 0.0,
               co_owner_profile: Optional[dict] = None,
               co_owner_other_income: Optional[float] = None) -> dict:
    """Tax on rental income at the owner's marginal slab, with the money-saving levers.

    Splitting a `co_owner_share` (0..1) to a resident co-owner divides GAV, deductions
    and interest pro-rata, and taxes each person on their own slab.
    """
    regime = "old" if regime == "old" else "new"
    other = float(owner_profile.get("other_income") if other_income is None else other_income) \
        if (other_income is not None or owner_profile.get("other_income") is not None) else 0.0

    share_owner = 1.0 - max(0.0, min(1.0, co_owner_share))

    def _person_tax(rent, muni, interest, prof, oi):
        hp = house_property_income(rent, muni, interest)
        inc = hp["income"]
        age = prof.get("age") if prof else None
        # incremental tax = tax(other + house income) − tax(other); a loss reduces tax
        # (old regime only allows the set-off against other income, capped at ₹2L)
        if inc < 0 and regime == "new":
            inc = 0                                            # new regime: no loss set-off vs other heads
        if inc < 0 and regime == "old":
            inc = max(inc, -200_000)                           # old: set-off capped at ₹2L/yr
        t = slab_tax(oi + inc, regime, age) - slab_tax(oi, regime, age)
        return hp, round(t), age

    owner_hp, owner_t, owner_age = _person_tax(
        share_owner * annual_rent, share_owner * municipal_tax, share_owner * loan_interest,
        owner_profile, other)

    # apples-to-apples "before": the FULL rent taxed to the owner alone at the SAME other
    # income & regime, with NO levers (no loan, no municipal, no split) — so the before→after
    # delta isolates exactly what the loan/municipal/split saved.
    _, no_lever_tax, _ = _person_tax(annual_rent, 0.0, 0.0, owner_profile, other)

    result = {
        "no_lever_tax": no_lever_tax,
        "regime": regime, "annual_rent": round(annual_rent), "owner": owner_profile.get("name"),
        "owner_share": share_owner, "owner_hp": owner_hp, "owner_tax": owner_t,
        "other_income": round(other),
        "co_owner": None, "co_tax": 0, "co_hp": None,
        "marginal_rate": marginal_rate(other, regime, owner_profile.get("age")),
    }

    if co_owner_share > 0 and co_owner_profile:
        co_oi = float(co_owner_other_income if co_owner_other_income is not None
                      else (co_owner_profile.get("other_income") or 0.0))
        co_hp, co_t, _ = _person_tax(
            co_owner_share * annual_rent, co_owner_share * municipal_tax,
            co_owner_share * loan_interest, co_owner_profile, co_oi)
        result["co_owner"] = co_owner_profile.get("name")
        result["co_share"] = co_owner_share
        result["co_hp"] = co_hp
        result["co_tax"] = co_t
        result["co_other_income"] = round(co_oi)

    result["total_tax"] = round(owner_t + result["co_tax"])
    return result
