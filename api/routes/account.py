"""Account, portfolio, and order routes."""
from fastapi import APIRouter, HTTPException
from api.models import OrderRequest, ModifyOrderRequest
from api import state

router = APIRouter(tags=["account"])


# ── Account ───────────────────────────────────────────────────────────────────

@router.get("/api/account/profile")
def get_profile():
    kite = state.get_kite()
    if kite:
        try:
            p = kite.profile()
            p["paper_mode"] = True
            return p
        except Exception:
            pass
    return {
        "user_id": "PAPER001", "user_name": "Paper Trader",
        "email": "paper@zerodha.com", "broker": "ZERODHA",
        "exchanges": ["NSE", "BSE", "NFO"],
        "products": ["CNC", "MIS", "NRML"],
        "order_types": ["MARKET", "LIMIT", "SL", "SL-M"],
        "paper_mode": True,
    }


@router.get("/api/account/funds")
def get_funds():
    return state.get_engine().get_funds_summary()


@router.get("/api/account/margins")
def get_margins():
    return state.get_engine().get_margins()


# ── Portfolio ─────────────────────────────────────────────────────────────────

@router.get("/api/portfolio/holdings")
def get_holdings():
    return {"holdings": []}


@router.get("/api/portfolio/positions")
def get_positions():
    return state.get_engine().get_positions()


# ── Orders ────────────────────────────────────────────────────────────────────

@router.get("/api/orders")
def get_orders():
    return state.get_engine().get_orders()


@router.get("/api/orders/trades")
def get_trades():
    return state.get_engine().get_trades()


@router.get("/api/orders/{order_id}/history")
def get_order_history(order_id: str):
    return state.get_engine().order_history(order_id)


@router.post("/api/orders")
def place_order(req: OrderRequest):
    try:
        oid = state.get_engine().place_order(
            variety=req.variety, exchange=req.exchange,
            tradingsymbol=req.tradingsymbol.upper(),
            transaction_type=req.transaction_type,
            quantity=req.quantity, product=req.product,
            order_type=req.order_type, price=req.price,
            trigger_price=req.trigger_price, validity=req.validity,
            tag=req.tag,
        )
        return {"order_id": oid, "status": "success"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/api/orders/{order_id}")
def modify_order(order_id: str, req: ModifyOrderRequest):
    try:
        state.get_engine().modify_order(
            order_id=order_id, variety=req.variety,
            quantity=req.quantity, price=req.price,
            trigger_price=req.trigger_price,
            order_type=req.order_type, validity=req.validity,
        )
        return {"order_id": order_id, "status": "modified"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/api/orders/{order_id}")
def cancel_order(order_id: str, variety: str = "regular"):
    try:
        state.get_engine().cancel_order(order_id=order_id, variety=variety)
        return {"order_id": order_id, "status": "cancelled"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
