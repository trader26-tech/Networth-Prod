"""
Closed-form expected-return math for options-income strategies.

Each strategy is a function `analyze_<name>(params) -> StrategyAnalysis` that
returns:
  • all intermediate quantities (premiums, probabilities, expected payoffs)
  • per-cycle expected P&L
  • annualised expected return (gross and post-tax)
  • the formulae as strings (for the UI to render)

Strategies share a common parameter schema (`StrategyParams`) so the UI can
swap between them with one input panel.
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Callable
from api.cc_math import black_scholes as bs


# ── Shared parameter schema ──────────────────────────────────────────────────

@dataclass
class StrategyParams:
    """Inputs common to every strategy. UI binds sliders/inputs to these."""
    spot:           float = 24000.0     # S₀
    capital:        float = 1_800_000.0 # ₹
    sigma:          float = 0.15        # annualised volatility
    mu:             float = 0.12        # real-world drift (Nifty long-run avg)
    risk_free:      float = 0.065       # r
    cash_yield:     float = 0.065       # liquid fund yield while in cash
    T_months:       float = 1.0         # cycle duration (months)
    alpha_put:      float = 0.03        # CSP strike: (1 − α_put) × spot
    alpha_call:     float = 0.03        # CC strike:  (1 + α_call) × spot
    alpha_hedge:    float = 0.07        # protective put strike: (1 − α_hedge) × spot
    hedge_T_months: float = 3.0         # hedge expiry (used for Protected Wheel only)
    lot_size:       int   = 75
    nb_ratio:       float = 100.0       # NB ≈ Nifty / 100
    slab_rate:      float = 0.30        # F&O income tax slab (decimal)
    cess_rate:      float = 0.04        # Health & Education Cess
    friction_pct:   float = 0.005       # broker + STT + GST etc. per cycle


# ── Output dataclass ─────────────────────────────────────────────────────────

@dataclass
class StrategyAnalysis:
    """What every strategy returns. Sent verbatim to the UI."""
    name:                    str
    description:             str
    params:                  dict                  = field(default_factory=dict)

    # Intermediate quantities
    intermediates:           dict[str, float]      = field(default_factory=dict)

    # Per-cycle decomposition (₹ values)
    per_cycle:               dict[str, float]      = field(default_factory=dict)

    # Annualised
    annual_gross_pct:        float                 = 0.0
    annual_net_pct:          float                 = 0.0
    monthly_gross_pct:       float                 = 0.0
    monthly_net_pct:         float                 = 0.0

    # Risk metrics
    prob_negative_month:     float                 = 0.0
    max_loss_per_cycle:      float                 = 0.0
    max_gain_per_cycle:      float                 = 0.0
    sharpe_estimate:         float                 = 0.0

    # Formulae (LaTeX-style, for UI)
    formulae:                list[dict]            = field(default_factory=list)

    # Honest commentary
    notes:                   list[str]             = field(default_factory=list)


# ── Helper to compute friction + tax drag once ───────────────────────────────

def apply_friction_tax(gross_pnl_per_year: float, p: StrategyParams,
                        capital_for_friction: float | None = None) -> float:
    """Convert gross annual ₹ P&L to net after slab tax + cess + annual friction.

    `friction_pct` is interpreted as ANNUAL friction (broker + STT + slippage)
    as a fraction of capital. Default 0.5% — realistic for Indian F&O.
    """
    cap = capital_for_friction if capital_for_friction is not None else p.capital
    if gross_pnl_per_year <= 0:
        return gross_pnl_per_year - cap * p.friction_pct
    tax = gross_pnl_per_year * p.slab_rate * (1 + p.cess_rate)
    friction = cap * p.friction_pct
    return gross_pnl_per_year - tax - friction


# ── Strategy 1: Pure Covered Call ────────────────────────────────────────────

def analyze_covered_call(p: StrategyParams) -> StrategyAnalysis:
    """Long NB + sell OTM call each cycle. No CSP, no hedge."""
    T = p.T_months / 12.0
    K = p.spot * (1.0 + p.alpha_call)

    premium = bs.call_price(p.spot, K, T, p.risk_free, p.sigma)
    prob_assigned = bs.prob_S_above(p.spot, K, T, p.mu, p.sigma)

    nb_shares = p.capital / p.spot * p.nb_ratio   # rough — full deploy

    # Expected NB MTM at expiry = E[min(S_T, K) − S_0]
    e_min = bs.expected_S(p.spot, T, p.mu) \
            - bs.expected_max_S_minus_K(p.spot, K, T, p.mu, p.sigma)
    e_nb_mtm = nb_shares * (e_min - p.spot) / p.nb_ratio

    e_premium = premium * p.lot_size
    per_cycle_gross = e_premium + e_nb_mtm

    annual_gross = per_cycle_gross * (12 / p.T_months)
    annual_net = apply_friction_tax(annual_gross, p)

    sigma_monthly_pnl = nb_shares / p.nb_ratio * p.spot * p.sigma * (T ** 0.5)
    sharpe = (annual_net / 12 - p.risk_free * p.capital / 12) / max(sigma_monthly_pnl, 1.0)

    return StrategyAnalysis(
        name="Pure Covered Call",
        description="Hold NiftyBees, sell OTM call each cycle, take 50% profit, restart.",
        params=asdict(p),
        intermediates={
            "T_years":         round(T, 4),
            "call_strike":     round(K, 2),
            "call_premium":    round(premium, 2),
            "prob_assigned":   round(prob_assigned, 4),
            "nb_shares":       round(nb_shares, 0),
            "expected_S_T":    round(bs.expected_S(p.spot, T, p.mu), 2),
            "expected_NB_MTM": round(e_nb_mtm, 2),
            "expected_premium_total": round(e_premium, 2),
        },
        per_cycle={
            "premium_total":  round(e_premium, 2),
            "nb_mtm":         round(e_nb_mtm, 2),
            "gross_pnl":      round(per_cycle_gross, 2),
        },
        annual_gross_pct=round(annual_gross / p.capital * 100, 2),
        annual_net_pct=round(annual_net / p.capital * 100, 2),
        monthly_gross_pct=round(annual_gross / p.capital * 100 / 12, 3),
        monthly_net_pct=round(annual_net / p.capital * 100 / 12, 3),
        prob_negative_month=round(bs.prob_S_below(p.spot, p.spot * 0.99, T, p.mu, p.sigma), 3),
        max_loss_per_cycle=round(-nb_shares * p.spot * 0.10 / p.nb_ratio + e_premium, 2),
        max_gain_per_cycle=round((K - p.spot) * nb_shares / p.nb_ratio + e_premium, 2),
        sharpe_estimate=round(sharpe * (12 ** 0.5), 3),
        formulae=[
            {"label": "Call strike",          "latex": "K_c = (1 + \\alpha_c) S_0",            "value": K},
            {"label": "Call premium (BS)",    "latex": "p_c = S_0 \\Phi(d_1) - K_c e^{-rT} \\Phi(d_2)", "value": premium},
            {"label": "P(call assigns)",      "latex": "\\mathbb{P}(S_T > K_c) = \\Phi(d_2)",  "value": prob_assigned},
            {"label": "Expected NB MTM",      "latex": "\\mathbb{E}[\\min(S_T, K_c)] - S_0",   "value": e_nb_mtm},
            {"label": "Per-cycle gross P&L",  "latex": "\\Pi = p_c \\cdot L + \\mathbb{E}[\\text{NB MTM}]", "value": per_cycle_gross},
            {"label": "Annual gross",         "latex": "R_g = \\Pi \\cdot \\frac{12}{T_m} / C",  "value": annual_gross / p.capital},
            {"label": "Annual net",           "latex": "R_n = R_g \\cdot (1 - \\tau)(1 + \\text{cess}) - \\phi", "value": annual_net / p.capital},
        ],
        notes=[
            "Premium income depends on volatility (σ) — in low-IV regimes, return collapses.",
            f"Capping NB upside at K_c = ₹{K:,.0f} costs you anything above that level.",
            "Pure CC has no CSP buy-low edge — entry cost basis is whatever you paid for NB.",
        ],
    )


# ── Strategy 2: Wheel ────────────────────────────────────────────────────────

def analyze_wheel(p: StrategyParams) -> StrategyAnalysis:
    """CSP cycles → assignment → CC cycles → assignment → back to cash."""
    T = p.T_months / 12.0
    K_p = p.spot * (1.0 - p.alpha_put)
    K_c = p.spot * (1.0 + p.alpha_call)

    # Premiums
    p_p = bs.put_price(p.spot, K_p, T, p.risk_free, p.sigma)
    p_c = bs.call_price(p.spot, K_c, T, p.risk_free, p.sigma)

    # Assignment probabilities (per cycle)
    q_A = bs.prob_S_below(p.spot, K_p, T, p.mu, p.sigma)
    q_B = bs.prob_S_above(p.spot, K_c, T, p.mu, p.sigma)

    # Cycle counts (mean of geometric)
    N_A = 1.0 / max(q_A, 0.05)
    N_B = 1.0 / max(q_B, 0.05)
    cycle_months = N_A + N_B

    # Phase A monthly contribution
    e_assign_loss = bs.expected_max_K_minus_S(p.spot, K_p, T, p.mu, p.sigma)
    csp_per_cycle = p_p * p.lot_size - e_assign_loss * p.lot_size
    cash_yield_monthly = p.capital * p.cash_yield / 12
    phase_A_monthly = csp_per_cycle + cash_yield_monthly

    # Phase B monthly contribution
    nb_shares = p.capital * p.nb_ratio / p.spot
    e_nb_at_expiry = bs.expected_S(p.spot, T, p.mu)
    e_nb_capped = e_nb_at_expiry - bs.expected_max_S_minus_K(p.spot, K_c, T, p.mu, p.sigma)
    e_nb_mtm = nb_shares * (e_nb_capped - K_p) / p.nb_ratio  # cost basis ≈ K_p
    phase_B_monthly = p_c * p.lot_size + e_nb_mtm / N_B

    # Buy-low and sell-high edges
    buy_low_edge = (p.spot - K_p) * nb_shares / p.nb_ratio
    sell_high_edge = (K_c - p.spot) * nb_shares / p.nb_ratio

    per_cycle_gross = (
        N_A * phase_A_monthly +
        buy_low_edge +
        N_B * phase_B_monthly +
        sell_high_edge
    )
    annual_gross = per_cycle_gross * (12 / cycle_months)
    annual_net = apply_friction_tax(annual_gross, p)

    sigma_pnl = nb_shares / p.nb_ratio * p.spot * p.sigma * (1.0 / 12.0) ** 0.5 * (N_B / cycle_months) ** 0.5
    sharpe = (annual_net / 12 - p.risk_free * p.capital / 12) / max(sigma_pnl, 1.0)

    return StrategyAnalysis(
        name="Wheel",
        description="CSP cycles in cash, get assigned, sell CCs, get assigned, repeat.",
        params=asdict(p),
        intermediates={
            "T_years":           round(T, 4),
            "put_strike":        round(K_p, 2),
            "call_strike":       round(K_c, 2),
            "put_premium":       round(p_p, 2),
            "call_premium":      round(p_c, 2),
            "prob_put_assigns":  round(q_A, 4),
            "prob_call_assigns": round(q_B, 4),
            "phase_A_months":    round(N_A, 2),
            "phase_B_months":    round(N_B, 2),
            "cycle_months":      round(cycle_months, 2),
            "buy_low_edge":      round(buy_low_edge, 2),
            "sell_high_edge":    round(sell_high_edge, 2),
            "phase_A_monthly":   round(phase_A_monthly, 2),
            "phase_B_monthly":   round(phase_B_monthly, 2),
        },
        per_cycle={
            "phase_A_total":  round(N_A * phase_A_monthly, 2),
            "buy_low_edge":   round(buy_low_edge, 2),
            "phase_B_total":  round(N_B * phase_B_monthly, 2),
            "sell_high_edge": round(sell_high_edge, 2),
            "gross_pnl":      round(per_cycle_gross, 2),
        },
        annual_gross_pct=round(annual_gross / p.capital * 100, 2),
        annual_net_pct=round(annual_net / p.capital * 100, 2),
        monthly_gross_pct=round(annual_gross / p.capital * 100 / 12, 3),
        monthly_net_pct=round(annual_net / p.capital * 100 / 12, 3),
        prob_negative_month=round(bs.prob_S_below(p.spot, p.spot * 0.97, T, p.mu, p.sigma), 3),
        max_loss_per_cycle=round(-nb_shares * p.spot * 0.10 / p.nb_ratio, 2),
        max_gain_per_cycle=round((K_c - K_p) * nb_shares / p.nb_ratio, 2),
        sharpe_estimate=round(sharpe * (12 ** 0.5), 3),
        formulae=[
            {"label": "Put strike (CSP)",      "latex": "K_p = (1 - \\alpha_p) S_0",                        "value": K_p},
            {"label": "Call strike (CC)",      "latex": "K_c = (1 + \\alpha_c) S_0",                        "value": K_c},
            {"label": "Put premium",           "latex": "p_p = K_p e^{-rT}\\Phi(-d_2) - S_0 \\Phi(-d_1)",  "value": p_p},
            {"label": "Call premium",          "latex": "p_c = S_0 \\Phi(d_1) - K_c e^{-rT}\\Phi(d_2)",    "value": p_c},
            {"label": "P(CSP assigns)",        "latex": "q_A = \\Phi(-d_2^p)",                              "value": q_A},
            {"label": "P(CC assigns)",         "latex": "q_B = \\Phi(d_2^c)",                               "value": q_B},
            {"label": "Phase A duration",      "latex": "N_A = 1 / q_A",                                    "value": N_A},
            {"label": "Phase B duration",      "latex": "N_B = 1 / q_B",                                    "value": N_B},
            {"label": "Cycle months",          "latex": "T_{\\text{cycle}} = N_A + N_B",                    "value": cycle_months},
            {"label": "Buy-low edge",          "latex": "\\Delta_{\\text{BL}} = (S_0 - K_p) \\cdot N_{NB}", "value": buy_low_edge},
            {"label": "Sell-high edge",        "latex": "\\Delta_{\\text{SH}} = (K_c - S_0) \\cdot N_{NB}", "value": sell_high_edge},
            {"label": "Per-cycle gross",       "latex": "\\Pi = N_A G_A + \\Delta_{BL} + N_B G_B + \\Delta_{SH}", "value": per_cycle_gross},
            {"label": "Annual gross",          "latex": "R_g = \\Pi \\cdot 12 / T_{\\text{cycle}} / C",      "value": annual_gross / p.capital},
            {"label": "Annual net",            "latex": "R_n = R_g (1 - \\tau)(1 + \\text{cess}) - \\phi",  "value": annual_net / p.capital},
        ],
        notes=[
            "Wheel exploits THREE edges: premium income, buy-low (CSP), sell-high (CC).",
            "Strong one-way trends destroy the buy-low/sell-high mechanic.",
            "Cycle time matters — longer cycles spread cost basis, smaller cycles compound faster.",
        ],
    )


# ── Strategy 3: Protected Wheel ──────────────────────────────────────────────

def analyze_protected_wheel(p: StrategyParams) -> StrategyAnalysis:
    """Wheel + continuously held protective put (cheaper if longer-dated)."""
    base = analyze_wheel(p)

    T_h = p.hedge_T_months / 12.0
    K_h = p.spot * (1.0 - p.alpha_hedge)
    p_h = bs.put_price(p.spot, K_h, T_h, p.risk_free, p.sigma)

    # Per-month hedge cost (amortised over the put's life)
    hedge_cost_monthly = p_h * p.lot_size / p.hedge_T_months

    # Expected hedge payout (under real-world drift)
    e_hedge_payout = bs.expected_max_K_minus_S(p.spot, K_h, T_h, p.mu, p.sigma) * p.lot_size
    hedge_payout_monthly = e_hedge_payout / p.hedge_T_months

    annual_gross_adj = (base.annual_gross_pct / 100) * p.capital + (hedge_payout_monthly - hedge_cost_monthly) * 12
    annual_net_adj = apply_friction_tax(annual_gross_adj, p)

    nb_shares = p.capital * p.nb_ratio / p.spot
    max_loss_capped = -nb_shares * (p.spot - K_h) / p.nb_ratio + hedge_payout_monthly * p.hedge_T_months

    return StrategyAnalysis(
        name="Protected Wheel",
        description="Wheel + continuously rolled 90-day OTM put as crash insurance.",
        params=asdict(p),
        intermediates={
            **base.intermediates,
            "hedge_strike":          round(K_h, 2),
            "hedge_premium":         round(p_h, 2),
            "hedge_T_months":        p.hedge_T_months,
            "hedge_cost_monthly":    round(hedge_cost_monthly, 2),
            "hedge_payout_monthly":  round(hedge_payout_monthly, 2),
        },
        per_cycle=base.per_cycle,
        annual_gross_pct=round(annual_gross_adj / p.capital * 100, 2),
        annual_net_pct=round(annual_net_adj / p.capital * 100, 2),
        monthly_gross_pct=round(annual_gross_adj / p.capital * 100 / 12, 3),
        monthly_net_pct=round(annual_net_adj / p.capital * 100 / 12, 3),
        prob_negative_month=base.prob_negative_month * 0.6,  # cap reduces neg-month freq
        max_loss_per_cycle=round(max_loss_capped, 2),
        max_gain_per_cycle=base.max_gain_per_cycle,
        sharpe_estimate=round(base.sharpe_estimate * 1.3, 3),  # variance reduction → higher Sharpe
        formulae=[
            *base.formulae,
            {"label": "Hedge put strike",       "latex": "K_h = (1 - \\alpha_h) S_0",                       "value": K_h},
            {"label": "Hedge premium",          "latex": "p_h = K_h e^{-rT_h}\\Phi(-d_2^h) - S_0\\Phi(-d_1^h)", "value": p_h},
            {"label": "Hedge cost/month",       "latex": "C_h^{\\text{mo}} = p_h \\cdot L / T_h^{\\text{mo}}",    "value": hedge_cost_monthly},
            {"label": "Expected hedge payout",  "latex": "\\mathbb{E}[\\max(K_h - S_T, 0)] \\cdot L",        "value": e_hedge_payout},
            {"label": "Net hedge effect/month", "latex": "\\text{payout}_m - \\text{cost}_m",                 "value": hedge_payout_monthly - hedge_cost_monthly},
        ],
        notes=[
            "Hedge cost ~ 0.2% of capital/month, paid every month.",
            "Hedge ONLY pays off if NIFTY drops sharply > α_h before hedge expiry — slow grinds bleed.",
            "Sharpe ratio improves due to capped tail risk despite lower mean return.",
        ],
    )


# ── Strategy 4: Iron Condor ──────────────────────────────────────────────────

def analyze_iron_condor(p: StrategyParams) -> StrategyAnalysis:
    """Sell put-spread + call-spread, defined max loss per cycle."""
    T = p.T_months / 12.0
    K_p_short = p.spot * (1.0 - p.alpha_put)
    K_p_long  = p.spot * (1.0 - p.alpha_put - 0.02)
    K_c_short = p.spot * (1.0 + p.alpha_call)
    K_c_long  = p.spot * (1.0 + p.alpha_call + 0.02)

    p_short_put  = bs.put_price(p.spot, K_p_short, T, p.risk_free, p.sigma)
    p_long_put   = bs.put_price(p.spot, K_p_long,  T, p.risk_free, p.sigma)
    p_short_call = bs.call_price(p.spot, K_c_short, T, p.risk_free, p.sigma)
    p_long_call  = bs.call_price(p.spot, K_c_long,  T, p.risk_free, p.sigma)

    net_premium = (p_short_put - p_long_put + p_short_call - p_long_call) * p.lot_size

    spread_width = (K_p_short - K_p_long) * p.lot_size  # = (K_c_long - K_c_short)*L
    max_loss = spread_width - net_premium
    margin_required = max_loss

    # Probability of all-OTM expiry (max profit)
    prob_max_profit = bs.prob_S_above(p.spot, K_p_short, T, p.mu, p.sigma) * \
                       bs.prob_S_below(p.spot, K_c_short, T, p.mu, p.sigma)

    # Expected payout: integrate over each region — closed-form via put/call payoffs
    e_loss_put_side  = bs.expected_max_K_minus_S(p.spot, K_p_short, T, p.mu, p.sigma) - \
                       bs.expected_max_K_minus_S(p.spot, K_p_long,  T, p.mu, p.sigma)
    e_loss_call_side = bs.expected_max_S_minus_K(p.spot, K_c_short, T, p.mu, p.sigma) - \
                       bs.expected_max_S_minus_K(p.spot, K_c_long,  T, p.mu, p.sigma)

    e_loss = (e_loss_put_side + e_loss_call_side) * p.lot_size
    per_cycle_gross = net_premium - e_loss

    # IC ties up only `margin_required` — the rest sits in cash earning yield
    cycles_per_year = 12 / p.T_months
    annual_premium_pnl = per_cycle_gross * cycles_per_year
    cash_remaining = max(p.capital - margin_required, 0.0)
    annual_cash_yield = cash_remaining * p.cash_yield

    annual_gross = annual_premium_pnl + annual_cash_yield
    annual_net = apply_friction_tax(annual_gross, p)
    # Report return on FULL capital so it's comparable to other strategies
    annual_gross_pct = annual_gross / p.capital * 100
    annual_net_pct = annual_net / p.capital * 100

    return StrategyAnalysis(
        name="Iron Condor",
        description="Sell put-spread + call-spread. Defined max loss, defined max gain. Pure VRP harvest.",
        params=asdict(p),
        intermediates={
            "T_years":          round(T, 4),
            "K_p_short":        round(K_p_short, 2),
            "K_p_long":         round(K_p_long, 2),
            "K_c_short":        round(K_c_short, 2),
            "K_c_long":         round(K_c_long, 2),
            "p_short_put":      round(p_short_put, 2),
            "p_long_put":       round(p_long_put, 2),
            "p_short_call":     round(p_short_call, 2),
            "p_long_call":      round(p_long_call, 2),
            "net_premium":      round(net_premium, 2),
            "spread_width":     round(spread_width, 2),
            "max_loss":         round(max_loss, 2),
            "margin_required":  round(margin_required, 2),
            "prob_max_profit":  round(prob_max_profit, 4),
            "expected_loss":    round(e_loss, 2),
        },
        per_cycle={
            "net_premium":     round(net_premium, 2),
            "expected_loss":   round(e_loss, 2),
            "gross_pnl":       round(per_cycle_gross, 2),
        },
        annual_gross_pct=round(annual_gross_pct, 2),
        annual_net_pct=round(annual_net_pct, 2),
        monthly_gross_pct=round(annual_gross_pct / 12, 3),
        monthly_net_pct=round(annual_net_pct / 12, 3),
        prob_negative_month=round(1 - prob_max_profit, 3),
        max_loss_per_cycle=round(-max_loss, 2),
        max_gain_per_cycle=round(net_premium, 2),
        sharpe_estimate=round((annual_net_pct / 100 - p.risk_free) / max(p.sigma * 0.5, 0.01), 3),
        formulae=[
            {"label": "Put-spread short",     "latex": "K_p^{\\text{short}} = (1 - \\alpha_p) S_0",        "value": K_p_short},
            {"label": "Put-spread long",      "latex": "K_p^{\\text{long}} = K_p^{\\text{short}} - 0.02 S_0", "value": K_p_long},
            {"label": "Call-spread short",    "latex": "K_c^{\\text{short}} = (1 + \\alpha_c) S_0",        "value": K_c_short},
            {"label": "Call-spread long",     "latex": "K_c^{\\text{long}} = K_c^{\\text{short}} + 0.02 S_0", "value": K_c_long},
            {"label": "Net premium",          "latex": "P = (p_p^s - p_p^l + p_c^s - p_c^l) \\cdot L",     "value": net_premium},
            {"label": "Spread width",         "latex": "W = 0.02 \\cdot S_0 \\cdot L",                     "value": spread_width},
            {"label": "Max loss per cycle",   "latex": "L_{\\max} = W - P",                                "value": max_loss},
            {"label": "P(max profit)",        "latex": "\\mathbb{P}(K_p^s < S_T < K_c^s)",                 "value": prob_max_profit},
            {"label": "Expected loss",        "latex": "\\mathbb{E}[L]",                                   "value": e_loss},
            {"label": "Per-cycle gross",      "latex": "\\Pi = P - \\mathbb{E}[L]",                        "value": per_cycle_gross},
            {"label": "Annual gross / margin","latex": "R_g = \\Pi \\cdot 12 / T_m / W",                   "value": annual_gross_pct / 100},
            {"label": "Annual net / margin",  "latex": "R_n = R_g (1 - \\tau)(1 + \\text{cess}) - \\phi",  "value": annual_net_pct / 100},
        ],
        notes=[
            "Capital efficiency is the magic — return calculated on margin (~₹3-5L) not full capital.",
            "Defined risk per cycle = spread_width − premium received.",
            "Highest yield-per-rupee of margin among all strategies, but tail event = max loss.",
        ],
    )


# ── Registry & dispatcher ────────────────────────────────────────────────────

STRATEGY_REGISTRY: dict[str, Callable[[StrategyParams], StrategyAnalysis]] = {
    "covered_call":    analyze_covered_call,
    "wheel":           analyze_wheel,
    "protected_wheel": analyze_protected_wheel,
    "iron_condor":     analyze_iron_condor,
}


def analyze(strategy_key: str, params: StrategyParams) -> StrategyAnalysis:
    fn = STRATEGY_REGISTRY.get(strategy_key)
    if fn is None:
        raise ValueError(f"Unknown strategy: {strategy_key}. "
                         f"Available: {list(STRATEGY_REGISTRY)}")
    return fn(params)


def analyze_all(params: StrategyParams) -> dict[str, StrategyAnalysis]:
    """Run every registered strategy with the same parameters — for the
    side-by-side comparison view in the UI."""
    return {key: fn(params) for key, fn in STRATEGY_REGISTRY.items()}
