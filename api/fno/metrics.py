"""
Derived F&O hero metrics: pledged capital, CAGR on it, and max drawdown.

- Pledged capital = market value of the shares pledged as F&O collateral
  (Σ collateral_quantity × last_price from kite.holdings()). This is the capital
  the strategies are run against, so it's the CAGR denominator. Refreshed in the
  background by the engine and cached in app_cache; a manual override wins.
- CAGR = the cumulative P&L (net) as a return on pledged capital, annualised over
  the days since the first trade.
- Max drawdown = the largest peak-to-trough fall of the cumulative-P&L curve,
  computed for the whole book and for each strategy.
"""
from __future__ import annotations

from datetime import datetime, date as date_cls
from typing import Optional

from . import store, kite as fno_kite
from ..portfolio import store as _kv        # generic app_cache KV (cache_get/set)

_PLEDGE_KEY = "fno_pledged"              # {"value": float, "by_account": {...}}
_OVERRIDE_KEY = "fno_pledged_override"   # {"value": float} — manual, wins if set
_REFRESH_EVERY = 1800                    # s — background pledge refresh cadence


# ── Pledged capital ─────────────────────────────────────────────────────────────
def _fetch_pledged_for(acc: dict) -> Optional[float]:
    """Market value of shares pledged as collateral on one account, live."""
    try:
        k = fno_kite.client(acc)
        holdings = k.holdings() or []
    except Exception:
        return None
    total = 0.0
    for h in holdings:
        qty = h.get("collateral_quantity") or 0
        if qty:
            total += float(qty) * float(h.get("last_price") or 0)
    return round(total, 2)


def refresh_pledged() -> Optional[dict]:
    """Best-effort background refresh (called from the engine). Sums pledged
    value across connected accounts and caches it. Never raises."""
    try:
        by_acc: dict[str, float] = {}
        for acc in store.list_accounts():
            if acc.get("access_token") and acc.get("status") == "connected":
                v = _fetch_pledged_for(acc)
                if v is not None:
                    by_acc[acc["id"]] = v
        if not by_acc:
            return None
        payload = {"value": round(sum(by_acc.values()), 2), "by_account": by_acc}
        _kv.cache_set(_PLEDGE_KEY, payload)
        return payload
    except Exception:
        return None


def pledged(accounts: Optional[set] = None) -> dict:
    """{value, source, updated_at} — for a selected subset of accounts, sum the
    cached per-account pledged values; otherwise a manual override wins, else the
    cached live total. value is None until the first fetch/override."""
    cached = _kv.cache_get(_PLEDGE_KEY)
    by_acc = None
    if cached and isinstance(cached.get("value"), dict):
        by_acc = cached["value"].get("by_account")

    if accounts and by_acc:
        val = round(sum(float(v) for k, v in by_acc.items() if k in accounts), 2)
        return {"value": val, "source": "kite", "updated_at": cached.get("updated_at")}

    ov = _kv.cache_get(_OVERRIDE_KEY)
    if ov and isinstance(ov.get("value"), dict) and ov["value"].get("value") is not None:
        return {"value": float(ov["value"]["value"]), "source": "manual", "updated_at": ov.get("updated_at")}
    if cached and isinstance(cached.get("value"), dict) and cached["value"].get("value") is not None:
        return {"value": float(cached["value"]["value"]), "source": "kite",
                "updated_at": cached.get("updated_at")}
    return {"value": None, "source": None, "updated_at": None}


def set_override(value: Optional[float]) -> dict:
    """Set (or clear when value is None) the manual pledged-capital override."""
    if value is None or value <= 0:
        _kv.cache_set(_OVERRIDE_KEY, {"value": None})
    else:
        _kv.cache_set(_OVERRIDE_KEY, {"value": round(float(value), 2)})
    return pledged()


# ── CAGR ─────────────────────────────────────────────────────────────────────────
def cagr(total_pnl: float, pledged_value: Optional[float],
         first_date: Optional[str]) -> Optional[dict]:
    """Annualise the cumulative P&L as a return on pledged capital.
    Returns {cagr, total_return, days} or None when it can't be computed."""
    if not pledged_value or pledged_value <= 0 or not first_date:
        return None
    try:
        d0 = date_cls.fromisoformat(first_date[:10])
    except ValueError:
        return None
    today = datetime.now(store.IST).date()
    days = max(1, (today - d0).days)
    total_return = total_pnl / pledged_value
    growth = 1 + total_return
    if growth <= 0:                    # lost ≥ the whole capital → CAGR undefined
        return {"cagr": None, "total_return": round(total_return, 4), "days": days,
                "pledged": pledged_value}
    annualised = growth ** (365.0 / days) - 1
    return {"cagr": round(annualised, 4), "total_return": round(total_return, 4),
            "days": days, "pledged": pledged_value}


# ── Max drawdown ────────────────────────────────────────────────────────────────
def _drawdown(series: list[tuple[str, float]]) -> dict:
    """Largest peak-to-trough fall of the cumulative sum of a dated P&L series.
    Peak starts at 0 (flat book before the first trade)."""
    cum = 0.0
    peak = 0.0
    peak_date: Optional[str] = None
    mdd = 0.0
    at_peak: Optional[str] = None
    at_trough: Optional[str] = None
    for d, v in sorted(series):
        cum += v
        if cum > peak:
            peak = cum
            peak_date = d
        dd = peak - cum
        if dd > mdd:
            mdd = dd
            at_peak = peak_date
            at_trough = d
    return {"mdd": round(mdd, 2), "peak_date": at_peak, "trough_date": at_trough}


def max_drawdown(daily_rows: list[dict]) -> dict:
    """Combined + per-strategy max drawdown from fno_daily_pnl rows."""
    combined: dict[str, float] = {}
    by_strat: dict[str, dict[str, float]] = {}
    for r in daily_rows:
        d = (r.get("date") or "")[:10]
        if not d:
            continue
        t = float(r.get("total") or 0)
        combined[d] = combined.get(d, 0.0) + t
        s = r.get("strategy") or "other"
        by_strat.setdefault(s, {})
        by_strat[s][d] = by_strat[s].get(d, 0.0) + t
    return {
        "combined": _drawdown(list(combined.items())),
        "by_strategy": {s: _drawdown(list(days.items())) for s, days in by_strat.items()},
    }
