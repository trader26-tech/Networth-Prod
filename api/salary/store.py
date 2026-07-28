"""
Salary / earned-income store — Supabase-backed, JSON-file fallback.

Stores the *raw facts* about each person's recurring income: who earns it, how
much, in which currency, how often, and which bank account it lands in. The INR
value and per-month figure are derived at read time (see api/salary/engine.py +
api/salary/fx.py) so a KWD salary re-values automatically as the rate moves.

Data model:
  salary_entries   one row per income stream (person can have several)

Same shape as api/gold/store.py — minus documents.
"""
from __future__ import annotations

import os
import re
import json
import uuid
from datetime import datetime
from typing import Any, Optional

ENTRIES_TABLE = "salary_entries"

SALARY_COLUMNS = {
    "id", "person", "amount", "currency", "frequency",
    "bank_account", "note", "created_at", "updated_at",
}

FREQUENCIES = ("monthly", "annual")

# Salary is region-tagged by its currency, exactly like expenses: a salary paid
# in INR is Indian income, anything else (KWD…) is Kuwait/abroad. Keeps the two
# tabs' India-vs-Kuwait split consistent.
REGION_LABELS = {"india": "India", "kuwait": "Kuwait"}


def region_of(currency: Optional[str]) -> str:
    return "india" if (currency or "INR").upper() == "INR" else "kuwait"


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
_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "salary_data")
_ENTRIES_FILE = os.path.join(_DATA_DIR, "entries.json")

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
        print(f"✓ Salary store: Supabase active ({url})")
        return _client
    except Exception as e:
        print(f"⚠  Salary store: Supabase init failed (using JSON): {e}")
        return None


def is_active() -> bool:
    return _get_client() is not None


def reset_client_cache():
    global _client, _init_attempted, _tables_ok
    _client = None
    _init_attempted = False
    _tables_ok = False


MIGRATION_HINT = (
    "Salary table not found in Supabase. Run the migration in SUPABASE.md "
    "(\"Salary / income table\") once in the Supabase SQL editor to create "
    "salary_entries, then retry."
)


def tables_ready() -> bool:
    global _tables_ok
    if _tables_ok:
        return True
    client = _get_client()
    if not client:
        return True  # JSON fallback is always ready
    try:
        client.table(ENTRIES_TABLE).select("id").limit(1).execute()
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
    for k in ("person", "currency", "frequency", "bank_account", "note"):
        if k in data and data[k] is not None:
            out[k] = str(data[k]).strip() or None
    if "amount" in data:
        out["amount"] = _as_float(data["amount"])
    if out.get("currency"):
        out["currency"] = out["currency"].upper()
    if out.get("frequency") not in (None, *FREQUENCIES):
        out["frequency"] = "monthly"
    return out


def _decorate(row: dict) -> dict:
    item = {k: row.get(k) for k in SALARY_COLUMNS}
    region = region_of(item.get("currency"))          # KWD… → Kuwait, INR → India
    item["region"] = region
    item["region_label"] = REGION_LABELS.get(region, "India")
    return item


def _sb_insert(client, payload: dict) -> dict:
    body = dict(payload)
    for _ in range(len(body) + 1):
        try:
            res = client.table(ENTRIES_TABLE).insert(body).execute()
            return (res.data or [body])[0]
        except Exception as e:
            col = _missing_column(e)
            if col and col in body and col not in ("id", "person"):
                print(f"⚠ Salary: dropping missing column `{col}` — run the ALTER to persist it.")
                body.pop(col)
                continue
            raise
    raise RuntimeError("Salary insert failed after stripping unknown columns.")


def _sb_update(client, eid: str, updates: dict) -> list:
    body = dict(updates)
    for _ in range(len(body) + 1):
        try:
            res = client.table(ENTRIES_TABLE).update(body).eq("id", eid).execute()
            return res.data or []
        except Exception as e:
            col = _missing_column(e)
            if col and col in body:
                print(f"⚠ Salary: dropping missing column `{col}` on update.")
                body.pop(col)
                continue
            raise
    return []


# ── CRUD ──────────────────────────────────────────────────────────────────────
def list_entries() -> list[dict]:
    client = _get_client()
    if client:
        rows = (client.table(ENTRIES_TABLE).select("*")
                .order("created_at", desc=True).execute().data) or []
    else:
        rows = sorted(_read_json(_ENTRIES_FILE),
                      key=lambda r: r.get("created_at", ""), reverse=True)
    return [_decorate(r) for r in rows]


def get_entry(eid: str) -> Optional[dict]:
    client = _get_client()
    if client:
        rows = client.table(ENTRIES_TABLE).select("*").eq("id", eid).limit(1).execute().data or []
        return _decorate(rows[0]) if rows else None
    rows = [r for r in _read_json(_ENTRIES_FILE) if r["id"] == eid]
    return _decorate(rows[0]) if rows else None


def create_entry(data: dict) -> dict:
    payload = _clean_payload(data)
    payload["id"] = _new_id()
    payload["created_at"] = _now()
    payload["updated_at"] = payload["created_at"]
    payload.setdefault("person", "Untitled")
    payload.setdefault("currency", "INR")
    payload.setdefault("frequency", "monthly")

    client = _get_client()
    if client:
        row = _sb_insert(client, payload)
        return _decorate(row)
    entries = _read_json(_ENTRIES_FILE)
    entries.append(payload)
    _write_json(_ENTRIES_FILE, entries)
    return _decorate(payload)


def update_entry(eid: str, patch: dict) -> Optional[dict]:
    updates = _clean_payload(patch)
    if not updates:
        return get_entry(eid)
    updates["updated_at"] = _now()

    client = _get_client()
    if client:
        rows = _sb_update(client, eid, updates)
        if not rows:
            return None
        return get_entry(eid)
    entries = _read_json(_ENTRIES_FILE)
    found = False
    for r in entries:
        if r["id"] == eid:
            r.update(updates)
            found = True
            break
    if not found:
        return None
    _write_json(_ENTRIES_FILE, entries)
    return get_entry(eid)


def delete_entry(eid: str) -> bool:
    client = _get_client()
    if client:
        res = client.table(ENTRIES_TABLE).delete().eq("id", eid).execute()
        return bool(res.data)
    entries = _read_json(_ENTRIES_FILE)
    remaining = [r for r in entries if r["id"] != eid]
    if len(remaining) == len(entries):
        return False
    _write_json(_ENTRIES_FILE, remaining)
    return True


def bank_labels() -> list[str]:
    """Distinct bank-account labels already used — drives the auto-suggest."""
    seen: dict[str, str] = {}
    for r in list_entries():
        b = (r.get("bank_account") or "").strip()
        if b:
            seen.setdefault(b.lower(), b)
    return sorted(seen.values(), key=lambda s: s.lower())
