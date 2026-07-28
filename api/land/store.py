"""
Land-parcel store — Supabase-backed, with a JSON-file fallback for offline dev.

Data model (2NF — no repeating groups, every non-key column depends on the
whole key):

  land_parcels        one row per parcel; the facts about the land itself
  land_documents      one row per uploaded file, FK land_id → land_parcels.id

Document *files* live in Supabase Storage (bucket `land-docs`); land_documents
only keeps metadata + the object path. Without SUPABASE_URL/_KEY configured we
fall back to api/land_data/*.json plus a local docs/ directory, so the feature
works with zero cloud setup.

CAGR is **never persisted** — it's derived at read time from bought_price,
current/after-brokerage price and bought_date (see compute_metrics()).

The public interface mirrors the other stores (list/get/create/update/delete)
so routes stay store-agnostic.
"""
from __future__ import annotations

import os
import re
import json
import uuid
from datetime import datetime, date
from typing import Any, Optional

from api.documents.store import DocumentStore

PARCELS_TABLE = "land_parcels"
DOCS_TABLE = "land_documents"
BUCKET = "land-docs"

# Columns that physically exist on land_parcels (everything else is derived).
PARCEL_COLUMNS = {
    "id", "name", "owner", "location", "area_sqft", "bought_date", "bought_price",
    "current_estimated_price", "after_brokerage_price", "notes", "sellable_on",
    "created_at", "updated_at",
}


def _missing_column(err: Exception) -> Optional[str]:
    """If the error is 'column X does not exist' (Postgres) or 'Could not find
    the X column' (PostgREST schema cache), return X — else None. Lets writes
    transparently drop a not-yet-migrated optional column (owner, area_sqft, …)
    and retry, so the app keeps working before the user runs the ALTER."""
    msg = str(err)
    m = re.search(r"column [\"']?[\w]+\.?([\w]+)[\"']? does not exist", msg)
    if m:
        return m.group(1)
    m = re.search(r"[Cc]ould not find the '([\w]+)' column", msg)
    if m:
        return m.group(1)
    return None

# ── local-fallback paths ──────────────────────────────────────────────────────
_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "land_data")
_PARCELS_FILE = os.path.join(_DATA_DIR, "parcels.json")
_DOCS_FILE = os.path.join(_DATA_DIR, "documents.json")
_DOCS_DIR = os.path.join(_DATA_DIR, "docs")

_client = None
_init_attempted = False
_bucket_ready = False


# ══════════════════════════════════════════════════════════════════════════════
# Supabase client (same lazy/​fallback pattern as cc_supabase_store.py)
# ══════════════════════════════════════════════════════════════════════════════
def _read_env(key: str) -> str:
    val = os.environ.get(key, "").strip()
    if val:
        return val
    try:
        from dotenv import dotenv_values
        # store.py lives at api/land/ → project root is three levels up.
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
        print(f"✓ Land store: Supabase active ({url})")
        return _client
    except Exception as e:
        print(f"⚠  Land store: Supabase init failed (using JSON): {e}")
        return None


def is_active() -> bool:
    return _get_client() is not None


def reset_client_cache():
    """Force the next _get_client() to re-read env vars (used after the user
    edits the Supabase URL/key from the Settings page)."""
    global _client, _init_attempted, _bucket_ready, _tables_ok
    _client = None
    _init_attempted = False
    _bucket_ready = False
    _tables_ok = False


MIGRATION_HINT = (
    "Land tables not found in Supabase. Run the migration in SUPABASE.md "
    "(\"Land net-worth tables\") once in the Supabase SQL editor to create "
    "land_parcels + land_documents, then retry."
)

_tables_ok = False


def tables_ready() -> bool:
    """True when the backing store is usable. JSON fallback is always ready;
    Supabase is ready only once the land tables exist (cached after first hit)."""
    global _tables_ok
    if _tables_ok:
        return True
    client = _get_client()
    if not client:
        return True  # JSON fallback — nothing to migrate
    try:
        client.table(PARCELS_TABLE).select("id").limit(1).execute()
        _tables_ok = True
        return True
    except Exception:
        return False


def _ensure_bucket(client) -> None:
    """Create the private `land-docs` storage bucket once if it doesn't exist."""
    global _bucket_ready
    if _bucket_ready:
        return
    try:
        client.storage.create_bucket(BUCKET, options={"public": False})
    except Exception:
        # Already exists (or insufficient perms) — assume present and move on.
        pass
    _bucket_ready = True


# ══════════════════════════════════════════════════════════════════════════════
# Derived metrics — CAGR & gains (computed, never stored)
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


