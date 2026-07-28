"""
F&O tab routes — multi-account Zerodha via the paid Kite Connect app.

Flow per account (mirrors /api/equity but with the paid app as default creds):
  1. POST /api/fno/accounts                 → save (label, person[, own key/secret])
  2. GET  /api/fno/accounts/{id}/login-url  → Kite login (event logged)
  3. POST /api/fno/accounts/{id}/connect    → request_token → access_token (logged)
Live P&L then flows from the background engine (api/fno/engine.py):
  ws  /ws/fno            per-second day P&L push (display only)
  DB  fno_pnl_snapshots  1-minute history   → GET /series?range=today
  DB  fno_daily_pnl      per-day per-strategy → calendar / ranges / strategy stats
"""
from __future__ import annotations

import asyncio
from datetime import date as date_cls, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, UploadFile, File, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from api import state
from api.fno import store, kite as fno_kite, pnl as fno_pnl, engine as fno_engine, metrics as fno_metrics, openpos as fno_openpos, health as fno_health
from api.auth import kite_oauth

router = APIRouter(prefix="/api/fno", tags=["fno"])
ws_router = APIRouter(tags=["fno"])          # /ws/* lives outside the /api prefix


def _guard():
    if not store.tables_ready():
        raise HTTPException(503, store.MIGRATION_HINT)


@router.api_route("/cron/tick", methods=["GET", "POST"])
def cron_tick(key: Optional[str] = None):
    """External per-minute heartbeat — captures the current minute's P&L snapshot
    server-side so it's stored even when NOBODY has the app open (and wakes an
    idle host). Auth-exempt (listed in api/main.py `_AUTH_PUBLIC`) but gated by
    the `FNO_CRON_KEY` env var when it's set. Point a per-minute cron at
    `/api/fno/cron/tick?key=…` during market hours (see SUPABASE.md → pg_cron)."""
    want = store._read_env("FNO_CRON_KEY")
    if want and (key or "").strip() != want:
        raise HTTPException(403, "Bad cron key.")
    _guard()
    return fno_engine.capture_once()


class AccountIn(BaseModel):
    account_label: str
    person: Optional[str] = None
    api_key: Optional[str] = None        # blank → paid app from .env
    api_secret: Optional[str] = None
    note: Optional[str] = None


class EditIn(BaseModel):
    account_label: Optional[str] = None
    person: Optional[str] = None
    note: Optional[str] = None
    api_key: Optional[str] = None        # this account's OWN Kite Connect app key
    api_secret: Optional[str] = None


class ConnectIn(BaseModel):
    # Either paste the request_token (or the whole Kite redirect URL) so we do
    # the exchange, or paste a ready access_token to store directly.
    request_token: Optional[str] = None
    access_token: Optional[str] = None


class TradePatch(BaseModel):
    strategy: str                        # any short name (sentinel, crude, ram, …)


# ── Accounts + login ────────────────────────────────────────────────────────────
def _accounts_with_meta() -> list[dict]:
    """Public accounts enriched with each one's single-tradebook status
    (imported-fill count + last import's filename/time from the login log)."""
    stats = store.import_stats()
    feed_id = store.get_price_feed_id()
    last_import: dict = {}
    for l in store.list_logs(500):                    # newest-first
        aid = l.get("account_id")
        if l.get("event") == "tradebook_import" and aid not in last_import:
            last_import[aid] = l
    out = []
    for a in store.list_accounts():
        pa = store.public_account(a)
        li = last_import.get(a["id"])
        detail = (li or {}).get("detail") or ""
        pa["tradebook"] = {
            "count": stats.get(a["id"], 0),
            "name": detail.split(":")[0].strip() if detail else None,
            "at": (li or {}).get("created_at"),
        }
        pa["price_feed"] = (a["id"] == feed_id)
        stmt = store.get_pnl_statement(a["id"])   # brokerage/charges from the imported P&L statement
        pa["pnl_charges"] = round(float((stmt or {}).get("charges") or 0), 2) if stmt else None
        pa["pnl_other"] = round(float((stmt or {}).get("other") or 0), 2) if stmt else None
        out.append(pa)
    return out


@router.get("/accounts")
def accounts():
    _guard()
    return _accounts_with_meta()


# ── data-health (dashboard bell) — relogins + stale/incomplete F&O data ─────────
class HealthDismissIn(BaseModel):
    key: str
    token: str = ""
    action: str = "done"          # 'done' (until state changes) | 'ignore' (until restored)


@router.get("/health")
def data_health():
    """Things to fix: expired Kite sessions (Ranjeev twice — his account + the
    master price-feed app) and tradebooks not caught up to the last trading day.
    Degrades gracefully to an empty feed if the F&O tables aren't migrated."""
    if not store.tables_ready():
        return {"today": "", "issues": [], "count": 0, "high_count": 0,
                "ignored": [], "ignored_count": 0, "ok": True}
    return fno_health.build_health()


@router.post("/health/dismiss")
def data_health_dismiss(body: HealthDismissIn):
    fno_health.dismiss(body.key, body.token, body.action)
    return fno_health.build_health()


@router.post("/health/restore")
def data_health_restore(body: HealthDismissIn):
    fno_health.restore(body.key)
    return fno_health.build_health()


