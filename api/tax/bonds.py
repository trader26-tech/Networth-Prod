"""
Bonds / fixed-income tax engine — FY 2025-26 (AY 2026-27), India.

DETERMINISTIC. No LLM. Every number the bonds chatbot ("Bandhan") shows comes from
here. It reads the family's REAL bond book (api/bonds) and grounds the advice in it.

A bond taxes you in two distinct ways — this engine models both:

────────────────────────────────────────────────────────────────────────────────
A) COUPON / INTEREST INCOME — the recurring tax (the big one)
────────────────────────────────────────────────────────────────────────────────
Interest from a bond is "Income from Other Sources", stacked on the holder's other
income and taxed at their SLAB rate. Two things change the bill dramatically:

  • **Tax-free bonds** (PSU 10(15) — NHAI/REC/PFC/IRFC/IREDA/HUDCO): the coupon is
    FULLY EXEMPT. No tax, no TDS, ever. The single cleanest lever.
  • **Who holds it.** Interest is taxed in the HOLDER's slab. Under the new regime a
    person whose *total* income ≤ ₹12 L pays ZERO tax (Sec 87A rebate). So a bond in a
    low-income resident adult's name (an adult child / parent) can be tax-free in their
    hands. Clubbing (Sec 64) blocks a spouse or a minor — the income comes back to you.

  TDS: 10% u/s 193 is deducted at source on taxable bond interest (nothing on tax-free
  bonds). If the holder's income is below the taxable limit, Form 15G/15H stops the TDS
  so the cash isn't locked up waiting for a refund.

Levers (most impactful first): tax-free PSU bonds · hold in a low-slab family member's
name · new-vs-old regime · Form 15G/15H · spread maturities across financial years.

────────────────────────────────────────────────────────────────────────────────
B) CAPITAL GAINS on SALE / REDEMPTION — only if you sell before maturity
────────────────────────────────────────────────────────────────────────────────
Held to maturity, a plain bond is redeemed at par → no capital gain (you were taxed on
the coupons). Sell early on the exchange and any price gain is a capital gain:

  • **Listed** bonds held > 12 months → LONG-TERM: 12.5% (no indexation, Sec 112).
    Sold within 12 months → short-term at slab.
  • **Unlisted** bonds/debentures bought on/after 23-Jul-2024 → Sec 50AA: the gain is
    ALWAYS deemed SHORT-TERM and taxed at slab, however long you held it.
  • Market-Linked Debentures (MLDs) → Sec 50AA, always slab.
  • A capital LOSS on a bond can be set off against other capital gains (e.g. shares/
    property) — a way to shelter a gain elsewhere.

⚠ Estimates for planning. Confirm with a chartered accountant — the exact tax depends on
listing status, holding dates, other income, the regime chosen and the finance act.
"""
from __future__ import annotations

from typing import Optional

from .house import slab_tax, marginal_rate

TDS_RATE = 0.10                       # 10% u/s 193 on taxable bond interest
NEW_REBATE_LIMIT = 1_200_000          # new-regime 87A: total income ≤ ₹12L → nil tax
OLD_REBATE_LIMIT = 500_000            # old-regime 87A: total income ≤ ₹5L → nil tax


# ── PSU issuers whose bonds are commonly the tax-free 10(15) series ──────────────
_TAXFREE_ISSUER_HINTS = ("NHAI", "REC", "PFC", "IRFC", "IREDA", "HUDCO", "NABARD",
                         "NTPC", "IIFCL", "INDIAN RAILWAY", "POWER FINANCE",
                         "RURAL ELECTRIF")


def _prof_other_income(prof: dict, override: Optional[float]) -> float:
    if override is not None:
        return max(0.0, float(override))
    return max(0.0, float(prof.get("other_income") or 0))


def interest_tax(taxable_interest: float, prof: dict, *, other_income: Optional[float] = None,
                 regime: str = "new") -> dict:
    """Incremental slab tax on `taxable_interest` stacked on the holder's other income.
    Returns the tax under the chosen regime + both regimes for comparison."""
    interest = max(0.0, float(taxable_interest or 0))
    other = _prof_other_income(prof, other_income)
    age = prof.get("age")

    def _t(reg):
        return round(slab_tax(other + interest, reg, age) - slab_tax(other, reg, age))

    new_tax, old_tax = _t("new"), _t("old")
    chosen = new_tax if regime == "new" else old_tax
    total_ti = other + interest
    return {
        "interest": round(interest), "other_income": round(other),
        "tax": chosen, "new_tax": new_tax, "old_tax": old_tax,
        "marginal_rate": marginal_rate(other, regime, age),
        "under_rebate": (total_ti <= (NEW_REBATE_LIMIT if regime == "new" else OLD_REBATE_LIMIT)),
        "headroom": max(0.0, round((NEW_REBATE_LIMIT if regime == "new" else OLD_REBATE_LIMIT) - other)),
    }


