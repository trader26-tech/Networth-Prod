"""
Supabase store for bond holdings (JSON-file fallback for dev).

One flat table — `bonds` — one row per bond holding (per member / broker).
Income, YTM, payout schedule and maturity ladder are computed on read in
api/bonds/engine.py. See SUPABASE.md → "Bonds table".
"""
from __future__ import annotations

import os
import json
import uuid
from datetime import datetime
from typing import Optional

TABLE = "bonds"

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "bonds_data")
_FILE = os.path.join(_DATA_DIR, "bonds.json")

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
        print(f"✓ Bonds store: Supabase active ({url})")
        return _client
    except Exception as e:
        print(f"⚠  Bonds store: Supabase init failed (using JSON): {e}")
        return None


def reset_client_cache():
    global _client, _init_attempted, _tables_ok
    _client = None
    _init_attempted = False
    _tables_ok = False


MIGRATION_HINT = (
    "Bonds table not found in Supabase. Run the migration in SUPABASE.md "
    "(\"Bonds table\") once in the Supabase SQL editor to create bonds, then retry."
)


def tables_ready() -> bool:
    global _tables_ok
    if _tables_ok:
        return True
    client = _get_client()
    if not client:
        return True
    try:
        # select the v2 columns too, so a table that predates the schedule
        # migration reports "not ready" and surfaces the migration hint.
        client.table(TABLE).select("id,schedule,ytm_input,first_payment_date,sellable_on").limit(1).execute()
        _tables_ok = True
        return True
    except Exception:
        return False


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


_FIELDS = ("owner", "broker", "issuer", "bond_type", "isin", "rating", "tax_free",
           "face_value", "quantity", "buy_price", "coupon_rate", "coupon_freq",
           "repayment_type", "purchase_date", "first_payment_date", "maturity_date",
           "redemption_value", "ytm_input", "schedule", "note", "sellable_on")
_NUMERIC = ("face_value", "quantity", "buy_price", "coupon_rate", "redemption_value", "ytm_input")


def _clean(payload: dict) -> dict:
    out = {}
    for k in _FIELDS:
        if k in payload:
            out[k] = payload[k]
    for n in _NUMERIC:
        if n in out and out[n] is not None and out[n] != "":
            try:
                out[n] = float(out[n])
            except (ValueError, TypeError):
                out[n] = None
    if "tax_free" in out:
        out["tax_free"] = bool(out["tax_free"])
    # schedule is a JSON list of {date, interest, principal}; coerce amounts to float
    if "schedule" in out:
        sched = out["schedule"]
        if isinstance(sched, list):
            cleaned = []
            for r in sched:
                if not isinstance(r, dict) or not r.get("date"):
                    continue
                cleaned.append({
                    "date": str(r.get("date"))[:10],
                    "interest": float(r.get("interest") or 0),
                    "principal": float(r.get("principal") or 0),
                })
            out["schedule"] = cleaned
        else:
            out["schedule"] = None
    return out


def list_bonds() -> list[dict]:
    client = _get_client()
    if client:
        return client.table(TABLE).select("*").order("owner").execute().data or []
    return _read_json()


def add_bond(payload: dict) -> dict:
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


def update_bond(bond_id: str, payload: dict) -> Optional[dict]:
    patch = _clean(payload)
    client = _get_client()
    if client:
        res = client.table(TABLE).update(patch).eq("id", bond_id).execute()
        return (res.data or [None])[0]
    data = _read_json()
    hit = None
    for r in data:
        if r.get("id") == bond_id:
            r.update(patch)
            hit = r
    if hit:
        _write_json(data)
    return hit


def delete_bond(bond_id: str) -> int:
    client = _get_client()
    if client:
        res = client.table(TABLE).delete().eq("id", bond_id).execute()
        return len(res.data or [])
    data = _read_json()
    keep = [r for r in data if r.get("id") != bond_id]
    n = len(data) - len(keep)
    _write_json(keep)
    return n


# ── per-payment received/pending/not-received status ────────────────────────────
# Bond payouts are *computed* from each bond's schedule, so we can't store a status
# on the payment itself. Instead we keep a tiny side table keyed by (bond_id, date):
# one row per payment the user has marked. Anything unmarked defaults to "pending"
# (i.e. expected / awaiting), mirroring the Dividends calendar. Supabase-backed with
# a JSON-file fallback; degrades gracefully (never crashes) if the table is absent.
STATUS_TABLE = "bond_payment_status"
_STATUS_FILE = os.path.join(_DATA_DIR, "bond_payment_status.json")
_STATUSES = ("pending", "received", "not_received")
_status_table_ok: Optional[bool] = None


def _coerce_status(v) -> str:
    s = str(v or "").strip().lower()
    return s if s in _STATUSES else "pending"


def _status_use_supabase() -> bool:
    """Supabase only when configured AND the bond_payment_status table exists.
    Positive result is cached; a negative is re-probed each call so creating the
    table later (via the migration) is picked up without a restart — and, until
    then, statuses persist to the JSON file instead of silently vanishing."""
    global _status_table_ok
    client = _get_client()
    if not client:
        return False
    if _status_table_ok:
        return True
    try:
        client.table(STATUS_TABLE).select("bond_id").limit(1).execute()
        _status_table_ok = True
        return True
    except Exception:
        return False


