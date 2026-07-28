"""
Option chain builder — live (Kite) and mock.
Cached NFO instrument list + spot price helpers.
"""
import time
import threading
from api import options_engine as opt_eng
from api import state

# NFO instrument list cached for 1 hour. The list only changes when new contracts
# are listed or expire — once per trading day. A 1-hour TTL is generous and keeps
# us well under Kite's rate limits even when several endpoints poll in parallel.
_NFO_TTL_SECONDS = 3600

# A lock so that parallel requests don't all race to refetch the 10k-row list
# at the same time — without this, a burst of polling endpoints all blow past
# the cache check simultaneously and trigger the "Too many requests" error.
_nfo_cache: dict = {"data": None, "ts": 0.0}
_nfo_lock = threading.Lock()

def spot_for(underlying: str) -> float:
    """Live spot price for an index underlying.

    When Kite is connected: always fetches from Kite LTP — no caching, no fallback.
    If the live call fails the error propagates so the UI shows a proper message.
    When Kite is not configured at all: returns the static default (offline/dev mode).
    """
    info = opt_eng.UNDERLYINGS.get(underlying, {})
    kite = state.get_kite()
    if kite and info:
        key  = f"{info['exchange']}:{info['symbol']}"
        data = kite.ltp([key])
        p    = data.get(key, {}).get("last_price")
        if p and float(p) > 0:
            return float(p)
        raise ValueError(
            f"Kite is connected but could not fetch the live price for {underlying}. "
            "Please check your connection or re-login."
        )
    # No Kite session: static baseline only (offline/dev mode)
    return float(info.get("spot", 0))


def invalidate_nfo_cache() -> None:
    """Force the next _get_nfo_instruments() call to re-fetch from Kite.
    Call this whenever a new Kite session is established."""
    _nfo_cache["data"] = None
    _nfo_cache["ts"]   = 0.0


def _get_nfo_instruments() -> list:
    """Cached fetch of the NFO instrument list (~10k rows).

    Double-checked locking: the fast path (cache hit) is lock-free; only on a
    cache miss do we acquire the lock, and once inside, re-check the cache so
    that only ONE request actually performs the Kite call when multiple
    endpoints arrive simultaneously.
    """
    now = time.time()
    if _nfo_cache["data"] and now - _nfo_cache["ts"] < _NFO_TTL_SECONDS:
        return _nfo_cache["data"]

    with _nfo_lock:
        # Re-check inside the lock — another thread may have just refreshed it
        now = time.time()
        if _nfo_cache["data"] and now - _nfo_cache["ts"] < _NFO_TTL_SECONDS:
            return _nfo_cache["data"]
        kite = state.get_kite()
        data = kite.instruments("NFO")
        _nfo_cache["data"] = data
        _nfo_cache["ts"]   = now
        return data


