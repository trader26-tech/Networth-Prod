"""
Per-stock Screener.in links. Clicking a holding's logo opens its Screener page.

For NSE-listed companies the URL is simply ``screener.in/company/{SYMBOL}/`` — so
we can DERIVE a correct link for essentially every equity holding. ETFs / funds
(NIFTYBEES, LIQUIDCASE, …) have no Screener page, so those are left blank for the
user to fill in (or clear) by hand.

Keyed by the **base symbol** (NSE series suffix stripped, uppercased), exactly
like manual_prices, so OCCLLTD-BE and OCCLLTD share one link. Supabase-backed
(table ``portfolio_screener_links``) with a JSON-file fallback, degrading
gracefully to "no links" if the table hasn't been created yet.
"""
from __future__ import annotations

import os
import re
from typing import Optional

from . import store

TABLE = "portfolio_screener_links"
_FILE = os.path.join(store._DATA_DIR, "screener_links.json")
_SERIES = re.compile(r"-[A-Z]{1,2}$")

_BASE = "https://www.screener.in/company"

# Symbols/names that have no Screener company page — ETFs, index funds, liquid
# funds, sovereign gold bonds. Conservative on purpose: we'd rather leave a link
# blank (user adds it) than write a wrong one for a real stock.
_FUND_SUFFIX = ("BEES",)
_FUND_EXACT = {
    "LIQUIDCASE", "LIQUIDADD", "LIQUIDBETF", "LIQUID", "MON100", "MOM100",
    "MOM50", "MAFANG", "HNGSNGBEES", "MASPTOP50",
}
_FUND_NAME = re.compile(r"\b(ETF|FUND|LIQUID|SGB|SOVEREIGN GOLD|INDEX)\b", re.I)


def base_symbol(sym: str) -> str:
    """OCCLLTD-BE → OCCLLTD (mirrors the price feed + frontend heuristic)."""
    return _SERIES.sub("", (sym or "").upper().strip())


def is_fund_like(symbol: str, name: str | None = None) -> bool:
    """True for ETFs / index-or-liquid funds / SGBs — no Screener page exists."""
    s = base_symbol(symbol)
    if s in _FUND_EXACT or s.endswith(_FUND_SUFFIX):
        return True
    return bool(_FUND_NAME.search(name or ""))


def derive_url(symbol: str) -> Optional[str]:
    """Standard Screener company URL for an NSE equity, or None for fund-likes."""
    s = base_symbol(symbol)
    if not s or is_fund_like(s):
        return None
    return f"{_BASE}/{s}/"


_table_ok: Optional[bool] = None


def _use_supabase() -> bool:
    global _table_ok
    client = store._get_client()
    if not client:
        return False
    if _table_ok:
        return True
    try:
        client.table(TABLE).select("symbol").limit(1).execute()
        _table_ok = True
        return True
    except Exception:
        return False


def list_links() -> dict[str, str]:
    """{BASE_SYMBOL: url} for every stored link. Merges the JSON fallback with
    Supabase (Supabase wins)."""
    out: dict[str, str] = {}
    rows = list(store._read(_FILE))
    if _use_supabase():
        try:
            rows += store._fetch_all(store._get_client(), TABLE)
        except Exception:
            pass
    for r in rows:
        s = base_symbol(r.get("symbol"))
        u = (r.get("url") or "").strip()
        if s and u:
            out[s] = u
    return out


def set_link(symbol: str, url: Optional[str]) -> dict:
    """Upsert one link; ``url`` blank/None clears it. Keyed by base symbol."""
    sym = base_symbol(symbol)
    if not sym:
        return {"symbol": sym, "url": None}
    clean = (url or "").strip()
    clearing = not clean
    row = {"symbol": sym, "url": clean, "updated_at": store._now()}
    if _use_supabase():
        client = store._get_client()
        try:
            client.table(TABLE).delete().eq("symbol", sym).execute()
            if not clearing:
                client.table(TABLE).insert(row).execute()
        except Exception:
            global _table_ok
            _table_ok = None
            _write_json(sym, None if clearing else row)
    else:
        _write_json(sym, None if clearing else row)
    return {"symbol": sym, "url": None if clearing else clean}


def seed_missing(holdings: list[dict]) -> int:
    """Populate a derived Screener link for every held equity that doesn't already
    have one (fund-likes are skipped). Returns how many links were written. Safe
    to re-run — it never overwrites a link you've set."""
    existing = list_links()
    written = 0
    seen: set[str] = set()
    for h in holdings:
        sym = base_symbol(h.get("symbol"))
        if not sym or sym in seen or sym in existing:
            continue
        seen.add(sym)
        url = derive_url(sym)
        if url:
            set_link(sym, url)
            written += 1
    return written


def _write_json(sym: str, row: Optional[dict]) -> None:
    data = [m for m in store._read(_FILE) if base_symbol(m.get("symbol")) != sym]
    if row:
        data.append(row)
    store._write(_FILE, data)


def table_ready() -> bool:
    client = store._get_client()
    if not client:
        return True
    try:
        client.table(TABLE).select("symbol").limit(1).execute()
        return True
    except Exception:
        return False
