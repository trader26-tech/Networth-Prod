"""
Fundamentals API — serves Snowflake-scored stock data.

Endpoints:
  GET  /api/fundamentals/snowflake             → full scored universe
  GET  /api/fundamentals/snowflake/{ticker}    → single stock
  GET  /api/fundamentals/snowflake/quotes      → live LTP for given tickers

The full universe is cached in-memory after first request (5,735 rows × 45 cols
takes ~5MB JSON; trivial). The CSV on disk is the source of truth; rebuilding
the cache means re-running the scorer.
"""
from __future__ import annotations

import time
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from api import state
from api.fundamentals.snowflake import score_universe


class IdentifierList(BaseModel):
    """POST body for batch lookups — keeps the request small and the logs clean."""
    identifiers: list[str]

router = APIRouter(prefix="/api/fundamentals", tags=["fundamentals"])


_CACHE: dict[str, object] = {
    "scored":    None,    # list[dict] of all scored stocks
    "by_ticker": None,    # dict[str, dict] — UPPER ticker
    "by_name":   None,    # dict[str, dict] — normalized name → row
    "built_at":  0.0,
}


# Common company-name suffixes to strip when matching by name. Zerodha uses
# the long form ("RELIANCE INDUSTRIES LTD"); Tickertape stores
# "Reliance Industries Ltd" — but holders also write "LIMITED", "LTD.",
# "CORP", etc. Normalize both sides before comparing.
_SUFFIX_PATTERNS = (
    " LIMITED",  " LTD.",  " LTD",
    " CORPORATION", " CORP.", " CORP",
    " COMPANY", " CO.", " CO",
    " PRIVATE", " PVT.", " PVT",
    " INC.", " INC",
    " (INDIA)", " INDIA",
)


def _norm_name(s: str) -> str:
    """Uppercase + strip trailing corporate suffixes + collapse whitespace.
    Used to match Zerodha-style 'RELIANCE INDUSTRIES LTD' against Tickertape's
    'Reliance Industries Ltd'."""
    if not s:
        return ""
    out = s.upper().strip()
    # Strip non-alphanumeric junk that sometimes appears (e.g. '&', ',')
    out = " ".join(out.split())
    # Repeatedly strip suffixes from the end
    changed = True
    while changed:
        changed = False
        for suf in _SUFFIX_PATTERNS:
            if out.endswith(suf):
                out = out[: -len(suf)].rstrip(" .,")
                changed = True
    return out


def _build_cache(force: bool = False) -> None:
    """Build the in-memory index. Defensive: any failure (missing CSV, parse
    error, etc.) results in empty caches rather than an exception bubbling
    up. Endpoints read the empty state and respond with a clean 503-style
    payload so the frontend never sees a 500 from the framework layer."""
    if not force and _CACHE["scored"] is not None:
        return
    try:
        scored = score_universe()
    except Exception as e:
        print(f"  ⚠ Snowflake score_universe failed: {type(e).__name__}: {e}")
        scored = []
    by_ticker: dict = {}
    by_name:   dict = {}
    for s in scored:
        t = (s.get("ticker") or "").upper().strip()
        if t:
            by_ticker[t] = s
        n = _norm_name(s.get("name") or "")
        if n:
            # First-seen wins — large caps come first in a sorted universe
            by_name.setdefault(n, s)
    _CACHE["scored"]    = scored
    _CACHE["by_ticker"] = by_ticker
    _CACHE["by_name"]   = by_name
    _CACHE["built_at"]  = time.time()
    if scored:
        print(f"  ✓ Snowflake cache warmed: {len(scored)} stocks "
              f"({len(by_ticker)} tickers, {len(by_name)} names indexed)")
    else:
        print(f"  ⓘ Snowflake cache empty — no universe CSV at "
              f"{UNIVERSE_CSV.relative_to(UNIVERSE_CSV.parents[2]) if UNIVERSE_CSV.exists() else UNIVERSE_CSV}. "
              "Endpoints will return empty payloads with available=false.")


