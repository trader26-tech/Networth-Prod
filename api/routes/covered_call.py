"""
Covered Call analyzer — NiftyBees (BSE ETF) + Short Nifty CE.

Strategy:
  Buy  NiftyBees shares  → long Nifty exposure via ETF
  Sell Nifty CE option   → collect premium, cap upside at strike

Full-coverage ratio (matches 1 lot exposure exactly):
  shares = lots × lot_size × (nifty_spot / niftybees_price)
  ≈ lots × lot_size × 100  (since NiftyBees ≈ Nifty/100)

P&L at expiry (Nifty = ST):
  NiftyBees P&L = shares × (ST − S0) / 100
  Short call P&L = lots × lot_size × [premium − max(ST − K, 0)]
  Total P&L      = lots × lot_size × [(ST − S0) + premium − max(ST − K, 0)]

Key levels:
  Breakeven   = S0 − premium            (below this → loss)
  Max profit  = lots×lot_size×(K−S0+P)  (flat above K)
  Loss zone   = ST < S0 − premium
"""
import math
from fastapi import APIRouter, HTTPException
from api.core.chain import spot_for, build_real_chain, _get_nfo_instruments
from api import options_engine as opt_eng, state

router = APIRouter(prefix="/api/covered-call", tags=["covered_call"])

NIFTYBEES_RATIO = 100.0   # 1 share ≈ Nifty / 100


def _niftybees_price(nifty_spot: float) -> float | None:
    """Live NiftyBees LTP from Kite — no caching, no derived fallback when connected.

    Returns the real LTP when Kite is connected, None if the API call fails
    (so callers can show 'unavailable' rather than a wrong derived value), and
    Nifty/100 approximation only in pure offline mode (no Kite configured at all).
    """
    kite = state.get_kite()
    if kite:
        try:
            data = kite.ltp(["NSE:NIFTYBEES"])
            p = data.get("NSE:NIFTYBEES", {}).get("last_price")
            if p and float(p) > 0:
                return float(p)
        except Exception:
            pass
        return None   # Kite connected but price unavailable — caller shows error
    return round(nifty_spot / NIFTYBEES_RATIO, 2)


def _get_chain(underlying, expiry, spot):
    if state.get_kite():
        return build_real_chain(underlying, expiry, spot)
    return opt_eng.build_option_chain(underlying, expiry, spot, None)


def _pnl(ST, S0, K, P, lots, lot_size, shares, niftybees_entry):
    """Total covered-call P&L at expiry spot ST."""
    nb_pnl   = shares * (ST / NIFTYBEES_RATIO - niftybees_entry)
    call_pnl = lots * lot_size * (P - max(ST - K, 0))
    return nb_pnl + call_pnl


@router.get("/setup")
def setup(underlying: str = "NIFTY", expiry: str = ""):
    """
    Return spot, expiries, lot size, NiftyBees price, and the full OTM call chain
    so the frontend can render the strike picker immediately.
    """
    info = opt_eng.UNDERLYINGS.get(underlying)
    if not info:
        raise HTTPException(400, f"Unknown underlying: {underlying}")

    spot = spot_for(underlying)

    # Expiries
    from api.routes.options import _get_expiries
    expiries = _get_expiries(underlying)
    if not expiry:
        expiry = expiries[0] if expiries else ""

    chain_data = _get_chain(underlying, expiry, spot)
    chain      = chain_data["chain"]
    lot_size   = chain_data["lot_size"]
    dte        = chain_data["dte"]
    atm        = chain_data["atm_strike"]

    nb_price = _niftybees_price(spot)

    # Detect step size once
    all_strikes = sorted({float(r["strike"]) for r in chain})
    step = 50.0
    if len(all_strikes) >= 2:
        diffs = [all_strikes[i+1] - all_strikes[i] for i in range(len(all_strikes)-1)]
        step  = min(diffs)

    # Extract OTM + ATM calls, sorted by strike.
    # Allow strikes with very small but non-zero premium — far-OTM (+5-10%)
    # calls still matter for the user's scan even when premium ~₹0.50.
    otm_calls = []
    for row in chain:
        strike = float(row["strike"])
        ce     = row["ce"]
        if ce["price"] <= 0:
            continue

        if strike < spot - step:
            continue   # skip deep ITM calls

        bid   = float(ce.get("bid", 0) or 0)
        ask   = float(ce.get("ask", 0) or 0)
        ltp   = float(ce["price"])
        delta = ce.get("delta")
        iv    = ce.get("iv")

        otm_calls.append({
            "strike":       strike,
            "premium":      ltp,
            "bid":          bid,
            "ask":          ask,
            "delta":        round(delta, 3) if delta is not None else None,
            "iv":           round(iv, 2)    if iv    is not None else None,
            "symbol":       ce.get("symbol", ""),
            "oi":           int(ce.get("oi", 0)),
            "distance":     round(strike - spot, 1),
            "distance_pct": round((strike - spot) / spot * 100, 2),
            "is_atm":       abs(strike - atm) < 1,
        })

    otm_calls.sort(key=lambda x: x["strike"])

    return {
        "underlying":      underlying,
        "spot":            spot,
        "expiry":          expiry,
        "expiries":        expiries,
        "lot_size":        lot_size,
        "dte":             dte,
        "niftybees_price": nb_price,
        "atm_strike":      atm,
        "otm_calls":       otm_calls,
    }


@router.get("/analyze")
def analyze(
    underlying:           str   = "NIFTY",
    expiry:               str   = "",
    strike:               float = 0,
    lots:                 int   = 1,
    niftybees_price:      float = 0,
    custom_shares:        int   = 0,
    entry_premium:        float = 0,   # actual premium collected at trade entry
    entry_niftybees_price: float = 0,  # actual NiftyBees price at entry
    entry_nifty:          float = 0,   # actual Nifty level at entry (preferred over nb_price × ratio)
):
    """
    Compute the full covered-call analysis for a given strike and lot count.
    Pass entry_premium + entry_niftybees_price to get monitoring-mode P&L
    (payoff based on what was actually collected, not the current market price).
    """
    try:
        return _analyze_impl(
            underlying, expiry, strike, lots, niftybees_price, custom_shares,
            entry_premium, entry_niftybees_price, entry_nifty,
        )
    except HTTPException:
        raise
    except Exception as e:
        # Convert any unhandled error (e.g. stale chain, Kite session expired,
        # missing expiry) into a clean 400 so the browser doesn't see a torn
        # connection ("0 Unknown Error") with no CORS headers.
        import traceback, sys
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(400, f"Analyze failed: {type(e).__name__}: {e}")


def _analyze_impl(
    underlying, expiry, strike, lots, niftybees_price, custom_shares,
    entry_premium, entry_niftybees_price, entry_nifty,
):
    info = opt_eng.UNDERLYINGS.get(underlying)
    if not info:
        raise HTTPException(400, f"Unknown underlying: {underlying}")

    spot = spot_for(underlying)

    from api.routes.options import _get_expiries
    if not expiry:
        exps   = _get_expiries(underlying)
        expiry = exps[0] if exps else ""

    chain_data = _get_chain(underlying, expiry, spot)
    chain      = chain_data["chain"]
    lot_size   = chain_data["lot_size"]
    dte        = chain_data["dte"]
    atm        = chain_data["atm_strike"]

    if niftybees_price <= 0:
        niftybees_price = _niftybees_price(spot) or round(spot / NIFTYBEES_RATIO, 2)

    if strike <= 0:
        strike = atm  # default ATM

    # Find the strike in the current chain.  Saved positions may have a strike
    # that is now deep ITM / far from ATM and outside the ±12-strike window.
    # In that case fall back to Black-Scholes so monitoring never breaks.
    call_row = next((r for r in chain if abs(float(r["strike"]) - strike) < 0.5), None)
    if call_row is not None:
        ce        = call_row["ce"]
        bid       = float(ce.get("bid", 0) or 0)
        ltp       = float(ce["price"])
        delta     = ce.get("delta")
        iv        = ce.get("iv")
        price_src = "live"
    else:
        # Strike outside chain — use Black-Scholes with ATM IV as reference
        T         = opt_eng.days_to_expiry(expiry)
        r_rate    = opt_eng.RISK_FREE_RATE
        atm_row   = next((r for r in chain if abs(float(r["strike"]) - atm) < 0.5), None)
        ref_iv    = ((atm_row["ce"].get("iv") or 16.0) / 100) if atm_row else 0.16
        bs        = opt_eng.black_scholes(spot, strike, T, r_rate, ref_iv, "CE")
        ltp       = bs["price"]
        bid       = 0.0
        delta     = bs["delta"]
        iv        = bs["iv"]
        price_src = "bs"

    current_call_price = bid if bid > 0 else ltp   # best estimate of current market price

    # Payoff curve uses entry_premium when monitoring an existing position;
    # otherwise uses the current market price (new position builder mode).
    premium = entry_premium if entry_premium > 0 else current_call_price

    # ── Coverage calculation ──────────────────────────────────────────────────
    # Full cover: enough NiftyBees so that every Nifty-point move is matched
    shares_full = int(lots * lot_size * round(spot / niftybees_price))
    shares = custom_shares if custom_shares > 0 else shares_full

    coverage_ratio = round(shares / shares_full * 100, 1) if shares_full > 0 else 100.0

    # Use actual entry prices when monitoring an existing position
    niftybees_entry = entry_niftybees_price if entry_niftybees_price > 0 else round(spot / NIFTYBEES_RATIO, 2)

    # entry_nifty: the exact Nifty level when the position was opened.
    # Priority: explicit entry_nifty param → derived from nb price → current spot.
    if entry_nifty <= 0:
        entry_nifty = round(niftybees_entry * NIFTYBEES_RATIO, 1) if niftybees_entry > 0 else spot
    entry_nifty = round(entry_nifty, 1)

    # ── Key financial levels (all anchored to entry_nifty, not current spot) ─
    breakeven       = round(entry_nifty - premium, 1)
    downside_pct    = round(premium / entry_nifty * 100, 2) if entry_nifty else 0
    upside_to_cap   = round(strike - entry_nifty, 1)
    upside_cap_pct  = round((strike - entry_nifty) / entry_nifty * 100, 2) if entry_nifty else 0

    max_profit_pts  = (strike - entry_nifty + premium) * lots * lot_size
    max_profit      = round(max_profit_pts, 2)
    max_profit_pct  = round(max_profit / (shares * niftybees_entry) * 100, 2) if (shares * niftybees_entry) else 0

    niftybees_cost  = round(shares * (entry_niftybees_price if entry_niftybees_price > 0 else niftybees_price), 2)
    premium_total   = round(lots * lot_size * premium, 2)
    net_capital     = round(niftybees_cost - premium_total, 2)

    # ── Payoff curve ─────────────────────────────────────────────────────────
    # Range spans both entry_nifty and current spot so both are always visible.
    ref  = min(spot, entry_nifty)
    ceil = max(spot, entry_nifty)
    lo   = ref  * 0.65
    hi   = ceil * 1.35
    n    = 300
    step = (hi - lo) / n

    payoff_curve = []
    for i in range(n + 1):
        ST  = lo + i * step
        pnl = _pnl(ST, spot, strike, premium, lots, lot_size, shares, niftybees_entry)
        payoff_curve.append({"nifty": round(ST, 0), "pnl": round(pnl, 0)})

    # ── Scenario table ────────────────────────────────────────────────────────
    pct_moves = [-30, -20, -15, -10, -7, -5, -3, 0, 3, 5, 7, 10, 15, 20, 30]
    scenarios = []
    for pct in pct_moves:
        ST      = round(spot * (1 + pct / 100))
        pnl     = _pnl(ST, spot, strike, premium, lots, lot_size, shares, niftybees_entry)
        nb_pnl  = shares * (ST / NIFTYBEES_RATIO - niftybees_entry)
        c_pnl   = lots * lot_size * (premium - max(ST - strike, 0))
        roi     = round(pnl / net_capital * 100, 2) if net_capital > 0 else 0
        scenarios.append({
            "label":        f"{'+' if pct >= 0 else ''}{pct}%",
            "pct":          pct,
            "nifty":        ST,
            "niftybees_pnl": round(nb_pnl, 0),
            "call_pnl":     round(c_pnl, 0),
            "total_pnl":    round(pnl, 0),
            "roi":          roi,
            "is_loss":      pnl < 0,
            "is_capped":    ST >= strike,
            "is_at_entry":  pct == 0,
        })

    # ── Risk metrics ─────────────────────────────────────────────────────────
    # Max loss: Nifty goes to near zero (theoretical)
    pnl_at_70pct_drop = _pnl(spot * 0.70, spot, strike, premium, lots, lot_size, shares, niftybees_entry)
    pnl_at_50pct_drop = _pnl(spot * 0.50, spot, strike, premium, lots, lot_size, shares, niftybees_entry)

    # Opportunity cost: if spot rockets to 1.2×, how much extra we "gave up"
    pnl_with_call    = _pnl(spot * 1.20, spot, strike, premium, lots, lot_size, shares, niftybees_entry)
    pnl_without_call = lots * lot_size * (spot * 1.20 - spot)  # just holding NiftyBees
    opportunity_cost = round(pnl_without_call - pnl_with_call, 0)

    return {
        "underlying":       underlying,
        "spot":             spot,
        "expiry":           expiry,
        "dte":              dte,
        "strike":           strike,
        "lots":             lots,
        "lot_size":         lot_size,
        "premium":              round(premium, 2),          # entry premium (used for payoff curve)
        "current_call_price":   round(current_call_price, 2), # current market price of the call
        "call_price_source":    price_src,                    # "live" | "bs"
        "bid":                  round(bid, 2),
        "ltp":                  round(ltp, 2),
        "delta":                round(delta, 3) if delta else None,
        "iv":                   round(iv, 2)    if iv    else None,
        "symbol":               ce.get("symbol", "") if call_row else "",
        # Entry reference
        "entry_nifty":      round(entry_nifty, 1),
        "niftybees_entry":  round(niftybees_entry, 2),
        # NiftyBees
        "niftybees_price":  round(niftybees_price, 2),
        "shares":           shares,
        "shares_full":      shares_full,
        "coverage_ratio":   coverage_ratio,
        # Capital
        "niftybees_cost":   niftybees_cost,
        "premium_total":    premium_total,
        "net_capital":      net_capital,
        # Key levels
        "breakeven":        breakeven,
        "downside_pct":     downside_pct,
        "upside_to_cap":    upside_to_cap,
        "upside_cap_pct":   upside_cap_pct,
        "max_profit":       max_profit,
        "max_profit_pct":   max_profit_pct,
        "atm_strike":       atm,
        # Analysis
        "payoff_curve":     payoff_curve,
        "scenarios":        scenarios,
        "pnl_at_70pct_spot": round(pnl_at_70pct_drop, 0),
        "pnl_at_50pct_spot": round(pnl_at_50pct_drop, 0),
        "opportunity_cost_at_20pct_up": opportunity_cost,
    }


