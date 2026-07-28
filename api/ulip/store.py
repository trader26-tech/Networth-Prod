"""
ULIP (Unit Linked Insurance Plan) store — Supabase-backed, JSON-file fallback.

Stores the *raw facts* about each policy: who owns it, the insurer/plan, the
premium (amount + frequency + paying term), the life cover (sum assured) and the
current fund value. Everything derived — lock-in end, maturity, premiums paid /
remaining, gain and XIRR — is computed on read (see api/ulip/engine.py).

Data model:
  ulip_policies   one row per policy

Same shape as api/salary/store.py.
"""
from __future__ import annotations

import os
import re
import json
import uuid
from datetime import datetime
from typing import Any, Optional

POLICIES_TABLE = "ulip_policies"

ULIP_COLUMNS = {
    "id", "owner", "insurer", "plan_name", "policy_number", "life_assured",
    "start_date", "policy_term_years", "premium_paying_term_years",
    "premium_amount", "premium_frequency", "sum_assured",
    "fund_value", "fund_type", "note", "sellable_on", "created_at", "updated_at",
}

FREQUENCIES = ("monthly", "quarterly", "half_yearly", "yearly", "single")
FUND_TYPES = ("equity", "balanced", "debt", "other")


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
_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "ulip_data")
_POLICIES_FILE = os.path.join(_DATA_DIR, "policies.json")

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
        print(f"✓ ULIP store: Supabase active ({url})")
        return _client
    except Exception as e:
        print(f"⚠  ULIP store: Supabase init failed (using JSON): {e}")
        return None


def is_active() -> bool:
    return _get_client() is not None


def reset_client_cache():
    global _client, _init_attempted, _tables_ok
    _client = None
    _init_attempted = False
    _tables_ok = False


MIGRATION_HINT = (
    "ULIP table not found in Supabase. Run the migration in SUPABASE.md "
    "(\"ULIP policies table\") once in the Supabase SQL editor to create "
    "ulip_policies, then retry."
)


def tables_ready() -> bool:
    global _tables_ok
    if _tables_ok:
        return True
    client = _get_client()
    if not client:
        return True  # JSON fallback is always ready
    try:
        client.table(POLICIES_TABLE).select("id").limit(1).execute()
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
    for k in ("owner", "insurer", "plan_name", "policy_number", "life_assured",
              "start_date", "premium_frequency", "fund_type", "note", "sellable_on"):
        if k in data and data[k] is not None:
            out[k] = str(data[k]).strip() or None
    for k in ("policy_term_years", "premium_paying_term_years",
              "premium_amount", "sum_assured", "fund_value"):
        if k in data:
            out[k] = _as_float(data[k])
    if out.get("premium_frequency") not in (None, *FREQUENCIES):
        out["premium_frequency"] = "yearly"
    if out.get("fund_type") not in (None, *FUND_TYPES):
        out["fund_type"] = "equity"
    return out


def _decorate(row: dict) -> dict:
    return {k: row.get(k) for k in ULIP_COLUMNS}


def _sb_insert(client, payload: dict) -> dict:
    body = dict(payload)
    for _ in range(len(body) + 1):
        try:
            res = client.table(POLICIES_TABLE).insert(body).execute()
            return (res.data or [body])[0]
        except Exception as e:
            col = _missing_column(e)
            if col and col in body and col not in ("id", "plan_name"):
                print(f"⚠ ULIP: dropping missing column `{col}` — run the ALTER to persist it.")
                body.pop(col)
                continue
            raise
    raise RuntimeError("ULIP insert failed after stripping unknown columns.")


def _sb_update(client, pid: str, updates: dict) -> list:
    body = dict(updates)
    for _ in range(len(body) + 1):
        try:
            res = client.table(POLICIES_TABLE).update(body).eq("id", pid).execute()
            return res.data or []
        except Exception as e:
            col = _missing_column(e)
            if col and col in body:
                print(f"⚠ ULIP: dropping missing column `{col}` on update.")
                body.pop(col)
                continue
            raise
    return []


# ── CRUD ──────────────────────────────────────────────────────────────────────
def list_policies() -> list[dict]:
    client = _get_client()
    if client:
        rows = (client.table(POLICIES_TABLE).select("*")
                .order("created_at", desc=True).execute().data) or []
    else:
        rows = sorted(_read_json(_POLICIES_FILE),
                      key=lambda r: r.get("created_at", ""), reverse=True)
    return [_decorate(r) for r in rows]


def get_policy(pid: str) -> Optional[dict]:
    client = _get_client()
    if client:
        rows = client.table(POLICIES_TABLE).select("*").eq("id", pid).limit(1).execute().data or []
        return _decorate(rows[0]) if rows else None
    rows = [r for r in _read_json(_POLICIES_FILE) if r["id"] == pid]
    return _decorate(rows[0]) if rows else None


def create_policy(data: dict) -> dict:
    payload = _clean_payload(data)
    payload["id"] = _new_id()
    payload["created_at"] = _now()
    payload["updated_at"] = payload["created_at"]
    payload.setdefault("plan_name", "ULIP policy")
    payload.setdefault("premium_frequency", "yearly")

    client = _get_client()
    if client:
        row = _sb_insert(client, payload)
        return _decorate(row)
    policies = _read_json(_POLICIES_FILE)
    policies.append(payload)
    _write_json(_POLICIES_FILE, policies)
    return _decorate(payload)


def update_policy(pid: str, patch: dict) -> Optional[dict]:
    updates = _clean_payload(patch)
    if not updates:
        return get_policy(pid)
    updates["updated_at"] = _now()

    client = _get_client()
    if client:
        rows = _sb_update(client, pid, updates)
        if not rows:
            return None
        return get_policy(pid)
    policies = _read_json(_POLICIES_FILE)
    found = False
    for r in policies:
        if r["id"] == pid:
            r.update(updates)
            found = True
            break
    if not found:
        return None
    _write_json(_POLICIES_FILE, policies)
    return get_policy(pid)


def delete_policy(pid: str) -> bool:
    client = _get_client()
    if client:
        res = client.table(POLICIES_TABLE).delete().eq("id", pid).execute()
        return bool(res.data)
    policies = _read_json(_POLICIES_FILE)
    remaining = [r for r in policies if r["id"] != pid]
    if len(remaining) == len(policies):
        return False
    _write_json(_POLICIES_FILE, remaining)
    return True


def insurers() -> list[str]:
    """Distinct insurer names already used — for the auto-suggest."""
    seen: dict[str, str] = {}
    for r in list_policies():
        b = (r.get("insurer") or "").strip()
        if b:
            seen.setdefault(b.lower(), b)
    return sorted(seen.values(), key=lambda s: s.lower())
