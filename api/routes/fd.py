"""Fixed Deposit routes: per-FD CRUD + derived metrics (maturity, current value,
interest income, payout calendar) and a portfolio summary.

Interest payouts (non-cumulative FDs) feed the dashboard's monthly income; the
current value counts toward net worth. Everything derived is computed on read
(see api/fd/engine.py). Storage: Supabase (fd_deposits) with a JSON-file
fallback. See SUPABASE.md → "Fixed Deposits table".
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..fd import store
from ..fd import engine

router = APIRouter(prefix="/api/fd", tags=["fd"])


def _require_ready() -> None:
    if not store.tables_ready():
        raise HTTPException(503, store.MIGRATION_HINT)


class FdIn(BaseModel):
    owner: Optional[str] = None
    bank: str
    principal: Optional[float] = None
    interest_rate: Optional[float] = None
    start_date: Optional[str] = None
    tenure_months: Optional[int] = None
    compounding_frequency: str = "quarterly"
    payout_type: str = "payout"               # payout | cumulative
    payout_frequency: str = "quarterly"        # monthly | quarterly | half_yearly | yearly
    note: Optional[str] = None
    sellable_on: Optional[str] = None


@router.get("/meta")
def meta():
    return {"payout_types": list(store.PAYOUT_TYPES), "frequencies": list(store.FREQUENCIES)}


@router.get("/banks")
def banks():
    _require_ready()
    return {"banks": store.banks()}


@router.get("/summary")
def summary():
    _require_ready()
    return engine.build_summary(store.list_fds())


@router.get("/items")
def list_items():
    _require_ready()
    return engine.build_summary(store.list_fds())["fds"]


@router.get("/items/{fid}")
def get_item(fid: str):
    _require_ready()
    f = store.get_fd(fid)
    if not f:
        raise HTTPException(404, "FD not found.")
    return engine.enrich(f)


@router.post("/items")
def create_item(body: FdIn):
    _require_ready()
    if not (body.bank or "").strip():
        raise HTTPException(400, "Bank is required.")
    return engine.enrich(store.create_fd(body.model_dump()))


@router.put("/items/{fid}")
def update_item(fid: str, patch: dict):
    _require_ready()
    updated = store.update_fd(fid, patch)
    if not updated:
        raise HTTPException(404, "FD not found.")
    return engine.enrich(updated)


@router.delete("/items/{fid}")
def delete_item(fid: str):
    _require_ready()
    if not store.delete_fd(fid):
        raise HTTPException(404, "FD not found.")
    return {"ok": True}
