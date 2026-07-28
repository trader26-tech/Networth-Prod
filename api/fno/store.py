"""
F&O tab storage — Supabase-primary with a local JSON fallback (same pattern as
api/portfolio/store.py).

  fno_accounts       connected Zerodha accounts (paid Kite Connect app). Blank
                     api_key/api_secret on a row → env KITE_API_KEY/SECRET.
  fno_login_log      audit trail of every Kite login event.
  fno_trades         raw fills (live kite.trades() sync + Console tradebook import).
  fno_daily_pnl      one row per account × day × strategy — powers the calendar.
  fno_pnl_snapshots  1-minute day-P&L snapshots — powers the intraday chart.
"""
from __future__ import annotations

import os
import json
import uuid
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

ACCOUNTS_TABLE = "fno_accounts"
LOG_TABLE = "fno_login_log"
TRADES_TABLE = "fno_trades"
DAILY_TABLE = "fno_daily_pnl"
SNAPSHOTS_TABLE = "fno_pnl_snapshots"

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fno_data")
_FILES = {
    ACCOUNTS_TABLE: os.path.join(_DATA_DIR, "accounts.json"),
    LOG_TABLE: os.path.join(_DATA_DIR, "login_log.json"),
    TRADES_TABLE: os.path.join(_DATA_DIR, "trades.json"),
    DAILY_TABLE: os.path.join(_DATA_DIR, "daily_pnl.json"),
    SNAPSHOTS_TABLE: os.path.join(_DATA_DIR, "snapshots.json"),
}

_ACCOUNT_FIELDS = ("person", "account_label", "api_key", "api_secret", "access_token",
                   "kite_user_id", "user_name", "status", "token_updated_at",
                   "last_synced", "note", "strategy")
_SECRET_FIELDS = ("api_secret", "access_token")

_client = None
_init_attempted = False
_tables_ok = False

MIGRATION_HINT = (
    "F&O tables not found in Supabase. Run the migration in SUPABASE.md "
    "(\"F&O tab\") once in the Supabase SQL editor to create fno_accounts, "
    "fno_login_log, fno_trades, fno_daily_pnl and fno_pnl_snapshots, then retry."
)


def _read_env(key: str) -> str:
    val = os.environ.get(key, "").strip()
    if val:
        return val
    try:
        from dotenv import dotenv_values
        root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        env_path = os.path.join(root, ".env")
        if os.path.isfile(env_path):
            return (dotenv_values(env_path).get(key) or "").strip()
    except Exception:
        pass
    return ""


def _get_client():
    global _client, _init_attempted
    if _client is not None or _init_attempted:
        return _client
    _init_attempted = True
    url = _read_env("SUPABASE_URL").rstrip("/")
    key = _read_env("SUPABASE_SERVICE_KEY") or _read_env("SUPABASE_KEY")
    if not url or not key:
        return None
    try:
        from supabase import create_client
        _client = create_client(url, key)
        print(f"✓ F&O store: Supabase active ({url})")
        return _client
    except Exception as e:
        print(f"⚠  F&O store: Supabase init failed (using JSON): {e}")
        return None


def tables_ready() -> bool:
    global _tables_ok
    if _tables_ok:
        return True
    client = _get_client()
    if not client:
        return True
    try:
        for t in (ACCOUNTS_TABLE, LOG_TABLE, TRADES_TABLE, DAILY_TABLE, SNAPSHOTS_TABLE):
            client.table(t).select("id").limit(1).execute()
        _tables_ok = True
        return True
    except Exception:
        return False


# ── JSON-file fallback helpers ──────────────────────────────────────────────────
def _read(table: str) -> list:
    try:
        with open(_FILES[table], "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _write(table: str, data: list) -> None:
    os.makedirs(_DATA_DIR, exist_ok=True)
    path = _FILES[table]
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, default=str)
    os.replace(tmp, path)


def _now() -> str:
    return datetime.now(IST).isoformat()


def today_ist() -> str:
    """The current TRADING day (9AM-anchored): rolls over at 03:00 IST, not
    midnight, so a late-night US-crude / MCX session (00:00–03:00 IST) still
    counts as the day that opened at 09:00 the previous morning."""
    now = datetime.now(IST)
    d = now.date() if now.hour >= 3 else now.date() - timedelta(days=1)
    return d.isoformat()


def _fetch_all(client, table: str, **eqs) -> list[dict]:
    """Page past Supabase's 1000-row cap."""
    out: list[dict] = []
    start, page = 0, 1000
    while True:
        q = client.table(table).select("*")
        for k, v in eqs.items():
            q = q.eq(k, v)
        rows = q.range(start, start + page - 1).execute().data or []
        out.extend(rows)
        if len(rows) < page:
            return out
        start += page


# ── Accounts ────────────────────────────────────────────────────────────────────
def env_app_creds() -> tuple[str, str]:
    """The paid Kite Connect app in .env — the default for every account."""
    return _read_env("KITE_API_KEY"), _read_env("KITE_API_SECRET")


def account_creds(acc: dict) -> tuple[str, str]:
    """Effective (api_key, api_secret) for an account — own creds or env app."""
    key = (acc.get("api_key") or "").strip()
    sec = (acc.get("api_secret") or "").strip()
    if key and sec:
        return key, sec
    return env_app_creds()


def public_account(a: dict) -> dict:
    out = {k: a.get(k) for k in ("id", "person", "account_label", "kite_user_id",
                                 "user_name", "status", "token_updated_at",
                                 "last_synced", "note", "created_at")}
    out["price_feed"] = bool(a.get("price_feed"))
    sv = (a.get("strategy") or "").strip().lower()
    out["strategy"] = sv or (None if "strategy" in a else get_account_strategy(a.get("id")))
    own_key = (a.get("api_key") or "").strip()
    env_key = (env_app_creds()[0] or "").strip()
    key = own_key or env_key
    out["api_key_hint"] = (key[:4] + "…") if key else None
    out["uses_env_app"] = not own_key
    # "Paid" = this account's EFFECTIVE key is the shared paid app key — whether it
    # borrows the env app (no own key) OR stores that same paid key as its own key.
    # (uses_env_app alone was wrong: Ranjeev stores the paid key on the row itself.)
    out["is_paid_app"] = bool(key and env_key and key == env_key)
    out["connected"] = bool(a.get("access_token")) and a.get("status") == "connected"
    return out