def _empty_universe() -> bool:
    """True when the universe couldn't be loaded — endpoints use this to
    answer 'no data available' instead of crashing."""
    _build_cache()
    return not _CACHE["scored"]


# Warm the cache at import time so the first HTTP request doesn't pay the cost.
try:
    _build_cache()
except Exception as e:
    print(f"  ⚠ Snowflake cache warm failed at import: {e}")


@router.get("/snowflake")
def list_snowflake(
    bucket: Optional[str] = Query(None, description="Filter by bucket: KEEP_AND_ADD/HOLD/TRIM/EXIT"),
    cap: Optional[str] = Query(None, description="Filter by cap tier: large/mid/small"),
    limit: int = Query(0, ge=0, le=10_000, description="0 = no limit"),
):
    """Return scored universe. Optionally filter by bucket and/or cap tier."""
    if _empty_universe():
        return {"rows": [], "count": 0, "built_at": _CACHE["built_at"], "available": False}
    rows = list(_CACHE["scored"])  # type: ignore[arg-type]

    if bucket:
        rows = [r for r in rows if r["bucket"] == bucket.upper()]
    if cap:
        rows = [r for r in rows if r["cap_tier"] == cap.lower()]
    if limit:
        rows = rows[:limit]

    return {
        "rows":      rows,
        "count":     len(rows),
        "built_at":  _CACHE["built_at"],
        "available": True,
    }


def _resolve_batch(identifiers: list[str]) -> dict:
    """Shared resolver — matches each item against both the ticker index and
    the normalized-name index. Accepts NSE codes ('RELIANCE') or long names
    ('RELIANCE INDUSTRIES LTD') interchangeably."""
    if _empty_universe():
        return {
            "rows": [], "matches": {}, "missing": [i for i in (identifiers or []) if i and i.strip()],
            "matched": 0,
            "requested": len([i for i in (identifiers or []) if i and i.strip()]),
            "available": False,
        }
    by_ticker: dict = _CACHE["by_ticker"]  # type: ignore[assignment]
    by_name:   dict = _CACHE["by_name"]    # type: ignore[assignment]

    requested = [t.strip() for t in (identifiers or []) if t and t.strip()]
    found: dict[str, dict] = {}
    missing: list[str] = []
    for raw in requested:
        key_t = raw.upper()
        if key_t in by_ticker:
            found[raw] = by_ticker[key_t]
            continue
        key_n = _norm_name(raw)
        if key_n in by_name:
            found[raw] = by_name[key_n]
            continue
        missing.append(raw)
    return {
        "rows":      list(found.values()),
        "matches":   found,
        "missing":   missing,
        "matched":   len(found),
        "requested": len(requested),
        "available": True,
    }


@router.post("/snowflake/by-tickers")
def snowflake_by_tickers_post(body: IdentifierList):
    """Batch lookup via POST. Body: {"identifiers": ["RELIANCE", "HDFC BANK LTD", ...]}.
    Preferred over the GET form for large lists — keeps access logs readable."""
    return _resolve_batch(body.identifiers)


@router.get("/snowflake/by-tickers")
def snowflake_by_tickers_get(
    tickers: str = Query(..., description="Comma-separated tickers OR company names"),
):
    """GET form (legacy)."""
    return _resolve_batch([t for t in (tickers or "").split(",")])