def _looks_tax_free(bond: dict) -> bool:
    if bond.get("tax_free"):
        return True
    iss = (bond.get("issuer") or "").upper()
    return any(h in iss for h in _TAXFREE_ISSUER_HINTS)


# ══════════════════════════════════════════════════════════════════════════════
#  Per-owner roll-up from the real bond book (the chatbot's single source of truth)
# ══════════════════════════════════════════════════════════════════════════════
def portfolio(bonds: list[dict], profiles: dict) -> dict:
    """bonds = enriched rows from api.bonds.engine.build_summary()['bonds'].
    profiles = {name.lower(): profile}. Returns per-owner interest-tax picture + a
    whole-family roll-up + the best achievable tax if the levers were applied."""

    def prof_for(name: str) -> dict:
        return profiles.get((name or "").strip().lower(),
                            {"name": name, "residency": "resident", "relation": "self",
                             "spouse": None, "other_income": None, "age": None})

    by_owner: dict[str, dict] = {}
    for b in bonds:
        owner = b.get("owner") or "—"
        tf = _looks_tax_free(b)
        interest = float(b.get("annual_income") or 0)
        invested = float(b.get("invested") or 0)
        g = by_owner.setdefault(owner, {
            "owner": owner, "bonds": 0, "invested": 0.0,
            "taxable_interest": 0.0, "taxfree_interest": 0.0,
            "taxable_invested": 0.0, "taxfree_invested": 0.0, "issuers": [],
        })
        g["bonds"] += 1
        g["invested"] += invested
        if tf:
            g["taxfree_interest"] += interest
            g["taxfree_invested"] += invested
        else:
            g["taxable_interest"] += interest
            g["taxable_invested"] += invested
        g["issuers"].append({"issuer": b.get("issuer") or "—", "invested": round(invested),
                             "interest": round(interest), "tax_free": tf,
                             "bond_type": b.get("bond_type") or "Other",
                             "years_to_maturity": b.get("years_to_maturity")})

    # resident, non-clubbed adults a holding could be shifted to (for the "best" case)
    def clubbed(owner_p: dict, target_p: dict) -> bool:
        o = (owner_p.get("name") or "").strip().lower()
        t = (target_p.get("name") or "").strip().lower()
        if (owner_p.get("spouse") or "").strip().lower() == t:
            return True
        if (target_p.get("spouse") or "").strip().lower() == o:
            return True
        if target_p.get("is_minor") and (target_p.get("relation") in ("son", "daughter")):
            return True
        return False

    all_profs = list(profiles.values())

    owners_out = []
    tot = {"invested": 0.0, "taxable_invested": 0.0, "taxfree_invested": 0.0,
           "taxable_interest": 0.0, "taxfree_interest": 0.0, "tds": 0.0,
           "tax_now": 0.0, "tax_best": 0.0}

    for owner, g in by_owner.items():
        op = prof_for(owner)
        it = interest_tax(g["taxable_interest"], op, regime="new")
        # pick the cheaper regime as "current best for this holder as-is"
        tax_now = min(it["new_tax"], it["old_tax"])
        best_regime = "new" if it["new_tax"] <= it["old_tax"] else "old"
        tds = round(g["taxable_interest"] * TDS_RATE)

        # best achievable: shift this owner's taxable interest to the resident adult who
        # taxes it least (often ₹0 if they're under the ₹12L rebate), else keep the owner.
        best_tax, best_to = tax_now, None
        for cand in all_profs:
            if (cand.get("name") or "").strip().lower() == owner.strip().lower():
                continue
            if (cand.get("residency") or "resident") != "resident":
                continue
            if clubbed(op, cand):
                continue
            ct = interest_tax(g["taxable_interest"], cand, regime="new")
            cand_tax = min(ct["new_tax"], ct["old_tax"])
            if cand_tax < best_tax:
                best_tax, best_to = cand_tax, cand.get("name")

        owners_out.append({
            "owner": owner, "residency": op.get("residency") or "resident",
            "other_income": round(_prof_other_income(op, None)),
            "bonds": g["bonds"], "invested": round(g["invested"]),
            "taxable_invested": round(g["taxable_invested"]),
            "taxfree_invested": round(g["taxfree_invested"]),
            "taxable_interest": round(g["taxable_interest"]),
            "taxfree_interest": round(g["taxfree_interest"]),
            "tds": tds, "tax_now": tax_now, "best_regime": best_regime,
            "new_tax": it["new_tax"], "old_tax": it["old_tax"],
            "marginal_rate": it["marginal_rate"], "under_rebate": it["under_rebate"],
            "tax_best": best_tax, "best_shift_to": best_to,
            "issuers": sorted(g["issuers"], key=lambda x: -x["interest"]),
        })

        tot["invested"] += g["invested"]
        tot["taxable_invested"] += g["taxable_invested"]
        tot["taxfree_invested"] += g["taxfree_invested"]
        tot["taxable_interest"] += g["taxable_interest"]
        tot["taxfree_interest"] += g["taxfree_interest"]
        tot["tds"] += tds
        tot["tax_now"] += tax_now
        tot["tax_best"] += best_tax

    owners_out.sort(key=lambda o: -o["taxable_interest"])
    tot = {k: round(v) for k, v in tot.items()}
    tot["total_interest"] = tot["taxable_interest"] + tot["taxfree_interest"]
    tot["saving_max"] = max(0, tot["tax_now"] - tot["tax_best"])
    tot["count"] = sum(o["bonds"] for o in owners_out)
    tot["member_count"] = len(owners_out)
    # what fraction of interest is already tax-free
    tot["taxfree_pct"] = round(tot["taxfree_interest"] / tot["total_interest"], 4) if tot["total_interest"] else 0.0

    return {
        "portfolio": tot,
        "owners": owners_out,
        "capital_gains": {
            "listed_lt_rate": 0.125, "listed_lt_months": 12,
            "note": "Held to maturity a bond is redeemed at par → no capital gain. Sell early: listed "
                    "bonds held >12 months are long-term at 12.5%; unlisted bonds & MLDs (Sec 50AA) are "
                    "always short-term at slab.",
        },
        "assumptions": {
            "fy": "2025-26 (AY 2026-27)", "tds_rate": TDS_RATE,
            "new_rebate_limit": NEW_REBATE_LIMIT, "old_rebate_limit": OLD_REBATE_LIMIT,
            "notes": [
                "Interest is taxed at the holder's slab; tax-free PSU bonds (Sec 10(15)) are fully exempt.",
                "New regime: total income ≤ ₹12L pays nil tax (Sec 87A rebate) — a bond in a low-income "
                "resident adult's name can be tax-free in their hands.",
                "10% TDS (Sec 193) is deducted on taxable bond interest; Form 15G/15H stops it if income "
                "is below the taxable limit.",
                "A gift to a SPOUSE or MINOR is clubbed back (Sec 64) — it saves nothing.",
                "Estimates for planning — confirm with a chartered accountant before you act.",
            ],
        },
    }


