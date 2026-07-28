"""
Prices for held symbols and the Nifty-50 benchmark, from free public feeds.

Two read paths, both cached:
  • get_quotes(...)   → {symbol: {last, prev, change_pct}} for the live stocks
    page. Sourced from Yahoo's chart `meta` (regularMarketPrice +
    previous-session close) — session-stable and correct for the day-change.
  • get_current_prices(...) → {symbol: price}; tries Kite LTP first (your
    connected Zerodha session) then falls back to Yahoo. Used elsewhere.

Plus nifty_series()/history() for the portfolio-vs-Nifty backtest.

Note: we avoid Yahoo's batched *spark* endpoint for quotes — after market close
its daily array trails today's bar with a null, so positional indexing returns a
day-shifted (stale) close. The chart `meta` fields don't have that problem.
"""
from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from typing import Optional

# NSE trading-series suffix (OCCLLTD-BE, IDEA-BZ, …). The series (BE/BZ/EQ/SM/…)
# is NOT part of the symbol Yahoo/Kite key on, so we strip it before any lookup.
# Mirrors the frontend's DividendStore.base() heuristic (dash + 1–2 letters),
# which won't touch real hyphenated tickers like BAJAJ-AUTO (4-letter tail).
_SERIES_SUFFIX = re.compile(r"-[A-Z]{1,2}$")


def _base_symbol(symbol: str) -> str:
    return _SERIES_SUFFIX.sub("", (symbol or "").upper())

_PX_TTL = 300
_Q_TTL = 30          # live-quote cache — short so polling stays near real-time
_px_cache: dict[str, tuple[float, float]] = {}     # symbol -> (price, ts)
_nifty_cache: dict = {"data": None, "ts": 0.0}
_NIFTY_TTL = 1800


def _fetch_json(url: str, timeout: float = 12.0):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _chunks(xs: list, n: int):
    for i in range(0, len(xs), n):
        yield xs[i:i + n]


def _yahoo_ticker(symbol: str, exchange: Optional[str]) -> str:
    e = (exchange or "").upper()
    if e in ("US", "USD", "NASDAQ", "NYSE", "ARCA", "BATS", "AMEX"):
        return symbol                       # US tickers carry no suffix on Yahoo
    base = _base_symbol(symbol)             # OCCLLTD-BE → OCCLLTD
    if e == "BSE":
        return base + ".BO"
    return base + ".NS"


def _yahoo_spark(tickers: list[str]) -> dict[str, float]:
    out: dict[str, float] = {}
    for chunk in _chunks(tickers, 20):   # Yahoo spark caps ~20 symbols/call
        q = urllib.parse.quote(",".join(chunk), safe=",")   # keep commas literal; encode & etc.
        url = f"https://query1.finance.yahoo.com/v8/finance/spark?symbols={q}&range=1d&interval=1d"
        try:
            data = _fetch_json(url)
            for tk, d in (data or {}).items():
                closes = [c for c in (d.get("close") or []) if c]
                if closes:
                    out[tk] = float(closes[-1])
        except Exception:
            continue
    return out