def _live_quotes_impl(identifiers: list[str]) -> dict:
    """Shared resolver for the LTP endpoint (GET legacy + POST preferred)."""
    if _empty_universe():
        return {"quotes": {}, "kite_connected": state.get_kite() is not None,
                "matched": 0, "requested": 0, "available": False}
    by_ticker: dict = _CACHE["by_ticker"]  # type: ignore[assignment]
    by_name:   dict = _CACHE["by_name"]    # type: ignore[assignment]

    raw_inputs = [t.strip() for t in (identifiers or []) if t and t.strip()]
    if not raw_inputs:
        return {"quotes": {}, "kite_connected": False}

    # Map each input → NSE ticker (the symbol Kite expects).
    input_to_ticker: dict[str, str] = {}
    for raw in raw_inputs:
        key_t = raw.upper()
        if key_t in by_ticker:
            input_to_ticker[raw] = key_t
            continue
        key_n = _norm_name(raw)
        if key_n in by_name:
            sym = (by_name[key_n].get("ticker") or "").upper().strip()
            if sym:
                input_to_ticker[raw] = sym

    if not input_to_ticker:
        # Nothing matched — return empty quotes, but signal Kite state honestly.
        return {"quotes": {}, "kite_connected": state.get_kite() is not None,
                "matched": 0, "requested": len(raw_inputs)}

    kite = state.get_kite()
    if kite is None:
        return {"quotes": {}, "kite_connected": False,
                "matched": len(input_to_ticker), "requested": len(raw_inputs)}

    unique_tickers = sorted(set(input_to_ticker.values()))
    keys = [f"NSE:{s}" for s in unique_tickers]
    out: dict[str, dict] = {}
    try:
        resp = kite.ltp(keys)
        ticker_to_ltp: dict[str, float] = {}
        for sym in unique_tickers:
            entry = resp.get(f"NSE:{sym}") or {}
            ltp = entry.get("last_price")
            if ltp is not None:
                ticker_to_ltp[sym] = float(ltp)
        for raw, sym in input_to_ticker.items():
            if sym in ticker_to_ltp:
                out[raw] = {"ltp": ticker_to_ltp[sym], "ticker": sym}
    except Exception as e:
        return {"quotes": {}, "kite_connected": True, "error": str(e)}

    return {"quotes": out, "kite_connected": True,
            "matched": len(input_to_ticker), "requested": len(raw_inputs)}


@router.post("/snowflake/quotes")
def live_quotes_post(body: IdentifierList):
    """Batch live LTP via POST. Body: {"identifiers": [...]}.
    Preferred for large lists — keeps the access log clean."""
    return _live_quotes_impl(body.identifiers)


@router.get("/snowflake/quotes")
def live_quotes_get(
    tickers: str = Query(..., description="Comma-separated NSE symbols OR company names"),
):
    """GET form (legacy)."""
    return _live_quotes_impl([t for t in (tickers or "").split(",")])


@router.get("/snowflake/{ticker}")
def get_snowflake(ticker: str):
    """Single-stock snowflake detail."""
    if _empty_universe():
        raise HTTPException(503, "Snowflake universe not loaded on this deployment.")
    by_ticker: dict = _CACHE["by_ticker"]  # type: ignore[assignment]
    row = by_ticker.get(ticker.upper())
    if row is None:
        raise HTTPException(404, f"No snowflake data for {ticker!r}")
    return row


@router.post("/snowflake/rebuild")
def rebuild():
    """Force-rebuild the in-memory cache (e.g. after a CSV refresh)."""
    _build_cache(force=True)
    return {"ok": True, "built_at": _CACHE["built_at"], "count": len(_CACHE["scored"])}  # type: ignore[arg-type]


# ─── 1-year price sparklines (yfinance) ──────────────────────────────────────
# Each portfolio row can show a miniature 1Y chart. We bulk-fetch from yfinance
# (Yahoo) using the .NS suffix for NSE-listed stocks, cache for 6 hours, and
# return weekly close points so the SVG payload stays small.

_SPARKLINE_CACHE: dict[str, dict] = {}
_SPARKLINE_TTL_SECONDS = 6 * 60 * 60   # 6h — daily-bar data updates only after market close

# NSE instrument-token lookup (for the Kite fallback). Populated lazily; cleared
# only on process restart since it changes daily at most.
_KITE_INSTRUMENTS_CACHE: dict[str, int] = {}
_kite_instruments_loaded = False


def _ensure_kite_instruments() -> dict[str, int]:
    """Return {tradingsymbol: instrument_token} for all NSE equities."""
    global _kite_instruments_loaded
    if _kite_instruments_loaded:
        return _KITE_INSTRUMENTS_CACHE
    kite = state.get_kite()
    if kite is None:
        return _KITE_INSTRUMENTS_CACHE
    try:
        for inst in kite.instruments("NSE"):
            if inst.get("instrument_type") == "EQ":
                sym = (inst.get("tradingsymbol") or "").upper().strip()
                tok = inst.get("instrument_token")
                if sym and tok:
                    _KITE_INSTRUMENTS_CACHE[sym] = int(tok)
        _kite_instruments_loaded = True
    except Exception as e:
        print(f"⚠  sparklines: kite.instruments failed: {e}")
    return _KITE_INSTRUMENTS_CACHE


