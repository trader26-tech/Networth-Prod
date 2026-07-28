"""
Monthly expenses store — Supabase-backed, JSON-file fallback.

One row per recurring expense: what it is, the category, amount + currency
(KWD/INR/…), how often, who pays, the payment method, and flags for subscription
/ essential / active. The monthly ₹-equivalent (currency-converted + frequency-
normalised) and all the splits are computed on read (see api/expenses/engine.py).

Data model:
  expenses   one row per expense

Same shape as api/salary/store.py.
"""
from __future__ import annotations

import os
import re
import json
import uuid
from datetime import datetime
from typing import Any, Optional

EXPENSES_TABLE = "expenses"

EXPENSE_COLUMNS = {
    "id", "owner", "name", "category", "amount", "currency", "frequency",
    "payment_method", "is_subscription", "essential", "active",
    "is_template", "template_id", "on_date", "end_date", "note",
    "created_at", "updated_at",
}

FREQUENCIES = ("weekly", "monthly", "quarterly", "half_yearly", "yearly", "one_time")

# Expenses are logged for one of two regions; the region fixes the currency so a
# person logging never has to pick a currency. Everything is still converted to
# INR on read (live FX) for the treemap + net-worth surplus.
REGIONS = {"india": "INR", "kuwait": "KWD"}
REGION_LABELS = {"india": "India", "kuwait": "Kuwait"}


def region_of(currency: Optional[str]) -> str:
    """India = INR, everything else (KWD…) = Kuwait/abroad."""
    return "india" if (currency or "INR").upper() == "INR" else "kuwait"


def currency_of(region: Optional[str]) -> str:
    return REGIONS.get((region or "india").lower(), "INR")

# Suggested categories (free-text — these just seed the picker).
# This IS the vocabulary the statement parser and the Claude/MCP inbox categorise
# against (see api/expenses/statement.py), so one spend always lands in one
# category no matter which way it was entered. Keep the two in sync.
CATEGORIES = [
    "Housing / Rent", "Groceries", "Utilities", "Internet & Phone",
    "Transport & Fuel", "EMI / Loan", "Insurance", "Education", "Healthcare",
    "Subscriptions", "Dining out", "Shopping", "Domestic help",
    "Entertainment", "Travel", "Personal care", "Donations",
    "Investments", "Cash / ATM", "Transfers / UPI", "Miscellaneous",
]


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
_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "expenses_data")
_EXP_FILE = os.path.join(_DATA_DIR, "expenses.json")

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
        print(f"✓ Expenses store: Supabase active ({url})")
        return _client
    except Exception as e:
        print(f"⚠  Expenses store: Supabase init failed (using JSON): {e}")
        return None


def is_active() -> bool:
    return _get_client() is not None


def reset_client_cache():
    global _client, _init_attempted, _tables_ok, _END_DATE_OK
    _client = None
    _init_attempted = False
    _tables_ok = False
    _END_DATE_OK = None


MIGRATION_HINT = (
    "Expenses table not found in Supabase. Run the migration in SUPABASE.md "
    "(\"Monthly expenses table\") once in the Supabase SQL editor to create "
    "expenses, then retry."
)


def tables_ready() -> bool:
    global _tables_ok
    if _tables_ok:
        return True
    client = _get_client()
    if not client:
        return True
    try:
        client.table(EXPENSES_TABLE).select("id").limit(1).execute()
        _tables_ok = True
        return True
    except Exception:
        return False


_END_DATE_OK: Optional[bool] = None


def end_date_ready() -> bool:
    """Whether the `end_date` column exists (enables 'stop recurring going
    forward'). Without it everything else still works; stopping just can't
    persist a cutoff. Cached after first probe."""
    global _END_DATE_OK
    if _END_DATE_OK is not None:
        return _END_DATE_OK
    client = _get_client()
    if not client:
        _END_DATE_OK = True          # JSON fallback stores any key
        return True
    try:
        client.table(EXPENSES_TABLE).select("end_date").limit(1).execute()
        _END_DATE_OK = True
    except Exception:
        _END_DATE_OK = False
    return _END_DATE_OK


END_DATE_MIGRATION = (
    "ALTER TABLE expenses ADD COLUMN IF NOT EXISTS end_date text;"
)


