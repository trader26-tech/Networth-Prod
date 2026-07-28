"""
Land + Build store — self-built properties (land bought at one date, building
constructed at a later date). Supabase-backed with a JSON-file fallback.

The key difference from Land/Apartments: returns are a **two-leg money-weighted
IRR** — the single rate r where
    land_cost·(1+r)^years_since_land + build_cost·(1+r)^years_since_build = current
Because the two outlays were committed at different times, a plain
(current/cost)^(1/years)−1 would be wrong. Rent (these are usually rented) is
added on top: total CAGR = appreciation IRR + annual rent ÷ total invested.

Data model (2NF):
  built_properties     one row per property (land + construction legs)
  built_documents      one row per file, FK property_id → built_properties.id
Files live in the private Supabase Storage bucket `built-docs`.
"""
from __future__ import annotations

import os
import re
import json
import uuid
from datetime import datetime, date
from typing import Any, Optional

PROPS_TABLE = "built_properties"
DOCS_TABLE = "built_documents"
BUCKET = "built-docs"

PROP_COLUMNS = {
    "id", "name", "owner", "location", "area_sqft",
    "land_cost", "land_date", "construction_cost", "construction_date",
    "current_estimated_price", "after_brokerage_price", "monthly_rent", "notes", "sellable_on",
    "created_at", "updated_at",
}


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
_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "built_data")
_PROPS_FILE = os.path.join(_DATA_DIR, "properties.json")
_DOCS_FILE = os.path.join(_DATA_DIR, "documents.json")
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
        print(f"✓ Built store: Supabase active ({url})")
        return _client
    except Exception as e:
        print(f"⚠  Built store: Supabase init failed (using JSON): {e}")
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
    "Land+Build tables not found in Supabase. Run the migration in SUPABASE.md "
    "(\"Land + Build tables\") once in the Supabase SQL editor to create "
    "built_properties + built_documents, then retry."
)


def tables_ready() -> bool:
    global _tables_ok
    if _tables_ok:
        return True
    client = _get_client()
    if not client:
        return True
    try:
        client.table(PROPS_TABLE).select("id").limit(1).execute()
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
# Derived metrics — two-leg IRR + rent (computed, never stored)
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


