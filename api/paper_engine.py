"""
Paper Trading Engine — simulates order execution with virtual money.
All state is in-memory (reset on restart) + persisted to paper_state.json.
"""
import json, time, uuid, random, os
from datetime import datetime, date
from typing import Optional

STATE_FILE = os.path.join(os.path.dirname(__file__), "paper_state.json")

DEFAULT_STATE = {
    "cash": 1_000_000.0,   # ₹10 lakh virtual capital
    "orders": [],
    "trades": [],
    "positions": {},       # key: "EXCHANGE:SYMBOL:PRODUCT"
    "holdings": {},        # key: "SYMBOL" (CNC settled)
    "gtts": [],
    "next_order_num": 1000,
}


def _load() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return json.loads(json.dumps(DEFAULT_STATE))


def _save(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, default=str)


class PaperEngine:
    def __init__(self, price_fn=None):
        self.state = _load()
        self._price_fn = price_fn   # callable(exchange, symbol) -> float | None

    def _get_price(self, exchange: str, symbol: str) -> float:
        if self._price_fn:
            try:
                p = self._price_fn(exchange, symbol)
                if p and p > 0:
                    return float(p)
            except Exception:
                pass
        # Deterministic mock price based on symbol hash
        base = abs(hash(symbol)) % 4000 + 100
        jitter = random.uniform(-0.005, 0.005)
        return round(base * (1 + jitter), 2)

    def _new_order_id(self) -> str:
        n = self.state["next_order_num"]
        self.state["next_order_num"] += 1
        return f"PAPER{n:08d}"

    # ── Orders ──────────────────────────────────────────────────────────────

    def place_order(self, *, variety, exchange, tradingsymbol, transaction_type,
                    quantity, product, order_type, price=None, trigger_price=None,
                    validity="DAY", tag=None, disclosed_quantity=0) -> str:
        order_id = self._new_order_id()
        ltp = self._get_price(exchange, tradingsymbol)
        exec_price = price if (order_type == "LIMIT" and price) else ltp
        now = datetime.now()

        order = {
            "order_id": order_id,
            "variety": variety,
            "exchange": exchange,
            "tradingsymbol": tradingsymbol,
            "transaction_type": transaction_type,
            "quantity": quantity,
            "filled_quantity": 0,
            "pending_quantity": quantity,
            "product": product,
            "order_type": order_type,
            "price": price or 0.0,
            "trigger_price": trigger_price or 0.0,
            "average_price": 0.0,
            "validity": validity,
            "status": "OPEN",
            "status_message": "",
            "tag": tag or "",
            "order_timestamp": now.isoformat(),
            "exchange_timestamp": now.isoformat(),
            "disclosed_quantity": disclosed_quantity,
        }

        # Immediate fill for MARKET orders; LIMIT fills if price is reasonable
        should_fill = (order_type == "MARKET") or (
            order_type == "LIMIT" and (
                (transaction_type == "BUY" and exec_price >= ltp * 0.98) or
                (transaction_type == "SELL" and exec_price <= ltp * 1.02)
            )
        )

        if should_fill:
            fill_price = exec_price
            cost = fill_price * quantity
            if transaction_type == "BUY":
                if self.state["cash"] < cost:
                    order["status"] = "REJECTED"
                    order["status_message"] = "Insufficient paper funds"
                else:
                    self.state["cash"] -= cost
                    self._update_position(exchange, tradingsymbol, product, quantity, fill_price, "BUY")
                    order["status"] = "COMPLETE"
                    order["filled_quantity"] = quantity
                    order["pending_quantity"] = 0
                    order["average_price"] = fill_price
                    self._add_trade(order_id, exchange, tradingsymbol, product, "BUY", quantity, fill_price)
            else:
                self.state["cash"] += cost
                self._update_position(exchange, tradingsymbol, product, quantity, fill_price, "SELL")
                order["status"] = "COMPLETE"
                order["filled_quantity"] = quantity
                order["pending_quantity"] = 0
                order["average_price"] = fill_price
                self._add_trade(order_id, exchange, tradingsymbol, product, "SELL", quantity, fill_price)

        self.state["orders"].append(order)
        _save(self.state)
        return order_id

    def modify_order(self, *, order_id, variety, quantity=None, price=None,
                     trigger_price=None, order_type=None, validity=None) -> str:
        for o in self.state["orders"]:
            if o["order_id"] == order_id and o["status"] == "OPEN":
                if quantity: o["quantity"] = quantity
                if price: o["price"] = price
                if trigger_price: o["trigger_price"] = trigger_price
                if order_type: o["order_type"] = order_type
                if validity: o["validity"] = validity
                _save(self.state)
                return order_id
        raise ValueError(f"Order {order_id} not found or not modifiable")

    def cancel_order(self, *, order_id, variety) -> str:
        for o in self.state["orders"]:
            if o["order_id"] == order_id and o["status"] == "OPEN":
                o["status"] = "CANCELLED"
                _save(self.state)
                return order_id
        raise ValueError(f"Order {order_id} not found or already closed")

    def order_history(self, order_id: str) -> list:
        for o in self.state["orders"]:
            if o["order_id"] == order_id:
                return [dict(o, status_message=o.get("status_message", ""))]
        return []

    # ── Internal helpers ────────────────────────────────────────────────────

    def _update_position(self, exchange, symbol, product, qty, price, side):
        key = f"{exchange}:{symbol}:{product}"
        pos = self.state["positions"].get(key, {
            "exchange": exchange, "tradingsymbol": symbol, "product": product,
            "quantity": 0, "average_price": 0.0, "last_price": price,
            "pnl": 0.0, "unrealised": 0.0, "realised": 0.0,
        })
        if side == "BUY":
            total_cost = pos["average_price"] * pos["quantity"] + price * qty
            pos["quantity"] += qty
            pos["average_price"] = total_cost / pos["quantity"] if pos["quantity"] else 0
        else:
            realised = (price - pos["average_price"]) * qty
            pos["realised"] = pos.get("realised", 0.0) + realised
            pos["quantity"] -= qty
            if pos["quantity"] <= 0:
                pos["quantity"] = 0
                pos["average_price"] = 0.0
        pos["last_price"] = price
        if pos["quantity"] > 0:
            self.state["positions"][key] = pos
        elif key in self.state["positions"]:
            del self.state["positions"][key]

    def _add_trade(self, order_id, exchange, symbol, product, side, qty, price):
        self.state["trades"].append({
            "trade_id": f"T{int(time.time()*1000)}",
            "order_id": order_id,
            "exchange": exchange,
            "tradingsymbol": symbol,
            "product": product,
            "transaction_type": side,
            "quantity": qty,
            "average_price": price,
            "fill_timestamp": datetime.now().isoformat(),
        })

    # ── Read endpoints ───────────────────────────────────────────────────────

    def get_orders(self) -> list:
        return list(self.state["orders"])

    def get_trades(self) -> list:
        return list(self.state["trades"])

    def get_positions(self) -> dict:
        positions = list(self.state["positions"].values())
        for p in positions:
            ltp = self._get_price(p["exchange"], p["tradingsymbol"])
            p["last_price"] = ltp
            p["unrealised"] = (ltp - p["average_price"]) * p["quantity"]
            p["pnl"] = p["unrealised"] + p.get("realised", 0.0)
        return {"net": positions, "day": positions}

    def get_margins(self) -> dict:
        equity = sum(
            p["last_price"] * p["quantity"]
            for p in self.state["positions"].values()
        )
        return {
            "equity": {
                "enabled": True,
                "net": self.state["cash"] + equity,
                "available": {"live_balance": self.state["cash"], "opening_balance": self.state["cash"]},
                "utilised": {"debits": 0, "exposure": equity},
            },
            "commodity": {"enabled": False}
        }

    def get_funds_summary(self) -> dict:
        return {
            "cash": round(self.state["cash"], 2),
            "paper_mode": True,
            "initial_capital": DEFAULT_STATE["cash"],
            "pnl": round(self._total_pnl(), 2),
        }

    def _total_pnl(self) -> float:
        pnl = 0.0
        for p in self.state["positions"].values():
            ltp = self._get_price(p["exchange"], p["tradingsymbol"])
            pnl += (ltp - p["average_price"]) * p["quantity"] + p.get("realised", 0.0)
        for t in self.state["trades"]:
            pass  # already in positions
        return pnl

    def reset(self):
        self.state = json.loads(json.dumps(DEFAULT_STATE))
        _save(self.state)

    # ── GTT ─────────────────────────────────────────────────────────────────

    def place_gtt(self, *, trigger_type, tradingsymbol, exchange, trigger_values,
                  last_price, orders) -> int:
        gtt_id = len(self.state["gtts"]) + 1
        self.state["gtts"].append({
            "id": gtt_id,
            "trigger_type": trigger_type,
            "tradingsymbol": tradingsymbol,
            "exchange": exchange,
            "trigger_values": trigger_values,
            "last_price": last_price,
            "orders": orders,
            "status": "active",
            "created_at": datetime.now().isoformat(),
        })
        _save(self.state)
        return gtt_id

    def delete_gtt(self, gtt_id: int) -> int:
        for g in self.state["gtts"]:
            if g["id"] == gtt_id:
                g["status"] = "deleted"
                _save(self.state)
                return gtt_id
        raise ValueError(f"GTT {gtt_id} not found")

    def get_gtts(self) -> list:
        return [g for g in self.state["gtts"] if g["status"] == "active"]
