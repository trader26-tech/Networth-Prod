"""
ISIN → (symbol, name, exchange) directory for equities and ETFs.
================================================================

A CAS identifies every holding by ISIN but never by trading symbol, and the
whole stocks app (live prices, dividends, screener links, performance, the
dashboard roll-up) keys on the SYMBOL. So a CAS-imported holding with no symbol
is invisible to all of that. This module resolves the symbol + a clean company
name from the ISIN, using the exchanges' own public security masters:

  * NSE  EQUITY_L.csv        — ISIN → SYMBOL, NAME OF COMPANY   (~2,400 stocks)
  * BSE  ListofScripData     — ISIN → scrip_id, Scrip_Name      (BSE-only names)

Both are free and unauthenticated. The merged map is cached in the durable KV
(`app_cache`) so it is fetched at most once a day, and the lookup itself is a
dict hit. Network failure is never fatal: unresolved holdings keep their raw
CAS name and simply stay symbol-less, exactly as before.
"""

from __future__ import annotations

import csv
import io
from typing import Any

# NSE publishes one CSV per instrument family; together they cover every
# listed equity, ETF, REIT and InvIT. All share the columns SYMBOL / NAME OF
# COMPANY (or SecurityName) / ISIN NUMBER, so one parser handles them all.
_NSE_CSVS = [
    "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv",
    "https://nsearchives.nseindia.com/content/equities/eq_etfseclist.csv",
    "https://nsearchives.nseindia.com/content/equities/REITS_L.csv",
    "https://nsearchives.nseindia.com/content/equities/INVITS_L.csv",
]
# BSE fills in scrips that are BSE-only (not listed on NSE).
_BSE_URL = (
    "https://api.bseindia.com/BseIndiaAPI/api/ListofScripData/w"
    "?Group=&Scripcode=&industry=&segment=Equity&status=Active"
)
_CACHE_KEY = "isin_directory_v1"
_HEADERS = {"User-Agent": "networth.io/1.0", "Referer": "https://www.bseindia.com/"}
_TIMEOUT = 40.0

# in-process memo so repeated imports in one run don't re-hit the KV/network
_MEMO: dict[str, dict[str, Any]] | None = None


def _hdr_index(hdr: list[str], *names: str) -> int | None:
    """First matching column index (headers vary: 'NAME OF COMPANY' vs
    'SECURITYNAME', 'ISIN NUMBER' vs 'ISINNUMBER')."""
    norm = [h.strip().upper().replace(" ", "") for h in hdr]
    for n in names:
        key = n.upper().replace(" ", "")
        if key in norm:
            return norm.index(key)
    return None


def _fetch_nse() -> dict[str, dict[str, Any]]:
    import httpx

    out: dict[str, dict[str, Any]] = {}
    errors = 0
    for url in _NSE_CSVS:
        try:
            r = httpx.get(url, timeout=_TIMEOUT, headers=_HEADERS, follow_redirects=True)
            r.raise_for_status()
            rows = list(csv.reader(io.StringIO(r.text)))
            if not rows:
                continue
            hdr = rows[0]
            si = _hdr_index(hdr, "SYMBOL")
            ni = _hdr_index(hdr, "NAME OF COMPANY", "SECURITYNAME")
            ii = _hdr_index(hdr, "ISIN NUMBER", "ISINNUMBER")
            if si is None or ii is None:
                continue
            for row in rows[1:]:
                if len(row) <= max(si, ii):
                    continue
                isin = row[ii].strip().upper()
                sym = row[si].strip()
                if not isin or not sym:
                    continue
                out.setdefault(isin, {
                    "symbol": sym,
                    "name": row[ni].strip() if ni is not None and len(row) > ni else "",
                    "exchange": "NSE",
                })
        except Exception:
            errors += 1
    if errors == len(_NSE_CSVS):
        raise RuntimeError("all NSE masters unreachable")
    return out


def _fetch_bse() -> dict[str, dict[str, Any]]:
    import httpx

    out: dict[str, dict[str, Any]] = {}
    r = httpx.get(_BSE_URL, timeout=_TIMEOUT, headers=_HEADERS, follow_redirects=True)
    r.raise_for_status()
    for row in r.json():
        isin = (row.get("ISIN_NUMBER") or "").strip().upper()
        if not isin:
            continue
        out[isin] = {
            "symbol": (row.get("scrip_id") or "").strip() or (row.get("SCRIP_CD") or ""),
            "name": (row.get("Scrip_Name") or "").strip(),
            "exchange": "BSE",
            "bse_code": (row.get("SCRIP_CD") or "").strip(),
        }
    return out


def _build() -> dict[str, dict[str, Any]]:
    """Merge NSE + BSE. NSE wins on overlap (its symbols are what the app uses)."""
    merged: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for fetch in (_fetch_bse, _fetch_nse):  # NSE last so it overrides
        try:
            merged.update(fetch())
        except Exception as e:  # noqa: BLE001 — degrade, never fail the import
            errors.append(f"{fetch.__name__}: {e}")
    if errors and not merged:
        raise RuntimeError("; ".join(errors))
    return merged


def _load() -> dict[str, dict[str, Any]]:
    global _MEMO
    if _MEMO is not None:
        return _MEMO

    # durable cache first
    try:
        from ..portfolio import store as pstore

        hit = pstore.cache_get(_CACHE_KEY)
        if isinstance(hit, dict) and hit.get("map"):
            _MEMO = hit["map"]
            return _MEMO
    except Exception:
        pass

    try:
        m = _build()
    except Exception:
        _MEMO = {}
        return _MEMO

    _MEMO = m
    try:
        from ..portfolio import store as pstore

        pstore.cache_set(_CACHE_KEY, {"map": m})
    except Exception:
        pass
    return _MEMO


def resolve(isin: str) -> dict[str, Any] | None:
    """Return {symbol, name, exchange[, bse_code]} for an ISIN, or None."""
    if not isin:
        return None
    return _load().get(isin.strip().upper())


def enrich_holdings(holdings: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Fill symbol / exchange / clean name on CAS holdings, in place.

    The trading symbol is what unlocks live prices, dividends, the screener and
    the dashboard roll-up, so this is what makes an imported holding a
    first-class stock rather than an inert row. The CAS's raw name is kept as a
    fallback and in `name_raw` for reference.
    """
    directory = _load()
    if not directory:
        return {"resolved": 0, "total": len(holdings), "source": "unavailable"}

    resolved = 0
    for h in holdings:
        info = directory.get((h.get("isin") or "").strip().upper())
        if not info:
            continue
        if not h.get("symbol"):
            h["symbol"] = info["symbol"]
        if not h.get("exchange"):
            h["exchange"] = info["exchange"]
        if info.get("name"):
            h["name_raw"] = h.get("name")
            h["name"] = info["name"]
        h["_isin_resolved"] = True
        resolved += 1

    return {"resolved": resolved, "total": len(holdings), "source": "nse+bse"}