def list_payment_status() -> list[dict]:
    """All marked payments: [{bond_id, date, status}]. Unmarked → pending (implied)."""
    if _status_use_supabase():
        try:
            rows = _get_client().table(STATUS_TABLE).select("bond_id,date,status").execute().data or []
        except Exception:
            rows = _read_status_json()          # transient Supabase blip → local copy
    else:
        rows = _read_status_json()              # no client / table not migrated yet
    out = []
    for r in rows:
        bid, dt = r.get("bond_id"), (r.get("date") or "")[:10]
        if not bid or not dt:
            continue
        out.append({"bond_id": str(bid), "date": dt, "status": _coerce_status(r.get("status"))})
    return out


def set_payment_status(bond_id: str, date: str, status: str) -> dict:
    """Upsert one payment's status, keyed by (bond_id, date). Marking a payment
    back to the default 'pending' removes the row so the table stays sparse.
    Supabase-primary with a JSON-file fallback so a mark is NEVER lost when the
    table is missing or Supabase is briefly unreachable."""
    global _status_table_ok
    bond_id = str(bond_id or "").strip()
    date = str(date or "")[:10]
    status = _coerce_status(status)
    row = {"bond_id": bond_id, "date": date, "status": status}
    if _status_use_supabase():
        try:
            client = _get_client()
            if status == "pending":
                client.table(STATUS_TABLE).delete().eq("bond_id", bond_id).eq("date", date).execute()
            else:
                client.table(STATUS_TABLE).upsert(row, on_conflict="bond_id,date").execute()
            return row
        except Exception:
            _status_table_ok = None             # stop trusting the table; persist to JSON below
    data = _read_status_json()
    data = [r for r in data if not (str(r.get("bond_id")) == bond_id and (r.get("date") or "")[:10] == date)]
    if status != "pending":
        data.append(row)
    _write_status_json(data)
    return row


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


# ── Bond SIPs (a planned recurring/one-off purchase you log the real details of
#    once it executes). Stored in the durable app_cache KV so they persist across
#    deploys (same pattern as F&O statements/tradebooks). ─────────────────────────
_SIPS_KEY = "bond_sips"


def get_sips() -> list:
    """All bond SIPs, newest expected-date first."""
    try:
        from ..portfolio import store as kv
        rec = kv.cache_get(_SIPS_KEY)
        blob = rec.get("value") if rec and isinstance(rec.get("value"), list) else []
        return sorted(blob, key=lambda s: (s.get("expected_date") or ""))
    except Exception:
        return []


def _save_sips(items: list) -> None:
    from ..portfolio import store as kv
    kv.cache_set(_SIPS_KEY, items)


def add_sip(sip: dict) -> dict:
    row = {
        "id": uuid.uuid4().hex[:10],
        "owner": (sip.get("owner") or "").strip(),
        "total": float(sip.get("total") or 0),
        "expected_date": (sip.get("expected_date") or "")[:10],
        "note": (sip.get("note") or "").strip(),
        "splits": [{"name": (s.get("name") or "").strip(), "amount": float(s.get("amount") or 0)}
                   for s in (sip.get("splits") or []) if (s.get("name") or "").strip()],
        "status": "pending",
        "created_at": datetime.now().isoformat(),
        "logged_at": None,
    }
    items = get_sips()
    items.append(row)
    _save_sips(items)
    return row


def update_sip(sip_id: str, patch: dict) -> Optional[dict]:
    items = get_sips()
    hit = None
    for s in items:
        if s.get("id") == sip_id:
            for k in ("owner", "total", "expected_date", "note", "splits", "status", "logged_at"):
                if k in patch:
                    s[k] = patch[k]
            hit = s
    if hit:
        _save_sips(items)
    return hit


def delete_sip(sip_id: str) -> bool:
    items = get_sips()
    kept = [s for s in items if s.get("id") != sip_id]
    if len(kept) != len(items):
        _save_sips(kept)
        return True
    return False


# ── Bank-statement upload history (durable KV) ──────────────────────────────────
# One row per reconcile-confirm: what file, who uploaded it, the dates it covers,
# and how many interest payments it marked received. Durable via app_cache so the
# audit trail survives redeploys/replicas (same store as SIPs).
_UPLOADS_KEY = "bond_statement_uploads"


def list_uploads() -> list:
    """Statement uploads, newest first."""
    try:
        from ..portfolio import store as kv
        rec = kv.cache_get(_UPLOADS_KEY)
        blob = rec.get("value") if rec and isinstance(rec.get("value"), list) else []
        return sorted(blob, key=lambda u: (u.get("uploaded_at") or ""), reverse=True)
    except Exception:
        return []


def add_upload(rec: dict) -> dict:
    row = {
        "id": uuid.uuid4().hex[:10],
        "filename": (rec.get("filename") or "").strip() or "statement",
        "uploaded_by": (rec.get("uploaded_by") or "").strip() or None,
        "uploaded_at": datetime.now().isoformat(),
        "date_from": (rec.get("date_from") or "")[:10] or None,
        "date_to": (rec.get("date_to") or "")[:10] or None,
        "owners": rec.get("owners") or [],
        "credits": int(rec.get("credits") or 0),
        "marked": int(rec.get("marked") or 0),
        "amount": round(float(rec.get("amount") or 0), 2),
    }
    from ..portfolio import store as kv
    items = list_uploads()
    items.append(row)
    kv.cache_set(_UPLOADS_KEY, items[-200:])   # keep the last 200
    return row


def delete_upload(upload_id: str) -> bool:
    from ..portfolio import store as kv
    items = list_uploads()
    kept = [u for u in items if u.get("id") != upload_id]
    if len(kept) != len(items):
        kv.cache_set(_UPLOADS_KEY, kept)
        return True
    return False
