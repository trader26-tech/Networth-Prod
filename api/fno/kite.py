"""
Kite Connect adapter for the F&O tab (paid app — market data allowed).

Each fno_account logs in through the paid Kite Connect app (env
KITE_API_KEY/KITE_API_SECRET by default, per-account override possible), so one
app serves any number of Zerodha user logins — each login yields that user's
own access_token, stored on the account row.
"""
from __future__ import annotations

import re
from typing import Optional

from . import store


class NotConfigured(Exception):
    pass


def _creds(acc: dict) -> tuple[str, str]:
    key, sec = store.account_creds(acc)
    if not key or not sec:
        raise NotConfigured(
            "No Kite API app configured — set KITE_API_KEY / KITE_API_SECRET in .env "
            "or store an api_key/api_secret on this account.")
    return key, sec


def login_url(acc: dict) -> str:
    from kiteconnect import KiteConnect
    key, _ = _creds(acc)
    return KiteConnect(api_key=key).login_url()


def exchange(acc: dict, request_token: str) -> dict:
    """request_token (from the Kite login redirect) → access_token + profile."""
    from kiteconnect import KiteConnect
    key, sec = _creds(acc)
    k = KiteConnect(api_key=key)
    session = k.generate_session(request_token, api_secret=sec)
    token = session["access_token"]
    k.set_access_token(token)
    prof = {}
    try:
        prof = k.profile() or {}
    except Exception:
        pass
    return {"access_token": token,
            "user_id": prof.get("user_id") or session.get("user_id"),
            "user_name": prof.get("user_name") or session.get("user_name")}


def validate_access_token(acc: dict, access_token: str) -> dict:
    """Store-a-ready-token path: verify it against Kite (profile() raises on a
    bad/expired token) and return the same shape as exchange()."""
    from kiteconnect import KiteConnect
    key, _ = _creds(acc)
    k = KiteConnect(api_key=key)
    k.set_access_token(access_token.strip())
    prof = k.profile() or {}
    return {"access_token": access_token.strip(),
            "user_id": prof.get("user_id"), "user_name": prof.get("user_name")}


def client(acc: dict):
    """KiteConnect for a connected account. Raises on a missing token."""
    from kiteconnect import KiteConnect
    key, _ = _creds(acc)
    token = (acc.get("access_token") or "").strip()
    if not token:
        raise NotConfigured("Account not connected — log in to Kite first.")
    k = KiteConnect(api_key=key)
    k.set_access_token(token)
    return k


def is_token_error(e: Exception) -> bool:
    msg = str(e).lower()
    return any(w in msg for w in ("token", "session", "expire", "unauth",
                                  "login", "403", "api_key"))


# ── Strategy classification ─────────────────────────────────────────────────────
# sentinel  — the ATM short-straddle program: index options on NFO/BFO.
# crude     — the crude-oil program: anything CRUDEOIL on MCX.
# other     — everything else (stock options, other commodities…).
_INDEX_PREFIX = re.compile(r"^(NIFTY|BANKNIFTY|FINNIFTY|MIDCPNIFTY|NIFTYNXT50|SENSEX|BANKEX)")


def classify(exchange: str, tradingsymbol: str, instrument_type: str = "") -> str:
    ex = (exchange or "").upper()
    sym = (tradingsymbol or "").upper()
    itype = (instrument_type or "").upper()
    if ex == "MCX" and sym.startswith("CRUDEOIL"):
        return "crude"
    opt = itype in ("CE", "PE") or sym.endswith("CE") or sym.endswith("PE")
    if ex in ("NFO", "BFO") and opt and _INDEX_PREFIX.match(sym):
        return "sentinel"
    return "other"