@router.post("/accounts")
def add_account(body: AccountIn):
    _guard()
    if not body.account_label.strip():
        raise HTTPException(400, "Account label is required.")
    acc = store.add_account({
        "account_label": body.account_label.strip(),
        "person": (body.person or "").strip() or None,
        "api_key": (body.api_key or "").strip() or None,
        "api_secret": (body.api_secret or "").strip() or None,
        "note": body.note, "status": "pending",
    })
    try:
        url = kite_oauth.begin_login("fno", acc)
    except Exception as e:
        raise HTTPException(400, f"Saved, but couldn't build the Kite login URL: {e}")
    store.add_log(acc["id"], "login_url_issued", f"account added ({acc['account_label']})")
    return {**store.public_account(acc), "login_url": url}


@router.get("/accounts/{acc_id}/login-url")
def login_url(acc_id: str):
    _guard()
    acc = store.get_account(acc_id)
    if not acc:
        raise HTTPException(404, "Account not found.")
    try:
        url = kite_oauth.begin_login("fno", acc)
    except Exception as e:
        raise HTTPException(400, f"Could not build the Kite login URL: {e}")
    store.add_log(acc_id, "login_url_issued", acc.get("account_label") or "")
    return {"login_url": url}


def _extract_request_token(pasted: str) -> str:
    """Accept the bare request_token OR the whole Kite redirect URL the user
    copies from their address bar (…?request_token=XXXX&action=login&…)."""
    s = (pasted or "").strip()
    if not s:
        return ""
    if "request_token" in s:
        try:
            from urllib.parse import urlparse, parse_qs
            qs = urlparse(s).query or s
            vals = parse_qs(qs)
            if vals.get("request_token"):
                return vals["request_token"][0].strip()
        except Exception:
            pass
        import re
        m = re.search(r"request_token=([A-Za-z0-9._-]+)", s)
        if m:
            return m.group(1)
    return s          # assume they pasted just the token


def _route_and_store(clicked_id: str, clicked_acc: dict, sess: dict) -> dict:
    """Route a fresh Kite session to the account it ACTUALLY belongs to — the one
    whose kite_user_id matches whoever just authorized on Kite — not blindly to the
    button that was clicked. This is what makes multi-account work when Kite reuses
    the browser's existing Zerodha login (it returns the same user every time)."""
    uid = (sess.get("user_id") or "").strip()
    accounts = store.list_accounts()
    owner = next((a for a in accounts if (a.get("kite_user_id") or "").strip() == uid), None) if uid else None

    if owner:                                   # this Zerodha user already has an account
        target, routed = owner, (owner["id"] != clicked_id)
    elif not (clicked_acc.get("kite_user_id") or "").strip():
        target, routed = clicked_acc, False     # unclaimed slot → this user claims it
    else:
        # the clicked account is a KNOWN, different Zerodha user → don't overwrite it
        raise HTTPException(409,
            f"You logged in to Kite as {sess.get('user_name') or uid} ({uid}), but this "
            f"account belongs to {clicked_acc.get('user_name') or clicked_acc.get('kite_user_id')}. "
            f"Kite reuses your browser's Zerodha login, so it returns whoever is already "
            f"signed in. To connect a DIFFERENT account: open the Kite login in an incognito "
            f"window, sign in as that account, then paste its redirect URL here. (Or use "
            f"‘Add account’ to create one for {uid}.)")

    res = _store_session(target["id"], target, sess)
    res["routed"] = routed
    res["connected_as"] = {"user_id": uid, "user_name": sess.get("user_name"),
                           "account_label": target.get("account_label"), "account_id": target["id"]}
    return res


def _store_session(acc_id: str, acc: dict, sess: dict) -> dict:
    """Persist a fresh Kite session on the account + refresh the shared app
    session, then pull today's fills. Shared by both connect paths."""
    store.update_account(acc_id, {
        "access_token": sess["access_token"], "kite_user_id": sess.get("user_id"),
        "user_name": sess.get("user_name"), "status": "connected",
        "token_updated_at": store._now(),
    })
    store.add_log(acc_id, "connected",
                  f"{sess.get('user_id') or ''} {sess.get('user_name') or ''}".strip())

    # Account on the shared paid app → also refresh the app-wide Kite session
    # (.env KITE_ACCESS_TOKEN powers option-chain, ticker & co).
    if not (acc.get("api_key") or "").strip():
        try:
            state.write_env(KITE_ACCESS_TOKEN=sess["access_token"])
            state.load_kite()
        except Exception:
            pass

    acc = store.get_account(acc_id)
    synced = 0
    try:
        k = fno_kite.client(acc)
        synced = store.upsert_trades(fno_kite.fetch_trades(k, acc_id))
        store.update_account(acc_id, {"last_synced": store._now()})
    except Exception:
        pass
    return {"ok": True, "account": store.public_account(store.get_account(acc_id)),
            "trades_synced": synced}


@router.post("/accounts/{acc_id}/connect")
def connect(acc_id: str, body: ConnectIn):
    _guard()
    acc = store.get_account(acc_id)
    if not acc:
        raise HTTPException(404, "Account not found.")

    # Path A — a ready access_token pasted directly (validate, then store).
    direct = (body.access_token or "").strip()
    if direct:
        try:
            sess = fno_kite.validate_access_token(acc, direct)
        except Exception as e:
            store.add_log(acc_id, "login_failed", f"access_token: {str(e)[:280]}")
            raise HTTPException(400, "That access token was rejected by Kite — it may be "
                                     "for a different app or already expired.")
        return _route_and_store(acc_id, acc, sess)

    # Path B — request_token (or a pasted redirect URL) → exchange.
    req = _extract_request_token(body.request_token or "")
    if not req:
        raise HTTPException(400, "Paste the request token (or the Kite redirect URL), "
                                 "or a ready access token.")
    try:
        sess = fno_kite.exchange(acc, req)
    except Exception as e:
        store.add_log(acc_id, "login_failed", str(e)[:300])
        msg = str(e).lower()
        if "checksum" in msg:
            raise HTTPException(400, "API secret doesn't match the API key — check the app credentials.")
        if "token" in msg and ("invalid" in msg or "expired" in msg):
            raise HTTPException(400, "Request token invalid/expired (it lasts only a few "
                                     "minutes) — log in on Kite again and paste the fresh token.")
        raise HTTPException(400, f"Kite login failed: {e}")
    return _route_and_store(acc_id, acc, sess)


