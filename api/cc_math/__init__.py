"""
Modular options-trading math package.

Submodules
----------
black_scholes : Pure BS pricing, Greeks, implied vol solver
strategy      : Per-cycle and annualised expected return for Wheel / CC / Protected Wheel / Iron Condor
taxation      : Indian charge + tax calculations (re-exports cc_charges helpers)
sensitivity   : Parameter sweeps and Monte Carlo

Each submodule is independently testable and side-effect-free, so future
strategies (calendar spreads, ratio writes, etc.) can be added by writing one
new file and registering it in `strategy.STRATEGY_REGISTRY`.
"""
from api.cc_math import black_scholes, strategy, sensitivity
from api.cc_math import taxation

__all__ = ["black_scholes", "strategy", "sensitivity", "taxation"]
