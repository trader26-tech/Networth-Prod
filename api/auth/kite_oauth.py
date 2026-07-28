"""
Kite OAuth broker — the one place that turns a Kite ``request_token`` into stored
sessions, for BOTH the Stocks (equity) and F&O tabs.

Why this exists
---------------
The old flow made you copy the ``request_token`` out of the Kite redirect URL and
paste it into a modal. Now Kite's **Redirect URL** points at a backend callback
(``/api/auth/kite/callback``) that hands the token straight to :func:`complete_login`
— no paste.

A single Kite ``access_token`` authorises BOTH holdings and F&O positions for a
Zerodha user (the "Personal free" vs "paid Connect" split only limits market
data). So when a login lands we **fan the fresh token out to every account —
stock and F&O — that shares the same Kite user AND the same api_key**. That is
how one login lights up both tabs (e.g. Ranjeev's paid app covering Stocks + F&O).

State handshake (CSRF-safe)
---------------------------
:func:`begin_login` mints a single-use ``state`` nonce, remembers which account
started the flow (in the durable ``app_cache`` KV, short TTL) and appends it to
the Kite login URL via ``redirect_params``. Kite echoes it back on the postback,
so the callback knows which app's ``api_secret`` to exchange with. We never store
the secret in the KV — creds are re-derived from the account row at callback time.
If Kite ever drops the param, :func:`complete_login` falls back to the shared env
paid app so the common case still works.
"""
from __future__ import annotations

import secrets
import time
from typing import Optional, Tuple
from urllib.parse import quote

from api import state
from api.fno import store as fno_store
from api.fno import kite as fno_kite
from api.portfolio import store as eq_store
from api.portfolio import kite_client as eq_kite

# A request_token dies in ~5 min at Kite; give the handshake a little longer.
_PENDING_TTL = 600.0


# ── pending-login state (durable KV, survives replicas/restarts) ─────────────────
def _kv_key(nonce: str) -> str:
    return f"kite_login:{nonce}"


def _remember(nonce: str, data: dict) -> None:
    eq_store.cache_set(_kv_key(nonce), {**data, "ts": time.time()})


def _recall(nonce: str) -> Optional[dict]:
    rec = eq_store.cache_get(_kv_key(nonce)) if nonce else None
    val = (rec or {}).get("value")
    if not val or val.get("used"):
        return None
    if time.time() - float(val.get("ts") or 0) > _PENDING_TTL:
        return None
    return val


def _burn(nonce: str) -> None:
    """Single-use: mark the nonce spent so a replayed callback can't reuse it."""
    try:
        eq_store.cache_set(_kv_key(nonce), {"used": True, "ts": time.time()})
    except Exception:
        pass


# Single-user fallback: the LAST login we started. The callback uses this when
# Kite didn't echo redirect_params AND the nonce lookup missed — so we NEVER fall
# back to the paid env app (which triggers "user not enabled" for other accounts).
_PENDING_SLOT = "kite_pending_login"


def _set_pending(acc_id, source) -> None:
    try:
        eq_store.cache_set(_PENDING_SLOT, {"acc_id": acc_id, "source": source, "ts": time.time()})
    except Exception:
        pass


def _get_pending() -> Optional[dict]:
    try:
        rec = eq_store.cache_get(_PENDING_SLOT)
        val = (rec or {}).get("value")
        if val and time.time() - float(val.get("ts") or 0) <= _PENDING_TTL:
            return val
    except Exception:
        pass
    return None


# ── begin ────────────────────────────────────────────────────────────────────────
def _creds_for(source: str, acc: dict) -> Tuple[str, str]:
    if source == "fno":
        return fno_store.account_creds(acc)
    return (acc.get("api_key") or "").strip(), (acc.get("api_secret") or "").strip()


def begin_login(source: str, acc: dict) -> str:
    """Build the Kite login URL for ``acc`` (``source`` = 'fno' | 'stock') and stash
    a state nonce so the callback can finish the exchange with the right app.
    """
    from kiteconnect import KiteConnect
    api_key, _ = _creds_for(source, acc)
    if not api_key:
        raise ValueError("No Kite API key configured for this account.")
    from urllib.parse import urlencode
    nonce = secrets.token_urlsafe(18)
    _remember(nonce, {"acc_id": acc.get("id"), "source": source})
    _set_pending(acc.get("id"), source)          # single-user fallback if state is lost
    # Encode the account id + source DIRECTLY in redirect_params (Kite echoes these
    # back on the postback) so the callback knows exactly which app's key to use —
    # even if the durable KV nonce lookup fails (missing app_cache / a 2nd replica).
    # Falling back to the shared paid app would trigger "user not enabled" for any
    # non-paid account, so we avoid guessing.
    rp = urlencode({"src": source, "acc": acc.get("id") or "", "st": nonce})
    base = KiteConnect(api_key=api_key).login_url()
    return f"{base}&redirect_params={quote(rp, safe='')}"