def list_accounts() -> list[dict]:
    client = _get_client()
    if client:
        try:
            return sorted(_fetch_all(client, ACCOUNTS_TABLE), key=lambda a: a.get("created_at") or "")
        except Exception:
            return []
    return _read(ACCOUNTS_TABLE)


def get_account(acc_id: str) -> Optional[dict]:
    client = _get_client()
    if client:
        rows = client.table(ACCOUNTS_TABLE).select("*").eq("id", acc_id).limit(1).execute().data or []
        return rows[0] if rows else None
    return next((a for a in _read(ACCOUNTS_TABLE) if a.get("id") == acc_id), None)


def add_account(payload: dict) -> dict:
    row = {k: payload.get(k) for k in _ACCOUNT_FIELDS if k in payload}
    row["id"] = uuid.uuid4().hex[:12]
    row["status"] = row.get("status") or "pending"
    row["created_at"] = _now()
    client = _get_client()
    if client:
        client.table(ACCOUNTS_TABLE).insert(row).execute()
    else:
        data = _read(ACCOUNTS_TABLE)
        data.append(row)
        _write(ACCOUNTS_TABLE, data)
    return row


def update_account(acc_id: str, patch: dict) -> Optional[dict]:
    patch = {k: v for k, v in patch.items() if k in _ACCOUNT_FIELDS}
    client = _get_client()
    if client:
        rows = client.table(ACCOUNTS_TABLE).update(patch).eq("id", acc_id).execute().data or []
        return rows[0] if rows else None
    data = _read(ACCOUNTS_TABLE)
    hit = None
    for a in data:
        if a.get("id") == acc_id:
            a.update(patch)
            hit = a
    if hit:
        _write(ACCOUNTS_TABLE, data)
    return hit


_PRICE_FEED_KEY = "fno_price_feed"    # KV fallback used until the price_feed column exists
_legacy_feed_promoted = False


def get_price_feed_id() -> Optional[str]:
    """Which account is the live-price feed (the paid Kite Connect app that has
    market-data access). Free accounts read LTPs through this one.

    Persisted as the price_feed=true row on fno_accounts so it lives in the
    database with the account it belongs to. Until that column exists (migration
    in SUPABASE.md), it degrades to the KV store and auto-promotes to the column
    once available — so the feature never breaks during the transition."""
    for a in list_accounts():
        if a.get("price_feed"):
            return a.get("id")
    # No column-flagged feed — consult the KV fallback (pre-migration selection).
    kv_id = _kv_feed_id()
    if kv_id:
        global _legacy_feed_promoted
        if not _legacy_feed_promoted:
            _legacy_feed_promoted = True
            if _set_feed_column(kv_id):        # column now exists → promote & retire the blob
                _kv_set(None)
    return kv_id


def set_price_feed_id(account_id: Optional[str]) -> None:
    """Designate exactly one account as the live-price feed (or None to clear).
    Enforces the single-feed invariant, and stays working whether or not the
    price_feed column has been added yet."""
    if _set_feed_column(account_id):
        _kv_set(None)                          # DB column is the single source of truth
    else:
        _kv_set(account_id)                    # column missing yet → keep it in the KV store


def _set_feed_column(account_id: Optional[str]) -> bool:
    """Write the single-feed flag to the fno_accounts.price_feed column. Returns
    False if the column isn't there yet (pre-migration) so callers can fall back."""
    client = _get_client()
    if client:
        try:
            client.table(ACCOUNTS_TABLE).update({"price_feed": False}).eq("price_feed", True).execute()
            if account_id:
                client.table(ACCOUNTS_TABLE).update({"price_feed": True}).eq("id", account_id).execute()
            return True
        except Exception:
            return False                       # column missing / transient error
    data = _read(ACCOUNTS_TABLE)
    changed = False
    for a in data:
        want = bool(account_id) and a.get("id") == account_id
        if bool(a.get("price_feed")) != want:
            a["price_feed"] = want
            changed = True
    if changed:
        _write(ACCOUNTS_TABLE, data)
    return True


def _kv_feed_id() -> Optional[str]:
    try:
        from ..portfolio import store as kv
        rec = kv.cache_get(_PRICE_FEED_KEY)
        if rec and isinstance(rec.get("value"), dict):
            return rec["value"].get("account_id") or None
    except Exception:
        pass
    return None


def _kv_set(account_id: Optional[str]) -> None:
    try:
        from ..portfolio import store as kv
        kv.cache_set(_PRICE_FEED_KEY, {"account_id": account_id or None})
    except Exception:
        pass


# ── Per-account strategy rules ────────────────────────────────────────────────
# An account can pin all its (non-crude) trades to a strategy name — e.g. every
# fill from Ranjeev's account → "sentinel", from Ram's → "ram". Crude oil (MCX
# CRUDEOIL*) is ALWAYS kept as its own "crude" strategy, whoever traded it.
#
# The label is stored as the fno_accounts.strategy COLUMN — it lives in the DB
# with the account it belongs to. This is the single source of truth: the live
# engine and the daily rebuild only READ it, they never compute or change it. A
# per-account DB column (not a shared {id: label} blob) also can't be raced back
# to empty by a concurrent read-modify-write. Until the column exists (migration
# in SUPABASE.md) it degrades to the legacy KV blob and auto-promotes into the
# column once available — so the feature never breaks during the transition.
_ACCT_STRAT_KEY = "fno_account_strategy"    # legacy KV blob (pre-migration fallback)
_legacy_strat_promoted = False


