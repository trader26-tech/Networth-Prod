"""
Apartment-unit store — Supabase-backed, JSON-file fallback for offline dev.
Mirrors api/land/store.py, but each unit also has a **monthly_rent**, and the
returns blend **appreciation + rental yield** into an overall CAGR.

Data model (2NF):
  apartment_units       one row per flat/unit
  apartment_documents   one row per uploaded file, FK apartment_id → apartment_units.id

Files live in the private Supabase Storage bucket `apartment-docs`. All return
metrics (rate/sq ft, yields, CAGRs) are **derived at read time** — never stored.
"""
from __future__ import annotations

import os
import re
import json
import uuid
from datetime import datetime, date
from typing import Any, Optional

from api.documents.store import DocumentStore

UNITS_TABLE = "apartment_units"
DOCS_TABLE = "apartment_documents"
TENANTS_TABLE = "apartment_tenants"
BUCKET = "apartment-docs"

# Columns that physically exist on apartment_tenants (everything else derived).
TENANT_COLUMNS = {
    "id", "apartment_id", "name", "phone", "advance_paid",
    "move_in_date", "move_out_date", "notes", "created_at", "updated_at",
}

# Columns that physically exist on apartment_units (everything else is derived).
UNIT_COLUMNS = {
    "id", "name", "owner", "location", "area_sqft", "bought_date", "bought_price",
    "current_estimated_price", "after_brokerage_price", "monthly_rent", "notes", "sellable_on",
    "created_at", "updated_at",
}


def _missing_column(err: Exception) -> Optional[str]:
    """Return the column name an error reports as missing (Postgres / PostgREST),
    so writes can drop a not-yet-migrated optional column and retry."""
    msg = str(err)
    m = re.search(r"column [\"']?[\w]+\.?([\w]+)[\"']? does not exist", msg)
    if m:
        return m.group(1)
    m = re.search(r"[Cc]ould not find the '([\w]+)' column", msg)
    if m:
        return m.group(1)
    return None

# ── local-fallback paths ──────────────────────────────────────────────────────
_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "apartments_data")
_UNITS_FILE = os.path.join(_DATA_DIR, "units.json")
_DOCS_FILE = os.path.join(_DATA_DIR, "documents.json")
_TENANTS_FILE = os.path.join(_DATA_DIR, "tenants.json")
_DOCS_DIR = os.path.join(_DATA_DIR, "docs")

_client = None
_init_attempted = False
_bucket_ready = False
_tables_ok = False


# ══════════════════════════════════════════════════════════════════════════════
# Supabase client
# ══════════════════════════════════════════════════════════════════════════════
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
        print(f"✓ Apartments store: Supabase active ({url})")
        return _client
    except Exception as e:
        print(f"⚠  Apartments store: Supabase init failed (using JSON): {e}")
        return None


def is_active() -> bool:
    return _get_client() is not None


def reset_client_cache():
    global _client, _init_attempted, _bucket_ready, _tables_ok
    _client = None
    _init_attempted = False
    _bucket_ready = False
    _tables_ok = False


MIGRATION_HINT = (
    "Apartment tables not found in Supabase. Run the migration in SUPABASE.md "
    "(\"Apartment net-worth tables\") once in the Supabase SQL editor to create "
    "apartment_units + apartment_documents, then retry."
)


def tables_ready() -> bool:
    global _tables_ok
    if _tables_ok:
        return True
    client = _get_client()
    if not client:
        return True
    try:
        client.table(UNITS_TABLE).select("id").limit(1).execute()
        _tables_ok = True
        return True
    except Exception:
        return False


def _ensure_bucket(client) -> None:
    global _bucket_ready
    if _bucket_ready:
        return
    try:
        client.storage.create_bucket(BUCKET, options={"public": False})
    except Exception:
        pass
    _bucket_ready = True


# ══════════════════════════════════════════════════════════════════════════════
# Derived metrics — appreciation + rent → overall CAGR (computed, never stored)
# ══════════════════════════════════════════════════════════════════════════════
def _parse_date(s: Any) -> Optional[date]:
    if not s:
        return None
    if isinstance(s, date):
        return s
    txt = str(s).strip()[:10]
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(txt, fmt).date()
        except ValueError:
            continue
    return None


def _cagr(start: Optional[float], end: Optional[float], years: float) -> Optional[float]:
    if not start or start <= 0 or end is None or end <= 0 or years <= 0:
        return None
    try:
        return (end / start) ** (1.0 / years) - 1.0
    except (ValueError, ZeroDivisionError, OverflowError):
        return None