# ── complete (called by the public callback) ─────────────────────────────────────
def complete_login(request_token: str, source: Optional[str] = None,
                   acc_id: Optional[str] = None, state_nonce: str = "") -> dict:
    """Exchange ``request_token`` and fan the resulting session out to every
    matching account. ``source``/``acc_id`` come from the redirect_params Kite
    echoed back (authoritative); the state nonce is the durable-KV fallback.
    Raises ValueError with a user-friendly message on failure.
    """
    request_token = (request_token or "").strip()
    if not request_token:
        raise ValueError("No request token was returned by Kite.")

    # Resolve WHICH account started this login, most-reliable source first:
    #   1) redirect_params echoed by Kite  2) the durable state nonce
    #   3) the single-user "last login started" slot (works even if 1 & 2 fail)
    if not (source and acc_id):
        rec = _recall(state_nonce) or _get_pending()
        source = source or (rec or {}).get("source")
        acc_id = acc_id or (rec or {}).get("acc_id")

    api_key = api_secret = ""
    if source == "fno" and acc_id:
        acc = fno_store.get_account(acc_id)
        if acc:
            api_key, api_secret = fno_store.account_creds(acc)
    elif source == "stock" and acc_id:
        acc = eq_store.get_account(acc_id)
        if acc:
            api_key = (acc.get("api_key") or "").strip()
            api_secret = (acc.get("api_secret") or "").strip()

    # Absolute last resort — the shared paid app. This is WRONG for any account
    # that isn't on the paid app (→ "user not enabled"), so we only reach here if
    # every resolver above missed; the friendlier exchange error then guides the fix.
    if not (api_key and api_secret):
        api_key, api_secret = fno_store.env_app_creds()
        source, acc_id = source or "fno", acc_id
    if not (api_key and api_secret):
        raise ValueError("Could not determine which Kite app to complete this login with.")

    session = _exchange(api_key, api_secret, request_token)
    if state_nonce:
        _burn(state_nonce)
    accounts = _fan_out(api_key, session, source, acc_id)
    _refresh_shared_app(api_key, session)
    return {"ok": True, "user_id": session.get("user_id"),
            "user_name": session.get("user_name"), "accounts": accounts}


def _exchange(api_key: str, api_secret: str, request_token: str) -> dict:
    from kiteconnect import KiteConnect
    try:
        k = KiteConnect(api_key=api_key)
        sess = k.generate_session(request_token, api_secret=api_secret)
    except Exception as e:
        msg = str(e).lower()
        if "not enabled" in msg:
            raise ValueError("The user is not enabled for this app — this account's API key belongs to a "
                             "different Zerodha user. Open this account and set its OWN Kite Connect app "
                             "key/secret (created while logged in as this user), then log in again.")
        if "checksum" in msg:
            raise ValueError("API secret doesn't match the API key — check the app credentials in the developer console.")
        if "token" in msg and ("invalid" in msg or "expired" in msg):
            raise ValueError("The login token expired (it lasts only a few minutes) — please log in again.")
        raise ValueError(f"Kite login failed: {e}")
    token = sess["access_token"]
    prof = {}
    try:
        k.set_access_token(token)
        prof = k.profile() or {}
    except Exception:
        pass
    return {"access_token": token,
            "user_id": (prof.get("user_id") or sess.get("user_id") or "").strip(),
            "user_name": prof.get("user_name") or sess.get("user_name")}


# ── linked accounts — the ONE definition, shared by login AND logout ─────────────
def _linked(api_key: str, uid: str, init_source: Optional[str],
            init_acc_id: Optional[str]) -> list[tuple[str, dict]]:
    """Every account — stock + F&O — linked to this login: same effective api_key
    AND same Kite user, plus the initiating account when it's still an unclaimed
    slot. This is the single source of truth for "linked" so a login (fan the
    token OUT) and a logout (clear them all) can never disagree about the set.
    Returns (kind, account) where kind is 'fno' | 'stock'."""
    uid = (uid or "").strip()
    env_key = (fno_store.env_app_creds()[0] or "").strip()

    def matches(a: dict, app_key: str, source: str) -> bool:
        if app_key != api_key:
            return False
        au = (a.get("kite_user_id") or "").strip()
        if uid and au == uid:                 # already this user's account
            return True
        if source == init_source and a.get("id") == init_acc_id:
            return (not au) or au == uid      # claim only if unclaimed / same user
        return False

    out: list[tuple[str, dict]] = []
    for a in fno_store.list_accounts():
        if matches(a, (a.get("api_key") or "").strip() or env_key, "fno"):
            out.append(("fno", a))
    for a in eq_store.list_accounts():
        if matches(a, (a.get("api_key") or "").strip(), "stock"):
            out.append(("stock", a))
    return out


