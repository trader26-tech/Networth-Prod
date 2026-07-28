"""
Tiny store for income-receipt confirmations — "did the rent/coupon the system
expected this month actually land?". One row per (period, key). Supabase table
`income_receipts` with a JSON-file fallback.
"""
from __future__ import annotations

import os
import json
from datetime import datetime
from typing import Optional

TABLE = "income_receipts"
SETTINGS_TABLE = "app_settings"

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "dashboard_data")
_FILE = os.path.join(_DATA_DIR, "receipts.json")
_SETTINGS_FILE = os.path.join(_DATA_DIR, "settings.json")

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
        return _client
    except Exception:
        return None


def tables_ready() -> bool:
    global _tables_ok
    if _tables_ok:
        return True
    client = _get_client()
    if not client:
        return True
    try:
        client.table(TABLE).select("key").limit(1).execute()
        _tables_ok = True
        return True
    except Exception:
        return False


MIGRATION_HINT = (
    "income_receipts table not found. Run the migration in SUPABASE.md "
    "(\"Income receipts table\") in the Supabase SQL editor, then retry."
)


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


def received_keys(period: str) -> dict[str, dict]:
    """{key: receipt} for a given period (e.g. '2026-06')."""
    client = _get_client()
    if client:
        try:
            rows = client.table(TABLE).select("*").eq("period", period).execute().data or []
        except Exception:
            rows = []
    else:
        rows = [r for r in _read_json() if r.get("period") == period]
    return {r["key"]: r for r in rows if r.get("key")}


def _read_settings_json() -> dict:
    try:
        with open(_SETTINGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _write_settings_json(data: dict) -> None:
    os.makedirs(_DATA_DIR, exist_ok=True)
    tmp = _SETTINGS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)
    os.replace(tmp, _SETTINGS_FILE)


def get_setting(key: str) -> dict:
    """Stored config blob for a key (e.g. 'goal'). Empty dict if unset."""
    client = _get_client()
    if client:
        try:
            rows = client.table(SETTINGS_TABLE).select("value").eq("key", key).limit(1).execute().data or []
            return (rows[0].get("value") if rows else {}) or {}
        except Exception:
            return _read_settings_json().get(key, {})
    return _read_settings_json().get(key, {})


def set_setting(key: str, value: dict) -> dict:
    now = datetime.now().isoformat()
    client = _get_client()
    if client:
        try:
            client.table(SETTINGS_TABLE).upsert({"key": key, "value": value, "updated_at": now}).execute()
            return value
        except Exception:
            pass
    data = _read_settings_json()
    data[key] = value
    _write_settings_json(data)
    return value


def set_receipt(period: str, key: str, received: bool, amount: Optional[float] = None,
                on_date: Optional[str] = None) -> dict:
    now = datetime.now().isoformat()
    row = {"period": period, "key": key, "received": bool(received),
           "amount": amount, "on_date": on_date, "updated_at": now}
    client = _get_client()
    if client:
        try:
            client.table(TABLE).delete().eq("period", period).eq("key", key).execute()
            if received:
                # drop on_date if the column hasn't been added yet, then retry
                try:
                    client.table(TABLE).insert(row).execute()
                except Exception:
                    client.table(TABLE).insert({k: v for k, v in row.items() if k != "on_date"}).execute()
            return row
        except Exception:
            pass
    data = [r for r in _read_json() if not (r.get("period") == period and r.get("key") == key)]
    if received:
        data.append(row)
    _write_json(data)
    return row
