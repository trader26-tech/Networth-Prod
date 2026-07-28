"""GTT (Good Till Triggered) routes."""
from fastapi import APIRouter, HTTPException
from api.models import GTTRequest
from api import state

router = APIRouter(prefix="/api/gtt", tags=["gtt"])


@router.get("")
def get_gtts():
    return state.get_engine().get_gtts()


@router.post("")
def place_gtt(req: GTTRequest):
    gtt_id = state.get_engine().place_gtt(
        trigger_type=req.trigger_type,
        tradingsymbol=req.tradingsymbol,
        exchange=req.exchange,
        trigger_values=req.trigger_values,
        last_price=req.last_price,
        orders=req.orders,
    )
    return {"gtt_id": gtt_id}


@router.delete("/{gtt_id}")
def delete_gtt(gtt_id: int):
    try:
        state.get_engine().delete_gtt(gtt_id)
        return {"gtt_id": gtt_id, "status": "deleted"}
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))
