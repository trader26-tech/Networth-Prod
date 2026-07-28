"""
Kite Connect **Personal** (official, free) adapter — read-only use.

Personal apps give holdings/positions/funds/orders for free; they exclude
market data, so we price holdings via the free Yahoo feed elsewhere. Each of
your accounts has its own api_key/api_secret (one Personal app per account);
the daily login produces an access_token we store per account.
"""
from __future__ import annotations

from typing import Optional


def login_url(api_key: str) -> str:
    from kiteconnect import KiteConnect
    return KiteConnect(api_key=api_key).login_url()


def exchange(api_key: str, api_secret: str, request_token: str) -> dict:
    """request_token (from the Kite login redirect) → access_token + profile."""
    from kiteconnect import KiteConnect
    k = KiteConnect(api_key=api_key)
    session = k.generate_session(request_token, api_secret=api_secret)
    token = session["access_token"]
    k.set_access_token(token)
    prof = {}
    try:
        prof = k.profile() or {}
    except Exception:
        pass
    return {"access_token": token, "user_id": prof.get("user_id"), "user_name": prof.get("user_name")}


def fetch_holdings(api_key: str, access_token: str) -> list[dict]:
    """Normalized holdings: [{symbol, exchange, isin, quantity, avg_price}].

    Raises on an invalid/expired token so the caller can prompt a re-login.
    """
    from kiteconnect import KiteConnect
    k = KiteConnect(api_key=api_key)
    k.set_access_token(access_token)
    raw = k.holdings() or []
    out: list[dict] = []
    for h in raw:
        # total owned = free + T+1 (bought today) + pledged/collateral.
        # Kite keeps pledged shares out of `quantity`, so they must be added
        # back or invested under-counts (matches the Kite app's total).
        qty = ((h.get("quantity") or 0) + (h.get("t1_quantity") or 0)
               + (h.get("collateral_quantity") or 0))
        if not qty:
            continue
        out.append({
            "symbol": h.get("tradingsymbol"),
            "exchange": h.get("exchange") or "NSE",
            "isin": h.get("isin"),
            "quantity": qty,
            "avg_price": h.get("average_price") or 0,
        })
    return out
