"""
Fixed Deposit store — Supabase-backed, JSON-file fallback.

Stores the *raw facts* about each FD: who owns it, the bank, principal, rate,
start date, tenure, and whether interest is paid out (non-cumulative) or
reinvested (cumulative). Maturity date/amount, current value, interest income
and the payout calendar are all computed on read (see api/fd/engine.py).

Data model:
  fd_deposits   one row per deposit

Same shape as api/ulip/store.py.
"""
from __future__ import annotations

import os
import re
import json
import uuid
from datetime import datetime
from typing import Any, Optional

FD_TABLE = "fd_deposits"

FD_COLUMNS = {
    "id", "owner", "bank", "principal", "interest_rate", "start_date",
    "tenure_months", "compounding_frequency", "payout_type", "payout_frequency",
    "note", "sellable_on", "created_at", "updated_at",
}

PAYOUT_TYPES = ("payout", "cumulative")
FREQUENCIES = ("monthly", "quarterly", "half_yearly", "yearly")


def _missing_column(err: Exception) -> Optional[str]:
    msg = str(err)
    m = re.search(r"column [\"']?[\w]+\.?([\w]+)[\"']? does not exist", msg)
    if m:
        return m.group(1)
    m = re.search(r"[Cc]ould not find the '([\w]+)' column", msg)
    if m:
        return m.group(1)
    return None


# ── local-fallback paths ──────────────────────────────────────────────────────
_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fd_data")
_FD_FILE = os.path.join(_DATA_DIR, "deposits.json")

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
        print(f"✓ FD store: Supabase active ({url})")
        return _client
    except Exception as e:
        print(f"⚠  FD store: Supabase init failed (using JSON): {e}")
        return None


def is_active() -> bool:
    return _get_client() is not None


def reset_client_cache():
    global _client, _init_attempted, _tables_ok
    _client = None
    _init_attempted = False
    _tables_ok = False


MIGRATION_HINT = (
    "FD table not found in Supabase. Run the migration in SUPABASE.md "
    "(\"Fixed Deposits table\") once in the Supabase SQL editor to create "
    "fd_deposits, then retry."
)


def tables_ready() -> bool:
    global _tables_ok
    if _tables_ok:
        return True
    client = _get_client()
    if not client:
        return True
    try:
        client.table(FD_TABLE).select("id").limit(1).execute()
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


def _as_int(v: Any) -> Optional[int]:
    f = _as_float(v)
    return int(round(f)) if f is not None else None


def _read_json(path: str) -> list:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _write_json(path: str, data: list) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)
    os.replace(tmp, path)


def _now() -> str:
    return datetime.now().isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def _clean_payload(data: dict) -> dict:
    out: dict[str, Any] = {}
    for k in ("owner", "bank", "start_date", "compounding_frequency",
              "payout_type", "payout_frequency", "note", "sellable_on"):
        if k in data and data[k] is not None:
            out[k] = str(data[k]).strip() or None
    for k in ("principal", "interest_rate"):
        if k in data:
            out[k] = _as_float(data[k])
    if "tenure_months" in data:
        out["tenure_months"] = _as_int(data["tenure_months"])
    if out.get("payout_type") not in (None, *PAYOUT_TYPES):
        out["payout_type"] = "payout"
    if out.get("payout_frequency") not in (None, *FREQUENCIES):
        out["payout_frequency"] = "quarterly"
    if out.get("compounding_frequency") not in (None, *FREQUENCIES):
        out["compounding_frequency"] = "quarterly"
    return out


def _decorate(row: dict) -> dict:
    return {k: row.get(k) for k in FD_COLUMNS}


def _sb_insert(client, payload: dict) -> dict:
    body = dict(payload)
    for _ in range(len(body) + 1):
        try:
            res = client.table(FD_TABLE).insert(body).execute()
            return (res.data or [body])[0]
        except Exception as e:
            col = _missing_column(e)
            if col and col in body and col not in ("id", "bank"):
                print(f"⚠ FD: dropping missing column `{col}` — run the ALTER to persist it.")
                body.pop(col)
                continue
            raise
    raise RuntimeError("FD insert failed after stripping unknown columns.")


def _sb_update(client, fid: str, updates: dict) -> list:
    body = dict(updates)
    for _ in range(len(body) + 1):
        try:
            res = client.table(FD_TABLE).update(body).eq("id", fid).execute()
            return res.data or []
        except Exception as e:
            col = _missing_column(e)
            if col and col in body:
                print(f"⚠ FD: dropping missing column `{col}` on update.")
                body.pop(col)
                continue
            raise
    return []


# ── CRUD ──────────────────────────────────────────────────────────────────────
def list_fds() -> list[dict]:
    client = _get_client()
    if client:
        rows = (client.table(FD_TABLE).select("*")
                .order("created_at", desc=True).execute().data) or []
    else:
        rows = sorted(_read_json(_FD_FILE),
                      key=lambda r: r.get("created_at", ""), reverse=True)
    return [_decorate(r) for r in rows]


def get_fd(fid: str) -> Optional[dict]:
    client = _get_client()
    if client:
        rows = client.table(FD_TABLE).select("*").eq("id", fid).limit(1).execute().data or []
        return _decorate(rows[0]) if rows else None
    rows = [r for r in _read_json(_FD_FILE) if r["id"] == fid]
    return _decorate(rows[0]) if rows else None


def create_fd(data: dict) -> dict:
    payload = _clean_payload(data)
    payload["id"] = _new_id()
    payload["created_at"] = _now()
    payload["updated_at"] = payload["created_at"]
    payload.setdefault("bank", "Fixed Deposit")
    payload.setdefault("payout_type", "payout")
    payload.setdefault("payout_frequency", "quarterly")
    payload.setdefault("compounding_frequency", "quarterly")

    client = _get_client()
    if client:
        row = _sb_insert(client, payload)
        return _decorate(row)
    fds = _read_json(_FD_FILE)
    fds.append(payload)
    _write_json(_FD_FILE, fds)
    return _decorate(payload)


def update_fd(fid: str, patch: dict) -> Optional[dict]:
    updates = _clean_payload(patch)
    if not updates:
        return get_fd(fid)
    updates["updated_at"] = _now()

    client = _get_client()
    if client:
        rows = _sb_update(client, fid, updates)
        if not rows:
            return None
        return get_fd(fid)
    fds = _read_json(_FD_FILE)
    found = False
    for r in fds:
        if r["id"] == fid:
            r.update(updates)
            found = True
            break
    if not found:
        return None
    _write_json(_FD_FILE, fds)
    return get_fd(fid)


def delete_fd(fid: str) -> bool:
    client = _get_client()
    if client:
        res = client.table(FD_TABLE).delete().eq("id", fid).execute()
        return bool(res.data)
    fds = _read_json(_FD_FILE)
    remaining = [r for r in fds if r["id"] != fid]
    if len(remaining) == len(fds):
        return False
    _write_json(_FD_FILE, remaining)
    return True


def banks() -> list[str]:
    """Distinct bank names already used — for the auto-suggest."""
    seen: dict[str, str] = {}
    for r in list_fds():
        b = (r.get("bank") or "").strip()
        if b:
            seen.setdefault(b.lower(), b)
    return sorted(seen.values(), key=lambda s: s.lower())