# ── Pre-market scan helpers ────────────────────────────────────────────────────
import datetime as _dt
import math as _math


def _bs_call(S, K, T, iv=0.16, r=0.065):
    """Black-Scholes price for a European call. iv is decimal (0.16 = 16%)."""
    if T <= 0 or iv <= 0:
        return max(S - K, 0)
    sqrtT = _math.sqrt(T)
    d1 = (_math.log(S / K) + (r + 0.5 * iv * iv) * T) / (iv * sqrtT)
    d2 = d1 - iv * sqrtT
    def N(x):
        a1, a2, a3, a4, a5, p = 0.254829592, -0.284496736, 1.421413741, -1.453152027, 1.061405429, 0.3275911
        sg = -1 if x < 0 else 1
        ax = abs(x) / _math.sqrt(2)
        t  = 1 / (1 + p * ax)
        y  = 1 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * _math.exp(-ax * ax)
        return 0.5 * (1 + sg * y)
    return S * N(d1) - K * _math.exp(-r * T) * N(d2)


def _days_to_50pct_capture(spot: float, strike: float, premium: float,
                            iv_decimal: float, dte: int) -> int:
    """Days for an OTM short call's value to decay to 50% (assuming spot
    unchanged and IV unchanged). Used to estimate how fast we'd hit our
    50%-of-max-profit early-exit target."""
    if premium <= 0 or dte <= 0:
        return 0
    target = premium * 0.5
    for d in range(1, dte + 1):
        days_left = max(dte - d, 0)
        val = _bs_call(spot, strike, days_left / 365.0, iv_decimal)
        if val <= target:
            return d
    return dte


def _fetch_vix() -> float:
    kite = state.get_kite()
    if kite:
        try:
            r = kite.ltp(["NSE:INDIA VIX"])
            v = r.get("NSE:INDIA VIX", {}).get("last_price")
            if v and v > 0:
                return float(v)
        except Exception:
            pass
    try:
        import requests as _req
        headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json",
                   "Referer": "https://www.nseindia.com"}
        r = _req.get("https://www.nseindia.com/api/allIndices", headers=headers, timeout=4)
        for idx in r.json().get("data", []):
            if "INDIA VIX" in idx.get("index", ""):
                return float(idx["last"])
    except Exception:
        pass
    return 14.0


def _cycle_quality_factor(dte: int, cadence: str = "monthly") -> float:
    """DTE-based bell curve — penalises out-of-band expiries for the chosen cadence.

    Two curves:
    • monthly (default)  — peaks at 22-35 DTE (the monthly sweet spot)
    • quarterly          — peaks at 60-110 DTE (lazy-CC sweet spot, fits the
      75-100 DTE hedge cycle, lower vega vs ½-yearlies)
    """
    if cadence == "quarterly":
        if dte <  20:  return 0.20    # too short for quarterly cycling
        if dte <  40:  return 0.50
        if dte <  60:  return 0.80
        if dte <= 110: return 1.00    # ★ QUARTERLY SWEET SPOT (matches 75-100d hedge)
        if dte <= 140: return 0.85
        return 0.55                   # 6+ months — vega exposure too high

    # monthly (default)
    if dte < 5:    return 0.40    # weekly — heavy penalty
    if dte < 14:   return 0.70    # biweekly — moderate penalty
    if dte <= 21:  return 0.95    # short monthly — slight penalty
    if dte <= 35:  return 1.00    # MONTHLY SWEET SPOT
    if dte <= 50:  return 0.85    # bimonthly — slow theta begins
    if dte <= 90:  return 0.65    # quarterly — vega risk grows
    return 0.40                   # >90d — vega bomb territory


def _iv_quality_factor(atm_iv_pct: float, vix: float) -> float:
    """Reward selling vol when chain is rich vs VIX, penalise when cheap.
    Clipped to [0.8, 1.3] so it nudges but doesn't dominate the score."""
    if vix <= 0 or atm_iv_pct <= 0:
        return 1.0
    ratio = atm_iv_pct / vix
    return max(0.8, min(1.3, ratio))


def _delta_quality_factor(delta: float) -> float:
    """Reward safer (lower-Δ / further-OTM) strikes; penalise the high-Δ ones
    that would whip-fire the Δ 0.40 roll-up trigger on any minor rally.

    Curve:
      Δ ≤ 0.18         → 1.00  ★ safe — far-OTM is the goal for a defensive CC
      0.18 < Δ ≤ 0.22  → 1.00  ★ still well within the safe zone
      0.22 < Δ ≤ 0.30  → 0.95  (acceptable — borderline assignment risk)
      0.30 < Δ ≤ 0.35  → 0.70  (modest penalty — getting close to roll trigger)
      0.35 < Δ < 0.40  → 0.45  (close to roll trigger — risky)
      Δ ≥ 0.40         → 0.25  (already in roll territory — penalise hard)
    """
    d = abs(delta)
    if d >= 0.40:  return 0.25
    if d >= 0.35:  return 0.45
    if d >  0.30:  return 0.70
    if d >  0.22:  return 0.95
    return 1.00


def _atm_iv_from_chain(chain: list, spot: float) -> float:
    """Extract ATM IV from chain (use whichever leg has it)."""
    if not chain:
        return 0.0
    atm_row = min(chain, key=lambda r: abs(float(r["strike"]) - spot))
    ce_iv = float((atm_row.get("ce") or {}).get("iv") or 0)
    pe_iv = float((atm_row.get("pe") or {}).get("iv") or 0)
    return max(ce_iv, pe_iv)