@router.get("/accounts/{acc_id}/credentials")
def account_credentials(acc_id: str):
    """Reveal this account's effective Kite api_key + api_secret so the edit panel
    can show them for viewing / copying (into the matching Stocks account). This
    is a single-user personal app and every /api route is already behind the OTP+
    PIN auth, so only the owner ever sees these."""
    _guard()
    acc = store.get_account(acc_id)
    if not acc:
        raise HTTPException(404, "Account not found.")
    key, sec = store.account_creds(acc)
    return {"api_key": key or "", "api_secret": sec or ""}


@router.post("/accounts/{acc_id}/disconnect")
def disconnect_account(acc_id: str):
    """Log off this account (and any Stocks/F&O account sharing its api_key +
    Kite user) by clearing the stored token — the mirror of the login fan-out.
    Lets you flush a session and re-test the login without waiting for the daily
    expiry."""
    _guard()
    acc = store.get_account(acc_id)
    if not acc:
        raise HTTPException(404, "Account not found.")
    cleared = kite_oauth.disconnect("fno", acc)
    store.add_log(acc_id, "disconnected", f"logged off · {len(cleared)} linked")
    return {"ok": True, "cleared": cleared}


@router.put("/accounts/{acc_id}")
def edit_account(acc_id: str, body: EditIn):
    _guard()
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    # api_key/api_secret: a blank string means "leave the stored key as-is" (the
    # edit form never pre-fills the secret), so only overwrite when a value is given.
    for k in ("api_key", "api_secret"):
        if k in patch and not (patch[k] or "").strip():
            patch.pop(k)
        elif k in patch:
            patch[k] = patch[k].strip()
    acc = store.update_account(acc_id, patch)
    if not acc:
        raise HTTPException(404, "Account not found.")
    return store.public_account(acc)


@router.delete("/accounts/{acc_id}")
def delete_account(acc_id: str):
    _guard()
    if not store.delete_account(acc_id):
        raise HTTPException(404, "Account not found.")
    if store.get_price_feed_id() == acc_id:
        store.set_price_feed_id(None)
    store.set_account_strategy(acc_id, None)
    return {"ok": True}


class PriceFeedIn(BaseModel):
    account_id: Optional[str] = None      # None clears the designation


@router.put("/price-feed")
def set_price_feed(body: PriceFeedIn):
    """Designate the paid account whose Kite session provides live prices for
    every account (free accounts read LTPs through it)."""
    _guard()
    if body.account_id and not store.get_account(body.account_id):
        raise HTTPException(404, "Account not found.")
    store.set_price_feed_id(body.account_id)
    return {"ok": True, "account_id": body.account_id}


@router.get("/login-log")
def login_log(limit: int = 100):
    _guard()
    labels = {a["id"]: a.get("account_label") for a in store.list_accounts()}
    return [{**r, "account_label": labels.get(r.get("account_id"))}
            for r in store.list_logs(min(limit, 500))]


# ── Trades ──────────────────────────────────────────────────────────────────────
@router.post("/accounts/{acc_id}/sync-trades")
def sync_trades(acc_id: str):
    _guard()
    acc = store.get_account(acc_id)
    if not acc:
        raise HTTPException(404, "Account not found.")
    try:
        k = fno_kite.client(acc)
        added = store.upsert_trades(fno_kite.fetch_trades(k, acc_id))
    except fno_kite.NotConfigured as e:
        raise HTTPException(409, str(e))
    except Exception as e:
        if fno_kite.is_token_error(e):
            store.update_account(acc_id, {"status": "expired"})
            store.add_log(acc_id, "token_expired", str(e)[:200])
            raise HTTPException(401, "Kite session expired — log in to this account again.")
        raise HTTPException(400, f"Could not fetch trades: {e}")
    store.update_account(acc_id, {"last_synced": store._now()})
    return {"ok": True, "added": added}


@router.get("/trades")
def trades(date: Optional[str] = None, strategy: Optional[str] = None,
           accounts: Optional[str] = None):
    _guard()
    # resolve each row's strategy for display/filtering: an explicit per-trade
    # pin wins, else the per-account pin (crude always crude) / stored value.
    ov = store.get_account_strategies()
    tp = store.get_trade_strategies()
    rows = store.list_trades(date=date)
    for t in rows:
        t["strategy"] = store.resolve_trade_strategy(t, ov, tp)
    if strategy:
        rows = [t for t in rows if t.get("strategy") == strategy]
    sel = _acc_set(accounts)
    if sel:
        rows = [t for t in rows if t.get("account_id") in sel]
    return rows


