"""
Manual LTP overrides for holdings the live feed can't price (delisted, illiquid,
or just missing from Yahoo/Kite). One row per symbol; the user types today's
price and it's used in valuation until a live price returns.

Keyed by the **base symbol** (NSE series suffix stripped, uppercased) so
OCCLLTD-BE and OCCLLTD share a single override. Supabase-backed (table
``portfolio_manual_prices``) with a JSON-file fallback, and degrades gracefully
to "no overrides" if the table hasn't been created yet — so nothing breaks
before the one-line migration is run.
"""
from __future__ import annotations

import os
import re
from typing import Optional

from . import store

TABLE = "portfolio_manual_prices"
_FILE = os.path.join(store._DATA_DIR, "manual_prices.json")
_SERIES = re.compile(r"-[A-Z]{1,2}$")


def base_symbol(sym: str) -> str:
    """OCCLLTD-BE → OCCLLTD (mirrors the price feed + frontend heuristic)."""
    return _SERIES.sub("", (sym or "").upper().strip())


_table_ok: Optional[bool] = None


def _use_supabase() -> bool:
    """Supabase only when it's configured AND the table exists; otherwise the
    JSON file. The positive result is cached; a negative is re-probed so adding
    the table later (via the migration) is picked up without a restart — and,
    crucially, overrides persist to JSON in the meantime instead of vanishing."""
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


def list_prices() -> dict[str, float]:
    """{BASE_SYMBOL: price} for every override. Merges the JSON fallback with
    Supabase (Supabase wins) so a value is found wherever the write landed."""
    out: dict[str, float] = {}
    rows = list(store._read(_FILE))                       # local fallback first
    if _use_supabase():
        try:
            rows += store._fetch_all(store._get_client(), TABLE)   # cloud overlays
        except Exception:
            pass
    for r in rows:
        s = base_symbol(r.get("symbol"))
        p = r.get("price")
        if s and p is not None:
            try:
                out[s] = float(p)
            except (TypeError, ValueError):
                pass
    return out


def set_price(symbol: str, price: Optional[float]) -> dict:
    """Upsert one override; ``price`` None/≤0 clears it. Keyed by base symbol."""
    sym = base_symbol(symbol)
    if not sym:
        return {"symbol": sym, "price": None}
    clearing = price is None or _f(price) <= 0
    row = {"symbol": sym, "price": _f(price), "updated_at": store._now()}
    if _use_supabase():
        client = store._get_client()
        try:
            # delete-then-insert (no on_conflict needed → works whatever the
            # table's key setup is); raises on a genuinely broken write.
            client.table(TABLE).delete().eq("symbol", sym).execute()
            if not clearing:
                client.table(TABLE).insert(row).execute()
        except Exception:
            # Supabase rejected the write — persist to JSON so the price never
            # silently vanishes, and stop trusting the table this process.
            global _table_ok
            _table_ok = None
            _write_json(sym, None if clearing else row)
    else:
        _write_json(sym, None if clearing else row)
    return {"symbol": sym, "price": None if clearing else _f(price)}


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


def _f(v, d: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return d
