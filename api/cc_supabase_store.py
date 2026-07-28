"""
Supabase-backed store for covered-call positions.

Mirrors the public interface of api.covered_call_store (list_positions,
get_position, create_position, update_position, close_call_cycle,
add_call_cycle, delete_position) so the rest of the app is store-agnostic.

Activated by setting SUPABASE_URL + SUPABASE_KEY (or SUPABASE_SERVICE_KEY)
env vars. Without them, the JSON-file store is used. See SUPABASE.md for
the table schema + setup walkthrough.
"""
from __future__ import annotations
import os
import uuid
from datetime import datetime
from typing import Any, Optional

TABLE = "covered_call_positions"

_client = None
_init_attempted = False


def _read_env(key: str) -> str:
    """Read an env var, falling back to .env on disk if not in process env.
    Belt-and-braces: main.py already calls load_dotenv() at startup, but if
    something has bypassed that boot path, we still pick up the .env file."""
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
    """Return the Supabase client, lazily initialised. Returns None if env
    vars are missing or supabase-py isn't installed."""
    global _client, _init_attempted
    if _client is not None or _init_attempted:
        return _client
    _init_attempted = True

    url = _read_env("SUPABASE_URL").rstrip("/")  # strip trailing slash → avoids PGRST125
    # Prefer service key (full access from backend); fall back to anon key.
    key = _read_env("SUPABASE_SERVICE_KEY") or _read_env("SUPABASE_KEY")
    if not url or not key:
        return None
    try:
        from supabase import create_client
        _client = create_client(url, key)
        print(f"✓ Supabase store active ({url})")
        return _client
    except Exception as e:
        print(f"⚠  Supabase init failed (falling back to JSON): {e}")
        return None


def reset_client_cache():
    """Force the next _get_client() call to re-read env vars. Used by the
    /api/auth/supabase-status endpoint's re-test button so user can change
    env vars and re-test without restarting the server."""
    global _client, _init_attempted
    _client = None
    _init_attempted = False


def is_active() -> bool:
    """True when Supabase is configured AND reachable."""
    return _get_client() is not None


# ── Row ↔ dict helpers ────────────────────────────────────────────────────────

def _row_to_position(row: dict) -> dict:
    """Convert a Supabase row to the in-memory position shape the API uses.
    The schema mirrors the JSON store's keys; we only normalise nulls."""
    p = dict(row)
    # Supabase returns ISO strings already; nothing to do for created_at/closed_at.
    p.setdefault("call_history", [])
    p.setdefault("notes", "")
    p.setdefault("total_premium_collected", 0.0)
    p.setdefault("tags", [])
    if p.get("call_history") is None:
        p["call_history"] = []
    if p.get("tags") is None:
        p["tags"] = []
    return p


# ── CRUD ──────────────────────────────────────────────────────────────────────

def list_positions() -> list[dict]:
    client = _get_client()
    if not client:
        return []
    res = client.table(TABLE).select("*").order("created_at", desc=True).execute()
    return [_row_to_position(r) for r in (res.data or [])]


def get_position(pid: str) -> Optional[dict]:
    client = _get_client()
    if not client:
        return None
    res = client.table(TABLE).select("*").eq("id", pid).limit(1).execute()
    rows = res.data or []
    return _row_to_position(rows[0]) if rows else None


# Column-existence probe — set to True once we've successfully written `tags`.
# If a write fails because the column is missing (user hasn't run the
# migration in SUPABASE.md yet), we strip `tags` from the payload, persist
# the rest, and the field becomes a session-only no-op until the column is
# added. Newer deploys flip the flag back to True automatically once the
# column appears.
_TAGS_COLUMN_OK: bool = True
_MISSING_TAG_HINT = (
    "Heads-up: your covered_call_positions table is missing the `tags` column. "
    "Run this in Supabase → SQL Editor to enable persistent tags:\n"
    "  ALTER TABLE covered_call_positions ADD COLUMN IF NOT EXISTS tags TEXT[] NOT NULL DEFAULT '{}';"
)


def _is_missing_tags_error(err: Exception) -> bool:
    msg = str(err).lower()
    return "tags" in msg and ("does not exist" in msg or "could not find" in msg or "schema cache" in msg)


def _strip_tags(payload: dict) -> dict:
    return {k: v for k, v in payload.items() if k != "tags"}


