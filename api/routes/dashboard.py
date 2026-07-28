"""Home dashboard: a unified portfolio view (value · CAGR · income · liquidity)
plus income-receipt confirmation. Goal-planning and urgent-cash plans are
computed on the frontend from the positions this returns."""
from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from ..dashboard import aggregate
from ..dashboard import store

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/summary")
def summary(gold24k: Optional[float] = None, silver: Optional[float] = None):
    # gold24k/silver = the user's local ₹/gram overrides from the Gold page, so the
    # dashboard gold value matches the Gold tab. Omitted → live spot rate.
    return aggregate.build_dashboard(gold24k=gold24k, silver=silver)


class Receipt(BaseModel):
    key: str
    received: bool
    period: str | None = None
    amount: float | None = None


@router.post("/receipt")
def set_receipt(r: Receipt):
    today = date.today()
    period = r.period or f"{today.year:04d}-{today.month:02d}"
    store.set_receipt(period, r.key, r.received, r.amount)
    # just record it — the client updates its tallies optimistically, so we
    # skip the (expensive) full dashboard recompute and return immediately.
    return {"ok": True, "key": r.key, "received": r.received, "period": period}


@router.get("/settings")
def get_settings():
    """Saved goal/asset assumptions (per-class CAGR, yield, sell mode)."""
    return store.get_setting("goal")


@router.put("/settings")
def put_settings(body: dict):
    return store.set_setting("goal", body or {})


@router.get("/nav-pins")
def get_nav_pins():
    """The user's pinned sidebar tabs, in their chosen order. Stored durably in
    app_settings so pins survive across devices and browser cache clears."""
    v = store.get_setting("nav_pins")
    pins = v.get("pins") if isinstance(v, dict) else None
    return {"pins": [str(p) for p in pins] if isinstance(pins, list) else []}


@router.put("/nav-pins")
def put_nav_pins(body: dict):
    raw = body.get("pins") if isinstance(body, dict) else None
    # keep order, drop blanks/dupes so a tab can only be pinned once
    seen, pins = set(), []
    for p in (raw or []):
        s = str(p).strip()
        if s and s not in seen:
            seen.add(s); pins.append(s)
    store.set_setting("nav_pins", {"pins": pins})
    return {"pins": pins}