def _yahoo_chart_one(ticker: str) -> Optional[float]:
    """Per-symbol price via the chart endpoint — succeeds for some symbols the
    batch spark endpoint silently drops (e.g. GSPL.NS)."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(ticker)}?range=1d&interval=1d"
    try:
        d = _fetch_json(url)
        meta = d["chart"]["result"][0]["meta"]
        px = meta.get("regularMarketPrice") or meta.get("previousClose")
        return float(px) if px else None
    except Exception:
        return None


def _yahoo_resolve(symbol: str) -> Optional[str]:
    """Resolve a Zerodha symbol to Yahoo's current ticker via search — handles
    renames (OCCL→OCCLLTD.NS) and listings where the .NS/.BO guess is wrong.
    Returns a ticker like 'OCCLLTD.NS' or None."""
    try:
        q = urllib.parse.quote(_base_symbol(symbol))
        d = _fetch_json(
            f"https://query2.finance.yahoo.com/v1/finance/search?q={q}&quotesCount=6&newsCount=0")
        cands = [x.get("symbol") for x in (d.get("quotes") or [])
                 if (x.get("symbol") or "").endswith((".NS", ".BO"))]
        # prefer NSE, then BSE
        for suf in (".NS", ".BO"):
            for c in cands:
                if c.endswith(suf):
                    return c
    except Exception:
        return None
    return None


def get_current_prices(items: list[tuple[str, Optional[str]]]) -> tuple[dict[str, float], str]:
    """items: [(symbol, exchange)] → ({symbol: price}, source). Uses cache first."""
    now = time.time()
    prices: dict[str, float] = {}
    need: list[tuple[str, Optional[str]]] = []
    for sym, exch in items:
        c = _px_cache.get(sym)
        if c and (now - c[1]) < _PX_TTL:
            prices[sym] = c[0]
        else:
            need.append((sym, exch))
    if not need:
        return prices, "cache"

    source = "yahoo"
    fetched: dict[str, float] = {}

    # 1) Kite LTP (batched)
    try:
        from api import state
        kite = state.get_kite()
        if kite:
            keys = [f"{(e or 'NSE').upper()}:{_base_symbol(s)}" for s, e in need]
            res: dict = {}
            for ch in _chunks(keys, 400):
                res.update(kite.ltp(ch))
            for s, e in need:
                v = res.get(f"{(e or 'NSE').upper()}:{_base_symbol(s)}")
                if v and v.get("last_price"):
                    fetched[s] = float(v["last_price"])
            if fetched:
                source = "kite"
    except Exception:
        fetched = {}

    # 2) Yahoo fallback for whatever Kite didn't return
    remaining = [(s, e) for s, e in need if s not in fetched]
    yahoo_used = False
    if remaining:
        # 2a) batch spark on the best-guess .NS/.BO ticker
        tickmap = {_yahoo_ticker(s, e): s for s, e in remaining}
        yp = _yahoo_spark(list(tickmap.keys()))
        for tk, px in yp.items():
            fetched[tickmap[tk]] = px
            yahoo_used = True

        # 2b) per-symbol chart endpoint for whatever spark dropped (e.g. GSPL),
        #     trying both NSE and BSE tickers
        for s, e in remaining:
            if s in fetched:
                continue
            for tk in (_yahoo_ticker(s, e), _base_symbol(s) + ".NS", _base_symbol(s) + ".BO"):
                px = _yahoo_chart_one(tk)
                if px:
                    fetched[s] = px
                    yahoo_used = True
                    break

        # 2c) last resort: resolve renamed tickers via search (OCCL→OCCLLTD)
        for s, e in remaining:
            if s in fetched:
                continue
            tk = _yahoo_resolve(s)
            if tk:
                px = _yahoo_chart_one(tk)
                if px:
                    fetched[s] = px
                    yahoo_used = True
    if source == "kite" and yahoo_used:
        source = "kite+yahoo"
    elif source != "kite":
        source = "yahoo"

    for s, px in fetched.items():
        _px_cache[s] = (px, now)
        prices[s] = px
    return prices, source


_q_cache: dict[str, tuple[dict, float]] = {}   # symbol -> ({last, prev, change_pct}, ts)
_MAX_WORKERS = 10                              # concurrent Yahoo fetches


# A quote whose last trade is older than this is treated as DEAD, not just
# after-hours — Yahoo silently freezes some Indian tickers (e.g. EMBASSY.NS has
# been stuck at a Jul-2024 price for years while EMBASSY.BO trades live). The
# window is generous so a normal weekend + holiday (Fri close → Tue fetch, ~4d)
# never trips it; only a genuinely abandoned listing does, and the resolver then
# falls through to the sibling exchange (.NS ⇄ .BO) that is still trading.
_STALE_QUOTE_SECS = 8 * 86400


def _chart_quote(ticker: str) -> Optional[dict]:
    """{last, prev} from the chart meta. MUST use range=1d: `chartPreviousClose`
    is "the close before the requested range", so range=5d would give the close
    ~5 trading days ago — turning the day-change into a multi-day move (e.g. a
    +33% "day gain"). range=1d makes it the genuine previous-session close.

    Returns None for a STALE listing (last trade > _STALE_QUOTE_SECS ago) so the
    caller can try the same name on the other exchange rather than serve a dead,
    years-old price."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(ticker)}?range=1d&interval=1d"
    try:
        d = _fetch_json(url, timeout=8.0)
        meta = d["chart"]["result"][0]["meta"]
        t = meta.get("regularMarketTime")
        if t and (time.time() - float(t)) > _STALE_QUOTE_SECS:
            return None                     # frozen/abandoned listing — skip it
        last = meta.get("regularMarketPrice") or meta.get("previousClose") or meta.get("chartPreviousClose")
        prev = (meta.get("regularMarketPreviousClose") or meta.get("previousClose")
                or meta.get("chartPreviousClose") or last)
        return {"last": float(last), "prev": float(prev)} if last else None
    except Exception:
        return None


