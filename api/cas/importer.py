"""
Turn a parsed CAS into app records.
===================================

`build_preview()` maps parsed holdings onto the shapes the stocks and bonds
stores expect, without writing anything — the UI shows this for confirmation.
`commit()` then persists the confirmed selection.

Kept separate from parser.py so the parsing (pure, testable against a PDF) is
independent of persistence (which touches Supabase / JSON stores).
"""

from __future__ import annotations

import re
import uuid
from datetime import date
from typing import Any

# Bond names in a CAS embed the coupon and often the redemption date, e.g.
#   "UGRO CAPITAL LTD#11.65% USEC NGRT SUB TR 2 TAX NCUM RTD RED NCD PP- RD 17-05-2031"
_PCT_RE = re.compile(r"(\d{1,2}(?:\.\d{1,4})?)\s*%")
# Some descriptions omit the '%': "GREAVES FINANCE 1 LIMITED 10.50 NCD".
# Requires a decimal point so plain quantities ("1", "17") never match.
_BARE_RATE_RE = re.compile(r"\b(\d{1,2}\.\d{2})\b(?!\s*\d)")
_RD_DATE_RE = re.compile(r"\b(\d{2})-(\d{2})-(\d{4})\b")
_COMPACT_DATE_RE = re.compile(r"\b(\d{2})(\d{2})(20\d{2})\b")
# NCD shorthand maturity, e.g. "26MY28" = 26 May 2028
_MON3 = {
    "JA": 1, "JAN": 1, "FE": 2, "FEB": 2, "MR": 3, "MAR": 3, "AP": 4, "APR": 4,
    "MY": 5, "MAY": 5, "JN": 6, "JUN": 6, "JL": 7, "JUL": 7, "AU": 8, "AUG": 8,
    "SE": 9, "SEP": 9, "OC": 10, "OCT": 10, "NO": 11, "NOV": 11, "DE": 12, "DEC": 12,
}
_SHORT_DATE_RE = re.compile(r"\b(\d{1,2})([A-Z]{2,3})(\d{2})\b")

# Frequency hints that appear in NCD descriptions
# NOTE on NCUM: in CDSL security descriptions "NCUM" means NON-cumulative (a
# regular coupon payer), and "CUM" means cumulative. Matching "NCUM" as
# cumulative is exactly backwards, so NCUM is checked first and maps to a
# regular payer; only a standalone CUM/CUMULATIVE means cumulative.
_FREQ_HINTS = [
    (r"\bMTH?LY\b|\bMONTHLY\b", "monthly"),
    (r"\bQ(?:TR|UARTER)LY?\b|\bQLY\b", "quarterly"),
    (r"\bH(?:ALF)?\s?Y(?:EAR)?LY\b|\bSEMI\b", "half_yearly"),
    (r"\bANN(?:UAL)?LY?\b|\bYEARLY\b", "annual"),
    (r"\bZERO\s?C(?:OUPON)?\b|\bZCB\b", "zero"),
    (r"\bNCUM\b|\bNON[-\s]?CUM(?:ULATIVE)?\b", "annual"),
    (r"(?<!N)\bCUM(?:ULATIVE)?\b", "cumulative"),
]

_RATING_RE = re.compile(r"\b(AAA|AA\+|AA-|AA|A\+|A-|A|BBB\+|BBB-|BBB|BB|B|C|D)\b")


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _guess_coupon(name: str, parsed: float | None) -> float | None:
    """Coupon % — prefer the parser's column read, else the rate in the name."""
    if parsed:
        return parsed
    for pat in (_PCT_RE, _BARE_RATE_RE):
        m = pat.search(name or "")
        if not m:
            continue
        try:
            v = float(m.group(1))
        except ValueError:
            continue
        if 0 < v <= 30:
            return v
    return None


