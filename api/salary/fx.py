"""
Live foreign-exchange → INR, cached server-side.

Salaries can be earned in any currency (e.g. dad's pay in Kuwaiti Dinar). We
store the raw amount + its currency and convert to INR at *read* time, so the
dashboard always reflects today's rate.

Source (free, no API key):
  • open.er-api.com  — base INR, gives units-per-INR for every currency

We cache ~10 min and fall back to the last good values, then to a hardcoded
sane table, so the UI always has *something* to convert with. Same pattern as
api/gold/prices.py.
"""
from __future__ import annotations

import json
import time
import urllib.request
from datetime import datetime, timezone

_TTL = 600  # 10 minutes
_cache: dict = {"data": None, "ts": 0.0}

# Currencies we surface in the picker (INR first). Others still convert if the
# live feed knows them; this list just drives the dropdown + fallback table.
CURRENCIES = ["INR", "KWD", "USD", "AED", "GBP", "EUR", "SGD", "AUD", "CAD", "QAR", "SAR", "OMR", "BHD"]

# Last-resort INR per 1 unit of currency (only if the API is unreachable and the
# cache is empty). Rough mid-market values — refreshed automatically when online.
_FALLBACK_INR_PER = {
    "INR": 1.0, "KWD": 278.0, "USD": 85.0, "AED": 23.1, "GBP": 108.0,
    "EUR": 92.0, "SGD": 63.0, "AUD": 56.0, "CAD": 62.0, "QAR": 23.3,
    "SAR": 22.6, "OMR": 221.0, "BHD": 225.0,
}


def _fetch_json(url: str, timeout: float = 10.0):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _fetch_live() -> dict:
    # base INR → rates[X] = how many X per 1 INR. INR per 1 X = 1 / rates[X].
    d = _fetch_json("https://open.er-api.com/v6/latest/INR")
    rates = d.get("rates") or {}
    inr_per = {}
    for cur, per_inr in rates.items():
        try:
            if per_inr and float(per_inr) > 0:
                inr_per[cur] = round(1.0 / float(per_inr), 6)
        except (TypeError, ValueError):
            continue
    inr_per["INR"] = 1.0
    if len(inr_per) < 2:
        raise RuntimeError("forex feed returned no usable rates")
    return {
        "ok": True,
        "stale": False,
        "inr_per": inr_per,
        "source": "open.er-api.com",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def get_rates(force: bool = False) -> dict:
    """Cached INR-per-unit table, refreshing every ~10 min. Always returns a dict
    with an `inr_per` map that at least covers the common currencies."""
    now = time.time()
    if not force and _cache["data"] and (now - _cache["ts"]) < _TTL:
        return dict(_cache["data"])
    try:
        data = _fetch_live()
        # make sure every currency we offer is present (fall back per-key)
        for cur, v in _FALLBACK_INR_PER.items():
            data["inr_per"].setdefault(cur, v)
        _cache["data"] = data
        _cache["ts"] = now
        return dict(data)
    except Exception as e:
        if _cache["data"]:
            stale = dict(_cache["data"])
            stale["stale"] = True
            return stale
        return {
            "ok": False, "stale": True, "inr_per": dict(_FALLBACK_INR_PER),
            "source": "fallback", "error": str(e)[:140],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }


def inr_per_unit(currency: str, rates: dict | None = None) -> float:
    """INR value of 1 unit of `currency`. Unknown currency → treated as INR (1.0)."""
    rates = rates or get_rates()
    cur = (currency or "INR").strip().upper()
    table = rates.get("inr_per") or {}
    return float(table.get(cur) or _FALLBACK_INR_PER.get(cur) or 1.0)


def to_inr(amount: float, currency: str, rates: dict | None = None) -> float:
    """Convert `amount` in `currency` to INR at the (cached) live rate."""
    try:
        return float(amount or 0) * inr_per_unit(currency, rates)
    except (TypeError, ValueError):
        return 0.0