def compute_metrics(p: dict, today: Optional[date] = None) -> dict:
    """Return the derived numbers for a parcel (does not mutate `p`)."""
    today = today or date.today()
    bought = _as_float(p.get("bought_price"))
    current = _as_float(p.get("current_estimated_price"))
    net_sell = _as_float(p.get("after_brokerage_price"))
    area = _as_float(p.get("area_sqft"))
    bdate = _parse_date(p.get("bought_date"))
    years = ((today - bdate).days / 365.25) if bdate else 0.0

    gain = (current - bought) if (current is not None and bought is not None) else None
    net_gain = (net_sell - bought) if (net_sell is not None and bought is not None) else None
    gain_pct = (gain / bought) if (gain is not None and bought) else None
    has_area = area is not None and area > 0

    return {
        "holding_years": round(years, 2) if years else None,
        "gain": round(gain, 2) if gain is not None else None,
        "gain_pct": gain_pct,
        "net_gain": round(net_gain, 2) if net_gain is not None else None,
        # Gross CAGR is on the current estimated price; net CAGR is on the
        # after-brokerage realisable price (the money you'd actually pocket).
        "cagr": _cagr(bought, current, years),
        "net_cagr": _cagr(bought, net_sell, years),
        # ₹ per sq ft — present value and original cost, when an area is known.
        "rate_per_sqft": round(current / area, 2) if (has_area and current is not None) else None,
        "bought_rate_per_sqft": round(bought / area, 2) if (has_area and bought is not None) else None,
    }


