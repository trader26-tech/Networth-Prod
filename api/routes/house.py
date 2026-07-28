"""Bay Villa funding-plan routes: persist the user-edited plan (sources,
milestones, allocations) as one JSON blob in the durable app_cache KV — same
pattern as F&O statements / bond SIPs. The frontend owns the plan shape; the
server just stores whatever it sends and hands it back.
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from ..portfolio import store as kv

router = APIRouter(prefix="/api/house", tags=["house"])

_PLAN_KEY = "house_funding_plan"


class PlanIn(BaseModel):
    plan: dict


@router.get("/plan")
def get_plan():
    rec = kv.cache_get(_PLAN_KEY)
    plan = rec.get("value") if rec and isinstance(rec.get("value"), dict) else None
    return {"plan": plan, "durable": kv.cache_durable(),
            "updated_at": (rec or {}).get("updated_at")}


@router.put("/plan")
def save_plan(body: PlanIn):
    kv.cache_set(_PLAN_KEY, body.plan)
    return {"ok": True, "durable": kv.cache_durable()}


@router.delete("/plan")
def reset_plan():
    kv.cache_set(_PLAN_KEY, None)
    return {"ok": True}
