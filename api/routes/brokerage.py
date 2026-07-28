"""Brokerage-accounts routes: CRUD on each family member's broker account
(start/end amount + dates) and a computed CAGR summary, including the combined
money-weighted return. See SUPABASE.md → "Brokerage accounts table"."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..brokerage import store
from ..brokerage import engine

router = APIRouter(prefix="/api/brokerage", tags=["brokerage"])


class Account(BaseModel):
    member: str
    broker: str = ""
    start_date: str
    start_amount: float
    end_date: str
    end_amount: float
    note: Optional[str] = ""
    sellable_on: Optional[str] = None


def _require_ready() -> None:
    if not store.tables_ready():
        raise HTTPException(503, store.MIGRATION_HINT)


@router.get("/summary")
def summary():
    _require_ready()
    return engine.build_summary(store.list_accounts())


@router.post("/accounts")
def create(acc: Account):
    _require_ready()
    return store.add_account(acc.model_dump())


@router.put("/accounts/{acc_id}")
def update(acc_id: str, acc: Account):
    _require_ready()
    row = store.update_account(acc_id, acc.model_dump())
    if not row:
        raise HTTPException(404, "Account not found.")
    return row


@router.delete("/accounts/{acc_id}")
def delete(acc_id: str):
    _require_ready()
    return {"deleted": store.delete_account(acc_id)}