def _as_float(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


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
# Parcels CRUD
# ══════════════════════════════════════════════════════════════════════════════
def _clean_parcel_payload(data: dict) -> dict:
    """Keep only real columns; coerce money fields to float|None."""
    out: dict[str, Any] = {}
    for k in ("name", "owner", "location", "notes", "bought_date", "sellable_on"):
        if k in data and data[k] is not None:
            out[k] = str(data[k]).strip() or None
    for k in ("bought_price", "current_estimated_price", "after_brokerage_price", "area_sqft"):
        if k in data:
            out[k] = _as_float(data[k])
    return out


def _decorate(parcel: dict, docs: list[dict]) -> dict:
    """Attach documents + derived metrics to a parcel row for the API response."""
    p = {k: parcel.get(k) for k in PARCEL_COLUMNS}
    p["documents"] = docs
    p.update(compute_metrics(parcel))
    return p


def _sb_insert(client, payload: dict) -> dict:
    """Insert, dropping any not-yet-migrated optional column and retrying."""
    body = dict(payload)
    for _ in range(len(body) + 1):
        try:
            res = client.table(PARCELS_TABLE).insert(body).execute()
            return (res.data or [body])[0]
        except Exception as e:
            col = _missing_column(e)
            if col and col in body and col not in ("id", "name"):
                print(f"⚠ Land: dropping missing column `{col}` — run "
                      f"`ALTER TABLE land_parcels ADD COLUMN IF NOT EXISTS {col} ...;` to persist it.")
                body.pop(col)
                continue
            raise
    raise RuntimeError("Land insert failed after stripping unknown columns.")


def _sb_update(client, pid: str, updates: dict) -> list:
    body = dict(updates)
    for _ in range(len(body) + 1):
        try:
            res = client.table(PARCELS_TABLE).update(body).eq("id", pid).execute()
            return res.data or []
        except Exception as e:
            col = _missing_column(e)
            if col and col in body:
                print(f"⚠ Land: dropping missing column `{col}` on update.")
                body.pop(col)
                continue
            raise
    return []


def list_parcels() -> list[dict]:
    client = _get_client()
    if client:
        rows = (client.table(PARCELS_TABLE).select("*")
                .order("created_at", desc=True).execute().data) or []
        docs = (client.table(DOCS_TABLE).select("*")
                .order("created_at", desc=True).execute().data) or []
    else:
        rows = sorted(_read_json(_PARCELS_FILE),
                      key=lambda r: r.get("created_at", ""), reverse=True)
        docs = _read_json(_DOCS_FILE)
    by_land: dict[str, list] = {}
    for d in docs:
        by_land.setdefault(d.get("land_id"), []).append(_public_doc(d))
    return [_decorate(r, by_land.get(r["id"], [])) for r in rows]


def get_parcel(pid: str) -> Optional[dict]:
    client = _get_client()
    if client:
        rows = client.table(PARCELS_TABLE).select("*").eq("id", pid).limit(1).execute().data or []
        if not rows:
            return None
        docs = client.table(DOCS_TABLE).select("*").eq("land_id", pid).execute().data or []
        return _decorate(rows[0], [_public_doc(d) for d in docs])
    rows = [r for r in _read_json(_PARCELS_FILE) if r["id"] == pid]
    if not rows:
        return None
    docs = [_public_doc(d) for d in _read_json(_DOCS_FILE) if d.get("land_id") == pid]
    return _decorate(rows[0], docs)


def create_parcel(data: dict) -> dict:
    payload = _clean_parcel_payload(data)
    payload["id"] = _new_id()
    payload["created_at"] = _now()
    payload["updated_at"] = payload["created_at"]
    payload.setdefault("name", "Untitled parcel")

    client = _get_client()
    if client:
        row = _sb_insert(client, payload)
        return _decorate(row, [])
    parcels = _read_json(_PARCELS_FILE)
    parcels.append(payload)
    _write_json(_PARCELS_FILE, parcels)
    return _decorate(payload, [])


def update_parcel(pid: str, patch: dict) -> Optional[dict]:
    updates = _clean_parcel_payload(patch)
    if not updates:
        return get_parcel(pid)
    updates["updated_at"] = _now()

    client = _get_client()
    if client:
        rows = _sb_update(client, pid, updates)
        if not rows:
            return None
        return get_parcel(pid)
    parcels = _read_json(_PARCELS_FILE)
    found = False
    for r in parcels:
        if r["id"] == pid:
            r.update(updates)
            found = True
            break
    if not found:
        return None
    _write_json(_PARCELS_FILE, parcels)
    return get_parcel(pid)


def delete_parcel(pid: str) -> bool:
    # Delete the parcel's documents (files + rows) first, then the parcel.
    for d in _raw_docs(pid):
        _delete_doc_file(d)
    client = _get_client()
    if client:
        client.table(DOCS_TABLE).delete().eq("land_id", pid).execute()
        res = client.table(PARCELS_TABLE).delete().eq("id", pid).execute()
        return bool(res.data)
    parcels = _read_json(_PARCELS_FILE)
    remaining = [r for r in parcels if r["id"] != pid]
    if len(remaining) == len(parcels):
        return False
    _write_json(_PARCELS_FILE, remaining)
    docs = [d for d in _read_json(_DOCS_FILE) if d.get("land_id") != pid]
    _write_json(_DOCS_FILE, docs)
    return True


# ══════════════════════════════════════════════════════════════════════════════
# Documents
# ══════════════════════════════════════════════════════════════════════════════
_docs = DocumentStore(bucket=BUCKET, table=DOCS_TABLE, fk="land_id",
                      get_client=_get_client, data_dir=_DOCS_DIR, json_file=_DOCS_FILE)


def _public_doc(d: dict) -> dict:
    return _docs.public(d)


def _raw_docs(land_id: str) -> list[dict]:
    return _docs.raw_for(land_id)


def _get_raw_doc(doc_id: str) -> Optional[dict]:
    return _docs.get_raw(doc_id)


def add_document(land_id: str, filename: str, content: bytes, mime: str) -> dict:
    return _docs.add(land_id, filename, content, mime)


def open_document(doc_id: str, preview: bool = False) -> Optional[dict]:
    return _docs.open(doc_id, preview)


def rename_document(doc_id: str, new_name: str) -> Optional[dict]:
    return _docs.rename(doc_id, new_name)


def delete_document(doc_id: str) -> bool:
    return _docs.delete(doc_id)


# ══════════════════════════════════════════════════════════════════════════════
# Portfolio-level summary
# ══════════════════════════════════════════════════════════════════════════════
def compute_summary() -> dict:
    parcels = list_parcels()
    invested = sum(p["bought_price"] or 0 for p in parcels if p.get("bought_price"))
    current = sum(p["current_estimated_price"] or 0 for p in parcels if p.get("current_estimated_price"))
    realisable = sum(
        (p.get("after_brokerage_price") if p.get("after_brokerage_price") is not None
         else p.get("current_estimated_price")) or 0
        for p in parcels
    )
    gain = current - invested
    return {
        "parcel_count": len(parcels),
        "invested": round(invested, 2),
        "current_value": round(current, 2),
        "realisable_value": round(realisable, 2),
        "total_gain": round(gain, 2),
        "total_gain_pct": (gain / invested) if invested else None,
        "blended_cagr": money_weighted_cagr(parcels),
        "document_count": sum(len(p.get("documents", [])) for p in parcels),
    }


def money_weighted_cagr(items: list[dict], today: Optional[date] = None) -> Optional[float]:
    """Portfolio CAGR that respects each asset's own holding period — the single
    rate r at which every purchase, compounded from its own buy-date to today,
    sums to the current total value (money-weighted / IRR). This is correct for
    assets bought at *different times*, unlike a plain average of per-asset CAGRs.

    Solves  Σ bought_i·(1+r)^years_i  =  Σ current_i  for r.
    """
    today = today or date.today()
    legs: list[tuple[float, float]] = []     # (years, bought_price)
    total_current = 0.0
    for p in items:
        bought = _as_float(p.get("bought_price"))
        current = _as_float(p.get("current_estimated_price"))
        bdate = _parse_date(p.get("bought_date"))
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

    lo, hi = -0.9499, 5.0                    # search -95% .. +500% per year
    if fv(hi) < total_current:               # extraordinary return — clamp
        return round(hi, 6)
    if fv(lo) > total_current:
        return round(lo, 6)
    for _ in range(100):                     # bisection (fv is monotonic in r)
        mid = (lo + hi) / 2.0
        if fv(mid) < total_current:
            lo = mid
        else:
            hi = mid
    return round((lo + hi) / 2.0, 6)