def _as_float(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def compute_metrics(u: dict, today: Optional[date] = None) -> dict:
    """Derived numbers for a unit — price appreciation + rental yield + overall CAGR."""
    today = today or date.today()
    bought = _as_float(u.get("bought_price"))
    current = _as_float(u.get("current_estimated_price"))
    net_sell = _as_float(u.get("after_brokerage_price"))
    area = _as_float(u.get("area_sqft"))
    rent_m = _as_float(u.get("monthly_rent"))
    bdate = _parse_date(u.get("bought_date"))
    years = ((today - bdate).days / 365.25) if bdate else 0.0

    gain = (current - bought) if (current is not None and bought is not None) else None
    gain_pct = (gain / bought) if (gain is not None and bought) else None
    has_area = area is not None and area > 0

    annual_rent = (rent_m * 12) if rent_m else None
    rent_yield = (annual_rent / current) if (annual_rent and current) else None              # gross, on present value
    rent_yield_on_cost = (annual_rent / bought) if (annual_rent and bought) else None        # vs original cost

    appreciation = _cagr(bought, current, years)
    # Overall CAGR = price-appreciation CAGR + rental yield-on-cost (income relative
    # to what you paid), so it's dimensionally consistent with the appreciation CAGR.
    total_cagr: Optional[float] = None
    if appreciation is not None:
        total_cagr = appreciation + (rent_yield_on_cost or 0.0)
    elif rent_yield_on_cost is not None:
        total_cagr = rent_yield_on_cost

    return {
        "holding_years": round(years, 2) if years else None,
        "gain": round(gain, 2) if gain is not None else None,
        "gain_pct": gain_pct,
        "cagr": appreciation,                 # price appreciation only
        "net_cagr": _cagr(bought, net_sell, years),
        "annual_rent": round(annual_rent, 2) if annual_rent is not None else None,
        "rent_yield": rent_yield,             # annual rent ÷ present value
        "rent_yield_on_cost": rent_yield_on_cost,
        "total_cagr": total_cagr,             # appreciation + rent — the headline number
        "rate_per_sqft": round(current / area, 2) if (has_area and current is not None) else None,
        "bought_rate_per_sqft": round(bought / area, 2) if (has_area and bought is not None) else None,
    }


# ══════════════════════════════════════════════════════════════════════════════
# JSON-file fallback helpers
# ══════════════════════════════════════════════════════════════════════════════
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


# ══════════════════════════════════════════════════════════════════════════════
# Units CRUD
# ══════════════════════════════════════════════════════════════════════════════
def _clean_unit_payload(data: dict) -> dict:
    out: dict[str, Any] = {}
    for k in ("name", "owner", "location", "notes", "bought_date", "sellable_on"):
        if k in data and data[k] is not None:
            out[k] = str(data[k]).strip() or None
    for k in ("bought_price", "current_estimated_price", "after_brokerage_price",
              "area_sqft", "monthly_rent"):
        if k in data:
            out[k] = _as_float(data[k])
    return out


def _decorate(unit: dict, docs: list[dict], tenants: Optional[list[dict]] = None) -> dict:
    u = {k: unit.get(k) for k in UNIT_COLUMNS}
    u["documents"] = docs
    tenants = tenants or []
    u["tenants"] = tenants
    # current tenant = active (no move-out), latest move-in first
    actives = [t for t in tenants if t.get("active")]
    u["current_tenant"] = actives[0] if actives else None
    u.update(compute_metrics(unit))
    return u


def _sb_insert(client, payload: dict, table: str = UNITS_TABLE, protect: tuple = ("id", "name")) -> dict:
    body = dict(payload)
    for _ in range(len(body) + 1):
        try:
            res = client.table(table).insert(body).execute()
            return (res.data or [body])[0]
        except Exception as e:
            col = _missing_column(e)
            if col and col in body and col not in protect:
                print(f"⚠ Apartments: dropping missing column `{col}` — run "
                      f"`ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} ...;` to persist it.")
                body.pop(col)
                continue
            raise
    raise RuntimeError(f"Insert into {table} failed after stripping unknown columns.")


def _sb_update(client, uid: str, updates: dict, table: str = UNITS_TABLE) -> list:
    body = dict(updates)
    for _ in range(len(body) + 1):
        try:
            res = client.table(table).update(body).eq("id", uid).execute()
            return res.data or []
        except Exception as e:
            col = _missing_column(e)
            if col and col in body:
                print(f"⚠ Apartments: dropping missing column `{col}` on update.")
                body.pop(col)
                continue
            raise
    return []


def list_units() -> list[dict]:
    client = _get_client()
    if client:
        rows = (client.table(UNITS_TABLE).select("*")
                .order("created_at", desc=True).execute().data) or []
        docs = (client.table(DOCS_TABLE).select("*")
                .order("created_at", desc=True).execute().data) or []
        tenants = _all_tenants_remote(client)
    else:
        rows = sorted(_read_json(_UNITS_FILE),
                      key=lambda r: r.get("created_at", ""), reverse=True)
        docs = _read_json(_DOCS_FILE)
        tenants = _read_json(_TENANTS_FILE)
    by_unit: dict[str, list] = {}
    for d in docs:
        by_unit.setdefault(d.get("apartment_id"), []).append(_public_doc(d))
    by_tenant: dict[str, list] = {}
    for t in tenants:
        by_tenant.setdefault(t.get("apartment_id"), []).append(_public_tenant(t))
    for k in by_tenant:
        by_tenant[k] = _sort_tenants(by_tenant[k])
    return [_decorate(r, by_unit.get(r["id"], []), by_tenant.get(r["id"], [])) for r in rows]


def get_unit(uid: str) -> Optional[dict]:
    client = _get_client()
    if client:
        rows = client.table(UNITS_TABLE).select("*").eq("id", uid).limit(1).execute().data or []
        if not rows:
            return None
        docs = client.table(DOCS_TABLE).select("*").eq("apartment_id", uid).execute().data or []
        tenants = _tenants_for_remote(client, uid)
        return _decorate(rows[0], [_public_doc(d) for d in docs],
                         _sort_tenants([_public_tenant(t) for t in tenants]))
    rows = [r for r in _read_json(_UNITS_FILE) if r["id"] == uid]
    if not rows:
        return None
    docs = [_public_doc(d) for d in _read_json(_DOCS_FILE) if d.get("apartment_id") == uid]
    tenants = _sort_tenants([_public_tenant(t) for t in _read_json(_TENANTS_FILE)
                             if t.get("apartment_id") == uid])
    return _decorate(rows[0], docs, tenants)


def create_unit(data: dict) -> dict:
    payload = _clean_unit_payload(data)
    payload["id"] = _new_id()
    payload["created_at"] = _now()
    payload["updated_at"] = payload["created_at"]
    payload.setdefault("name", "Untitled unit")

    client = _get_client()
    if client:
        row = _sb_insert(client, payload)
        return _decorate(row, [])
    units = _read_json(_UNITS_FILE)
    units.append(payload)
    _write_json(_UNITS_FILE, units)
    return _decorate(payload, [])


def update_unit(uid: str, patch: dict) -> Optional[dict]:
    updates = _clean_unit_payload(patch)
    if not updates:
        return get_unit(uid)
    updates["updated_at"] = _now()

    client = _get_client()
    if client:
        rows = _sb_update(client, uid, updates)
        if not rows:
            return None
        return get_unit(uid)
    units = _read_json(_UNITS_FILE)
    found = False
    for r in units:
        if r["id"] == uid:
            r.update(updates)
            found = True
            break
    if not found:
        return None
    _write_json(_UNITS_FILE, units)
    return get_unit(uid)


def delete_unit(uid: str) -> bool:
    for d in _raw_docs(uid):
        _delete_doc_file(d)
    client = _get_client()
    if client:
        client.table(DOCS_TABLE).delete().eq("apartment_id", uid).execute()
        try:
            client.table(TENANTS_TABLE).delete().eq("apartment_id", uid).execute()
        except Exception:
            pass  # FK ON DELETE CASCADE will handle it if the table exists
        res = client.table(UNITS_TABLE).delete().eq("id", uid).execute()
        return bool(res.data)
    units = _read_json(_UNITS_FILE)
    remaining = [r for r in units if r["id"] != uid]
    if len(remaining) == len(units):
        return False
    _write_json(_UNITS_FILE, remaining)
    docs = [d for d in _read_json(_DOCS_FILE) if d.get("apartment_id") != uid]
    _write_json(_DOCS_FILE, docs)
    tenants = [t for t in _read_json(_TENANTS_FILE) if t.get("apartment_id") != uid]
    _write_json(_TENANTS_FILE, tenants)
    return True


# ══════════════════════════════════════════════════════════════════════════════
# Documents
# ══════════════════════════════════════════════════════════════════════════════
_docs = DocumentStore(bucket=BUCKET, table=DOCS_TABLE, fk="apartment_id",
                      get_client=_get_client, data_dir=_DOCS_DIR, json_file=_DOCS_FILE)


def _public_doc(d: dict) -> dict:
    return _docs.public(d)


def _raw_docs(apartment_id: str) -> list[dict]:
    return _docs.raw_for(apartment_id)


def _get_raw_doc(doc_id: str) -> Optional[dict]:
    return _docs.get_raw(doc_id)


def add_document(apartment_id: str, filename: str, content: bytes, mime: str) -> dict:
    return _docs.add(apartment_id, filename, content, mime)


def open_document(doc_id: str, preview: bool = False) -> Optional[dict]:
    return _docs.open(doc_id, preview)


def rename_document(doc_id: str, new_name: str) -> Optional[dict]:
    return _docs.rename(doc_id, new_name)


def delete_document(doc_id: str) -> bool:
    return _docs.delete(doc_id)


# ══════════════════════════════════════════════════════════════════════════════
# Tenants — one row per tenancy, FK apartment_id → apartment_units.id.
# Duration (years lived) is derived at read time from move_in/move_out dates.
# ══════════════════════════════════════════════════════════════════════════════
def _tenancy_years(t: dict, today: Optional[date] = None) -> Optional[float]:
    today = today or date.today()
    mi = _parse_date(t.get("move_in_date"))
    if not mi:
        return None
    mo = _parse_date(t.get("move_out_date")) or today
    days = (mo - mi).days
    return round(days / 365.25, 2) if days >= 0 else 0.0


def _public_tenant(t: dict) -> dict:
    return {
        "id": t.get("id"),
        "apartment_id": t.get("apartment_id"),
        "name": t.get("name"),
        "phone": t.get("phone"),
        "advance_paid": _as_float(t.get("advance_paid")),
        "move_in_date": t.get("move_in_date"),
        "move_out_date": t.get("move_out_date"),
        "notes": t.get("notes"),
        "created_at": t.get("created_at"),
        # derived
        "tenancy_years": _tenancy_years(t),
        "active": not _parse_date(t.get("move_out_date")),
    }


def _sort_tenants(tenants: list[dict]) -> list[dict]:
    """Active first, then by move-in date descending (most recent on top)."""
    return sorted(tenants, key=lambda t: (0 if t.get("active") else 1,
                                          _neg_date_key(t.get("move_in_date"))))


def _neg_date_key(s: Any) -> str:
    """Sort key that puts later dates first (descending) for ISO date strings."""
    d = _parse_date(s)
    return "" if not d else str((date(9999, 12, 31) - d).days).zfill(8)


def _all_tenants_remote(client) -> list[dict]:
    try:
        return client.table(TENANTS_TABLE).select("*").execute().data or []
    except Exception:
        return []  # table not migrated yet — degrade gracefully


def _tenants_for_remote(client, uid: str) -> list[dict]:
    try:
        return client.table(TENANTS_TABLE).select("*").eq("apartment_id", uid).execute().data or []
    except Exception:
        return []


def _clean_tenant_payload(data: dict) -> dict:
    out: dict[str, Any] = {}
    for k in ("name", "phone", "notes", "move_in_date", "move_out_date"):
        if k in data:
            v = data[k]
            out[k] = (str(v).strip() or None) if v is not None else None
    if "advance_paid" in data:
        out["advance_paid"] = _as_float(data["advance_paid"])
    return out


def add_tenant(apartment_id: str, data: dict) -> dict:
    payload = _clean_tenant_payload(data)
    payload["id"] = _new_id()
    payload["apartment_id"] = apartment_id
    payload["created_at"] = _now()
    payload["updated_at"] = payload["created_at"]
    payload.setdefault("name", "Tenant")

    client = _get_client()
    if client:
        row = _sb_insert(client, payload, table=TENANTS_TABLE,
                         protect=("id", "apartment_id", "name"))
        return _public_tenant(row)
    rows = _read_json(_TENANTS_FILE)
    rows.append(payload)
    _write_json(_TENANTS_FILE, rows)
    return _public_tenant(payload)


def update_tenant(tid: str, patch: dict) -> Optional[dict]:
    updates = _clean_tenant_payload(patch)
    if not updates:
        return get_tenant(tid)
    updates["updated_at"] = _now()
    client = _get_client()
    if client:
        rows = _sb_update(client, tid, updates, table=TENANTS_TABLE)
        if not rows:
            return None
        return _public_tenant(rows[0])
    rows = _read_json(_TENANTS_FILE)
    found = None
    for r in rows:
        if r.get("id") == tid:
            r.update(updates)
            found = r
            break
    if not found:
        return None
    _write_json(_TENANTS_FILE, rows)
    return _public_tenant(found)


def get_tenant(tid: str) -> Optional[dict]:
    client = _get_client()
    if client:
        rows = client.table(TENANTS_TABLE).select("*").eq("id", tid).limit(1).execute().data or []
        return _public_tenant(rows[0]) if rows else None
    rows = [r for r in _read_json(_TENANTS_FILE) if r.get("id") == tid]
    return _public_tenant(rows[0]) if rows else None


def delete_tenant(tid: str) -> bool:
    client = _get_client()
    if client:
        res = client.table(TENANTS_TABLE).delete().eq("id", tid).execute()
        return bool(res.data)
    rows = _read_json(_TENANTS_FILE)
    remaining = [r for r in rows if r.get("id") != tid]
    if len(remaining) == len(rows):
        return False
    _write_json(_TENANTS_FILE, remaining)
    return True


# ══════════════════════════════════════════════════════════════════════════════
# Portfolio-level summary
# ══════════════════════════════════════════════════════════════════════════════
def compute_summary() -> dict:
    units = list_units()
    invested = sum(u["bought_price"] or 0 for u in units if u.get("bought_price"))
    current = sum(u["current_estimated_price"] or 0 for u in units if u.get("current_estimated_price"))
    realisable = sum(
        (u.get("after_brokerage_price") if u.get("after_brokerage_price") is not None
         else u.get("current_estimated_price")) or 0
        for u in units
    )
    monthly_rent = sum(u.get("monthly_rent") or 0 for u in units)
    annual_rent = monthly_rent * 12
    gain = current - invested

    # Money-weighted appreciation (respects each unit's own holding period), then
    # add the portfolio rental yield-on-cost for the overall (rent+price) CAGR.
    appreciation = money_weighted_cagr(units)
    rent_on_cost = (annual_rent / invested) if invested else None
    if appreciation is not None:
        blended_total = appreciation + (rent_on_cost or 0.0)
    else:
        blended_total = rent_on_cost

    return {
        "unit_count": len(units),
        "invested": round(invested, 2),
        "current_value": round(current, 2),
        "realisable_value": round(realisable, 2),
        "total_gain": round(gain, 2),
        "total_gain_pct": (gain / invested) if invested else None,
        "monthly_rent": round(monthly_rent, 2),
        "annual_rent": round(annual_rent, 2),
        "gross_yield": (annual_rent / current) if current else None,
        "blended_appreciation_cagr": appreciation,
        "blended_total_cagr": blended_total,
        "document_count": sum(len(u.get("documents", [])) for u in units),
    }


def money_weighted_cagr(items: list[dict], today: Optional[date] = None) -> Optional[float]:
    """Portfolio appreciation CAGR respecting each unit's own holding period: the
    single rate r where Σ bought_i·(1+r)^years_i = Σ current_i (money-weighted /
    IRR). Correct for units bought at different times, unlike averaging CAGRs."""
    today = today or date.today()
    legs: list[tuple[float, float]] = []
    total_current = 0.0
    for u in items:
        bought = _as_float(u.get("bought_price"))
        current = _as_float(u.get("current_estimated_price"))
        bdate = _parse_date(u.get("bought_date"))
        if not bought or bought <= 0 or current is None or current <= 0 or not bdate:
            continue
        years = (today - bdate).days / 365.25
        if years <= 0:
            continue
        legs.append((years, bought))
        total_current += current
    if not legs or total_current <= 0:
        return None

    def fv(r: float) -> float:
        return sum(b * (1.0 + r) ** y for y, b in legs)

    lo, hi = -0.9499, 5.0
    if fv(hi) < total_current:
        return round(hi, 6)
    if fv(lo) > total_current:
        return round(lo, 6)
    for _ in range(100):
        mid = (lo + hi) / 2.0
        if fv(mid) < total_current:
            lo = mid
        else:
            hi = mid
    return round((lo + hi) / 2.0, 6)