def _resolve_to_nse_ticker(identifier: str) -> str | None:
    """Accepts either an NSE ticker ('RELIANCE') or a long name and returns
    the NSE ticker so we can suffix with '.NS' for Yahoo."""
    if not identifier:
        return None
    _build_cache()
    by_ticker: dict = _CACHE["by_ticker"] or {}  # type: ignore[assignment]
    by_name:   dict = _CACHE["by_name"] or {}    # type: ignore[assignment]
    key_t = identifier.upper().strip()
    if key_t in by_ticker:
        return key_t
    key_n = _norm_name(identifier)
    if key_n in by_name:
        sym = (by_name[key_n].get("ticker") or "").upper().strip()
        return sym or None
    # As a last resort: assume the input IS the ticker (works for stocks not in
    # the Tickertape universe but valid on Yahoo).
    return key_t


def _fetch_sparklines_kite(tickers: list[str]) -> dict[str, dict]:
    """Fallback sparkline fetcher using Kite's historical_data API for any
    NSE equity yfinance couldn't cover. Daily bars over the last 365 days,
    resampled to weekly closes. Kite rate-limits historical to 3 req/sec
    so we keep this strictly to misses, not the bulk path."""
    kite = state.get_kite()
    if kite is None:
        return {}

    inst_map = _ensure_kite_instruments()
    if not inst_map:
        return {}

    from datetime import datetime, timedelta
    today = datetime.now().date()
    from_date = (today - timedelta(days=365)).strftime("%Y-%m-%d")
    to_date   = today.strftime("%Y-%m-%d")

    out: dict[str, dict] = {}
    for sym in tickers:
        tok = inst_map.get(sym.upper().strip())
        if not tok:
            continue
        try:
            bars = kite.historical_data(tok, from_date, to_date, "day")
        except Exception as e:
            print(f"  sparklines/kite: {sym} failed: {e}")
            continue
        if not bars:
            continue
        # Resample to weekly Friday closes (manual — no pandas).
        weekly: list[float] = []
        last_iso_week = None
        for bar in bars:
            d = bar.get("date")
            if d is None:
                continue
            iso = (d.isocalendar() if hasattr(d, "isocalendar") else None)
            if iso is None:
                continue
            week_key = (iso[0], iso[1])  # (year, week_number)
            close = bar.get("close")
            if close is None:
                continue
            if week_key != last_iso_week:
                weekly.append(float(close))
                last_iso_week = week_key
            else:
                # overwrite — keeps the last close of each ISO week
                weekly[-1] = float(close)
        if len(weekly) < 2:
            continue
        first, last = weekly[0], weekly[-1]
        high, low   = max(weekly), min(weekly)
        change_pct  = ((last - first) / first * 100.0) if first else 0.0
        out[sym.upper().strip()] = {
            "points":     [round(v, 2) for v in weekly],
            "first":      round(first, 2),
            "last":       round(last, 2),
            "high":       round(high, 2),
            "low":        round(low, 2),
            "change_pct": round(change_pct, 2),
            "source":     "kite",
        }
    return out


