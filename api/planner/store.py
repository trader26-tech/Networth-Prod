"""
Buy-planner store — Supabase-backed, JSON-file fallback.

One table drives the "When can I buy this?" planner on the home dashboard:

  purchase_wishlist   things you want to buy (name + ₹ price + priority order),
                      each with a financing choice — "income" (saved from your
                      monthly surplus) or "savings" (funded by selling specific
                      assets). `finance_assets` is the JSON list of asset
                      position-keys earmarked to sell; `sold_assets` is the
                      subset already sold (collected).

Each asset's *sell date* lives on the asset's own table (e.g. apartments,
land), set on its page and read back through gather_positions(). The scheduling
(buyable now vs in N months, collected-so-far) runs client-side off these rows
plus the dashboard's monthly surplus & each asset's realisable value + sell date.
"""
from __future__ import annotations

import os
import re
import json
import uuid
from datetime import datetime
from typing import Any, Optional

WISHLIST_TABLE = "purchase_wishlist"

WISHLIST_COLUMNS = {
    "id", "name", "price", "priority", "finance_mode", "finance_assets",
    "sold_assets", "target_date", "monthly_contribution", "saved", "bought",
    "note", "created_at", "updated_at",
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
_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "planner_data")
_WISHLIST_FILE = os.path.join(_DATA_DIR, "wishlist.json")

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
        print(f"✓ Planner store: Supabase active ({url})")
        return _client
    except Exception as e:
        print(f"⚠  Planner store: Supabase init failed (using JSON): {e}")
        return None


def is_active() -> bool:
    return _get_client() is not None


def reset_client_cache():
    global _client, _init_attempted, _tables_ok
    _client = None
    _init_attempted = False
    _tables_ok = False


MIGRATION_HINT = (
    "Planner table not found in Supabase. Run the migration in SUPABASE.md "
    "(\"Buy-planner table\") once in the Supabase SQL editor to create "
    "purchase_wishlist, then retry."
)


def tables_ready() -> bool:
    global _tables_ok
    if _tables_ok:
        return True
    client = _get_client()
    if not client:
        return True  # JSON fallback is always ready
    try:
        client.table(WISHLIST_TABLE).select("id").limit(1).execute()
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


def _as_int(v: Any) -> Optional[int]:
    f = _as_float(v)
    return int(f) if f is not None else None


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


def _sb_insert(client, table: str, payload: dict, keep: tuple) -> dict:
    body = dict(payload)
    for _ in range(len(body) + 1):
        try:
            res = client.table(table).insert(body).execute()
            return (res.data or [body])[0]
        except Exception as e:
            col = _missing_column(e)
            if col and col in body and col not in keep:
                print(f"⚠ Planner: dropping missing column `{col}` — run the ALTER to persist it.")
                body.pop(col)
                continue
            raise
    raise RuntimeError("Planner insert failed after stripping unknown columns.")


def _sb_update(client, table: str, eid: str, updates: dict) -> list:
    body = dict(updates)
    for _ in range(len(body) + 1):
        try:
            res = client.table(table).update(body).eq("id", eid).execute()
            return res.data or []
        except Exception as e:
            col = _missing_column(e)
            if col and col in body:
                print(f"⚠ Planner: dropping missing column `{col}` on update.")
                body.pop(col)
                continue
            raise
    return []


# ── wishlist ──────────────────────────────────────────────────────────────────
def _clean_wishlist(data: dict) -> dict:
    out: dict[str, Any] = {}
    for k in ("name", "finance_mode", "finance_assets", "sold_assets", "target_date", "note"):
        if k in data and data[k] is not None:
            out[k] = str(data[k]).strip() or None
    if out.get("finance_mode") not in (None, "income", "savings"):
        out["finance_mode"] = "income"
    if "price" in data:
        out["price"] = _as_float(data["price"])
    if "monthly_contribution" in data:
        out["monthly_contribution"] = _as_float(data["monthly_contribution"])
    if "saved" in data:
        out["saved"] = _as_float(data["saved"])
    if "priority" in data:
        out["priority"] = _as_int(data["priority"])
    if "bought" in data:
        out["bought"] = _as_bool(data["bought"], False)
    return out


def _decorate_wishlist(row: dict) -> dict:
    item = {k: row.get(k) for k in WISHLIST_COLUMNS}
    item["bought"] = bool(item.get("bought"))
    item["priority"] = item.get("priority") if item.get("priority") is not None else 0
    item["finance_mode"] = item.get("finance_mode") or "income"
    return item


def list_wishlist() -> list[dict]:
    client = _get_client()
    if client:
        rows = (client.table(WISHLIST_TABLE).select("*")
                .order("priority").execute().data) or []
    else:
        rows = _read_json(_WISHLIST_FILE)
    rows = [_decorate_wishlist(r) for r in rows]
    rows.sort(key=lambda r: (r.get("priority", 0), r.get("created_at", "")))
    return rows


def get_wishlist_item(eid: str) -> Optional[dict]:
    client = _get_client()
    if client:
        rows = client.table(WISHLIST_TABLE).select("*").eq("id", eid).limit(1).execute().data or []
        return _decorate_wishlist(rows[0]) if rows else None
    rows = [r for r in _read_json(_WISHLIST_FILE) if r["id"] == eid]
    return _decorate_wishlist(rows[0]) if rows else None


def create_wishlist_item(data: dict) -> dict:
    payload = _clean_wishlist(data)
    payload["id"] = _new_id()
    payload["created_at"] = _now()
    payload["updated_at"] = payload["created_at"]
    payload.setdefault("name", "Item")
    payload.setdefault("bought", False)
    payload.setdefault("finance_mode", "income")
    if payload.get("priority") is None:
        # append to the end of the list
        existing = list_wishlist()
        payload["priority"] = (max((r.get("priority", 0) for r in existing), default=-1) + 1)

    client = _get_client()
    if client:
        row = _sb_insert(client, WISHLIST_TABLE, payload, keep=("id", "name"))
        return _decorate_wishlist(row)
    items = _read_json(_WISHLIST_FILE)
    items.append(payload)
    _write_json(_WISHLIST_FILE, items)
    return _decorate_wishlist(payload)


def update_wishlist_item(eid: str, patch: dict) -> Optional[dict]:
    updates = _clean_wishlist(patch)
    if not updates:
        return get_wishlist_item(eid)
    updates["updated_at"] = _now()

    client = _get_client()
    if client:
        rows = _sb_update(client, WISHLIST_TABLE, eid, updates)
        if not rows:
            return None
        return get_wishlist_item(eid)
    items = _read_json(_WISHLIST_FILE)
    found = False
    for r in items:
        if r["id"] == eid:
            r.update(updates)
            found = True
            break
    if not found:
        return None
    _write_json(_WISHLIST_FILE, items)
    return get_wishlist_item(eid)


def delete_wishlist_item(eid: str) -> bool:
    client = _get_client()
    if client:
        res = client.table(WISHLIST_TABLE).delete().eq("id", eid).execute()
        return bool(res.data)
    items = _read_json(_WISHLIST_FILE)
    remaining = [r for r in items if r["id"] != eid]
    if len(remaining) == len(items):
        return False
    _write_json(_WISHLIST_FILE, remaining)
    return True


def reorder_wishlist(order: list[str]) -> list[dict]:
    """Set priority = index for each id in `order`. Ids not listed keep going
    after, in their current order."""
    rank = {eid: i for i, eid in enumerate(order)}
    for it in list_wishlist():
        if it["id"] in rank:
            update_wishlist_item(it["id"], {"priority": rank[it["id"]]})
    return list_wishlist()
