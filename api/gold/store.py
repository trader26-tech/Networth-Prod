"""
Gold / Silver / Other asset store — Supabase-backed, JSON-file fallback.

Stores the *raw facts* about each piece (metal, net weight, purity, optional
purchase date/price, optional manual value). Current value is derived on the
**frontend** from live spot rates (see api/gold/prices.py) × weight × purity, so
this store stays price-agnostic and the same item re-values automatically as the
metal price moves.

Data model (2NF):
  gold_items      one row per piece
  gold_documents  one row per file, FK gold_id → gold_items.id  (bucket gold-docs)
"""
from __future__ import annotations

import os
import re
import json
import uuid
from datetime import datetime
from typing import Any, Optional

from api.documents.store import DocumentStore

ITEMS_TABLE = "gold_items"
DOCS_TABLE = "gold_documents"
BUCKET = "gold-docs"

GOLD_COLUMNS = {
    "id", "name", "owner", "metal_type", "weight_g", "purity_pct",
    "manual_value", "purchase_date", "purchase_price", "location", "notes", "sellable_on",
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
_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "gold_data")
_ITEMS_FILE = os.path.join(_DATA_DIR, "items.json")
_DOCS_FILE = os.path.join(_DATA_DIR, "documents.json")
_DOCS_DIR = os.path.join(_DATA_DIR, "docs")

_client = None
_init_attempted = False
_bucket_ready = False
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
        print(f"✓ Gold store: Supabase active ({url})")
        return _client
    except Exception as e:
        print(f"⚠  Gold store: Supabase init failed (using JSON): {e}")
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
    "Gold tables not found in Supabase. Run the migration in SUPABASE.md "
    "(\"Gold / Silver tables\") once in the Supabase SQL editor to create "
    "gold_items + gold_documents, then retry."
)


def tables_ready() -> bool:
    global _tables_ok
    if _tables_ok:
        return True
    client = _get_client()
    if not client:
        return True
    try:
        client.table(ITEMS_TABLE).select("id").limit(1).execute()
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
    for k in ("name", "owner", "metal_type", "location", "notes", "purchase_date", "sellable_on"):
        if k in data and data[k] is not None:
            out[k] = str(data[k]).strip() or None
    for k in ("weight_g", "purity_pct", "manual_value", "purchase_price"):
        if k in data:
            out[k] = _as_float(data[k])
    if out.get("metal_type") not in (None, "gold", "silver", "other"):
        out["metal_type"] = "gold"
    return out


def _decorate(row: dict, docs: list[dict]) -> dict:
    item = {k: row.get(k) for k in GOLD_COLUMNS}
    item["documents"] = docs
    return item


_docs = DocumentStore(bucket=BUCKET, table=DOCS_TABLE, fk="gold_id",
                      get_client=_get_client, data_dir=_DOCS_DIR, json_file=_DOCS_FILE)


def _public_doc(d: dict) -> dict:
    return _docs.public(d)


def _sb_insert(client, payload: dict) -> dict:
    body = dict(payload)
    for _ in range(len(body) + 1):
        try:
            res = client.table(ITEMS_TABLE).insert(body).execute()
            return (res.data or [body])[0]
        except Exception as e:
            col = _missing_column(e)
            if col and col in body and col not in ("id", "name"):
                print(f"⚠ Gold: dropping missing column `{col}` — run the ALTER to persist it.")
                body.pop(col)
                continue
            raise
    raise RuntimeError("Gold insert failed after stripping unknown columns.")


def _sb_update(client, iid: str, updates: dict) -> list:
    body = dict(updates)
    for _ in range(len(body) + 1):
        try:
            res = client.table(ITEMS_TABLE).update(body).eq("id", iid).execute()
            return res.data or []
        except Exception as e:
            col = _missing_column(e)
            if col and col in body:
                print(f"⚠ Gold: dropping missing column `{col}` on update.")
                body.pop(col)
                continue
            raise
    return []


