"""
NSE corporate-actions reader — the source of *declared* dividends.

Reads NSE's official corporate-filings/actions feed (the same list published at
nseindia.com → Corporate Filings → Corporate Actions) and returns the declared
dividends: which symbol, the ex-date, and ₹/share (parsed from the filing's
subject line, e.g. "Dividend - Rs 24 Per Share").

NSE's JSON API rejects plain/HTTP-1 clients (403). We use an HTTP/2 client with
a browser-like header set and a cookie warm-up hit first, which the site allows.
Everything is best-effort: any failure returns [] so callers degrade gracefully.

Note: NSE blocks many datacenter IPs outright, so this can succeed from a normal
host yet 403 from some cloud environments — callers should treat [] as "no data
this run", never as an error.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Optional

_WARMUP = "https://www.nseindia.com/companies-listing/corporate-filings-actions"
_API = "https://www.nseindia.com/api/corporates-corporateActions"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",     # NOT br — we don't ship a brotli decoder
    "Connection": "keep-alive",
}

_NUM = r"(\d+(?:\.\d+)?)"


def _parse_amount(subject: str, face_val=None) -> Optional[float]:
    """₹/share from a filing subject. Sums multiple components in one filing
    (e.g. "Interim Dividend - Rs 5 & Special Dividend - Rs 2" → 7). Falls back to
    a percentage-of-face-value figure ("Dividend - 150%") when no ₹ is given."""
    s = subject or ""
    if "DIVIDEND" not in s.upper():
        return None
    total = 0.0
    found = False
    for m in re.finditer(r"(?:Rs\.?|Re\.?|₹|INR)\s*" + _NUM, s, re.I):
        total += float(m.group(1)); found = True
    if not found and face_val:
        try:
            fv = float(face_val)
            for m in re.finditer(_NUM + r"\s*%", s):
                total += float(m.group(1)) / 100.0 * fv; found = True
        except (TypeError, ValueError):
            pass
    return round(total, 4) if (found and total > 0) else None


def _parse_date(s: str) -> Optional[str]:
    s = (s or "").strip()
    for fmt in ("%d-%b-%Y", "%d-%b-%y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def fetch_dividends(days_ahead: int = 60) -> list[dict]:
    """Declared dividends over roughly [today-2d, today+days_ahead].

    → [{symbol, ex_date, per_share, subject, isin, record_date, series, name}]
    Empty on any failure (blocked IP, timeout, shape change)."""
    try:
        import httpx
    except Exception:
        return []
    today = date.today()
    params = {
        "index": "equities",
        "from_date": (today - timedelta(days=2)).strftime("%d-%m-%Y"),
        "to_date": (today + timedelta(days=days_ahead)).strftime("%d-%m-%Y"),
    }
    try:
        # HTTP/1.1 is enough (NSE allows it with the warm-up + headers) and avoids
        # depending on the optional h2 package being present in production.
        with httpx.Client(headers=_HEADERS, timeout=20, follow_redirects=True) as c:
            c.get(_WARMUP)                                  # prime cookies
            r = c.get(_API, params=params,
                      headers={"Accept": "application/json", "Referer": _WARMUP})
            if r.status_code != 200:
                print(f"  ⓘ NSE corp-actions {r.status_code} (blocked?) — no data this run", flush=True)
                return []
            data = r.json()
        rows = data if isinstance(data, list) else (data.get("data") or [])
    except Exception as e:
        print(f"  ⓘ NSE corp-actions fetch failed: {type(e).__name__}: {e}", flush=True)
        return []

    out: list[dict] = []
    for x in rows:
        subj = x.get("subject") or ""
        if "DIVIDEND" not in subj.upper():
            continue
        ps = _parse_amount(subj, x.get("faceVal"))
        ex = _parse_date(x.get("exDate") or "")
        if not ps or not ex:
            continue
        out.append({
            "symbol": (x.get("symbol") or "").strip().upper(),
            "ex_date": ex, "per_share": ps, "subject": subj.strip(),
            "isin": x.get("isin"), "record_date": _parse_date(x.get("recDate") or ""),
            "series": (x.get("series") or "").strip(), "name": x.get("comp"),
        })
    return out
