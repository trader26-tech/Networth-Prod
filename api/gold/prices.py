"""
Live gold + silver spot prices in INR per gram, cached server-side.

Sources (free, no API key):
  • api.gold-api.com  — XAU / XAG spot in USD per troy ounce
  • open.er-api.com   — USD → INR forex

We proxy + cache these on the backend so the browser avoids CORS/rate limits.
The cache holds the *raw* spot-derived rates; any retail premium is applied by
the frontend (a per-device setting). Falls back to the last good values, then to
a hardcoded sane default, so the UI always has something to price with.
"""
from __future__ import annotations

import json
import time
import urllib.request
from datetime import datetime, timezone

_OZ_TO_G = 31.1034768
_TTL = 600  # 10 minutes
_cache: dict = {"data": None, "ts": 0.0}

# Last-resort fallback (only used if the APIs are unreachable and cache is empty).
_FALLBACK = {
    "gold_24k_per_g": 13800.0,
    "silver_per_g": 235.0,
    "usd_inr": 95.0,
    "ok": False,
    "stale": True,
    "source": "fallback",
}


def _fetch_json(url: str, timeout: float = 10.0):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _fetch_live() -> dict:
    gold_oz = float(_fetch_json("https://api.gold-api.com/price/XAU")["price"])   # USD/oz
    silver_oz = float(_fetch_json("https://api.gold-api.com/price/XAG")["price"])  # USD/oz
    usd_inr = float(_fetch_json("https://open.er-api.com/v6/latest/USD")["rates"]["INR"])
    return {
        "gold_24k_per_g": round(gold_oz * usd_inr / _OZ_TO_G, 2),
        "silver_per_g": round(silver_oz * usd_inr / _OZ_TO_G, 2),
        "usd_inr": round(usd_inr, 4),
        "spot_gold_usd_oz": round(gold_oz, 2),
        "spot_silver_usd_oz": round(silver_oz, 2),
        "ok": True,
        "stale": False,
        "source": "gold-api.com + open.er-api.com",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


_perf_cache: dict = {"data": None, "ts": 0.0}
_PERF_TTL = 6 * 3600  # 6 hours — historical CAGR barely moves intraday


def _yahoo_monthly(symbol: str) -> dict:
    """5y monthly closes from Yahoo Finance, keyed by (year, month)."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=5y&interval=1mo"
    d = _fetch_json(url, timeout=15)
    r = d["chart"]["result"][0]
    ts = r["timestamp"]
    close = r["indicators"]["quote"][0]["close"]
    out: dict = {}
    for t, c in zip(ts, close):
        if c is None:
            continue
        dt = datetime.fromtimestamp(t, tz=timezone.utc)
        out[(dt.year, dt.month)] = float(c)
    return out


def _cagr_series(series: dict) -> dict:
    """1y/3y/5y CAGR from a {(year,month): price} INR series."""
    months = sorted(series.keys())
    if len(months) < 2:
        return {}
    latest_key = months[-1]
    latest = series[latest_key]
    res: dict = {}
    for n in (1, 3, 5):
        ly, lm = latest_key
        target = (ly - n) * 12 + lm
        old_key = min(months, key=lambda m: abs((m[0] * 12 + m[1]) - target))
        old = series[old_key]
        # require the matched point to be within ~3 months of the target horizon
        if old and old > 0 and abs((old_key[0] * 12 + old_key[1]) - target) <= 3:
            res[f"{n}y"] = round((latest / old) ** (1.0 / n) - 1.0, 4)
    return res


def get_performance(force: bool = False) -> dict:
    """1y/3y/5y CAGR of gold & silver in INR/gram (international spot × forex).
    22K and 24K share the same % growth, so one 'gold' figure covers both."""
    now = time.time()
    if not force and _perf_cache["data"] and (now - _perf_cache["ts"]) < _PERF_TTL:
        return dict(_perf_cache["data"])
    try:
        gold = _yahoo_monthly("GC=F")    # USD/oz
        silver = _yahoo_monthly("SI=F")  # USD/oz
        fx = _yahoo_monthly("INR=X")     # USD/INR
        gold_inr = {m: gold[m] * fx[m] / _OZ_TO_G for m in gold if m in fx}
        silver_inr = {m: silver[m] * fx[m] / _OZ_TO_G for m in silver if m in fx}
        common = sorted(set(gold_inr) & set(silver_inr))
        data = {
            "ok": True,
            "gold": _cagr_series(gold_inr),
            "silver": _cagr_series(silver_inr),
            "series": {
                "labels": [f"{y}-{mo:02d}" for (y, mo) in common],
                "gold": [round(gold_inr[m], 2) for m in common],
                "silver": [round(silver_inr[m], 2) for m in common],
            },
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        _perf_cache["data"] = data
        _perf_cache["ts"] = now
        return dict(data)
    except Exception as e:
        if _perf_cache["data"]:
            return dict(_perf_cache["data"])
        return {"ok": False, "gold": {}, "silver": {}, "error": str(e)[:140]}


def get_prices(force: bool = False) -> dict:
    """Return cached spot rates (INR/g for 24K gold and silver), refreshing every
    ~10 min. Always returns a dict with gold_24k_per_g + silver_per_g."""
    now = time.time()
    if not force and _cache["data"] and (now - _cache["ts"]) < _TTL:
        return dict(_cache["data"])
    try:
        data = _fetch_live()
        _cache["data"] = data
        _cache["ts"] = now
        return dict(data)
    except Exception as e:
        if _cache["data"]:
            stale = dict(_cache["data"])
            stale["stale"] = True
            return stale
        fb = dict(_FALLBACK)
        fb["error"] = str(e)[:140]
        fb["updated_at"] = datetime.now(timezone.utc).isoformat()
        return fb
