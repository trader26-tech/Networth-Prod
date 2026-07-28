"""Cash / funds-in-hand routes: balances (cash + bank), live ₹ conversion,
where auto-suggest. The most-liquid asset on the dashboard.

Storage: Supabase (cash_funds) with a JSON-file fallback. See SUPABASE.md →
"Cash / funds table".
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..cash import store
from ..cash import engine
from ..salary import fx

router = APIRouter(prefix="/api/cash", tags=["cash"])


def _require_ready() -> None:
    if not store.tables_ready():
        raise HTTPException(503, store.MIGRATION_HINT)


class CashIn(BaseModel):
    owner: Optional[str] = None
    type: str = "bank"                    # cash | bank
    where: Optional[str] = None           # bank name or location
    account_label: Optional[str] = None
    balance: Optional[float] = None
    currency: str = "INR"
    as_of_date: Optional[str] = None
    note: Optional[str] = None


@router.get("/meta")
def meta():
    return {"types": list(store.TYPES), "currencies": fx.CURRENCIES}


@router.get("/fx")
def fx_rates(refresh: bool = False):
    return fx.get_rates(force=refresh)


@router.get("/wheres")
def wheres():
    _require_ready()
    return {"wheres": store.wheres()}


@router.get("/summary")
def summary():
    _require_ready()
    return engine.build_summary(store.list_items())


@router.get("/items")
def list_items():
    _require_ready()
    return engine.build_summary(store.list_items())["entries"]


@router.get("/items/{cid}")
def get_item(cid: str):
    _require_ready()
    c = store.get_item(cid)
    if not c:
        raise HTTPException(404, "Not found.")
    return engine.enrich(c)


@router.post("/items")
def create_item(body: CashIn):
    _require_ready()
    return engine.enrich(store.create_item(body.model_dump()))


@router.put("/items/{cid}")
def update_item(cid: str, patch: dict):
    _require_ready()
    updated = store.update_item(cid, patch)
    if not updated:
        raise HTTPException(404, "Not found.")
    return engine.enrich(updated)


@router.delete("/items/{cid}")
def delete_item(cid: str):
    _require_ready()
    if not store.delete_item(cid):
        raise HTTPException(404, "Not found.")
    return {"ok": True}
