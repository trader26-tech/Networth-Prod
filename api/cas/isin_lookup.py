"""
ISIN → bond terms lookup.
=========================

A CAS states what you *hold* but often not the instrument's terms: for most
NCDs the redemption date is simply not printed. The ISIN is the canonical
identifier for a listed security though, so the terms are public — this module
resolves them and caches the result.

Resolved per ISIN: issuer, coupon %, maturity date, face value, and interest
payment frequency. Frequency matters as much as maturity: these NCDs mostly pay
**monthly**, and assuming annual would produce a wrong payout calendar.

Design:
  * Results are cached in the durable KV store (`app_cache`) so an ISIN is
    fetched once, not on every import. Bond terms are immutable, so the cache
    never needs invalidating.
  * Network failure is never fatal — `enrich_bonds()` fills in what it can and
    leaves the rest for the user, exactly as before.
  * Lookups run concurrently with a bounded pool; the upstream is a small free
    host, so concurrency is deliberately modest.
"""

from __future__ import annotations

import concurrent.futures as futures
import re
from typing import Any, Iterable

_SOURCE_URL = "https://bond-detail.onrender.com/bonds/{isin}"
_CACHE_PREFIX = "isin_terms:"
_TIMEOUT = 45.0
_WORKERS = 6

# The source publishes a machine-readable summary in the page's meta
# description, e.g.
#   "MUTHOOT MCRED LIMITED 9.30 NCD 20NV28 FVRS10000 (ISIN INE101Q07CA7) bond
#    is issued by MUTHOOT MCRED LIMITED , matures on 20 Nov 2028, has a face
#    value of ₹10000, pays monthly around ₹77.50 per scrip."
_DESC_RE = re.compile(r'name="description"\s+content="(.*?)"', re.S)
_ISSUER_RE = re.compile(r"is issued by\s+(.*?)\s*,\s*matures on", re.I | re.S)
_MATURES_RE = re.compile(r"matures on\s+(\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4})", re.I)
_FACE_RE = re.compile(r"face value of\s*₹?\s*([\d,]+)", re.I)
_PAYS_RE = re.compile(
    r"pays\s+(monthly|quarterly|half[-\s]?yearly|semi[-\s]?annually|annually|yearly|"
    r"on maturity|cumulative)",
    re.I,
)
# Leading "<ISSUER> 9.30 NCD ..." — the number before "NCD" is the coupon.
_COUPON_RE = re.compile(r"(\d{1,2}(?:\.\d{1,4})?)\s*%?\s*(?:NCD|BOND|DEB)", re.I)

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}

_FREQ_MAP = {
    "monthly": "monthly",
    "quarterly": "quarterly",
    "half-yearly": "half_yearly",
    "half yearly": "half_yearly",
    "halfyearly": "half_yearly",
    "semi-annually": "half_yearly",
    "semi annually": "half_yearly",
    "annually": "annual",
    "yearly": "annual",
    "on maturity": "cumulative",
    "cumulative": "cumulative",
}


