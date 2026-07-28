"""
BSE corporate-actions reader — the BSE source of *declared* dividends, the
counterpart to nse.fetch_dividends.

BSE serves its full corporate-actions list (the same table on bseindia.com →
Corporates → Corporate Actions) as a date-ranged CSV via the site's own
"download" endpoint, `CorpactCSVDownload/w`. That returns every declared action
for the window — dividends, bonuses, splits, buybacks — in the exact CSV shape
`bse_csv.parse` already handles, so we just fetch it and reuse that parser.

Unlike NSE, BSE typically only publishes ~3 weeks of forthcoming ex-dates, so a
60-day request simply returns whatever is declared so far. Everything is
best-effort: any failure returns [] so callers degrade gracefully (BSE, like
NSE, can 403/redirect a datacenter IP — treat [] as "no data this run").

Output is the SAME shape as nse.fetch_dividends, so the ingester consumes both
unchanged: [{symbol, ex_date, per_share, subject, record_date, name, code}].
"""
from __future__ import annotations

from datetime import date, timedelta

from . import bse_csv

_API = "https://api.bseindia.com/BseIndiaAPI/api/CorpactCSVDownload/w"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",     # NOT br — no brotli decoder shipped
    "Origin": "https://www.bseindia.com",
    "Referer": "https://www.bseindia.com/corporates/corporates_act.html",
    "Connection": "keep-alive",
}


def fetch_dividends(days_ahead: int = 60) -> list[dict]:
    """Declared BSE dividends over roughly [today, today+days_ahead].

    → [{symbol, ex_date, per_share, subject, record_date, name, code}]
    Empty on any failure (blocked IP, timeout, shape change)."""
    try:
        import httpx
    except Exception:
        return []
    today = date.today()
    params = {
        "scripcode": "",
        "Fdate": today.strftime("%Y%m%d"),
        "TDate": (today + timedelta(days=days_ahead)).strftime("%Y%m%d"),
        "Purposecode": "", "strSearch": "P",
        "ddlindustrys": "", "ddlcategorys": "E", "segment": "0",
    }
    try:
        with httpx.Client(headers=_HEADERS, timeout=30, follow_redirects=True) as c:
            r = c.get(_API, params=params)
            if r.status_code != 200 or "text/csv" not in (r.headers.get("content-type") or ""):
                print(f"  ⓘ BSE corp-actions {r.status_code} (blocked?) — no data this run", flush=True)
                return []
            parsed = bse_csv.parse("bse-corpaction.csv", r.content)
    except Exception as e:
        print(f"  ⓘ BSE corp-actions fetch failed: {type(e).__name__}: {e}", flush=True)
        return []
    return parsed.get("dividends", [])