def _guess_maturity(name: str, parsed: str | None) -> str | None:
    """ISO maturity — prefer the parsed column, else a date inside the name."""
    if parsed:
        return parsed
    m = _RD_DATE_RE.search(name or "")
    if m:
        d, mo, y = m.groups()
        return f"{y}-{mo}-{d}"
    m = _COMPACT_DATE_RE.search(name or "")
    if m:
        d, mo, y = m.groups()
        if 1 <= int(mo) <= 12 and 1 <= int(d) <= 31:
            return f"{y}-{mo}-{d}"
    # NCD shorthand: "26MY28" -> 2028-05-26
    m = _SHORT_DATE_RE.search((name or "").upper())
    if m:
        d, mon, yy = m.groups()
        mo = _MON3.get(mon)
        if mo and 1 <= int(d) <= 31:
            return f"20{yy}-{mo:02d}-{int(d):02d}"
    return None


def _guess_freq(name: str, parsed: str | None) -> str:
    if parsed:
        return parsed
    up = (name or "").upper()
    for pat, freq in _FREQ_HINTS:
        if re.search(pat, up):
            return freq
    return "annual"


def _guess_rating(name: str) -> str:
    m = _RATING_RE.search((name or "").upper())
    return m.group(1) if m else ""


def _issuer_from_name(name: str) -> str:
    """
    Trim CAS decoration down to something readable for the bonds page:
      "UGRO CAPITAL LTD#11.65% USEC NGRT SUB TR 2 ..." -> "UGRO CAPITAL LTD 11.65%"
    """
    s = re.sub(r"\s+", " ", (name or "").strip())
    s = s.split("#")[0].strip() if "#" in s else s
    # keep the leading company words plus a trailing coupon if present
    pct = _PCT_RE.search(name or "")
    head = " ".join(s.split()[:6]).strip(" -,")
    if pct and pct.group(0) not in head:
        head = f"{head} {pct.group(1)}%"
    return head or (name or "Unknown issuer")


def _bond_type(name: str, isin: str) -> str:
    up = (name or "").upper()
    if "GOI" in up or "G-SEC" in up or "GSEC" in up or (isin or "").startswith("IN00"):
        return "G-Sec"
    if "TAX FREE" in up or "TAXFREE" in up:
        return "Tax-free"
    if "NCD" in up or "DEBENTURE" in up:
        return "Corporate NCD"
    return "Corporate NCD"