def _kv_account_strategies() -> dict:
    try:
        from ..portfolio import store as kv
        rec = kv.cache_get(_ACCT_STRAT_KEY)
        if rec and isinstance(rec.get("value"), dict):
            return {k: v for k, v in rec["value"].items() if v}
    except Exception:
        pass
    return {}


def get_account_strategies() -> dict:
    """{account_id: strategy_label} read from the fno_accounts.strategy column
    (falls back to the legacy KV blob until the column is migrated)."""
    accts = list_accounts()
    if any("strategy" in a for a in accts):          # column present on the rows
        out = {a.get("id"): (a.get("strategy") or "").strip().lower()
               for a in accts if (a.get("strategy") or "").strip()}
        # one-time promotion of any legacy KV pins into the new column
        global _legacy_strat_promoted
        if not _legacy_strat_promoted:
            _legacy_strat_promoted = True
            for aid, lbl in _kv_account_strategies().items():
                if aid not in out and _set_strategy_column(aid, lbl):
                    out[aid] = lbl
            _kv_set_strategies({})                   # column is now the source of truth
        return out
    return _kv_account_strategies()                  # pre-migration fallback


def get_account_strategy(acc_id: str) -> Optional[str]:
    return get_account_strategies().get(acc_id)


def set_account_strategy(acc_id: str, label: Optional[str]) -> None:
    """Pin (or clear) an account's strategy. Writes the fno_accounts.strategy
    column; if the column isn't there yet, keeps it in the legacy KV blob."""
    label = (label or "").strip().lower() or None
    if not _set_strategy_column(acc_id, label):
        m = _kv_account_strategies()
        if label:
            m[acc_id] = label
        else:
            m.pop(acc_id, None)
        _kv_set_strategies(m)


def _set_strategy_column(acc_id: str, label: Optional[str]) -> bool:
    """Write the strategy label to fno_accounts.strategy. Returns False if the
    column isn't there yet (pre-migration) so callers can fall back to the KV."""
    client = _get_client()
    if client:
        try:
            client.table(ACCOUNTS_TABLE).update({"strategy": label}).eq("id", acc_id).execute()
            return True
        except Exception:
            return False
    data = _read(ACCOUNTS_TABLE)
    hit = False
    for a in data:
        if a.get("id") == acc_id:
            a["strategy"] = label
            hit = True
    if hit:
        _write(ACCOUNTS_TABLE, data)
    return hit


def _kv_set_strategies(m: dict) -> None:
    try:
        from ..portfolio import store as kv
        kv.cache_set(_ACCT_STRAT_KEY, m)
    except Exception:
        pass


# ── Strategy catalog (the list of strategies you can tag trades with) ─────────
# Three built-ins are always present; the user can add more (name + colour),
# stored in the KV cache so they survive restarts and appear on every device.
_STRAT_CATALOG_KEY = "fno_strategy_catalog"
DEFAULT_STRATEGIES = [
    {"key": "sentinel", "label": "Sentinel",   "color": "#387ed1"},
    {"key": "crude",    "label": "Crude Oil",  "color": "#d97706"},
    {"key": "other",    "label": "Other F&O",  "color": "#9097b4"},
]
_DEFAULT_STRAT_KEYS = {s["key"] for s in DEFAULT_STRATEGIES}


def _get_custom_strategies() -> list:
    try:
        from ..portfolio import store as kv
        rec = kv.cache_get(_STRAT_CATALOG_KEY)
        if rec and isinstance(rec.get("value"), list):
            return [s for s in rec["value"] if isinstance(s, dict) and s.get("key")]
    except Exception:
        pass
    return []


def _save_custom_strategies(items: list) -> None:
    try:
        from ..portfolio import store as kv
        kv.cache_set(_STRAT_CATALOG_KEY, items)
    except Exception:
        pass


def get_strategy_catalog() -> list:
    """Built-ins first, then user-added strategies (deduped by key)."""
    out = [dict(s) for s in DEFAULT_STRATEGIES]
    seen = set(_DEFAULT_STRAT_KEYS)
    for s in _get_custom_strategies():
        k = (s.get("key") or "").strip().lower()
        if k and k not in seen:
            seen.add(k)
            out.append({"key": k, "label": (s.get("label") or k).strip(), "color": (s.get("color") or "#6b7190")})
    return out


def add_strategy(key: str, label: str, color: str) -> list:
    key = (key or "").strip().lower()
    if not key or key in _DEFAULT_STRAT_KEYS:
        return get_strategy_catalog()                 # ignore blank / reserved names
    custom = [s for s in _get_custom_strategies() if (s.get("key") or "").strip().lower() != key]
    custom.append({"key": key, "label": (label or key).strip(), "color": (color or "#6b7190")})
    _save_custom_strategies(custom)
    return get_strategy_catalog()


def remove_strategy(key: str) -> list:
    key = (key or "").strip().lower()
    custom = [s for s in _get_custom_strategies() if (s.get("key") or "").strip().lower() != key]
    _save_custom_strategies(custom)
    return get_strategy_catalog()


def strat_with_override(base: str, account_id: str, overrides: Optional[dict] = None) -> str:
    """Apply an account's strategy pin. Crude is always crude; everything else on a
    pinned account becomes that account's label. Unpinned accounts keep `base`."""
    if base == "crude":
        return "crude"
    ov = overrides if overrides is not None else get_account_strategies()
    return ov.get(account_id) or base


# ── Per-LEG strategy overrides (open positions only) ──────────────────────────
# The account pin is the default; from the Open Positions table the user can
# retag one specific open leg to any strategy. That per-leg pin wins, and it is
# applied ONLY to open/live legs (openpos + engine) — the closed daily history is
# never touched. Keyed "<account_id>|<tradingsymbol>", stored in the KV cache.
_LEG_STRAT_KEY = "fno_leg_strategy"