def _iso(date_text: str) -> str | None:
    """'20 Nov 2028' -> '2028-11-20'."""
    m = re.match(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", (date_text or "").strip())
    if not m:
        return None
    d, mon, y = m.groups()
    mo = _MONTHS.get(mon[:4].lower()) or _MONTHS.get(mon[:3].lower())
    if not mo:
        return None
    return f"{y}-{mo:02d}-{int(d):02d}"


def _parse_description(desc: str, isin: str) -> dict[str, Any]:
    """Pull structured bond terms out of the summary sentence."""
    out: dict[str, Any] = {"isin": isin, "source": "bond-detail"}

    head = desc.split("(ISIN")[0]
    cm = _COUPON_RE.search(head)
    if cm:
        try:
            v = float(cm.group(1))
            if 0 < v <= 30:
                out["coupon_rate"] = v
        except ValueError:
            pass

    im = _ISSUER_RE.search(desc)
    if im:
        out["issuer"] = re.sub(r"\s+", " ", im.group(1)).strip(" ,")

    mm = _MATURES_RE.search(desc)
    if mm:
        iso = _iso(mm.group(1))
        if iso:
            out["maturity_date"] = iso

    fm = _FACE_RE.search(desc)
    if fm:
        try:
            out["face_value"] = float(fm.group(1).replace(",", ""))
        except ValueError:
            pass

    pm = _PAYS_RE.search(desc)
    if pm:
        key = pm.group(1).lower().replace("_", "-")
        out["coupon_freq"] = _FREQ_MAP.get(key) or _FREQ_MAP.get(
            key.replace("-", " ")
        )

    # A full-title fallback for the maturity: "... NCD 20NV28 FVRS10000"
    if "maturity_date" not in out:
        out.update(_from_short_code(head))

    out["title"] = re.sub(r"\s+", " ", head).strip()
    return out


_SHORT_MONTHS = {
    "JA": 1, "JN": 6, "JL": 7, "FB": 2, "MR": 3, "AP": 4, "MY": 5,
    "AG": 8, "SP": 9, "OC": 10, "NV": 11, "DC": 12,
}
_SHORT_RE = re.compile(r"\b(\d{1,2})([A-Z]{2})(\d{2})\b")


def _from_short_code(text: str) -> dict[str, Any]:
    """Decode the NCD shorthand maturity: '20NV28' -> 2028-11-20."""
    m = _SHORT_RE.search((text or "").upper())
    if not m:
        return {}
    d, mon, yy = m.groups()
    mo = _SHORT_MONTHS.get(mon)
    if not mo or not (1 <= int(d) <= 31):
        return {}
    return {"maturity_date": f"20{yy}-{mo:02d}-{int(d):02d}"}


def _cache_get(isin: str) -> dict[str, Any] | None:
    try:
        from ..portfolio import store as pstore

        hit = pstore.cache_get(f"{_CACHE_PREFIX}{isin}")
        if isinstance(hit, dict) and hit.get("isin"):
            return hit
    except Exception:
        pass
    return None


def _cache_put(isin: str, data: dict[str, Any]) -> None:
    try:
        from ..portfolio import store as pstore

        pstore.cache_set(f"{_CACHE_PREFIX}{isin}", data)
    except Exception:
        pass


def lookup_one(isin: str, *, use_cache: bool = True) -> dict[str, Any] | None:
    """Resolve one ISIN's bond terms. Returns None if it cannot be resolved."""
    isin = (isin or "").strip().upper()
    if not isin:
        return None

    if use_cache:
        hit = _cache_get(isin)
        if hit is not None:
            return hit

    try:
        import httpx

        resp = httpx.get(
            _SOURCE_URL.format(isin=isin),
            timeout=_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": "networth.io/1.0 (bond terms lookup)"},
        )
        if resp.status_code != 200:
            return None
        m = _DESC_RE.search(resp.text)
        if not m:
            return None
        desc = re.sub(r"\s+", " ", m.group(1)).strip()
        if isin not in desc.upper():
            return None  # wrong/placeholder page
        data = _parse_description(desc, isin)
    except Exception:
        return None

    if data.get("maturity_date") or data.get("coupon_rate"):
        _cache_put(isin, data)
        return data
    return None


def lookup_many(isins: Iterable[str], *, use_cache: bool = True) -> dict[str, dict]:
    """Resolve several ISINs concurrently. Failures are simply absent."""
    want = [i.strip().upper() for i in isins if (i or "").strip()]
    out: dict[str, dict] = {}
    todo: list[str] = []

    for isin in dict.fromkeys(want):  # de-dupe, keep order
        if use_cache:
            hit = _cache_get(isin)
            if hit is not None:
                out[isin] = hit
                continue
        todo.append(isin)

    if not todo:
        return out

    with futures.ThreadPoolExecutor(max_workers=_WORKERS) as pool:
        for isin, res in zip(
            todo, pool.map(lambda i: lookup_one(i, use_cache=False), todo)
        ):
            if res:
                out[isin] = res
    return out


def enrich_bonds(bonds: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Fill missing terms on parsed/draft bond rows, in place.

    Only ever *adds* what is missing — a value the CAS itself stated always
    wins, since the statement is authoritative for your holding. Returns a
    report of what was filled so the UI can show provenance.
    """
    # Look up any bond whose terms are not fully known from the statement.
    # Frequency counts as unknown unless the CAS actually stated it: our default
    # is "annual", and silently keeping that for a monthly payer would generate
    # a wrong payout calendar. So a bond with coupon+maturity but no
    # CAS-confirmed frequency still needs resolving.
    need = [
        b.get("isin")
        for b in bonds
        if b.get("isin")
        and (
            not b.get("maturity_date")
            or not b.get("coupon_rate")
            or not b.get("_freq_from_cas")
        )
    ]
    if not need:
        return {"looked_up": 0, "resolved": 0, "filled": {}}

    found = lookup_many(need)
    filled: dict[str, list[str]] = {}

    for b in bonds:
        info = found.get((b.get("isin") or "").upper())
        if not info:
            continue
        got: list[str] = []

        if not b.get("maturity_date") and info.get("maturity_date"):
            b["maturity_date"] = info["maturity_date"]
            got.append("maturity_date")
        if not b.get("coupon_rate") and info.get("coupon_rate"):
            b["coupon_rate"] = info["coupon_rate"]
            got.append("coupon_rate")
        # Frequency drives the payout calendar; a looked-up value beats our
        # "annual" default, but never overrides one read from the statement.
        if info.get("coupon_freq") and not b.get("_freq_from_cas"):
            if b.get("coupon_freq") != info["coupon_freq"]:
                b["coupon_freq"] = info["coupon_freq"]
                got.append("coupon_freq")
        if not b.get("face_value") and info.get("face_value"):
            b["face_value"] = info["face_value"]
            got.append("face_value")
        # The looked-up issuer name is far cleaner than the CAS abbreviation.
        if info.get("issuer"):
            b["issuer_resolved"] = info["issuer"]
            if info.get("coupon_rate") and info.get("maturity_date"):
                b["issuer"] = (
                    f"{info['issuer']} {info['coupon_rate']}% "
                    f"{info['maturity_date'][:4]}"
                )
                got.append("issuer")

        if got:
            b["_enriched"] = got
            filled[b["isin"]] = got

    return {"looked_up": len(need), "resolved": len(found), "filled": filled}