def _fetch_sparklines_yf(tickers: list[str]) -> dict[str, dict]:
    """Bulk-download 1Y daily history from yfinance, return weekly close
    points + summary stats per ticker."""
    import yfinance as yf  # imported lazily — keeps boot fast if dep is missing

    yf_symbols = [f"{t}.NS" for t in tickers]
    out: dict[str, dict] = {}
    try:
        # group_by="ticker" returns a wide frame keyed by symbol when multi
        data = yf.download(
            tickers=" ".join(yf_symbols),
            period="1y",
            interval="1d",
            group_by="ticker",
            threads=True,
            progress=False,
            auto_adjust=True,
        )
    except Exception as e:
        print(f"⚠  sparklines: yfinance download failed: {e}")
        return out

    for nse, yf_sym in zip(tickers, yf_symbols):
        try:
            # Single ticker: yf returns flat columns; multi: nested by symbol
            if hasattr(data, "columns") and (yf_sym in data.columns.get_level_values(0)
                                              if data.columns.nlevels > 1 else False):
                series = data[yf_sym]["Close"].dropna()
            elif "Close" in (data.columns if hasattr(data, "columns") else []):
                series = data["Close"].dropna()
            else:
                continue
            if series.empty:
                continue
            # Resample to ~52 weekly Friday closes to keep the payload tiny.
            weekly = series.resample("W-FRI").last().dropna()
            points = [round(float(v), 2) for v in weekly.tolist()]
            if len(points) < 2:
                continue
            first, last = points[0], points[-1]
            high, low = max(points), min(points)
            change_pct = ((last - first) / first * 100.0) if first else 0.0
            out[nse] = {
                "points":     points,
                "first":      first,
                "last":       last,
                "high":       high,
                "low":        low,
                "change_pct": round(change_pct, 2),
            }
        except Exception as e:
            print(f"  sparklines: failed for {nse}: {e}")
            continue
    return out


@router.post("/sparklines")
def sparklines(body: IdentifierList):
    """Return a 1Y weekly-close sparkline per identifier. Identifiers can be
    NSE tickers ('RELIANCE') or long names ('RELIANCE INDUSTRIES LTD').
    Response shape:
        { sparklines: { RELIANCE: { points: [...], change_pct: 24.0, ... } },
          missing: [...] }
    """
    raw = [t.strip() for t in (body.identifiers or []) if t and t.strip()]
    if not raw:
        return {"sparklines": {}, "missing": []}

    # Resolve every input to its NSE ticker (we need that for yfinance).
    input_to_ticker: dict[str, str] = {}
    for r in raw:
        sym = _resolve_to_nse_ticker(r)
        if sym:
            input_to_ticker[r] = sym

    now = time.time()
    sparks: dict[str, dict] = {}
    misses: list[str] = []
    # Which tickers do we need to fetch (cache miss)?
    to_fetch: set[str] = set()
    for raw_id, sym in input_to_ticker.items():
        cached = _SPARKLINE_CACHE.get(sym)
        if cached and (now - cached["fetched_at"]) < _SPARKLINE_TTL_SECONDS:
            sparks[raw_id] = cached["data"]
        else:
            to_fetch.add(sym)

    if to_fetch:
        # ── Pass 1: yfinance (bulk, fastest path) ─────────────────────────
        try:
            yf_fetched = _fetch_sparklines_yf(sorted(to_fetch))
        except ImportError:
            yf_fetched = {}  # don't bail — Kite fallback still works
            print("⚠  sparklines: yfinance not installed; falling back to Kite for all")

        # ── Pass 2: Kite for anything yfinance missed ─────────────────────
        kite_misses = sorted(to_fetch - set(yf_fetched.keys()))
        kite_fetched: dict[str, dict] = {}
        if kite_misses:
            kite_fetched = _fetch_sparklines_kite(kite_misses)

        # Merge + cache
        all_fetched = {**yf_fetched, **kite_fetched}
        for sym, data in all_fetched.items():
            # Tag the source so the UI/logs can tell where the line came from.
            if "source" not in data:
                data["source"] = "yfinance"
            _SPARKLINE_CACHE[sym] = {"data": data, "fetched_at": now}

        # Satisfy any inputs that pointed to fetched tickers.
        for raw_id, sym in input_to_ticker.items():
            if raw_id in sparks:
                continue
            if sym in all_fetched:
                sparks[raw_id] = all_fetched[sym]
            else:
                misses.append(raw_id)

    # Inputs we couldn't even resolve to a ticker are misses too.
    for r in raw:
        if r not in input_to_ticker and r not in misses:
            misses.append(r)

    return {"sparklines": sparks, "missing": misses}