@router.get("/open-positions")
def open_positions(accounts: Optional[str] = None):
    """Legs still open across the selected accounts, marked to market via the
    paid price-feed. `unrealized` here is CARRIED FORWARD — it is never part of
    any day's realised P&L (that books when the leg is finally closed)."""
    _guard()
    sel = _acc_set(accounts)
    res = fno_openpos.open_positions(list(sel) if sel else None)
    res["as_of"] = datetime.now().isoformat()
    return res


class LegStrategyIn(BaseModel):
    account_id: str
    tradingsymbol: str
    strategy: Optional[str] = None       # None / "" clears the per-leg override


@router.put("/open-positions/strategy")
def set_leg_strategy(body: LegStrategyIn):
    """Retag ONE open leg's strategy from the Open Positions table. Overrides the
    account pin for that leg only, and touches only the open/live view — the
    closed daily history is untouched (no rebuild)."""
    _guard()
    strat = (body.strategy or "").strip().lower()
    if strat and (len(strat) > 24 or not all(c.isalnum() or c in " -_" for c in strat)):
        raise HTTPException(400, "strategy must be a short name (letters/numbers).")
    store.set_leg_strategy(body.account_id, body.tradingsymbol, strat or None)
    return {"ok": True, "strategy": strat or None}


@router.get("/open-series")
def open_series(accounts: Optional[str] = None, date: Optional[str] = None):
    """Intraday points of the open-positions (carry-forward) unrealized P&L for
    the current day — the live graph of where the open book stands, marked to
    market by the engine every minute. Forward-filled + summed over the selected
    accounts."""
    _guard()
    sel = _acc_set(accounts)
    day = (date or store.today_ist())[:10]
    pts = store.get_open_series(day)
    last: dict = {}
    out = []
    for p in pts:
        for a, v in (p.get("a") or {}).items():
            if not sel or a in sel:
                last[a] = v
        out.append({"t": (p.get("t") or "")[:16], "pnl": round(sum(last.values()), 2)})
    return {"date": day, "points": out}


@router.patch("/trades/{trade_pk}")
def patch_trade(trade_pk: str, body: TradePatch):
    _guard()
    strat = (body.strategy or "").strip().lower()
    if not strat or len(strat) > 24 or not all(c.isalnum() or c in " -_" for c in strat):
        raise HTTPException(400, "strategy must be a short name (letters/numbers).")
    row = store.update_trade(trade_pk, {"strategy": strat})
    if not row:
        raise HTTPException(404, "Trade not found.")
    store.set_trade_strategies([trade_pk], strat)         # explicit pin → wins over the account pin
    store.delete_trades_daily(row.get("account_id"))      # strategy key may change → start clean
    fno_pnl.rebuild_daily_from_trades(row.get("account_id"))
    return row


# ── Strategy catalog (add / remove the strategies you can tag trades with) ────
@router.get("/strategy-catalog")
def strategy_catalog():
    _guard()
    return store.get_strategy_catalog()


class NewStrategyIn(BaseModel):
    key: str
    label: Optional[str] = None
    color: Optional[str] = None


@router.post("/strategy-catalog")
def add_strategy_route(body: NewStrategyIn):
    _guard()
    key = (body.key or "").strip().lower()
    if not key or len(key) > 24 or not all(c.isalnum() or c in " -_" for c in key):
        raise HTTPException(400, "Strategy name must be short (letters, numbers, spaces, - or _).")
    return store.add_strategy(key, body.label or key, body.color or "#6b7190")


@router.delete("/strategy-catalog/{key}")
def remove_strategy_route(key: str):
    _guard()
    return store.remove_strategy(key)


class BulkTradeStrategyIn(BaseModel):
    ids: list[str]
    strategy: str


@router.patch("/trades/bulk/strategy")
def bulk_trade_strategy(body: BulkTradeStrategyIn):
    """Set the strategy on MANY trades at once, then rebuild each affected
    account's daily P&L exactly ONCE. This is what the Trade-History bulk-assign
    UI calls — far faster and more consistent than reclassifying row by row."""
    _guard()
    strat = (body.strategy or "").strip().lower()
    if not strat or len(strat) > 24 or not all(c.isalnum() or c in " -_" for c in strat):
        raise HTTPException(400, "strategy must be a short name (letters/numbers).")
    accounts = store.update_trades_strategy(body.ids or [], strat)   # stored value + affected accounts
    store.set_trade_strategies(body.ids or [], strat)                # explicit pin → wins over the account pin
    for acc in accounts:
        store.delete_trades_daily(acc)
        fno_pnl.rebuild_daily_from_trades(acc)
    return {"ok": True, "updated": len(body.ids or []), "accounts": len(accounts), "strategy": strat}


class BulkLegItem(BaseModel):
    account_id: str
    tradingsymbol: str


class BulkLegStrategyIn(BaseModel):
    legs: list[BulkLegItem]
    strategy: Optional[str] = None       # None / "" clears the per-leg override


@router.put("/open-positions/bulk-strategy")
def bulk_leg_strategy(body: BulkLegStrategyIn):
    """Retag MANY open legs' strategy at once from the Open-Positions bulk UI.
    Overrides only the live/open view (no daily-history rebuild)."""
    _guard()
    strat = (body.strategy or "").strip().lower()
    if strat and (len(strat) > 24 or not all(c.isalnum() or c in " -_" for c in strat)):
        raise HTTPException(400, "strategy must be a short name (letters/numbers).")
    n = 0
    for leg in (body.legs or []):
        store.set_leg_strategy(leg.account_id, leg.tradingsymbol, strat or None)
        n += 1
    return {"ok": True, "updated": n, "strategy": strat or None}


