"""
Strategy Math API — exposes the cc_math package over HTTP for the UI.

Endpoints:
  POST /api/cc-math/analyze         — run one strategy with given params
  POST /api/cc-math/compare         — run all strategies with same params
  POST /api/cc-math/sensitivity     — sweep one parameter, return curve

All endpoints accept the same StrategyParams body so the UI binds one input
panel to all four endpoints.
"""
from __future__ import annotations
from dataclasses import asdict, fields as _dc_fields
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from api.cc_math import strategy as _strat
from api.cc_math import sensitivity as _sens
from api.cc_math.strategy import StrategyParams

router = APIRouter(prefix="/api/cc-math", tags=["cc_math"])

# Field names that StrategyParams actually accepts. We filter the Pydantic
# model_dump() through this set so subclasses (which add extra fields like
# `strategy` or `param_name`) don't leak into the dataclass constructor.
_STRATEGY_PARAM_FIELDS = {f.name for f in _dc_fields(StrategyParams)}


# ── Pydantic input schema (mirrors StrategyParams) ───────────────────────────

class ParamsIn(BaseModel):
    spot:           float = 24000.0
    capital:        float = 1_800_000.0
    sigma:          float = Field(0.15, ge=0.05, le=1.0)
    mu:             float = Field(0.12, ge=-0.5, le=0.5)
    risk_free:      float = Field(0.065, ge=0.0, le=0.20)
    cash_yield:     float = Field(0.065, ge=0.0, le=0.20)
    T_months:       float = Field(1.0, ge=0.1, le=12.0)
    alpha_put:      float = Field(0.03, ge=0.0, le=0.20)
    alpha_call:     float = Field(0.03, ge=0.0, le=0.20)
    alpha_hedge:    float = Field(0.07, ge=0.01, le=0.30)
    hedge_T_months: float = Field(3.0, ge=1.0, le=12.0)
    lot_size:       int   = 75
    nb_ratio:       float = 100.0
    slab_rate:      float = Field(0.30, ge=0.0, le=0.50)
    cess_rate:      float = Field(0.04, ge=0.0, le=0.10)
    friction_pct:   float = Field(0.005, ge=0.0, le=0.05)

    def to_dataclass(self) -> StrategyParams:
        # Only forward fields that StrategyParams actually defines — subclasses
        # (AnalyzeIn, SensitivityIn) add extra fields that must be stripped.
        data = {k: v for k, v in self.model_dump().items()
                if k in _STRATEGY_PARAM_FIELDS}
        return StrategyParams(**data)


class AnalyzeIn(ParamsIn):
    strategy: str = "wheel"


class SensitivityIn(ParamsIn):
    strategy:    str    = "wheel"
    param_name:  str    = "alpha_call"
    low:         float
    high:        float
    steps:       int    = Field(25, ge=2, le=200)


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/strategies")
def list_strategies():
    """Returns the list of registered strategies for the UI to populate the
       strategy selector."""
    out = []
    for key, fn in _strat.STRATEGY_REGISTRY.items():
        # Quick analyse with defaults to capture description
        a = fn(StrategyParams())
        out.append({
            "key":         key,
            "name":        a.name,
            "description": a.description,
        })
    return {"strategies": out}


@router.post("/analyze")
def analyze(body: AnalyzeIn):
    """Run ONE strategy with the given parameters, return full breakdown."""
    if body.strategy not in _strat.STRATEGY_REGISTRY:
        raise HTTPException(
            400,
            f"Unknown strategy '{body.strategy}'. Available: {list(_strat.STRATEGY_REGISTRY)}",
        )
    params = body.to_dataclass()
    result = _strat.analyze(body.strategy, params)
    return asdict(result)


@router.post("/compare")
def compare(body: ParamsIn):
    """Run ALL strategies with the same params — for the side-by-side view."""
    params = body.to_dataclass()
    results = _strat.analyze_all(params)
    return {key: asdict(r) for key, r in results.items()}


@router.post("/sensitivity")
def sensitivity(body: SensitivityIn):
    """Sweep one parameter, return the curve of net return vs that parameter."""
    if body.strategy not in _strat.STRATEGY_REGISTRY:
        raise HTTPException(400, f"Unknown strategy '{body.strategy}'")
    params = body.to_dataclass()
    try:
        curve = _sens.sweep(
            body.strategy, params,
            param_name=body.param_name,
            low=body.low, high=body.high, steps=body.steps,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "strategy":   body.strategy,
        "param_name": body.param_name,
        "curve":      curve,
    }
