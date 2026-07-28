"""
Bank-statement reconciliation for bond interest.

Flow: the user downloads their bank statement (Excel/CSV), uploads it here, and we
  1. parse every CREDIT (money-in) transaction — date, amount, narration;
  2. match each credit to a still-pending bond interest payment, using the
     ISSUER name in the narration (the strong signal), corroborated by the
     amount (net of 10% TDS) and the scheduled date;
  3. hand the matches back for the user to confirm; on accept the caller marks
     each matched payment "received" via bonds.store.set_payment_status.

Only the issuer-name credits (NEFT/ACH from NBFCs) match a bond; equity dividends
("… DIV 25 26"), FD interest ("MONTHLY INTEREST CREDIT …") and transfers fall
through to `unmatched` and never get auto-ticked.
"""
from __future__ import annotations

import io
import re
from datetime import datetime

# Legal-form / plumbing words that carry no identity — stripped before matching
# so "Aye Finance Limited" and "AYE FINANCE LIMITED-RAMPRASAD…" line up. We KEEP
# identity words like finance/capital/credit/microfin/green (they distinguish
# "Muthoot Capital" from "Muthoot Microfin").
_NOISE = {
    "ltd", "limited", "pvt", "private", "co", "company", "the", "and",
    "ncd", "listed", "ppac", "debent", "debenture", "debentures", "bond", "bonds",
    "india", "indian", "llp", "neft", "ach", "imps", "upi", "rtgs", "cr", "dr",
    "ramprasad", "ranjeev", "ramprasad-ranjeev", "sent", "using", "paytm",
    "payment", "int", "interest", "coupon", "dscnb", "account", "acc",
}
_DATE_RE = re.compile(r"^\d{2}/\d{2}/\d{2,4}$")
_AMT_RE = re.compile(r"^-?[\d,]+\.?\d*$")

# Words shared by many NBFC names — they can't identify WHICH issuer on their own,
# so a match must also share a distinctive "brand" token (greaves, ugro, navi…).
_GENERIC = {"finance", "finserv", "financial", "services", "service", "fin", "corp"}


def _tokens(s: str) -> list[str]:
    """Significant lowercase identity tokens from a name/narration."""
    s = re.sub(r"[^a-z0-9 ]", " ", (s or "").lower())
    return [t for t in s.split() if len(t) > 2 and not t.isdigit() and t not in _NOISE]


def _brand(toks: set) -> set:
    """The distinctive (non-generic) identity tokens of an issuer."""
    return {t for t in toks if t not in _GENERIC}


