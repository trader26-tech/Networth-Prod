"""
Compute per-account CAGR and the combined (money-weighted) return across all
family brokerage accounts.

Each account is a single lump: start_amount invested on start_date, worth
end_amount on end_date. Per-account CAGR = (end/start)^(1/years) − 1. The
combined figure is an XIRR over every account's two cashflows, so accounts with
different sizes and time spans are blended correctly.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Optional


def _pdate(s: Any) -> Optional[date]:
    if not s:
        return None
    try:
        y, m, d = (int(x) for x in str(s)[:10].split("-"))
        return date(y, m, d)
    except Exception:
        return None


def _years(d0: Optional[date], d1: Optional[date]) -> Optional[float]:
    if not d0 or not d1:
        return None
    return (d1 - d0).days / 365.0


def cagr(start: float, end: float, yrs: Optional[float]) -> Optional[float]:
    if not start or start <= 0 or end is None or end < 0 or not yrs or yrs <= 0:
        return None
    try:
        return round((end / start) ** (1.0 / yrs) - 1.0, 6)
    except (ValueError, OverflowError, ZeroDivisionError):
        return None


def xirr(flows: list[tuple[date, float]]) -> Optional[float]:
    """Money-weighted annualised return: r where Σ amount/(1+r)^years = 0."""
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


def _enrich(a: dict) -> dict:
    start = float(a.get("start_amount") or 0)
    end = float(a.get("end_amount") or 0)
    sd, ed = _pdate(a.get("start_date")), _pdate(a.get("end_date"))
    yrs = _years(sd, ed)
    gain = end - start
    return {
        "id": a.get("id"),
        "member": a.get("member") or "—",
        "broker": a.get("broker") or "—",
        "start_date": a.get("start_date"),
        "end_date": a.get("end_date"),
        "start_amount": round(start, 2),
        "end_amount": round(end, 2),
        "note": a.get("note") or "",
        "years": round(yrs, 2) if yrs else None,
        "gain": round(gain, 2),
        "gain_pct": (end / start - 1.0) if start > 0 else None,
        "cagr": cagr(start, end, yrs),
    }


def _flows(accounts: list[dict]) -> list[tuple[date, float]]:
    flows: list[tuple[date, float]] = []
    for a in accounts:
        sd, ed = _pdate(a.get("start_date")), _pdate(a.get("end_date"))
        s, e = float(a.get("start_amount") or 0), float(a.get("end_amount") or 0)
        if sd and s:
            flows.append((sd, -s))
        if ed:
            flows.append((ed, e))
    return flows


def build_summary(raw_accounts: list[dict]) -> dict:
    accounts = [_enrich(a) for a in raw_accounts]

    total_start = sum(a["start_amount"] for a in accounts)
    total_end = sum(a["end_amount"] for a in accounts)
    total_gain = total_end - total_start
    combined_cagr = xirr(_flows(raw_accounts))

    # group by member
    members: dict[str, dict] = {}
    member_raw: dict[str, list] = {}
    for a, ra in zip(accounts, raw_accounts):
        m = a["member"]
        g = members.setdefault(m, {"member": m, "start_amount": 0.0, "end_amount": 0.0,
                                   "accounts": 0, "brokers": set()})
        g["start_amount"] += a["start_amount"]
        g["end_amount"] += a["end_amount"]
        g["accounts"] += 1
        g["brokers"].add(a["broker"])
        member_raw.setdefault(m, []).append(ra)
    member_list = []
    for m, g in members.items():
        gain = g["end_amount"] - g["start_amount"]
        member_list.append({
            "member": m,
            "start_amount": round(g["start_amount"], 2),
            "end_amount": round(g["end_amount"], 2),
            "gain": round(gain, 2),
            "gain_pct": (g["end_amount"] / g["start_amount"] - 1.0) if g["start_amount"] > 0 else None,
            "cagr": xirr(_flows(member_raw[m])),
            "accounts": g["accounts"],
            "brokers": sorted(g["brokers"]),
        })
    member_list.sort(key=lambda x: x["end_amount"], reverse=True)

    rated = [a for a in accounts if a["cagr"] is not None]
    best = max(rated, key=lambda a: a["cagr"]) if rated else None
    worst = min(rated, key=lambda a: a["cagr"]) if rated else None
    starts = [a["start_date"] for a in accounts if a["start_date"]]
    ends = [a["end_date"] for a in accounts if a["end_date"]]

    return {
        "accounts": accounts,
        "members": member_list,
        "count": len(accounts),
        "member_count": len(member_list),
        "total_invested": round(total_start, 2),
        "total_current": round(total_end, 2),
        "total_gain": round(total_gain, 2),
        "total_gain_pct": (total_end / total_start - 1.0) if total_start > 0 else None,
        "combined_cagr": combined_cagr,
        "best": {"member": best["member"], "broker": best["broker"], "cagr": best["cagr"]} if best else None,
        "worst": {"member": worst["member"], "broker": worst["broker"], "cagr": worst["cagr"]} if worst else None,
        "earliest_start": min(starts) if starts else None,
        "latest_end": max(ends) if ends else None,
    }
