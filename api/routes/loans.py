"""Liabilities (loans) routes: per-loan CRUD + a portfolio summary, plus a
"preclose" action that zeroes the outstanding balance.

Outstanding principal (converted to INR for foreign loans) subtracts from net
worth on the dashboard; the monthly EMI counts as an expense against income.
Everything derived is computed on read (see api/loans/engine.py). Storage:
Supabase (loans) with a JSON-file fallback. See SUPABASE.md.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..loans import store
from ..loans import engine
from ..loans import fx_cost as fxcost

router = APIRouter(prefix="/api/loans", tags=["loans"])


def _require_ready() -> None:
    if not store.tables_ready():
        raise HTTPException(503, store.MIGRATION_HINT)


class LoanIn(BaseModel):
    owner: Optional[str] = None
    lender: str
    loan_type: str = "Other"
    account_no: Optional[str] = None
    currency: str = "INR"
    original_amount: Optional[float] = None
    outstanding_principal: Optional[float] = None
    interest_rate: Optional[float] = None
    emi_amount: Optional[float] = None
    emi_frequency: str = "monthly"
    start_date: Optional[str] = None
    maturity_date: Optional[str] = None
    next_installment_date: Optional[str] = None
    installments_paid: Optional[int] = None
    installments_remaining: Optional[int] = None
    status: str = "active"
    closed_on: Optional[str] = None
    note: Optional[str] = None


class PaymentStatusIn(BaseModel):
    loan_id: str
    date: str
    status: str = "pending"       # paid | pending | unpaid


@router.get("/meta")
def meta():
    return {"loan_types": list(store.LOAN_TYPES), "frequencies": list(store.FREQUENCIES)}


@router.get("/lenders")
def lenders():
    _require_ready()
    return {"lenders": store.lenders()}


@router.get("/payment-status")
def list_payment_status():
    """Every installment the user has marked paid / unpaid, keyed by (loan_id, date).
    Anything unmarked defaults to pending (due)."""
    return store.list_payment_status()


@router.put("/payment-status")
def set_payment_status(body: PaymentStatusIn):
    """Mark one loan installment paid / pending / unpaid."""
    return store.set_payment_status(body.loan_id, body.date, body.status)


@router.get("/summary")
def summary():
    _require_ready()
    return engine.build_summary(store.list_loans())


@router.get("/items")
def list_items():
    _require_ready()
    return engine.build_summary(store.list_loans())["loans"]


@router.get("/items/{lid}/fx-cost")
def fx_cost_item(lid: str):
    """Real ₹-adjusted effective interest rate for a foreign-currency loan, with
    the month-by-month series behind it. See api/loans/fx_cost.py."""
    _require_ready()
    l = store.get_loan(lid)
    if not l:
        raise HTTPException(404, "Loan not found.")
    return fxcost.fx_cost(l)


@router.get("/items/{lid}")
def get_item(lid: str):
    _require_ready()
    l = store.get_loan(lid)
    if not l:
        raise HTTPException(404, "Loan not found.")
    return engine.enrich(l)


@router.post("/items")
def create_item(body: LoanIn):
    _require_ready()
    if not (body.lender or "").strip():
        raise HTTPException(400, "Lender is required.")
    return engine.enrich(store.create_loan(body.model_dump()))


@router.put("/items/{lid}")
def update_item(lid: str, patch: dict):
    _require_ready()
    updated = store.update_loan(lid, patch)
    if not updated:
        raise HTTPException(404, "Loan not found.")
    return engine.enrich(updated)


@router.post("/items/{lid}/preclose")
def preclose_item(lid: str):
    """Foreclose a loan: mark it closed and zero the outstanding so it stops
    subtracting from net worth and its EMI stops counting against income."""
    _require_ready()
    updated = store.update_loan(lid, {
        "status": "closed",
        "outstanding_principal": 0,
        "installments_remaining": 0,
        "closed_on": date.today().isoformat(),
    })
    if not updated:
        raise HTTPException(404, "Loan not found.")
    return engine.enrich(updated)


@router.post("/items/{lid}/reopen")
def reopen_item(lid: str):
    """Undo a preclose (in case it was a mistake)."""
    _require_ready()
    updated = store.update_loan(lid, {"status": "active", "closed_on": None})
    if not updated:
        raise HTTPException(404, "Loan not found.")
    return engine.enrich(updated)


@router.delete("/items/{lid}")
def delete_item(lid: str):
    _require_ready()
    if not store.delete_loan(lid):
        raise HTTPException(404, "Loan not found.")
    return {"ok": True}