class StrategyIn(BaseModel):
    strategy: Optional[str] = None      # None / "" clears the pin


@router.put("/accounts/{acc_id}/strategy")
def set_account_strategy(acc_id: str, body: StrategyIn):
    """Pin every (non-crude) trade from this account to a strategy name — e.g.
    all of Ranjeev's fills → 'sentinel', all of Ram's → 'ram'. Crude oil always
    stays its own 'crude' strategy. Rebuilds the account's daily P&L so the
    calendar, ranges and strategy chart reflect the pin immediately."""
    _guard()
    if not store.get_account(acc_id):
        raise HTTPException(404, "Account not found.")
    store.set_account_strategy(acc_id, body.strategy)
    store.delete_trades_daily(acc_id)          # strategy keys change → rebuild clean
    fno_pnl.rebuild_daily_from_trades(acc_id)
    return {"ok": True, "account_id": acc_id, "strategy": store.get_account_strategy(acc_id)}


def _coverage(tbs: list[dict], legacy: Optional[dict]) -> dict:
    """Overall coverage across all tradebook records (+ any legacy import)."""
    spans = [t for t in tbs if t.get("date_from") and t.get("date_to")]
    if legacy and legacy.get("date_from"):
        spans = spans + [legacy]
    fro = min((t["date_from"] for t in spans), default=None)
    to = max((t["date_to"] for t in spans), default=None)
    fills = sum(t.get("count", 0) for t in tbs) + (legacy.get("count", 0) if legacy else 0)
    return {"date_from": fro, "date_to": to, "fills": fills, "books": len(spans)}


def _legacy_tradebook(acc_id: str) -> Optional[dict]:
    """A synthetic record for old 'source=import' fills (pre-multi-tradebook) so
    they still show on the timeline."""
    dates = [(t.get("trade_date") or "")[:10] for t in store.all_trades()
             if t.get("account_id") == acc_id and t.get("source") == "import"]
    dates = [d for d in dates if d]
    if not dates:
        return None
    return {"id": "legacy", "name": "Earlier import", "date_from": min(dates),
            "date_to": max(dates), "count": len(dates), "at": None, "legacy": True}


def _statements_coverage(stmts: list) -> dict:
    """Union span + summed charges/realised across an account's P&L statements."""
    if not stmts:
        return {"date_from": None, "date_to": None, "count": 0,
                "charges": 0.0, "other": 0.0, "realized": 0.0, "unrealized": 0.0}
    def _sum(k):
        return round(sum(float(s.get(k) or 0) for s in stmts), 2)
    return {
        "count": len(stmts),
        "date_from": min((s.get("date_from") for s in stmts if s.get("date_from")), default=None),
        "date_to": max((s.get("date_to") for s in stmts if s.get("date_to")), default=None),
        "charges": _sum("charges"), "other": _sum("other"),
        "realized": _sum("realized"), "unrealized": _sum("unrealized"),
    }


def _statement_public(s: dict) -> dict:
    """Statement record for the UI — omit the bulky per-day breakdown."""
    return {k: v for k, v in s.items() if k != "days"}


def _tradebooks_payload(acc_id: str) -> dict:
    tbs = store.get_tradebooks(acc_id)
    legacy = _legacy_tradebook(acc_id)
    books = list(tbs) + ([legacy] if legacy else [])
    books.sort(key=lambda t: t.get("date_from") or "")
    stmts = store.get_pnl_statements(acc_id)
    return {"tradebooks": books, "coverage": _coverage(tbs, legacy),
            "statement": store.get_pnl_statement(acc_id),           # aggregate (back-compat)
            "statements": [_statement_public(s) for s in stmts],    # the list, for the timeline
            "statement_coverage": _statements_coverage(stmts)}


@router.get("/accounts/{acc_id}/tradebooks")
def list_tradebooks(acc_id: str):
    _guard()
    if not store.get_account(acc_id):
        raise HTTPException(404, "Account not found.")
    return _tradebooks_payload(acc_id)


@router.post("/accounts/{acc_id}/import-tradebook")
async def import_tradebook(acc_id: str, file: UploadFile = File(...)):
    """Add a Zerodha Console → Reports → Tradebook export (F&O / MCX, CSV or XLSX)
    to this account. Multiple tradebooks are supported — each is APPENDED (Kite
    caps exports at ~1 year, so you add one per period). Overlaps are safe: fills
    dedupe on (account, trade_id). Each import records its own date range for the
    coverage timeline, then daily P&L is rebuilt from the merged set."""
    _guard()
    acc = store.get_account(acc_id)
    if not acc:
        raise HTTPException(404, "Account not found.")
    raw = await file.read()
    tb_id = __import__("uuid").uuid4().hex[:10]
    try:
        rows = fno_pnl.parse_tradebook(acc_id, file.filename or "", raw, source=f"tb:{tb_id}")
    except Exception as e:
        raise HTTPException(400, str(e))
    if not rows:
        raise HTTPException(400, "No F&O/MCX trades found in that file — export the "
                                 "Tradebook for the F&O or MCX segment from Console.")
    dates = [r["trade_date"] for r in rows if r.get("trade_date")]
    date_from, date_to = (min(dates), max(dates)) if dates else (None, None)
    added = store.upsert_trades(rows)           # APPEND + dedupe on (account, trade_id)
    store.add_tradebook(acc_id, {
        "id": tb_id, "name": file.filename or "tradebook",
        "date_from": date_from, "date_to": date_to,
        "count": len(rows), "added": added, "at": store._now(),
    })
    store.delete_trades_daily(acc_id)
    rebuild = fno_pnl.rebuild_daily_from_trades(acc_id)
    store.add_log(acc_id, "tradebook_import", f"{file.filename}: {len(rows)} fills ({date_from}→{date_to}), {added} new")
    return {"ok": True, "rows": len(rows), "added": added, "date_from": date_from, "date_to": date_to,
            **rebuild, **_tradebooks_payload(acc_id)}


