"""
Bond analytics: build each holding's real cashflow schedule, then derive
monthly income, YTM (money-weighted yield), maturity value and the portfolio's
payout calendar + maturity ladder.

Each bond can carry its own **custom, per-period schedule** (`bond["schedule"]`,
a list of {date, interest, principal}). Real bonds pay staggered amounts that
*vary* every period — so when a custom schedule is present it is the source of
truth and we make NO constant-coupon assumption. Coupon rate is then derived as
an effective average (total interest ÷ years ÷ face); YTM is XIRR over the actual
(varying) cashflows.

When no custom schedule exists we fall back to generating one from purchase to
maturity by frequency + repayment type:
  - bullet     → principal returned in full at maturity, coupons each period
  - amortizing → principal returned in equal slices each period; interest is on
                 the *outstanding* balance (so payments taper)
  - cumulative / zero-coupon → no periodic payout; a single redemption at maturity

`generate_schedule()` seeds the form: given a target YTM it back-solves the
coupon that reproduces that YTM, then emits a (flat) starting schedule the user
edits into the real varying amounts.

TDS: each payment carries `tds` (10% of interest, 0 for tax-free bonds) and
`net` (= total − tds). Principal is never taxed. The frontend toggles gross/net.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any, Optional

FREQ_PPY = {"monthly": 12, "quarterly": 4, "half_yearly": 2, "annual": 1,
            "cumulative": 0, "zero": 0}

TDS_RATE = 0.10  # 10% on interest of taxable bonds

# Credit-rating → numeric score (higher = safer). Used for the combined gauge.
RATING_SCORE = {
    "AAA": 8.0, "AA+": 7.5, "AA": 7.0, "AA-": 6.5,
    "A+": 6.0, "A": 5.5, "A-": 5.0,
    "BBB+": 4.3, "BBB": 4.0, "BBB-": 3.7,
    "BB+": 3.3, "BB": 3.0, "BB-": 2.7,
    "B+": 2.3, "B": 2.0, "B-": 1.7,
    "C": 1.3, "D": 1.0,
}
RATING_MAX = 8.0


# Rating-agency prefixes to strip so "CRISIL A", "CARE A+", "IND A-",
# "Acuite A" score on the grade, not the agency's leading letter.
_RATING_AGENCIES = ("CRISIL", "CARE", "ICRA", "INDIARATINGS", "INDRA", "IND",
                    "ACUITE", "BRICKWORK", "BWR", "INFOMERICS", "SMERA", "FITCH")


def _rating_score(rating: Any) -> Optional[float]:
    if not rating:
        return None
    u = str(rating).upper().replace(" ", "")
    # drop a parenthetical/outlook suffix: "AA-(STABLE)" → "AA-"
    u = u.split("(")[0]
    # strip a leading agency name: "CRISILAA-" → "AA-"
    for ag in _RATING_AGENCIES:
        if u.startswith(ag) and len(u) > len(ag):
            u = u[len(ag):]
            break
    if u in RATING_SCORE:
        return RATING_SCORE[u]
    # progressively trim trailing junk: "AA-/STABLE" → "AA-" → "AA"
    for cut in (4, 3, 2, 1):
        if u[:cut] in RATING_SCORE:
            return RATING_SCORE[u[:cut]]
    return None


def _score_label(score: float) -> str:
    """Nearest rating label for a numeric score."""
    best, gap = "—", 1e9
    for lbl, sc in RATING_SCORE.items():
        if abs(sc - score) < gap:
            best, gap = lbl, abs(sc - score)
    return best


def _pdate(s: Any) -> Optional[date]:
    if not s:
        return None
    try:
        y, m, d = (int(x) for x in str(s)[:10].split("-"))
        return date(y, m, d)
    except Exception:
        return None


def _add_months(d: date, n: int) -> date:
    m = d.month - 1 + n
    y = d.year + m // 12
    m = m % 12 + 1
    # clamp day to month length
    for dd in (d.day, 28, 29, 30, 31):
        try:
            return date(y, m, min(d.day, dd))
        except ValueError:
            continue
    return date(y, m, 28)


def _f(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (ValueError, TypeError):
        return default


def _normalize_custom(rows: Any) -> list[dict]:
    """A stored custom schedule → [{date, interest, principal, total}] sorted by date."""
    out: list[dict] = []
    if not isinstance(rows, list):
        return out
    for r in rows:
        if not isinstance(r, dict):
            continue
        d = _pdate(r.get("date"))
        if not d:
            continue
        interest = round(_f(r.get("interest")), 2)
        principal = round(_f(r.get("principal")), 2)
        out.append({"date": d.isoformat(), "interest": interest,
                    "principal": principal, "total": round(interest + principal, 2)})
    out.sort(key=lambda x: x["date"])
    return out


def schedule(bond: dict) -> list[dict]:
    """[{date, interest, principal, total}] from purchase to maturity.

    If the bond carries a custom `schedule`, that (varying, user-edited) list is
    the source of truth. Otherwise generate from coupon/frequency/repayment.
    """
    custom = _normalize_custom(bond.get("schedule"))
    if custom:
        return custom

    face = _f(bond.get("face_value"), 100.0) * _f(bond.get("quantity"), 0.0)
    rate = _f(bond.get("coupon_rate")) / 100.0
    freq = (bond.get("coupon_freq") or "annual").lower()
    amort = (bond.get("repayment_type") or "bullet").lower() == "amortizing"
    pd_, md = _pdate(bond.get("purchase_date")), _pdate(bond.get("maturity_date"))
    fp = _pdate(bond.get("first_payment_date"))
    ppy = FREQ_PPY.get(freq, 1)
    if not pd_ or not md or md <= pd_ or face <= 0:
        return []

    out: list[dict] = []

    if ppy == 0:  # cumulative / zero-coupon → single redemption
        redeem = bond.get("redemption_value")
        if redeem not in (None, "", 0):
            mv = _f(redeem)
        elif freq == "zero":
            mv = face                                  # redeemed at face
        else:                                          # cumulative: compound annually
            yrs = (md - pd_).days / 365.0
            mv = face * ((1 + rate) ** yrs) if rate > 0 else face
        out.append({"date": md.isoformat(), "interest": round(mv - face if mv > face else 0.0, 2),
                    "principal": round(min(mv, face), 2), "total": round(mv, 2)})
        return out

    step = 12 // ppy
    # anchor: first_payment_date when given, else purchase_date + one step
    anchor = fp or _add_months(pd_, step)
    total_months = (md.year - anchor.year) * 12 + (md.month - anchor.month)
    n = max(1, round(total_months / step) + 1)
    principal_slice = face / n if amort else 0.0
    outstanding = face
    for i in range(n):
        dt = _add_months(anchor, i * step)
        if i == n - 1:
            dt = md
        interest = outstanding * rate / ppy
        principal = principal_slice if amort else (face if i == n - 1 else 0.0)
        principal = min(principal, outstanding)
        outstanding -= principal
        out.append({"date": dt.isoformat(), "interest": round(interest, 2),
                    "principal": round(principal, 2), "total": round(interest + principal, 2)})
    return out


def generate_schedule(invested: float, face_total: float, ytm: float,
                      first_payment_date: Any, maturity_date: Any,
                      coupon_freq: str, repayment_type: str) -> dict:
    """Seed a schedule for the form: back-solve the coupon that reproduces the
    target YTM, then emit a starting (flat) schedule the user edits.

    Returns {schedule: [{date, interest, principal}], coupon_rate}.
    """
    md = _pdate(maturity_date)
    fp = _pdate(first_payment_date)
    if not md or face_total <= 0 or invested <= 0:
        return {"schedule": [], "coupon_rate": 0.0}
    pd_ = fp and _add_months(fp, -(12 // max(1, FREQ_PPY.get((coupon_freq or "annual").lower(), 1))))
    ytm = _f(ytm) / 100.0 if ytm and _f(ytm) > 1 else _f(ytm)  # accept 9 or 0.09

    def sched_for(coupon_pct: float) -> list[dict]:
        b = {"face_value": face_total, "quantity": 1.0, "coupon_rate": coupon_pct,
             "coupon_freq": coupon_freq, "repayment_type": repayment_type,
             "purchase_date": (pd_ or md).isoformat() if pd_ else None,
             "first_payment_date": fp.isoformat() if fp else None,
             "maturity_date": md.isoformat()}
        return schedule(b)

    def ytm_for(coupon_pct: float) -> Optional[float]:
        sch = sched_for(coupon_pct)
        if not sch:
            return None
        flows = [((pd_ or fp or md), -invested)]
        for p in sch:
            flows.append((_pdate(p["date"]), p["total"]))
        return xirr(flows)

    # bisection on coupon% so that ytm_for(coupon) == target ytm
    lo, hi = 0.0, 40.0
    best = 0.0
    for _ in range(60):
        mid = (lo + hi) / 2.0
        y = ytm_for(mid)
        if y is None:
            break
        if abs(y - ytm) < 1e-5:
            best = mid
            break
        if y < ytm:
            lo = mid
        else:
            hi = mid
        best = mid
    sch = sched_for(best)
    return {"schedule": [{"date": p["date"], "interest": p["interest"],
                          "principal": p["principal"]} for p in sch],
            "coupon_rate": round(best, 4)}


def xirr(flows: list[tuple[date, float]]) -> Optional[float]:
    flows = [(d, a) for d, a in flows if d]
    if len(flows) < 2:
        return None
    d0 = min(d for d, _ in flows)
    yrs = [(d - d0).days / 365.0 for d, _ in flows]
    amts = [a for _, a in flows]

    def npv(r: float) -> float:
        s = 0.0
        for y, a in zip(yrs, amts):
            try:
                s += a / ((1.0 + r) ** y)
            except (OverflowError, ZeroDivisionError, ValueError):
                return float("inf")
        return s

    lo, hi = -0.9999, 10.0
    flo, fhi = npv(lo), npv(hi)
    if flo * fhi > 0:
        hi = 1000.0
        fhi = npv(hi)
        if flo * fhi > 0:
            return None
    for _ in range(200):
        mid = (lo + hi) / 2.0
        fm = npv(mid)
        if abs(fm) < 1e-4:
            return round(mid, 6)
        if flo * fm < 0:
            hi = mid
        else:
            lo = mid; flo = fm
    return round((lo + hi) / 2.0, 6)


def _enrich(bond: dict, today: date) -> dict:
    sch = schedule(bond)
    face = _f(bond.get("face_value"), 100.0) * _f(bond.get("quantity"), 0.0)
    invested = _f(bond.get("buy_price"), _f(bond.get("face_value"), 100.0)) * _f(bond.get("quantity"), 0.0)
    tax_free = bool(bond.get("tax_free"))
    net_factor = 1.0 if tax_free else (1.0 - TDS_RATE)
    pd_, md = _pdate(bond.get("purchase_date")), _pdate(bond.get("maturity_date"))

    # YTM from purchase, over the actual (possibly varying) schedule
    flows = [(pd_, -invested)] if pd_ and invested else []
    for p in sch:
        flows.append((_pdate(p["date"]), p["total"]))
    ytm = xirr(flows)

    # effective-average coupon: total life interest ÷ years ÷ face × 100
    total_interest = sum(p["interest"] for p in sch)
    span_yrs = ((md - pd_).days / 365.0) if (pd_ and md and md > pd_) else 0.0
    coupon_rate = (total_interest / span_yrs / face * 100.0) if (span_yrs > 0 and face > 0) else _f(bond.get("coupon_rate"))

    # forward-looking income: interest landing in the next 12 months
    horizon = _add_months(today, 12)
    next_12m_interest = sum(p["interest"] for p in sch
                            if pd_ and (d := _pdate(p["date"])) and today < d <= horizon)
    monthly_income = next_12m_interest / 12.0

    # capital recovered (principal already received) vs still outstanding
    capital_recovered = sum(p["principal"] for p in sch
                            if (d := _pdate(p["date"])) and d <= today)
    future_principal = sum(p["principal"] for p in sch
                           if (d := _pdate(p["date"])) and d > today)
    # next coupon/payment
    future = [p for p in sch if (d := _pdate(p["date"])) and d > today]
    nxt = future[0] if future else None
    yrs_left = (md - today).days / 365.0 if md and md > today else 0.0

    return {
        "id": bond.get("id"),
        "owner": bond.get("owner") or "—",
        "broker": bond.get("broker") or "",
        "issuer": bond.get("issuer") or "—",
        "bond_type": bond.get("bond_type") or "Other",
        "isin": bond.get("isin") or "",
        "rating": bond.get("rating") or "",
        "tax_free": tax_free,
        "face_value": _f(bond.get("face_value"), 100.0),
        "quantity": _f(bond.get("quantity")),
        "buy_price": _f(bond.get("buy_price")),
        "coupon_rate": round(coupon_rate, 4),
        "coupon_freq": bond.get("coupon_freq") or "annual",
        "repayment_type": bond.get("repayment_type") or "bullet",
        "purchase_date": bond.get("purchase_date"),
        "first_payment_date": bond.get("first_payment_date"),
        "maturity_date": bond.get("maturity_date"),
        "ytm_input": _f(bond.get("ytm_input")) if bond.get("ytm_input") not in (None, "") else None,
        "schedule": sch,
        "note": bond.get("note") or "",
        "invested": round(invested, 2),
        "face_total": round(face, 2),
        "annual_income": round(next_12m_interest, 2),
        "annual_income_net": round(next_12m_interest * net_factor, 2),
        "monthly_income": round(monthly_income, 2),
        "monthly_income_net": round(monthly_income * net_factor, 2),
        "ytm": ytm,
        "current_yield": (next_12m_interest / invested) if invested > 0 else None,
        "capital_recovered": round(capital_recovered, 2),
        "capital_recovered_pct": round(capital_recovered / invested, 4) if invested > 0 else 0.0,
        "future_principal": round(future_principal, 2),
        "principal_outstanding": round(future_principal, 2),
        "years_to_maturity": round(yrs_left, 2),
        "next_payment": {"date": nxt["date"], "amount": nxt["total"]} if nxt else None,
    }


def build_summary(raw_bonds: list[dict], today: Optional[date] = None) -> dict:
    today = today or date.today()
    bonds = [_enrich(b, today) for b in raw_bonds]

    total_invested = sum(b["invested"] for b in bonds)
    total_monthly = sum(b["monthly_income"] for b in bonds)
    total_monthly_net = sum(b["monthly_income_net"] for b in bonds)
    total_annual = sum(b["annual_income"] for b in bonds)
    total_annual_net = sum(b["annual_income_net"] for b in bonds)
    total_maturity = sum(b["future_principal"] for b in bonds)
    total_recovered = sum(b["capital_recovered"] for b in bonds)
    avg_coupon = (sum(b["coupon_rate"] * b["invested"] for b in bonds) / total_invested) if total_invested > 0 else None
    avg_years = (sum(b["years_to_maturity"] * b["invested"] for b in bonds) / total_invested) if total_invested > 0 else None
    taxfree_invested = sum(b["invested"] for b in bonds if b["tax_free"])

    # combined credit rating: invested-weighted score over bonds that carry a rating
    rated = [(b, _rating_score(b["rating"])) for b in bonds]
    rated = [(b, s) for b, s in rated if s is not None and b["invested"] > 0]
    rw = sum(b["invested"] for b, _ in rated)
    combined_rating = None
    if rw > 0:
        score = sum(s * b["invested"] for b, s in rated) / rw
        combined_rating = {"score": round(score, 3), "label": _score_label(score),
                           "weighted": round(rw, 2), "max": RATING_MAX,
                           "rated_pct": round(rw / total_invested, 4) if total_invested > 0 else 0.0}

    # portfolio YTM: pooled money-weighted IRR (XIRR) over every bond's REAL
    # schedule — so staggered/varying interest AND staggered principal are all
    # priced at their own dates. Aggregate same-date flows so dense schedules
    # (e.g. many monthly payers) solve cleanly. Falls back to an invested-
    # weighted blend of per-bond YTMs if the pooled solve can't converge.
    pooled: dict[date, float] = {}
    for b in bonds:
        pd_ = _pdate(b["purchase_date"])
        if pd_ and b["invested"]:
            pooled[pd_] = pooled.get(pd_, 0.0) - b["invested"]
        for p in b["schedule"]:
            d = _pdate(p["date"])
            if d:
                pooled[d] = pooled.get(d, 0.0) + p["total"]
    portfolio_ytm = xirr(sorted(pooled.items()))
    if portfolio_ytm is None:
        wsum = sum(b["invested"] for b in bonds if b["ytm"] is not None and b["invested"] > 0)
        if wsum > 0:
            portfolio_ytm = round(sum(b["ytm"] * b["invested"] for b in bonds
                                      if b["ytm"] is not None and b["invested"] > 0) / wsum, 6)

    # per-member
    members: dict[str, dict] = {}
    for b in bonds:
        m = members.setdefault(b["owner"], {"member": b["owner"], "invested": 0.0,
                                            "monthly_income": 0.0, "monthly_income_net": 0.0,
                                            "bonds": 0, "flows": []})
        m["invested"] += b["invested"]
        m["monthly_income"] += b["monthly_income"]
        m["monthly_income_net"] += b["monthly_income_net"]
        m["bonds"] += 1
    for b in bonds:
        mm = members[b["owner"]]
        pd_ = _pdate(b["purchase_date"])
        if pd_ and b["invested"]:
            mm["flows"].append((pd_, -b["invested"]))
        for p in b["schedule"]:
            mm["flows"].append((_pdate(p["date"]), p["total"]))
    member_list = []
    for m in members.values():
        member_list.append({
            "member": m["member"], "invested": round(m["invested"], 2),
            "monthly_income": round(m["monthly_income"], 2),
            "monthly_income_net": round(m["monthly_income_net"], 2), "bonds": m["bonds"],
            "ytm": xirr(m["flows"]),
        })
    member_list.sort(key=lambda x: x["monthly_income"], reverse=True)

    # per-account (family member · broker) — for attribution + filter
    accounts: dict[tuple, dict] = {}
    horizon = _add_months(today, 12)
    for b in bonds:
        key = (b["owner"], b["broker"])
        a = accounts.setdefault(key, {"owner": b["owner"], "broker": b["broker"],
                                      "invested": 0.0, "monthly_income": 0.0,
                                      "monthly_income_net": 0.0, "interest_12m": 0.0,
                                      "principal_12m": 0.0, "net_12m": 0.0, "bonds": 0})
        a["invested"] += b["invested"]
        a["monthly_income"] += b["monthly_income"]
        a["monthly_income_net"] += b["monthly_income_net"]
        a["bonds"] += 1
        nf = 1.0 if b["tax_free"] else (1.0 - TDS_RATE)
        for p in b["schedule"]:
            d = _pdate(p["date"])
            if d and today < d <= horizon:
                a["interest_12m"] += p["interest"]
                a["principal_12m"] += p["principal"]
                a["net_12m"] += p["interest"] * nf + p["principal"]
    payments_by_account = []
    for a in accounts.values():
        payments_by_account.append({k: (round(v, 2) if isinstance(v, float) else v)
                                    for k, v in a.items()})
    payments_by_account.sort(key=lambda x: x["invested"], reverse=True)

    # full future payment schedule, grouped by month, each month listing its
    # individual payments (date · issuer · amount) — Wint-Wealth style. Also a
    # year-by-year cashflow breakdown (interest + principal + total + bonds).
    months: dict[str, dict] = {}
    years: dict[str, dict] = {}
    for b in bonds:
        nf = 1.0 if b["tax_free"] else (1.0 - TDS_RATE)
        for p in b["schedule"]:
            d = _pdate(p["date"])
            if not d or d <= today:
                continue
            tds = 0.0 if b["tax_free"] else round(p["interest"] * TDS_RATE, 2)
            net = round(p["total"] - tds, 2)
            key = f"{d.year:04d}-{d.month:02d}"
            m = months.setdefault(key, {"month": key, "total": 0.0, "interest": 0.0,
                                        "principal": 0.0, "tds": 0.0, "net": 0.0, "payments": []})
            m["total"] += p["total"]; m["interest"] += p["interest"]; m["principal"] += p["principal"]
            m["tds"] += tds; m["net"] += net
            m["payments"].append({
                "date": p["date"], "issuer": b["issuer"], "owner": b["owner"],
                "broker": b["broker"], "tax_free": b["tax_free"],
                "interest": p["interest"], "principal": p["principal"], "total": p["total"],
                "tds": tds, "net": net,
            })
            yk = str(d.year)
            yr = years.setdefault(yk, {"year": yk, "interest": 0.0, "principal": 0.0,
                                       "total": 0.0, "net": 0.0, "matures": set()})
            yr["interest"] += p["interest"]; yr["principal"] += p["principal"]
            yr["total"] += p["total"]; yr["net"] += net
            if p["principal"] > 0:
                yr["matures"].add(b["issuer"])

    payment_schedule = []
    running_recovered = total_recovered  # principal already received before today
    for k in sorted(months):
        m = months[k]
        m["payments"].sort(key=lambda x: x["date"])
        m["count"] = len(m["payments"])
        running_recovered += m["principal"]
        m["capital_recovered"] = round(running_recovered, 2)
        m["capital_recovered_pct"] = round(running_recovered / total_invested, 4) if total_invested > 0 else 0.0
        for f in ("total", "interest", "principal", "tds", "net"):
            m[f] = round(m[f], 2)
        payment_schedule.append(m)

    yearly_cashflow = []
    for k in sorted(years):
        y = years[k]
        yearly_cashflow.append({
            "year": k, "interest": round(y["interest"], 2),
            "principal": round(y["principal"], 2), "total": round(y["total"], 2),
            "net": round(y["net"], 2), "matures": sorted(y["matures"]),
        })

    return {
        "bonds": bonds,
        "members": member_list,
        "payments_by_account": payments_by_account,
        "count": len(bonds),
        "member_count": len(member_list),
        "account_count": len(payments_by_account),
        "total_invested": round(total_invested, 2),
        "total_monthly_income": round(total_monthly, 2),
        "total_monthly_income_net": round(total_monthly_net, 2),
        "total_annual_income": round(total_annual, 2),
        "total_annual_income_net": round(total_annual_net, 2),
        "total_maturity_value": round(total_maturity, 2),
        "total_capital_recovered": round(total_recovered, 2),
        "portfolio_ytm": portfolio_ytm,
        "combined_rating": combined_rating,
        "avg_coupon": round(avg_coupon, 4) if avg_coupon is not None else None,
        "avg_years_to_maturity": round(avg_years, 2) if avg_years is not None else None,
        "taxfree_invested": round(taxfree_invested, 2),
        "payment_schedule": payment_schedule,
        "yearly_cashflow": yearly_cashflow,
    }