def _leg_key(account_id: str, tradingsymbol: str) -> str:
    return f"{account_id}|{tradingsymbol}"


def get_leg_strategies() -> dict:
    try:
        from ..portfolio import store as kv
        rec = kv.cache_get(_LEG_STRAT_KEY)
        if rec and isinstance(rec.get("value"), dict):
            return {k: v for k, v in rec["value"].items() if v}
    except Exception:
        pass
    return {}


def set_leg_strategy(account_id: str, tradingsymbol: str, label: Optional[str]) -> None:
    m = get_leg_strategies()
    label = (label or "").strip().lower() or None
    k = _leg_key(account_id, tradingsymbol)
    if label:
        m[k] = label
    else:
        m.pop(k, None)
    try:
        from ..portfolio import store as kv
        kv.cache_set(_LEG_STRAT_KEY, m)
    except Exception:
        pass


def resolve_leg_strategy(base: str, account_id: str, tradingsymbol: str,
                         account_pins: Optional[dict] = None,
                         leg_pins: Optional[dict] = None) -> str:
    """Strategy for one open/live leg: an explicit per-leg pin wins outright;
    otherwise fall back to the account pin (crude always crude) / instrument base."""
    lp = leg_pins if leg_pins is not None else get_leg_strategies()
    pinned = lp.get(_leg_key(account_id, tradingsymbol))
    if pinned:
        return pinned
    return strat_with_override(base, account_id, account_pins)


# ── Per-TRADE strategy overrides (Trade History) ──────────────────────────────
# The account pin defaults every non-crude trade to that account's strategy — so
# without this, retagging a single trade never "stuck" (the pin re-won at read
# time). A per-trade pin (set from the Trade-History dropdown or bulk-assign) now
# wins outright, exactly like the per-leg pin does for open positions. Keyed by
# trade id, stored in the KV cache; applied at display AND in the daily rebuild.
_TRADE_STRAT_KEY = "fno_trade_strategy"


def get_trade_strategies() -> dict:
    """{trade_id: strategy_label} for trades the user retagged explicitly."""
    try:
        from ..portfolio import store as kv
        rec = kv.cache_get(_TRADE_STRAT_KEY)
        if rec and isinstance(rec.get("value"), dict):
            return {k: v for k, v in rec["value"].items() if v}
    except Exception:
        pass
    return {}


def set_trade_strategies(ids: list, label: Optional[str]) -> None:
    """Pin (or clear, when label is falsy) the strategy for one or more trades."""
    m = get_trade_strategies()
    label = (label or "").strip().lower() or None
    for tid in (ids or []):
        if not tid:
            continue
        if label:
            m[tid] = label
        else:
            m.pop(tid, None)
    try:
        from ..portfolio import store as kv
        kv.cache_set(_TRADE_STRAT_KEY, m)
    except Exception:
        pass


def resolve_trade_strategy(trade: dict, account_pins: Optional[dict] = None,
                           trade_pins: Optional[dict] = None) -> str:
    """Final strategy for one trade: an explicit per-trade pin wins outright;
    otherwise fall back to the account pin (crude always crude) / stored value."""
    tp = trade_pins if trade_pins is not None else get_trade_strategies()
    pinned = tp.get(trade.get("id"))
    if pinned:
        return pinned
    return strat_with_override(trade.get("strategy") or "other", trade.get("account_id"), account_pins)


# ── Intraday open-positions P&L series (carry-forward marked-to-market) ────────
# The engine appends a per-minute point of every account's open-positions
# unrealized P&L during market hours, so the Today chart can graph the live open
# book even for accounts that aren't individually logged in (priced via the paid
# feed). Kept for the current day only (auto-resets when the date rolls). KV-only.
_OPEN_SERIES_KEY = "fno_open_series"


def append_open_series(date_str: str, ts_min: str, by_account: dict) -> None:
    try:
        from ..portfolio import store as kv
        rec = kv.cache_get(_OPEN_SERIES_KEY)
        blob = rec.get("value") if rec and isinstance(rec.get("value"), dict) else {}
        if blob.get("date") != date_str:
            blob = {"date": date_str, "pts": []}
        pts = blob.get("pts") or []
        if pts and pts[-1].get("t") == ts_min:
            pts[-1]["a"] = by_account            # same minute → replace
        else:
            pts.append({"t": ts_min, "a": by_account})
        blob["pts"] = pts[-600:]                  # ~a full session, capped
        blob["date"] = date_str
        kv.cache_set(_OPEN_SERIES_KEY, blob)
    except Exception:
        pass


def get_open_series(date_str: str) -> list:
    try:
        from ..portfolio import store as kv
        rec = kv.cache_get(_OPEN_SERIES_KEY)
        blob = rec.get("value") if rec and isinstance(rec.get("value"), dict) else {}
        if blob.get("date") == date_str:
            return blob.get("pts") or []
    except Exception:
        pass
    return []


def delete_account(acc_id: str) -> int:
    client = _get_client()
    if client:
        for t in (TRADES_TABLE, DAILY_TABLE, SNAPSHOTS_TABLE, LOG_TABLE):
            try:
                client.table(t).delete().eq("account_id", acc_id).execute()
            except Exception:
                pass
        res = client.table(ACCOUNTS_TABLE).delete().eq("id", acc_id).execute()
        return len(res.data or [])
    accts = _read(ACCOUNTS_TABLE)
    keep = [a for a in accts if a.get("id") != acc_id]
    n = len(accts) - len(keep)
    _write(ACCOUNTS_TABLE, keep)
    for t in (TRADES_TABLE, DAILY_TABLE, SNAPSHOTS_TABLE, LOG_TABLE):
        _write(t, [r for r in _read(t) if r.get("account_id") != acc_id])
    return n


