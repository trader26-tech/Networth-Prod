"""
Strategy catalog + all scanner endpoints.

To add a new strategy:
  1. Create api/scanners/<name>.py
  2. Add scan_<name> and scan_<name>_all functions
  3. Register two routes below (scan-<name> and scan-<name>-all)
  4. Add entry to api/catalog.py
  5. Add the frontend card in scanner.html
"""
from fastapi import APIRouter, HTTPException
from api.catalog import STRATEGIES
from api.core.charges import compute_charges
from api.scanners.bwb import scan_bwb, scan_bwb_all
from api.scanners.iron_condor import scan_iron_condor, scan_iron_condor_all
from api.scanners.batman import scan_batman, scan_batman_all

router = APIRouter(tags=["scanners"])


@router.get("/api/strategies")
def list_strategies():
    return {"strategies": list(STRATEGIES.values())}


# ── Charges helper ────────────────────────────────────────────────────────────

@router.post("/api/options/charges")
def calc_charges(legs: list[dict], include_exit: bool = True):
    return compute_charges(legs, include_exit)


# ── BWB ───────────────────────────────────────────────────────────────────────

@router.get("/api/options/scan-bwb")
def route_scan_bwb(
    underlying: str = "NIFTY", expiry: str = "",
    max_loss: int = 0, min_profit: int = 1000,
    min_oi: int = 10000, min_volume: int = 100,
    max_spread_pct: float = 5.0, max_atm_pct: float = 5.0,
    min_liquidity: int = 0, min_lots: int = 5,
    min_pop: float = 0.0, min_ev: int = -999999,
    sort_by: str = "profit", max_strikes: int = 8,
    page: int = 1, per_page: int = 30,
):
    info = __import__("api.options_engine", fromlist=["UNDERLYINGS"]).UNDERLYINGS.get(underlying)
    if not info:
        raise HTTPException(400, f"Unknown underlying: {underlying}")
    return scan_bwb(
        underlying=underlying, expiry=expiry,
        max_loss=max_loss, min_profit=min_profit,
        min_oi=min_oi, min_volume=min_volume,
        max_spread_pct=max_spread_pct, max_atm_pct=max_atm_pct,
        min_liquidity=min_liquidity, min_lots=min_lots,
        min_pop=min_pop, min_ev=min_ev,
        sort_by=sort_by, max_strikes=max_strikes,
        page=page, per_page=per_page,
    )


@router.get("/api/options/scan-bwb-all")
def route_scan_bwb_all(
    max_loss: int = 0, min_profit: int = 1000,
    min_oi: int = 10000, min_volume: int = 100,
    max_spread_pct: float = 5.0, max_atm_pct: float = 5.0,
    min_lots: int = 5, min_pop: float = 0.0, min_ev: int = -999999,
    expiries_per_underlying: int = 10, sort_by: str = "profit",
    auto_relax: bool = True, page: int = 1, per_page: int = 30,
):
    return scan_bwb_all(
        max_loss=max_loss, min_profit=min_profit,
        min_oi=min_oi, min_volume=min_volume,
        max_spread_pct=max_spread_pct, max_atm_pct=max_atm_pct,
        min_lots=min_lots, min_pop=min_pop, min_ev=min_ev,
        expiries_per_underlying=expiries_per_underlying,
        sort_by=sort_by, auto_relax=auto_relax,
        page=page, per_page=per_page,
    )


# ── Iron Condor ───────────────────────────────────────────────────────────────