def _as_float(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _irr(legs: list[tuple[float, float]], current: float) -> Optional[float]:
    """Solve Σ cost_i·(1+r)^years_i = current for r (monotonic → bisection)."""
    if not legs or current is None or current <= 0:
        return None

    def fv(r: float) -> float:
        return sum(c * (1.0 + r) ** y for y, c in legs)

    lo, hi = -0.9499, 5.0
    if fv(hi) < current:
        return round(hi, 6)
    if fv(lo) > current:
        return round(lo, 6)
    for _ in range(100):
        mid = (lo + hi) / 2.0
        if fv(mid) < current:
            lo = mid
        else:
            hi = mid
    return round((lo + hi) / 2.0, 6)


def _legs(p: dict, today: date) -> list[tuple[float, float]]:
    """Build the (years, cost) legs for a property — land and construction."""
    legs: list[tuple[float, float]] = []
    for cost_key, date_key in (("land_cost", "land_date"), ("construction_cost", "construction_date")):
        cost = _as_float(p.get(cost_key))
        d = _parse_date(p.get(date_key))
        if cost and cost > 0 and d:
            years = (today - d).days / 365.25
            if years > 0:
                legs.append((years, cost))
    return legs


def compute_metrics(p: dict, today: Optional[date] = None) -> dict:
    today = today or date.today()
    land_cost = _as_float(p.get("land_cost")) or 0.0
    build_cost = _as_float(p.get("construction_cost")) or 0.0
    invested = land_cost + build_cost
    current = _as_float(p.get("current_estimated_price"))
    net_sell = _as_float(p.get("after_brokerage_price"))
    area = _as_float(p.get("area_sqft"))
    rent_m = _as_float(p.get("monthly_rent"))
    has_area = area is not None and area > 0

    legs = _legs(p, today)
    # earliest leg date → "held" years (usually the land purchase)
    earliest = None
    for key in ("land_date", "construction_date"):
        d = _parse_date(p.get(key))
        if d and (earliest is None or d < earliest):
            earliest = d
    held = ((today - earliest).days / 365.25) if earliest else None

    gain = (current - invested) if (current is not None and invested) else None
    gain_pct = (gain / invested) if (gain is not None and invested) else None

    annual_rent = (rent_m * 12) if rent_m else None
    rent_yield = (annual_rent / current) if (annual_rent and current) else None
    rent_yield_on_cost = (annual_rent / invested) if (annual_rent and invested) else None

    appreciation = _irr(legs, current) if (legs and current) else None
    if appreciation is not None:
        total_cagr = appreciation + (rent_yield_on_cost or 0.0)
    else:
        total_cagr = rent_yield_on_cost

    # IRR including land+build legs AND the after-brokerage (net) terminal value
    appreciation_net = _irr(legs, net_sell) if (legs and net_sell) else None

    return {
        "invested": round(invested, 2) if invested else None,
        "holding_years": round(held, 2) if held else None,
        "gain": round(gain, 2) if gain is not None else None,
        "gain_pct": gain_pct,
        "cagr": appreciation,             # two-leg appreciation IRR
        "net_cagr": appreciation_net,
        "annual_rent": round(annual_rent, 2) if annual_rent is not None else None,
        "rent_yield": rent_yield,
        "rent_yield_on_cost": rent_yield_on_cost,
        "total_cagr": total_cagr,         # appreciation IRR + rent — headline
        "rate_per_sqft": round(current / area, 2) if (has_area and current is not None) else None,
        "invested_rate_per_sqft": round(invested / area, 2) if (has_area and invested) else None,
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
# Properties CRUD
# ══════════════════════════════════════════════════════════════════════════════
def _clean_payload(data: dict) -> dict:
    out: dict[str, Any] = {}
    for k in ("name", "owner", "location", "notes", "land_date", "construction_date", "sellable_on"):
        if k in data and data[k] is not None:
            out[k] = str(data[k]).strip() or None
    for k in ("area_sqft", "land_cost", "construction_cost",
              "current_estimated_price", "after_brokerage_price", "monthly_rent"):
        if k in data:
            out[k] = _as_float(data[k])
    return out


def _decorate(row: dict, docs: list[dict]) -> dict:
    p = {k: row.get(k) for k in PROP_COLUMNS}
    p["documents"] = docs
    p.update(compute_metrics(row))
    return p


def _sb_insert(client, payload: dict) -> dict:
    body = dict(payload)
    for _ in range(len(body) + 1):
        try:
            res = client.table(PROPS_TABLE).insert(body).execute()
            return (res.data or [body])[0]
        except Exception as e:
            col = _missing_column(e)
            if col and col in body and col not in ("id", "name"):
                print(f"⚠ Built: dropping missing column `{col}` — run the ALTER to persist it.")
                body.pop(col)
                continue
            raise
    raise RuntimeError("Built insert failed after stripping unknown columns.")


def _sb_update(client, pid: str, updates: dict) -> list:
    body = dict(updates)
    for _ in range(len(body) + 1):
        try:
            res = client.table(PROPS_TABLE).update(body).eq("id", pid).execute()
            return res.data or []
        except Exception as e:
            col = _missing_column(e)
            if col and col in body:
                print(f"⚠ Built: dropping missing column `{col}` on update.")
                body.pop(col)
                continue
            raise
    return []


def list_properties() -> list[dict]:
    client = _get_client()
    if client:
        rows = (client.table(PROPS_TABLE).select("*")
                .order("created_at", desc=True).execute().data) or []
        docs = (client.table(DOCS_TABLE).select("*")
                .order("created_at", desc=True).execute().data) or []
    else:
        rows = sorted(_read_json(_PROPS_FILE),
                      key=lambda r: r.get("created_at", ""), reverse=True)
        docs = _read_json(_DOCS_FILE)
    by_prop: dict[str, list] = {}
    for d in docs:
        by_prop.setdefault(d.get("property_id"), []).append(_public_doc(d))
    return [_decorate(r, by_prop.get(r["id"], [])) for r in rows]


def get_property(pid: str) -> Optional[dict]:
    client = _get_client()
    if client:
        rows = client.table(PROPS_TABLE).select("*").eq("id", pid).limit(1).execute().data or []
        if not rows:
            return None
        docs = client.table(DOCS_TABLE).select("*").eq("property_id", pid).execute().data or []
        return _decorate(rows[0], [_public_doc(d) for d in docs])
    rows = [r for r in _read_json(_PROPS_FILE) if r["id"] == pid]
    if not rows:
        return None
    docs = [_public_doc(d) for d in _read_json(_DOCS_FILE) if d.get("property_id") == pid]
    return _decorate(rows[0], docs)


def create_property(data: dict) -> dict:
    payload = _clean_payload(data)
    payload["id"] = _new_id()
    payload["created_at"] = _now()
    payload["updated_at"] = payload["created_at"]
    payload.setdefault("name", "Untitled property")

    client = _get_client()
    if client:
        row = _sb_insert(client, payload)
        return _decorate(row, [])
    items = _read_json(_PROPS_FILE)
    items.append(payload)
    _write_json(_PROPS_FILE, items)
    return _decorate(payload, [])


def update_property(pid: str, patch: dict) -> Optional[dict]:
    updates = _clean_payload(patch)
    if not updates:
        return get_property(pid)
    updates["updated_at"] = _now()

    client = _get_client()
    if client:
        rows = _sb_update(client, pid, updates)
        if not rows:
            return None
        return get_property(pid)
    items = _read_json(_PROPS_FILE)
    found = False
    for r in items:
        if r["id"] == pid:
            r.update(updates)
            found = True
            break
    if not found:
        return None
    _write_json(_PROPS_FILE, items)
    return get_property(pid)


def delete_property(pid: str) -> bool:
    for d in _raw_docs(pid):
        _delete_doc_file(d)
    client = _get_client()
    if client:
        client.table(DOCS_TABLE).delete().eq("property_id", pid).execute()
        res = client.table(PROPS_TABLE).delete().eq("id", pid).execute()
        return bool(res.data)
    items = _read_json(_PROPS_FILE)
    remaining = [r for r in items if r["id"] != pid]
    if len(remaining) == len(items):
        return False
    _write_json(_PROPS_FILE, remaining)
    docs = [d for d in _read_json(_DOCS_FILE) if d.get("property_id") != pid]
    _write_json(_DOCS_FILE, docs)
    return True


# ══════════════════════════════════════════════════════════════════════════════
# Documents
# ══════════════════════════════════════════════════════════════════════════════
def _public_doc(d: dict) -> dict:
    return {
        "id": d.get("id"),
        "property_id": d.get("property_id"),
        "filename": d.get("filename"),
        "mime_type": d.get("mime_type"),
        "size": d.get("size"),
        "created_at": d.get("created_at"),
    }


def _raw_docs(property_id: str) -> list[dict]:
    client = _get_client()
    if client:
        return client.table(DOCS_TABLE).select("*").eq("property_id", property_id).execute().data or []
    return [d for d in _read_json(_DOCS_FILE) if d.get("property_id") == property_id]


def _get_raw_doc(doc_id: str) -> Optional[dict]:
    client = _get_client()
    if client:
        rows = client.table(DOCS_TABLE).select("*").eq("id", doc_id).limit(1).execute().data or []
        return rows[0] if rows else None
    rows = [d for d in _read_json(_DOCS_FILE) if d.get("id") == doc_id]
    return rows[0] if rows else None


def add_document(property_id: str, filename: str, content: bytes, mime: str) -> dict:
    doc_id = _new_id()
    safe_name = os.path.basename(filename or "document")
    meta = {
        "id": doc_id,
        "property_id": property_id,
        "filename": safe_name,
        "mime_type": mime or "application/octet-stream",
        "size": len(content),
        "created_at": _now(),
    }
    client = _get_client()
    if client:
        _ensure_bucket(client)
        object_path = f"{property_id}/{doc_id}__{safe_name}"
        client.storage.from_(BUCKET).upload(
            object_path, content,
            {"content-type": meta["mime_type"], "upsert": "true"},
        )
        meta["storage_path"] = object_path
        client.table(DOCS_TABLE).insert(meta).execute()
    else:
        os.makedirs(_DOCS_DIR, exist_ok=True)
        local_path = os.path.join(_DOCS_DIR, f"{doc_id}__{safe_name}")
        with open(local_path, "wb") as f:
            f.write(content)
        meta["storage_path"] = local_path
        docs = _read_json(_DOCS_FILE)
        docs.append(meta)
        _write_json(_DOCS_FILE, docs)
    return _public_doc(meta)


def open_document(doc_id: str) -> Optional[dict]:
    d = _get_raw_doc(doc_id)
    if not d:
        return None
    client = _get_client()
    if client:
        try:
            res = client.storage.from_(BUCKET).create_signed_url(d["storage_path"], 3600)
            url = res.get("signedURL") or res.get("signedUrl") or res.get("signed_url")
            return {"redirect": url} if url else None
        except Exception:
            return None
    path = d.get("storage_path")
    if not path or not os.path.isfile(path):
        return None
    return {"path": path, "filename": d.get("filename"), "mime": d.get("mime_type")}


def _delete_doc_file(d: dict) -> None:
    client = _get_client()
    if client:
        try:
            client.storage.from_(BUCKET).remove([d["storage_path"]])
        except Exception:
            pass
    else:
        try:
            if d.get("storage_path") and os.path.isfile(d["storage_path"]):
                os.remove(d["storage_path"])
        except OSError:
            pass


def delete_document(doc_id: str) -> bool:
    d = _get_raw_doc(doc_id)
    if not d:
        return False
    _delete_doc_file(d)
    client = _get_client()
    if client:
        res = client.table(DOCS_TABLE).delete().eq("id", doc_id).execute()
        return bool(res.data)
    docs = _read_json(_DOCS_FILE)
    remaining = [x for x in docs if x.get("id") != doc_id]
    if len(remaining) == len(docs):
        return False
    _write_json(_DOCS_FILE, remaining)
    return True


# ══════════════════════════════════════════════════════════════════════════════
# Portfolio summary
# ══════════════════════════════════════════════════════════════════════════════
def compute_summary() -> dict:
    props = list_properties()
    invested = sum((p.get("invested") or 0) for p in props)
    current = sum(p["current_estimated_price"] or 0 for p in props if p.get("current_estimated_price"))
    realisable = sum(
        (p.get("after_brokerage_price") if p.get("after_brokerage_price") is not None
         else p.get("current_estimated_price")) or 0
        for p in props
    )
    monthly_rent = sum(p.get("monthly_rent") or 0 for p in props)
    annual_rent = monthly_rent * 12
    gain = current - invested

    appreciation = _portfolio_irr(props)
    rent_on_cost = (annual_rent / invested) if invested else None
    if appreciation is not None:
        blended_total = appreciation + (rent_on_cost or 0.0)
    else:
        blended_total = rent_on_cost

    return {
        "property_count": len(props),
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
        "document_count": sum(len(p.get("documents", [])) for p in props),
    }


def _portfolio_irr(props: list[dict], today: Optional[date] = None) -> Optional[float]:
    """Money-weighted IRR across every property's land + construction legs."""
    today = today or date.today()
    legs: list[tuple[float, float]] = []
    total_current = 0.0
    for p in props:
        plegs = _legs(p, today)
        current = _as_float(p.get("current_estimated_price"))
        if not plegs or current is None or current <= 0:
            continue
        legs.extend(plegs)
        total_current += current
    return _irr(legs, total_current) if legs else None