@router.get("/accounts/{acc_id}/pnl-statements")
def list_pnl_statements(acc_id: str):
    _guard()
    if not store.get_account(acc_id):
        raise HTTPException(404, "Account not found.")
    return _tradebooks_payload(acc_id)


@router.post("/accounts/{acc_id}/import-pnl-statement")
async def import_pnl_statement(acc_id: str, file: UploadFile = File(...)):
    """Import a Zerodha P&L STATEMENT (Console → Reports → P&L → Download, Excel).
    Unlike the tradebook (fills, which can be incomplete), this carries Zerodha's
    own authoritative realised P&L per symbol — booked matches Console exactly.

    MULTIPLE statements are supported (Console caps the range; download one per
    period). Each is added and becomes authoritative for the days it covers. If a
    new statement's date range OVERLAPS one already imported, the old one is
    REPLACED for that period, so booked is never counted twice. Persisted in the
    DB (app_cache) so it survives refreshes/restarts."""
    _guard()
    if not store.get_account(acc_id):
        raise HTTPException(404, "Account not found.")
    raw = await file.read()
    try:
        parsed = fno_pnl.parse_pnl_statement(acc_id, file.filename or "", raw)
    except Exception as e:
        raise HTTPException(400, str(e))
    sm = parsed["summary"]
    stmt_id = __import__("uuid").uuid4().hex[:10]
    rec = {"id": stmt_id, "name": file.filename or "P&L statement",
           "date_from": parsed.get("date_from"), "date_to": parsed.get("date_to"),
           "symbols": len(parsed["symbols"]),
           "realized": sm.get("realized"), "unrealized": sm.get("unrealized"),
           "charges": sm.get("charges"), "other": sm.get("other"),
           "days": fno_pnl.statement_days(acc_id, parsed), "at": store._now()}
    before = {s["id"] for s in store.get_pnl_statements(acc_id) if s.get("id")}
    kept = store.add_pnl_statement(acc_id, rec)         # overlap → replace
    replaced = len(before) + 1 - len(kept)              # how many overlaps got dropped
    res = fno_pnl.rebuild_statement_daily(acc_id)       # rewrite authoritative rows from ALL
    store.add_log(acc_id, "pnl_statement_import",
                  f"{rec['name']}: realised {sm.get('realized')} ({rec['date_from']}→{rec['date_to']})"
                  + (f", replaced {replaced} overlapping" if replaced > 0 else ""))
    return {"ok": True, **res, "replaced": max(0, replaced),
            "statement": rec, **_tradebooks_payload(acc_id)}


@router.delete("/accounts/{acc_id}/pnl-statements/{stmt_id}")
def delete_one_pnl_statement(acc_id: str, stmt_id: str):
    """Remove ONE imported statement and re-derive booked from the rest (+ fills
    for the days no statement covers now)."""
    _guard()
    if not store.get_account(acc_id):
        raise HTTPException(404, "Account not found.")
    store.remove_pnl_statement(acc_id, stmt_id)
    fno_pnl.rebuild_statement_daily(acc_id)
    store.add_log(acc_id, "pnl_statement_deleted", f"{stmt_id} removed")
    return {"ok": True, **_tradebooks_payload(acc_id)}


@router.delete("/accounts/{acc_id}/pnl-statement")
def delete_pnl_statement(acc_id: str):
    """Drop ALL imported P&L statements and fall back to the fill-replay booked."""
    _guard()
    if not store.get_account(acc_id):
        raise HTTPException(404, "Account not found.")
    store.clear_pnl_statements(acc_id)
    store.delete_daily_by_source(acc_id, "statement")
    fno_pnl.rebuild_daily_from_trades(acc_id)
    store.add_log(acc_id, "pnl_statement_deleted", "cleared all · reverted to fill-replay booked")
    return {"ok": True, "reverted": True, **_tradebooks_payload(acc_id)}


@router.delete("/accounts/{acc_id}/tradebooks/{tb_id}")
def delete_one_tradebook(acc_id: str, tb_id: str):
    """Remove ONE tradebook (its fills + record) and rebuild daily P&L. Overlapping
    fills also present in another tradebook are kept (they carry that book's source)."""
    _guard()
    if not store.get_account(acc_id):
        raise HTTPException(404, "Account not found.")
    removed = store.delete_import_trades(acc_id) if tb_id == "legacy" else store.delete_trades_by_source(acc_id, f"tb:{tb_id}")
    if tb_id != "legacy":
        store.remove_tradebook(acc_id, tb_id)
    store.delete_trades_daily(acc_id)
    fno_pnl.rebuild_daily_from_trades(acc_id)
    store.add_log(acc_id, "tradebook_deleted", f"{tb_id}: {removed} fills removed")
    return {"ok": True, "removed": removed, **_tradebooks_payload(acc_id)}


