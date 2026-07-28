"""
Other-income store — Supabase-backed, JSON-file fallback.

For income that isn't a salary: dividends, interest, rent, bonuses, capital
gains, gifts, freelance, etc. Two kinds of row share one table:

  • templates  — recurring income definitions (the reminders source)
  • log rows   — actual dated receipts (one occurrence each)

distinguished by `is_template`. The ₹-equivalent (currency-converted +
frequency-normalised) and the splits are computed on read (see
api/other_income/engine.py). Same shape as api/expenses/store.py.

Data model:
  other_income   one row per template OR logged receipt
"""
from __future__ import annotations

import os
import re
import json
import uuid
from datetime import datetime
from typing import Any, Optional

INCOME_TABLE = "other_income"

INCOME_COLUMNS = {
    "id", "owner", "source", "category", "amount", "currency", "frequency",
    "account", "active", "is_template", "template_id", "on_date", "note",
    "created_at", "updated_at",
}

FREQUENCIES = ("weekly", "monthly", "quarterly", "half_yearly", "yearly", "one_time")

# Suggested categories (free-text — these just seed the picker).
CATEGORIES = [
    "Dividend", "Interest", "Rental income", "Bonus / Incentive",
    "Capital gains", "Freelance / Consulting", "Business income",
    "Commission", "Royalty", "Pension", "Gift", "Refund / Reimbursement",
    "Cashback / Rewards", "Maturity payout", "Other",
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
_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "other_income_data")
_INCOME_FILE = os.path.join(_DATA_DIR, "other_income.json")

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
        print(f"✓ Other-income store: Supabase active ({url})")
        return _client
    except Exception as e:
        print(f"⚠  Other-income store: Supabase init failed (using JSON): {e}")
        return None


def is_active() -> bool:
    return _get_client() is not None


def reset_client_cache():
    global _client, _init_attempted, _tables_ok
    _client = None
    _init_attempted = False
    _tables_ok = False


MIGRATION_HINT = (
    "Other-income table not found in Supabase. Run the migration in SUPABASE.md "
    "(\"Other income table\") once in the Supabase SQL editor to create "
    "other_income, then retry."
)


def tables_ready() -> bool:
    global _tables_ok
    if _tables_ok:
        return True
    client = _get_client()
    if not client:
        return True  # JSON fallback is always ready
    try:
        client.table(INCOME_TABLE).select("id").limit(1).execute()
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
    for k in ("owner", "source", "category", "currency", "frequency",
              "account", "on_date", "template_id", "note"):
        if k in data and data[k] is not None:
            out[k] = str(data[k]).strip() or None
    if "amount" in data:
        out["amount"] = _as_float(data["amount"])
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
    item = {k: row.get(k) for k in INCOME_COLUMNS}
    item["active"] = True if item.get("active") is None else bool(item.get("active"))
    item["is_template"] = True if item.get("is_template") is None else bool(item.get("is_template"))
    return item


def _sb_insert(client, payload: dict) -> dict:
    body = dict(payload)
    for _ in range(len(body) + 1):
        try:
            res = client.table(INCOME_TABLE).insert(body).execute()
            return (res.data or [body])[0]
        except Exception as e:
            col = _missing_column(e)
            if col and col in body and col not in ("id", "source"):
                print(f"⚠ Other-income: dropping missing column `{col}` — run the ALTER to persist it.")
                body.pop(col)
                continue
            raise
    raise RuntimeError("Other-income insert failed after stripping unknown columns.")


def _sb_update(client, eid: str, updates: dict) -> list:
    body = dict(updates)
    for _ in range(len(body) + 1):
        try:
            res = client.table(INCOME_TABLE).update(body).eq("id", eid).execute()
            return res.data or []
        except Exception as e:
            col = _missing_column(e)
            if col and col in body:
                print(f"⚠ Other-income: dropping missing column `{col}` on update.")
                body.pop(col)
                continue
            raise
    return []


# ── CRUD ──────────────────────────────────────────────────────────────────────
def list_income() -> list[dict]:
    client = _get_client()
    if client:
        rows = (client.table(INCOME_TABLE).select("*")
                .order("created_at", desc=True).execute().data) or []
    else:
        rows = sorted(_read_json(_INCOME_FILE),
                      key=lambda r: r.get("created_at", ""), reverse=True)
    return [_decorate(r) for r in rows]