# ── Login log ───────────────────────────────────────────────────────────────────
def add_log(account_id: Optional[str], event: str, detail: str = "") -> None:
    """Best-effort audit entry — never raises."""
    row = {"id": uuid.uuid4().hex[:12], "account_id": account_id,
           "event": event, "detail": (detail or "")[:500], "created_at": _now()}
    try:
        client = _get_client()
        if client:
            client.table(LOG_TABLE).insert(row).execute()
        else:
            data = _read(LOG_TABLE)
            data.append(row)
            _write(LOG_TABLE, data[-1000:])          # cap the local file
    except Exception:
        pass


def list_logs(limit: int = 100) -> list[dict]:
    client = _get_client()
    if client:
        try:
            return (client.table(LOG_TABLE).select("*")
                    .order("created_at", desc=True).limit(limit).execute().data or [])
        except Exception:
            return []
    rows = _read(LOG_TABLE)
    return sorted(rows, key=lambda r: r.get("created_at") or "", reverse=True)[:limit]


# ── Trades ──────────────────────────────────────────────────────────────────────
def upsert_trades(rows: list[dict]) -> int:
    """Insert fills, skipping ones already stored (unique account_id+trade_id).
    Returns how many were newly added."""
    if not rows:
        return 0
    client = _get_client()
    if client:
        existing = set()
        acc_ids = {r["account_id"] for r in rows}
        for acc_id in acc_ids:
            for t in _fetch_all(client, TRADES_TABLE, account_id=acc_id):
                existing.add((t.get("account_id"), str(t.get("trade_id"))))
        fresh = [r for r in rows if (r["account_id"], str(r["trade_id"])) not in existing]
        for r in fresh:
            r.setdefault("id", uuid.uuid4().hex[:12])
        if fresh:
            client.table(TRADES_TABLE).insert(fresh).execute()
        return len(fresh)
    data = _read(TRADES_TABLE)
    existing = {(t.get("account_id"), str(t.get("trade_id"))) for t in data}
    fresh = [r for r in rows if (r["account_id"], str(r["trade_id"])) not in existing]
    for r in fresh:
        r.setdefault("id", uuid.uuid4().hex[:12])
    if fresh:
        data.extend(fresh)
        _write(TRADES_TABLE, data)
    return len(fresh)


def list_trades(date: Optional[str] = None, strategy: Optional[str] = None,
                account_id: Optional[str] = None) -> list[dict]:
    client = _get_client()
    if client:
        try:
            # Page past PostgREST's 1000-row cap — a plain .limit() is silently
            # clamped to 1000, which (ascending) drops the NEWEST fills, so
            # today's trades vanish from history while the P&L rebuild (which
            # uses the paginated all_trades) still books them. Fetch them all.
            eqs = {}
            if date:
                eqs["trade_date"] = date
            if strategy:
                eqs["strategy"] = strategy
            if account_id:
                eqs["account_id"] = account_id
            rows = _fetch_all(client, TRADES_TABLE, **eqs)
            return sorted(rows, key=lambda r: r.get("fill_ts") or "")
        except Exception:
            return []
    rows = _read(TRADES_TABLE)
    if date:
        rows = [r for r in rows if (r.get("trade_date") or "")[:10] == date]
    if strategy:
        rows = [r for r in rows if r.get("strategy") == strategy]
    if account_id:
        rows = [r for r in rows if r.get("account_id") == account_id]
    return sorted(rows, key=lambda r: r.get("fill_ts") or "")


def all_trades() -> list[dict]:
    client = _get_client()
    if client:
        try:
            return _fetch_all(client, TRADES_TABLE)
        except Exception:
            return []
    return _read(TRADES_TABLE)


def _is_imported(src: str) -> bool:
    """Imported (tradebook) fills, vs 'kite' live-synced ones. Legacy imports use
    source='import'; multi-tradebook imports use 'tb:<id>'."""
    s = (src or "").strip()
    return s == "import" or s.startswith("tb:")


def import_stats() -> dict:
    """{account_id: count of imported (tradebook) fills}."""
    out: dict[str, int] = {}
    for t in all_trades():
        if _is_imported(t.get("source")):
            aid = t.get("account_id")
            out[aid] = out.get(aid, 0) + 1
    return out


def delete_import_trades(account_id: str) -> int:
    """Remove ALL of an account's tradebook fills (any 'import' / 'tb:*' source).
    Kite-synced fills are left untouched. Returns how many were removed."""
    data = [t for t in all_trades() if t.get("account_id") == account_id and _is_imported(t.get("source"))]
    client = _get_client()
    if client:
        # delete by source value(s) present (Supabase has no startswith on delete)
        srcs = {t.get("source") for t in data}
        for s in srcs:
            try:
                client.table(TRADES_TABLE).delete().eq("account_id", account_id).eq("source", s).execute()
            except Exception:
                pass
        return len(data)
    allrows = _read(TRADES_TABLE)
    keep = [t for t in allrows if not (t.get("account_id") == account_id and _is_imported(t.get("source")))]
    n = len(allrows) - len(keep)
    _write(TRADES_TABLE, keep)
    return n


def delete_trades_by_source(account_id: str, source: str) -> int:
    """Remove one tradebook's fills (exact source, e.g. 'tb:<id>')."""
    client = _get_client()
    if client:
        res = (client.table(TRADES_TABLE).delete()
               .eq("account_id", account_id).eq("source", source).execute())
        return len(res.data or [])
    data = _read(TRADES_TABLE)
    keep = [t for t in data if not (t.get("account_id") == account_id and t.get("source") == source)]
    n = len(data) - len(keep)
    _write(TRADES_TABLE, keep)
    return n


# ── Per-account tradebook records (multiple imports + coverage timeline) ──────
_TRADEBOOKS_KEY = "fno_tradebooks"


def get_tradebooks(account_id: str) -> list:
    try:
        from ..portfolio import store as kv
        rec = kv.cache_get(_TRADEBOOKS_KEY)
        blob = rec.get("value") if rec and isinstance(rec.get("value"), dict) else {}
        return list(blob.get(account_id) or [])
    except Exception:
        return []


