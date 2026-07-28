"""
Portfolio analytics — value every account's holdings live (free Yahoo feed),
then roll up totals, per-account, per-person and per-stock views plus glance
metrics (today's P&L, best/worst performer today).
"""
from __future__ import annotations

import time
import threading
from typing import Optional

from . import store
from ..stocks import prices as price_feed
from ..salary import fx as fx_mod

# ── background refresher + warm cache ───────────────────────────────────────────
# Valuing ~1000 holdings hits the price feed, so we NEVER do that on the request
# path. A single daemon thread recomputes the whole summary on a timer (fast
# while the market is open, slow when closed) and stores it; every request just
# reads the warm cache in O(1). Prewarmed on startup → no cold-start stall.
#
# Upgradable by design: the cache is read/written only through `_cache_get` /
# `_cache_set`, so swapping the in-process dict for Redis (multi-replica) is a
# 2-function change, and an SSE/WebSocket push can hook `_publish()` after each
# refresh — none of which touches `build_summary()` or `_compute()`.
_cache: dict = {"data": None, "ts": 0.0}
# Live quotes are the ONE expensive input (a Yahoo round-trip for every symbol).
# We fetch them only on the refresher's cadence and cache them here; on-demand
# recomputes (e.g. after setting a manual price) REUSE this snapshot instead of
# re-hitting Yahoo — prices don't change between refreshes, so a manual-price
# edit becomes a fast in-memory re-aggregation rather than a multi-second fetch.
_quotes_cache: dict = {"data": {}, "src": "none", "ts": 0.0}
# The other inputs (accounts, holdings, NSE name map) are read from Supabase and
# change only on import/edit — so they're snapshotted here too. On-demand
# recomputes (e.g. a manual-price edit, which changes NONE of these) reuse the
# snapshot; the refresher and any holdings-changing mutation reload it. Result:
# a price edit is pure in-memory re-aggregation, no DB round-trips.
_inputs: dict = {"accounts": None, "holdings": None, "symname": None}
_lock = threading.Lock()
_inputs_lock = threading.Lock()   # serializes Supabase reads (client isn't thread-safe)
_prewarm_lock = threading.Lock()  # collapses concurrent cold requests into one fetch
_wake = threading.Event()        # set by invalidate() to force an immediate refresh
_stop = threading.Event()
_started = False
_seeded = False                  # have we hydrated _quotes_cache from the durable snapshot?
_QUOTES_KEY = "stock_quotes"     # app_cache key for the persisted live-quotes snapshot

_OPEN_INTERVAL = 20.0            # refresh cadence while the market is open
_CLOSED_INTERVAL = 300.0        # ... and when it's closed (prices don't move)


def _cache_get() -> Optional[dict]:
    with _lock:
        return _cache["data"]


def _cache_set(data: dict) -> None:
    with _lock:
        _cache["data"] = data
        _cache["ts"] = time.time()
    _publish(data)


def _publish(data: dict) -> None:
    """Hook for a future SSE/WebSocket push to live clients. No-op for now."""
    pass


def _market_open() -> bool:
    """Refresh fast whenever ANY held market (India or US) is open, so US
    holdings keep updating during US hours even when the NSE is closed."""
    from . import markets
    return markets.any_open()


def _load_inputs(fetch: bool):
    """Return (accounts, holdings, symname), reading them from Supabase only when
    `fetch` (the refresher) or the snapshot is empty; otherwise reuse the cached
    snapshot so on-demand recomputes do zero DB round-trips."""
    # Serialize the Supabase reads: the refresher thread and a request-path
    # (cold-start) recompute can hit this at the same time, and the shared client
    # isn't thread-safe — concurrent reads returned an EMPTY holdings list, which
    # left the whole summary stuck "warming" (every LTP blank). The lock also
    # guards the shared _inputs snapshot from a torn read.
    with _inputs_lock:
        if fetch or _inputs["holdings"] is None:
            _inputs["accounts"] = store.list_accounts()
            _inputs["holdings"] = store.list_holdings()
            try:
                from .motilal_client import _nse_symbol_name_map
                _inputs["symname"] = _nse_symbol_name_map()
            except Exception:
                _inputs["symname"] = {}
        return _inputs["accounts"], _inputs["holdings"], _inputs["symname"] or {}


