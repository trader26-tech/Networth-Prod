"""
Persistent store for hedge positions (long protective puts).

Two backends:
  • Supabase (cloud Postgres) — used when SUPABASE_URL + SUPABASE_KEY are set.
    Survives container redeploys, supports multi-instance.
  • JSON file (api/hedges.json) — local dev fallback. Resets on Railway every
    deploy because the container filesystem is ephemeral.

The selection happens once per call (via _backend()) so toggling the env vars
on Railway and redeploying immediately switches stores.

A hedge is a separate first-class entity, not a leg of any single CC. One
hedge can be tagged to multiple CC strategies (many-to-many relationship).
Cost allocation across tagged strategies is time-weighted.
"""
import json, os, uuid
from datetime import datetime

STORE_FILE = os.path.join(os.path.dirname(__file__), "hedges.json")


def _backend():
    """Return the active backend module, or None to use the local JSON path."""
    try:
        from api import hedges_supabase_store
        if hedges_supabase_store.is_active():
            return hedges_supabase_store
    except Exception:
        pass
    return None


# ── JSON file helpers ─────────────────────────────────────────────────────

def _load() -> list[dict]:
    if os.path.exists(STORE_FILE):
        try:
            with open(STORE_FILE) as f:
                return json.load(f)
        except Exception:
            return []
    return []


def _save(hedges: list[dict]) -> None:
    with open(STORE_FILE, "w") as f:
        json.dump(hedges, f, indent=2, default=str)


# ── Public API ────────────────────────────────────────────────────────────

def list_hedges(status: str | None = None) -> list[dict]:
    """List all hedges, optionally filtered by status (open / closed)."""
    b = _backend()
    if b: return b.list_hedges(status)
    items = _load()
    if status:
        items = [h for h in items if h.get("status") == status]
    return items


def get_hedge(hid: str) -> dict | None:
    b = _backend()
    if b: return b.get_hedge(hid)
    return next((h for h in _load() if h["id"] == hid), None)


def create_hedge(data: dict) -> dict:
    """Create a new hedge. Required fields: strike, expiry, lots, lot_size,
    premium_paid, symbol (optional). status defaults to 'open'."""
    b = _backend()
    if b: return b.create_hedge(data)
    hedges = _load()
    data["id"]         = str(uuid.uuid4())[:8]
    data["created_at"] = datetime.now().isoformat()
    data.setdefault("status",            "open")
    data.setdefault("tagged_strategies", [])
    data.setdefault("notes",             "")
    data.setdefault("close_price",       None)
    data.setdefault("closed_at",         None)
    data.setdefault("realized_pnl",      None)
    hedges.append(data)
    _save(hedges)
    return data


def update_hedge(hid: str, updates: dict) -> dict | None:
    b = _backend()
    if b: return b.update_hedge(hid, updates)
    hedges = _load()
    for i, h in enumerate(hedges):
        if h["id"] == hid:
            hedges[i].update(updates)
            _save(hedges)
            return hedges[i]
    return None


def delete_hedge(hid: str) -> bool:
    b = _backend()
    if b: return b.delete_hedge(hid)
    hedges = _load()
    new_hedges = [h for h in hedges if h["id"] != hid]
    if len(new_hedges) == len(hedges):
        return False
    _save(new_hedges)
    return True


def close_hedge(hid: str, close_price: float, kind: str = "closed") -> dict | None:
    """Mark a hedge as closed. kind = 'closed' (manual close) or 'rolled'
    (closed because it was replaced by a new hedge — used during roll)."""
    b = _backend()
    if b: return b.close_hedge(hid, close_price, kind)
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


def tag_strategy(hid: str, strategy_id: str) -> dict | None:
    """Tag a CC strategy to this hedge. Idempotent (won't duplicate)."""
    b = _backend()
    if b: return b.tag_strategy(hid, strategy_id)
    h = get_hedge(hid)
    if not h:
        return None
    tags = list(h.get("tagged_strategies", []))
    if strategy_id not in tags:
        tags.append(strategy_id)
    return update_hedge(hid, {"tagged_strategies": tags})


def untag_strategy(hid: str, strategy_id: str) -> dict | None:
    b = _backend()
    if b: return b.untag_strategy(hid, strategy_id)
    h = get_hedge(hid)
    if not h:
        return None
    tags = [t for t in h.get("tagged_strategies", []) if t != strategy_id]
    return update_hedge(hid, {"tagged_strategies": tags})


def hedges_for_strategy(strategy_id: str) -> list[dict]:
    """Return all hedges tagged to a given CC strategy (open + closed)."""
    b = _backend()
    if b: return b.hedges_for_strategy(strategy_id)
    return [h for h in _load() if strategy_id in (h.get("tagged_strategies") or [])]
