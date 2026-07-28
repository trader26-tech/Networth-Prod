"""
Core mathematical functions shared across all strategy scanners.
All functions are pure (no I/O, no state) — easy to unit-test.
"""
import math


def bs_delta(S: float, K: float, T: float, sigma: float,
             r: float = 0.065, opt_type: str = 'CE') -> float:
    """Black-Scholes delta. Returns positive for CE, negative for PE."""
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0.0
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    nd1 = 0.5 * math.erfc(-d1 / math.sqrt(2))
    return nd1 if opt_type == 'CE' else nd1 - 1.0


def _lognormal_integrate(pnl_fn, spot: float, T: float, sigma: float,
                         lo_mult: float = 0.4, hi_mult: float = 2.0,
                         n: int = 600, r: float = 0.065) -> tuple[float, float]:
    """
    Integrate pnl_fn(S) over log-normal spot distribution.
    Returns (probability_of_profit, expected_value_per_share).
    """
    if spot <= 0 or T <= 0 or sigma <= 0:
        return 0.0, 0.0

    mu_T    = math.log(spot) + (r - 0.5 * sigma * sigma) * T
    sigma_T = sigma * math.sqrt(T)
    if sigma_T <= 0:
        return 0.0, 0.0

    lo   = spot * lo_mult
    hi   = spot * hi_mult
    step = (hi - lo) / n

    prob = 0.0
    ev   = 0.0
    for i in range(n):
        S = lo + (i + 0.5) * step
        if S <= 0:
            continue
        pnl = pnl_fn(S)
        z   = (math.log(S) - mu_T) / sigma_T
        pdf = math.exp(-0.5 * z * z) / (S * sigma_T * math.sqrt(2 * math.pi))
        if pnl > 0:
            prob += pdf * step
        ev += pnl * pdf * step

    return min(max(prob, 0.0), 1.0), ev


# ── BWB ───────────────────────────────────────────────────────────────────────

def bwb_pnl_at_expiry(opt_type: str, K1, K2, K3, P1, P2, P3, S: float) -> float:
    if opt_type == "CE":
        return max(S - K1, 0) - 2 * max(S - K2, 0) + max(S - K3, 0) + (-P1 + 2 * P2 - P3)
    return max(K1 - S, 0) - 2 * max(K2 - S, 0) + max(K3 - S, 0) + (-P1 + 2 * P2 - P3)


def bwb_pop_and_ev(opt_type: str, K1, K2, K3, P1, P2, P3,
                   spot: float, T: float, sigma: float,
                   lot_size: int) -> tuple[float, float]:
    if spot <= 0:
        return 0.0, 0.0
    if T <= 0 or sigma <= 0:
        pnl = bwb_pnl_at_expiry(opt_type, K1, K2, K3, P1, P2, P3, spot)
        return (1.0 if pnl > 0 else 0.0), pnl * lot_size

    prob, ev = _lognormal_integrate(
        lambda S: bwb_pnl_at_expiry(opt_type, K1, K2, K3, P1, P2, P3, S),
        spot, T, sigma, lo_mult=0.4, hi_mult=2.0,
    )
    return prob, ev * lot_size


# ── Iron Condor ───────────────────────────────────────────────────────────────

def ic_pnl_at_expiry(K1, K2, K3, K4, P1, P2, P3, P4, S: float) -> float:
    """
    Iron Condor: +K1 PE, −K2 PE, −K3 CE, +K4 CE
    net_credit = (P2 + P3) − (P1 + P4)
    """
    nc = (P2 + P3) - (P1 + P4)
    return (max(K1 - S, 0) - max(K2 - S, 0)
            - max(S - K3, 0) + max(S - K4, 0) + nc)


def ic_pop_and_ev(K1, K2, K3, K4, P1, P2, P3, P4,
                  spot: float, T: float, sigma: float,
                  lot_size: int) -> tuple[float, float]:
    if spot <= 0:
        return 0.0, 0.0
    if T <= 0 or sigma <= 0:
        pnl = ic_pnl_at_expiry(K1, K2, K3, K4, P1, P2, P3, P4, spot)
        return (1.0 if pnl > 0 else 0.0), pnl * lot_size

    prob, ev = _lognormal_integrate(
        lambda S: ic_pnl_at_expiry(K1, K2, K3, K4, P1, P2, P3, P4, S),
        spot, T, sigma, lo_mult=0.5, hi_mult=1.8,
    )
    return prob, ev * lot_size


# ── Batman ────────────────────────────────────────────────────────────────────

def batman_pnl_at_expiry(K1, K2, K3, K4, K5, K6,
                          P1, P2, P3, P4, P5, P6, S: float) -> float:
    """Batman = Put BWB (K1/K2/K3) + Call BWB (K4/K5/K6)."""
    put_pnl  = (max(K1 - S, 0) - 2 * max(K2 - S, 0) + max(K3 - S, 0)
                + (-P1 + 2 * P2 - P3))
    call_pnl = (max(S - K4, 0) - 2 * max(S - K5, 0) + max(S - K6, 0)
                + (-P4 + 2 * P5 - P6))
    return put_pnl + call_pnl


def batman_pop_and_ev(K1, K2, K3, K4, K5, K6,
                       P1, P2, P3, P4, P5, P6,
                       spot: float, T: float, sigma: float,
                       lot_size: int) -> tuple[float, float]:
    if spot <= 0:
        return 0.0, 0.0
    if T <= 0 or sigma <= 0:
        pnl = batman_pnl_at_expiry(K1, K2, K3, K4, K5, K6, P1, P2, P3, P4, P5, P6, spot)
        return (1.0 if pnl > 0 else 0.0), pnl * lot_size

    prob, ev = _lognormal_integrate(
        lambda S: batman_pnl_at_expiry(K1, K2, K3, K4, K5, K6, P1, P2, P3, P4, P5, P6, S),
        spot, T, sigma, lo_mult=0.4, hi_mult=2.0,
    )
    return prob, ev * lot_size
