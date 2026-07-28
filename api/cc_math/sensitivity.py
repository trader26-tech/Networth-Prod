"""
Parameter sweeps for sensitivity analysis.

Given a strategy + base parameters, produce a curve of expected return as a
single parameter is varied. The UI uses these curves to draw "what-if"
charts — drag a slider, see the corresponding curve update.
"""
from __future__ import annotations
from dataclasses import replace
from api.cc_math import strategy as _strat
from api.cc_math.strategy import StrategyParams


def sweep(strategy_key: str, base: StrategyParams, *,
          param_name: str, low: float, high: float, steps: int = 25) -> list[dict]:
    """Vary one numeric attribute of `base` from `low` to `high` in `steps`,
       return a list of {x, monthly_net_pct, annual_net_pct, sharpe} dicts."""
    if not hasattr(base, param_name):
        raise ValueError(f"StrategyParams has no field '{param_name}'")
    if steps < 2:
        steps = 2

    out: list[dict] = []
    for i in range(steps):
        x = low + (high - low) * i / (steps - 1)
        p = replace(base, **{param_name: x})
        a = _strat.analyze(strategy_key, p)
        out.append({
            "x": round(x, 4),
            "monthly_net_pct": a.monthly_net_pct,
            "annual_net_pct":  a.annual_net_pct,
            "sharpe":          a.sharpe_estimate,
            "max_loss":        a.max_loss_per_cycle,
            "max_gain":        a.max_gain_per_cycle,
        })
    return out
