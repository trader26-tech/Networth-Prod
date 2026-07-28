"""
Bank-statement reconciliation for DIVIDENDS.

Same idea as the bonds reconcile, tuned for dividends:
  • A dividend credit in the statement is identified by the narration ("… DIV 25
    26", "FINAL DIV", an "ACH C-" NACH credit …) — UPI/IMPS person transfers and
    NBFC interest (bonds) are ignored.
  • Dividend entries are split per person with amount = per_share × shares. Bank
    narrations are often abbreviated (INB = Indian Bank, BOM, TMB…), so the EXACT
    amount is the primary key; the company name only boosts/​disambiguates.
  • We match each dividend credit to the dividend-log entry with the same amount,
    within a date window (dividends land weeks after the record date), preferring
    the account holder's own rows and a name-token overlap. Matches are handed back
    for the user to confirm; on accept the entries are marked "received".

Reuses the generic statement parser from api.bonds.statement.
"""
from __future__ import annotations

import re
from datetime import datetime

from api.bonds.statement import parse_credits, _tokens, _brand  # generic reader + tokenisers

# Words that mark a credit as a dividend/company payout (vs a UPI/IMPS transfer).
_DIV_HINTS = ("div", "dividend", "fnl", "final", "interim", "ach c-")


def is_dividend_credit(narration: str) -> bool:
    s = (narration or "").lower()
    if s.startswith(("upi-", "imps-", "imps ")):
        return False
    return any(h in s for h in _DIV_HINTS)


def _amount(d: dict) -> float:
    return round(float(d.get("per_share") or 0) * float(d.get("shares") or 0), 2)


def _div_payments(dividends: list[dict]) -> list[dict]:
    out: list[dict] = []
    for d in dividends:
        if (d.get("currency") or "INR").upper() != "INR":
            continue                                   # bank statement is INR
        amt = _amount(d)
        if amt <= 0:
            continue
        name = d.get("name") or d.get("symbol") or ""
        toks = set(_tokens(name)) | set(_tokens(d.get("symbol") or ""))
        out.append({
            "id": str(d.get("id") or ""), "name": name, "symbol": d.get("symbol"),
            "date": str(d.get("date") or "")[:10], "amount": amt,
            "status": d.get("status") or "pending", "person": d.get("person"),
            "toks": toks, "brand": _brand(toks),
        })
    return out


def reconcile(credits: list[dict], dividends: list[dict], holder: str = "",
              window_days: int = 45) -> dict:
    """Match dividend credits to dividend-log entries.

    Buckets: matched (auto-tick, pending + exact amount), review (plausible, confirm),
    already (entry already 'received'), unmatched (no dividend match)."""
    payments = _div_payments(dividends)
    holder_l = (holder or "").strip().lower()
    div_credits = [c for c in credits if is_dividend_credit(c["narration"])]
    # Every OTHER deposit in the statement (UPI/salary/interest/…). Surfaced so the
    # user can eyeball the WHOLE statement and catch a dividend the heuristic skipped
    # — nothing from the file is hidden.
    ignored = [{"date": c["date"], "amount": c["amount"], "narration": c["narration"]}
               for c in credits if not is_dividend_credit(c["narration"])]
    ignored.sort(key=lambda r: r["date"])
    scored = []

    for c in div_credits:
        ntoks = set(_tokens(c["narration"]))
        cd = datetime.fromisoformat(c["date"]).date()
        best = None
        for p in payments:
            amt_diff = abs(c["amount"] - p["amount"])
            if amt_diff > max(1.0, 0.01 * p["amount"]):
                continue                               # amount is the hard key for dividends
            try:
                pd_ = datetime.fromisoformat(p["date"]).date()
            except ValueError:
                continue
            dd = (cd - pd_).days                       # credit lands AFTER the record date
            if dd < -7 or dd > window_days:
                continue
            name_hit = 1 if (p["brand"] & ntoks) else 0
            person = (p.get("person") or "").lower()
            owner_ok = 1 if (not person or person == holder_l) else 0
            # rank: name match, then holder-owned, then amount exactness, then date
            key = (1 - name_hit, 1 - owner_ok, round(amt_diff, 2), abs(dd))
            if best is None or key < best[0]:
                best = (key, p, dd, amt_diff, name_hit, owner_ok)
        if best is not None:
            scored.append((best[0], c, best[1], best[2], best[3], best[4], best[5]))

    # Tightest (name+owner+amount) claims each dividend first; no double-assignment.
    scored.sort(key=lambda x: x[0])
    used, taken_credit = set(), set()
    matched, review, already = [], [], []
    for key, c, p, dd, amt_diff, name_hit, owner_ok in scored:
        cid = (c["date"], c["amount"], c["narration"])
        if p["id"] in used or cid in taken_credit:
            continue
        used.add(p["id"]); taken_credit.add(cid)
        rec = {
            "credit_date": c["date"], "credit_amount": c["amount"], "narration": c["narration"],
            "div_id": p["id"], "name": p["name"], "symbol": p["symbol"], "person": p["person"],
            "div_date": p["date"], "amount": p["amount"], "date_diff": dd,
            "amount_diff": round(amt_diff, 2), "name_match": bool(name_hit),
        }
        # High confidence needs a near-exact amount AND corroboration (the company
        # name in the narration, or the row being the account holder's / untagged).
        # A shared-token, off-by-a-rupee guess (e.g. Tata Comms vs Tata Invest) →
        # review, so nothing is auto-marked on a coincidence.
        strong_amount = amt_diff <= 0.5
        confident = strong_amount and (bool(name_hit) or bool(owner_ok))
        if p["status"] == "received":
            rec["confidence"] = "already"; already.append(rec)
        elif confident:
            rec["confidence"] = "high"; matched.append(rec)
        else:
            rec["confidence"] = "review"; review.append(rec)

    resolved = {(r["credit_date"], r["credit_amount"], r["narration"]) for r in matched + review + already}
    unmatched = [{"date": c["date"], "amount": c["amount"], "narration": c["narration"]}
                 for c in div_credits
                 if (c["date"], c["amount"], c["narration"]) not in resolved]

    for lst in (matched, review, already):
        lst.sort(key=lambda r: r["credit_date"])
    unmatched.sort(key=lambda r: r["date"])
    return {
        "matched": matched, "review": review, "already": already,
        "unmatched": unmatched, "ignored": ignored,
        "counts": {"credits": len(div_credits), "all_credits": len(credits),
                   "matched": len(matched), "review": len(review),
                   "already": len(already), "unmatched": len(unmatched),
                   "ignored": len(ignored)},
    }
