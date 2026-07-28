"""
Live stock portfolio — replaces the old manual `brokerage_accounts` model.

  stock_accounts   one row per brokerage account (currently Kite "connected").
                   Holds the per-account Kite credentials + daily access token
                   (server-only — never returned to the frontend).
  stock_holdings   snapshot of each account's holdings, replaced on every sync.

Market data is NOT stored — current prices are fetched live (free Yahoo feed)
on read. Cost basis (avg price, qty) comes from the broker holdings snapshot.
"""
from __future__ import annotations

import os
import json
import time
import uuid
from datetime import datetime
from typing import Optional

ACCOUNTS_TABLE = "stock_accounts"
HOLDINGS_TABLE = "stock_holdings"
CACHE_TABLE = "app_cache"                 # generic persistent KV (jsonb blobs)

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "portfolio_data")
_ACCOUNTS_FILE = os.path.join(_DATA_DIR, "accounts.json")
_HOLDINGS_FILE = os.path.join(_DATA_DIR, "holdings.json")
_CACHE_FILE = os.path.join(_DATA_DIR, "app_cache.json")

_client = None
_init_attempted = False
_tables_ok = False

# fields kept on an account row
_ACCOUNT_FIELDS = ("person", "broker", "account_label", "kind", "api_key", "api_secret",
                   "access_token", "kite_user_id", "status", "token_updated_at",
                   "last_synced", "note", "sellable_on")
# never leaked to the frontend
_SECRET_FIELDS = ("api_secret", "access_token")


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
        print(f"✓ Portfolio store: Supabase active ({url})")
        return _client
    except Exception as e:
        print(f"⚠  Portfolio store: Supabase init failed (using JSON): {e}")
        return None


def reset_client_cache():
    global _client, _init_attempted, _tables_ok
    _client = None
    _init_attempted = False
    _tables_ok = False


MIGRATION_HINT = (
    "Stock portfolio tables not found in Supabase. Run the migration in "
    "SUPABASE.md (\"Stock portfolio tables\") once in the Supabase SQL editor to "
    "create stock_accounts + stock_holdings, then retry."
)


def tables_ready() -> bool:
    global _tables_ok
    if _tables_ok:
        return True
    client = _get_client()
    if not client:
        return True
    try:
        client.table(ACCOUNTS_TABLE).select("id").limit(1).execute()
        client.table(HOLDINGS_TABLE).select("id").limit(1).execute()
        _tables_ok = True
        return True
    except Exception:
        return False


# ── Generic persistent KV (app_cache) ───────────────────────────────────────────
# A durable place for the price snapshot (and any future materialised cache).
# Supabase-primary so it's shared across replicas/restarts; a local JSON file is
# always written too, so a same-instance restart warms instantly even offline.
# Every call is best-effort and NEVER raises — a missing table or network blip
# just degrades to the next tier.
#
# PERF: when the `app_cache` table is absent, we must NOT hammer Supabase with a
# doomed round-trip on every single KV read/write (a KV-heavy page like F&O does
# dozens per load → seconds of dead latency). Once a call fails we mark the KV
# backend "absent" for a short window and go straight to the file; we re-probe
# every _CACHE_PROBE_TTL seconds so it flips on automatically once the table is
# created — no restart needed.
_cache_durable: Optional[bool] = None
_cache_absent_until: float = 0.0
_CACHE_PROBE_TTL = 60.0


def _kv_client():
    """The Supabase client, or None when we should skip it (no client, or the
    table was recently seen missing and we're inside the back-off window)."""
    if _cache_absent_until and time.time() < _cache_absent_until:
        return None
    return _get_client()


def _kv_failed() -> None:
    global _cache_absent_until
    _cache_absent_until = time.time() + _CACHE_PROBE_TTL


def _kv_ok() -> None:
    global _cache_durable, _cache_absent_until
    _cache_durable = True
    _cache_absent_until = 0.0


