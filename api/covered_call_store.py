"""
Persistent store for covered call positions (NiftyBees ETF + weekly short CE cycles).

Two backends:
  • Supabase (cloud Postgres) — used when SUPABASE_URL + SUPABASE_KEY are set.
    Survives container redeploys, supports multi-instance.
  • JSON file (api/cc_positions.json) — local dev fallback. Resets on Railway
    every deploy because the container filesystem is ephemeral.

The selection happens once per call (via _backend()) so toggling the env vars
on Railway and redeploying immediately switches stores.
"""

import json, os, uuid
from datetime import datetime

STORE_FILE = os.path.join(os.path.dirname(__file__), "cc_positions.json")


def _backend():
    """Return the active backend module, or None to use the local JSON path."""
    try:
        from api import cc_supabase_store
        if cc_supabase_store.is_active():
            return cc_supabase_store
    except Exception:
        pass
    return None


def _load() -> list[dict]:
    if os.path.exists(STORE_FILE):
        with open(STORE_FILE) as f:
            return json.load(f)
    return []


def _save(positions: list[dict]) -> None:
    with open(STORE_FILE, "w") as f:
        json.dump(positions, f, indent=2, default=str)


def list_positions() -> list[dict]:
    b = _backend()
    if b: return b.list_positions()
    return _load()


def get_position(pid: str) -> dict | None:
    b = _backend()
    if b: return b.get_position(pid)
    return next((p for p in _load() if p["id"] == pid), None)


def create_position(data: dict) -> dict:
    b = _backend()
    if b: return b.create_position(data)
    positions = _load()
    data["id"]         = str(uuid.uuid4())[:8]
    data["created_at"] = datetime.now().isoformat()
    data.setdefault("status", "active")
    data.setdefault("call_history", [])
    data.setdefault("total_premium_collected", 0.0)
    data.setdefault("notes", "")
    data.setdefault("tags", [])
    positions.append(data)
    _save(positions)
    return data


def update_position(pid: str, updates: dict) -> dict | None:
    b = _backend()
    if b: return b.update_position(pid, updates)
    positions = _load()
    for i, p in enumerate(positions):
        if p["id"] == pid:
            positions[i].update(updates)
            _save(positions)
            return positions[i]
    return None


def close_call_cycle(
    pid: str,
    exit_price: float,
    close_kind: str | None = None,
    nb_action:  str       = "held_all",
    nb_shares_sold: int   = 0,
    nb_sell_price:  float = 0.0,
    notes:          str   = "",
) -> dict | None:
    """Mark active call closed; archive to call_history; accumulate premium.

    Optional rich-close fields capture what the user actually did:
      close_kind   : 'expired_worthless' | 'closed_at_profit' | 'closed_at_loss'
                     | 'rolled' | 'assigned'   (None → derive from pnl)
      nb_action    : 'held_all' | 'sold_partial' | 'sold_all'
      nb_shares_sold + nb_sell_price : recorded if user sold any NB
    """
    b = _backend()
    if b:
        return b.close_call_cycle(pid, exit_price=exit_price, close_kind=close_kind,
                                  nb_action=nb_action, nb_shares_sold=nb_shares_sold,
                                  nb_sell_price=nb_sell_price, notes=notes)
    positions = _load()
    for i, p in enumerate(positions):
        if p["id"] != pid:
            continue
        ac = p.get("active_call")
        if not ac:
            return p

        # Option leg P&L
        lot_pnl = (ac["premium_received"] - exit_price) * ac["lots"] * ac["lot_size"]
        ac["exit_price"]  = exit_price
        ac["exit_date"]   = datetime.now().isoformat()
        ac["pnl"]         = round(lot_pnl, 2)
        ac["status"]      = "closed"
        ac["capture_pct"] = round(
            (ac["premium_received"] - exit_price) / ac["premium_received"] * 100, 1
        ) if ac["premium_received"] > 0 else 0

        # Rich close metadata
        if close_kind is None:
            if exit_price <= 0.05 and lot_pnl > 0:
                close_kind = "expired_worthless"
            elif lot_pnl > 0:
                close_kind = "closed_at_profit"
            else:
                close_kind = "closed_at_loss"
        ac["close_kind"]      = close_kind
        ac["nb_action"]       = nb_action
        ac["nb_shares_sold"]  = int(nb_shares_sold or 0)
        ac["nb_sell_price"]   = float(nb_sell_price or 0)
        ac["close_notes"]     = (notes or "").strip()

        # If NB shares were sold, record realised NB P&L on the cycle and
        # decrement the position's NB share count + cost basis.
        nb_realised = 0.0
        if nb_shares_sold and nb_sell_price > 0 and nb_action != "held_all":
            shares = min(int(nb_shares_sold), int(p.get("shares", 0) or 0))
            entry  = float(p.get("niftybees_entry_price", 0) or 0)
            nb_realised = round(shares * (nb_sell_price - entry), 2)
            ac["nb_realised_pnl"] = nb_realised
            # Update the held NB
            p["shares"]         = int(p.get("shares", 0) or 0) - shares
            p["niftybees_cost"] = round(p.get("shares", 0) * entry, 2)
            if p["shares"] <= 0:
                p["status"] = "closed"
                p["closed_at"] = datetime.now().isoformat()
        else:
            ac["nb_realised_pnl"] = 0.0

        p.setdefault("call_history", []).append(ac)
        p["active_call"] = None
        p["total_premium_collected"] = round(
            p.get("total_premium_collected", 0) + ac["premium_total"], 2
        )
        positions[i] = p
        _save(positions)
        return p
    return None


def add_call_cycle(pid: str, call: dict) -> dict | None:
    """Open a new short call cycle on an existing position."""
    b = _backend()
    if b: return b.add_call_cycle(pid, call)
    positions = _load()
    for i, p in enumerate(positions):
        if p["id"] != pid:
            continue
        call["id"]         = str(uuid.uuid4())[:8]
        call["entry_date"] = datetime.now().isoformat()
        call["status"]     = "open"
        p["active_call"]   = call
        positions[i] = p
        _save(positions)
        return p
    return None


def delete_position(pid: str) -> bool:
    b = _backend()
    if b: return b.delete_position(pid)
    positions = _load()
    new = [p for p in positions if p["id"] != pid]
    if len(new) == len(positions):
        return False
    _save(new)
    return True
