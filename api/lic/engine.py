"""
Turn each raw LIC policy into plain-English facts a beginner can read at a glance:

  • status            — is it still running, already matured (paid out), or surrendered?
  • life_cover        — how much your family gets if something happens (sum assured)
  • annual_premium    — what you pay per year to keep it
  • premiums_paid     — roughly how much you've put in so far
  • expected_maturity — the guaranteed amount + bonus you'll receive at maturity
  • years_to_maturity — how long until it pays out

…and a household summary: cover in force, yearly outgo, money still to come, money
already received, plus a maturity "ladder" (which policy pays out, and when).

Pure stdlib. Mirrors api/ulip/engine.py.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

_PER_YEAR = {"monthly": 12, "quarterly": 4, "half_yearly": 2, "yearly": 1, "single": 1}

TYPE_LABEL = {
    "endowment": "Endowment",
    "money_back": "Money-Back",
    "ulip": "Unit-Linked (ULIP)",
    "health": "Health",
    "term": "Term",
    "whole_life": "Whole Life",
}
TYPE_BLURB = {
    "endowment": "Savings + life cover. Pays a lump sum (sum assured + bonus) at maturity, or to your family earlier.",
    "money_back": "Pays you money back at intervals during the term, plus a final maturity amount.",
    "ulip": "Premiums are invested in market funds; the payout is the fund value.",
    "health": "Covers hospital / medical costs.",
    "term": "Pure protection — large cover for a low premium, no maturity payout.",
    "whole_life": "Cover for your whole life; pays out to your family.",
}


def _pdate(s) -> Optional[date]:
    if not s:
        return None
    try:
        return date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return None


def _years_between(a: date, b: date) -> float:
    return (b - a).days / 365.25


def _f(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def enrich(policy: dict, today: Optional[date] = None) -> dict:
    today = today or date.today()
    out = dict(policy)

    start = _pdate(policy.get("start_date"))
    maturity = _pdate(policy.get("maturity_date"))
    term = _f(policy.get("term_years"))
    pay_years = _f(policy.get("premium_paying_years")) or term
    freq = (policy.get("premium_frequency") or "yearly")
    per_year = _PER_YEAR.get(freq, 1)

    # yearly outgo
    annual = _f(policy.get("premium_annual"))
    if not annual:
        annual = _f(policy.get("premium")) * per_year
    out["annual_premium"] = round(annual, 2)

    # status — trust the stored value; otherwise infer from the maturity date
    status = (policy.get("status") or "").strip().lower()
    if status not in ("active", "matured", "surrendered", "lapsed"):
        status = "matured" if (maturity and maturity <= today) else "active"
    out["status"] = status
    out["is_active"] = status == "active"
    out["is_matured"] = status == "matured"
    out["is_surrendered"] = status in ("surrendered", "lapsed")

    # life cover (protection). For ULIP with no cover fall back to fund value.
    out["life_cover"] = policy.get("sum_assured")

    # the payout figure people care about
    ptype = policy.get("plan_type")
    total_mat = policy.get("total_maturity")
    if total_mat is None:
        mm = _f(policy.get("maturity_amount")); bn = _f(policy.get("bonus"))
        total_mat = round(mm + bn, 2) if (mm or bn) else None
    if ptype == "ulip":
        out["expected_maturity"] = policy.get("fund_value")
    else:
        out["expected_maturity"] = total_mat

    # premiums paid so far (approximation from the schedule)
    paid_est = None
    if start and annual:
        end = min(today, maturity) if maturity else today
        elapsed = max(0.0, _years_between(start, end))
        years_paid = min(elapsed, pay_years) if pay_years else elapsed
        if status in ("matured",) and pay_years:
            years_paid = pay_years
        paid_est = round(annual * years_paid, 0)
    out["premiums_paid"] = paid_est
    out["premium_paying_years_eff"] = round(pay_years, 1) if pay_years else None
    out["fully_paid"] = bool(status != "active" or (pay_years and start and
                          _years_between(start, today) >= pay_years))

    # time to maturity
    out["years_to_maturity"] = (round(max(0.0, _years_between(today, maturity)), 1)
                                if (maturity and status == "active") else None)
    out["maturity_year"] = maturity.year if maturity else None
    out["start_year"] = start.year if start else None

    # ── how many more years of premium to pay ─────────────────────────────────
    years_left = None
    if status == "active" and pay_years and start:
        elapsed = max(0.0, _years_between(start, today))
        years_left = round(max(0.0, pay_years - elapsed), 1)
    out["premium_years_left"] = years_left
    out["premium_done"] = bool(status == "active" and years_left is not None and years_left <= 0)

    # ── how long the life is covered ──────────────────────────────────────────
    # Jeevan Anand (Table 149) & whole-life plans keep the cover alive *for life*
    # after the endowment term pays out — a key thing to know for a claim.
    plan_name = (policy.get("plan") or "")
    is_whole_life = (bool(policy.get("whole_life")) or ptype == "whole_life"
                     or "149" in plan_name or "jeevan anand" in plan_name.lower())
    out["is_whole_life"] = is_whole_life
    if is_whole_life:
        out["cover_lifelong"] = True
        out["cover_until_label"] = "For life"
    elif maturity:
        out["cover_lifelong"] = False
        out["cover_until_label"] = f"Until {maturity.year}"
    else:
        out["cover_lifelong"] = False
        out["cover_until_label"] = "—"

    # accident rider — extra sum on accidental death/disability (149: equals SA)
    ab = _f(policy.get("accident_benefit"))
    if not ab and is_whole_life:
        ab = _f(policy.get("sum_assured"))
    out["accident_benefit"] = round(ab, 2) if ab else None

    # ── claim readiness — who receives the money, and whom to call ────────────
    out["has_nominee"] = bool((policy.get("nominee") or "").strip())
    out["has_claim_contact"] = bool((policy.get("agent_name") or "").strip()
                                    or (policy.get("agent_phone") or "").strip()
                                    or (policy.get("branch") or "").strip())

    # friendly labels
    out["type_label"] = TYPE_LABEL.get(ptype, (ptype or "Policy").title())
    out["type_blurb"] = TYPE_BLURB.get(ptype, "")
    return out


def build_summary(policies: list[dict], today: Optional[date] = None) -> dict:
    today = today or date.today()
    rows = [enrich(p, today) for p in policies]

    active = [r for r in rows if r["is_active"]]
    matured = [r for r in rows if r["is_matured"]]
    surrendered = [r for r in rows if r["is_surrendered"]]

    in_force_cover = sum(_f(r.get("life_cover")) for r in active)
    annual_premium = sum(_f(r.get("annual_premium")) for r in active)
    expected_maturity = sum(_f(r.get("expected_maturity")) for r in active)
    received = (sum(_f(r.get("expected_maturity")) for r in matured)
                + sum(_f(r.get("fund_value")) for r in surrendered))
    invested = sum(_f(r.get("premiums_paid")) for r in rows)

    # the next policy that will pay out
    upcoming = sorted(
        [r for r in active if _pdate(r.get("maturity_date")) and _f(r.get("expected_maturity"))],
        key=lambda r: r["maturity_date"])
    next_maturity = None
    if upcoming:
        n = upcoming[0]
        next_maturity = {
            "date": n["maturity_date"], "holder": n.get("holder"), "plan": n.get("plan"),
            "policy_number": n.get("policy_number"), "amount": n.get("expected_maturity"),
        }

    # per-person rollup — cover, outgo, payout, PLUS the emergency essentials:
    # how long they're covered, the accident rider, the nominee and whom to call.
    by_holder: dict[str, dict] = {}
    for r in rows:
        h = r.get("holder") or "—"
        b = by_holder.setdefault(h, {
            "holder": h, "count": 0, "active": 0,
            "cover": 0.0, "annual_premium": 0.0, "expected": 0.0,
            "accident_benefit": 0.0, "cover_lifelong": False, "cover_until_year": None,
            "premium_years_left": None, "bonus": 0.0,
            "nominee": None, "nominee_relation": None, "nominee_phone": None,
            "agent_name": None, "agent_phone": None, "branch": None,
            "missing_nominee": 0, "missing_contact": 0,
        })
        b["count"] += 1
        if r["is_active"]:
            b["active"] += 1
            b["cover"] += _f(r.get("life_cover"))
            b["annual_premium"] += _f(r.get("annual_premium"))
            b["expected"] += _f(r.get("expected_maturity"))
            b["accident_benefit"] += _f(r.get("accident_benefit"))
            b["bonus"] += _f(r.get("bonus"))
            if r.get("cover_lifelong"):
                b["cover_lifelong"] = True
            elif r.get("maturity_year"):
                b["cover_until_year"] = max(b["cover_until_year"] or 0, r["maturity_year"])
            # longest remaining premium run across the person's policies
            yl = r.get("premium_years_left")
            if yl is not None:
                b["premium_years_left"] = yl if b["premium_years_left"] is None else max(b["premium_years_left"], yl)
            # first non-empty nominee / agent details represent the person
            for k in ("nominee", "nominee_relation", "nominee_phone", "agent_name", "agent_phone", "branch"):
                if not b[k] and r.get(k):
                    b[k] = r[k]
            if not r.get("has_nominee"):
                b["missing_nominee"] += 1
            if not r.get("has_claim_contact"):
                b["missing_contact"] += 1
    holders = sorted(by_holder.values(), key=lambda x: -x["cover"])
    for b in holders:
        b["cover"] = round(b["cover"], 2)
        b["annual_premium"] = round(b["annual_premium"], 2)
        b["expected"] = round(b["expected"], 2)
        b["accident_benefit"] = round(b["accident_benefit"], 2)
        b["bonus"] = round(b["bonus"], 2)
    missing_nominee_total = sum(1 for r in rows if r["is_active"] and not r.get("has_nominee"))

    # per-type rollup
    by_type: dict[str, dict] = {}
    for r in rows:
        t = r.get("plan_type") or "other"
        b = by_type.setdefault(t, {"type": t, "label": r.get("type_label"),
                                   "count": 0, "active": 0, "expected": 0.0, "cover": 0.0})
        b["count"] += 1
        if r["is_active"]:
            b["active"] += 1
            b["expected"] += _f(r.get("expected_maturity"))
            b["cover"] += _f(r.get("life_cover"))
    types = sorted(by_type.values(), key=lambda x: -x["count"])

    return {
        "count": len(rows),
        "active_count": len(active),
        "matured_count": len(matured),
        "surrendered_count": len(surrendered),
        "in_force_cover": round(in_force_cover, 2),
        "annual_premium": round(annual_premium, 2),
        "monthly_premium": round(annual_premium / 12, 2),
        "expected_maturity": round(expected_maturity, 2),
        "received": round(received, 2),
        "invested": round(invested, 2),
        "next_maturity": next_maturity,
        "missing_nominee": missing_nominee_total,
        "by_holder": holders,
        "by_type": types,
        "ladder": [
            {"date": r["maturity_date"], "year": r.get("maturity_year"),
             "holder": r.get("holder"), "plan": r.get("plan"),
             "policy_number": r.get("policy_number"), "amount": r.get("expected_maturity")}
            for r in upcoming
        ],
        "policies": rows,
    }