def cache_get(key: str) -> Optional[dict]:
    """Return {'value': <blob>, 'updated_at': <iso>} for `key`, or None."""
    client = _kv_client()
    if client:
        try:
            rows = client.table(CACHE_TABLE).select("value,updated_at").eq("key", key).limit(1).execute().data or []
            _kv_ok()
            if rows:
                return {"value": rows[0].get("value"), "updated_at": rows[0].get("updated_at")}
        except Exception:
            _kv_failed()                          # table missing / offline → back off, use the file
    try:
        with open(_CACHE_FILE, "r", encoding="utf-8") as f:
            return (json.load(f) or {}).get(key)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def cache_durable() -> bool:
    """True when the `app_cache` Supabase table exists → KV writes are shared &
    survive redeploys. False → we're on the ephemeral local-file fallback (lost on
    a Railway redeploy or a 2nd replica). Cached; positive result is sticky. Used
    to warn in-app that F&O statements / tradebooks / tax / corp-actions won't
    persist until the one-line `app_cache` migration is run (see SUPABASE.md)."""
    if _cache_durable:
        return True
    # respect the back-off window so this probe doesn't add latency either
    if _cache_absent_until and time.time() < _cache_absent_until:
        return False
    client = _get_client()
    if not client:
        return False                              # no Supabase at all → file only
    try:
        client.table(CACHE_TABLE).select("key").limit(1).execute()
        _kv_ok()
        return True
    except Exception:
        _kv_failed()                              # back off; flips on automatically once the table is added
        return False


def cache_set(key: str, value) -> None:
    """Persist `value` (JSON-serialisable) under `key`. Writes Supabase (shared)
    AND the local file (fast same-instance restarts). Best-effort, never raises."""
    updated = _now()
    client = _kv_client()
    if client:
        try:
            client.table(CACHE_TABLE).upsert({"key": key, "value": value, "updated_at": updated}).execute()
            _kv_ok()                              # a successful write proves the table exists
        except Exception:
            _kv_failed()                          # table not migrated yet → file still covers us
    try:
        os.makedirs(_DATA_DIR, exist_ok=True)
        try:
            with open(_CACHE_FILE, "r", encoding="utf-8") as f:
                allc = json.load(f) or {}
        except (FileNotFoundError, json.JSONDecodeError):
            allc = {}
        allc[key] = {"value": value, "updated_at": updated}
        tmp = _CACHE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(allc, f, default=str)
        os.replace(tmp, _CACHE_FILE)
    except Exception:
        pass


# ── JSON-file fallback ──────────────────────────────────────────────────────────
def _read(path: str) -> list:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _write(path: str, data: list) -> None:
    os.makedirs(_DATA_DIR, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)
    os.replace(tmp, path)


def _now() -> str:
    return datetime.now().isoformat()


def public_account(a: dict) -> dict:
    """Account shape safe for the frontend — secrets stripped, key masked."""
    out = {k: a.get(k) for k in ("id", "person", "broker", "account_label", "kind",
                                 "kite_user_id", "status", "token_updated_at",
                                 "last_synced", "note", "sellable_on", "created_at")}
    ak = a.get("api_key") or ""
    out["api_key_hint"] = (ak[:4] + "…") if ak else None
    out["connected"] = bool(a.get("access_token"))
    return out


# ── Accounts ────────────────────────────────────────────────────────────────────
def list_accounts() -> list[dict]:
    client = _get_client()
    if client:
        try:
            return _fetch_all(client, ACCOUNTS_TABLE)
        except Exception:
            return []                       # table not created yet
    return _read(_ACCOUNTS_FILE)


def get_account(acc_id: str) -> Optional[dict]:
    client = _get_client()
    if client:
        rows = client.table(ACCOUNTS_TABLE).select("*").eq("id", acc_id).limit(1).execute().data or []
        return rows[0] if rows else None
    return next((a for a in _read(_ACCOUNTS_FILE) if a.get("id") == acc_id), None)