# ══════════════════════════════════════════════════════════════════════════════
#  Live optimiser — recompute interest-tax as the user pulls the levers
# ══════════════════════════════════════════════════════════════════════════════
def income_plan(owner_profile: dict, taxable_interest: float, *,
                other_income: Optional[float] = None, regime: str = "new",
                taxfree_switch: float = 0.0, co_owner_profile: Optional[dict] = None,
                co_owner_share: float = 0.0, co_owner_other_income: Optional[float] = None) -> dict:
    """Interest-tax for one holder with the levers applied:
      • `taxfree_switch`  — ₹ of taxable interest moved into tax-free bonds (that slice → ₹0 tax, no TDS)
      • split `co_owner_share` (0..1) of the REMAINING taxable interest to a resident co-holder
    Returns before/after tax + TDS so the chat can show the saving live."""
    regime = "old" if regime == "old" else "new"
    gross = max(0.0, float(taxable_interest or 0))
    switch = max(0.0, min(gross, float(taxfree_switch or 0)))
    remaining = gross - switch                                # still taxable after moving to tax-free
    share = max(0.0, min(1.0, float(co_owner_share or 0)))

    # baseline — the whole taxable interest taxed in the owner's hands, chosen regime
    was = interest_tax(gross, owner_profile, other_income=other_income, regime=regime)["tax"]
    tds_before = round(gross * TDS_RATE)

    owner_portion = remaining * (1.0 - share)
    co_portion = remaining * share

    owner_it = interest_tax(owner_portion, owner_profile, other_income=other_income, regime=regime)
    owner_tax = owner_it["tax"]

    co_owner, co_tax, co_it = None, 0, None
    if share > 0 and co_owner_profile:
        co_it = interest_tax(co_portion, co_owner_profile,
                             other_income=co_owner_other_income, regime=regime)
        co_tax = co_it["tax"]
        co_owner = co_owner_profile.get("name")

    total = owner_tax + co_tax
    tds_after = round(remaining * TDS_RATE)                   # switched-to-tax-free slice loses its TDS

    return {
        "regime": regime,
        "gross_taxable_interest": round(gross),
        "taxfree_switch": round(switch),
        "remaining_taxable": round(remaining),
        "owner": owner_profile.get("name"), "owner_share": round(1.0 - share, 4),
        "owner_portion": round(owner_portion), "owner_tax": owner_tax,
        "owner_other_income": owner_it["other_income"], "owner_marginal_rate": owner_it["marginal_rate"],
        "owner_under_rebate": owner_it["under_rebate"],
        "co_owner": co_owner, "co_share": round(share, 4), "co_portion": round(co_portion),
        "co_tax": co_tax, "co_other_income": (co_it["other_income"] if co_it else 0),
        "co_under_rebate": (co_it["under_rebate"] if co_it else False),
        "was": was, "total_tax": total, "saved": max(0, was - total),
        "tds_before": tds_before, "tds_after": tds_after, "tds_saved": max(0, tds_before - tds_after),
    }
