"""Paper trading utility routes."""
from fastapi import APIRouter
from api import state

router = APIRouter(prefix="/api/paper", tags=["paper"])


@router.post("/reset")
def reset_paper():
    state.get_engine().reset()
    return {"status": "reset", "message": "Paper account reset to ₹10,00,000"}
