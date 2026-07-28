"""
Cash / funds-in-hand store — Supabase-backed, JSON-file fallback.

Tracks liquid funds: physical cash in hand and bank balances. Balances can be
in any currency (e.g. KWD held in Kuwait) and convert to INR at read time. The
most-liquid asset on the dashboard.

Data model:
  cash_funds   one row per stash / account

Same shape as api/salary/store.py.
"""
from __future__ import annotations

import os
import re
import json
import uuid
from datetime import datetime
from typing import Any, Optional

TABLE = "cash_funds"

CASH_COLUMNS = {
    "id", "owner", "type", "where", "account_label", "balance", "currency",
    "as_of_date", "note", "created_at", "updated_at",
}

TYPES = ("cash", "bank")


def _missing_column(err: Exception) -> Optional[str]:
    msg = str(err)
    m = re.search(r"column [\"']?[\w]+\.?([\w]+)[\"']? does not exist", msg)
    if m:
        return m.group(1)
    m = re.search(r"[Cc]ould not find the '([\w]+)' column", msg)
    if m:
        return m.group(1)
    return None


_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "cash_data")
_FILE = os.path.join(_DATA_DIR, "funds.json")

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
        print(f"✓ Cash store: Supabase active ({url})")
        return _client
    except Exception as e:
        print(f"⚠  Cash store: Supabase init failed (using JSON): {e}")
        return None


def is_active() -> bool:
    return _get_client() is not None


def reset_client_cache():
    global _client, _init_attempted, _tables_ok
    _client = None
    _init_attempted = False
    _tables_ok = False


MIGRATION_HINT = (
    "Cash table not found in Supabase. Run the migration in SUPABASE.md "
    "(\"Cash / funds table\") once in the Supabase SQL editor to create "
    "cash_funds, then retry."
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


# ── helpers ───────────────────────────────────────────────────────────────────
def _as_float(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


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


def _now() -> str:
    return datetime.now().isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def _clean_payload(data: dict) -> dict:
    out: dict[str, Any] = {}
    for k in ("owner", "type", "where", "account_label", "currency", "as_of_date", "note"):
        if k in data and data[k] is not None:
            out[k] = str(data[k]).strip() or None
    if "balance" in data:
        out["balance"] = _as_float(data["balance"])
    if out.get("currency"):
        out["currency"] = out["currency"].upper()
    if out.get("type") not in (None, *TYPES):
        out["type"] = "bank"
    return out


def _decorate(row: dict) -> dict:
    return {k: row.get(k) for k in CASH_COLUMNS}


def _sb_insert(client, payload: dict) -> dict:
    body = dict(payload)
    for _ in range(len(body) + 1):
        try:
            res = client.table(TABLE).insert(body).execute()
            return (res.data or [body])[0]
        except Exception as e:
            col = _missing_column(e)
            if col and col in body and col not in ("id",):
                print(f"⚠ Cash: dropping missing column `{col}` — run the ALTER to persist it.")
                body.pop(col)
                continue
            raise
    raise RuntimeError("Cash insert failed after stripping unknown columns.")


def _sb_update(client, cid: str, updates: dict) -> list:
    body = dict(updates)
    for _ in range(len(body) + 1):
        try:
            res = client.table(TABLE).update(body).eq("id", cid).execute()
            return res.data or []
        except Exception as e:
            col = _missing_column(e)
            if col and col in body:
                print(f"⚠ Cash: dropping missing column `{col}` on update.")
                body.pop(col)
                continue
            raise
    return []


# ── CRUD ──────────────────────────────────────────────────────────────────────
def list_items() -> list[dict]:
    client = _get_client()
    if client:
        rows = (client.table(TABLE).select("*").order("created_at", desc=True).execute().data) or []
    else:
        rows = sorted(_read_json(), key=lambda r: r.get("created_at", ""), reverse=True)
    return [_decorate(r) for r in rows]


def get_item(cid: str) -> Optional[dict]:
    client = _get_client()
    if client:
        rows = client.table(TABLE).select("*").eq("id", cid).limit(1).execute().data or []
        return _decorate(rows[0]) if rows else None
    rows = [r for r in _read_json() if r["id"] == cid]
    return _decorate(rows[0]) if rows else None


def create_item(data: dict) -> dict:
    payload = _clean_payload(data)
    payload["id"] = _new_id()
    payload["created_at"] = _now()
    payload["updated_at"] = payload["created_at"]
    payload.setdefault("type", "bank")
    payload.setdefault("currency", "INR")

    client = _get_client()
    if client:
        return _decorate(_sb_insert(client, payload))
    items = _read_json()
    items.append(payload)
    _write_json(items)
    return _decorate(payload)


def update_item(cid: str, patch: dict) -> Optional[dict]:
    updates = _clean_payload(patch)
    if not updates:
        return get_item(cid)
    updates["updated_at"] = _now()
    client = _get_client()
    if client:
        rows = _sb_update(client, cid, updates)
        return get_item(cid) if rows else None
    items = _read_json()
    found = False
    for r in items:
        if r["id"] == cid:
            r.update(updates)
            found = True
            break
    if not found:
        return None
    _write_json(items)
    return get_item(cid)


def delete_item(cid: str) -> bool:
    client = _get_client()
    if client:
        res = client.table(TABLE).delete().eq("id", cid).execute()
        return bool(res.data)
    items = _read_json()
    remaining = [r for r in items if r["id"] != cid]
    if len(remaining) == len(items):
        return False
    _write_json(remaining)
    return True


def wheres() -> list[str]:
    """Distinct bank/location labels already used — for the auto-suggest."""
    seen: dict[str, str] = {}
    for r in list_items():
        w = (r.get("where") or "").strip()
        if w:
            seen.setdefault(w.lower(), w)
    return sorted(seen.values(), key=lambda s: s.lower())
