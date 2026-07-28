"""Other-income routes: dividends, interest, rent, bonuses, gifts, etc.

Mirrors the Expenses module — a month-by-month log of actual receipts plus
recurring reminder templates. Each row is stored raw (amount + currency +
frequency) and converted to ₹ at read time (live FX via the salary feed).
Storage: Supabase (other_income) with a JSON-file fallback. See SUPABASE.md →
"Other income table".
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from datetime import date

from ..other_income import store
from ..other_income import engine
from ..salary import fx
from ..dashboard import aggregate

router = APIRouter(prefix="/api/other-income", tags=["other-income"])


def _require_ready() -> None:
    if not store.tables_ready():
        raise HTTPException(503, store.MIGRATION_HINT)


class IncomeIn(BaseModel):
    owner: Optional[str] = None
    source: str
    category: Optional[str] = None
    amount: Optional[float] = None
    currency: str = "INR"
    frequency: str = "monthly"          # weekly|monthly|quarterly|half_yearly|yearly|one_time
    account: Optional[str] = None
    active: bool = True
    on_date: Optional[str] = None       # for one-time: the exact date it falls
    note: Optional[str] = None


class LogIn(BaseModel):
    owner: Optional[str] = None
    source: str
    category: Optional[str] = None
    amount: Optional[float] = None
    currency: str = "INR"
    on_date: Optional[str] = None         # date the income was received (default today)
    account: Optional[str] = None
    template_id: Optional[str] = None
    note: Optional[str] = None


class ReminderIn(BaseModel):
    template_id: str
    period: str                            # YYYY-MM


def _cur_period() -> str:
    t = date.today()
    return f"{t.year:04d}-{t.month:02d}"


@router.get("/meta")
def meta():
    return {"frequencies": list(store.FREQUENCIES), "categories": list(store.CATEGORIES),
            "currencies": fx.CURRENCIES}


@router.get("/fx")
def fx_rates(refresh: bool = False):
    return fx.get_rates(force=refresh)


@router.get("/accounts")
def accounts():
    _require_ready()
    return {"accounts": store.accounts()}


@router.get("/summary")
def summary():
    """Recurring-template projection (monthly-normalised) — feeds dashboard income."""
    _require_ready()
    return engine.build_summary(store.list_templates(), canon=aggregate.canon_owner)


# ── reminder templates (recurring definitions) ────────────────────────────────
@router.get("/templates")
def templates(period: Optional[str] = None):
    """Templates for the reminders, each flagged whether it's already been
    logged in `period`."""
    _require_ready()
    period = period or _cur_period()
    logged = store.log_template_ids(period)
    out = []
    for t in store.list_templates():
        e = engine.enrich(t)
        e["added"] = t["id"] in logged
        out.append(e)
    out.sort(key=lambda x: (x.get("added"), -(x.get("monthly_inr") or 0)))
    return {"period": period, "templates": out}


@router.post("/templates")
def create_template(body: IncomeIn):
    _require_ready()
    if not (body.source or "").strip():
        raise HTTPException(400, "Source is required.")
    data = body.model_dump()
    data["is_template"] = True
    return engine.enrich(store.create_income(data))


@router.put("/templates/{eid}")
def update_template(eid: str, patch: dict):
    _require_ready()
    updated = store.update_income(eid, patch)
    if not updated:
        raise HTTPException(404, "Template not found.")
    return engine.enrich(updated)


@router.delete("/templates/{eid}")
def delete_template(eid: str):
    _require_ready()
    if not store.delete_income(eid):
        raise HTTPException(404, "Template not found.")
    return {"ok": True}


@router.post("/reminder")
def toggle_reminder(body: ReminderIn):
    """Tick a reminder: add it to the log for `period` (or remove if already
    there). Returns the created log entry, or the removed flag."""
    _require_ready()
    tpl = store.get_income(body.template_id)
    if not tpl:
        raise HTTPException(404, "Template not found.")
    existing = [e for e in store.list_log(body.period) if e.get("template_id") == body.template_id]
    if existing:
        for e in existing:
            store.delete_income(e["id"])
        return {"added": False, "removed": len(existing)}
    on_date = date.today().isoformat() if body.period == _cur_period() else f"{body.period}-01"
    entry = {
        "owner": tpl.get("owner"), "source": tpl.get("source"), "category": tpl.get("category"),
        "amount": tpl.get("amount"), "currency": tpl.get("currency"), "frequency": "one_time",
        "account": tpl.get("account"), "active": True, "is_template": False,
        "template_id": body.template_id, "on_date": on_date, "note": tpl.get("note"),
    }
    return {"added": True, "entry": engine.enrich(store.create_income(entry))}


# ── actual income log (the month-by-month table) ──────────────────────────────
@router.get("/log")
def log(period: Optional[str] = None):
    """Actual logged receipts for a month (default current) + totals/splits."""
    _require_ready()
    period = period or _cur_period()
    summ = engine.log_summary(store.list_log(period), canon=aggregate.canon_owner)
    summ["period"] = period
    return summ


@router.post("/log")
def create_log(body: LogIn):
    _require_ready()
    if not (body.source or "").strip():
        raise HTTPException(400, "Source is required.")
    data = body.model_dump()
    data["is_template"] = False
    data["frequency"] = "one_time"
    data["active"] = True
    if not data.get("on_date"):
        data["on_date"] = date.today().isoformat()
    return engine.enrich(store.create_income(data))


@router.put("/log/{eid}")
def update_log(eid: str, patch: dict):
    _require_ready()
    patch = dict(patch); patch["is_template"] = False
    updated = store.update_income(eid, patch)
    if not updated:
        raise HTTPException(404, "Income not found.")
    return engine.enrich(updated)


@router.delete("/log/{eid}")
def delete_log(eid: str):
    _require_ready()
    if not store.delete_income(eid):
        raise HTTPException(404, "Income not found.")
    return {"ok": True}
