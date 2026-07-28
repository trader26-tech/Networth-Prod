"""
Black-Scholes pricing, Greeks, and implied-volatility solver — pure functions,
no I/O, no global state. Safe to import from anywhere.

All math here is in standard finance notation:
  S  = spot
  K  = strike
  T  = years to expiry
  r  = risk-free rate (continuous compounding)
  q  = dividend yield (default 0)
  σ  = volatility (decimal, e.g. 0.15 = 15%)
"""
from __future__ import annotations
import math


# ── Standard normal helpers ──────────────────────────────────────────────────

def norm_cdf(x: float) -> float:
    """Φ(x) — cumulative standard normal."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def norm_pdf(x: float) -> float:
    """φ(x) — standard normal density."""
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


# ── d1 / d2 ───────────────────────────────────────────────────────────────────

def d1(S: float, K: float, T: float, r: float, sigma: float, q: float = 0.0) -> float:
    if T <= 0 or sigma <= 0:
        return float("inf") if S > K else float("-inf")
    return (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))


def d2(S: float, K: float, T: float, r: float, sigma: float, q: float = 0.0) -> float:
    return d1(S, K, T, r, sigma, q) - sigma * math.sqrt(T)


# ── Pricing ───────────────────────────────────────────────────────────────────

def call_price(S: float, K: float, T: float, r: float, sigma: float, q: float = 0.0) -> float:
    if T <= 0:
        return max(S - K, 0.0)
    _d1 = d1(S, K, T, r, sigma, q)
    _d2 = d2(S, K, T, r, sigma, q)
    return S * math.exp(-q * T) * norm_cdf(_d1) - K * math.exp(-r * T) * norm_cdf(_d2)


def put_price(S: float, K: float, T: float, r: float, sigma: float, q: float = 0.0) -> float:
    if T <= 0:
        return max(K - S, 0.0)
    _d1 = d1(S, K, T, r, sigma, q)
    _d2 = d2(S, K, T, r, sigma, q)
    return K * math.exp(-r * T) * norm_cdf(-_d2) - S * math.exp(-q * T) * norm_cdf(-_d1)


# ── Greeks ────────────────────────────────────────────────────────────────────

def call_delta(S: float, K: float, T: float, r: float, sigma: float, q: float = 0.0) -> float:
    if T <= 0:
        return 1.0 if S > K else 0.0
    return math.exp(-q * T) * norm_cdf(d1(S, K, T, r, sigma, q))


def put_delta(S: float, K: float, T: float, r: float, sigma: float, q: float = 0.0) -> float:
    if T <= 0:
        return -1.0 if S < K else 0.0
    return -math.exp(-q * T) * norm_cdf(-d1(S, K, T, r, sigma, q))


def gamma(S: float, K: float, T: float, r: float, sigma: float, q: float = 0.0) -> float:
    if T <= 0 or sigma <= 0 or S <= 0:
        return 0.0
    return math.exp(-q * T) * norm_pdf(d1(S, K, T, r, sigma, q)) / (S * sigma * math.sqrt(T))


def vega(S: float, K: float, T: float, r: float, sigma: float, q: float = 0.0) -> float:
    """Per 1% IV move (i.e., ∂Price/∂σ × 0.01)."""
    if T <= 0:
        return 0.0
    return S * math.exp(-q * T) * norm_pdf(d1(S, K, T, r, sigma, q)) * math.sqrt(T) / 100.0


def call_theta(S: float, K: float, T: float, r: float, sigma: float, q: float = 0.0) -> float:
    """Per-day theta (∂Price/∂T × −1/365)."""
    if T <= 0:
        return 0.0
    _d1 = d1(S, K, T, r, sigma, q)
    _d2 = d2(S, K, T, r, sigma, q)
    term1 = -S * math.exp(-q * T) * norm_pdf(_d1) * sigma / (2 * math.sqrt(T))
    term2 = -r * K * math.exp(-r * T) * norm_cdf(_d2)
    term3 = q * S * math.exp(-q * T) * norm_cdf(_d1)
    return (term1 + term2 + term3) / 365.0


def put_theta(S: float, K: float, T: float, r: float, sigma: float, q: float = 0.0) -> float:
    if T <= 0:
        return 0.0
    _d1 = d1(S, K, T, r, sigma, q)
    _d2 = d2(S, K, T, r, sigma, q)
    term1 = -S * math.exp(-q * T) * norm_pdf(_d1) * sigma / (2 * math.sqrt(T))
    term2 = r * K * math.exp(-r * T) * norm_cdf(-_d2)
    term3 = -q * S * math.exp(-q * T) * norm_cdf(-_d1)
    return (term1 + term2 + term3) / 365.0


# ── Implied volatility (Newton-Raphson with safety) ──────────────────────────

def implied_vol(S: float, K: float, T: float, r: float, market_price: float,
                opt_type: str, q: float = 0.0,
                max_iter: int = 50, tol: float = 1e-3) -> float:
    """Returns IV in DECIMAL form (e.g., 0.15 = 15%). Returns 0 on failure."""
    if T <= 0 or market_price <= 0:
        return 0.0
    sigma = 0.25
    for _ in range(max_iter):
        if opt_type.upper() == "CE":
            price = call_price(S, K, T, r, sigma, q)
        else:
            price = put_price(S, K, T, r, sigma, q)
        v = vega(S, K, T, r, sigma, q) * 100  # un-normalize from per-1% to per-1
        diff = price - market_price
        if abs(diff) < tol:
            return sigma
        if abs(v) < 1e-10:
            break
        sigma = max(0.001, min(sigma - diff / v, 5.0))
    return sigma


# ── Probability helpers (for strategy expected-value math) ───────────────────

def prob_S_above(S: float, K: float, T: float, mu: float, sigma: float) -> float:
    """ℙ(S_T > K) under real-world drift μ. Uses log-normal."""
    if T <= 0 or sigma <= 0:
        return 1.0 if S > K else 0.0
    z = (math.log(S / K) + (mu - 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    return norm_cdf(z)


def prob_S_below(S: float, K: float, T: float, mu: float, sigma: float) -> float:
    return 1.0 - prob_S_above(S, K, T, mu, sigma)


def expected_S(S: float, T: float, mu: float) -> float:
    """E[S_T | S_0 = S] under log-normal with real-world drift μ."""
    return S * math.exp(mu * T)


def expected_max_K_minus_S(S: float, K: float, T: float, mu: float, sigma: float) -> float:
    """E[max(K − S_T, 0)] — expected PUT payoff under real-world drift μ.

    Closed form: K·Φ(−d2_μ) − S·e^{μT}·Φ(−d1_μ), where d1/d2 use μ instead of r.
    """
    if T <= 0:
        return max(K - S, 0.0)
    sqrtT = sigma * math.sqrt(T)
    d1_mu = (math.log(S / K) + (mu + 0.5 * sigma * sigma) * T) / sqrtT
    d2_mu = d1_mu - sqrtT
    return K * norm_cdf(-d2_mu) - S * math.exp(mu * T) * norm_cdf(-d1_mu)


def expected_max_S_minus_K(S: float, K: float, T: float, mu: float, sigma: float) -> float:
    """E[max(S_T − K, 0)] — expected CALL payoff under real-world drift μ."""
    if T <= 0:
        return max(S - K, 0.0)
    sqrtT = sigma * math.sqrt(T)
    d1_mu = (math.log(S / K) + (mu + 0.5 * sigma * sigma) * T) / sqrtT
    d2_mu = d1_mu - sqrtT
    return S * math.exp(mu * T) * norm_cdf(d1_mu) - K * norm_cdf(d2_mu)