def _persist_snapshot() -> None:
    """Durably save the freshly-fetched live quotes so the NEXT boot/replica serves
    real prices instantly (stale-while-revalidate) instead of a blank table."""
    q = _quotes_cache
    if q.get("data"):
        store.cache_set(_QUOTES_KEY, {"data": q["data"], "src": q.get("src"), "ts": q.get("ts")})


def _seed_from_snapshot() -> None:
    """Hydrate the in-memory quotes from the last durable snapshot — once, and only
    while still cold. Lets the first request serve real (if slightly stale) prices
    immediately; the refresher upgrades them to live within one cycle."""
    global _seeded
    if _seeded or _quotes_cache.get("data"):
        return
    _seeded = True
    val = (store.cache_get(_QUOTES_KEY) or {}).get("value") or {}
    if val.get("data"):
        _quotes_cache["data"] = val["data"]
        _quotes_cache["src"] = val.get("src") or "cache"
        _quotes_cache["ts"] = val.get("ts") or 0.0


def _refresh_once() -> None:
    try:
        _cache_set(_compute(fetch=True))    # the refresher is the only fetcher
        _persist_snapshot()                 # durably save the live quotes (SWR)
    except Exception:
        pass                            # keep last good cache on transient failure


def _loop() -> None:
    while not _stop.is_set():
        _refresh_once()
        _wake.wait(timeout=(_OPEN_INTERVAL if _market_open() else _CLOSED_INTERVAL))
        _wake.clear()


def start_refresher() -> None:
    """Idempotent — launches the daemon refresher (and prewarms the cache)."""
    global _started
    if _started:
        return
    _started = True
    _stop.clear()
    _seed_from_snapshot()               # instant warm start from the durable snapshot
    threading.Thread(target=_loop, daemon=True, name="portfolio-refresher").start()


def invalidate(reload_inputs: bool = True) -> None:
    """Drop the summary cache and wake the refresher so the next read is fresh.

    `reload_inputs=True` (default, for import/edit/delete/connect) also drops the
    accounts+holdings snapshot so it's re-read. A price-only change (manual LTP)
    passes `reload_inputs=False`: nothing about the holdings changed, so the next
    recompute reuses the snapshot and stays a fast in-memory re-aggregation."""
    with _lock:
        _cache["data"] = None
        if reload_inputs:
            _inputs["holdings"] = None
    _wake.set()


def build_summary() -> dict:
    """O(1) read of the warm cache — the request path NEVER hits the price feed.

    Stale-while-revalidate: on a cold start the in-memory quotes are hydrated from
    the last durable snapshot (Supabase/file), so the first request returns real —
    if slightly stale — prices instantly instead of a blank "warming" table. The
    background refresher upgrades them to live within one cycle. Only the very
    first run ever (no snapshot anywhere) falls back to a synchronous warm."""
    start_refresher()                   # seeds _quotes_cache from the snapshot
    data = _cache_get()
    if data is not None:
        return data
    # Cold: serve from the seeded snapshot immediately (no feed call, no blocking).
    _seed_from_snapshot()
    if _quotes_cache.get("data"):
        d = _compute()                  # fast in-memory re-aggregation on seeded quotes
        if _cache_get() is None:
            _cache_set(d)
        return _cache_get() or d
    # First-ever run, nothing persisted yet: warm synchronously this once. The
    # lock collapses concurrent cold requests into a single fetch.
    with _prewarm_lock:
        if _cache_get() is None:
            _refresh_once()
    return _cache_get() or _compute()