def _rank_strikes(spot: float, vix: float, chain: list, dte: int, cadence: str = "monthly") -> list:
    """Score and rank OTM call strikes using the Best-Trade Score (BTS).

    Designed for a CC strategy with 60% TP + DTE≤14 force-exit. Replaces the
    old annualised-yield score that biased toward weeklies.

    Formula:
      BTS = NetYieldPerCycle × Safety × Liquidity × IVQuality × CycleQuality × DeltaQuality

    Where:
      NetYieldPerCycle = (TP_target × premium − friction) / spot   (per-cycle %)
      Safety           = 1 − 2·|Δ|·√(t_hold / T)                  (P stays OTM)
      Liquidity        = 0.5 + 0.5·min(OI/10000, 1)               (OI factor)
      IVQuality        = clip(ATM_IV / VIX, 0.8, 1.3)             (sell rich vol)
      CycleQuality     = DTE bell curve (monthly = 1.0, weekly = 0.4)
      DeltaQuality     = entry-band penalty (0.22-0.30 = 1.0; ≥ 0.35 ≤ 0.55)

    The legacy `score` field is kept for backwards compatibility; ranking
    sorts by BTS now (which favours monthlies in 95% of scenarios).
    """
    candidates = []
    safe_dte = max(dte, 1)
    atm_iv_pct = _atm_iv_from_chain(chain, spot)
    cycle_quality = _cycle_quality_factor(dte, cadence)
    iv_quality    = _iv_quality_factor(atm_iv_pct, vix)

    # Cadence-aware capture target: monthlies aim for 60% (steep late-cycle
    # theta), quarterlies exit earlier at 30% (slow theta tail isn't worth
    # the vega/lockup — better to cycle faster).
    tp_target = 0.30 if cadence == "quarterly" else 0.60

    # Friction estimate per cycle: ~0.05% of spot capital
    # (covers brokerage + STT + slippage on entry+exit, typical for 1 lot Nifty)
    FRICTION_PCT_PER_CYCLE = 0.05

    for row in chain:
        strike = float(row["strike"])
        ce = row.get("ce", {})
        price = float(ce.get("price", 0) or 0)
        # Allow far-OTM strikes with low premium into the ranker. Tradeable
        # min tick on NSE options is ₹0.05; anything above that should be
        # eligible for the scan. The downstream BTS factors (Liquidity,
        # NetYield) will rank truly worthless strikes near the bottom.
        if strike < spot or price < 0.05:
            continue
        otm_pct = (strike - spot) / spot * 100

        delta = abs(float(ce.get("delta") or 0.30))
        iv_pct = float(ce.get("iv") or vix)
        iv_dec = max(iv_pct / 100.0, 0.05)
        oi    = int(ce.get("oi", 0) or 0)

        # ── Theta-based early-exit metrics ─────────────────────────────────
        # Days until call value decays to 60% of current (60% TP rule).
        # We still expose `days_to_50pct` for backwards compat in the UI.
        days_to_50 = _days_to_50pct_capture(spot, strike, price, iv_dec, safe_dte)
        days_to_50 = max(days_to_50, 1)

        captured        = price * tp_target                       # cadence-aware TP capture
        prem_per_day    = price / max(days_to_50, 1)
        capital_proxy   = spot                                     # per Nifty unit (≈ NiftyBees cost)
        early_yield_pct = (captured / capital_proxy) * (365.0 / days_to_50) * 100.0
        hold_yield_pct  = (price / capital_proxy) * (365.0 / safe_dte) * 100.0

        # ── Per-cycle net yield (for BTS — NOT annualised) ────────────────
        # This is the actual % of capital captured in ONE cycle, after friction.
        gross_per_cycle_pct = (captured / capital_proxy) * 100.0
        net_yield_per_cycle_pct = max(gross_per_cycle_pct - FRICTION_PCT_PER_CYCLE, 0.001)

        # ── Probability call stays OTM during the holding window ──────────
        # Crude reflection-principle proxy: P(touch K within τ) ≈ 2·Δ·√(τ/T).
        hold_frac  = days_to_50 / safe_dte
        prob_touch = min(1.0, 2.0 * delta * (hold_frac ** 0.5))
        prob_safe  = max(0.05, 1.0 - prob_touch)
        prob_keep_pct = max(0.0, min(100.0, (1 - delta) * 100))    # OTM-at-expiry %

        # ── Liquidity (BTS uses 10k-OI normalisation, slightly stricter) ──
        liq_factor       = 0.6 + 0.4 * min(oi / 20000.0, 1.0)        # legacy score
        bts_liq_factor   = 0.5 + 0.5 * min(oi / 10000.0, 1.0)         # BTS

        # ── Legacy score (kept for backwards compat in API responses) ────
        score = early_yield_pct * prob_safe * liq_factor

        # ── Best-Trade Score (the actual ranking key) ────────────────────
        # Safety is CUBED so further-OTM strikes (where P(stays OTM) is
        # close to 1) dominate the ranking. A near-ATM strike at ~60%
        # safe gets only 0.22 from safety, while a +5% OTM at ~82% safe
        # gets 0.55 — a 2.5× advantage that beats the ~2× premium
        # advantage near-ATM has. Combined with the flatter delta_quality
        # curve, the ranker actively prefers the safer / further-OTM trade.
        delta_quality = _delta_quality_factor(delta)
        bts = (net_yield_per_cycle_pct
               * (prob_safe ** 3)
               * bts_liq_factor
               * iv_quality
               * cycle_quality
               * delta_quality)

        if   delta < 0.20: assignment_risk = "low"
        elif delta < 0.35: assignment_risk = "medium"
        else:              assignment_risk = "high"

        if   oi >= 10000: liquidity = "high"
        elif oi >= 1000:  liquidity = "medium"
        else:             liquidity = "low"

        # ── Beginner-friendly explanations (60% TP framed) ─────────────────
        why_good, why_caution = [], []

        if days_to_50 <= max(safe_dte // 3, 1):
            why_good.append(
                f"Fast theta — premium expected to decay halfway in just "
                f"~{days_to_50}d. Should hit 60% TP target shortly after."
            )
        elif days_to_50 <= safe_dte // 2:
            why_good.append(
                f"Reasonable theta — ~{days_to_50}d to mid-decay, on track to "
                f"hit 60% TP before DTE-14 force-exit."
            )
        else:
            why_caution.append(
                f"Slow decay — needs ~{days_to_50}d to mid-decay; may not hit "
                f"60% TP before the DTE-14 cutoff."
            )

        # Cycle quality + IV quality narrative
        if cycle_quality >= 0.95:
            why_good.append(
                f"Optimal cycle length — {dte}d expiry hits the theta sweet spot."
            )
        elif cycle_quality < 0.7:
            why_caution.append(
                f"Suboptimal cycle ({dte}d) — penalised in scoring (cycle_quality {cycle_quality:.2f})."
            )

        if iv_quality >= 1.1:
            why_good.append(
                f"Chain pricing rich vs market — ATM IV {atm_iv_pct:.1f}% vs VIX {vix:.1f} "
                f"(ratio {iv_quality:.2f}× — favourable to sell)."
            )
        elif iv_quality < 0.95:
            why_caution.append(
                f"Chain pricing thin — ATM IV {atm_iv_pct:.1f}% vs VIX {vix:.1f} "
                f"(ratio {iv_quality:.2f}× — premium below market regime)."
            )

        if early_yield_pct >= 25:
            why_good.append(
                f"Excellent income velocity — ≈{early_yield_pct:.1f}% annualised "
                f"on the early exit (vs {hold_yield_pct:.1f}% if held to expiry)."
            )
        elif early_yield_pct >= 12:
            why_good.append(
                f"Solid early-exit yield ≈ {early_yield_pct:.1f}% annualised."
            )
        else:
            why_caution.append(
                f"Modest early-exit yield ≈ {early_yield_pct:.1f}% annualised."
            )

        if prob_safe >= 0.80:
            why_good.append(
                f"~{int(prob_safe * 100)}% chance the call stays OTM through "
                f"your {days_to_50}d hold — clean exit likely."
            )
        elif prob_safe >= 0.60:
            why_caution.append(
                f"~{int((1 - prob_safe) * 100)}% chance Nifty touches {strike:.0f} "
                f"before you hit 50%; if it does, the exit gets harder (option "
                f"price rises faster than theta can decay it)."
            )
        else:
            why_caution.append(
                f"High probability (~{int((1 - prob_safe) * 100)}%) Nifty "
                f"crosses {strike:.0f} during the holding window — call may be "
                f"worth more than entry before theta does its job."
            )

        if oi >= 10000:
            why_good.append(
                f"Excellent liquidity (OI {oi:,}) — tight spreads when you exit."
            )
        elif oi >= 1000:
            why_good.append(f"Reasonable liquidity (OI {oi:,}).")
        else:
            why_caution.append(
                f"Thin liquidity (OI {oi:,}) — wider bid-ask, slippage on exit."
            )

        if otm_pct < 0.8:
            why_caution.append(
                f"Only {otm_pct:.1f}% OTM — any Nifty uptick pushes it ITM and "
                f"theta can't keep up."
            )

        # ── Entry-band classification (Δ 0.25-0.30 is the recommended band) ───
        in_entry_band = 0.25 <= delta <= 0.30
        entry_band = "in_band" if in_entry_band else (
            "below_band" if delta < 0.25 else "above_band"
        )
        if in_entry_band:
            why_good.append(
                f"Δ {delta:.2f} sits inside the recommended 0.25-0.30 entry band — "
                f"sweet spot for premium yield × safety."
            )
        elif delta < 0.25:
            why_caution.append(
                f"Δ {delta:.2f} is below the 0.25 floor — premium too thin to justify "
                f"the position; consider closer-to-ATM strike."
            )
        elif delta > 0.30:
            why_caution.append(
                f"Δ {delta:.2f} above the 0.30 ceiling — premium higher but assignment "
                f"risk grows; will whip-fire the Δ 0.40 roll-up faster."
            )

        candidates.append({
            "strike":          strike,
            "premium":         round(price, 2),
            "otm_pct":         round(otm_pct, 2),
            "delta":           round(delta, 3),
            "iv":              round(iv_pct, 1),
            "oi":              oi,
            "days_to_50pct":   days_to_50,
            "premium_per_day": round(prem_per_day, 2),
            "early_yield_pct": round(early_yield_pct, 1),
            "hold_yield_pct":  round(hold_yield_pct, 1),
            "prob_safe_pct":   round(prob_safe * 100, 0),
            "prob_keep_pct":   round(prob_keep_pct, 0),
            "score":           round(score, 2),         # legacy, kept for compat
            "bts":             round(bts, 4),           # NEW — primary ranking key
            "net_yield_per_cycle_pct": round(net_yield_per_cycle_pct, 3),
            "cycle_quality":   round(cycle_quality, 2),
            "iv_quality":      round(iv_quality, 2),
            "delta_quality":   round(delta_quality, 2),  # NEW — high-delta penalty
            "in_entry_band":   in_entry_band,           # NEW — Δ 0.25-0.30 flag
            "entry_band":      entry_band,              # in_band / below_band / above_band
            "assignment_risk": assignment_risk,
            "liquidity":       liquidity,
            "max_gain_pct":    round((strike - spot + price) / spot * 100, 2),
            "why_good":        why_good,
            "why_caution":     why_caution,
            "summary": (
                f"{otm_pct:.1f}% OTM · Δ {delta:.2f} · "
                f"60% capture in ~{days_to_50}d · "
                f"≈{early_yield_pct:.1f}% p.a. · "
                f"{int(prob_safe * 100)}% odds of clean exit"
            ),
        })

    # Sort by BTS (the new ranking key — embeds friction, IV richness, cycle quality).
    candidates.sort(key=lambda c: c["bts"], reverse=True)
    for i, c in enumerate(candidates):
        c["rank"] = i + 1
    return candidates


@router.get("/premarket")
def premarket_scan(underlying: str = "NIFTY", expiry: str = "", cadence: str = "monthly"):
    spot = spot_for(underlying)
    from api.routes.options import _get_expiries
    expiries   = _get_expiries(underlying)
    if not expiry:
        # Cadence-aware default expiry: monthly picks the nearest, quarterly
        # picks the first expiry with DTE >= 60 (the lazy-CC sweet spot).
        if cadence == "quarterly":
            for e in expiries:
                if opt_eng.days_to_expiry(e) * 365 >= 60:
                    expiry = e
                    break
        if not expiry:
            expiry = expiries[0] if expiries else ""
    chain_data = _get_chain(underlying, expiry, spot)
    chain      = chain_data["chain"]
    dte        = chain_data["dte"]

    total_ce = sum(int(r.get("ce", {}).get("oi", 0) or 0) for r in chain)
    total_pe = sum(int(r.get("pe", {}).get("oi", 0) or 0) for r in chain)
    pcr      = round(total_pe / total_ce, 2) if total_ce else 1.0

    atm     = chain_data["atm_strike"]
    atm_row = next((r for r in chain if abs(float(r["strike"]) - atm) < 1), None)
    atm_iv  = float(atm_row["ce"].get("iv") or 0) if atm_row else 0.0

    vix    = _fetch_vix()
    ranked = _rank_strikes(spot, vix, chain, dte, cadence)

    # ── Per-expiry IV richness vs market VIX ──────────────────────────────
    # VIX is a single market-wide 30d-vol number, but the chain's ATM IV
    # changes per expiry. Compare the two: ATM IV ≥ VIX ⇒ options are
    # priced rich relative to the broader market (good for selling).
    iv_richness_pct = ((atm_iv - vix) / vix * 100) if vix > 0 and atm_iv > 0 else 0.0
    if   iv_richness_pct >= 5:  iv_label = f"Rich (+{iv_richness_pct:.0f}% vs VIX)"
    elif iv_richness_pct >= -5: iv_label = "Fair (≈ VIX)"
    else:                       iv_label = f"Cheap ({iv_richness_pct:.0f}% vs VIX)"
    iv_ok = atm_iv >= vix * 0.95

    vix_ok = vix >= 11
    pcr_ok = pcr >= 0.7
    vix_skip_active = vix > 22   # NEW — skip entries when VIX > 22

    checks = sum([vix_ok, iv_ok, pcr_ok])
    if   vix_skip_active:  signal = "skip"   # VIX > 22 forces skip regardless
    elif checks <= 1:      signal = "skip"
    elif checks == 2:      signal = "caution"
    else:                  signal = "go"

    pcr_label = ("Bullish (put-heavy)" if pcr > 1.2
                 else "Neutral" if pcr > 0.9
                 else "Bearish (call-heavy)")

    # ── Strikes inside the recommended Δ 0.25-0.30 entry band ─────────────
    in_band_strikes = [r for r in ranked if r.get("in_entry_band")]
    recommended_strike = in_band_strikes[0] if in_band_strikes else (ranked[0] if ranked else None)

    return {
        "timestamp":       _dt.datetime.now().isoformat(),
        "spot":            spot,
        "expiry":          expiry,
        "expiries":        expiries,
        "dte":             chain_data["dte"],
        "vix":             round(vix, 1),
        "vix_ok":          vix_ok,
        "vix_label":       "Good" if vix >= 13 else ("Acceptable" if vix >= 11 else "Too low"),
        "vix_skip_active": vix_skip_active,
        "vix_skip_message": (
            f"VIX {vix:.1f} > 22 — skip new entries this cycle. Premium looks rich "
            f"but tail risk is elevated. Resume when VIX < 18 or 14 days pass."
            if vix_skip_active else None
        ),
        "atm_iv":          round(atm_iv, 1) if atm_iv else None,
        "iv_richness_pct": round(iv_richness_pct, 1),
        "iv_label":        iv_label,
        "iv_ok":           iv_ok,
        "pcr":             pcr,
        "pcr_label":       pcr_label,
        "pcr_ok":          pcr_ok,
        "checks_passed":   checks,
        "checks_total":    3,
        "overall_signal":  signal,
        "ranked_strikes":  ranked,
        # NEW — entry rules
        "delta_band":      {"min": 0.25, "max": 0.30, "label": "Recommended entry: Δ 0.25-0.30"},
        "in_band_strikes": in_band_strikes,
        "recommended_strike": recommended_strike,
    }


@router.get("/best-trade")
def best_trade(
    underlying:    str = "NIFTY",
    top_n:         int = 4,
    scan_expiries: int = 4,
    cadence:       str = "monthly",
):
    """Cross-expiry best-strike scan with one-per-expiry diversity.

    Cadence selects which DTE band the scan covers:
      monthly   — first 4 expiries (~1-30 DTE), targets 60% TP
      quarterly — only expiries with DTE 50-140 (the lazy-CC sweet spot),
                  targets 30% TP, boosts cycle_quality for 60-110 DTE
    """
    from api.routes.options import _get_expiries

    spot = spot_for(underlying)
    expiries = _get_expiries(underlying)
    vix = _fetch_vix()

    # Cadence-aware expiry filter. For monthly we keep the existing "first N"
    # behaviour; for quarterly we walk the entire expiry list and keep ones
    # inside the 50-140 DTE band (or the closest match if none qualify).
    if cadence == "quarterly":
        in_band = [e for e in expiries if 50 <= opt_eng.days_to_expiry(e) * 365 <= 140]
        if not in_band and expiries:
            # Fallback: closest single expiry to 75d so the scan never empties.
            in_band = [min(expiries, key=lambda e: abs(opt_eng.days_to_expiry(e) * 365 - 75))]
        target_expiries = in_band[:max(1, scan_expiries)]
    else:
        target_expiries = expiries[:max(1, scan_expiries)]

    import time as _t
    by_expiry: dict[str, list] = {}
    scanned: list = []
    for idx, expiry in enumerate(target_expiries):
        # Tiny inter-expiry delay so we don't burst the kite.quote() endpoint —
        # cheaper than catching rate-limit exceptions and retrying.
        if idx > 0:
            _t.sleep(0.25)
        try:
            chain_data = _get_chain(underlying, expiry, spot)
        except Exception as e:
            print(f"best-trade: skip {expiry} ({e})")
            continue
        chain = chain_data["chain"]
        dte   = chain_data["dte"]
        if dte <= 0:
            continue
        ranked = _rank_strikes(spot, vix, chain, dte, cadence)
        if not ranked:
            continue
        for c in ranked:
            c["expiry"]      = expiry
            c["dte"]         = dte
            c["expiry_rank"] = c.get("rank")
        by_expiry[expiry] = ranked
        scanned.append({"expiry": expiry, "dte": dte, "candidates": len(ranked)})

    # One per expiry — the best-scoring strike inside that expiry.
    # Ranks NOW use BTS (Best-Trade Score), which embeds friction, IV richness,
    # and a cycle-quality penalty for weeklies. Monthlies will dominate in
    # 95%+ of normal scans (weeklies only win when premium is exceptionally rich).
    diverse_top: list = []
    for expiry, ranked in by_expiry.items():
        diverse_top.append(ranked[0])
    diverse_top.sort(key=lambda c: c.get("bts", 0), reverse=True)
    for i, c in enumerate(diverse_top):
        c["global_rank"] = i + 1

    top = diverse_top[:max(1, top_n)]

    # Same-expiry runners-up for the global #1 (so user can compare strikes
    # within the winning expiry without re-running a per-expiry scan)
    same_expiry_alts: list = []
    if top:
        winner_expiry = top[0]["expiry"]
        peers = by_expiry.get(winner_expiry, [])
        same_expiry_alts = peers[1:4]  # ranks 2-4 within the winning expiry

    return {
        "underlying":     underlying,
        "spot":           round(spot, 2),
        "vix":            round(vix, 1),
        "cadence":        cadence,
        "tp_target_pct":  30 if cadence == "quarterly" else 60,
        "expiries_scanned": scanned,
        "total_scanned":  sum(len(v) for v in by_expiry.values()),
        "top_n":          len(top),
        "candidates":     top,
        "same_expiry_alternatives": same_expiry_alts,
        "scoring": {
            "formula": "BTS = NetYieldPerCycle × Safety × Liquidity × IVQuality × CycleQuality × DeltaQuality",
            "ranking_key": "bts",   # UI primary number
            "components": {
                "net_yield_per_cycle_pct": "(0.6 × premium − friction_per_cycle) / spot × 100. % of capital captured in ONE cycle at 60% TP, after ~₹3K friction. NOT annualised, so weeklies don't get a free 'high yield'.",
                "prob_safe (Safety)": "max(0.05, 1 − min(1, 2·|delta|·√(days_to_50 / dte))) — reflection-principle proxy for P(call stays OTM). CUBED in the BTS formula so further-OTM strikes (P_safe ~ 0.85+) win decisively over near-ATM (P_safe ~ 0.60).",
                "liquidity": "0.5 + 0.5 × min(OI / 10000, 1) — penalises thin contracts where wide spreads erode the edge.",
                "iv_quality": "clip(ATM_IV / VIX, 0.8, 1.3) — rewards selling vol when chain is rich vs market regime.",
                "cycle_quality": "DTE bell curve. Weekly (<5d)=0.40 · Biweekly=0.70 · MONTHLY (22-35d)=1.00 sweet spot · Bimonthly=0.85 · Quarterly=0.65 · >90d=0.40.",
                "delta_quality": "Δ ≤ 0.22 = 1.00 ★ safe far-OTM is preferred · 0.22-0.30=0.95 · 0.30-0.35=0.70 · 0.35-0.40=0.45 · ≥0.40=0.25. Heavily penalises high-Δ strikes that whip-fire the Δ 0.40 roll-up trigger; lets safer low-Δ strikes through unscathed.",
            },
            "why_monthlies_now_win": "OLD score used annualised yield (365/days_to_50) which inflated weeklies. BTS uses per-cycle yield × cycle_quality, so weeklies are dampened by 0.40× while monthlies stay at 1.00×.",
            "why_high_delta_now_loses": "OLD score didn't penalise high delta. A Δ 0.45 strike has rich premium, but with the new Δ 0.40 roll-up trigger it would fire almost immediately on any rally — paying back the premium in roll friction. DeltaQuality 0.30 dampens these aggressively so the algorithm doesn't recommend them.",
            "tie_breaker": "When BTS scores are close, prefer the strike inside Δ 0.25-0.30 (in_entry_band=true) over higher delta — more breathing room before the Δ 0.40 roll-up fires.",
        },
        "legacy_score_note": "The `score` field on each candidate is the OLD annualised-yield formula, kept for backwards compatibility. The actual ranking now uses `bts`. If you see weeklies still ranking #1, the backend hasn't been restarted — kill uvicorn and start it fresh.",
    }


# ── Position CRUD ──────────────────────────────────────────────────────────────

from api import covered_call_store as _store
from pydantic import BaseModel as _BM


class _CallIn(_BM):
    strike: float; expiry: str; lots: int; lot_size: int
    premium_received: float; premium_total: float
    cadence: str = "monthly"          # "monthly" or "quarterly" — drives TP/force-exit thresholds
    entry_iv:  float | None = None    # ATM IV at entry (for vega-blowout exit alert)


class _PositionIn(_BM):
    name: str = ""
    underlying: str = "NIFTY"
    shares: int; niftybees_entry_price: float; niftybees_cost: float
    entry_nifty: float = 0   # Nifty index level at position open (stored directly)
    lots: int; lot_size: int
    active_call: _CallIn | None = None
    notes: str = ""


class _CloseIn(_BM):
    exit_price:    float
    close_kind:    str   | None = None  # explicit override; None → auto-derive
    nb_action:     str          = "held_all"
    nb_shares_sold: int         = 0
    nb_sell_price:  float       = 0.0
    notes:          str         = ""


# Per-request chain cache so multiple positions sharing the same expiry only
# fetch the option chain once. Reset on every list_positions() call.
_CHAIN_CACHE: dict = {}


def _direct_call_quote(strike: float, expiry: str) -> dict | None:
    """Direct kite.quote() for one specific CE strike. Used when the cached
    option chain doesn't cover this strike (the chain only spans ATM ± 12
    strikes — far-OTM short calls fall outside, which is exactly the case
    where a stale BS fallback diverges from Zerodha's live LTP).

    Returns {ltp, bid, ask, last_trade_time} or None if Kite/instrument
    isn't available."""
    kite = state.get_kite()
    if not kite:
        return None
    try:
        instruments = _get_nfo_instruments()
        match = next(
            (i for i in instruments
             if str(i.get("name", "")).upper() == "NIFTY"
             and i.get("instrument_type") == "CE"
             and abs(float(i.get("strike") or 0) - float(strike)) < 0.5
             and (i["expiry"].isoformat() if hasattr(i["expiry"], "isoformat")
                  else str(i["expiry"])[:10]) == expiry),
            None,
        )
        if not match:
            return None
        key = f"NFO:{match['tradingsymbol']}"
        q = kite.quote([key])
        v = q.get(key) or {}
        depth = v.get("depth") or {}
        buys  = depth.get("buy")  or []
        sells = depth.get("sell") or []
        ltp_t = v.get("last_trade_time")
        return {
            "ltp":   float(v.get("last_price") or 0),
            "bid":   float((buys[0].get("price")  if buys  else 0) or 0),
            "ask":   float((sells[0].get("price") if sells else 0) or 0),
            "last_trade_time": (ltp_t.isoformat() if hasattr(ltp_t, "isoformat") else (ltp_t or "")),
            "symbol": match["tradingsymbol"],
        }
    except Exception:
        return None


def _live_call_price(strike: float, expiry: str, spot: float) -> dict:
    """Return live price quote for a CE strike/expiry. Order of preference:
       1. Direct kite.quote() for the exact symbol (most accurate).
       2. Cached chain row (works for ATM-area strikes already pre-fetched).
       3. Black-Scholes estimate using the real DTE (last resort).

    Returns {ltp, bid, ask, mid, current, source, iv_decimal, delta, iv_pct}.
       current   = best estimate of buyback cost (mid if bid+ask both valid,
                   else LTP, else BS).
       delta     = signed call delta (0..1 for OTM/ATM CE)
       iv_pct    = IV in % (e.g. 16.5)
       source    = 'direct' | 'chain' | 'bs'.
    """
    iv_decimal = 0.16
    iv_pct: float | None = None
    delta: float | None = None
    T_years    = max(opt_eng.days_to_expiry(expiry), 0.0)

    def _fill_greeks_from_bs(price_for_iv: float) -> tuple[float | None, float | None]:
        """Compute IV (from market price) + delta (BS at that IV). Used when
        the chain row didn't supply them, e.g. far-OTM strikes outside the
        cached chain window."""
        if T_years <= 0 or price_for_iv <= 0:
            return None, None
        iv_solved = opt_eng.implied_volatility(spot, strike, T_years, opt_eng.RISK_FREE_RATE,
                                                price_for_iv, "CE")
        if not (1.0 <= iv_solved <= 200.0):
            # Fallback: use a sensible default IV for the delta calc
            sigma = 0.16
            iv_solved = 16.0
        else:
            sigma = iv_solved / 100.0
        bs = opt_eng.black_scholes(spot, strike, T_years, opt_eng.RISK_FREE_RATE, sigma, "CE")
        return iv_solved, float(bs["delta"])

    # 1) Direct symbol quote — the canonical path.
    direct = _direct_call_quote(strike, expiry)
    if direct is not None and (direct["ltp"] > 0 or direct["bid"] > 0 or direct["ask"] > 0):
        bid, ask, ltp = direct["bid"], direct["ask"], direct["ltp"]
        mid = (bid + ask) / 2.0 if (bid > 0 and ask > 0) else 0.0
        current = mid if mid > 0 else (ltp if ltp > 0 else (ask if ask > 0 else bid))
        # Direct quote doesn't carry IV/delta — solve them from the live price.
        iv_pct, delta = _fill_greeks_from_bs(current)
        if iv_pct is not None:
            iv_decimal = iv_pct / 100.0
        return {
            "ltp": ltp, "bid": bid, "ask": ask, "mid": mid,
            "current": current, "source": "direct",
            "last_trade_time": direct["last_trade_time"],
            "iv_decimal": iv_decimal,
            "iv_pct":     round(iv_pct, 2) if iv_pct is not None else None,
            "delta":      round(delta, 4) if delta is not None else None,
            "symbol": direct["symbol"],
        }

    # 2) Cached chain (only useful when strike is within ATM ± 12).
    cache_key = ("NIFTY", expiry)
    chain = _CHAIN_CACHE.get(cache_key)
    if chain is None:
        try:
            chain = _get_chain("NIFTY", expiry, spot)["chain"]
        except Exception:
            chain = []
        _CHAIN_CACHE[cache_key] = chain

    for row in chain:
        if abs(float(row["strike"]) - float(strike)) < 0.5:
            ce = row.get("ce") or {}
            ltp = float(ce.get("price") or 0)
            bid = float(ce.get("bid")   or 0)
            ask = float(ce.get("ask")   or 0)
            iv  = ce.get("iv")
            if iv:
                iv_pct     = float(iv)
                iv_decimal = iv_pct / 100.0
            d_chain = ce.get("delta")
            if d_chain is not None:
                delta = float(d_chain)
            mid = (bid + ask) / 2.0 if (bid > 0 and ask > 0) else 0.0
            current = mid if mid > 0 else (ltp if ltp > 0 else (ask if ask > 0 else bid))
            if current > 0:
                # If chain didn't carry delta, derive it via BS at the chain IV
                if delta is None:
                    _iv_solved, delta = _fill_greeks_from_bs(current)
                    if iv_pct is None and _iv_solved is not None:
                        iv_pct = _iv_solved
                return {
                    "ltp": ltp, "bid": bid, "ask": ask, "mid": mid,
                    "current": current, "source": "chain",
                    "last_trade_time": "", "iv_decimal": iv_decimal,
                    "iv_pct":     round(iv_pct, 2) if iv_pct is not None else None,
                    "delta":      round(delta, 4) if delta is not None else None,
                    "symbol": ce.get("symbol", ""),
                }
            break

    # 3) Black-Scholes fallback — flagged so the UI can warn the user.
    bs = max(_bs_call(spot, float(strike), T_years, iv_decimal), 0.05)
    iv_pct, delta = _fill_greeks_from_bs(bs)
    return {
        "ltp": 0.0, "bid": 0.0, "ask": 0.0, "mid": 0.0,
        "current": bs, "source": "bs",
        "last_trade_time": "", "iv_decimal": iv_decimal,
        "iv_pct":     round(iv_pct, 2) if iv_pct is not None else None,
        "delta":      round(delta, 4) if delta is not None else None,
        "symbol": "",
    }


def _enrich(p: dict, spot: float, nb_live: float | None) -> dict:
    etf_pnl = (round(p["shares"] * (nb_live - p["niftybees_entry_price"]), 2)
               if nb_live is not None else None)
    opt_pnl = sum(c.get("pnl", 0) or 0 for c in p.get("call_history", []))
    capture = 100.0
    current_call_price = None
    call_quote: dict | None = None
    ac = p.get("active_call")
    if ac and ac.get("premium_received"):
        pr   = float(ac["premium_received"])
        call_quote = _live_call_price(float(ac["strike"]), ac["expiry"], spot)
        current_call_price = call_quote["current"]
        mtm  = (pr - current_call_price) * int(ac["lots"]) * int(ac["lot_size"])
        opt_pnl += mtm
        capture = round((pr - current_call_price) / pr * 100, 1) if pr > 0 else 0.0
        # Refresh DTE on every read so the slim-row "Xd" stays current.
        # Stored dte goes stale once the position was created in the past;
        # always recompute from expiry vs now.
        try:
            T = opt_eng.days_to_expiry(ac["expiry"])   # years
            ac["dte"] = max(int(round(T * 365)), 0)
        except Exception:
            ac.setdefault("dte", 0)

    total_pnl = (round(etf_pnl + opt_pnl, 2) if etf_pnl is not None else None)

    ep = dict(p)
    live = {
        "nifty_spot":         round(spot, 0),
        "niftybees_price":    round(nb_live, 2) if nb_live is not None else None,
        "etf_pnl":            etf_pnl,
        "options_pnl":        round(opt_pnl, 2),
        "total_pnl":          total_pnl,
        "capture_pct":        capture,
        "current_call_price": round(current_call_price, 2) if current_call_price is not None else None,
    }
    if call_quote is not None:
        live.update({
            "call_ltp":          round(call_quote["ltp"], 2),
            "call_bid":          round(call_quote["bid"], 2),
            "call_ask":          round(call_quote["ask"], 2),
            "call_mid":          round(call_quote["mid"], 2),
            "call_price_source": call_quote["source"],   # 'direct' | 'chain' | 'bs'
            "call_symbol":       call_quote.get("symbol") or "",
            "call_last_trade":   call_quote.get("last_trade_time") or "",
            "call_delta":        call_quote.get("delta"),
            "call_iv":           call_quote.get("iv_pct"),
        })
    ep["live"] = live
    return ep


@router.get("/positions")
def list_positions():
    spot    = spot_for("NIFTY")          # raises if Kite connected but fails — spot is critical
    nb_live = _niftybees_price(spot)     # None when Kite connected but NiftyBees unavailable
    _CHAIN_CACHE.clear()                 # fresh chain per request, shared across positions
    return {
        "spot":            round(spot, 2),
        "niftybees_price": round(nb_live, 2) if nb_live is not None else None,
        "positions":       [_enrich(p, spot, nb_live) for p in _store.list_positions()],
    }


@router.post("/positions")
def create_position(body: _PositionIn):
    import uuid as _uuid, datetime as _datetime
    data = body.model_dump()
    if data.get("active_call"):
        ac = dict(data["active_call"])
        ac["id"]         = str(_uuid.uuid4())[:8]
        ac["entry_date"] = _datetime.datetime.now().isoformat()
        ac["status"]     = "open"
        data["active_call"]            = ac
        data["total_premium_collected"] = ac["premium_total"]
    else:
        data["total_premium_collected"] = 0.0
    return _store.create_position(data)


@router.put("/positions/{pid}")
def update_position(pid: str, body: dict):
    p = _store.update_position(pid, body)
    if not p:
        raise HTTPException(404, "Position not found")
    return p


@router.put("/positions/{pid}/tags")
def update_position_tags(pid: str, body: dict):
    """Replace the position's tag list with body['tags'] (a list of strings)."""
    raw = body.get("tags") or []
    if not isinstance(raw, list):
        raise HTTPException(400, "tags must be a list of strings")
    cleaned: list[str] = []
    seen: set[str] = set()
    for t in raw:
        s = str(t or "").strip().lower()
        if not s or len(s) > 24:
            continue
        if s in seen:
            continue
        seen.add(s)
        cleaned.append(s)
        if len(cleaned) >= 10:
            break
    p = _store.update_position(pid, {"tags": cleaned})
    if not p:
        raise HTTPException(404, "Position not found")
    return {"id": pid, "tags": cleaned}


@router.delete("/positions/{pid}")
def delete_position(pid: str):
    if not _store.delete_position(pid):
        raise HTTPException(404, "Position not found")
    return {"ok": True}


@router.post("/positions/{pid}/close-call")
def close_call(pid: str, body: _CloseIn):
    p = _store.close_call_cycle(
        pid,
        exit_price     = body.exit_price,
        close_kind     = body.close_kind,
        nb_action      = body.nb_action,
        nb_shares_sold = body.nb_shares_sold,
        nb_sell_price  = body.nb_sell_price,
        notes          = body.notes,
    )
    if not p:
        raise HTTPException(404, "Position not found or no active call")
    return p


@router.post("/positions/{pid}/add-call")
def add_call(pid: str, body: _CallIn):
    p = _store.add_call_cycle(pid, body.model_dump())
    if not p:
        raise HTTPException(404, "Position not found")
    return p


# ── Exit-strategy engine ───────────────────────────────────────────────────────
# Roll-up-on-momentum trigger: when Nifty has rallied past strike + 2% AND the
# call's mark-to-market loss has matched (or exceeded) the position's max profit
# AND there are still > 3 days to expiry, the user wants to close the call,
# realise the loss, and immediately reopen a new call ~2% OTM from current spot
# so the NiftyBees upside is uncapped going forward.

class _RollUpIn(_BM):
    close_price:  float        # buyback price for the current call
    new_strike:   float
    new_expiry:   str
    new_premium:  float        # premium per unit collected on the new call
    new_lots:     int = 0      # 0 → reuse current lots
    new_lot_size: int = 0      # 0 → reuse current lot_size


def _round_to_step(value: float, chain: list) -> int:
    """Round value to the nearest strike present in the chain."""
    strikes = sorted({float(r["strike"]) for r in chain})
    if not strikes:
        return int(round(value / 50.0) * 50)
    return int(min(strikes, key=lambda k: abs(k - value)))


def _strike_quote(chain: list, strike: float, side: str = "ce"):
    """Return the {price, iv, ...} dict for a given strike, or None."""
    for row in chain:
        if abs(float(row["strike"]) - strike) < 0.5:
            return row.get(side)
    return None


def _iv_label(current_iv_pct: float, vix: float | None = None) -> str:
    """Human label for the call's IV, optionally compared against VIX."""
    if current_iv_pct is None or current_iv_pct <= 0:
        return "Unknown"
    if vix and vix > 0:
        diff = current_iv_pct - vix
        if diff > 4:  return "Rich (above VIX)"
        if diff < -3: return "Cheap (below VIX)"
    if current_iv_pct < 12:  return "Cheap"
    if current_iv_pct < 18:  return "Normal"
    if current_iv_pct < 25:  return "Elevated"
    return "Very rich"


def _vix_now() -> float | None:
    """Current INDIA VIX from Kite if available, else None."""
    kite = state.get_kite()
    if not kite:
        return None
    try:
        d = kite.ltp(["NSE:INDIA VIX"])
        v = d.get("NSE:INDIA VIX", {}).get("last_price")
        return float(v) if v else None
    except Exception:
        return None


def _theta_per_day(spot: float, K: float, T_years: float, iv_decimal: float) -> float:
    """Per-unit theta: how much call value erodes in a single day at flat spot."""
    if T_years <= 1 / 365.0:
        return 0.0
    today = _bs_call(spot, K, T_years, iv_decimal)
    tomo  = _bs_call(spot, K, T_years - 1.0 / 365.0, iv_decimal)
    return max(today - tomo, 0.0)


@router.get("/positions/{pid}/exit-status")
def exit_status(pid: str):
    """Live evaluation of all four exit-strategy stops for a single position.

    Stops (in priority order, highest first):
      Stop 3 — Assignment Defence : DTE ≤ 1 AND call ITM
      Stop 2 — Roll-Up trigger    : loss ≥ premium AND Nifty > strike × 1.02
      Stop 1 — Take-Profit (50%)  : call value ≤ 50% of entry premium
      Stop 4 — Hold (default)     : nothing else firing

    Returns the live cash-flow breakdown, all four stop statuses, the
    "primary stop" the user should focus on, IV context, and a
    `recommended_action` block with step-by-step instructions plus a
    suggested roll target so the frontend can render one-click execution."""
    p = _store.get_position(pid)
    if not p:
        raise HTTPException(404, "Position not found")

    spot    = spot_for("NIFTY")
    nb_live = _niftybees_price(spot)
    ac      = p.get("active_call")

    if not ac:
        return {
            "status":          "no_call",
            "primary_stop":    None,
            "spot":            round(spot, 2),
            "niftybees_price": round(nb_live, 2) if nb_live is not None else None,
            "message":         "No active call on this position. Sell a new call to enable monitoring.",
        }

    # ── Live values from the chain ────────────────────────────────────────────
    expiry      = ac["expiry"]
    T_years     = max(opt_eng.days_to_expiry(expiry), 0.0)
    dte         = max(int(round(T_years * 365)), 0)
    qty         = int(ac["lots"]) * int(ac["lot_size"])
    prem_recv   = float(ac["premium_received"])
    prem_total  = prem_recv * qty
    K           = float(ac["strike"])
    cadence     = (ac.get("cadence") or "monthly").lower()
    entry_iv    = ac.get("entry_iv")    # IV at entry — for vega-exit comparison

    # Cadence-aware thresholds. Quarterlies exit at 30% capture and have a
    # later force-exit DTE (steep theta lives in the last 30d, not 14d).
    if cadence == "quarterly":
        TP_FACTOR        = 0.30   # 30% TP target → buy back at 70% of entry premium
        FORCE_EXIT_DTE   = 30
        TP_FACTOR_LABEL  = "30% rule"
    else:
        TP_FACTOR        = 0.60   # 60% TP target → buy back at 40% of entry premium
        FORCE_EXIT_DTE   = 14
        TP_FACTOR_LABEL  = "60% rule"

    chain_data         = _get_chain("NIFTY", expiry, spot)
    chain              = chain_data["chain"]
    iv_decimal         = 0.16
    current_iv_pct     = None

    # Same hierarchy as the hub: direct kite.quote() for the exact CE
    # symbol → cached chain row → BS estimate. The direct quote matches
    # what Zerodha shows on its own UI for that strike.
    quote              = _live_call_price(K, expiry, spot)
    current_call_price = float(quote["current"])
    if quote.get("iv_decimal"):
        iv_decimal     = float(quote["iv_decimal"])
    ce                 = _strike_quote(chain, K, "ce")
    if ce and ce.get("iv"):
        current_iv_pct = float(ce["iv"])
        iv_decimal     = current_iv_pct / 100.0

    if not current_call_price or current_call_price <= 0:
        current_call_price = max(_bs_call(spot, K, T_years, iv_decimal), 0.05)

    # ── Cash-flow snapshot ───────────────────────────────────────────────────
    buyback_cost      = current_call_price * qty
    realized_if_close = prem_total - buyback_cost            # +ve = profit, -ve = loss
    mtm_loss          = max(0.0, -realized_if_close)         # loss as positive number
    mtm_profit        = max(0.0,  realized_if_close)         # profit as positive number
    captured_pct      = (mtm_profit / prem_total * 100) if prem_total > 0 else 0.0
    pct_of_entry      = (current_call_price / prem_recv * 100) if prem_recv > 0 else 0.0

    # NB economics
    nb_pnl_unrealized   = (p["shares"] * (nb_live - p["niftybees_entry_price"])
                           if nb_live is not None else 0.0)
    combined_max_profit = (p["shares"] * (K / NIFTYBEES_RATIO - p["niftybees_entry_price"])
                           + prem_total)
    option_max_profit   = prem_total

    # Greeks-derived helpers
    intrinsic   = max(spot - K, 0.0)
    time_value  = max(current_call_price - intrinsic, 0.0)
    is_itm      = spot > K
    theta_unit  = _theta_per_day(spot, K, T_years, iv_decimal)
    theta_total = theta_unit * qty                            # ₹ today if spot flat

    # IV context
    vix       = _vix_now()
    iv_label  = _iv_label(current_iv_pct or iv_decimal * 100, vix)

    # ── Stop 1 — Take-Profit (cadence-aware) ─────────────────────────────────
    # Monthly: 60% TP (buy back at 40% of entry).
    # Quarterly: 30% TP (buy back at 70% of entry) — exits before the slow
    # theta tail eats the time you could spend on a fresh cycle.
    take_profit_threshold = prem_recv * (1.0 - TP_FACTOR)
    pct_threshold         = (1.0 - TP_FACTOR) * 100
    s1_fired = current_call_price <= take_profit_threshold and dte > 0
    s1_armed = (not s1_fired) and pct_of_entry <= pct_threshold * 1.15 and dte > 0
    s1 = {
        "name":            f"Take-Profit ({TP_FACTOR_LABEL})",
        "status":          "fired" if s1_fired else ("armed" if s1_armed else "safe"),
        "current_price":   round(current_call_price, 2),
        "threshold_price": round(take_profit_threshold, 2),
        "current_pct_of_entry": round(pct_of_entry, 1),
        "captured_amount":      round(mtm_profit, 2),
        "captured_pct":         round(captured_pct, 1),
        "remaining_potential":  round(buyback_cost, 2),
        "explainer":            (f"Captures {int(TP_FACTOR*100)}% of the cycle's max profit and recycles into a fresh trade. "
                                 f"Quarterly mode uses 30% (slow theta tail isn't worth holding); monthly mode uses 60% (steep late-cycle theta). "
                                 f"Paired with the DTE ≤ {FORCE_EXIT_DTE} force-exit, gamma/vega-tail risk is bounded."),
    }

    # ── Stop 2 — Roll-Up trigger (DELTA-BASED, cadence-aware) ────────────────
    # Monthly:   Δ 0.40 fire / Δ 0.33 arm / DTE_MIN 5
    #   Δ 0.40 ≈ 40% chance ITM. With Δ 0.25-0.30 entry, this gives ~1%
    #   Nifty breathing room before the trigger fires.
    # Quarterly: Δ 0.35 fire / Δ 0.28 arm / DTE_MIN 30
    #   Long-dated calls have ~3-4× the vega of monthlies — waiting until
    #   Δ 0.40 means a Nifty rally PLUS the IV expansion that usually
    #   accompanies it both work against you. Acting at Δ 0.35 caps the
    #   damage. DTE_MIN equals force-exit (Stop 5) — rolling later would
    #   be overridden by Stop 5 anyway, so the trigger is meaningless there.
    if cadence == "quarterly":
        DELTA_FIRE  = 0.35
        DELTA_ARM   = 0.28
        DTE_MIN     = FORCE_EXIT_DTE   # 30 — same as Stop 5 floor
    else:
        DELTA_FIRE  = 0.40
        DELTA_ARM   = 0.33
        DTE_MIN     = 5

    # Delta from the live chain row (preferred) or BS fallback.
    call_delta_abs = 0.0
    if ce and ce.get("delta") is not None:
        call_delta_abs = abs(float(ce["delta"]))
    elif T_years > 0 and iv_decimal > 0:
        bs_now = opt_eng.black_scholes(spot, K, T_years, opt_eng.RISK_FREE_RATE, iv_decimal, "CE")
        call_delta_abs = abs(float(bs_now["delta"]))

    # Old conditions kept for back-compat informational display.
    spot_threshold = K * 1.02
    s2_cond_spot   = spot > spot_threshold
    s2_cond_loss   = option_max_profit > 0 and mtm_loss >= option_max_profit
    s2_cond_dte    = dte > DTE_MIN
    s2_cond_delta  = call_delta_abs >= DELTA_FIRE

    # Delta-driven fire/arm with DTE gate so we don't roll right before
    # the force-exit (Stop 5) would fire anyway.
    s2_fired = s2_cond_delta and s2_cond_dte
    s2_armed = (not s2_fired) and call_delta_abs >= DELTA_ARM and s2_cond_dte

    s2 = {
        "name":   "Roll-Up Trigger",
        "status": "fired" if s2_fired else ("armed" if s2_armed else "safe"),
        # New primary condition
        "cond_delta":             s2_cond_delta,
        "delta_current":          round(call_delta_abs, 3),
        "delta_threshold":        DELTA_FIRE,
        "delta_arm_threshold":    DELTA_ARM,
        "delta_progress_pct":     round(min(100.0, call_delta_abs / DELTA_FIRE * 100), 1),
        # Legacy fields kept for back-compat with the existing UI panels
        "cond_spot":              s2_cond_spot,
        "cond_loss":              s2_cond_loss,
        "cond_dte":               s2_cond_dte,
        "spot_required":          round(spot_threshold, 0),
        "spot_current":           round(spot, 0),
        "spot_gap":               round(spot - spot_threshold, 0),
        "loss_required":          round(option_max_profit, 2),
        "loss_current":           round(mtm_loss, 2),
        "loss_progress_pct":      round(min(100.0, mtm_loss / option_max_profit * 100), 1) if option_max_profit > 0 else 0.0,
        "dte_current":            dte,
        "dte_min":                DTE_MIN,
        "explainer":              (
            f"Fires when the short call's delta reaches {DELTA_FIRE:.2f}. "
            f"At quarterly cadence this is set tighter (0.35 vs 0.40) because long-dated "
            f"calls have ~3-4× more vega — waiting through a vol-spike-during-rally costs "
            f"more than acting early. DTE gate: must have at least {DTE_MIN}d left to make "
            f"a fresh cycle worthwhile."
            if cadence == "quarterly" else
            "Fires when the short call's delta reaches 0.40 — roughly a 40% chance the call finishes ITM. "
            "Acting at delta 0.40 (still OTM) is much cheaper than waiting for spot to cross strike. "
            "At 0.30 entry, a 1% Nifty rally takes delta to ~0.37 — the trigger gives one Δ-step of "
            "breathing room before firing."
        ),
    }

    # ── Stop 3 — Assignment Defence ──────────────────────────────────────────
    s3_fired = dte <= 1 and is_itm
    s3_armed = (dte <= 1 and not is_itm) or (dte == 2 and is_itm)
    s3 = {
        "name":         "Assignment Defence",
        "status":       "fired" if s3_fired else ("armed" if s3_armed else "safe"),
        "is_itm":       is_itm,
        "intrinsic":    round(intrinsic, 2),
        "buyback_cost": round(buyback_cost, 2),
        "dte":          dte,
        "explainer":    "At expiry, ITM calls are auto-assigned — broker sells your NiftyBees at the strike. For a bullish long-term holder this is the worst possible outcome. Always close before expiry if the call is ITM. Cost = intrinsic value, which you'd lose to assignment anyway.",
    }

    # ── Stop 5 — DTE force-exit (cadence-aware) ──────────────────────────────
    # Monthly: DTE ≤ 14. Quarterly: DTE ≤ 30 (the steep theta window is the
    # final 30 days; holding into that range is paying maximum gamma risk
    # for marginal extra theta).
    s5_fired = 0 < dte <= FORCE_EXIT_DTE
    s5_armed = FORCE_EXIT_DTE < dte <= FORCE_EXIT_DTE + 5
    s5 = {
        "name":            f"DTE ≤ {FORCE_EXIT_DTE} Force-Exit",
        "status":          "fired" if s5_fired else ("armed" if s5_armed else "safe"),
        "dte_current":     dte,
        "dte_threshold":   FORCE_EXIT_DTE,
        "current_price":   round(current_call_price, 2),
        "buyback_cost":    round(buyback_cost, 2),
        "explainer":       f"Forces close once DTE ≤ {FORCE_EXIT_DTE}. The final stretch of any cycle is where gamma + assignment risk concentrate — paying that risk for the small remaining theta is a bad trade. Close, redeploy into the next cycle.",
    }

    # ── Stop 6 — Vega blowout (quarterly only) ───────────────────────────────
    # On long-dated CCs (90+ DTE) the call has vega ~0.3-0.4 per 1% IV move.
    # A VIX/IV spike can blow the position negative even with Nifty flat —
    # so when IV has expanded ≥ 25% from entry AND the position is MTM-negative,
    # we close to recover the residual premium and re-enter when vol normalises.
    s6_fired = False
    s6_armed = False
    iv_expand_pct = 0.0
    if cadence == "quarterly" and entry_iv and current_iv_pct and entry_iv > 0:
        iv_expand_pct = (current_iv_pct - entry_iv) / entry_iv * 100.0
        s6_fired = iv_expand_pct >= 25.0 and realized_if_close < 0 and dte > FORCE_EXIT_DTE
        s6_armed = (not s6_fired) and iv_expand_pct >= 15.0 and dte > FORCE_EXIT_DTE
    s6 = {
        "name":               "Vega Blowout (IV +25%)",
        "status":             "fired" if s6_fired else ("armed" if s6_armed else "safe"),
        "applies":            cadence == "quarterly",
        "entry_iv":           round(entry_iv, 1) if entry_iv else None,
        "current_iv":         round(current_iv_pct, 1) if current_iv_pct else None,
        "iv_expand_pct":      round(iv_expand_pct, 1),
        "iv_expand_threshold": 25.0,
        "explainer":          "Quarterly-only. On long-dated CCs, a 25%+ jump in IV erodes the premium even when Nifty is flat (vega risk). Fires when IV is up 25%+ from entry AND the position is MTM-negative — close to recover residual premium, wait for vol to normalise, re-enter.",
    }

    # ── Stop 7 — MTM stop (quarterly only) ───────────────────────────────────
    # If buyback cost has run > 1.5× the premium received, we've lost 50% of
    # the premium we collected and the trade isn't recovering. Cut bait rather
    # than wait out the steep gamma/vega tail.
    mtm_stop_threshold = prem_total * 1.5
    s7_fired = False
    s7_armed = False
    if cadence == "quarterly":
        s7_fired = buyback_cost >= mtm_stop_threshold and dte > FORCE_EXIT_DTE
        s7_armed = (not s7_fired) and buyback_cost >= prem_total * 1.25 and dte > FORCE_EXIT_DTE
    s7 = {
        "name":               "MTM Stop (loss ≥ 50% premium)",
        "status":             "fired" if s7_fired else ("armed" if s7_armed else "safe"),
        "applies":            cadence == "quarterly",
        "buyback_cost":       round(buyback_cost, 2),
        "stop_threshold":     round(mtm_stop_threshold, 2),
        "loss_pct_of_premium": round((mtm_loss / prem_total * 100) if prem_total > 0 else 0.0, 1),
        "explainer":          "Quarterly-only. Hard stop when the mark-to-market loss exceeds 50% of the premium received — the trade is no longer paying for itself and shouldn't be held into the steep tail. Close and accept the partial loss; the longer-cycle thesis is broken.",
    }

    # ── Stop 4 — Hold (no-action zone) ───────────────────────────────────────
    s4 = {
        "name":             "Hold (no-action zone)",
        "status":           "safe",  # marked active by primary_stop logic below
        "todays_theta":     round(theta_total, 2),
        "captured_so_far":  round(mtm_profit, 2),
        "remaining_to_capture": round(buyback_cost, 2),
        "explainer":        "All defensive triggers safe. Theta is decaying the call's value in your favour. Acting here only adds friction (broker fees, slippage). Let the trade work.",
    }

    # ── Priority resolution → primary stop ───────────────────────────────────
    # Order: Stop 3 (assignment defence) > Stop 7 (MTM stop) > Stop 6 (vega) >
    #        Stop 5 (DTE force-exit) > Stop 2 (roll-up) > Stop 1 (take-profit) > Stop 4 (hold)
    if s3_fired:
        primary_stop = "stop3"; primary_status = "fired"
    elif s7_fired:
        primary_stop = "stop7"; primary_status = "fired"
    elif s6_fired:
        primary_stop = "stop6"; primary_status = "fired"
    elif s5_fired:
        primary_stop = "stop5"; primary_status = "fired"
    elif s2_fired:
        primary_stop = "stop2"; primary_status = "fired"
    elif s1_fired:
        primary_stop = "stop1"; primary_status = "fired"
    elif s2_armed or s3_armed or s5_armed or s1_armed or s6_armed or s7_armed:
        # an armed condition is the next thing to watch
        if   s3_armed: primary_stop = "stop3"; primary_status = "armed"
        elif s7_armed: primary_stop = "stop7"; primary_status = "armed"
        elif s6_armed: primary_stop = "stop6"; primary_status = "armed"
        elif s5_armed: primary_stop = "stop5"; primary_status = "armed"
        elif s2_armed: primary_stop = "stop2"; primary_status = "armed"
        else:          primary_stop = "stop1"; primary_status = "armed"
    else:
        primary_stop  = "stop4"
        primary_status = "active"
        s4["status"]   = "active"

    # ── Suggested rolls — applies NB-entry strike floor ──────────────────────
    # Floor rule: new strike ≥ NB_entry × 1.01. This guarantees that even
    # if the new call eventually gets assigned, NiftyBees would be sold AT
    # OR ABOVE its cost basis — eliminating the V-recovery loss trap.
    nb_entry_nifty = float(p.get("entry_nifty") or 0)
    if nb_entry_nifty <= 0 and p.get("niftybees_entry_price"):
        nb_entry_nifty = float(p["niftybees_entry_price"]) * NIFTYBEES_RATIO
    floor_strike = nb_entry_nifty * 1.01 if nb_entry_nifty > 0 else 0.0

    def _suggest(target_pct: float):
        desired = spot * (1.0 + target_pct)
        target  = max(floor_strike, desired)         # floor wins if spot < entry
        new_K   = _round_to_step(target, chain)
        new_ce  = _strike_quote(chain, new_K, "ce")
        prem    = None
        new_iv  = None
        if new_ce:
            prem = float(new_ce.get("bid") or new_ce.get("price") or 0)
            niv  = new_ce.get("iv")
            if niv: new_iv = float(niv)
        if not prem or prem <= 0:
            prem = max(_bs_call(spot, new_K, T_years, iv_decimal), 0.05)
        return {
            "new_strike":              new_K,
            "expiry":                  expiry,
            "estimated_premium":       round(prem, 2),
            "estimated_premium_total": round(prem * qty, 2),
            "estimated_iv":            round(new_iv, 1) if new_iv else None,
            "net_cash_flow":           round(prem * qty - buyback_cost, 2),
            "floor_active":            new_K >= floor_strike > desired,
            "floor_strike":            round(floor_strike, 0) if floor_strike else None,
        }

    suggested_roll       = _suggest(0.03)   # +3% OTM (defensive — gives NB room to grow)
    suggested_takeprofit = _suggest(0.02)   # +2% OTM (income redeploy)

    # ── Recommended action (matches primary_stop) ────────────────────────────
    action_blocks = {
        "stop3": {
            "kind":  "close_only",
            "label": "Close Now — Avoid Assignment",
            "tone":  "danger",
            "steps": [
                f"1. Buy back {int(K)} CE @ ₹{current_call_price:.2f} × {qty}  →  pay ₹{buyback_cost:,.0f}",
                f"2. NiftyBees stays — DO NOT let assignment happen",
                f"3. Wait for next-week's expiry on {expiry}, then re-sell a fresh call",
            ],
            "primary_button":   "Close Call Now",
            "secondary_button": None,
        },
        "stop2": {
            "kind":  "roll_up",
            "label": "Roll Up to Higher Strike",
            "tone":  "warn",
            "steps": [
                f"⚡ Trigger fired — call delta = {call_delta_abs:.2f} (threshold {DELTA_FIRE:.2f})"
                + (f" · floor strike active ({int(floor_strike)})" if (floor_strike and suggested_roll.get('floor_active')) else ""),
                f"1. Buy back {int(K)} CE @ ₹{current_call_price:.2f} × {qty}  →  pay ₹{buyback_cost:,.0f}",
                f"   Realised loss on old call: ₹{realized_if_close:,.0f}",
                f"2. Sell new {int(suggested_roll['new_strike'])} CE @ ₹{suggested_roll['estimated_premium']:.2f} × {qty}  →  collect ₹{suggested_roll['estimated_premium_total']:,.0f}",
                f"3. Keep all NiftyBees — upside now uncapped between {int(K)} and {int(suggested_roll['new_strike'])}",
                f"   Net cash today: ₹{suggested_roll['net_cash_flow']:,.0f}",
            ],
            "primary_button":   "Roll Up Now (Close + Reopen)",
            "secondary_button": "Close Call Only",
        },
        "stop1": {
            "kind":  "take_profit",
            "label": "Take Profit + Redeploy",
            "tone":  "good",
            "steps": [
                f"1. Buy back {int(K)} CE @ ₹{current_call_price:.2f} × {qty}  →  pay ₹{buyback_cost:,.0f}",
                f"   Realised profit: +₹{mtm_profit:,.0f}  ({captured_pct:.0f}% of original premium captured)",
                f"2. Sell new {int(suggested_takeprofit['new_strike'])} CE @ ₹{suggested_takeprofit['estimated_premium']:.2f} × {qty}  →  collect ₹{suggested_takeprofit['estimated_premium_total']:,.0f}",
                f"3. Resume cycle — new theta starts decaying immediately",
            ],
            "primary_button":   "Take Profit + Roll to New Cycle",
            "secondary_button": "Just Close (no reopen)",
        },
        "stop4": {
            "kind":  "hold",
            "label": "Hold — Theta is Working",
            "tone":  "safe",
            "steps": [
                f"Call delta: {call_delta_abs:.2f}  (roll-up fires at {DELTA_FIRE:.2f})",
                f"Today's theta accrual: +₹{theta_total:,.0f}  (if Nifty stays flat)",
                f"Captured so far: +₹{mtm_profit:,.0f}  ({captured_pct:.0f}% of premium)",
                f"Next check: in 30 seconds (auto-refresh on)",
                f"Do nothing — let the trade earn out.",
            ],
            "primary_button":   None,
            "secondary_button": "Force Refresh",
        },
        "stop5": {
            "kind":  "force_close",
            "label": f"Force-Close — DTE ≤ {FORCE_EXIT_DTE}",
            "tone":  "warning",
            "steps": [
                f"DTE remaining: {dte} days  (force-exit fires at ≤ {FORCE_EXIT_DTE})",
                f"Buy back {int(K)} CE @ ₹{current_call_price:.2f} × {qty}  →  pay ₹{buyback_cost:,.0f}",
                f"Realised this cycle: ₹{mtm_profit:,.0f}",
                f"Then sell next cycle's CC at fresh strike (Δ 0.25-0.30).",
                f"Why: the final stretch carries escalating gamma + assignment risk for marginal remaining theta.",
            ],
            "primary_button":   "Close & Roll Forward",
            "secondary_button": "Just Close (no reopen)",
        },
        "stop6": {
            "kind":  "vega_close",
            "label": "Vega Blowout — Close to Recover Premium",
            "tone":  "warn",
            "steps": [
                f"IV is up {iv_expand_pct:.1f}% from entry ({entry_iv:.1f}% → {(current_iv_pct or 0):.1f}%)" if entry_iv and current_iv_pct else f"IV expanded sharply since entry",
                f"Buy back {int(K)} CE @ ₹{current_call_price:.2f} × {qty}  →  pay ₹{buyback_cost:,.0f}",
                f"MTM realised: ₹{realized_if_close:,.0f}",
                f"Wait for VIX to normalise (typically 1-3 weeks), then re-enter a fresh quarterly CC.",
                f"Why: long-dated calls have ~3-4× the vega of monthlies; a vol spike is more damaging than time decay can repair.",
            ],
            "primary_button":   "Close Call (Vega Exit)",
            "secondary_button": "Hold Anyway",
        },
        "stop7": {
            "kind":  "mtm_stop",
            "label": "MTM Stop — Cut Losses",
            "tone":  "danger",
            "steps": [
                f"Buyback cost ₹{buyback_cost:,.0f} ≥ 1.5× premium collected ₹{prem_total:,.0f}",
                f"Loss-to-premium ratio: {(mtm_loss / prem_total * 100) if prem_total else 0:.0f}%  (stop fires at 50%)",
                f"Buy back {int(K)} CE @ ₹{current_call_price:.2f} × {qty}  →  realise ₹{realized_if_close:,.0f}",
                f"Reassess: take a smaller new CC further OTM, or sit out until thesis is clearer.",
                f"Why: the quarterly thesis depends on the call decaying — at 50%+ loss it's no longer paying for itself.",
            ],
            "primary_button":   "Close Call (MTM Stop)",
            "secondary_button": "Hold Anyway",
        },
    }

    return {
        "primary_stop":    primary_stop,
        "primary_status":  primary_status,
        "spot":            round(spot, 2),
        "niftybees_price": round(nb_live, 2) if nb_live is not None else None,
        "active_call": {
            "strike":                  K,
            "expiry":                  expiry,
            "dte":                     dte,
            "lots":                    int(ac["lots"]),
            "lot_size":                int(ac["lot_size"]),
            "qty":                     qty,
            "premium_received":        round(prem_recv, 2),
            "premium_collected_total": round(prem_total, 2),
            "current_price":           round(current_call_price, 2),
            "buyback_cost_total":      round(buyback_cost, 2),
            "is_itm":                  is_itm,
            "intrinsic":               round(intrinsic, 2),
            "time_value":              round(time_value, 2),
            "mtm_loss":                round(mtm_loss, 2),
            "mtm_profit":              round(mtm_profit, 2),
            "captured_pct":            round(captured_pct, 1),
            "pct_of_entry_premium":    round(pct_of_entry, 1),
            "realized_if_close":       round(realized_if_close, 2),
            "current_iv":              round(current_iv_pct, 1) if current_iv_pct else None,
            "theta_per_day":           round(theta_unit, 2),
            "theta_per_day_total":     round(theta_total, 2),
            "delta":                   round(call_delta_abs, 3),
            "delta_threshold":         DELTA_FIRE,
        },
        "iv_context": {
            "current_iv": round(current_iv_pct, 1) if current_iv_pct else None,
            "label":      iv_label,
            "vix":        round(vix, 2) if vix else None,
            "explainer":  ("Rich IV → call is overpriced (bad time to buy back, good time to sell)"
                           if "Rich" in iv_label or "rich" in iv_label else
                           "Cheap IV → call is underpriced (good time to buy back, bad time to sell new)"
                           if "Cheap" in iv_label or "cheap" in iv_label else
                           "IV is in normal range — sell/buy decisions are theta-driven, not vol-driven"),
        },
        "option_max_profit":   round(option_max_profit, 2),
        "combined_max_profit": round(combined_max_profit, 2),
        "max_profit":          round(option_max_profit, 2),  # back-compat alias
        "nb_pnl_unrealized":   round(nb_pnl_unrealized, 2),
        "cadence": cadence,
        "stops": {
            "stop1": s1,
            "stop2": s2,
            "stop3": s3,
            "stop4": s4,
            "stop5": s5,    # DTE force-exit (cadence-aware: monthly=14d, quarterly=30d)
            "stop6": s6,    # Vega blowout (quarterly only)
            "stop7": s7,    # MTM stop (quarterly only)
        },
        "vix_skip_active":  vix > 22 if vix else False,
        "vix_skip_threshold": 22,
        "vix_skip_message": (
            f"VIX {vix:.1f} > 22 — premium looks rich but tail risk is elevated. "
            f"Skip new CC entries this cycle; existing positions managed normally."
            if (vix and vix > 22) else None
        ),
        "suggested_roll":          suggested_roll,
        "suggested_takeprofit":    suggested_takeprofit,
        "recommended_action":      action_blocks[primary_stop],

        # Back-compat keys for the old single-trigger UI (legacy)
        "status": primary_status,
        "trigger": {
            # Old fields (informational / kept for any consumer that reads them)
            "spot_above_strike_2pct": s2_cond_spot,
            "spot_required":          round(spot_threshold, 0),
            "spot_current":           round(spot, 0),
            "spot_gap":               round(spot - spot_threshold, 0),
            "loss_geq_max_profit":    s2_cond_loss,
            "loss_required":          round(option_max_profit, 2),
            "loss_current":           round(mtm_loss, 2),
            "loss_progress_pct":      round(min(100.0, mtm_loss / option_max_profit * 100), 1) if option_max_profit > 0 else 0.0,
            "dte_above_3":            s2_cond_dte,
            "dte_current":            dte,
            # New primary trigger inputs
            "delta_geq_threshold":    s2_cond_delta,
            "delta_current":          round(call_delta_abs, 3),
            "delta_threshold":        DELTA_FIRE,
            "delta_progress_pct":     round(min(100.0, call_delta_abs / DELTA_FIRE * 100), 1),
        },
    }


def _close_kind(c: dict) -> str:
    """Return the type of close. Uses the explicit `close_kind` field set when
    the user closed via the rich dialog; otherwise falls back to a derivation
    from pnl + exit_price + status so historical (pre-feature) cycles still
    render correctly."""
    explicit = (c.get("close_kind") or "").strip().lower()
    if explicit in ("expired_worthless", "closed_at_profit", "closed_at_loss",
                    "rolled", "assigned"):
        return explicit

    status     = (c.get("status") or "").lower()
    pnl        = float(c.get("pnl") or 0)
    exit_price = float(c.get("exit_price") or 0)
    if status == "rolled":
        return "rolled"
    if exit_price <= 0.05 and pnl > 0:
        return "expired_worthless"
    if pnl >= 0:
        return "closed_at_profit"
    return "closed_at_loss"


def _build_monthly_breakdown(cycles: list) -> list:
    """Group closed cycles by YYYY-MM and sum realised P&L per month."""
    from collections import OrderedDict
    months: dict = OrderedDict()
    for c in cycles:
        date = c.get("exit_date") or c.get("entry_date") or ""
        if not date:
            continue
        ym = date[:7]
        months.setdefault(ym, 0.0)
        months[ym] += float(c.get("pnl") or 0)
    return [{"month": m, "pnl": round(v, 2)} for m, v in sorted(months.items())]


def _build_pnl_decomposition(cycles: list, active_positions: list, nb_live: float) -> dict:
    """Cumulative P&L decomposed by source over time + current totals.

      premium_kept   = realised positive option P&L (premium captured)
      option_losses  = realised negative option P&L (defensive closes)
      nb_realised    = (not yet tracked — placeholder, 0 unless future field)
      nb_unrealised  = current MTM on NB still held in active positions
    """
    series:        list  = []
    running_prem:  float = 0.0
    running_loss:  float = 0.0  # stored as POSITIVE (drawn below 0)
    sorted_cycles = sorted(cycles, key=lambda c: c.get("exit_date") or c.get("entry_date") or "")

    for c in sorted_cycles:
        date = c.get("exit_date") or c.get("entry_date") or ""
        if not date:
            continue
        pnl = float(c.get("pnl") or 0)
        if pnl >= 0:
            running_prem += pnl
        else:
            running_loss += -pnl
        series.append({
            "date":          date[:10],
            "premium_kept":  round(running_prem, 2),
            "option_losses": round(running_loss, 2),  # positive number
            "net_realised":  round(running_prem - running_loss, 2),
            "cycle_pnl":     round(pnl, 2),
            "cycle_kind":    _close_kind(c),
        })

    nb_unrealised = sum(
        p.get("shares", 0) * (nb_live - p.get("niftybees_entry_price", nb_live))
        for p in active_positions
    )
    return {
        "series": series,
        "totals": {
            "premium_kept":  round(running_prem, 2),
            "option_losses": round(running_loss, 2),
            "net_realised":  round(running_prem - running_loss, 2),
            "nb_unrealised": round(nb_unrealised, 2),
            "grand_total":   round(running_prem - running_loss + nb_unrealised, 2),
        },
    }


def _build_cycle_outcomes(cycles: list) -> dict:
    """Per-cycle outcome data for the bars chart + a distribution summary."""
    sorted_cycles = sorted(cycles, key=lambda c: c.get("exit_date") or c.get("entry_date") or "")
    bars: list = []
    counts = {"expired_worthless": 0, "closed_at_profit": 0, "closed_at_loss": 0, "rolled": 0}
    for c in sorted_cycles:
        date = c.get("exit_date") or c.get("entry_date") or ""
        kind = _close_kind(c)
        counts[kind] = counts.get(kind, 0) + 1
        bars.append({
            "date":    date[:10],
            "pnl":     round(float(c.get("pnl") or 0), 2),
            "strike":  c.get("strike"),
            "kind":    kind,
        })
    total = max(1, sum(counts.values()))
    distribution = [
        {"kind": k, "count": v, "pct": round(v / total * 100, 1)}
        for k, v in counts.items() if v > 0
    ]
    return {"bars": bars, "distribution": distribution, "total_closed": sum(counts.values())}


@router.get("/hub-summary")
def hub_summary():
    """Categorize all active positions by their primary exit-strategy stop and
    return aggregated lifetime stats + a cumulative-P&L time series + a
    monthly P&L breakdown so the home screen can render KPIs, charts, and a
    grouped action queue without N round-trips per position."""
    from datetime import datetime
    positions = _store.list_positions()
    spot      = spot_for("NIFTY")
    nb_live   = _niftybees_price(spot)
    active    = [p for p in positions if p.get("status") == "active"]

    by_stop: dict = {"stop1": [], "stop2": [], "stop3": [], "stop4": []}
    for p in active:
        enriched = _enrich(p, spot, nb_live)
        if not p.get("active_call"):
            # No active call — surface in stop4 (idle) so user sees them
            enriched["primary_stop"]   = "stop4"
            enriched["primary_status"] = "safe"
            enriched["stop_summary"]   = {"name": "Idle (no active call)", "detail": "Sell a new call to start earning"}
            by_stop["stop4"].append(enriched)
            continue
        try:
            es = exit_status(p["id"])
            primary = es.get("primary_stop") or "stop4"
            ac = es.get("active_call", {})
            stop_obj = es.get("stops", {}).get(primary, {})
            # Build a compact summary the hub card displays
            if primary == "stop1":
                detail = (f"Call ₹{ac.get('current_price', 0):.2f} = "
                          f"{ac.get('pct_of_entry_premium', 0):.0f}% of entry · captured +₹{int(ac.get('mtm_profit', 0)):,}")
            elif primary == "stop2":
                detail = (f"Loss ₹{int(ac.get('mtm_loss', 0)):,} ≥ premium ₹{int(es.get('option_max_profit', 0)):,} · "
                          f"Nifty {int(es.get('spot', 0)):,} > strike+2%")
            elif primary == "stop3":
                detail = (f"DTE {ac.get('dte', 0)}d · "
                          f"{'ITM intrinsic ₹' + str(int(ac.get('intrinsic', 0))) if ac.get('is_itm') else 'still OTM'}")
            else:
                detail = (f"Theta +₹{int(ac.get('theta_per_day_total', 0)):,}/day · "
                          f"captured +₹{int(ac.get('mtm_profit', 0)):,} · "
                          f"DTE {ac.get('dte', 0)}d")
            enriched["primary_stop"]   = primary
            enriched["primary_status"] = es.get("primary_status", "safe")
            enriched["stop_summary"]   = {"name": stop_obj.get("name", ""), "detail": detail}
            enriched["active_call_live"] = ac
            enriched["recommended_kind"] = es.get("recommended_action", {}).get("kind", "hold")
            by_stop[primary].append(enriched)
        except Exception:
            enriched["primary_stop"]   = "stop4"
            enriched["primary_status"] = "safe"
            enriched["stop_summary"]   = {"name": "Live data unavailable", "detail": "Could not fetch live chain — using cached values"}
            by_stop["stop4"].append(enriched)

    # Sort each category. For action stops (1/2/3) — most urgent first (oldest entry,
    # closest to expiry). For hold (4) — newest first.
    def _urgency_key(e):
        ac = e.get("active_call_live") or e.get("active_call") or {}
        return (ac.get("dte", 999), e.get("created_at", ""))
    for k in ("stop1", "stop2", "stop3"):
        by_stop[k].sort(key=_urgency_key)
    by_stop["stop4"].sort(key=lambda e: e.get("created_at", ""), reverse=True)

    # ── Lifetime stats ──────────────────────────────────────────────────────
    closed_cycles: list = []
    for p in positions:
        for c in p.get("call_history", []):
            if c.get("status") in ("closed", "expired", "rolled"):
                closed_cycles.append(c)

    total_premium_collected = sum(float(p.get("total_premium_collected", 0) or 0) for p in positions)
    total_realized_pnl      = sum(float(c.get("pnl") or 0) for c in closed_cycles)
    winning                 = [c for c in closed_cycles if (c.get("pnl") or 0) > 0]
    win_rate                = (len(winning) / len(closed_cycles) * 100) if closed_cycles else 0.0

    active_options_unrealized = 0.0
    active_nb_unrealized      = 0.0
    for p in active:
        if nb_live is not None:
            active_nb_unrealized += float(p.get("shares", 0)) * (nb_live - float(p.get("niftybees_entry_price", nb_live)))
        live = p.get("live") or {}
        active_options_unrealized += float(live.get("options_pnl") or 0)

    # ── Charts: P&L decomposition + cycle outcomes ───────────────────────────
    decomposition = _build_pnl_decomposition(closed_cycles, active, nb_live or 0.0)
    outcomes      = _build_cycle_outcomes(closed_cycles)
    monthly       = _build_monthly_breakdown(closed_cycles)

    # ── Closed-cycles detail (flattened across positions, newest first) ──────
    closed_cycles_detail: list = []
    for p in positions:
        for c in p.get("call_history", []):
            if c.get("status") in ("closed", "expired", "rolled"):
                detail = dict(c)
                detail["position_id"]   = p["id"]
                detail["position_name"] = p.get("name", "")
                detail["kind"]          = _close_kind(c)
                closed_cycles_detail.append(detail)
    closed_cycles_detail.sort(
        key=lambda c: c.get("exit_date") or c.get("entry_date") or "",
        reverse=True,
    )

    # ── Actionable insights ──────────────────────────────────────────────────
    cat_counts = {k: len(v) for k, v in by_stop.items()}

    # 1. Next-action banner — highest-priority pending stop
    if cat_counts["stop3"] > 0:
        next_action = {
            "kind":     "stop3",
            "tone":     "danger",
            "icon":     "🛡️",
            "headline": f"{cat_counts['stop3']} position{'s' if cat_counts['stop3'] != 1 else ''} need CLOSE NOW",
            "subhead":  "Assignment defence — close before expiry to keep your NiftyBees",
            "cta":      "View urgent",
        }
    elif cat_counts["stop2"] > 0:
        next_action = {
            "kind":     "stop2",
            "tone":     "warn",
            "icon":     "🚨",
            "headline": f"{cat_counts['stop2']} position{'s' if cat_counts['stop2'] != 1 else ''} ready to ROLL UP",
            "subhead":  "Nifty rallied past strike — uncap NB to capture more upside",
            "cta":      "View roll-ups",
        }
    elif cat_counts["stop1"] > 0:
        next_action = {
            "kind":     "stop1",
            "tone":     "good",
            "icon":     "💰",
            "headline": f"{cat_counts['stop1']} position{'s' if cat_counts['stop1'] != 1 else ''} ready for TAKE PROFIT",
            "subhead":  "Easy theta captured — buy back + redeploy for next cycle",
            "cta":      "View take-profits",
        }
    else:
        all_clear = cat_counts["stop4"] > 0
        next_action = {
            "kind":     "stop4" if all_clear else "none",
            "tone":     "safe",
            "icon":     "✓",
            "headline": "All clear" if all_clear else "No active calls",
            "subhead":  "Theta is working — no action needed" if all_clear else "Sell a new call to start the engine",
            "cta":      "Open a new position" if not all_clear else None,
        }

    # 2. This week's expected income — sum of theta on each open call × DTE
    #    (approximates: at expiry, the entire current call value goes to zero,
    #     so the cash you'd "collect" by holding is the buyback_cost_total)
    this_week_potential = 0.0
    for p in active:
        if not p.get("active_call"):
            continue
        try:
            es = exit_status(p["id"])
            ac = es.get("active_call", {})
            this_week_potential += float(ac.get("buyback_cost_total") or 0) - 0  # what you'd save by holding
        except Exception:
            pass

    # 3. Last-12-week avg + last-week running total (trend)
    from datetime import timedelta
    today = datetime.now().date()
    last_12wk_pnl: list = []
    last_week_pnl  = 0.0
    this_week_pnl  = 0.0
    for c in closed_cycles:
        date_str = c.get("exit_date") or c.get("entry_date") or ""
        if not date_str:
            continue
        try:
            d = datetime.fromisoformat(date_str[:10]).date()
        except Exception:
            continue
        days_ago = (today - d).days
        if 0   <= days_ago < 7:    this_week_pnl  += float(c.get("pnl") or 0)
        if 7   <= days_ago < 14:   last_week_pnl  += float(c.get("pnl") or 0)
        if 0   <= days_ago < 84:   last_12wk_pnl.append(float(c.get("pnl") or 0))
    avg_weekly = (sum(last_12wk_pnl) / max(1, len(last_12wk_pnl) / 7 * 7)) if last_12wk_pnl else 0.0
    # simpler: total / 12
    avg_weekly = (sum(last_12wk_pnl) / 12) if last_12wk_pnl else 0.0

    # 4. Capital deployed + annualised yield
    total_nb_capital = sum(float(p.get("niftybees_cost", 0) or 0) for p in active)
    annualised_yield_pct = 0.0
    if total_nb_capital > 0 and len(last_12wk_pnl) > 0:
        weekly = sum(last_12wk_pnl) / 12
        annualised_yield_pct = weekly * 52 / total_nb_capital * 100

    return {
        "spot":            round(spot, 2),
        "niftybees_price": round(nb_live, 2) if nb_live is not None else None,
        "by_stop":         by_stop,
        "category_counts": cat_counts,

        "insights": {
            "next_action":       next_action,
            "this_week_potential": round(this_week_potential, 2),
            "this_week_so_far":    round(this_week_pnl, 2),
            "last_week_pnl":       round(last_week_pnl, 2),
            "avg_weekly_pnl":      round(avg_weekly, 2),
            "weeks_in_avg":        min(12, max(1, len(last_12wk_pnl))),
            "total_nb_capital":    round(total_nb_capital, 2),
            "annualised_yield":    round(annualised_yield_pct, 1),
        },

        "stats": {
            "total_premium_collected":   round(total_premium_collected, 2),
            "total_realized_pnl":        round(total_realized_pnl, 2),
            "active_positions":          len(active),
            "active_cycles":             len([p for p in active if p.get("active_call")]),
            "closed_cycles":             len(closed_cycles),
            "winning_cycles":            len(winning),
            "losing_cycles":             len(closed_cycles) - len(winning),
            "win_rate_pct":              round(win_rate, 1),
            "active_nb_unrealized":      round(active_nb_unrealized, 2),
            "active_options_unrealized": round(active_options_unrealized, 2),
            "lifetime_grand_total":      round(total_realized_pnl + active_nb_unrealized + active_options_unrealized, 2),
        },

        # New, more informative chart series
        "pnl_decomposition":     decomposition,
        "cycle_outcomes":        outcomes,
        "monthly_breakdown":     monthly,
        "closed_cycles_detail":  closed_cycles_detail,

        "generated_at":      datetime.now().isoformat(),
    }


@router.post("/positions/{pid}/roll-up")
def roll_up(pid: str, body: _RollUpIn):
    """Atomically close the current call (booking the realised loss) and open
    a new call at the supplied strike/expiry/premium. The store appends both
    legs to call_history so the audit trail is preserved."""
    p_before = _store.get_position(pid)
    if not p_before or not p_before.get("active_call"):
        raise HTTPException(404, "Position not found or no active call")
    ac        = p_before["active_call"]
    new_lots  = body.new_lots     or int(ac["lots"])
    new_size  = body.new_lot_size or int(ac["lot_size"])

    p = _store.close_call_cycle(pid, body.close_price)
    if not p:
        raise HTTPException(500, "Close-call step failed")

    new_call = {
        "strike":           body.new_strike,
        "expiry":           body.new_expiry,
        "lots":             new_lots,
        "lot_size":         new_size,
        "premium_received": body.new_premium,
        "premium_total":    body.new_premium * new_lots * new_size,
    }
    p = _store.add_call_cycle(pid, new_call)
    if not p:
        raise HTTPException(500, "Add-call step failed")
    return p