# ── Contract size (₹ per point, per traded unit) ────────────────────────────────
# MCX fills are stored in market LOTS and each lot moves ₹(contract size) per
# ₹1 price move — crude oil = 100 barrels. Kite reports this only on live
# positions (`multiplier`); its instrument master lists lot_size = 1 for EVERY
# MCX contract, so we can't read it there. Realised P&L from the trade log is
# therefore price_diff × qty × contract_multiplier. NFO/BFO/CDS/BCD fills already
# arrive in full units (e.g. SENSEX 140 = 7 lots × 20), so their multiplier is 1.
MCX_CONTRACT_SIZE = {
    "CRUDEOIL": 100, "CRUDEOILM": 10,
    "NATURALGAS": 1250, "NATURALGASMINI": 250, "NATGASMINI": 250,
    "GOLD": 100, "GOLDM": 10, "GOLDGUINEA": 8, "GOLDPETAL": 1, "GOLDTEN": 10,
    "SILVER": 30, "SILVERM": 5, "SILVERMIC": 1, "SILVER1000": 1,
    "COPPER": 2500, "ZINC": 5000, "ZINCMINI": 5000, "LEAD": 5000, "LEADMINI": 5000,
    "ALUMINIUM": 5000, "ALUMINI": 5000, "NICKEL": 1500, "MENTHAOIL": 360,
}


def _mcx_root(tradingsymbol: str) -> str:
    """CRUDEOIL26JUL6600CE → CRUDEOIL — the leading alphabetic run (drops the
    expiry / strike / CE-PE-FUT tail)."""
    s = (tradingsymbol or "").upper()
    i = 0
    while i < len(s) and s[i].isalpha():
        i += 1
    return s[:i]


def contract_multiplier(exchange: str, tradingsymbol: str) -> int:
    """₹-per-point multiplier for ONE traded unit of this instrument. 1 for
    NFO/BFO/CDS/BCD (quantities already come in full units); the MCX contract
    size for commodities (crude = 100). Unknown MCX roots fall back to 1 —
    add them to MCX_CONTRACT_SIZE as needed."""
    if (exchange or "").upper() != "MCX":
        return 1
    return MCX_CONTRACT_SIZE.get(_mcx_root(tradingsymbol), 1)


# ── Fetchers (normalized) ───────────────────────────────────────────────────────
def fetch_positions(k) -> dict:
    """kite.positions() → {'day': [...], 'net': [...]} with our strategy tag."""
    raw = k.positions() or {}
    out = {"day": [], "net": []}
    for book in ("day", "net"):
        for p in raw.get(book) or []:
            out[book].append({
                "tradingsymbol": p.get("tradingsymbol"),
                "exchange": p.get("exchange"),
                "instrument_token": p.get("instrument_token"),
                "product": p.get("product"),
                "quantity": p.get("quantity") or 0,
                "multiplier": p.get("multiplier") or 1,
                "average_price": p.get("average_price") or 0,
                "last_price": p.get("last_price") or 0,
                "buy_value": p.get("buy_value") or 0,
                "sell_value": p.get("sell_value") or 0,
                "m2m": p.get("m2m") or 0,
                "pnl": p.get("pnl") or 0,
                "realised": p.get("realised") or 0,
                "unrealised": p.get("unrealised") or 0,
                "strategy": classify(p.get("exchange"), p.get("tradingsymbol")),
            })
    return out


def fetch_trades(k, account_id: str) -> list[dict]:
    """Today's fills, normalized to fno_trades rows."""
    raw = k.trades() or []
    rows = []
    for t in raw:
        ts = t.get("fill_timestamp") or t.get("exchange_timestamp") or t.get("order_timestamp")
        ts_iso = ts.isoformat() if hasattr(ts, "isoformat") else (str(ts) if ts else None)
        date = (ts_iso or store.today_ist())[:10]
        sym = t.get("tradingsymbol") or ""
        itype = "CE" if sym.endswith("CE") else "PE" if sym.endswith("PE") else \
                ("FUT" if sym.endswith("FUT") else "")
        rows.append({
            "account_id": account_id,
            "trade_id": str(t.get("trade_id")),
            "order_id": str(t.get("order_id") or ""),
            "strategy": classify(t.get("exchange"), sym),
            "tradingsymbol": sym,
            "exchange": t.get("exchange"),
            "instrument_type": itype,
            "transaction_type": t.get("transaction_type"),
            "quantity": float(t.get("quantity") or 0),
            "price": float(t.get("average_price") or t.get("price") or 0),
            "product": t.get("product"),
            "trade_date": date,
            "fill_ts": ts_iso,
            "source": "kite",
        })
    # equities etc. don't belong here — keep only derivatives exchanges
    return [r for r in rows if (r["exchange"] or "").upper() in ("NFO", "MCX", "BFO", "CDS", "BCD")]