# ---------------------------------------------------------------------------
# preview construction
# ---------------------------------------------------------------------------
def build_preview(
    parsed: dict[str, Any],
    owner: str | None = None,
    *,
    lookup_isins: bool = True,
) -> dict[str, Any]:
    """
    Map a parse_cas() result onto app-shaped draft records.

    Nothing is written. Every row carries `_source` so the UI can show where a
    field came from, and bond rows carry `_needs` listing fields the CAS could
    not supply (purchase price/date are never in a CAS) so the user can fill
    them before committing.
    """
    investor = parsed.get("investor") or {}
    person = owner or investor.get("name") or "Self"
    as_of = (investor.get("period_to") or date.today().isoformat())[:10]

    # ---- accounts -> stock_accounts drafts
    accounts: list[dict[str, Any]] = []
    for a in parsed.get("accounts") or []:
        accounts.append(
            {
                "id": _new_id("acct"),
                "person": person,
                "broker": (a.get("broker") or a.get("depository") or "demat").title(),
                "account_label": a.get("account_label")
                or f"{a.get('depository') or 'Demat'} {a.get('client_id') or ''}".strip(),
                "kind": "manual",
                "status": "connected",
                "_dp_id": a.get("dp_id"),
                "_client_id": a.get("client_id"),
                "_depository": a.get("depository"),
            }
        )

    def _acct_label_for(row: dict[str, Any]) -> tuple[str | None, str | None]:
        acct = row.get("account") or {}
        return acct.get("dp_id"), acct.get("client_id")

    # ---- equities + demat funds -> stock_holdings drafts
    holdings: list[dict[str, Any]] = []
    for row in (parsed.get("equities") or []) + (parsed.get("demat_funds") or []):
        dp, client = _acct_label_for(row)
        acct = next(
            (a for a in accounts if a["_dp_id"] == dp and a["_client_id"] == client),
            accounts[0] if accounts else None,
        )
        qty = row.get("quantity") or 0
        price = row.get("price")
        holdings.append(
            {
                "id": _new_id("hold"),
                "account_id": acct["id"] if acct else None,
                "person": person,
                "broker": (acct or {}).get("broker") or "",
                "account_label": (acct or {}).get("account_label") or "",
                "symbol": None,          # a CAS carries no trading symbol
                "name": row.get("name") or row.get("isin"),
                "exchange": None,
                "isin": row.get("isin"),
                "currency": "INR",
                "quantity": qty,
                # A CAS states market price, never your purchase price. Seed
                # avg_price with the market price so value is right immediately;
                # the user can correct cost basis later.
                "avg_price": price,
                "import_price": price,
                "_value": row.get("value"),
                "_kind": row.get("category"),
                "_source": "cas",
            }
        )

    # ---- bonds + gsecs -> bonds drafts
    bond_rows: list[dict[str, Any]] = []
    for row in (parsed.get("bonds") or []) + (parsed.get("gsecs") or []):
        name = row.get("name") or ""
        isin = row.get("isin") or ""
        qty = row.get("quantity") or 0
        face = row.get("face_value")
        price = row.get("price")
        value = row.get("value")

        # Derive per-unit face when the CAS omits it but value/qty are known.
        if not face and qty and value:
            face = round(value / qty, 2)

        coupon = _guess_coupon(name, row.get("coupon_rate"))
        maturity = _guess_maturity(name, row.get("maturity_date"))
        freq = _guess_freq(name, row.get("coupon_freq"))

        dp, client = _acct_label_for(row)
        acct = next(
            (a for a in accounts if a["_dp_id"] == dp and a["_client_id"] == client), None
        )

        bond_rows.append(
            {
                "id": _new_id("bond"),
                "owner": person,
                "broker": (acct or {}).get("broker") or "",
                "issuer": _issuer_from_name(name),
                "bond_type": _bond_type(name, isin),
                "isin": isin,
                "rating": _guess_rating(name),
                "tax_free": bool(re.search(r"TAX\s?FREE", name.upper())),
                "face_value": face or 1000.0,
                "quantity": qty,
                # Seed cost at market price; the CAS has no purchase price.
                "buy_price": price or face or 0.0,
                "coupon_rate": coupon or 0.0,
                "coupon_freq": freq,
                "repayment_type": "bullet",
                "purchase_date": as_of,
                "first_payment_date": None,
                "maturity_date": maturity,
                "note": f"Imported from CAS {investor.get('cas_id') or ''}".strip(),
                "_value": value,
                "_name_raw": name,
                # True when the statement itself stated a frequency, so an ISIN
                # lookup must not overwrite it.
                "_freq_from_cas": bool(row.get("coupon_freq")),
                "_source": "cas",
            }
        )

    # Resolve missing terms from the ISIN. A CAS rarely prints the redemption
    # date for an NCD, but the ISIN identifies a listed security whose terms are
    # public — so look them up rather than making the user hunt for 13 dates.
    lookup_report: dict[str, Any] = {"looked_up": 0, "resolved": 0, "filled": {}}
    if lookup_isins and bond_rows:
        try:
            from . import isin_lookup

            lookup_report = isin_lookup.enrich_bonds(bond_rows)
        except Exception as e:  # never let a network hiccup break the import
            lookup_report = {"looked_up": 0, "resolved": 0, "filled": {}, "error": str(e)}

    # `_needs` is computed AFTER enrichment so it lists only what is still
    # genuinely missing and must come from the user.
    for rec in bond_rows:
        needs: list[str] = []
        if not rec.get("maturity_date"):
            needs.append("maturity_date")
        if not rec.get("coupon_rate"):
            needs.append("coupon_rate")
        # Never in a CAS: a statement shows market value, not what you paid.
        needs.extend(["purchase_date", "buy_price"])
        rec["_needs"] = needs

    # ---- MF folios (informational; no MF store in this app yet)
    folios = []
    for f in parsed.get("mutual_funds") or []:
        folios.append(
            {
                "amc": f.get("amc"),
                "scheme": f.get("scheme"),
                "isin": f.get("isin"),
                "folio": f.get("folio"),
                "units": f.get("units"),
                "value": f.get("value"),
            }
        )

    totals = parsed.get("totals") or {}
    return {
        "investor": investor,
        "as_of": as_of,
        "owner": person,
        "accounts": accounts,
        "holdings": holdings,
        "bonds": bond_rows,
        "mf_folios": folios,
        "totals": totals,
        "warnings": parsed.get("warnings") or [],
        "isin_lookup": lookup_report,
        "counts": {
            "accounts": len(accounts),
            "holdings": len(holdings),
            "bonds": len(bond_rows),
            "mf_folios": len(folios),
        },
    }