def _f(v, d: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def _compute(fetch: bool = False) -> dict:
    import re as _re
    accounts, holdings, symname = _load_inputs(fetch)
    # person / label live on the account — holdings rows cache them from import time,
    # so always resolve through the current account (renames take effect immediately).
    acc_by_id = {a.get("id"): a for a in accounts}
    def _co_name(sym, fallback):
        if fallback:
            return fallback
        base = _re.sub(r"-[A-Z]{1,2}$", "", (sym or "").upper())
        return symname.get(sym) or symname.get(base)

    # live quotes (last + today's change). Fetched ONLY on the refresher's
    # cadence (`fetch=True`) — never on the request path. This matters in prod:
    # Yahoo is slow/throttled from a datacenter IP, so a per-request fetch over
    # ~1000s of symbols would block the page for a long time. A request instead
    # serves whatever quotes are already cached (holdings fall back to cost until
    # the first background fetch lands, which the frontend picks up by polling).
    pairs = list({(h.get("symbol"), h.get("exchange")) for h in holdings if h.get("symbol")})
    if fetch:
        quotes, source = price_feed.get_quotes([(s, e) for s, e in pairs]) if pairs else ({}, "none")
        _quotes_cache["data"] = quotes
        _quotes_cache["src"] = source
        _quotes_cache["ts"] = time.time()
    else:
        quotes = _quotes_cache["data"]
        source = _quotes_cache["src"] if quotes else "warming"   # cold: prices land on the next refresh
    rates = fx_mod.get_rates()               # live FX → INR (for USD/IBKR etc.)
    from . import manual_prices as mp_store   # manual LTP overrides — always read fresh
    manual_px = mp_store.list_prices()
    from . import screener_links as sl_store   # per-stock Screener.in links
    scr_links = sl_store.list_links()

    # ── enrich each holding (values normalized to INR) ──────────────────────────
    enriched = []
    for h in holdings:
        sym = h.get("symbol")
        acc = acc_by_id.get(h.get("account_id")) or {}
        q = quotes.get(sym) or {}
        cur = (h.get("currency") or "INR").upper()
        fxr = fx_mod.inr_per_unit(cur, rates)          # 1 unit of `cur` in ₹
        qty = _f(h.get("quantity"))
        avg = _f(h.get("avg_price"))                   # native currency
        live = _f(q.get("last"))
        nm = _co_name(sym, h.get("name"))
        # Overrides are keyed by the row's DISPLAY symbol — the symbol, or the
        # name for name-only holdings (ETFs/SGBs/bonds). Match that exactly, or
        # the override wouldn't apply and value/P&L wouldn't move.
        manual = _f(manual_px.get(mp_store.base_symbol(sym or nm or "")))
        imp = _f(h.get("import_price"))                # price from the imported file
        last = live or manual or imp or avg            # prefer live → manual → file → cost
        prev = _f(q.get("prev")) or last
        value = qty * last * fxr
        invested = qty * avg * fxr
        enriched.append({
            "account_id": h.get("account_id"), "person": acc.get("person") or h.get("person"),
            "broker": acc.get("broker") or h.get("broker"),
            "account_label": acc.get("account_label") or h.get("account_label"),
            "symbol": sym, "name": nm, "exchange": h.get("exchange"),
            "isin": h.get("isin"), "currency": cur,
            "quantity": qty, "avg_price": round(avg, 2), "last_price": round(last, 2),
            "value": round(value, 2), "invested": round(invested, 2),
            "pnl": round(value - invested, 2),
            "pnl_pct": round((value / invested - 1.0), 4) if invested else None,
            "day_change": round(qty * (last - prev) * fxr, 2),
            "day_change_pct": round((last / prev - 1.0), 4) if prev else 0.0,
            "priced": bool(live or manual),
            "price_manual": bool(not live and manual),   # valued via a manual override
            "screener_url": scr_links.get(sl_store.base_symbol(sym or nm or "")),
        })

    def agg(rows: list[dict]) -> dict:
        value = sum(r["value"] for r in rows)
        invested = sum(r["invested"] for r in rows)
        day = sum(r["day_change"] for r in rows)
        prev_value = value - day
        return {
            "value": round(value, 2), "invested": round(invested, 2),
            "pnl": round(value - invested, 2),
            "pnl_pct": round((value / invested - 1.0), 4) if invested else None,
            "day_change": round(day, 2),
            "day_change_pct": round((value / prev_value - 1.0), 4) if prev_value else 0.0,
            "holdings": len(rows),
        }

    # ── per account (include accounts with no holdings yet) ─────────────────────
    by_acc = []
    for a in accounts:
        rows = [r for r in enriched if r["account_id"] == a["id"]]
        by_acc.append({**store.public_account(a), **agg(rows)})
    by_acc.sort(key=lambda x: x["value"], reverse=True)

    # ── per person ──────────────────────────────────────────────────────────────
    persons: dict[str, list] = {}
    for r in enriched:
        persons.setdefault(r["person"] or "—", []).append(r)
    by_person = [{"person": p, **agg(rows)} for p, rows in persons.items()]
    by_person.sort(key=lambda x: x["value"], reverse=True)

    # ── per stock (merge same symbol across accounts) ───────────────────────────
    stocks: dict[str, dict] = {}
    for r in enriched:
        key = r["symbol"] or ("isin:" + (r.get("isin") or "")) or ("nm:" + (r.get("name") or ""))
        s = stocks.setdefault(key, {
            "symbol": r["symbol"] or r.get("name") or "—", "name": r.get("name"),
            "exchange": r["exchange"], "currency": r.get("currency") or "INR", "isin": r.get("isin"),
            "quantity": 0.0, "invested": 0.0, "value": 0.0, "day_change": 0.0,
            # native (pre-FX) price × qty, so the merged LTP is the true weighted
            # price and ALWAYS reconciles with `value` — never the first account's
            # price copied over a value that blends every account (which diverges
            # when the live feed drops a symbol and accounts fall back to differing
            # import prices, e.g. an NSE vs BSE import of the same REIT).
            "_natval": 0.0,
            "accounts": 0, "priced": r["priced"], "screener_url": r.get("screener_url"),
        })
        if not s.get("isin") and r.get("isin"): s["isin"] = r.get("isin")
        s["priced"] = s["priced"] or r["priced"]
        s["quantity"] += r["quantity"]; s["invested"] += r["invested"]
        s["value"] += r["value"]; s["day_change"] += r["day_change"]; s["accounts"] += 1
        s["_natval"] += r["quantity"] * r["last_price"]
    by_stock = []
    for s in stocks.values():
        inv = s["invested"]; qty = s["quantity"]; val = s["value"]; day = s["day_change"]
        prev_val = val - day
        by_stock.append({
            **{k: v for k, v in s.items() if k != "_natval"},
            "avg_price": round(inv / qty, 2) if qty else 0,
            "last_price": round(s["_natval"] / qty, 2) if qty else 0,
            "invested": round(inv, 2), "value": round(val, 2),
            "day_change": round(day, 2),
            "day_change_pct": round((val / prev_val - 1.0), 4) if prev_val else 0.0,
            "pnl": round(val - inv, 2),
            "pnl_pct": round((val / inv - 1.0), 4) if inv else None,
        })
    by_stock.sort(key=lambda x: x["value"], reverse=True)

    # ── glance metrics ──────────────────────────────────────────────────────────
    movers = [s for s in by_stock if s["priced"]]
    best = max(movers, key=lambda x: x["day_change_pct"], default=None)
    worst = min(movers, key=lambda x: x["day_change_pct"], default=None)
    top = by_stock[0] if by_stock else None

    # ── per-account holding rows (powers the "view one account" filter) ─────────
    detail = [{
        "account_id": r["account_id"], "person": r["person"], "account_label": r["account_label"],
        "symbol": r["symbol"] or (r.get("name") or "—"), "name": r.get("name"), "isin": r.get("isin"),
        "exchange": r["exchange"], "quantity": r["quantity"], "avg_price": r["avg_price"],
        "last_price": r["last_price"], "invested": r["invested"], "value": r["value"],
        "pnl": r["pnl"], "pnl_pct": r["pnl_pct"], "day_change": r["day_change"],
        "day_change_pct": r["day_change_pct"], "priced": r["priced"],
        "price_manual": r.get("price_manual", False), "currency": r.get("currency") or "INR",
        "screener_url": r.get("screener_url"),
    } for r in enriched]
    detail.sort(key=lambda x: x["value"], reverse=True)

    totals = agg(enriched)
    return {
        "accounts": by_acc,
        "by_person": by_person,
        "stocks": by_stock,
        "holdings_detail": detail,
        "account_count": len(accounts),
        "connected_count": sum(1 for a in accounts if a.get("access_token")),
        "holding_count": len(enriched),
        "total_value": totals["value"],
        "total_invested": totals["invested"],
        "total_pnl": totals["pnl"],
        "total_pnl_pct": totals["pnl_pct"],
        "day_change": totals["day_change"],
        "day_change_pct": totals["day_change_pct"],
        "best_today": best and {"symbol": best["symbol"], "day_change_pct": best["day_change_pct"], "value": best["value"]},
        "worst_today": worst and {"symbol": worst["symbol"], "day_change_pct": worst["day_change_pct"], "value": worst["value"]},
        "top_holding": top and {"symbol": top["symbol"], "value": top["value"]},
        "price_source": source,
        "usd_inr": round(fx_mod.inr_per_unit("USD", rates), 4),   # for USD dividend + hover conversion
    }
