"""
Advisor policy: residency mapping + the family's standing financial goals.

Kept in code (not the KV/DB) on purpose — this is stable *policy* the financial
advisor plans toward, the same philosophy as the Claude skill: live numbers come
from the data tools, but "who is an NRI" and "the ₹100 Cr / ₹5L-a-month targets"
are edited here (git-versioned, no stale-cache risk). Mirrors the residency logic
the skill's tax reference relies on.
"""
from __future__ import annotations

# Lower-cased person names treated as NON-RESIDENT (NRI). Everyone else = resident.
# Ramprasad ("Ram") earns in Kuwait (KWD) and is the NRI; Maha / Ranjeev / Sanjeev
# are residents. Add aliases here if a person shows under a different name.
NRI_PEOPLE = {"ramprasad", "ram"}

LTCG_EXEMPT_PER_PERSON_INR = 125_000.0     # ₹1.25L equity-LTCG exemption, per person, per FY

# Standing goals the advisor plans toward. Editable — set the horizon once confirmed.
GOALS = {
    "net_worth_target_inr": 1_000_000_000,   # ₹100 Crore
    "net_worth_horizon_year": None,          # TODO: confirm target year with the user
    "monthly_income_target_inr": 500_000,    # sustainable ₹5,00,000 / month (inflation-adjusted)
    "notes": "Near-term: fund a house purchase tax-efficiently without derailing the two goals above.",
}

# Planning assumptions the engines default to (surfaced so they're explicit/editable).
ASSUMPTIONS = {
    "equity_return_nominal": 0.11,     # base; band 0.08–0.14
    "debt_return_nominal": 0.065,
    "inflation": 0.06,
    "safe_withdrawal_rate": 0.035,     # for the ₹5L/mo income plan
}


def residency_of(person: str | None) -> str:
    """'nri' for Ram/Ramprasad, else 'resident'."""
    return "nri" if (person or "").strip().lower() in NRI_PEOPLE else "resident"
