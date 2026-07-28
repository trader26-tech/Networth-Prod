"""
Supabase store for liabilities (loans) — JSON-file fallback for dev.

One flat table — `loans` — one row per loan (home / personal / vehicle / foreign).
Everything derived (INR value of a foreign-currency balance, progress %, monthly
EMI in INR, totals) is computed on read in api/loans/engine.py.

Mirrors the FD/bonds stores: Supabase-primary with a JSON-file fallback, graceful
if the table isn't migrated yet, and self-healing writes that drop columns the
table doesn't have so a missing ALTER never crashes a save.
See SUPABASE.md → "Liabilities (loans) table".
"""
from __future__ import annotations

import os
import re
import json
import uuid
from datetime import datetime
from typing import Any, Optional

TABLE = "loans"

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "loans_data")
_FILE = os.path.join(_DATA_DIR, "loans.json")

# Every column the app reads/writes. Kept as a whitelist so a stray key never
# reaches Supabase and reads always return a stable shape.
COLUMNS = (
    "id", "owner", "lender", "loan_type", "account_no", "currency",
    "original_amount", "outstanding_principal", "interest_rate",
    "emi_amount", "emi_frequency", "start_date", "maturity_date",
    "next_installment_date", "installments_paid", "installments_remaining",
    "status", "closed_on", "note", "created_at", "updated_at",
)
LOAN_TYPES = ("Home Loan", "Personal Loan", "Vehicle Loan", "Flexible Loan",
              "Gold Loan", "Education Loan", "Business Loan", "Credit Line", "Other")
FREQUENCIES = ("monthly", "quarterly", "half_yearly", "yearly")

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
        print(f"✓ Loans store: Supabase active ({url})")
        return _client
    except Exception as e:
        print(f"⚠  Loans store: Supabase init failed (using JSON): {e}")
        return None


def reset_client_cache():
    global _client, _init_attempted, _tables_ok
    _client = None
    _init_attempted = False
    _tables_ok = False


MIGRATION_HINT = (
    "Loans table not found in Supabase. Run the migration in SUPABASE.md "
    "(\"Liabilities (loans) table\") once in the Supabase SQL editor to create "
    "loans, then retry."
)


def tables_ready() -> bool:
    # Always ready: if the Supabase table isn't migrated yet we transparently use
    # the JSON-file fallback (see _use_supabase), so nothing 503s.
    return True


def _use_supabase() -> bool:
    """Supabase only when configured AND the loans table exists; otherwise the
    JSON file. Positive result cached; a negative is re-probed so creating the
    table later (via the migration) is picked up without a restart — and until
    then loans persist to JSON instead of erroring."""
    global _tables_ok
    client = _get_client()
    if not client:
        return False
    if _tables_ok:
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
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _as_int(v: Any) -> Optional[int]:
    f = _as_float(v)
    return int(round(f)) if f is not None else None


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


_MISS_RE = re.compile(r"column [\"']?([a-zA-Z0-9_]+)[\"']?", re.I)


def _missing_column(err: Exception) -> Optional[str]:
    m = _MISS_RE.search(str(err))
    return m.group(1) if m and "does not exist" in str(err).lower() else None


def _clean(data: dict) -> dict:
    out: dict[str, Any] = {}
    for k in ("owner", "lender", "loan_type", "account_no", "currency",
              "emi_frequency", "start_date", "maturity_date",
              "next_installment_date", "status", "closed_on", "note"):
        if k in data and data[k] is not None:
            out[k] = str(data[k]).strip() or None
    for k in ("original_amount", "outstanding_principal", "interest_rate", "emi_amount"):
        if k in data:
            out[k] = _as_float(data[k])
    for k in ("installments_paid", "installments_remaining"):
        if k in data:
            out[k] = _as_int(data[k])
    if out.get("currency"):
        out["currency"] = out["currency"].upper()[:3]
    if out.get("emi_frequency") not in (None, *FREQUENCIES):
        out["emi_frequency"] = "monthly"
    if out.get("status") not in (None, "active", "closed"):
        out["status"] = "active"
    return out


def _decorate(row: dict) -> dict:
    return {k: row.get(k) for k in COLUMNS}


def _sb_insert(client, payload: dict) -> dict:
    body = dict(payload)
    for _ in range(len(body) + 1):
        try:
            res = client.table(TABLE).insert(body).execute()
            return (res.data or [body])[0]
        except Exception as e:
            col = _missing_column(e)
            if col and col in body and col not in ("id",):
                print(f"⚠ Loans: dropping missing column `{col}` — run the ALTER to persist it.")
                body.pop(col)
                continue
            raise
    raise RuntimeError("Loan insert failed after stripping unknown columns.")


def _sb_update(client, lid: str, updates: dict) -> list:
    body = dict(updates)
    for _ in range(len(body) + 1):
        try:
            res = client.table(TABLE).update(body).eq("id", lid).execute()
            return res.data or []
        except Exception as e:
            col = _missing_column(e)
            if col and col in body:
                print(f"⚠ Loans: dropping missing column `{col}` on update.")
                body.pop(col)
                continue
            raise
    return []


# ── CRUD ──────────────────────────────────────────────────────────────────────
def list_loans() -> list[dict]:
    if _use_supabase():
        client = _get_client()
        rows = (client.table(TABLE).select("*").order("created_at", desc=True).execute().data) or []
    else:
        rows = sorted(_read_json(), key=lambda r: r.get("created_at", ""), reverse=True)
    return [_decorate(r) for r in rows]