# ── helpers ───────────────────────────────────────────────────────────────────
def _as_float(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _as_bool(v: Any, default: bool) -> bool:
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "yes", "on")


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
    for k in ("owner", "name", "category", "currency", "frequency",
              "payment_method", "on_date", "end_date", "template_id", "note"):
        if k in data and data[k] is not None:
            out[k] = str(data[k]).strip() or None
    if "amount" in data:
        out["amount"] = _as_float(data["amount"])
    if "is_subscription" in data:
        out["is_subscription"] = _as_bool(data["is_subscription"], False)
    if "essential" in data:
        out["essential"] = _as_bool(data["essential"], True)
    if "active" in data:
        out["active"] = _as_bool(data["active"], True)
    if "is_template" in data:
        out["is_template"] = _as_bool(data["is_template"], True)
    if out.get("currency"):
        out["currency"] = out["currency"].upper()
    if out.get("frequency") not in (None, *FREQUENCIES):
        out["frequency"] = "monthly"
    return out


def _decorate(row: dict) -> dict:
    item = {k: row.get(k) for k in EXPENSE_COLUMNS}
    # normalise booleans that may be missing/None from older rows
    item["is_subscription"] = bool(item.get("is_subscription"))
    item["essential"] = True if item.get("essential") is None else bool(item.get("essential"))
    item["active"] = True if item.get("active") is None else bool(item.get("active"))
    # legacy rows (pre-split) were recurring definitions → treat as templates
    item["is_template"] = True if item.get("is_template") is None else bool(item.get("is_template"))
    return item


def _sb_insert(client, payload: dict) -> dict:
    body = dict(payload)
    for _ in range(len(body) + 1):
        try:
            res = client.table(EXPENSES_TABLE).insert(body).execute()
            return (res.data or [body])[0]
        except Exception as e:
            col = _missing_column(e)
            if col and col in body and col not in ("id", "name"):
                print(f"⚠ Expenses: dropping missing column `{col}` — run the ALTER to persist it.")
                body.pop(col)
                continue
            raise
    raise RuntimeError("Expense insert failed after stripping unknown columns.")


def _sb_update(client, eid: str, updates: dict) -> list:
    body = dict(updates)
    for _ in range(len(body) + 1):
        try:
            res = client.table(EXPENSES_TABLE).update(body).eq("id", eid).execute()
            return res.data or []
        except Exception as e:
            col = _missing_column(e)
            if col and col in body:
                print(f"⚠ Expenses: dropping missing column `{col}` on update.")
                body.pop(col)
                continue
            raise
    return []


# ── CRUD ──────────────────────────────────────────────────────────────────────
def list_expenses() -> list[dict]:
    client = _get_client()
    if client:
        rows = (client.table(EXPENSES_TABLE).select("*")
                .order("created_at", desc=True).execute().data) or []
    else:
        rows = sorted(_read_json(_EXP_FILE),
                      key=lambda r: r.get("created_at", ""), reverse=True)
    return [_decorate(r) for r in rows]


def list_templates() -> list[dict]:
    """Recurring definitions — the reminders source."""
    return [e for e in list_expenses() if e.get("is_template")]


def list_log(period: Optional[str] = None) -> list[dict]:
    """Actual logged expenses (one dated occurrence each). Optionally filter to
    a YYYY-MM period by on_date."""
    rows = [e for e in list_expenses() if not e.get("is_template")]
    if period:
        rows = [e for e in rows if str(e.get("on_date") or "")[:7] == period]
    return rows


def log_template_ids(period: str) -> set:
    """template_ids that already have a log entry in this period."""
    return {e.get("template_id") for e in list_log(period) if e.get("template_id")}


def get_expense(eid: str) -> Optional[dict]:
    client = _get_client()
    if client:
        rows = client.table(EXPENSES_TABLE).select("*").eq("id", eid).limit(1).execute().data or []
        return _decorate(rows[0]) if rows else None
    rows = [r for r in _read_json(_EXP_FILE) if r["id"] == eid]
    return _decorate(rows[0]) if rows else None


def _prepare(data: dict) -> dict:
    """A raw expense dict → a complete, defaulted row ready to insert."""
    payload = _clean_payload(data)
    payload["id"] = _new_id()
    payload["created_at"] = _now()
    payload["updated_at"] = payload["created_at"]
    payload.setdefault("name", "Expense")
    payload.setdefault("currency", "INR")
    payload.setdefault("frequency", "monthly")
    payload.setdefault("is_subscription", False)
    payload.setdefault("essential", True)
    payload.setdefault("active", True)
    payload.setdefault("is_template", True)
    return payload