def _resolve_quote(sym: str, exch: Optional[str]) -> Optional[dict]:
    base = _base_symbol(sym)
    for tk in (_yahoo_ticker(sym, exch), base + ".NS", base + ".BO"):
        q = _chart_quote(tk)
        if q:
            return q
    rtk = _yahoo_resolve(sym)
    return _chart_quote(rtk) if rtk else None


def get_quotes(items: list[tuple[str, Optional[str]]]) -> tuple[dict[str, dict], str]:
    """items: [(symbol, exchange)] → ({symbol: {last, prev, change_pct}}, source).

    Free Yahoo feed with today's change %. Fetches are **concurrent** (handles
    ~1000 symbols quickly) and the last-known price is served if a refresh
    fails, so values never silently fall back to cost.
    """
    import concurrent.futures as cf
    now = time.time()
    out: dict[str, dict] = {}
    need: list[tuple[str, Optional[str]]] = []
    for sym, exch in items:
        c = _q_cache.get(sym)
        if c and (now - c[1]) < _Q_TTL:
            out[sym] = c[0]
        else:
            need.append((sym, exch))
    if not need:
        return out, "cache"

    got: dict[str, dict] = {}

    # Authoritative quotes from the chart endpoint's meta (regularMarketPrice +
    # chartPreviousClose), fetched per-symbol but concurrently. We deliberately
    # DON'T use the batched spark endpoint here: after market close its daily
    # `close` array has a null trailing (today) bar, so `closes[-1]` silently
    # becomes a *prior* day's close — day-shifted, stale prices that flip gains
    # to losses. The chart meta is session-stable (live during hours, official
    # close after) and gives the correct previous close for the day-change base.
    with cf.ThreadPoolExecutor(max_workers=_MAX_WORKERS) as ex:
        for s, q in ex.map(lambda se: (se[0], _resolve_quote(se[0], se[1])), need):
            if q:
                got[s] = q

    for s, q in got.items():
        last, prev = q["last"], q.get("prev") or q["last"]
        rec = {"last": last, "prev": prev, "change_pct": ((last / prev - 1.0) if prev else 0.0)}
        _q_cache[s] = (rec, now)
        out[s] = rec

    # anything still unresolved → serve the last-known quote rather than dropping
    # to cost (so a transient fetch failure never shows a bogus 0% / loss).
    for s, _e in need:
        if s not in out and s in _q_cache:
            out[s] = _q_cache[s][0]
    return out, "yahoo"


def _nifty_chart(rng: str) -> dict:
    """{date: close} for Nifty-50 over a range. Empty on failure."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/%5ENSEI?range={rng}&interval=1d"
    d = _fetch_json(url)
    r = d["chart"]["result"][0]
    ts = r["timestamp"]
    close = r["indicators"]["quote"][0]["close"]
    return {datetime.fromtimestamp(t, tz=timezone.utc).date(): float(c)
            for t, c in zip(ts, close) if c is not None}


# ── headline market indices (Nifty / Sensex / S&P 500 / Nasdaq) ────────────────
_INDICES: list[tuple[str, str, str]] = [
    ("nifty",  "Nifty 50", "^NSEI"),
    ("sensex", "Sensex",   "^BSESN"),
    ("sp500",  "S&P 500",  "^GSPC"),
    ("nasdaq", "Nasdaq",   "^IXIC"),
]
_idx_cache: dict = {"data": None, "ts": 0.0}
_IDX_TTL = 30                                    # near-real-time while a market is open


def _index_one(ticker: str) -> Optional[dict]:
    """Latest level + day-change for one index from Yahoo's chart `meta`."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(ticker)}?range=1d&interval=1d"
    try:
        d = _fetch_json(url)
        meta = d["chart"]["result"][0]["meta"]
        last = meta.get("regularMarketPrice")
        prev = meta.get("chartPreviousClose") or meta.get("previousClose")
        if not last or not prev:
            return None
        last, prev = float(last), float(prev)
        chg = last - prev
        return {"last": last, "change": chg,
                "change_pct": (chg / prev * 100.0) if prev else 0.0}
    except Exception:
        return None


