"""LIC life-insurance routes: per-policy CRUD + a beginner-friendly summary.

Traditional LIC policies (endowment, money-back), a couple of health / unit-linked
plans, seeded from the family's real register. We store the raw facts and derive
everything readable (status, cover in force, yearly outgo, expected vs received,
maturity ladder) on read — see api/lic/engine.py. Storage: a local JSON file
(api/lic_data/policies.json), so it works out of the box with no migration.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..lic import store
from ..lic import engine

router = APIRouter(prefix="/api/lic", tags=["lic"])


class PolicyIn(BaseModel):
    holder: Optional[str] = None
    policy_number: Optional[str] = None
    plan: str
    plan_type: str = "endowment"          # endowment|money_back|ulip|health|term|whole_life
    term_years: Optional[float] = None
    premium_paying_years: Optional[float] = None
    premium: Optional[float] = None
    premium_frequency: str = "yearly"     # monthly|quarterly|half_yearly|yearly|single
    premium_annual: Optional[float] = None
    paid_by: Optional[str] = None
    start_date: Optional[str] = None
    maturity_date: Optional[str] = None
    sum_assured: Optional[float] = None
    maturity_amount: Optional[float] = None
    bonus: Optional[float] = None
    total_maturity: Optional[float] = None
    status: str = "active"                 # active|matured|surrendered|lapsed
    fund_units: Optional[float] = None
    fund_nav: Optional[float] = None
    fund_value: Optional[float] = None
    invested: Optional[float] = None
    remarks: Optional[str] = None
    note: Optional[str] = None
    whole_life: Optional[bool] = None
    accident_benefit: Optional[float] = None
    nominee: Optional[str] = None
    nominee_relation: Optional[str] = None
    nominee_phone: Optional[str] = None
    agent_name: Optional[str] = None
    agent_phone: Optional[str] = None
    branch: Optional[str] = None


@router.get("/meta")
def meta():
    """Dropdown options for the add/edit form + friendly labels."""
    return {
        "plan_types": list(store.PLAN_TYPES),
        "type_labels": engine.TYPE_LABEL,
        "type_blurbs": engine.TYPE_BLURB,
        "frequencies": list(store.FREQUENCIES),
        "statuses": list(store.STATUSES),
    }


@router.get("/holders")
def holders():
    return {"holders": store.holders()}


@router.get("/summary")
def summary():
    """Household totals + each policy enriched with plain-English facts."""
    return engine.build_summary(store.list_policies())


@router.get("/items")
def list_items():
    return engine.build_summary(store.list_policies())["policies"]


@router.get("/items/{pid}")
def get_item(pid: str):
    p = store.get_policy(pid)
    if not p:
        raise HTTPException(404, "Policy not found.")
    return engine.enrich(p)


@router.post("/items")
def create_item(body: PolicyIn):
    if not (body.plan or "").strip():
        raise HTTPException(400, "Plan name is required.")
    return engine.enrich(store.create_policy(body.model_dump()))


@router.put("/items/{pid}")
def update_item(pid: str, patch: dict):
    updated = store.update_policy(pid, patch)
    if not updated:
        raise HTTPException(404, "Policy not found.")
    return engine.enrich(updated)


@router.delete("/items/{pid}")
def delete_item(pid: str):
    if not store.delete_policy(pid):
        raise HTTPException(404, "Policy not found.")
    return {"ok": True}