# ── CRUD ──────────────────────────────────────────────────────────────────────
def list_items() -> list[dict]:
    client = _get_client()
    if client:
        rows = (client.table(ITEMS_TABLE).select("*")
                .order("created_at", desc=True).execute().data) or []
        docs = (client.table(DOCS_TABLE).select("*")
                .order("created_at", desc=True).execute().data) or []
    else:
        rows = sorted(_read_json(_ITEMS_FILE),
                      key=lambda r: r.get("created_at", ""), reverse=True)
        docs = _read_json(_DOCS_FILE)
    by_item: dict[str, list] = {}
    for d in docs:
        by_item.setdefault(d.get("gold_id"), []).append(_public_doc(d))
    return [_decorate(r, by_item.get(r["id"], [])) for r in rows]


def get_item(iid: str) -> Optional[dict]:
    client = _get_client()
    if client:
        rows = client.table(ITEMS_TABLE).select("*").eq("id", iid).limit(1).execute().data or []
        if not rows:
            return None
        docs = client.table(DOCS_TABLE).select("*").eq("gold_id", iid).execute().data or []
        return _decorate(rows[0], [_public_doc(d) for d in docs])
    rows = [r for r in _read_json(_ITEMS_FILE) if r["id"] == iid]
    if not rows:
        return None
    docs = [_public_doc(d) for d in _read_json(_DOCS_FILE) if d.get("gold_id") == iid]
    return _decorate(rows[0], docs)


def create_item(data: dict) -> dict:
    payload = _clean_payload(data)
    payload["id"] = _new_id()
    payload["created_at"] = _now()
    payload["updated_at"] = payload["created_at"]
    payload.setdefault("name", "Untitled piece")
    payload.setdefault("metal_type", "gold")

    client = _get_client()
    if client:
        row = _sb_insert(client, payload)
        return _decorate(row, [])
    items = _read_json(_ITEMS_FILE)
    items.append(payload)
    _write_json(_ITEMS_FILE, items)
    return _decorate(payload, [])


def update_item(iid: str, patch: dict) -> Optional[dict]:
    updates = _clean_payload(patch)
    if not updates:
        return get_item(iid)
    updates["updated_at"] = _now()

    client = _get_client()
    if client:
        rows = _sb_update(client, iid, updates)
        if not rows:
            return None
        return get_item(iid)
    items = _read_json(_ITEMS_FILE)
    found = False
    for r in items:
        if r["id"] == iid:
            r.update(updates)
            found = True
            break
    if not found:
        return None
    _write_json(_ITEMS_FILE, items)
    return get_item(iid)


def delete_item(iid: str) -> bool:
    for d in _raw_docs(iid):
        _delete_doc_file(d)
    client = _get_client()
    if client:
        client.table(DOCS_TABLE).delete().eq("gold_id", iid).execute()
        res = client.table(ITEMS_TABLE).delete().eq("id", iid).execute()
        return bool(res.data)
    items = _read_json(_ITEMS_FILE)
    remaining = [r for r in items if r["id"] != iid]
    if len(remaining) == len(items):
        return False
    _write_json(_ITEMS_FILE, remaining)
    docs = [d for d in _read_json(_DOCS_FILE) if d.get("gold_id") != iid]
    _write_json(_DOCS_FILE, docs)
    return True


# ── Documents ─────────────────────────────────────────────────────────────────
def _raw_docs(gold_id: str) -> list[dict]:
    return _docs.raw_for(gold_id)


def _get_raw_doc(doc_id: str) -> Optional[dict]:
    return _docs.get_raw(doc_id)


def add_document(gold_id: str, filename: str, content: bytes, mime: str) -> dict:
    return _docs.add(gold_id, filename, content, mime)


def open_document(doc_id: str, preview: bool = False) -> Optional[dict]:
    return _docs.open(doc_id, preview)


def rename_document(doc_id: str, new_name: str) -> Optional[dict]:
    return _docs.rename(doc_id, new_name)


def delete_document(doc_id: str) -> bool:
    return _docs.delete(doc_id)
