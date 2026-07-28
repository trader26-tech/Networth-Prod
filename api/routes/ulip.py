"""ULIP routes: per-policy CRUD + derived metrics (lock-in, maturity, premiums
paid/remaining, gain, XIRR) and a portfolio summary.

A ULIP is part insurance, part market-linked investment. We store the raw facts
and derive everything on read (see api/ulip/engine.py). The current fund value
is what counts toward net worth. Storage: Supabase (ulip_policies) with a
JSON-file fallback. See SUPABASE.md → "ULIP policies table".
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..ulip import store
from ..ulip import engine

router = APIRouter(prefix="/api/ulip", tags=["ulip"])


def _require_ready() -> None:
    if not store.tables_ready():
        raise HTTPException(503, store.MIGRATION_HINT)


class PolicyIn(BaseModel):
    owner: Optional[str] = None
    insurer: Optional[str] = None
    plan_name: str
    policy_number: Optional[str] = None
    life_assured: Optional[str] = None
    start_date: Optional[str] = None
    policy_term_years: Optional[float] = None
    premium_paying_term_years: Optional[float] = None
    premium_amount: Optional[float] = None
    premium_frequency: str = "yearly"     # monthly|quarterly|half_yearly|yearly|single
    sum_assured: Optional[float] = None
    fund_value: Optional[float] = None
    fund_type: Optional[str] = "equity"   # equity|balanced|debt|other
    note: Optional[str] = None
    sellable_on: Optional[str] = None


@router.get("/meta")
def meta():
    """Dropdown options for the form."""
    return {"frequencies": list(store.FREQUENCIES), "fund_types": list(store.FUND_TYPES)}


@router.get("/insurers")
def insurers():
    """Distinct insurer names already used — for the auto-suggest."""
    _require_ready()
    return {"insurers": store.insurers()}


@router.get("/summary")
def summary():
    """Portfolio totals + each policy enriched with derived metrics."""
    _require_ready()
    return engine.build_summary(store.list_policies())


@router.get("/items")
def list_items():
    _require_ready()
    return engine.build_summary(store.list_policies())["policies"]


@router.get("/items/{pid}")
def get_item(pid: str):
    _require_ready()
    p = store.get_policy(pid)
    if not p:
        raise HTTPException(404, "Policy not found.")
    return engine.enrich(p)


@router.post("/items")
def create_item(body: PolicyIn):
    _require_ready()
    if not (body.plan_name or "").strip():
        raise HTTPException(400, "Plan name is required.")
    return engine.enrich(store.create_policy(body.model_dump()))


@router.put("/items/{pid}")
def update_item(pid: str, patch: dict):
    _require_ready()
    updated = store.update_policy(pid, patch)
    if not updated:
        raise HTTPException(404, "Policy not found.")
    return engine.enrich(updated)


@router.delete("/items/{pid}")
def delete_item(pid: str):
    _require_ready()
    if not store.delete_policy(pid):
        raise HTTPException(404, "Policy not found.")
    return {"ok": True}