def _num(v) -> float | None:
    s = str(v).strip().replace(",", "")
    if not s or s.lower() == "nan" or not _AMT_RE.match(s):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _iso(dcell: str) -> str | None:
    for fmt in ("%d/%m/%y", "%d/%m/%Y", "%d-%m-%y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(dcell.strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return None


# ── parse ────────────────────────────────────────────────────────────────────────
def parse_credits(content: bytes, filename: str) -> list[dict]:
    """Return [{date: 'YYYY-MM-DD', amount, narration}] for every credit (deposit)
    in the statement. Supports .xls (OLE2), .xlsx and .csv."""
    import pandas as pd

    name = (filename or "").lower()
    bio = io.BytesIO(content)
    if name.endswith(".csv"):
        df = pd.read_csv(bio, header=None, dtype=str, on_bad_lines="skip")
    else:
        engine = "xlrd" if name.endswith(".xls") else "openpyxl"
        try:
            df = pd.read_excel(bio, header=None, dtype=str, engine=engine)
        except Exception:
            bio.seek(0)
            df = pd.read_excel(bio, header=None, dtype=str)   # let pandas pick the engine

    # Locate the transaction header row (Date / Narration / Deposit columns).
    hdr = None
    header: list[str] = []
    for i in range(min(80, len(df))):
        cells = [str(x).strip().lower() for x in df.iloc[i].tolist()]
        if any(c == "date" for c in cells) and any("deposit" in c for c in cells) \
           and any("narration" in c or "description" in c or "particular" in c for c in cells):
            hdr, header = i, cells
            break
    if hdr is None:
        raise ValueError("Couldn't find the statement's transaction table (need Date / Narration / Deposit columns). "
                         "Export the account statement as Excel/CSV and try again.")

    di = header.index("date")
    ni = next(j for j, c in enumerate(header) if "narration" in c or "description" in c or "particular" in c)
    pi = next(j for j, c in enumerate(header) if "deposit" in c)

    credits: list[dict] = []
    for i in range(hdr + 1, len(df)):
        row = df.iloc[i].tolist()
        dcell = str(row[di]).strip() if di < len(row) else ""
        if not _DATE_RE.match(dcell):
            continue
        iso = _iso(dcell)
        if not iso:
            continue
        amt = _num(row[pi]) if pi < len(row) else None
        if not amt or amt <= 0:
            continue
        narr = re.sub(r"\s+", " ", str(row[ni]).strip()) if ni < len(row) else ""
        credits.append({"date": iso, "amount": round(amt, 2), "narration": narr})
    return credits


# ── match ──────────────────────────────────────────────────────────────────────
def _all_payments(bonds: list[dict], status_map: dict) -> list[dict]:
    """Every interest payment across all bonds (net-of-TDS + issuer tokens),
    annotated with its current mark ('received' | 'not_received' | 'pending').
    We keep already-received rows so a credit for them is shown as "already
    marked" rather than being mis-filed as unmatched."""
    from . import engine
    out: list[dict] = []
    for b in bonds:
        bid = str(b.get("id") or "")
        issuer = b.get("issuer") or ""
        toks = set(_tokens(issuer))
        tax_free = bool(b.get("tax_free"))
        for row in engine.schedule(b):
            interest = float(row.get("interest") or 0)
            if interest <= 0:
                continue
            d = str(row.get("date") or "")[:10]
            if not d:
                continue
            tds = 0.0 if tax_free else round(interest * 0.10, 2)
            out.append({
                "bond_id": bid, "issuer": issuer, "owner": b.get("owner"),
                "label": b.get("issuer"), "tax_free": tax_free,
                "toks": toks, "brand": _brand(toks),
                "date": d, "gross": round(interest, 2), "net": round(interest - tds, 2),
                "status": status_map.get((bid, d), "pending"),
            })
    return out


def reconcile(credits: list[dict], bonds: list[dict], status_map: dict,
              window_days: int = 14) -> dict:
    """Match statement credits to bond interest payments.

    A credit matches a payment only when the narration shares a DISTINCTIVE issuer
    token (so "Greaves Finance" ≠ "Manba Finance") and the payout date is within a
    window. Among candidates we pick the one whose scheduled amount (net of 10%
    TDS, or gross) is CLOSEST — that disambiguates two bonds from the same issuer.

    Buckets:
      • matched   — pending payment, amount within ~1% → confident auto-tick
      • review    — pending payment, issuer+date fit but amount off → confirm
      • already   — the matched payment is already marked received (nothing to do)
      • unmatched — no issuer match (dividends, FD interest, transfers)
    """
    payments = _all_payments(bonds, status_map)
    scored = []                                       # (sortkey, credit, payment, dd, amt_diff)

    for c in credits:
        ntoks = set(_tokens(c["narration"]))
        narr_lc = c["narration"].lower()
        cd = datetime.fromisoformat(c["date"]).date()
        best = None
        for p in payments:
            if not (p["brand"] & ntoks):              # need a distinctive issuer token
                continue
            dd = abs((datetime.fromisoformat(p["date"]).date() - cd).days)
            if dd > window_days:
                continue
            amt_diff = min(abs(c["amount"] - p["net"]), abs(c["amount"] - p["gross"]))
            within = amt_diff <= max(2.0, 0.01 * p["gross"])
            owner = (p.get("owner") or "").split()[0].lower()
            owner_hit = bool(owner) and owner in narr_lc     # account holder named in the credit
            # Prefer: within-tolerance, then the named owner, then a still-pending
            # payment (actionable) over an already-received one, then closest £/date.
            key = (0 if within else 1, 0 if owner_hit else 1,
                   0 if p["status"] == "pending" else 1, round(amt_diff, 2), dd)
            if best is None or key < best[0]:
                best = (key, p, dd, amt_diff)
        if best is not None:
            key, p, dd, amt_diff = best
            scored.append((key, c, p, dd, amt_diff))

    # Best (tightest, right-owner, pending) claims each payment first — no dupes.
    scored.sort(key=lambda x: x[0])
    used, taken_credit = set(), set()
    matched, review, already = [], [], []
    for key, c, p, dd, amt_diff in scored:
        cid = (c["date"], c["amount"], c["narration"])
        if (p["bond_id"], p["date"]) in used or cid in taken_credit:
            continue
        used.add((p["bond_id"], p["date"]))
        taken_credit.add(cid)
        within = amt_diff <= max(2.0, 0.01 * p["gross"])   # ~1% covers TDS-rounding wobble
        rec = {
            "credit_date": c["date"], "credit_amount": c["amount"], "narration": c["narration"],
            "bond_id": p["bond_id"], "issuer": p["issuer"], "owner": p["owner"],
            "scheduled_date": p["date"], "gross": p["gross"], "net": p["net"],
            "tax_free": p["tax_free"], "date_diff": dd, "amount_diff": round(amt_diff, 2),
            "amount_ok": within,
            "confidence": "high" if within else "review", "status": p["status"],
        }
        if p["status"] == "received":
            already.append(rec)
        elif within:
            matched.append(rec)
        else:
            review.append(rec)

    resolved = {(r["credit_date"], r["credit_amount"], r["narration"])
                for r in matched + review + already}
    unmatched = [{"date": c["date"], "amount": c["amount"], "narration": c["narration"]}
                 for c in credits
                 if (c["date"], c["amount"], c["narration"]) not in resolved]

    for lst in (matched, review, already):
        lst.sort(key=lambda r: r["scheduled_date"])
    unmatched.sort(key=lambda r: r["date"])
    return {
        "matched": matched, "review": review, "already": already, "unmatched": unmatched,
        "counts": {"credits": len(credits), "matched": len(matched), "review": len(review),
                   "already": len(already), "unmatched": len(unmatched)},
    }
