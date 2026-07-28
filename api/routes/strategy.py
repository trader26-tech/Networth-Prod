"""Strategy execution, tracking, and SL/target management."""
from fastapi import APIRouter, HTTPException
from api.models import ExecuteStrategyRequest, UpdateSlTargetRequest
from api.strategy_store import strategy_store
from api import state

router = APIRouter(prefix="/api/strategy", tags=["strategy"])


def _ltp_option(exchange: str, symbol: str):
    kite = state.get_kite()
    if kite:
        try:
            key  = f"{exchange}:{symbol}"
            data = kite.ltp([key])
            return data.get(key, {}).get("last_price")
        except Exception:
            pass
    return None


def _place_leg(leg: dict, transaction_type: str, tag: str, use_real: bool) -> str:
    qty  = leg["qty"] * leg["lot_size"]
    kite = state.get_kite()
    if use_real and kite:
        return str(kite.place_order(
            variety="regular", exchange="NFO",
            tradingsymbol=leg["symbol"],
            transaction_type=transaction_type,
            quantity=qty, product="NRML",
            order_type="MARKET",
        ))
    return str(state.get_engine().place_order(
        variety="regular", exchange="NFO", tradingsymbol=leg["symbol"],
        transaction_type=transaction_type,
        quantity=qty, product="NRML",
        order_type="MARKET", tag=tag,
    ))


@router.post("/execute")
def execute_strategy(req: ExecuteStrategyRequest):
    use_real  = req.use_real and state.get_kite() is not None
    order_ids = []
    entry_legs = []

    for leg in req.legs:
        try:
            oid = _place_leg(leg.model_dump(), leg.transaction_type, "strategy", use_real)
            order_ids.append(oid)
            entry_legs.append({
                "symbol":           leg.symbol,
                "exchange":         "NFO",
                "type":             leg.type,
                "strike":           leg.strike,
                "transaction_type": leg.transaction_type,
                "qty":              leg.qty,
                "lot_size":         leg.lot_size,
                "entry_premium":    leg.premium,
                "order_id":         oid,
            })
        except Exception as e:
            raise HTTPException(400, f"Order failed for {leg.symbol}: {e}")

    sid = strategy_store.add(
        name=req.strategy_name,
        underlying=req.underlying,
        expiry=req.expiry,
        spot_at_entry=req.spot_at_entry,
        legs=entry_legs,
        sl_amount=req.sl_amount,
        target_amount=req.target_amount,
        use_real=use_real,
    )
    return {"strategy_id": sid, "order_ids": order_ids, "real": use_real}


@router.post("/square-off/{sid}")
def square_off_strategy(sid: str):
    s = strategy_store.get(sid)
    if not s:
        raise HTTPException(404, "Strategy not found")
    if s["status"] != "open":
        raise HTTPException(400, "Strategy already closed")

    use_real = s.get("use_real", False)
    for leg in s["legs"]:
        exit_txn = "SELL" if leg["transaction_type"] == "BUY" else "BUY"
        try:
            _place_leg(leg, exit_txn, "square_off", use_real)
        except Exception as e:
            print(f"Square-off error {leg['symbol']}: {e}")

    pnl = strategy_store.calculate_mtm(sid, _ltp_option)
    strategy_store.close(sid, "closed", pnl)
    return {"status": "closed", "pnl": pnl}


@router.put("/{sid}/sl-target")
def update_sl_target(sid: str, req: UpdateSlTargetRequest):
    if not strategy_store.get(sid):
        raise HTTPException(404, "Strategy not found")
    strategy_store.update_sl_target(sid, req.sl_amount, req.target_amount)
    return {"status": "updated"}


@router.get("/open")
def get_open_strategies():
    open_strats = strategy_store.get_open()
    return [
        {**s, "current_pnl": strategy_store.calculate_mtm(s["id"], _ltp_option)}
        for s in open_strats
    ]


@router.get("/history")
def get_strategy_history():
    return strategy_store.get_closed()


@router.get("/stats")
def get_strategy_stats():
    return {
        **strategy_store.stats(),
        "today_pnl":  strategy_store.today_realized_pnl(),
        "open_count": len(strategy_store.get_open()),
    }


@router.get("/pnl-history")
def get_pnl_history():
    return strategy_store.pnl_history(60)