def get_loan(lid: str) -> Optional[dict]:
    if _use_supabase():
        client = _get_client()
        rows = client.table(TABLE).select("*").eq("id", lid).limit(1).execute().data or []
        return _decorate(rows[0]) if rows else None
    rows = [r for r in _read_json() if r.get("id") == lid]
    return _decorate(rows[0]) if rows else None


def create_loan(data: dict) -> dict:
    payload = _clean(data)
    payload["id"] = uuid.uuid4().hex[:12]
    payload["created_at"] = _now()
    payload["updated_at"] = payload["created_at"]
    payload.setdefault("currency", "INR")
    payload.setdefault("emi_frequency", "monthly")
    payload.setdefault("status", "active")
    payload.setdefault("lender", "Loan")
    if _use_supabase():
        return _decorate(_sb_insert(_get_client(), payload))
    data_all = _read_json()
    data_all.append(payload)
    _write_json(data_all)
    return _decorate(payload)


def update_loan(lid: str, patch: dict) -> Optional[dict]:
    updates = _clean(patch)
    updates["updated_at"] = _now()
    if _use_supabase():
        rows = _sb_update(_get_client(), lid, updates)
        return _decorate(rows[0]) if rows else None
    data_all = _read_json()
    hit = None
    for r in data_all:
        if r.get("id") == lid:
            r.update(updates)
            hit = r
    if hit is None:
        return None
    _write_json(data_all)
    return _decorate(hit)


def delete_loan(lid: str) -> bool:
    if _use_supabase():
        res = _get_client().table(TABLE).delete().eq("id", lid).execute()
        return bool(res.data)
    data_all = _read_json()
    kept = [r for r in data_all if r.get("id") != lid]
    if len(kept) == len(data_all):
        return False
    _write_json(kept)
    return True


def lenders() -> list[str]:
    seen = []
    for r in list_loans():
        b = (r.get("lender") or "").strip()
        if b and b not in seen:
            seen.append(b)
    return sorted(seen)


# ── per-installment paid / pending / unpaid status ──────────────────────────────
# The repayment ladder is *computed* from each loan's EMI + dates, so there's no
# row to hang a status on. We keep a tiny side table keyed by (loan_id, date): one
# row per installment the user has marked. Anything unmarked defaults to "pending"
# (i.e. due / awaiting), mirroring the bonds calendar. Supabase-backed with a
# JSON-file fallback; degrades gracefully (never crashes) if the table is absent.
STATUS_TABLE = "loan_payment_status"
_STATUS_FILE = os.path.join(_DATA_DIR, "loan_payment_status.json")
_STATUSES = ("pending", "paid", "unpaid")
_status_table_ok: Optional[bool] = None


def _coerce_status(v) -> str:
    s = str(v or "").strip().lower()
    return s if s in _STATUSES else "pending"


def _status_use_supabase() -> bool:
    """Supabase only when configured AND the loan_payment_status table exists.
    Positive result cached; a negative is re-probed each call so creating the
    table later (via the migration) is picked up without a restart — and, until
    then, marks persist to the JSON file instead of silently vanishing."""
    global _status_table_ok
    client = _get_client()
    if not client:
        return False
    if _status_table_ok:
        return True
    try:
        client.table(STATUS_TABLE).select("loan_id").limit(1).execute()
        _status_table_ok = True
        return True
    except Exception:
        return False


def _read_status_json() -> list:
    try:
        with open(_STATUS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _write_status_json(data: list) -> None:
    os.makedirs(_DATA_DIR, exist_ok=True)
    tmp = _STATUS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)
    os.replace(tmp, _STATUS_FILE)


def list_payment_status() -> list[dict]:
    """All marked installments: [{loan_id, date, status}]. Unmarked → pending."""
    if _status_use_supabase():
        try:
            rows = _get_client().table(STATUS_TABLE).select("loan_id,date,status").execute().data or []
        except Exception:
            rows = _read_status_json()          # transient Supabase blip → local copy
    else:
        rows = _read_status_json()              # no client / table not migrated yet
    out = []
    for r in rows:
        lid, dt = r.get("loan_id"), (r.get("date") or "")[:10]
        if not lid or not dt:
            continue
        out.append({"loan_id": str(lid), "date": dt, "status": _coerce_status(r.get("status"))})
    return out


def set_payment_status(loan_id: str, date: str, status: str) -> dict:
    """Upsert one installment's status, keyed by (loan_id, date). Marking back to
    the default 'pending' removes the row so the table stays sparse. Supabase-primary
    with a JSON-file fallback so a mark is NEVER lost when the table is missing or
    Supabase is briefly unreachable."""
    global _status_table_ok
    loan_id = str(loan_id or "").strip()
    date = str(date or "")[:10]
    status = _coerce_status(status)
    row = {"loan_id": loan_id, "date": date, "status": status}
    if _status_use_supabase():
        try:
            client = _get_client()
            if status == "pending":
                client.table(STATUS_TABLE).delete().eq("loan_id", loan_id).eq("date", date).execute()
            else:
                client.table(STATUS_TABLE).upsert(row, on_conflict="loan_id,date").execute()
            return row
        except Exception:
            _status_table_ok = None             # stop trusting the table; persist to JSON below
    data = _read_status_json()
    data = [r for r in data if not (str(r.get("loan_id")) == loan_id and (r.get("date") or "")[:10] == date)]
    if status != "pending":
        data.append(row)
    _write_status_json(data)
    return row
