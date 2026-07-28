"""
F&O data-health — the "things to fix" feed behind the dashboard bell.

Unlike the money reminders (bond/FD/loan payouts, which live on the dashboard
money-calendar), this is about *data completeness*: which Zerodha (Kite) sessions
are expired so live data can't be pulled, and which accounts' tradebooks are not
caught up to the last trading day. Each issue can be acted on (jump to F&O),
marked done, or ignored — dismissals persist in the durable KV (app_cache).

Notes on the account model (verified against the live store):
  • 3 accounts — Ranjeev, Sanjeev, Maha — each a Zerodha login.
  • Ranjeev's account is the price_feed: its api_key is the *master* Kite Connect
    app that serves live F&O prices to every account. So when Ranjeev's token is
    expired we raise TWO issues — his trading account AND the master price feed —
    because one login fixes two different broken things.
"""
from __future__ import annotations

from datetime import datetime, date, timedelta
from typing import Optional

from . import store
from ..portfolio import store as kv

IST = store.IST
_DISMISS_KEY = "fno_health_dismiss"


# ── dismissals (mark-done / ignore) ────────────────────────────────────────────
def _get_dismissals() -> dict:
    try:
        rec = kv.cache_get(_DISMISS_KEY)
        val = rec.get("value") if rec else None
        return val if isinstance(val, dict) else {}
    except Exception:
        return {}


def _save_dismissals(d: dict) -> None:
    try:
        kv.cache_set(_DISMISS_KEY, d)
    except Exception:
        pass


def dismiss(key: str, token: str, action: str = "done") -> None:
    """Hide an issue. 'done' hides it only while its state is unchanged (a new
    stale date / a fresh day re-surfaces it); 'ignore' hides it until restored."""
    d = _get_dismissals()
    d[key] = {"token": "*" if action == "ignore" else (token or ""), "action": action,
              "at": datetime.now(IST).isoformat()}
    _save_dismissals(d)


def restore(key: str) -> None:
    d = _get_dismissals()
    if key in d:
        d.pop(key, None)
        _save_dismissals(d)


def _suppressed(dismissals: dict, key: str, token: str) -> Optional[dict]:
    rec = dismissals.get(key)
    if not rec:
        return None
    if rec.get("token") == "*" or rec.get("token") == (token or ""):
        return rec
    return None


# ── trading-day maths (weekend-aware; holidays are close enough to ignore) ──────
def _behind_trading_days(latest_iso: Optional[str], today: date) -> Optional[int]:
    """How many completed weekday sessions sit between an account's newest data
    and today (today excluded — its session may still be open/unsynced). None if
    there's no data at all."""
    if not latest_iso:
        return None
    try:
        y, m, d = (int(x) for x in latest_iso[:10].split("-"))
        last = date(y, m, d)
    except Exception:
        return None
    n, cur = 0, last + timedelta(days=1)
    while cur < today:
        if cur.weekday() < 5:
            n += 1
        cur += timedelta(days=1)
    return n


def _fmt_date(iso: Optional[str]) -> str:
    if not iso:
        return "—"
    try:
        y, m, d = (int(x) for x in iso[:10].split("-"))
        return date(y, m, d).strftime("%-d %b")
    except Exception:
        return iso[:10]


# ── the feed ────────────────────────────────────────────────────────────────────
def build_health(today: Optional[date] = None) -> dict:
    today = today or datetime.now(IST).date()
    today_tok = today.isoformat()
    accts = store.list_accounts()
    try:
        feed_id = store.get_price_feed_id()
    except Exception:
        feed_id = None

    # newest trade date per account (imported or live-synced alike)
    latest: dict[str, str] = {}
    for t in store.all_trades():
        aid, d = t.get("account_id"), (t.get("trade_date") or "")[:10]
        if aid and d and d > latest.get(aid, ""):
            latest[aid] = d

    raw: list[dict] = []
    for a in accts:
        aid = a.get("id")
        person = a.get("person") or a.get("account_label") or "Zerodha"
        connected = bool(a.get("access_token")) and (a.get("status") or "") == "connected"
        is_feed = aid == feed_id
        ld = latest.get(aid)
        behind = _behind_trading_days(ld, today)

        if not connected:
            behind_note = (f" · data {behind} trading day(s) behind (last {_fmt_date(ld)})"
                           if behind else (" · no F&O data yet" if ld is None else ""))
            raw.append({
                "key": f"relogin:{aid}", "kind": "relogin", "severity": "high",
                "account_id": aid, "person": person,
                "title": f"Re-login {person}'s Zerodha",
                "detail": f"Session expired — P&L & live prices can't refresh until you re-login{behind_note}",
                "state_token": today_tok, "fix_label": "Re-login",
            })
            if is_feed:
                # the master price-feed app is down too → Ranjeev shows twice
                raw.append({
                    "key": f"master:{aid}", "kind": "master", "severity": "high",
                    "account_id": aid, "person": person,
                    "title": "Re-login the master API key",
                    "detail": f"{person}'s app is the shared price feed — every account shows stale prices until it's back",
                    "state_token": today_tok, "fix_label": "Re-login",
                })
            continue

        # connected but data hasn't caught up → did you trade? no api key? re-import?
        if ld is None:
            raw.append({
                "key": f"nodata:{aid}", "kind": "nodata", "severity": "medium",
                "account_id": aid, "person": person,
                "title": f"{person}: no F&O data yet",
                "detail": "Import a tradebook or P&L statement so this account's F&O shows up",
                "state_token": "none", "fix_label": "Import",
            })
        elif behind and behind >= 1:
            raw.append({
                "key": f"stale:{aid}", "kind": "stale", "severity": "medium",
                "account_id": aid, "person": person,
                "title": f"{person}'s F&O is {behind} trading day(s) behind",
                "detail": f"Last data {_fmt_date(ld)}. If you traded since, re-sync / import the tradebook — else ignore it",
                "state_token": ld, "fix_label": "Update",
            })

    # high (relogins) first; keep each person's items together, account before
    # its master-key item; medium (stale/nodata) after.
    sev = {"high": 0, "medium": 1}
    kind = {"relogin": 0, "master": 1, "stale": 2, "nodata": 3}
    raw.sort(key=lambda i: (sev.get(i["severity"], 9), i["person"], kind.get(i["kind"], 9)))

    dismissals = _get_dismissals()
    active, ignored = [], []
    for i in raw:
        rec = _suppressed(dismissals, i["key"], i["state_token"])
        if rec:
            ignored.append({**i, "dismissed_as": rec.get("action")})
        else:
            active.append(i)

    return {
        "today": today_tok,
        "issues": active,
        "count": len(active),
        "high_count": sum(1 for i in active if i["severity"] == "high"),
        "ignored": ignored,
        "ignored_count": len(ignored),
        "ok": len(active) == 0,
    }
