"""Salary / earned-income routes: per-person income CRUD, live FX, bank-label
auto-suggest, and a converted summary.

Salaries are stored raw (amount + currency + frequency + bank account) and
converted to INR at read time so a foreign-currency salary (e.g. KWD) always
reflects today's rate. Storage: Supabase (salary_entries) with a JSON-file
fallback. See SUPABASE.md → "Salary / income table".
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..salary import store
from ..salary import engine
from ..salary import fx

router = APIRouter(prefix="/api/salary", tags=["salary"])


def _require_ready() -> None:
    if not store.tables_ready():
        raise HTTPException(503, store.MIGRATION_HINT)


class EntryIn(BaseModel):
    person: str
    amount: Optional[float] = None
    currency: str = "INR"
    frequency: str = "monthly"          # monthly | annual
    bank_account: Optional[str] = None
    note: Optional[str] = None


@router.get("/currencies")
def currencies():
    """Currencies offered in the picker."""
    return {"currencies": fx.CURRENCIES}


@router.get("/fx")
def fx_rates(refresh: bool = False):
    """Live INR-per-unit rates, cached ~10 min."""
    return fx.get_rates(force=refresh)


@router.get("/banks")
def banks():
    """Distinct bank-account labels already used — for the auto-suggest."""
    _require_ready()
    return {"banks": store.bank_labels()}


@router.get("/summary")
def summary():
    """Enriched entries + per-person monthly INR totals + FX context."""
    _require_ready()
    return engine.build_summary(store.list_entries())


@router.get("/items")
def list_items():
    _require_ready()
    return engine.build_summary(store.list_entries())["entries"]


@router.get("/items/{eid}")
def get_item(eid: str):
    _require_ready()
    e = store.get_entry(eid)
    if not e:
        raise HTTPException(404, "Entry not found.")
    return engine.enrich(e)


@router.post("/items")
def create_item(body: EntryIn):
    _require_ready()
    if not (body.person or "").strip():
        raise HTTPException(400, "Person is required.")
    return engine.enrich(store.create_entry(body.model_dump()))


@router.put("/items/{eid}")
def update_item(eid: str, patch: dict):
    _require_ready()
    updated = store.update_entry(eid, patch)
    if not updated:
        raise HTTPException(404, "Entry not found.")
    return engine.enrich(updated)


@router.delete("/items/{eid}")
def delete_item(eid: str):
    _require_ready()
    if not store.delete_entry(eid):
        raise HTTPException(404, "Entry not found.")
    return {"ok": True}
