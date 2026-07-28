"""
Supabase store for family brokerage accounts (JSON-file fallback for dev).

One flat table — `brokerage_accounts` — one row per member's broker account:
a start (date + amount invested) and an end (date + value now/exit). CAGR per
account and the combined money-weighted return are computed on read in
api/brokerage/engine.py. See SUPABASE.md → "Brokerage accounts table".
"""
from __future__ import annotations

import os
import json
import uuid
from datetime import datetime
from typing import Optional

TABLE = "brokerage_accounts"

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "brokerage_data")
_FILE = os.path.join(_DATA_DIR, "accounts.json")

_client = None
_init_attempted = False
_tables_ok = False


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
        print(f"✓ Brokerage store: Supabase active ({url})")
        return _client
    except Exception as e:
        print(f"⚠  Brokerage store: Supabase init failed (using JSON): {e}")
        return None


def reset_client_cache():
    global _client, _init_attempted, _tables_ok
    _client = None
    _init_attempted = False
    _tables_ok = False


MIGRATION_HINT = (
    "Brokerage table not found in Supabase. Run the migration in SUPABASE.md "
    "(\"Brokerage accounts table\") once in the Supabase SQL editor to create "
    "brokerage_accounts, then retry."
)


def tables_ready() -> bool:
    global _tables_ok
    if _tables_ok:
        return True
    client = _get_client()
    if not client:
        return True
    try:
        client.table(TABLE).select("id").limit(1).execute()
        _tables_ok = True
        return True
    except Exception:
        return False


# ── JSON fallback ──────────────────────────────────────────────────────────────
def _read_json() -> list:
    try:
        with open(_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _write_json(data: list) -> None:
    os.makedirs(_DATA_DIR, exist_ok=True)
    tmp = _FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)
    os.replace(tmp, _FILE)


# ── fields ──────────────────────────────────────────────────────────────────────
_FIELDS = ("member", "broker", "start_date", "start_amount", "end_date", "end_amount", "note", "sellable_on")


def _clean(payload: dict) -> dict:
    out = {}
    for k in _FIELDS:
        if k in payload:
            out[k] = payload[k]
    for amt in ("start_amount", "end_amount"):
        if amt in out and out[amt] is not None:
            out[amt] = float(out[amt])
    return out


# ── API ────────────────────────────────────────────────────────────────────────
def list_accounts() -> list[dict]:
    client = _get_client()
    if client:
        return client.table(TABLE).select("*").order("member").execute().data or []
    return _read_json()


def add_account(payload: dict) -> dict:
    row = _clean(payload)
    row["id"] = uuid.uuid4().hex[:12]
    row["created_at"] = datetime.now().isoformat()
    client = _get_client()
    if client:
        client.table(TABLE).insert(row).execute()
    else:
        data = _read_json()
        data.append(row)
        _write_json(data)
    return row


def update_account(acc_id: str, payload: dict) -> Optional[dict]:
    patch = _clean(payload)
    client = _get_client()
    if client:
        res = client.table(TABLE).update(patch).eq("id", acc_id).execute()
        return (res.data or [None])[0]
    data = _read_json()
    hit = None
    for r in data:
        if r.get("id") == acc_id:
            r.update(patch)
            hit = r
    if hit:
        _write_json(data)
    return hit


def delete_account(acc_id: str) -> int:
    client = _get_client()
    if client:
        res = client.table(TABLE).delete().eq("id", acc_id).execute()
        return len(res.data or [])
    data = _read_json()
    keep = [r for r in data if r.get("id") != acc_id]
    n = len(data) - len(keep)
    _write_json(keep)
    return n