def _strip_private(rec: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in rec.items() if not k.startswith("_")}


def commit(preview: dict[str, Any], what: set[str] | None = None) -> dict[str, Any]:
    """
    Persist a (possibly user-edited) preview.

    `what` selects which sections to write: {"holdings", "bonds"}. Returns a
    per-section report; a failure in one section never rolls back the other,
    so the report is the source of truth about what landed.
    """
    what = what or {"holdings", "bonds"}
    report: dict[str, Any] = {"holdings": 0, "bonds": 0, "errors": []}

    if "holdings" in what and preview.get("holdings"):
        try:
            res = _write_holdings(preview)
            report["holdings"] = res["holdings"]
            report["accounts"] = res["accounts"]
        except Exception as e:
            report["errors"].append(f"holdings: {e}")

    if "bonds" in what and preview.get("bonds"):
        try:
            from ..bonds import store as bond_store

            n = 0
            for rec in preview["bonds"]:
                payload = _strip_private(rec)
                if not payload.get("maturity_date"):
                    report["errors"].append(
                        f"{payload.get('issuer')}: skipped, no maturity date"
                    )
                    continue
                payload.pop("id", None)
                bond_store.add_bond(payload)
                n += 1
            report["bonds"] = n
        except Exception as e:
            report["errors"].append(f"bonds: {e}")

    return report


def _write_holdings(preview: dict[str, Any]) -> dict[str, int]:
    """
    Persist accounts + holdings through api.portfolio.store, which owns the
    stock_accounts / stock_holdings tables.

    One demat account in the CAS becomes one manual stock_account; its holdings
    are written with replace_holdings() so re-importing a later CAS refreshes
    that account instead of duplicating it. Accounts are matched on the
    DP/client id recorded in account_label.
    """
    from ..portfolio import store as pstore

    existing = pstore.list_accounts()
    n_acct = 0
    n_hold = 0

    for draft in preview.get("accounts") or []:
        client_id = draft.get("_client_id") or ""
        label = draft.get("account_label") or ""

        # reuse an account previously imported for the same demat id
        match = next(
            (
                a
                for a in existing
                if client_id
                and client_id in str(a.get("account_label") or "")
            ),
            None,
        )
        if match is None:
            account = pstore.add_account(
                {
                    "person": draft.get("person"),
                    "broker": draft.get("broker") or "demat",
                    "account_label": label,
                    "kind": "manual",
                    "status": "connected",
                }
            )
            n_acct += 1
        else:
            account = match

        rows = [
            _strip_private(h)
            for h in preview.get("holdings") or []
            if h.get("account_id") == draft["id"]
        ]
        if not rows:
            continue
        pstore.replace_holdings(account, rows)
        n_hold += len(rows)

    return {"accounts": n_acct, "holdings": n_hold}
