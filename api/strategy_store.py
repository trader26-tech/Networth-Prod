"""
Strategy Store: tracks multi-leg option strategies, P&L, SL/target state.
Persists to strategy_state.json. The auto-SL loop lives in main.py's ticker task.
"""
import json, uuid, datetime, os
from typing import Optional, Callable

STATE_FILE = os.path.join(os.path.dirname(__file__), "strategy_state.json")


def _now() -> str:
    return datetime.datetime.now().isoformat()[:19]


class StrategyStore:
    def __init__(self):
        self._data: dict = {}
        self._load()

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load(self):
        try:
            with open(STATE_FILE) as f:
                self._data = json.load(f).get("strategies", {})
        except (FileNotFoundError, json.JSONDecodeError):
            self._data = {}

    def _save(self):
        with open(STATE_FILE, "w") as f:
            json.dump({"strategies": self._data}, f, indent=2, default=str)

    # ── CRUD ──────────────────────────────────────────────────────────────────

    def add(self, *, name: str, underlying: str, expiry: str,
            spot_at_entry: float, legs: list, sl_amount: Optional[float],
            target_amount: Optional[float], use_real: bool = False) -> str:
        """
        legs: [{symbol, exchange, type, strike, transaction_type, qty, lot_size, entry_premium, order_id}]
        sl_amount: negative value (e.g. -5000 means exit if P&L <= -5000)
        target_amount: positive value (e.g. 7500 means exit if P&L >= 7500)
        """
        sid = str(uuid.uuid4())[:8].upper()
        net_credit = sum(
            (-1 if l["transaction_type"] == "BUY" else 1)
            * l["entry_premium"] * l["qty"] * l["lot_size"]
            for l in legs
        )
        self._data[sid] = {
            "id": sid,
            "name": name,
            "underlying": underlying,
            "expiry": expiry,
            "spot_at_entry": round(spot_at_entry, 2),
            "legs": legs,
            "net_credit": round(net_credit, 2),
            "sl_amount": sl_amount,
            "target_amount": target_amount,
            "status": "open",           # open | closed | sl_hit | target_hit
            "use_real": use_real,
            "entry_time": _now(),
            "exit_time": None,
            "exit_pnl": None,
        }
        self._save()
        return sid

    def get(self, sid: str) -> Optional[dict]:
        return self._data.get(sid)

    def get_all(self) -> list:
        return sorted(self._data.values(), key=lambda s: s["entry_time"], reverse=True)

    def get_open(self) -> list:
        return [s for s in self._data.values() if s["status"] == "open"]

    def get_closed(self) -> list:
        return [s for s in self._data.values() if s["status"] != "open"]

    def close(self, sid: str, status: str, exit_pnl: float):
        if sid in self._data:
            self._data[sid]["status"] = status
            self._data[sid]["exit_time"] = _now()
            self._data[sid]["exit_pnl"] = round(exit_pnl, 2)
            self._save()

    def update_sl_target(self, sid: str, sl: Optional[float], target: Optional[float]):
        if sid in self._data:
            self._data[sid]["sl_amount"] = sl
            self._data[sid]["target_amount"] = target
            self._save()

    # ── P&L ───────────────────────────────────────────────────────────────────

    def calculate_mtm(self, sid: str, price_fn: Callable) -> float:
        """
        price_fn(exchange, symbol) -> float | None
        Returns current unrealised P&L. Fallback to entry premium if price unavailable.
        """
        s = self._data.get(sid)
        if not s:
            return 0.0
        pnl = 0.0
        for leg in s["legs"]:
            current = price_fn(leg["exchange"], leg["symbol"])
            if current is None:
                current = leg["entry_premium"]
            mult = -1 if leg["transaction_type"] == "BUY" else 1
            pnl += mult * (leg["entry_premium"] - current) * leg["qty"] * leg["lot_size"]
        return round(pnl, 2)

    def batch_mtm(self, price_fn: Callable) -> dict:
        """Returns {sid: pnl} for all open strategies."""
        return {s["id"]: self.calculate_mtm(s["id"], price_fn)
                for s in self.get_open()}

    # ── Statistics ────────────────────────────────────────────────────────────

    def today_realized_pnl(self) -> float:
        today = datetime.date.today().isoformat()
        return round(sum(
            s["exit_pnl"] for s in self._data.values()
            if s.get("exit_pnl") is not None and (s.get("exit_time") or "")[:10] == today
        ), 2)

    def stats(self) -> dict:
        closed = [s for s in self._data.values() if s.get("exit_pnl") is not None]
        if not closed:
            return {"total_trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0,
                    "total_pnl": 0.0, "avg_win": 0.0, "avg_loss": 0.0}
        wins = [s["exit_pnl"] for s in closed if s["exit_pnl"] > 0]
        losses = [s["exit_pnl"] for s in closed if s["exit_pnl"] <= 0]
        return {
            "total_trades": len(closed),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(len(wins) / len(closed) * 100, 1),
            "total_pnl": round(sum(s["exit_pnl"] for s in closed), 2),
            "avg_win": round(sum(wins) / len(wins), 2) if wins else 0.0,
            "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0.0,
        }

    def pnl_history(self, days: int = 30) -> list:
        """Daily realised P&L for sparkline chart."""
        buckets: dict = {}
        for s in self._data.values():
            if s.get("exit_pnl") is not None and s.get("exit_time"):
                d = s["exit_time"][:10]
                buckets[d] = round(buckets.get(d, 0.0) + s["exit_pnl"], 2)
        return [{"date": d, "pnl": v} for d, v in sorted(buckets.items())][-days:]


strategy_store = StrategyStore()