def create_expenses(rows: list[dict]) -> list[dict]:
    """Insert MANY expenses in one round trip — the bulk-import path.

    Each row carries the id we generated, so the caller can link its own records
    to the results without relying on the response order. Falls back to one-by-one
    only if the bulk call fails, so a single bad row can't sink the whole import.
    """
    payloads = [_prepare(r) for r in rows]
    if not payloads:
        return []
    client = _get_client()
    if not client:
        items = _read_json(_EXP_FILE)
        items.extend(payloads)
        _write_json(_EXP_FILE, items)
        return [_decorate(p) for p in payloads]

    body = [dict(p) for p in payloads]
    known = set().union(*(set(b) for b in body))
    for _ in range(len(known) + 1):
        try:
            res = client.table(EXPENSES_TABLE).insert(body).execute()
            return [_decorate(r) for r in (res.data or body)]
        except Exception as e:
            col = _missing_column(e)
            if col and col not in ("id", "name") and any(col in b for b in body):
                print(f"⚠ Expenses: dropping missing column `{col}` on bulk insert.")
                body = [{k: v for k, v in b.items() if k != col} for b in body]
                continue
            break
    # bulk failed for a non-schema reason — degrade to per-row so we still import
    out: list[dict] = []
    for p in payloads:
        try:
            out.append(_decorate(_sb_insert(client, dict(p))))
        except Exception as err:
            print(f"⚠ Expenses: row `{p.get('name')}` failed to insert: {err}")
    return out


def create_expense(data: dict) -> dict:
    payload = _prepare(data)

    client = _get_client()
    if client:
        row = _sb_insert(client, payload)
        return _decorate(row)
    items = _read_json(_EXP_FILE)
    items.append(payload)
    _write_json(_EXP_FILE, items)
    return _decorate(payload)


def update_expense(eid: str, patch: dict) -> Optional[dict]:
    updates = _clean_payload(patch)
    if not updates:
        return get_expense(eid)
    updates["updated_at"] = _now()

    client = _get_client()
    if client:
        rows = _sb_update(client, eid, updates)
        if not rows:
            return None
        return get_expense(eid)
    items = _read_json(_EXP_FILE)
    found = False
    for r in items:
        if r["id"] == eid:
            r.update(updates)
            found = True
            break
    if not found:
        return None
    _write_json(_EXP_FILE, items)
    return get_expense(eid)


def delete_expense(eid: str) -> bool:
    client = _get_client()
    if client:
        res = client.table(EXPENSES_TABLE).delete().eq("id", eid).execute()
        return bool(res.data)
    items = _read_json(_EXP_FILE)
    remaining = [r for r in items if r["id"] != eid]
    if len(remaining) == len(items):
        return False
    _write_json(_EXP_FILE, remaining)
    return True


def payment_methods() -> list[str]:
    """Distinct payment-method labels already used — for the auto-suggest."""
    seen: dict[str, str] = {}
    for r in list_expenses():
        b = (r.get("payment_method") or "").strip()
        if b:
            seen.setdefault(b.lower(), b)
    return sorted(seen.values(), key=lambda s: s.lower())


# ── custom categories ─────────────────────────────────────────────────────────
# The built-in CATEGORIES seed the picker; users can add their own, which persist
# in the app-cache KV so they show up in the picker and as (empty) category cards
# even before any expense uses them. Categories already used on an expense are
# discovered live from the entries, so only the *extra* names need storing.
_CUSTOM_CATS_KEY = "expenses_custom_categories"


def _cats_kv():
    from ..portfolio import store as kv
    return kv


def list_custom_categories() -> list[str]:
    try:
        rec = _cats_kv().cache_get(_CUSTOM_CATS_KEY)
        if rec and isinstance(rec.get("value"), dict):
            return [c for c in rec["value"].get("items", []) if c]
    except Exception:
        pass
    return []


def add_custom_category(name: str) -> list[str]:
    name = (name or "").strip()
    if not name:
        return all_categories()
    existing = list_custom_categories()
    low = {c.lower() for c in existing} | {c.lower() for c in CATEGORIES}
    if name.lower() not in low:
        existing.append(name)
        try:
            _cats_kv().cache_set(_CUSTOM_CATS_KEY, {"items": existing})
        except Exception:
            pass
    return all_categories()


def delete_custom_category(name: str) -> list[str]:
    name = (name or "").strip().lower()
    existing = list_custom_categories()
    kept = [c for c in existing if c.lower() != name]
    if len(kept) != len(existing):
        try:
            _cats_kv().cache_set(_CUSTOM_CATS_KEY, {"items": kept})
        except Exception:
            pass
    return all_categories()


def all_categories() -> list[str]:
    """Built-in suggestions first, then the user's custom ones (deduped)."""
    out = list(CATEGORIES)
    seen = {c.lower() for c in out}
    for c in list_custom_categories():
        if c.lower() not in seen:
            out.append(c)
            seen.add(c.lower())
    return out