def _save_tradebooks(account_id: str, tbs: list) -> None:
    try:
        from ..portfolio import store as kv
        rec = kv.cache_get(_TRADEBOOKS_KEY)
        blob = rec.get("value") if rec and isinstance(rec.get("value"), dict) else {}
        blob[account_id] = tbs
        kv.cache_set(_TRADEBOOKS_KEY, blob)
    except Exception:
        pass


def add_tradebook(account_id: str, tb: dict) -> None:
    tbs = get_tradebooks(account_id)
    tbs.append(tb)
    _save_tradebooks(account_id, tbs)


def remove_tradebook(account_id: str, tb_id: str) -> None:
    _save_tradebooks(account_id, [t for t in get_tradebooks(account_id) if t.get("id") != tb_id])


# ── imported P&L statement (one per account — authoritative booked/realised) ──────
# Zerodha P&L statements — MULTIPLE per account (each covers a date window; you
# download one per period, Console caps the range). Stored as a list per account
# in the app_cache KV (Supabase + file fallback), so they survive restarts. Each
# rec carries the summary + the per-(date,strategy) realised it contributes, so
# the authoritative 'statement' daily rows can be re-derived from all of them.
_STATEMENTS_KEY = "fno_pnl_statements"


def _stmt_blob() -> dict:
    from ..portfolio import store as kv
    rec = kv.cache_get(_STATEMENTS_KEY)
    return rec.get("value") if rec and isinstance(rec.get("value"), dict) else {}


def _save_stmt_blob(blob: dict) -> None:
    from ..portfolio import store as kv
    kv.cache_set(_STATEMENTS_KEY, blob)


def _reconstruct_statement_days(account_id: str, date_from, date_to) -> list:
    """Rebuild a legacy statement's per-(date,strategy) breakdown from the
    source='statement' daily rows it already wrote to the DB — so migrating the
    old single-statement shape doesn't lose its booked on the next rebuild."""
    rows = [r for r in list_daily(date_from, date_to, account_id)
            if r.get("source") == "statement"]
    return [{"date": (r.get("date") or "")[:10], "strategy": r.get("strategy"),
             "realized": float(r.get("realized") if r.get("realized") is not None else r.get("total") or 0),
             "count": int(r.get("trades_count") or 0)} for r in rows if r.get("date")]


def get_pnl_statements(account_id: str) -> list:
    """All statements for an account (oldest window first). Migrates the legacy
    single-dict shape (one statement, no id/days) to the first-class list form,
    persisting once so a later rebuild can re-derive booked from every statement."""
    try:
        v = _stmt_blob().get(account_id)
    except Exception:
        return []
    if v is None:
        return []
    items = [v] if isinstance(v, dict) else list(v)
    migrated = isinstance(v, dict)
    for s in items:
        if not s.get("id"):
            s["id"] = uuid.uuid4().hex[:10]; migrated = True
        if "days" not in s:
            s["days"] = _reconstruct_statement_days(account_id, s.get("date_from"), s.get("date_to"))
            migrated = True
    items.sort(key=lambda s: s.get("date_from") or "")
    if migrated:
        try:
            blob = _stmt_blob(); blob[account_id] = items; _save_stmt_blob(blob)
        except Exception:
            pass
    return items


def _windows_overlap(af, at, bf, bt) -> bool:
    if not (af and at and bf and bt):
        return False
    return af <= bt and bf <= at


def add_pnl_statement(account_id: str, rec: dict) -> list:
    """Add a statement. Any EXISTING statement whose window overlaps the new one is
    dropped first (block/replace) so a period is never counted twice."""
    kept = [s for s in get_pnl_statements(account_id)
            if not _windows_overlap(s.get("date_from"), s.get("date_to"),
                                    rec.get("date_from"), rec.get("date_to"))]
    kept.append(rec)
    kept.sort(key=lambda s: s.get("date_from") or "")
    try:
        blob = _stmt_blob(); blob[account_id] = kept; _save_stmt_blob(blob)
    except Exception:
        pass
    return kept


def remove_pnl_statement(account_id: str, stmt_id: str) -> list:
    kept = [s for s in get_pnl_statements(account_id) if s.get("id") != stmt_id]
    try:
        blob = _stmt_blob(); blob[account_id] = kept; _save_stmt_blob(blob)
    except Exception:
        pass
    return kept


def clear_pnl_statements(account_id: str) -> None:
    try:
        blob = _stmt_blob(); blob.pop(account_id, None); _save_stmt_blob(blob)
    except Exception:
        pass


def get_pnl_statement(account_id: str) -> Optional[dict]:
    """Aggregate summary across ALL of an account's statements (charges/other/
    realised summed, coverage = the union span). Drives the holder card's Charges
    and the 'has a statement' checks. None when no statement is imported."""
    stmts = get_pnl_statements(account_id)
    if not stmts:
        return None
    def _sum(k):
        return round(sum(float(s.get(k) or 0) for s in stmts), 2)
    return {
        "count": len(stmts),
        "charges": _sum("charges"), "other": _sum("other"),
        "realized": _sum("realized"), "unrealized": _sum("unrealized"),
        "date_from": min((s.get("date_from") for s in stmts if s.get("date_from")), default=None),
        "date_to": max((s.get("date_to") for s in stmts if s.get("date_to")), default=None),
    }


def statement_windows(account_id: str) -> list:
    """(date_from, date_to) for each imported statement — the days the statement is
    authoritative for, so the fill-replay rebuild can skip them (no double count)."""
    return [(s.get("date_from"), s.get("date_to")) for s in get_pnl_statements(account_id)
            if s.get("date_from") and s.get("date_to")]


def delete_trades_daily(account_id: str) -> None:
    """Drop the derived daily rows (source='trades') for an account so a rebuild
    starts clean; live rows are preserved."""
    delete_daily_by_source(account_id, "trades")