def list_templates() -> list[dict]:
    """Recurring definitions — the reminders source."""
    return [e for e in list_income() if e.get("is_template")]


def list_log(period: Optional[str] = None) -> list[dict]:
    """Actual logged receipts (one dated occurrence each). Optionally filter to
    a YYYY-MM period by on_date."""
    rows = [e for e in list_income() if not e.get("is_template")]
    if period:
        rows = [e for e in rows if str(e.get("on_date") or "")[:7] == period]
    return rows


def log_template_ids(period: str) -> set:
    """template_ids that already have a log entry in this period."""
    return {e.get("template_id") for e in list_log(period) if e.get("template_id")}


def get_income(eid: str) -> Optional[dict]:
    client = _get_client()
    if client:
        rows = client.table(INCOME_TABLE).select("*").eq("id", eid).limit(1).execute().data or []
        return _decorate(rows[0]) if rows else None
    rows = [r for r in _read_json(_INCOME_FILE) if r["id"] == eid]
    return _decorate(rows[0]) if rows else None


def _prepare(data: dict) -> dict:
    """A raw income dict → a complete, defaulted row ready to insert."""
    payload = _clean_payload(data)
    payload["id"] = _new_id()
    payload["created_at"] = _now()
    payload["updated_at"] = payload["created_at"]
    payload.setdefault("source", "Income")
    payload.setdefault("currency", "INR")
    payload.setdefault("frequency", "monthly")
    payload.setdefault("active", True)
    payload.setdefault("is_template", True)
    return payload


def create_incomes(rows: list[dict]) -> list[dict]:
    """Insert MANY receipts in one round trip (bulk statement import). Mirrors
    api/expenses/store.create_expenses — ids are ours, so results map back."""
    payloads = [_prepare(r) for r in rows]
    if not payloads:
        return []
    client = _get_client()
    if not client:
        items = _read_json(_INCOME_FILE)
        items.extend(payloads)
        _write_json(_INCOME_FILE, items)
        return [_decorate(p) for p in payloads]

    body = [dict(p) for p in payloads]
    known = set().union(*(set(b) for b in body))
    for _ in range(len(known) + 1):
        try:
            res = client.table(INCOME_TABLE).insert(body).execute()
            return [_decorate(r) for r in (res.data or body)]
        except Exception as e:
            col = _missing_column(e)
            if col and col not in ("id", "source") and any(col in b for b in body):
                print(f"⚠ Other-income: dropping missing column `{col}` on bulk insert.")
                body = [{k: v for k, v in b.items() if k != col} for b in body]
                continue
            break
    out: list[dict] = []
    for p in payloads:
        try:
            out.append(_decorate(_sb_insert(client, dict(p))))
        except Exception as err:
            print(f"⚠ Other-income: row `{p.get('source')}` failed to insert: {err}")
    return out


def create_income(data: dict) -> dict:
    payload = _prepare(data)

    client = _get_client()
    if client:
        row = _sb_insert(client, payload)
        return _decorate(row)
    items = _read_json(_INCOME_FILE)
    items.append(payload)
    _write_json(_INCOME_FILE, items)
    return _decorate(payload)


def update_income(eid: str, patch: dict) -> Optional[dict]:
    updates = _clean_payload(patch)
    if not updates:
        return get_income(eid)
    updates["updated_at"] = _now()

    client = _get_client()
    if client:
        rows = _sb_update(client, eid, updates)
        if not rows:
            return None
        return get_income(eid)
    items = _read_json(_INCOME_FILE)
    found = False
    for r in items:
        if r["id"] == eid:
            r.update(updates)
            found = True
            break
    if not found:
        return None
    _write_json(_INCOME_FILE, items)
    return get_income(eid)


def delete_income(eid: str) -> bool:
    client = _get_client()
    if client:
        res = client.table(INCOME_TABLE).delete().eq("id", eid).execute()
        return bool(res.data)
    items = _read_json(_INCOME_FILE)
    remaining = [r for r in items if r["id"] != eid]
    if len(remaining) == len(items):
        return False
    _write_json(_INCOME_FILE, remaining)
    return True


def accounts() -> list[str]:
    """Distinct account labels already used — for the auto-suggest."""
    seen: dict[str, str] = {}
    for r in list_income():
        b = (r.get("account") or "").strip()
        if b:
            seen.setdefault(b.lower(), b)
    return sorted(seen.values(), key=lambda s: s.lower())