def add_account(payload: dict) -> dict:
    row = {k: payload.get(k) for k in _ACCOUNT_FIELDS if k in payload}
    row["id"] = uuid.uuid4().hex[:12]
    row["kind"] = row.get("kind") or "connected"
    row["broker"] = row.get("broker") or "kite"
    row["status"] = row.get("status") or "pending"
    row["created_at"] = _now()
    client = _get_client()
    if client:
        client.table(ACCOUNTS_TABLE).insert(row).execute()
    else:
        data = _read(_ACCOUNTS_FILE)
        data.append(row)
        _write(_ACCOUNTS_FILE, data)
    return row


def update_account(acc_id: str, patch: dict) -> Optional[dict]:
    patch = {k: v for k, v in patch.items() if k in _ACCOUNT_FIELDS}
    client = _get_client()
    if client:
        rows = client.table(ACCOUNTS_TABLE).update(patch).eq("id", acc_id).execute().data or []
        return rows[0] if rows else None
    data = _read(_ACCOUNTS_FILE)
    hit = None
    for a in data:
        if a.get("id") == acc_id:
            a.update(patch)
            hit = a
    if hit:
        _write(_ACCOUNTS_FILE, data)
    return hit


def delete_account(acc_id: str) -> int:
    client = _get_client()
    if client:
        client.table(HOLDINGS_TABLE).delete().eq("account_id", acc_id).execute()
        res = client.table(ACCOUNTS_TABLE).delete().eq("id", acc_id).execute()
        return len(res.data or [])
    accts = _read(_ACCOUNTS_FILE)
    keep = [a for a in accts if a.get("id") != acc_id]
    n = len(accts) - len(keep)
    _write(_ACCOUNTS_FILE, keep)
    hold = [h for h in _read(_HOLDINGS_FILE) if h.get("account_id") != acc_id]
    _write(_HOLDINGS_FILE, hold)
    return n


# ── Holdings ────────────────────────────────────────────────────────────────────
def _fetch_all(client, table: str) -> list[dict]:
    """Page past Supabase's 1000-row default cap to fetch the whole table."""
    out: list[dict] = []
    start, page = 0, 1000
    while True:
        rows = client.table(table).select("*").range(start, start + page - 1).execute().data or []
        out.extend(rows)
        if len(rows) < page:
            return out
        start += page


def list_holdings() -> list[dict]:
    client = _get_client()
    if client:
        try:
            return _fetch_all(client, HOLDINGS_TABLE)
        except Exception:
            return []
    return _read(_HOLDINGS_FILE)


def replace_holdings(account: dict, holdings: list[dict]) -> None:
    """Swap an account's holdings for a freshly fetched snapshot."""
    acc_id = account["id"]
    rows = []
    for h in holdings:
        rows.append({
            "id": uuid.uuid4().hex[:12],
            "account_id": acc_id,
            "person": account.get("person"),
            "broker": account.get("broker"),
            "account_label": account.get("account_label"),
            "symbol": h.get("symbol") or "",
            "name": h.get("name"),
            "exchange": h.get("exchange") or "NSE",
            "isin": h.get("isin"),
            "currency": h.get("currency") or "INR",
            "quantity": float(h.get("quantity") or 0),
            "avg_price": float(h.get("avg_price") or 0),
            "import_price": float(h.get("import_price") or 0) or None,
            "last_synced": _now(),
        })
    client = _get_client()
    if client:
        client.table(HOLDINGS_TABLE).delete().eq("account_id", acc_id).execute()
        if rows:
            client.table(HOLDINGS_TABLE).insert(rows).execute()
    else:
        data = [h for h in _read(_HOLDINGS_FILE) if h.get("account_id") != acc_id]
        data.extend(rows)
        _write(_HOLDINGS_FILE, data)