@router.delete("/accounts/{acc_id}/tradebook")
def delete_tradebook(acc_id: str):
    """Clear ALL of this account's tradebooks (imported fills + records) and
    rebuild daily P&L from whatever Kite-synced fills remain."""
    _guard()
    acc = store.get_account(acc_id)
    if not acc:
        raise HTTPException(404, "Account not found.")
    removed = store.delete_import_trades(acc_id)
    for tb in store.get_tradebooks(acc_id):
        store.remove_tradebook(acc_id, tb.get("id"))
    store.delete_trades_daily(acc_id)
    fno_pnl.rebuild_daily_from_trades(acc_id)
    store.add_log(acc_id, "tradebook_deleted", f"cleared all · {removed} fills removed")
    return {"ok": True, "removed": removed}


@router.post("/rebuild-daily")
def rebuild_daily(account_id: Optional[str] = None):
    _guard()
    return fno_pnl.rebuild_daily_from_trades(account_id)


# ── Aggregates: summary, calendar, chart series, strategy stats ────────────────
def _acc_set(accounts: Optional[str]) -> Optional[set]:
    """Parse the comma-separated ?accounts= filter → a set of ids (None = all)."""
    if not accounts:
        return None
    ids = {a for a in accounts.split(",") if a}
    return ids or None


def _daily_rows(accounts: Optional[set] = None,
                date_from: Optional[str] = None, date_to: Optional[str] = None):
    rows = store.list_daily(date_from=date_from, date_to=date_to)
    if accounts:
        rows = [r for r in rows if r.get("account_id") in accounts]
    return rows


@router.get("/summary")
def summary(accounts: Optional[str] = None):
    _guard()
    sel = _acc_set(accounts)
    rows = _daily_rows(sel)
    today = store.today_ist()
    overall = sum(float(r.get("realized") or 0) for r in rows)
    today_total = sum(float(r.get("realized") or 0) for r in rows if (r.get("date") or "")[:10] == today)
    by_strategy: dict[str, float] = {}
    for r in rows:
        s = r.get("strategy") or "other"
        by_strategy[s] = round(by_strategy.get(s, 0.0) + float(r.get("realized") or 0), 2)
    first = min(((r.get("date") or "")[:10] for r in rows), default=None)
    pledged = fno_metrics.pledged(sel)
    live = fno_engine.latest()
    # today's live number, scoped to the selected accounts, so the hero/CAGR/
    # drawdown all agree with the chart.
    live_accs = live.get("accounts") or []
    if sel:
        live_accs = [a for a in live_accs if a.get("id") in sel]
    live_today = round(sum(a.get("day_pnl", 0) for a in live_accs), 2) if (live.get("market_open") and live_accs) else None
    overall_live = overall + (live_today - today_total) if live_today is not None else overall
    return {
        "accounts": _accounts_with_meta(),
        "market_open": fno_engine.market_open(),
        "live": live,
        "overall_pnl": round(overall, 2),          # all-time BOOKED (realised)
        "today_pnl": round(today_total, 2),        # BOOKED today (realised)
        "by_strategy": by_strategy,
        "since": first,
        "trading_days": len({(r.get("date") or "")[:10] for r in rows}),
        "pledged": pledged,
        "cagr": fno_metrics.cagr(overall, pledged.get("value"), first),
        "max_drawdown": fno_metrics.max_drawdown(rows),
    }


class PledgedIn(BaseModel):
    value: Optional[float] = None        # None / ≤0 clears the manual override


@router.get("/pledged")
def get_pledged():
    _guard()
    return fno_metrics.pledged()


@router.put("/pledged")
def set_pledged(body: PledgedIn):
    """Manually set (or clear) the pledged-capital used as the CAGR denominator."""
    _guard()
    return fno_metrics.set_override(body.value)


@router.post("/pledged/refresh")
def refresh_pledged():
    """Re-pull pledged collateral value live from Kite right now."""
    _guard()
    res = fno_metrics.refresh_pledged()
    if res is None:
        raise HTTPException(409, "Couldn't read pledged value from Kite — is an account connected?")
    return {"ok": True, **fno_metrics.pledged()}


