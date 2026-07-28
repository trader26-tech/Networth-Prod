"""
Supabase-backed store for hedge positions.

Mirrors the public interface of api.hedges_store (list_hedges, get_hedge,
create_hedge, update_hedge, close_hedge, tag_strategy, untag_strategy,
hedges_for_strategy, delete_hedge) so the rest of the app is store-agnostic.

Activated by setting SUPABASE_URL + SUPABASE_KEY (or SUPABASE_SERVICE_KEY)
env vars. Without them, the JSON-file store is used.

Required table schema (run once in Supabase SQL Editor):

  CREATE TABLE IF NOT EXISTS hedge_positions (
    id                   TEXT PRIMARY KEY,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    closed_at            TIMESTAMPTZ,
    status               TEXT NOT NULL DEFAULT 'open',     -- open / closed / rolled
    strike               NUMERIC NOT NULL,
    expiry               TEXT NOT NULL,
    lots                 INT NOT NULL DEFAULT 1,
    lot_size             INT NOT NULL DEFAULT 75,
    premium_paid         NUMERIC NOT NULL,
    close_price          NUMERIC,
    realized_pnl         NUMERIC,
    symbol               TEXT NOT NULL DEFAULT '',
    notes                TEXT NOT NULL DEFAULT '',
    tagged_strategies    TEXT[] NOT NULL DEFAULT '{}',
    rolled_from          TEXT
  );
  CREATE INDEX IF NOT EXISTS idx_hedge_status   ON hedge_positions (status);
  CREATE INDEX IF NOT EXISTS idx_hedge_tags     ON hedge_positions USING gin (tagged_strategies);
"""
from __future__ import annotations
import os
import uuid
from datetime import datetime
from typing import Optional

TABLE = "hedge_positions"

_client = None
_init_attempted = False


def _read_env(key: str) -> str:
    val = os.environ.get(key, "").strip()
    if val:
        return val
    try:
        from dotenv import dotenv_values
        env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
        if os.path.isfile(env_path):
            return (dotenv_values(env_path).get(key) or "").strip()
    except Exception:
        pass
    return ""


def _get_client():
    """Return a cached Supabase client. None if env vars missing or init fails."""
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
        print(f"✓ Hedges Supabase store active ({url})")
        return _client
    except Exception as e:
        print(f"⚠  Hedges Supabase init failed (falling back to JSON): {e}")
        return None


def reset_client_cache():
    global _client, _init_attempted
    _client = None
    _init_attempted = False


def is_active() -> bool:
    return _get_client() is not None


# ── Row ↔ dict normalisation ────────────────────────────────────────────────

def _row_to_hedge(row: dict) -> dict:
    h = dict(row)
    h.setdefault("status", "open")
    h.setdefault("notes", "")
    h.setdefault("tagged_strategies", [])
    if h.get("tagged_strategies") is None:
        h["tagged_strategies"] = []
    return h


# ── CRUD ────────────────────────────────────────────────────────────────────

def list_hedges(status: str | None = None) -> list[dict]:
    client = _get_client()
    if not client:
        return []
    q = client.table(TABLE).select("*").order("created_at", desc=True)
    if status:
        q = q.eq("status", status)
    res = q.execute()
    return [_row_to_hedge(r) for r in (res.data or [])]


def get_hedge(hid: str) -> Optional[dict]:
    client = _get_client()
    if not client:
        return None
    res = client.table(TABLE).select("*").eq("id", hid).limit(1).execute()
    rows = res.data or []
    return _row_to_hedge(rows[0]) if rows else None


def create_hedge(data: dict) -> dict:
    client = _get_client()
    if not client:
        raise RuntimeError("Supabase not configured")
    payload = dict(data)
    payload.setdefault("id",         str(uuid.uuid4())[:8])
    payload.setdefault("created_at", datetime.now().isoformat())
    payload.setdefault("status",            "open")
    payload.setdefault("tagged_strategies", [])
    payload.setdefault("notes",             "")
    payload.setdefault("symbol",            "")
    res = client.table(TABLE).insert(payload).execute()
    return _row_to_hedge((res.data or [payload])[0])


def update_hedge(hid: str, updates: dict) -> Optional[dict]:
    client = _get_client()
    if not client:
        return None
    res = client.table(TABLE).update(updates).eq("id", hid).execute()
    rows = res.data or []
    return _row_to_hedge(rows[0]) if rows else None


def delete_hedge(hid: str) -> bool:
    client = _get_client()
    if not client:
        return False
    res = client.table(TABLE).delete().eq("id", hid).execute()
    return bool(res.data)


def close_hedge(hid: str, close_price: float, kind: str = "closed") -> Optional[dict]:
    h = get_hedge(hid)
    if not h:
        return None
    qty = int(h.get("lots", 1)) * int(h.get("lot_size", 75))
    realized = (close_price - float(h["premium_paid"])) * qty
    return update_hedge(hid, {
        "status":       kind,
        "close_price":  close_price,
        "closed_at":    datetime.now().isoformat(),
        "realized_pnl": realized,
    })


def tag_strategy(hid: str, strategy_id: str) -> Optional[dict]:
    h = get_hedge(hid)
    if not h:
        return None
    tags = list(h.get("tagged_strategies", []))
    if strategy_id not in tags:
        tags.append(strategy_id)
    return update_hedge(hid, {"tagged_strategies": tags})


def untag_strategy(hid: str, strategy_id: str) -> Optional[dict]:
    h = get_hedge(hid)
    if not h:
        return None
    tags = [t for t in h.get("tagged_strategies", []) if t != strategy_id]
    return update_hedge(hid, {"tagged_strategies": tags})


def hedges_for_strategy(strategy_id: str) -> list[dict]:
    """Return all hedges tagged to a given CC strategy. Uses Postgres array
    contains operator via Supabase's `cs` filter."""
    client = _get_client()
    if not client:
        return []
    # cs (contains) filter — works because tagged_strategies is TEXT[]
    res = client.table(TABLE).select("*").contains("tagged_strategies", [strategy_id]).execute()
    return [_row_to_hedge(r) for r in (res.data or [])]
