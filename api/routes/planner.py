"""Buy-planner routes: a wishlist of things to buy (₹ price + priority), each
financed either from your monthly surplus ("income") or by selling specific
assets ("savings"). Each asset's sell date lives on the asset's own table and is
read back through the dashboard's positions, so there's no separate sell list.

The scheduling (buyable now vs in N months, collected-so-far) runs client-side
off these rows plus the dashboard. Storage: Supabase (purchase_wishlist) with a
JSON-file fallback. See SUPABASE.md → "Buy-planner table".
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..planner import store

router = APIRouter(prefix="/api/planner", tags=["planner"])


def _require_ready() -> None:
    if not store.tables_ready():
        raise HTTPException(503, store.MIGRATION_HINT)


class WishIn(BaseModel):
    name: str
    price: Optional[float] = None
    priority: Optional[int] = None
    finance_mode: str = "income"             # income | savings
    finance_assets: Optional[str] = None     # JSON list of asset position-keys
    sold_assets: Optional[str] = None        # JSON list of keys already sold
    target_date: Optional[str] = None        # when you want to buy it
    monthly_contribution: Optional[float] = None  # (legacy) ₹/mo set aside
    saved: Optional[float] = None            # ₹ actually set aside so far
    bought: bool = False
    note: Optional[str] = None


class ReorderIn(BaseModel):
    order: list[str]


@router.get("/summary")
def summary():
    """Everything the planner needs in one shot."""
    _require_ready()
    return {"wishlist": store.list_wishlist()}


# ── wishlist ──────────────────────────────────────────────────────────────────
@router.get("/wishlist")
def list_wishlist():
    _require_ready()
    return {"items": store.list_wishlist()}


@router.post("/wishlist")
def create_wishlist(body: WishIn):
    _require_ready()
    if not (body.name or "").strip():
        raise HTTPException(400, "Name is required.")
    return store.create_wishlist_item(body.model_dump())


@router.put("/wishlist/{eid}")
def update_wishlist(eid: str, patch: dict):
    _require_ready()
    updated = store.update_wishlist_item(eid, patch)
    if not updated:
        raise HTTPException(404, "Item not found.")
    return updated


@router.delete("/wishlist/{eid}")
def delete_wishlist(eid: str):
    _require_ready()
    if not store.delete_wishlist_item(eid):
        raise HTTPException(404, "Item not found.")
    return {"ok": True}


@router.post("/wishlist/reorder")
def reorder_wishlist(body: ReorderIn):
    _require_ready()
    return {"items": store.reorder_wishlist(body.order)}
