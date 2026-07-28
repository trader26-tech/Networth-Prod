"""WebSocket ticker + strategy SL/Target auto-monitor."""
import asyncio
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from api import mock_market, state
from api.strategy_store import strategy_store

router = APIRouter(tags=["ws"])


class ConnectionManager:
    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket):
        self.active = [w for w in self.active if w is not ws]

    async def broadcast(self, data: dict):
        dead = []
        for ws in self.active:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


manager = ConnectionManager()
_ticker_task = None


async def _ticker_loop():
    symbols    = list(mock_market.BASE_PRICES.keys())[:20]
    tick_count = 0

    while True:
        tick_count += 1

        # ── Price ticks ───────────────────────────────────────────────────────
        ticks = {}
        kite  = state.get_kite()
        for s in symbols:
            if kite:
                try:
                    keys = [f"NSE:{sym}" for sym in symbols]
                    data = kite.ltp(keys)
                    for sym in symbols:
                        p    = data.get(f"NSE:{sym}", {}).get("last_price", mock_market._drift(sym))
                        base = mock_market.BASE_PRICES.get(sym, p)
                        ticks[sym] = {
                            "last_price": p,
                            "change":     round(p - base, 2),
                            "change_pct": round((p - base) / base * 100, 2),
                        }
                    break
                except Exception:
                    pass
            ltp  = mock_market._drift(s)
            base = mock_market.BASE_PRICES.get(s, ltp)
            ticks[s] = {
                "last_price": ltp,
                "change":     round(ltp - base, 2),
                "change_pct": round((ltp - base) / base * 100, 2),
            }

        await manager.broadcast({"type": "tick", "data": ticks})

        # ── Strategy SL/Target monitor (every 5 ticks) ────────────────────────
        if tick_count % 5 == 0:
            open_strats = strategy_store.get_open()
            if open_strats:
                option_prices: dict = {}
                if kite:
                    all_keys = list({f"NFO:{leg['symbol']}"
                                     for s in open_strats for leg in s["legs"]})
                    try:
                        raw = kite.ltp(all_keys)
                        option_prices = {k.split(":", 1)[1]: v.get("last_price")
                                         for k, v in raw.items() if v.get("last_price")}
                    except Exception:
                        pass

                def opt_price(exchange, symbol):
                    return option_prices.get(symbol)

                mtm_broadcast = {}
                for s in open_strats:
                    pnl  = strategy_store.calculate_mtm(s["id"], opt_price)
                    mtm_broadcast[s["id"]] = pnl
                    sl   = s.get("sl_amount")
                    tgt  = s.get("target_amount")
                    triggered = None
                    if sl  is not None and pnl <= sl:  triggered = "sl_hit"
                    elif tgt is not None and pnl >= tgt: triggered = "target_hit"

                    if triggered:
                        use_real = s.get("use_real", False)
                        engine   = state.get_engine()
                        for leg in s["legs"]:
                            exit_txn = "SELL" if leg["transaction_type"] == "BUY" else "BUY"
                            qty      = leg["qty"] * leg["lot_size"]
                            try:
                                if use_real and kite:
                                    kite.place_order(
                                        variety="regular", exchange="NFO",
                                        tradingsymbol=leg["symbol"],
                                        transaction_type=exit_txn,
                                        quantity=qty, product="NRML",
                                        order_type="MARKET",
                                    )
                                else:
                                    engine.place_order(
                                        variety="regular", exchange="NFO",
                                        tradingsymbol=leg["symbol"],
                                        transaction_type=exit_txn,
                                        quantity=qty, product="NRML",
                                        order_type="MARKET", tag="auto_exit",
                                    )
                            except Exception as e:
                                print(f"Auto-exit error {leg['symbol']}: {e}")
                        strategy_store.close(s["id"], triggered, pnl)
                        await manager.broadcast({
                            "type": triggered,
                            "strategy_id":   s["id"],
                            "strategy_name": s["name"],
                            "pnl": pnl,
                        })

                if mtm_broadcast:
                    await manager.broadcast({"type": "strategy_mtm", "data": mtm_broadcast})

        await asyncio.sleep(1)


@router.websocket("/ws/ticker")
async def ticker_ws(websocket: WebSocket):
    global _ticker_task
    await manager.connect(websocket)
    if _ticker_task is None or _ticker_task.done():
        _ticker_task = asyncio.create_task(_ticker_loop())
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