def build_real_chain(
    underlying: str,
    expiry: str,
    spot: float,
    below_atm: int = 3,
    above_atm: int = 50,
) -> dict:
    """Build option chain from live Kite quote data (LTP + OI + Volume).

    below_atm/above_atm control the strike window around ATM. Default favours
    far-OTM calls (covered-call scanner). Set below_atm wider (e.g. 80) for
    far-OTM put scans.
    """
    kite        = state.get_kite()
    instruments = _get_nfo_instruments()
    relevant = [
        i for i in instruments
        if str(i.get("name", "")).upper() == underlying.upper()
        and i.get("instrument_type") in ("CE", "PE")
        and (i["expiry"].isoformat() if hasattr(i["expiry"], "isoformat")
             else str(i["expiry"])[:10]) == expiry
    ]
    if not relevant:
        raise ValueError(f"No NFO instruments for {underlying} {expiry}")

    lot_size     = relevant[0].get("lot_size") or opt_eng.LOT_SIZES.get(underlying, 75)
    all_strikes  = sorted({float(i["strike"]) for i in relevant})
    atm          = min(all_strikes, key=lambda k: abs(k - spot))
    atm_idx      = all_strikes.index(atm)
    strike_range = set(all_strikes[max(0, atm_idx - below_atm): atm_idx + above_atm + 1])

    syms_needed = [i for i in relevant if float(i["strike"]) in strike_range]
    keys        = [f"NFO:{i['tradingsymbol']}" for i in syms_needed]

    quotes: dict = {}
    for start in range(0, len(keys), 500):
        try:
            chunk = kite.quote(keys[start: start + 500])
            for k, v in chunk.items():
                sym   = k.split(":", 1)[1]
                depth = v.get("depth") or {}
                buys  = depth.get("buy")  or []
                sells = depth.get("sell") or []
                bid   = (buys[0].get("price")    if buys  else 0) or 0
                ask   = (sells[0].get("price")   if sells else 0) or 0
                bid_qty        = (buys[0].get("quantity")  if buys  else 0) or 0
                ask_qty        = (sells[0].get("quantity") if sells else 0) or 0
                bid_qty_total  = sum((b.get("quantity") or 0) for b in buys[:5])
                ask_qty_total  = sum((s.get("quantity") or 0) for s in sells[:5])
                last_trade     = v.get("last_trade_time")
                quotes[sym] = {
                    "price":          v.get("last_price") or 0.05,
                    "oi":             v.get("oi")         or 0,
                    "volume":         v.get("volume")     or 0,
                    "bid":            bid,
                    "ask":            ask,
                    "bid_qty":        bid_qty,
                    "ask_qty":        ask_qty,
                    "bid_qty_total":  bid_qty_total,
                    "ask_qty_total":  ask_qty_total,
                    "last_trade_time": (last_trade.isoformat()
                                        if hasattr(last_trade, "isoformat")
                                        else (last_trade or "")),
                }
        except Exception as e:
            print(f"Quote batch error: {e}")

    T = opt_eng.days_to_expiry(expiry)
    r = opt_eng.RISK_FREE_RATE

    def _greeks(price: float, K: float, opt_type: str):
        if T <= 0:
            return None
        iv_pct = opt_eng.implied_volatility(spot, K, T, r, price, opt_type)
        if not (1.0 <= iv_pct <= 150.0):
            return None
        sigma = iv_pct / 100
        bs    = opt_eng.black_scholes(spot, K, T, r, sigma, opt_type)
        return {"iv": round(iv_pct, 2), "delta": bs["delta"],
                "gamma": bs["gamma"], "theta": bs["theta"], "vega": bs["vega"]}

    chain_rows = []
    empty_q    = {"price": 0.05, "oi": 0, "volume": 0, "bid": 0, "ask": 0,
                  "bid_qty": 0, "ask_qty": 0, "bid_qty_total": 0, "ask_qty_total": 0}

    for K in sorted(strike_range):
        ce_inst = next((i for i in syms_needed if float(i["strike"]) == K and i["instrument_type"] == "CE"), None)
        pe_inst = next((i for i in syms_needed if float(i["strike"]) == K and i["instrument_type"] == "PE"), None)
        ce_sym  = ce_inst["tradingsymbol"] if ce_inst else ""
        pe_sym  = pe_inst["tradingsymbol"] if pe_inst else ""
        ce_q    = quotes.get(ce_sym, empty_q)
        pe_q    = quotes.get(pe_sym, empty_q)
        ce_p    = max(float(ce_q["price"]), 0.05)
        pe_p    = max(float(pe_q["price"]), 0.05)
        ce_g    = _greeks(ce_p, K, "CE")
        pe_g    = _greeks(pe_p, K, "PE")

        def _side(q, g, sym):
            return {
                "price":          max(float(q["price"]), 0.05),
                "iv":             g["iv"]    if g else None,
                "delta":          g["delta"] if g else None,
                "gamma":          g["gamma"] if g else None,
                "theta":          g["theta"] if g else None,
                "vega":           g["vega"]  if g else None,
                "oi":             int(q["oi"]),
                "volume":         int(q["volume"]),
                "bid":            float(q["bid"]),
                "ask":            float(q["ask"]),
                "bid_qty":        int(q["bid_qty"]),
                "ask_qty":        int(q["ask_qty"]),
                "bid_qty_total":  int(q.get("bid_qty_total", 0)),
                "ask_qty_total":  int(q.get("ask_qty_total", 0)),
                "symbol":         sym,
            }

        chain_rows.append({
            "strike": K, "atm": (K == atm),
            "ce": _side(ce_q, ce_g, ce_sym),
            "pe": _side(pe_q, pe_g, pe_sym),
        })

    return {
        "underlying": underlying, "spot": spot, "expiry": expiry,
        "dte": int(T * 365), "lot_size": lot_size,
        "atm_strike": atm, "chain": chain_rows,
    }