def create_position(data: dict) -> dict:
    global _TAGS_COLUMN_OK
    client = _get_client()
    if not client:
        raise RuntimeError("Supabase not configured")

    payload = dict(data)
    payload["id"]         = str(uuid.uuid4())[:8]
    payload["created_at"] = datetime.now().isoformat()
    payload.setdefault("status", "active")
    payload.setdefault("call_history", [])
    payload.setdefault("total_premium_collected", 0.0)
    payload.setdefault("notes", "")
    payload.setdefault("tags", [])

    if payload.get("active_call"):
        ac = dict(payload["active_call"])
        ac.setdefault("id", str(uuid.uuid4())[:8])
        ac.setdefault("entry_date", datetime.now().isoformat())
        ac.setdefault("status", "open")
        payload["active_call"]            = ac
        payload["total_premium_collected"] = float(ac.get("premium_total") or 0)

    if not _TAGS_COLUMN_OK:
        payload = _strip_tags(payload)

    try:
        res = client.table(TABLE).insert(payload).execute()
    except Exception as e:
        if _TAGS_COLUMN_OK and _is_missing_tags_error(e):
            print(f"⚠ Supabase: {_MISSING_TAG_HINT}")
            _TAGS_COLUMN_OK = False
            res = client.table(TABLE).insert(_strip_tags(payload)).execute()
        else:
            raise
    return _row_to_position((res.data or [payload])[0])


def update_position(pid: str, updates: dict) -> Optional[dict]:
    global _TAGS_COLUMN_OK
    client = _get_client()
    if not client:
        return None
    if not _TAGS_COLUMN_OK and "tags" in updates:
        updates = _strip_tags(updates)
    try:
        res = client.table(TABLE).update(updates).eq("id", pid).execute()
    except Exception as e:
        if _TAGS_COLUMN_OK and _is_missing_tags_error(e) and "tags" in updates:
            print(f"⚠ Supabase: {_MISSING_TAG_HINT}")
            _TAGS_COLUMN_OK = False
            res = client.table(TABLE).update(_strip_tags(updates)).eq("id", pid).execute()
        else:
            raise
    rows = res.data or []
    return _row_to_position(rows[0]) if rows else None


def close_call_cycle(
    pid: str,
    exit_price: float,
    close_kind: str | None = None,
    nb_action:  str       = "held_all",
    nb_shares_sold: int   = 0,
    nb_sell_price:  float = 0.0,
    notes:          str   = "",
) -> Optional[dict]:
    """Close the active call: archive to call_history, optionally sell NB."""
    p = get_position(pid)
    if not p:
        return None
    ac = p.get("active_call")
    if not ac:
        return p

    lots, lot_size = int(ac["lots"]), int(ac["lot_size"])
    prem_recv = float(ac["premium_received"] or 0)
    qty = lots * lot_size
    lot_pnl = (prem_recv - exit_price) * qty

    ac = dict(ac)
    ac["exit_price"]  = exit_price
    ac["exit_date"]   = datetime.now().isoformat()
    ac["pnl"]         = round(lot_pnl, 2)
    ac["status"]      = "closed"
    ac["capture_pct"] = round((prem_recv - exit_price) / prem_recv * 100, 1) if prem_recv > 0 else 0

    if close_kind is None:
        if exit_price <= 0.05 and lot_pnl > 0: close_kind = "expired_worthless"
        elif lot_pnl > 0:                       close_kind = "closed_at_profit"
        else:                                   close_kind = "closed_at_loss"
    ac["close_kind"]     = close_kind
    ac["nb_action"]      = nb_action
    ac["nb_shares_sold"] = int(nb_shares_sold or 0)
    ac["nb_sell_price"]  = float(nb_sell_price or 0)
    ac["close_notes"]    = (notes or "").strip()
    ac["nb_realised_pnl"] = 0.0

    history = list(p.get("call_history") or [])
    history.append(ac)

    updates: dict[str, Any] = {
        "active_call":             None,
        "call_history":            history,
        "total_premium_collected": round(float(p.get("total_premium_collected") or 0)
                                         + float(ac.get("premium_total") or 0), 2),
    }

    # Optional NB partial/full sell
    if nb_shares_sold and nb_sell_price > 0 and nb_action != "held_all":
        shares = min(int(nb_shares_sold), int(p.get("shares") or 0))
        entry  = float(p.get("niftybees_entry_price") or 0)
        nb_realised = round(shares * (nb_sell_price - entry), 2)
        ac["nb_realised_pnl"] = nb_realised
        updates["shares"]         = int(p.get("shares") or 0) - shares
        updates["niftybees_cost"] = round(updates["shares"] * entry, 2)
        if updates["shares"] <= 0:
            updates["status"]    = "closed"
            updates["closed_at"] = datetime.now().isoformat()
        # Re-set the now-amended call_history (ac was mutated)
        history[-1] = ac
        updates["call_history"] = history

    return update_position(pid, updates)


def add_call_cycle(pid: str, call: dict) -> Optional[dict]:
    """Open a new short-call cycle on an existing position."""
    p = get_position(pid)
    if not p:
        return None
    payload = dict(call)
    payload.setdefault("id", str(uuid.uuid4())[:8])
    payload.setdefault("entry_date", datetime.now().isoformat())
    payload.setdefault("status", "open")
    return update_position(pid, {"active_call": payload})


def delete_position(pid: str) -> bool:
    client = _get_client()
    if not client:
        return False
    res = client.table(TABLE).delete().eq("id", pid).execute()
    return bool(res.data)