def index_quotes() -> list[dict]:
    """[{id, label, last, change, change_pct}] for the headline indices.

    Cached ~30s and concurrent. On a transient fetch miss, serves the last good
    value per index so the hero never blanks or flashes a bogus 0%."""
    import concurrent.futures as cf
    now = time.time()
    if _idx_cache["data"] and (now - _idx_cache["ts"]) < _IDX_TTL:
        return _idx_cache["data"]
    prev_by_id = {r["id"]: r for r in (_idx_cache["data"] or [])}
    with cf.ThreadPoolExecutor(max_workers=len(_INDICES)) as ex:
        fetched = list(ex.map(lambda t: _index_one(t[2]), _INDICES))
    out: list[dict] = []
    for (iid, label, _tk), q in zip(_INDICES, fetched):
        if q:
            out.append({"id": iid, "label": label, **q})
        elif iid in prev_by_id:
            out.append(prev_by_id[iid])          # keep last-known on a miss
    if out:
        _idx_cache["data"] = out
        _idx_cache["ts"] = now
    return out


def nifty_series() -> list[tuple]:
    """Cached daily Nifty-50 series: [(date, level)] ascending. [] on failure.

    The long-range (5y) endpoint lags a day or two at the tail, which would cap
    the whole chart's x-axis short (e.g. stuck at last Friday). Overlay a fresh
    short-range fetch so the most recent trading days are present.
    """
    now = time.time()
    if _nifty_cache["data"] and (now - _nifty_cache["ts"]) < _NIFTY_TTL:
        return _nifty_cache["data"]
    try:
        merged = _nifty_chart("5y")
        try:
            merged.update(_nifty_chart("1mo"))   # refresh the recent tail
        except Exception:
            pass
        series = sorted(merged.items())
        if not series:
            return _nifty_cache["data"] or []
        _nifty_cache["data"] = series
        _nifty_cache["ts"] = now
        return series
    except Exception:
        return _nifty_cache["data"] or []


# ── historical daily closes (for the portfolio-vs-Nifty backtest) ───────────────
_hist_cache: dict[str, tuple[dict, float]] = {}     # rng -> ({symbol: [(date, close)]}, ts)
_HIST_TTL = 6 * 3600                                 # history barely changes intraday


def _hist_chunk(args) -> dict[str, list]:
    tickers, rng = args
    q = urllib.parse.quote(",".join(tickers), safe=",")
    url = f"https://query1.finance.yahoo.com/v8/finance/spark?symbols={q}&range={rng}&interval=1d"
    out: dict[str, list] = {}
    try:
        data = _fetch_json(url, timeout=14.0)
        for tk, d in (data or {}).items():
            ts = d.get("timestamp") or []
            cl = d.get("close") or []
            out[tk] = [(datetime.fromtimestamp(t, tz=timezone.utc).date(), float(c))
                       for t, c in zip(ts, cl) if c is not None]
    except Exception:
        pass
    return out


def history(items: list[tuple[str, Optional[str]]], rng: str = "1y") -> dict[str, list]:
    """{symbol: [(date, close)]} daily closes over `rng`, via batched spark
    (20 tickers/call, concurrent). Cached per range for a few hours."""
    import concurrent.futures as cf
    now = time.time()
    c = _hist_cache.get(rng)
    if c and (now - c[1]) < _HIST_TTL:
        cached = c[0]
        if all((s in cached) for s, _ in items):
            return cached
    tickmap = {_yahoo_ticker(s, e): s for s, e in items}
    chunks = [(ch, rng) for ch in _chunks(list(tickmap.keys()), 20)]
    out: dict[str, list] = {}
    with cf.ThreadPoolExecutor(max_workers=_MAX_WORKERS) as ex:
        for res in ex.map(_hist_chunk, chunks):
            for tk, ser in res.items():
                out[tickmap.get(tk, tk)] = ser
    _hist_cache[rng] = (out, now)
    return out


def nifty_summary(start_date: Optional[str]) -> dict:
    """Nifty-50 level now + at the portfolio's start date, with return & CAGR."""
    series = nifty_series()
    if not series:
        return {"ok": False}
    latest_d, latest = series[-1]
    sd = None
    try:
        y, m, dd = (int(x) for x in str(start_date)[:10].split("-"))
        sd = date(y, m, dd)
    except Exception:
        sd = series[0][0]
    # close on/after the start date
    start_pt = next(((d, c) for d, c in series if d >= sd), series[0])
    start = start_pt[1]
    years = max((latest_d - start_pt[0]).days / 365.0, 0.01)
    ret = latest / start - 1.0 if start else None
    cagr = (latest / start) ** (1.0 / years) - 1.0 if (start and years > 0) else None
    return {
        "ok": True,
        "level": round(latest, 2),
        "start_level": round(start, 2),
        "start_date": start_pt[0].isoformat(),
        "return_pct": round(ret, 6) if ret is not None else None,
        "cagr": round(cagr, 6) if cagr is not None else None,
    }