def has_daily_source(account_id: str, source: str) -> bool:
    """Does this account have any daily rows of a given source (e.g. 'statement')?"""
    client = _get_client()
    if client:
        try:
            return bool((client.table(DAILY_TABLE).select("id").eq("account_id", account_id)
                         .eq("source", source).limit(1).execute().data) or [])
        except Exception:
            return False
    return any(r.get("account_id") == account_id and r.get("source") == source for r in _read(DAILY_TABLE))


def delete_daily_by_source(account_id: str, source: str) -> None:
    """Drop an account's derived daily rows for one source (e.g. 'trades' or
    'statement') so a re-import/rebuild starts clean; other sources are kept."""
    client = _get_client()
    if client:
        try:
            client.table(DAILY_TABLE).delete().eq("account_id", account_id).eq("source", source).execute()
        except Exception:
            pass
        return
    data = _read(DAILY_TABLE)
    _write(DAILY_TABLE, [r for r in data if not (r.get("account_id") == account_id and r.get("source") == source)])


def delete_daily_before(account_id: str, before_date: str) -> None:
    """Delete an account's daily rows for COMPLETED days (date < before_date),
    EXCEPT source='statement' rows. A rebuild from the authoritative fills then
    rewrites those days cleanly — so stale 'live' rows (intraday m2m / unrealized
    with realized=0) can't survive and blank out a past day's booked P&L. Imported
    P&L-statement rows are authoritative and are preserved (an unrelated trade
    sync / re-pin must never wipe them — that was the 'refresh loses my P&L' bug)."""
    client = _get_client()
    if client:
        try:
            (client.table(DAILY_TABLE).delete().eq("account_id", account_id)
             .lt("date", before_date).neq("source", "statement").execute())
        except Exception:
            pass
        return
    data = _read(DAILY_TABLE)
    _write(DAILY_TABLE, [r for r in data if not (
        r.get("account_id") == account_id and (r.get("date") or "")[:10] < before_date
        and r.get("source") != "statement")])


def prune_live_daily(account_id: str, date: str, keep: "set|list") -> None:
    """Delete this account+date's source='live' rows whose strategy is NOT in
    `keep`. The live engine writes one row per (account,date,strategy) each tick;
    when a position's resolved strategy label changes (e.g. the account gets
    pinned to 'ram'), the row under the OLD label would otherwise linger and be
    double-counted. Called right after each live upsert to keep the day clean."""
    keep = set(keep or [])
    client = _get_client()
    if client:
        try:
            rows = (client.table(DAILY_TABLE).select("id,strategy")
                    .eq("account_id", account_id).eq("date", date)
                    .eq("source", "live").execute().data or [])
            stale = [r["id"] for r in rows if r.get("strategy") not in keep]
            for rid in stale:
                client.table(DAILY_TABLE).delete().eq("id", rid).execute()
        except Exception:
            pass
        return
    data = _read(DAILY_TABLE)
    _write(DAILY_TABLE, [r for r in data if not (
        r.get("account_id") == account_id and (r.get("date") or "")[:10] == date
        and r.get("source") == "live" and r.get("strategy") not in keep)])


def update_trade(trade_pk: str, patch: dict) -> Optional[dict]:
    patch = {k: v for k, v in patch.items() if k in ("strategy",)}
    client = _get_client()
    if client:
        rows = client.table(TRADES_TABLE).update(patch).eq("id", trade_pk).execute().data or []
        return rows[0] if rows else None
    data = _read(TRADES_TABLE)
    hit = None
    for t in data:
        if t.get("id") == trade_pk:
            t.update(patch)
            hit = t
    if hit:
        _write(TRADES_TABLE, data)
    return hit


def update_trades_strategy(ids: list, strat: str) -> set:
    """Bulk-set the strategy on many trades in ONE query. Returns the set of
    affected account_ids so the caller can rebuild each account's daily P&L once
    (instead of once per trade — faster, and no partial-rebuild races)."""
    ids = [i for i in (ids or []) if i]
    if not ids:
        return set()
    client = _get_client()
    if client:
        rows = client.table(TRADES_TABLE).update({"strategy": strat}).in_("id", ids).execute().data or []
        return {r.get("account_id") for r in rows if r.get("account_id")}
    data = _read(TRADES_TABLE)
    idset = set(ids); accts = set()
    for t in data:
        if t.get("id") in idset:
            t["strategy"] = strat
            if t.get("account_id"):
                accts.add(t["account_id"])
    _write(TRADES_TABLE, data)
    return accts


# ── Daily P&L ───────────────────────────────────────────────────────────────────
def upsert_daily(account_id: str, date: str, strategy: str, realized: float,
                 unrealized: float, total: float, trades_count: int, source: str) -> None:
    """Write one account×day×strategy row. A 'trades' rebuild won't overwrite
    TODAY's 'live' row (that's the real-time intraday number from Kite positions).
    For a COMPLETED past day the fills are authoritative, so 'trades' DOES replace
    a stale 'live' row — otherwise a day whose live rows only ever held unrealized
    m2m (realized=0) would show empty booked P&L forever."""
    client = _get_client()
    if client:
        try:
            q = (client.table(DAILY_TABLE).select("id,source").eq("account_id", account_id)
                 .eq("date", date).eq("strategy", strategy).limit(1).execute().data or [])
            if q:
                if source == "trades" and q[0].get("source") == "live" and date >= today_ist():
                    return
                if source in ("trades", "live") and q[0].get("source") == "statement" and date < today_ist():
                    return                        # an imported statement is authoritative for past days
                client.table(DAILY_TABLE).update({
                    "realized": realized, "unrealized": unrealized, "total": total,
                    "trades_count": trades_count, "source": source, "updated_at": _now(),
                }).eq("id", q[0]["id"]).execute()
            else:
                client.table(DAILY_TABLE).insert({
                    "id": uuid.uuid4().hex[:12], "account_id": account_id, "date": date,
                    "strategy": strategy, "realized": realized, "unrealized": unrealized,
                    "total": total, "trades_count": trades_count, "source": source,
                    "updated_at": _now(),
                }).execute()
        except Exception:
            pass
        return
    data = _read(DAILY_TABLE)
    hit = next((r for r in data if r.get("account_id") == account_id
                and (r.get("date") or "")[:10] == date and r.get("strategy") == strategy), None)
    if hit:
        if source == "trades" and hit.get("source") == "live" and date >= today_ist():
            return
        if source in ("trades", "live") and hit.get("source") == "statement" and date < today_ist():
            return
        hit.update({"realized": realized, "unrealized": unrealized, "total": total,
                    "trades_count": trades_count, "source": source, "updated_at": _now()})
    else:
        data.append({"id": uuid.uuid4().hex[:12], "account_id": account_id, "date": date,
                     "strategy": strategy, "realized": realized, "unrealized": unrealized,
                     "total": total, "trades_count": trades_count, "source": source,
                     "updated_at": _now()})
    _write(DAILY_TABLE, data)