def _fan_out(api_key: str, session: dict, init_source: Optional[str],
             init_acc_id: Optional[str]) -> list[dict]:
    """Write the fresh token onto every linked account, then best-effort resync
    each so both tabs go live from one login."""
    uid = (session.get("user_id") or "").strip()
    token = session["access_token"]
    updated: list[dict] = []
    for kind, a in _linked(api_key, uid, init_source, init_acc_id):
        if kind == "fno":
            fno_store.update_account(a["id"], {
                "access_token": token, "kite_user_id": uid or a.get("kite_user_id"),
                "user_name": session.get("user_name"), "status": "connected",
                "token_updated_at": fno_store._now()})
            _sync_fno(a["id"])
        else:
            eq_store.update_account(a["id"], {
                "access_token": token, "kite_user_id": uid or a.get("kite_user_id"),
                "status": "connected", "token_updated_at": eq_store._now()})
            _sync_stock(a["id"])
        updated.append({"source": kind, "id": a["id"], "label": a.get("account_label")})
    return updated


# ── logout — flush a whole Zerodha user, across BOTH tabs ─────────────────────────
# Asymmetry vs login is deliberate: a login can only be *applied* to accounts on
# the SAME api_key (a Kite access_token is bound to the app that minted it — see
# _linked). But *clearing* a token is always safe, so a logout targets everything
# for that Kite USER regardless of which app each account uses. That's what makes
# "log off Ranjeev" flush both his Stocks and his F&O even when the two accounts
# sit on different (free vs paid) apps.
def disconnect(source: str, acc: dict) -> list[dict]:
    """Log off ``acc`` and every account (stock + F&O) belonging to the same Kite
    user, so both tabs flip back to "not connected". Returns what was cleared."""
    uid = (acc.get("kite_user_id") or "").strip()
    targets: list[tuple[str, dict]] = []
    if uid:
        targets += [("fno", a) for a in fno_store.list_accounts()
                    if (a.get("kite_user_id") or "").strip() == uid]
        targets += [("stock", a) for a in eq_store.list_accounts()
                    if (a.get("kite_user_id") or "").strip() == uid]
    # Always include the clicked row (covers a never-connected slot with no uid).
    init_kind = source if source in ("fno", "stock") else "fno"
    if not any(k == init_kind and a.get("id") == acc.get("id") for k, a in targets):
        targets.append((init_kind, acc))

    cleared: list[dict] = []
    for kind, a in targets:
        st = fno_store if kind == "fno" else eq_store
        st.update_account(a["id"], {"access_token": "", "status": "expired",
                                    "token_updated_at": st._now()})
        cleared.append({"source": kind, "id": a["id"], "label": a.get("account_label")})
    # If this user drove the shared paid app, drop the app-wide live session too.
    _clear_shared_app((_creds_for(source, acc)[0] or "").strip())
    return cleared


def _clear_shared_app(api_key: str) -> None:
    """When the paid env app logs off, drop the app-wide Kite session too (live
    feed / option chain). It's restored on the next login."""
    env_key = (fno_store.env_app_creds()[0] or "").strip()
    if env_key and api_key == env_key:
        try:
            state.write_env(KITE_ACCESS_TOKEN="")
            state.set_kite(None)
        except Exception:
            pass


def _sync_fno(acc_id: str) -> None:
    try:
        acc = fno_store.get_account(acc_id)
        k = fno_kite.client(acc)
        fno_store.upsert_trades(fno_kite.fetch_trades(k, acc_id))
        fno_store.update_account(acc_id, {"last_synced": fno_store._now()})
    except Exception:
        pass


def _sync_stock(acc_id: str) -> None:
    try:
        acc = eq_store.get_account(acc_id)
        if (acc.get("broker") or "kite").lower() != "kite":
            return                                  # non-Kite brokers sync their own way
        holds = eq_kite.fetch_holdings(acc["api_key"], acc.get("access_token"))
        eq_store.replace_holdings(acc, holds)
        eq_store.update_account(acc_id, {"last_synced": eq_store._now()})
        from api.portfolio import engine as eq_engine
        eq_engine.invalidate()
    except Exception:
        pass


def _refresh_shared_app(api_key: str, session: dict) -> None:
    """When the paid env app just logged in, refresh the app-wide Kite session
    that powers the option chain, ticker & live price feed."""
    env_key = (fno_store.env_app_creds()[0] or "").strip()
    if env_key and api_key == env_key:
        try:
            state.write_env(KITE_ACCESS_TOKEN=session["access_token"])
            state.load_kite()
        except Exception:
            pass
