"""
Automatic corporate-action handling for tradebook-derived holdings.

Two layers (see also the per-user manual editor in routes/stocks.py):

  Layer 1 — Splits & bonuses (fully automatic).
    Yahoo's chart API exposes every split/bonus event (ratio + ex-date). We
    *back-adjust* the tradebook: any trade dated before a split gets its
    quantity x factor and price / factor, so FIFO then yields the correct
    present-day quantity and a consistent average cost. Bonuses show up as
    splits on Yahoo (e.g. a 1:1 bonus = a 2:1 split), so they're covered too.

  Layer 2 — Demergers (auto for known names).
    No free API maps "TATAMOTORS -> TMPV + TMCV". We keep a small built-in
    table of known Indian demergers, applied automatically; anything not here
    falls back to the user's manual mapping (which always wins).

Split lookups are cached in-process (6h) and fetched concurrently so a cold
load over a large portfolio stays responsive.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Optional

from .prices import _fetch_json, _yahoo_ticker

_SPLIT_TTL = 6 * 3600
_split_cache: dict[str, tuple[list[tuple[str, float]], float]] = {}   # symbol -> (events, ts)


# ── Layer 2: built-in demerger table ─────────────────────────────────────────
# old Zerodha symbol -> the live ticker(s) it became, with "shares received per
# 1 old share". Mirrors the manual-editor format so the route can merge them.
# NOTE: ratios should be verified against the scheme; the user's manual mapping
# overrides anything here.
BUILTIN_DEMERGERS: dict[str, list[dict]] = {
    # Tata Motors demerged into passenger (TMPV) + commercial (TMCV), 1:1.
    "TATAMOTORS": [{"symbol": "TMPV", "mult": 1.0}, {"symbol": "TMCV", "mult": 1.0}],
}


def merge_demergers(user_ca: Optional[dict]) -> dict:
    """Built-in table + the user's manual mappings (user wins per symbol)."""
    merged = {k: [dict(l) for l in v] for k, v in BUILTIN_DEMERGERS.items()}
    for sym, legs in (user_ca or {}).items():
        if legs:
            merged[sym] = legs
    return merged


# ── Layer 1: split / bonus back-adjustment ───────────────────────────────────
def fetch_splits(symbol: str, exchange: Optional[str]) -> list[tuple[str, float]]:
    """[(ex_date_iso, factor)] sorted by date; factor = new shares / old shares.
    Cached for 6h. Empty list when the stock never split (or lookup fails)."""
    now = time.time()
    c = _split_cache.get(symbol)
    if c and (now - c[1]) < _SPLIT_TTL:
        return c[0]

    events: list[tuple[str, float]] = []
    for tk in (_yahoo_ticker(symbol, exchange), symbol + ".NS", symbol + ".BO"):
        try:
            url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{tk}"
                   "?range=20y&interval=1d&events=split")
            d = _fetch_json(url)
            splits = (d["chart"]["result"][0].get("events", {}) or {}).get("splits", {}) or {}
            for s in splits.values():
                num, den, ts = s.get("numerator"), s.get("denominator"), s.get("date")
                if num and den and ts:
                    dt = datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
                    events.append((dt, float(num) / float(den)))
            if events:
                break
        except Exception:
            continue
    events.sort()
    _split_cache[symbol] = (events, now)
    return events


def adjust_trades_for_splits(trades: list[dict]) -> tuple[list[dict], dict[str, float]]:
    """Back-adjust trades for any split that occurred *after* the trade date.

    Returns (adjusted_trades, applied) where `applied` maps symbol ->
    cumulative factor for every symbol that was actually adjusted (for the UI).
    """
    syms: dict[str, Optional[str]] = {}
    for t in trades:
        syms.setdefault(t["symbol"], t.get("exchange"))

    # Fetch split histories concurrently (cache makes warm loads instant).
    split_map: dict[str, list[tuple[str, float]]] = {}
    if syms:
        with ThreadPoolExecutor(max_workers=min(12, len(syms))) as ex:
            futs = {ex.submit(fetch_splits, s, e): s for s, e in syms.items()}
            for f, s in futs.items():
                try:
                    split_map[s] = f.result()
                except Exception:
                    split_map[s] = []

    adjusted: list[dict] = []
    applied: dict[str, float] = {}
    for t in trades:
        evs = split_map.get(t["symbol"]) or []
        td = str(t.get("trade_date") or "")[:10]
        factor = 1.0
        for dt, f in evs:
            if td and dt and td < dt:        # split happened strictly after the trade
                factor *= f
        if factor != 1.0:
            t = dict(t)
            try:
                t["quantity"] = float(t["quantity"]) * factor
                t["price"] = float(t["price"]) / factor
                applied[t["symbol"]] = factor
            except (ValueError, TypeError):
                pass
        adjusted.append(t)
    return adjusted, applied
