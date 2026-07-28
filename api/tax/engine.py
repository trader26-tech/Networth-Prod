"""
Land / immovable-property capital-gains tax engine — FY 2025-26 (AY 2026-27), India.

DETERMINISTIC. No LLM. Every number the land chatbot shows comes from here so it is
reproducible and auditable. Rules encoded (with the section they come from):

Holding period (Sec 2(42A)):  immovable property held > 24 months → LONG-TERM.

Long-term capital gains on land (Sec 112, as amended by Finance (No.2) Act 2024):
  • Transfers on/after 23-Jul-2024 → default rate 12.5% WITHOUT indexation.
  • Grandfathering: a RESIDENT individual/HUF, for property acquired BEFORE 23-Jul-2024,
    may instead pay 20% WITH indexation if that is lower (proviso to Sec 112(1)).
    → This option is NOT available to a NON-RESIDENT (NRI) or a company.
  • Surcharge on the tax on capital gains is CAPPED at 15% (even if total income is higher).
  • Health & Education cess 4% on (tax + surcharge).

Short-term (≤ 24 months): added to total income, taxed at slab rates (no special rate).

Exemptions on LTCG from land:
  • Sec 54EC — invest the GAIN (≤ ₹50 lakh per FY) in NHAI/REC/PFC/IRFC bonds within
    6 months; that portion is exempt. 5-year lock-in. Available to residents AND NRIs.
  • Sec 54F — invest the NET SALE CONSIDERATION in ONE residential house
    (buy within −1/+2 yrs, or build within 3 yrs). Exemption = LTCG × invested / net
    consideration. CONDITION: on the sale date the seller must NOT own more than ONE
    residential house (apart from the new one). New-house cost for 54F is capped at ₹10 cr.

Transfer to a family member then sell (the "restructure" lever):
  • Gift to a "relative" (Sec 56(2)(x)) is TAX-FREE in the receiver's hands. Stamp duty on
    the gift deed still applies (state-specific).
  • On a later sale the receiver's cost = the ORIGINAL owner's cost (Sec 49(1)) and the
    holding period INCLUDES the original owner's (Sec 2(42A)) — so nothing is "reset".
  • CLUBBING (Sec 64): a gift to your SPOUSE or a MINOR child is taxed back to YOU, so it
    saves nothing. A gift to an ADULT child / parent / sibling is NOT clubbed.
  → So the saving comes from gifting to a RESIDENT ADULT relative who then (a) can use the
    20%-with-indexation option, (b) may use 54F if they own ≤ 1 house, and (c) faces only
    1% TDS (Sec 194-IA) instead of the heavy NRI TDS under Sec 195.

⚠ Estimates for planning. Confirm with a chartered accountant before acting — actual tax
depends on exact dates, valuations (incl. 1-Apr-2001 fair value for older land), other
income, and the finance act in force at sale.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

# ── rate constants (FY2025-26) ────────────────────────────────────────────────
LTCG_FLAT_RATE = 0.125          # 12.5% no-indexation (Sec 112, post 23-Jul-2024)
LTCG_INDEXED_RATE = 0.20        # 20% with indexation (resident grandfathering option)
CESS = 0.04                     # health & education cess
LTCG_SURCHARGE_CAP = 0.15       # surcharge on capital-gains tax capped at 15%
LT_HOLDING_MONTHS = 24          # immovable property long-term threshold
SEC_54EC_CAP = 5_000_000        # ₹50 lakh per FY
CUTOFF_NEW_REGIME = date(2024, 7, 23)   # 12.5% regime start
BASE_2001 = date(2001, 4, 1)    # indexation base date

# Cost Inflation Index (base 2001-02 = 100). FY2025-26 = 376 (CBDT notified).
CII = {
    2001: 100, 2002: 105, 2003: 109, 2004: 113, 2005: 117, 2006: 122, 2007: 129,
    2008: 137, 2009: 148, 2010: 167, 2011: 184, 2012: 200, 2013: 220, 2014: 240,
    2015: 254, 2016: 264, 2017: 272, 2018: 280, 2019: 289, 2020: 301, 2021: 317,
    2022: 331, 2023: 348, 2024: 363, 2025: 376,
}
CII_SALE = CII[2025]            # sale in FY2025-26


def _fy_start_year(d: date) -> int:
    """Indian financial year start year (Apr-Mar). 2024-05-10 → 2024; 2024-02-10 → 2023."""
    return d.year if d.month >= 4 else d.year - 1


def _cii_for(acq: date) -> int:
    """CII of the acquisition FY, clamped to the 2001-02 base for older assets."""
    y = _fy_start_year(acq)
    if y < 2001:
        return CII[2001]        # indexation base year is 2001-02
    return CII.get(y, CII[2001])


def _pdate(s) -> Optional[date]:
    if not s:
        return None
    try:
        return date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return None


def _months_between(a: date, b: date) -> int:
    return (b.year - a.year) * 12 + (b.month - a.month) - (1 if b.day < a.day else 0)


def _surcharge_rate(income_proxy: float) -> float:
    """Surcharge band by total income, then capped at 15% for capital-gains tax."""
    if income_proxy > 2_00_00_000:   band = 0.25
    elif income_proxy > 1_00_00_000: band = 0.15
    elif income_proxy > 50_00_000:   band = 0.10
    else:                            band = 0.0
    return min(band, LTCG_SURCHARGE_CAP)


def gross_up(tax: float, income_proxy: float) -> dict:
    """Add surcharge (capped 15%) + 4% cess to a base capital-gains tax."""
    sr = _surcharge_rate(income_proxy)
    surcharge = tax * sr
    cess = (tax + surcharge) * CESS
    return {"base_tax": round(tax), "surcharge_rate": sr, "surcharge": round(surcharge),
            "cess": round(cess), "total": round(tax + surcharge + cess)}


def holding(bought: date, sale: date) -> dict:
    m = _months_between(bought, sale)
    return {"months": m, "years": round(m / 12, 1), "long_term": m >= LT_HOLDING_MONTHS}


def indexed_cost(cost: float, acq: date) -> float:
    return round(cost * CII_SALE / _cii_for(acq))


def ltcg_options(gain_flat: float, gain_indexed: float, is_resident: bool,
                 acquired_before_cutoff: bool, income_proxy: float) -> dict:
    """Return the flat (12.5%) and, when allowed, indexed (20%) computations, and the
    cheaper one the seller would actually use."""
    flat = gross_up(gain_flat * LTCG_FLAT_RATE, income_proxy)
    flat["method"] = "flat"; flat["rate_label"] = "12.5% (no indexation)"; flat["gain"] = round(gain_flat)

    can_index = is_resident and acquired_before_cutoff
    opts = {"flat": flat, "indexed": None, "can_index": can_index}
    if can_index:
        idx = gross_up(max(0.0, gain_indexed) * LTCG_INDEXED_RATE, income_proxy)
        idx["method"] = "indexed"; idx["rate_label"] = "20% (with indexation)"; idx["gain"] = round(gain_indexed)
        opts["indexed"] = idx
        opts["chosen"] = idx if idx["total"] < flat["total"] else flat
    else:
        opts["chosen"] = flat
    return opts


def compute_sale(asset: dict, seller: dict, house_count: int,
                 sale_price: Optional[float] = None, today: Optional[date] = None) -> dict:
    """Full capital-gains picture for selling one land parcel as `seller` (a tax profile)."""
    today = today or date.today()
    bought = _pdate(asset.get("bought_date"))
    cost = float(asset.get("bought_price") or 0)
    sale_price = float(sale_price if sale_price is not None
                       else (asset.get("after_brokerage_price") or asset.get("current_estimated_price") or 0))
    is_resident = (seller.get("residency") or "resident") == "resident"

    hp = holding(bought, today) if bought else {"months": 0, "years": 0, "long_term": True}
    gain_flat = round(sale_price - cost)
    idx_cost = indexed_cost(cost, bought) if bought else cost
    gain_indexed = round(sale_price - idx_cost)
    acquired_before_cutoff = bool(bought and bought < CUTOFF_NEW_REGIME)
    pre_2001 = bool(bought and bought < BASE_2001)

    out = {
        "asset": asset.get("name"), "owner": asset.get("owner"),
        "seller": seller.get("name"), "residency": seller.get("residency"),
        "sale_price": round(sale_price), "cost": round(cost), "indexed_cost": round(idx_cost),
        "holding": hp, "gain": gain_flat, "gain_indexed": gain_indexed,
        "pre_2001": pre_2001, "house_count": house_count,
    }

    if not hp["long_term"]:
        # short-term → slab; we can't compute exact slab without full income, so flag it
        out["term"] = "short"
        out["note"] = "Held ≤ 24 months → short-term: the gain is added to income and taxed at slab rates (up to 30% + surcharge + cess)."
        out["tax"] = None
        return out

    out["term"] = "long"
    opts = ltcg_options(gain_flat, gain_indexed, is_resident, acquired_before_cutoff, gain_flat)
    out["options"] = opts
    out["tax"] = opts["chosen"]["total"]
    out["effective_pct"] = round(opts["chosen"]["total"] / gain_flat * 100, 2) if gain_flat else 0

    # 54F eligibility (needs ≤ 1 residential house on sale date, apart from the new one)
    out["sec54F_eligible"] = house_count <= 1
    # 54EC always available on land LTCG
    out["sec54EC_shield"] = min(SEC_54EC_CAP, max(0, gain_flat))

    # TDS the buyer must deduct
    if is_resident:
        out["tds"] = {"section": "194-IA", "rate": 0.01, "on": "sale value",
                      "amount": round(sale_price * 0.01) if sale_price >= 5_000_000 else 0,
                      "note": "1% of sale value if ≥ ₹50 lakh; adjusted against final tax."}
    else:
        # NRI: TDS u/s 195 on the LTCG at 12.5% + surcharge + cess (needs the gain certified,
        # else buyer deducts on the whole sale value). We show the gain-based figure.
        nri_tds = opts["flat"]["total"]
        out["tds"] = {"section": "195", "rate": round(opts["flat"]["total"] / gain_flat, 4) if gain_flat else 0,
                      "on": "capital gain", "amount": round(nri_tds),
                      "note": "NRI: buyer deducts TDS u/s 195 on the gain (≈12.5%+surcharge+cess). "
                              "Without a lower-deduction certificate (Form 13) the buyer may deduct on the "
                              "FULL sale value — a big cash lock-up until you claim the refund."}
    return out


def scenarios(asset: dict, owner_profile: dict, owner_house_count: int,
              gift_targets: list[dict], today: Optional[date] = None) -> dict:
    """Compare selling as-is vs the main restructuring levers, for one land parcel.

    Each scenario is tagged with a `type`:
      cash     — you still walk away with (most of) the money
      reinvest — tax → ~₹0 but the proceeds must go into a residential house (Sec 54F)
      blocked  — doesn't work (e.g. gift to spouse → clubbing)
    """
    today = today or date.today()
    base = compute_sale(asset, owner_profile, owner_house_count, today=today)
    result = {"base": base, "scenarios": []}
    if base.get("term") != "long" or base.get("tax") is None:
        return result

    gain = base["gain"]
    owner_resident = (owner_profile.get("residency") or "resident") == "resident"

    # ── 54EC bonds — cash-friendly, owner keeps the sale ────────────────────────
    shield = min(SEC_54EC_CAP, gain)
    taxable_after = gain - shield
    ec_tax = gross_up(taxable_after * LTCG_FLAT_RATE, taxable_after)["total"]
    result["scenarios"].append({
        "key": "sec54EC", "type": "cash", "for_owner": True,
        "title": "Park ₹50 L of the gain in 54EC bonds",
        "tax": ec_tax, "saved": base["tax"] - ec_tax,
        "detail": f"Invest {inr(shield)} of the gain in NHAI/REC bonds within 6 months. "
                  f"Only {inr(taxable_after)} of the gain stays taxable — you still take the rest as cash.",
        "caveats": ["₹50 lakh is the per-year cap (so this alone can't zero a big gain).",
                    "Money is locked for 5 years at ~5% (taxable) interest."],
    })

    # ── Owner reinvests in a house (Sec 54F) — only if owner owns ≤ 1 house ──────
    if base.get("sec54F_eligible"):
        result["scenarios"].append({
            "key": "sec54F_owner", "type": "reinvest", "for_owner": True,
            "title": "Reinvest the whole sale value in one house (Sec 54F)",
            "tax": 0, "saved": base["tax"],
            "detail": "Put the entire net sale proceeds into one residential house (buy within 2 years / build within 3) "
                      "and the LTCG is fully exempt → ₹0 tax.",
            "caveats": ["You own ≤ 1 house, so you qualify.",
                        "The money becomes a house, not cash.", "Hold the new house 3 years."],
        })

    # ── Gift to a family member, then they sell ─────────────────────────────────
    for t in gift_targets:
        rel = t.get("relation")
        name = t["name"]
        if t.get("clubbed"):
            result["scenarios"].append({
                "key": f"gift_{name}", "type": "blocked", "for_owner": False,
                "title": f"Gift to {name} ({rel}) then sell",
                "tax": base["tax"], "saved": 0,
                "detail": f"❌ {name} is your {rel} — this is caught by CLUBBING (Sec 64): the gain is taxed "
                          f"back to you, so it saves nothing.",
                "caveats": [],
            })
            continue
        hc = t.get("house_count", 0)
        sub = compute_sale(asset, t, hc, today=today)          # receiver's cost/date carry over
        plain = sub["tax"]                                     # they sell for cash (indexation option applies)
        result["scenarios"].append({
            "key": f"gift_{name}", "type": "cash", "for_owner": False,
            "title": f"Gift to {name} ({rel}, resident) then sell for cash",
            "tax": plain, "saved": base["tax"] - plain,
            "detail": (f"Gift to an adult child is tax-free (Sec 56(2)(x)) and not clubbed. As a resident, "
                       f"{name} can choose 20%-with-indexation → {inr(plain)}"
                       + (f" vs {inr(base['tax'])} for you as an NRI" if plain < base['tax'] else
                          f" — here the flat 12.5% still wins, so it matches your figure, but")
                       + f" only 1% TDS applies instead of the heavy NRI TDS u/s 195."),
            "caveats": ["Gift-deed stamp duty applies (state-specific; Tamil Nadu is concessional for family).",
                        "Gift genuinely & well before the sale — no money routed back to you.",
                        "Cost & purchase date carry over — nothing resets."],
        })
        if sub.get("sec54F_eligible"):
            result["scenarios"].append({
                "key": f"gift54F_{name}", "type": "reinvest", "for_owner": False,
                "title": f"Gift to {name}, they reinvest in a house (Sec 54F) → ₹0",
                "tax": 0, "saved": base["tax"],
                "detail": (f"{name} owns ≤ 1 house, so after the gift they can use Sec 54F: put the whole sale "
                           f"value into one residential house → LTCG fully exempt, ₹0 tax."),
                "caveats": ["Proceeds become a house, not cash.",
                            "Gift-deed stamp duty applies.", "New house must be held 3 years."],
            })

    live = [s for s in result["scenarios"] if s.get("type") != "blocked"]
    cash = [s for s in live if s.get("type") == "cash"]
    if cash:
        result["best_cash"] = min(cash, key=lambda s: s["tax"])
    if live:
        result["best"] = min(live, key=lambda s: s["tax"])      # overall lowest tax (may need reinvest)
    return result


def optimize_plan(items: list[dict], prefs: dict, today: Optional[date] = None) -> dict:
    """Deterministic sale-plan optimiser driven by the user's chosen AMOUNTS.

    prefs: {
      transfer_ok:  bool,     # OK to register a plot in a resident adult child's name first
      sell_fast:    bool,     # must sell quickly (a gift deed needs ~weeks to register)
      bonds_amount: number,   # total ₹ to put into 54EC bonds  (capped ₹50L per parcel)
      house_amount: number,   # total ₹ to reinvest into one house (Sec 54F)
    }
    They needn't shelter everything — whatever isn't put into bonds/house is taxed and
    comes back as cash. Returns per-parcel naive-vs-plan numbers, steps and totals.
    """
    today = today or date.today()
    transfer_ok = bool(prefs.get("transfer_ok"))
    sell_fast = bool(prefs.get("sell_fast"))
    bonds_budget = max(0.0, float(prefs.get("bonds_amount") or 0))
    house_budget = max(0.0, float(prefs.get("house_amount") or 0))

    # ── pass 1: per parcel — seller, gain, the do-nothing (naive) tax, 54F flag ──
    prepared = []
    for it in items:
        asset = it["asset"]; op = it["owner_profile"]; owner_hc = it.get("owner_house_count", 0)
        sale_value = it.get("registered_value")
        if not sale_value:
            sale_value = float(asset.get("after_brokerage_price") or asset.get("current_estimated_price") or 0)
        sale_value = round(float(sale_value))

        naive = compute_sale(asset, op, owner_hc, sale_price=sale_value, today=today)
        if naive.get("term") != "long" or naive.get("tax") is None:
            continue

        seller_name, via_gift, eligible54F = op.get("name"), False, bool(naive.get("sec54F_eligible"))
        if transfer_ok:
            best, best_key = None, None
            for t in it.get("gift_targets", []):
                thc = t.get("house_count", 0)
                key = (0 if thc <= 1 else 1,)          # prefer a ≤1-house son (unlocks 54F)
                if best is None or key < best_key:
                    best, best_key, best_hc = t, key, thc
            if best is not None:
                seller_name, via_gift = best["name"], True
                eligible54F = best_hc <= 1
        prepared.append({"name": asset.get("name"), "owner": asset.get("owner"), "sale_value": sale_value,
                         "gain": naive["gain"], "tax_naive": naive["tax"], "seller": seller_name,
                         "via_gift": via_gift, "eligible54F": eligible54F, "bonds": 0, "house": 0})

    # ── allocate the bonds budget (₹50L/parcel cap) then the house budget (54F) ──
    for p in sorted(prepared, key=lambda p: -p["gain"]):
        b = min(SEC_54EC_CAP, p["gain"], bonds_budget)
        p["bonds"] = round(b); bonds_budget -= b
    for p in sorted([x for x in prepared if x["eligible54F"]], key=lambda p: -p["gain"]):
        inv = min(max(0.0, p["sale_value"] - p["bonds"]), house_budget)   # bonds+house never exceed the sale
        p["house"] = round(inv); house_budget -= inv

    # ── pass 2: tax on what's left, cash in hand, steps ──────────────────────────
    rows = []
    tot = {"sale": 0, "gain": 0, "tax_naive": 0, "tax_opt": 0, "saved": 0, "cash": 0, "bonds": 0, "house": 0}
    for p in prepared:
        rem = max(0.0, p["gain"] - p["bonds"])
        # 54F exempts the gain in proportion to the net consideration reinvested
        exemption = min(rem, p["gain"] * (p["house"] / p["sale_value"])) if p["sale_value"] else 0
        taxable = max(0.0, rem - exemption)
        tax = gross_up(taxable * LTCG_FLAT_RATE, taxable)["total"]
        cash = round(p["sale_value"] - tax - p["bonds"] - p["house"])

        steps = []
        if p["via_gift"]:
            steps.append(f"Register it in {p['seller']}'s name first (resident son) — a tax-free gift; only 1% TDS, and Sec 54F becomes possible.")
        if p["bonds"]:
            steps.append(f"Put {inr(p['bonds'])} of the gain into 54EC bonds (5-yr lock, ~5% interest).")
        if p["house"]:
            steps.append(f"Reinvest {inr(p['house'])} into one house (Sec 54F) — exempts that share of the gain.")
        if not steps:
            steps.append("Sell as-is — no shelter chosen, so the full long-term tax applies.")
        if sell_fast and p["via_gift"]:
            steps.append("⚠ Selling fast: allow ~2–4 weeks to register the gift before the sale.")

        rows.append({
            "asset": p["name"], "owner": p["owner"], "sale_value": p["sale_value"], "gain": p["gain"],
            "seller": p["seller"], "via_gift": p["via_gift"],
            "tax_naive": p["tax_naive"], "tax_opt": tax, "saved": p["tax_naive"] - tax,
            "bonds": p["bonds"], "house": p["house"], "cash_in_hand": cash, "used_54F": bool(p["house"]),
            "rate_label": "12.5%", "steps": steps,
            "effective_rate": round(tax / p["gain"] * 100, 1) if p["gain"] else 0,
        })
        tot["sale"] += p["sale_value"]; tot["gain"] += p["gain"]
        tot["tax_naive"] += p["tax_naive"]; tot["tax_opt"] += tax; tot["saved"] += p["tax_naive"] - tax
        tot["cash"] += cash; tot["bonds"] += p["bonds"]; tot["house"] += p["house"]

    for k in tot:
        tot[k] = round(tot[k])
    return {"parcels": rows, "totals": tot, "prefs": prefs}


def inr(v) -> str:
    """₹ compact, Indian grouping — for the human-readable strings the bot speaks."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "—"
    neg = v < 0; a = abs(v)
    if a >= 1e7:
        s = f"₹{a/1e7:.2f} Cr"
    elif a >= 1e5:
        s = f"₹{a/1e5:.2f} L"
    else:
        s = f"₹{round(a):,.0f}"
    return ("-" + s) if neg else s
