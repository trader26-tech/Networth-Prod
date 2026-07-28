"""
Motilal Oswal API adapter (official, free for clients) — read-only.

Direct API login (no browser OAuth): SHA-256(password+apikey) + DOB (2FA) +
TOTP → AuthToken (daily) → getaccesstoken → accesstoken; holdings come from
/rest/report/v3/getdpholding.

Holdings give an ISIN but only a numeric token, so we resolve the trading
symbol from the **official NSE equity list** (ISIN → SYMBOL). That also means
anything not an NSE equity — bonds, NCDs, SGBs, mutual funds — is dropped, so
the user's bonds are never included.
"""
from __future__ import annotations

import csv
import io
import json
import time
import hashlib
import urllib.request
from typing import Optional

BASE = "https://openapi.motilaloswal.com"

# Common headers Motilal expects on every call. Device fields are informational;
# safe static values are fine for a server-side personal integration.
_BASE_HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "User-Agent": "MOSL/V.1.1.0",
    "SourceId": "WEB",
    "MacAddress": "00:00:00:00:00:00",
    "ClientLocalIp": "127.0.0.1",
    "ClientPublicIp": "127.0.0.1",
    "osname": "Linux", "osversion": "1.0",
    "devicemodel": "server", "manufacturer": "server",
    "productname": "AlgoInvest", "productversion": "1.0",
    # Motilal requires both of these or login fails with MO2022.
    "browsername": "Chrome", "browserversion": "120.0",
}


def _post(path: str, headers: dict, body: Optional[dict] = None) -> dict:
    data = json.dumps(body or {}).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=data, method="POST",
                                 headers={**_BASE_HEADERS, **headers})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def _err(res: dict) -> Optional[str]:
    status = str(res.get("status") or res.get("Status") or "").upper()
    if status and status not in ("SUCCESS", "TRUE", "OK"):
        return res.get("message") or res.get("Message") or "Motilal API error"
    return None


def _totp_code(totp: Optional[str]) -> Optional[str]:
    """Accept either a ready 6-digit code or a TOTP secret key (base32) and
    return the current 6-digit code."""
    if not totp:
        return None
    t = totp.strip().replace(" ", "")
    if t.isdigit() and len(t) <= 8:
        return t                                  # already a 6-digit code
    try:
        import pyotp
        return pyotp.TOTP(t).now()                # treat as a TOTP secret key
    except Exception:
        return t


# ── NSE equity ISIN → symbol map (also our bond/non-equity filter) ──────────────
_nse_map: dict[str, str] = {}
_nse_name: dict[str, str] = {}        # normalized company name → symbol (for ISIN-less files)
_nse_symname: dict[str, str] = {}     # symbol → company name (for display)
_nse_ts = 0.0
_NSE_TTL = 86400


def _norm_name(s: str) -> str:
    return "".join(ch for ch in (s or "").upper() if ch.isalnum())


def _load_nse() -> None:
    global _nse_map, _nse_name, _nse_symname, _nse_ts
    if _nse_map and (time.time() - _nse_ts) < _NSE_TTL:
        return
    try:
        req = urllib.request.Request(
            "https://archives.nseindia.com/content/equities/EQUITY_L.csv",
            headers={"User-Agent": "Mozilla/5.0", "Accept": "text/csv,*/*"})
        with urllib.request.urlopen(req, timeout=20) as r:
            text = r.read().decode("utf-8", "ignore")
        rdr = csv.DictReader(io.StringIO(text))
        m: dict[str, str] = {}
        nm: dict[str, str] = {}
        sn: dict[str, str] = {}
        for row in rdr:
            isin = (row.get(" ISIN NUMBER") or row.get("ISIN NUMBER") or "").strip()
            sym = (row.get("SYMBOL") or "").strip()
            name = (row.get("NAME OF COMPANY") or "").strip()
            if isin and sym:
                m[isin] = sym
            if sym and name:
                nm[_norm_name(name)] = sym
                sn[sym] = name
        if m:
            _nse_map, _nse_name, _nse_symname, _nse_ts = m, nm, sn, time.time()
    except Exception:
        pass


def _nse_isin_map() -> dict[str, str]:
    _load_nse()
    return _nse_map


def _nse_name_map() -> dict[str, str]:
    _load_nse()
    return _nse_name


def _nse_symbol_name_map() -> dict[str, str]:
    _load_nse()
    return _nse_symname


def login(api_key: str, api_secret: str, client_code: str, password: str,
          dob: str, totp: Optional[str] = None) -> dict:
    """→ {auth_token, access_token}. Raises on failure."""
    pwd = hashlib.sha256((password + api_key).encode("utf-8")).hexdigest()
    headers = {"ApiKey": api_key, "apisecretkey": api_secret, "vendorinfo": client_code}
    body = {"userid": client_code, "password": pwd, "2FA": dob}
    code = _totp_code(totp)
    if code:
        body["totp"] = code
    try:
        res = _post("/rest/login/v7/authdirectapi", headers, body)
    except Exception as e:
        raise RuntimeError(f"could not reach Motilal API ({e})")
    e = _err(res)
    if e:
        raise RuntimeError(e)
    auth = res.get("AuthToken") or (res.get("data") or {}).get("AuthToken")
    if not auth:
        raise RuntimeError(res.get("message") or "Motilal login failed (no AuthToken)")
    access = ""
    try:
        ar = _post("/rest/login/v1/getaccesstoken", {**headers, "Authorization": auth}, {})
        access = ar.get("accesstoken") or (ar.get("data") or {}).get("accesstoken") or ""
    except Exception:
        pass
    return {"auth_token": auth, "access_token": access}


def fetch_holdings(api_key: str, api_secret: str, auth_token: str,
                   access_token: str, client_code: str) -> list[dict]:
    """Equity-only normalized holdings. Non-equity (bonds/NCD/SGB/MF) dropped."""
    headers = {"ApiKey": api_key, "apisecretkey": api_secret,
               "Authorization": auth_token, "vendorinfo": client_code}
    if access_token:
        headers["accesstoken"] = access_token
    res = _post("/rest/report/v3/getdpholding", headers, {})
    e = _err(res)
    if e:
        raise RuntimeError(e)
    rows = res.get("data") or res.get("Data") or []
    isin_map = _nse_isin_map()
    out: list[dict] = []
    for h in rows:
        isin = (h.get("scripisinno") or h.get("isin") or "").strip()
        sym = isin_map.get(isin)
        if not sym:
            continue                       # not an NSE equity → skip bonds/NCD/SGB/MF
        qty = ((h.get("dpquantity") or 0) + (h.get("collateralquantity") or 0)
               + (h.get("btstquantity") or 0))
        if not qty:
            continue
        out.append({
            "symbol": sym, "exchange": "NSE", "isin": isin,
            "quantity": qty, "avg_price": h.get("buyavgprice") or 0,
        })
    return out