@router.get("/calendar")
def calendar(year: int, month: int, accounts: Optional[str] = None):
    _guard()
    if not (1 <= month <= 12):
        raise HTTPException(400, "month must be 1–12")
    sel = _acc_set(accounts)
    start = date_cls(year, month, 1)
    end = (start.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    rows = _daily_rows(sel, start.isoformat(), end.isoformat())
    days: dict[str, dict] = {}
    for r in rows:
        d = (r.get("date") or "")[:10]
        e = days.setdefault(d, {"date": d, "total": 0.0, "by_strategy": {}, "trades_count": 0})
        t = float(r.get("realized") or 0)
        e["total"] = round(e["total"] + t, 2)
        s = r.get("strategy") or "other"
        e["by_strategy"][s] = round(e["by_strategy"].get(s, 0.0) + t, 2)
        e["trades_count"] += int(r.get("trades_count") or 0)
    # live-sourced rows carry no fill counts — take the real number from the log
    fills: dict[str, int] = {}
    for t in store.all_trades():
        if sel and t.get("account_id") not in sel:
            continue
        d = (t.get("trade_date") or "")[:10]
        if start.isoformat() <= d <= end.isoformat():
            fills[d] = fills.get(d, 0) + 1
    for d, e in days.items():
        e["trades_count"] = max(e["trades_count"], fills.get(d, 0))
    month_total = round(sum(d["total"] for d in days.values()), 2)
    return {"year": year, "month": month, "days": sorted(days.values(), key=lambda x: x["date"]),
            "month_total": month_total}


@router.get("/series")
def series(range: str = "today", accounts: Optional[str] = None,
           date: Optional[str] = None):
    """Chart data. today (or an explicit date=YYYY-MM-DD) → 1-minute intraday
    points (the live per-second tail comes from /ws/fno). Longer ranges →
    cumulative daily P&L."""
    _guard()
    sel = _acc_set(accounts)
    rng = (range or "today").lower()
    if date:
        rng = "today"                      # an explicit day uses the intraday shape
    if rng == "today":
        today = (date or store.today_ist())[:10]
        # Live day → the rolling last-hour window (that's all the DB keeps). An
        # explicit PAST date has no minutes left (pruned) → empty, and the client
        # shows that day's closing total instead.
        snaps = store.list_snapshots(today) if date else store.list_recent_snapshots(75)
        if sel:
            snaps = [s for s in snaps if s.get("account_id") in sel]
        # forward-fill each account so a missed minute doesn't dip the sum.
        # Group by the minute, but keep a TZ-AWARE timestamp for each so the client
        # plots every point at its true clock time (snapshots come back from the DB
        # in UTC; stripping the zone would shift the whole intraday line by 5h30m).
        by_ts: dict[str, dict[str, float]] = {}
        ts_full: dict[str, str] = {}
        for s in snaps:
            raw = s.get("ts") or ""
            key = raw[:16]                          # minute bucket
            ts_full[key] = raw                      # full tz-aware ts for the client
            by_ts.setdefault(key, {})[s.get("account_id")] = float(s.get("day_pnl") or 0)
        last: dict[str, float] = {}
        points = []
        for key in sorted(by_ts):
            last.update(by_ts[key])
            points.append({"t": ts_full[key], "pnl": round(sum(last.values()), 2)})
        return {"range": "today", "date": today, "points": points}

    days_back = {"1w": 7, "1m": 31, "3m": 92, "6m": 183, "1y": 366}.get(rng)
    date_from = ((datetime.now(store.IST) - timedelta(days=days_back)).strftime("%Y-%m-%d")
                 if days_back else None)
    rows = _daily_rows(sel, date_from)
    accts_list = store.list_accounts()
    labels = {a["id"]: (a.get("account_label") or a.get("kite_user_id") or "—") for a in accts_list}
    persons = {a["id"]: a.get("person") for a in accts_list}
    by_date: dict[str, float] = {}
    by_date_acct: dict[str, dict[str, float]] = {}
    by_date_strat: dict[str, dict[str, float]] = {}
    for r in rows:
        d = (r.get("date") or "")[:10]
        aid = r.get("account_id")
        strat = r.get("strategy") or "other"
        tot = float(r.get("realized") or 0)
        by_date[d] = by_date.get(d, 0.0) + tot
        inner = by_date_acct.setdefault(d, {})
        inner[aid] = inner.get(aid, 0.0) + tot
        inner_s = by_date_strat.setdefault(d, {})
        inner_s[strat] = inner_s.get(strat, 0.0) + tot
    cum = 0.0
    points = []
    for d in sorted(by_date):
        day_total = round(by_date[d], 2)
        cum = round(cum + day_total, 2)
        # who made / lost how much that day — biggest loss first
        accts = sorted(
            ({"account_id": aid, "label": labels.get(aid, "—"),
              "person": persons.get(aid), "total": round(v, 2)}
             for aid, v in by_date_acct[d].items()),
            key=lambda x: x["total"])
        points.append({"t": d, "day": day_total, "pnl": cum, "by_account": accts,
                       "by_strategy": {s: round(v, 2) for s, v in by_date_strat[d].items()}})
    return {"range": rng, "points": points}


@router.get("/strategies")
def strategies(accounts: Optional[str] = None):
    _guard()
    rows = _daily_rows(_acc_set(accounts))
    today = store.today_ist()
    out: dict[str, dict] = {}
    by_strat_days: dict[str, dict[str, float]] = {}
    for r in rows:
        s = r.get("strategy") or "other"
        d = (r.get("date") or "")[:10]
        by_strat_days.setdefault(s, {})
        by_strat_days[s][d] = by_strat_days[s].get(d, 0.0) + float(r.get("realized") or 0)
    for s, days in by_strat_days.items():
        vals = list(days.values())
        wins = [v for v in vals if v > 0]
        losses = [v for v in vals if v < 0]
        best_d = max(days, key=lambda d: days[d])
        worst_d = min(days, key=lambda d: days[d])
        recent = sorted(days)[-14:]
        out[s] = {
            "strategy": s,
            "total": round(sum(vals), 2),
            "today": round(days.get(today, 0.0), 2),
            "days": len(vals),
            "win_days": len(wins),
            "loss_days": len(losses),
            "win_rate": round(len(wins) / len(vals), 4) if vals else None,
            "avg_day": round(sum(vals) / len(vals), 2) if vals else 0,
            "best": {"date": best_d, "total": round(days[best_d], 2)},
            "worst": {"date": worst_d, "total": round(days[worst_d], 2)},
            "recent": [{"date": d, "total": round(days[d], 2)} for d in recent],
        }
    order = {"sentinel": 0, "crude": 1, "other": 2}
    return sorted(out.values(), key=lambda x: order.get(x["strategy"], 9))


# ── Live WebSocket — per-second day P&L (display only; DB stays 1-minute) ──────
@ws_router.websocket("/ws/fno")
async def fno_ws(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            await websocket.send_json({"type": "fno_pnl", "data": fno_engine.latest()})
            await asyncio.sleep(1)
    except (WebSocketDisconnect, RuntimeError):
        pass