@router.get("/api/options/scan-iron-condor")
def route_scan_iron_condor(
    underlying: str = "NIFTY", expiry: str = "",
    max_loss: int = 10000, min_profit: int = 500,
    min_oi: int = 10000, min_volume: int = 100,
    max_spread_pct: float = 5.0, max_atm_pct: float = 20.0,
    min_lots: int = 5, min_pop: float = 0.0, min_ev: int = -999999,
    min_short_delta: float = 0.10, max_short_delta: float = 0.30,
    min_credit_ratio: float = 0.30, max_wing_count: int = 6,
    sort_by: str = "ev,pop", page: int = 1, per_page: int = 30,
):
    return scan_iron_condor(
        underlying=underlying, expiry=expiry,
        max_loss=max_loss, min_profit=min_profit,
        min_oi=min_oi, min_volume=min_volume,
        max_spread_pct=max_spread_pct, max_atm_pct=max_atm_pct,
        min_lots=min_lots, min_pop=min_pop, min_ev=min_ev,
        min_short_delta=min_short_delta, max_short_delta=max_short_delta,
        min_credit_ratio=min_credit_ratio, max_wing_count=max_wing_count,
        sort_by=sort_by, page=page, per_page=per_page,
    )


@router.get("/api/options/scan-iron-condor-all")
def route_scan_iron_condor_all(
    max_loss: int = 10000, min_profit: int = 500,
    min_oi: int = 10000, min_volume: int = 100,
    max_spread_pct: float = 5.0, max_atm_pct: float = 10.0,
    min_lots: int = 5, min_pop: float = 0.0, min_ev: int = -999999,
    min_short_delta: float = 0.10, max_short_delta: float = 0.30,
    min_credit_ratio: float = 0.30,
    expiries_per_underlying: int = 10, sort_by: str = "ev,pop",
    auto_relax: bool = True, page: int = 1, per_page: int = 30,
):
    return scan_iron_condor_all(
        max_loss=max_loss, min_profit=min_profit,
        min_oi=min_oi, min_volume=min_volume,
        max_spread_pct=max_spread_pct, max_atm_pct=max_atm_pct,
        min_lots=min_lots, min_pop=min_pop, min_ev=min_ev,
        min_short_delta=min_short_delta, max_short_delta=max_short_delta,
        min_credit_ratio=min_credit_ratio,
        expiries_per_underlying=expiries_per_underlying,
        sort_by=sort_by, auto_relax=auto_relax,
        page=page, per_page=per_page,
    )


# ── Batman ────────────────────────────────────────────────────────────────────

@router.get("/api/options/scan-batman")
def route_scan_batman(
    underlying: str = "NIFTY", expiry: str = "",
    max_loss: int = 5000, min_profit: int = 1000,
    min_oi: int = 10000, min_volume: int = 100,
    max_spread_pct: float = 5.0, max_atm_pct: float = 8.0,
    min_lots: int = 5, min_pop: float = 0.0, min_ev: int = -999999,
    sort_by: str = "pop,ev", max_strikes: int = 6,
    page: int = 1, per_page: int = 30,
):
    return scan_batman(
        underlying=underlying, expiry=expiry,
        max_loss=max_loss, min_profit=min_profit,
        min_oi=min_oi, min_volume=min_volume,
        max_spread_pct=max_spread_pct, max_atm_pct=max_atm_pct,
        min_lots=min_lots, min_pop=min_pop, min_ev=min_ev,
        sort_by=sort_by, max_strikes=max_strikes,
        page=page, per_page=per_page,
    )


@router.get("/api/options/scan-batman-all")
def route_scan_batman_all(
    max_loss: int = 5000, min_profit: int = 1000,
    min_oi: int = 10000, min_volume: int = 100,
    max_spread_pct: float = 5.0, max_atm_pct: float = 8.0,
    min_lots: int = 5, min_pop: float = 0.0, min_ev: int = -999999,
    expiries_per_underlying: int = 10, sort_by: str = "pop,ev",
    auto_relax: bool = True, page: int = 1, per_page: int = 30,
):
    return scan_batman_all(
        max_loss=max_loss, min_profit=min_profit,
        min_oi=min_oi, min_volume=min_volume,
        max_spread_pct=max_spread_pct, max_atm_pct=max_atm_pct,
        min_lots=min_lots, min_pop=min_pop, min_ev=min_ev,
        expiries_per_underlying=expiries_per_underlying,
        sort_by=sort_by, auto_relax=auto_relax,
        page=page, per_page=per_page,
    )
