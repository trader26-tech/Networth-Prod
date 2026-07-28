"""
Persistent JSON store for covered-call simulations.

A "simulation" is a full paper-trading sandbox: the user enters a starting
capital, the system buys NiftyBees at the live price, and from then on every
sell-call / close-call / roll action is recorded with full charges + tax
accounting. Resets to a clean slate when the user deletes or recreates.
"""
import json, os, uuid
from datetime import datetime

STORE_FILE = os.path.join(os.path.dirname(__file__), "cc_simulations.json")


def _load() -> list[dict]:
    if os.path.exists(STORE_FILE):
        try:
            with open(STORE_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return []
    return []


def _save(sims: list[dict]) -> None:
    with open(STORE_FILE, "w") as f:
        json.dump(sims, f, indent=2, default=str)


def list_simulations() -> list[dict]:
    return _load()


def get_simulation(sim_id: str) -> dict | None:
    return next((s for s in _load() if s.get("id") == sim_id), None)


def create_simulation(data: dict) -> dict:
    sims = _load()
    sim = {
        "id":         str(uuid.uuid4())[:8],
        "created_at": datetime.now().isoformat(),
        **data,
    }
    sim.setdefault("active_call",     None)
    sim.setdefault("call_history",    [])
    sim.setdefault("nb_history",      [])     # buy/sell ledger for the ETF leg
    sim.setdefault("cash_balance",    0.0)
    sim.setdefault("total_brokerage", 0.0)
    sim.setdefault("total_stt",       0.0)
    sim.setdefault("total_exchange",  0.0)
    sim.setdefault("total_other_charges", 0.0)
    sim.setdefault("total_gst",       0.0)
    sim.setdefault("total_premium_received", 0.0)
    sim.setdefault("total_premium_paid_back", 0.0)
    sim.setdefault("realised_options_pnl", 0.0)
    sim.setdefault("realised_etf_pnl",     0.0)
    sim.setdefault("realised_tax_paid",    0.0)
    sim.setdefault("notes",          "")
    sims.append(sim)
    _save(sims)
    return sim


def update_simulation(sim_id: str, patch: dict) -> dict | None:
    sims = _load()
    for i, s in enumerate(sims):
        if s.get("id") == sim_id:
            sims[i] = {**s, **patch}
            _save(sims)
            return sims[i]
    return None


def delete_simulation(sim_id: str) -> bool:
    sims = _load()
    new_sims = [s for s in sims if s.get("id") != sim_id]
    if len(new_sims) == len(sims):
        return False
    _save(new_sims)
    return True


def append_call_cycle(sim_id: str, cycle: dict) -> dict | None:
    """Add a closed cycle to history."""
    sim = get_simulation(sim_id)
    if not sim:
        return None
    history = list(sim.get("call_history", []))
    history.append(cycle)
    return update_simulation(sim_id, {"call_history": history, "active_call": None})