def list_daily(date_from: Optional[str] = None, date_to: Optional[str] = None,
               account_id: Optional[str] = None) -> list[dict]:
    client = _get_client()
    if client:
        try:
            q = client.table(DAILY_TABLE).select("*")
            if date_from:
                q = q.gte("date", date_from)
            if date_to:
                q = q.lte("date", date_to)
            if account_id:
                q = q.eq("account_id", account_id)
            return q.order("date", desc=False).limit(20000).execute().data or []
        except Exception:
            return []
    rows = _read(DAILY_TABLE)
    if date_from:
        rows = [r for r in rows if (r.get("date") or "")[:10] >= date_from]
    if date_to:
        rows = [r for r in rows if (r.get("date") or "")[:10] <= date_to]
    if account_id:
        rows = [r for r in rows if r.get("account_id") == account_id]
    return sorted(rows, key=lambda r: r.get("date") or "")


# ── Minute snapshots ────────────────────────────────────────────────────────────
def add_snapshot(account_id: str, ts: str, date: str, day_pnl: float,
                 by_strategy: dict) -> None:
    row = {"id": uuid.uuid4().hex[:12], "account_id": account_id, "ts": ts,
           "date": date, "day_pnl": day_pnl, "by_strategy": by_strategy}
    try:
        client = _get_client()
        if client:
            client.table(SNAPSHOTS_TABLE).upsert(
                row, on_conflict="account_id,ts").execute()
        else:
            data = _read(SNAPSHOTS_TABLE)
            data = [r for r in data if not (r.get("account_id") == account_id and r.get("ts") == ts)]
            data.append(row)
            _write(SNAPSHOTS_TABLE, data[-50000:])
    except Exception:
        pass


def list_snapshots(date: str, account_id: Optional[str] = None) -> list[dict]:
    client = _get_client()
    if client:
        try:
            # Page past PostgREST's 1000-row cap — a full trading day is ~375 min ×
            # N accounts (>1000 rows), and a plain .limit() ordered ascending would
            # silently drop the NEWEST minutes, so the "full day" chart would stop
            # short of now. Fetch them all, then sort.
            eqs = {"date": date}
            if account_id:
                eqs["account_id"] = account_id
            return sorted(_fetch_all(client, SNAPSHOTS_TABLE, **eqs),
                          key=lambda r: r.get("ts") or "")
        except Exception:
            return []
    rows = [r for r in _read(SNAPSHOTS_TABLE) if (r.get("date") or "")[:10] == date]
    if account_id:
        rows = [r for r in rows if r.get("account_id") == account_id]
    return sorted(rows, key=lambda r: r.get("ts") or "")


def prune_snapshots_before(keep_date: str) -> int:
    """Delete minute snapshots for any date strictly before keep_date — those
    days are complete, so only their daily P&L total (in fno_daily_pnl) is kept.
    Minute resolution lives only for the current live day. Best-effort."""
    client = _get_client()
    if client:
        try:
            res = client.table(SNAPSHOTS_TABLE).delete().lt("date", keep_date).execute()
            return len(res.data or [])
        except Exception:
            return 0
    data = _read(SNAPSHOTS_TABLE)
    keep = [s for s in data if (s.get("date") or "")[:10] >= keep_date]
    n = len(data) - len(keep)
    if n:
        _write(SNAPSHOTS_TABLE, keep)
    return n


def prune_snapshots_before_ts(cutoff_iso: str) -> int:
    """Rolling retention: delete every minute snapshot older than cutoff_iso (a
    tz-aware instant). The chart only draws the last ~hour, so that's all the DB
    needs to hold — this keeps fno_pnl_snapshots tiny (≈ one row/account/minute
    for one hour) instead of accumulating the whole day. Best-effort."""
    client = _get_client()
    if client:
        try:
            res = client.table(SNAPSHOTS_TABLE).delete().lt("ts", cutoff_iso).execute()
            return len(res.data or [])
        except Exception:
            return 0
    data = _read(SNAPSHOTS_TABLE)
    keep = [s for s in data if (s.get("ts") or "") >= cutoff_iso]
    n = len(data) - len(keep)
    if n:
        _write(SNAPSHOTS_TABLE, keep)
    return n


def list_recent_snapshots(minutes: int = 75) -> list[dict]:
    """The rolling live window the chart draws — every snapshot from the last
    `minutes`, across the midnight boundary (so late-MCX sessions don't get cut).
    Ordered oldest→newest. Best-effort."""
    cutoff = (datetime.now(IST) - timedelta(minutes=minutes)).isoformat()
    client = _get_client()
    if client:
        try:
            return (client.table(SNAPSHOTS_TABLE).select("*").gte("ts", cutoff)
                    .order("ts", desc=False).limit(5000).execute().data or [])
        except Exception:
            return []
    return sorted((s for s in _read(SNAPSHOTS_TABLE) if (s.get("ts") or "") >= cutoff),
                  key=lambda s: s.get("ts") or "")
