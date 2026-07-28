"""
Protected Wheel — 5-card decision dashboard.

A wheel runs as a CYCLE; on any given day 2-3 legs are simultaneously live.
The frontend asks for one /scan that fills 5 cards, each card answering one
question with "what to do now + WHY":

  1. NB Entry         — when + price + qty to buy NiftyBees
  2. CSP Put Sell     — strike + expiry + premium for a cash-secured put
  3. Hedge Put Buy    — 90-day OTM long put as crash insurance
  4. CC Call Sell     — strike + expiry for the short call against NB
  5. Roll Watch       — aggregated triggers across all legs

The orchestrator /best-cycle returns ONE optimal end-to-end plan
(NB entry + CSP + hedge + CC) ranked by risk-adjusted yield.

Most heavy lifting (chain access, BS pricing, VIX, ranking) is reused
from api/routes/covered_call.py to avoid logic drift.
"""
from __future__ import annotations
import math as _math
import datetime as _dt
from fastapi import APIRouter

from api.core.chain import spot_for, build_real_chain, _get_nfo_instruments
from api import options_engine as opt_eng, state
from api.routes.covered_call import (
    _bs_call,
    _days_to_50pct_capture,
    _fetch_vix,
    _get_chain,
    _niftybees_price,
    _rank_strikes,
    NIFTYBEES_RATIO,
)

router = APIRouter(prefix="/api/protected-wheel", tags=["protected_wheel"])


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _norm_cdf(x: float) -> float:
    """Abramowitz-Stegun approximation, ~7e-4 error."""
    a1, a2, a3, a4, a5, p = 0.254829592, -0.284496736, 1.421413741, -1.453152027, 1.061405429, 0.3275911
    sg = -1 if x < 0 else 1
    ax = abs(x) / _math.sqrt(2)
    t  = 1 / (1 + p * ax)
    y  = 1 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * _math.exp(-ax * ax)
    return 0.5 * (1 + sg * y)


def _norm_pdf(x: float) -> float:
    return (1.0 / _math.sqrt(2 * _math.pi)) * _math.exp(-0.5 * x * x)


def _bs_put(S: float, K: float, T: float, iv: float = 0.16, r: float = 0.065) -> float:
    """Black-Scholes price for a European put. iv is decimal (0.16 = 16%)."""
    if T <= 0 or iv <= 0:
        return max(K - S, 0.0)
    sqrtT = _math.sqrt(T)
    d1 = (_math.log(S / K) + (r + 0.5 * iv * iv) * T) / (iv * sqrtT)
    d2 = d1 - iv * sqrtT
    return K * _math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)


def _bs_put_greeks(S: float, K: float, T: float, iv: float, r: float = 0.065) -> dict:
    """All Greeks for a European put (BS).

    Returns: {price, delta, gamma, theta_per_day, vega_per_pct, d1, d2}.
    Theta is per calendar day; vega is per 1% IV move.
    """
    if T <= 0 or iv <= 0:
        return {"price": max(K - S, 0.0), "delta": -1.0 if S < K else 0.0,
                "gamma": 0.0, "theta_per_day": 0.0, "vega_per_pct": 0.0, "d1": 0.0, "d2": 0.0}
    sqrtT = _math.sqrt(T)
    d1 = (_math.log(S / K) + (r + 0.5 * iv * iv) * T) / (iv * sqrtT)
    d2 = d1 - iv * sqrtT
    price = K * _math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)
    delta = _norm_cdf(d1) - 1.0                                  # put delta
    gamma = _norm_pdf(d1) / (S * iv * sqrtT)
    theta_year = -(S * _norm_pdf(d1) * iv) / (2.0 * sqrtT) + r * K * _math.exp(-r * T) * _norm_cdf(-d2)
    theta_per_day = theta_year / 365.0
    vega_per_pct  = (S * _norm_pdf(d1) * sqrtT) / 100.0           # per 1% IV move
    return {"price": price, "delta": delta, "gamma": gamma,
            "theta_per_day": theta_per_day, "vega_per_pct": vega_per_pct,
            "d1": d1, "d2": d2}


def _fetch_hedge_chain(underlying: str, expiry: str, spot: float) -> dict | None:
    """Fetch a WIDE PE-only chain for hedge selection.

    The standard `build_real_chain` returns ±12 strikes around ATM (≈±2.4% on
    Nifty). Hedges need 4-12% OTM puts which fall *outside* that window.
    This fetcher pulls all PE strikes in spot × [0.84, 0.98] so the hedge
    ranker has enough candidates.

    Returns a dict in the same shape as build_real_chain — rows with `pe`
    data and dummy `ce` — so `_rank_hedge_puts` doesn't need any changes.
    Returns None when Kite is offline.
    """
    kite = state.get_kite()
    if not kite:
        return None
    instruments = _get_nfo_instruments()
    if not instruments:
        return None

    def _exp_iso(i):
        e = i.get("expiry")
        return e.isoformat() if hasattr(e, "isoformat") else str(e)[:10]

    relevant = [
        i for i in instruments
        if str(i.get("name", "")).upper() == underlying.upper()
        and i.get("instrument_type") == "PE"
        and _exp_iso(i) == expiry
    ]
    if not relevant:
        return None

    lo_strike = spot * 0.84
    hi_strike = spot * 0.98
    syms = [i for i in relevant if lo_strike <= float(i["strike"]) <= hi_strike]
    if not syms:
        return None

    keys   = [f"NFO:{i['tradingsymbol']}" for i in syms]
    quotes: dict = {}
    for start in range(0, len(keys), 500):
        try:
            chunk = kite.quote(keys[start: start + 500])
            for k, v in chunk.items():
                sym = k.split(":", 1)[1]
                depth = v.get("depth") or {}
                buys  = depth.get("buy")  or []
                sells = depth.get("sell") or []
                quotes[sym] = {
                    "price": v.get("last_price") or 0.05,
                    "oi":    v.get("oi") or 0,
                    "bid":   (buys[0].get("price")  if buys  else 0) or 0,
                    "ask":   (sells[0].get("price") if sells else 0) or 0,
                }
        except Exception as e:
            print(f"[hedge-chain] quote batch error: {e}")

    T = opt_eng.days_to_expiry(expiry)
    r = opt_eng.RISK_FREE_RATE
    chain_rows: list = []
    for inst in syms:
        K   = float(inst["strike"])
        sym = inst["tradingsymbol"]
        q   = quotes.get(sym, {"price": 0.05, "oi": 0, "bid": 0, "ask": 0})
        price = max(float(q["price"]), 0.05)
        # Greeks via implied vol
        try:
            iv_pct = opt_eng.implied_volatility(spot, K, T, r, price, "PE")
        except Exception:
            iv_pct = 14.0
        if not (1.0 <= iv_pct <= 150.0):
            iv_pct = 14.0
        sigma = iv_pct / 100.0
        try:
            bs = opt_eng.black_scholes(spot, K, T, r, sigma, "PE")
            delta = bs["delta"]
        except Exception:
            delta = -0.20
        chain_rows.append({
            "strike": K,
            "ce": {"price": 0.0},   # dummy — hedge ranker only uses pe
            "pe": {
                "price": price, "iv": round(iv_pct, 2), "delta": delta,
                "oi": int(q["oi"]), "bid": float(q["bid"]), "ask": float(q["ask"]),
                "symbol": sym,
            },
        })

    chain_rows.sort(key=lambda r: r["strike"])
    return {
        "underlying": underlying,
        "spot":       spot,
        "expiry":     expiry,
        "dte":        int(T * 365),
        "atm_strike": min((r["strike"] for r in chain_rows), key=lambda k: abs(k - spot)),
        "chain":      chain_rows,
    }


def _nifty_history_closes(days: int = 80) -> list[float]:
    """Daily closes for NIFTY 50 (or NIFTYBEES as proxy if index history fails).
    Returns ordered list, oldest first. Empty list if unavailable."""
    kite = state.get_kite()
    if not kite:
        return []
    try:
        from datetime import datetime, timedelta
        to_dt   = datetime.now()
        from_dt = to_dt - timedelta(days=int(days * 1.6))   # buffer for weekends
        # Index instrument token (NIFTY 50 cash). 256265 is the standard token used by Kite.
        try:
            candles = kite.historical_data(256265, from_dt.strftime("%Y-%m-%d"),
                                            to_dt.strftime("%Y-%m-%d"), "day")
            return [float(c["close"]) for c in candles][-days:]
        except Exception:
            pass
        # Fallback: NIFTYBEES daily candles, scaled ×100 to approximate Nifty
        instruments = kite.instruments("NSE")
        token = next((i["instrument_token"] for i in instruments
                      if i["tradingsymbol"] == "NIFTYBEES"), None)
        if not token:
            return []
        candles = kite.historical_data(token, from_dt.strftime("%Y-%m-%d"),
                                        to_dt.strftime("%Y-%m-%d"), "day")
        return [float(c["close"]) * NIFTYBEES_RATIO for c in candles][-days:]
    except Exception:
        return []


def _rsi14(closes: list[float]) -> float | None:
    """Wilder's RSI(14). Needs ≥ 15 closes; returns None otherwise."""
    if len(closes) < 15:
        return None
    gains, losses = 0.0, 0.0
    for i in range(1, 15):
        d = closes[i] - closes[i - 1]
        if d >= 0: gains += d
        else:      losses -= d
    avg_gain = gains / 14.0
    avg_loss = losses / 14.0
    for i in range(15, len(closes)):
        d = closes[i] - closes[i - 1]
        g = max(d, 0.0)
        l = max(-d, 0.0)
        avg_gain = (avg_gain * 13 + g) / 14.0
        avg_loss = (avg_loss * 13 + l) / 14.0
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - 100 / (1 + rs), 1)


def _sma(closes: list[float], n: int) -> float | None:
    if len(closes) < n:
        return None
    return round(sum(closes[-n:]) / n, 2)


def _vix_label(vix: float) -> tuple[str, bool]:
    if vix >= 22:   return ("Too high — elevated risk, premium rich but moves wild", False)
    if vix >= 18:   return ("Elevated — premium rich but expect bigger swings",       True)
    if vix >= 13:   return ("Healthy regime — best zone for selling premium",         True)
    if vix >= 11:   return ("Acceptable but premiums thin",                           True)
    return            ("Too low — premiums won't compensate the risk",                False)


def _nb_entry_signal(
    spot: float,
    nb_price: float | None,
    vix: float,
    atm_iv: float,
    closes: list[float],
    capital: float,
) -> dict:
    """Card 1 — when + price + qty to buy NiftyBees.

    Combines VIX regime, IV richness vs VIX, RSI(14), 50DMA distance,
    intraday gap to produce GO / WAIT / AGGRESSIVE / SKIP.
    """
    sma50 = _sma(closes, 50)
    sma20 = _sma(closes, 20)
    rsi   = _rsi14(closes)

    dist_50dma_pct = ((spot - sma50) / sma50 * 100) if sma50 else None
    dist_20dma_pct = ((spot - sma20) / sma20 * 100) if sma20 else None

    iv_richness_pct = ((atm_iv - vix) / vix * 100) if (vix > 0 and atm_iv > 0) else 0.0

    checks: list[dict] = []

    # 1) VIX regime
    vix_label, vix_ok = _vix_label(vix)
    checks.append({
        "name": "VIX regime",
        "value": f"{vix:.1f}",
        "ok": vix_ok,
        "rationale": f"India VIX is {vix:.1f}. {vix_label}. Premium income hinges on VIX staying above 11.",
    })

    # 2) IV richness vs VIX (chain pricing in more vol than market)
    iv_ok = atm_iv > 0 and iv_richness_pct >= -3
    if atm_iv <= 0:
        iv_msg = "ATM IV unavailable — falling back to VIX-only signal."
    elif iv_richness_pct >= 5:
        iv_msg = f"ATM IV is +{iv_richness_pct:.1f}% above VIX — chain is pricing premium richly. Selling vol now is favorable."
    elif iv_richness_pct >= -3:
        iv_msg = f"ATM IV is fair vs VIX ({iv_richness_pct:+.1f}%). Premium is in line with market expectation."
    else:
        iv_msg = f"ATM IV is {iv_richness_pct:.1f}% below VIX — chain is cheap. Selling vol pays less than the market regime suggests."
    checks.append({
        "name": "IV richness",
        "value": f"{iv_richness_pct:+.1f}% vs VIX" if atm_iv > 0 else "—",
        "ok": iv_ok,
        "rationale": iv_msg,
    })

    # 3) RSI(14)
    if rsi is not None:
        rsi_ok = 35 <= rsi <= 70
        if rsi >= 75:
            rsi_msg = f"RSI {rsi:.0f} — overbought. Pullback risk; better to wait or scale in."
        elif rsi >= 60:
            rsi_msg = f"RSI {rsi:.0f} — strong but not extended. OK to enter, mind drawdown."
        elif rsi >= 45:
            rsi_msg = f"RSI {rsi:.0f} — neutral momentum. Clean entry zone."
        elif rsi >= 30:
            rsi_msg = f"RSI {rsi:.0f} — pullback territory. Often a good buying zone for wheel entries."
        else:
            rsi_msg = f"RSI {rsi:.0f} — oversold. Either bounce coming or trend break — be cautious."
            rsi_ok = False
        checks.append({"name": "RSI(14)", "value": f"{rsi:.0f}",     "ok": rsi_ok, "rationale": rsi_msg})
    else:
        checks.append({"name": "RSI(14)", "value": "—", "ok": True,
                       "rationale": "RSI history unavailable (need ≥ 15 daily candles)."})

    # 4) 50DMA distance
    if dist_50dma_pct is not None:
        if dist_50dma_pct >= 5:
            dma_ok, dma_msg = False, f"NIFTY {dist_50dma_pct:+.1f}% above 50DMA — extended; mean-reversion risk on entry."
        elif dist_50dma_pct >= -2:
            dma_ok, dma_msg = True,  f"NIFTY {dist_50dma_pct:+.1f}% from 50DMA — healthy uptrend support."
        elif dist_50dma_pct >= -5:
            dma_ok, dma_msg = True,  f"NIFTY {dist_50dma_pct:+.1f}% below 50DMA — pullback to support, classic wheel entry."
        else:
            dma_ok, dma_msg = False, f"NIFTY {dist_50dma_pct:+.1f}% below 50DMA — possible trend break. Defensive sizing."
        checks.append({"name": "50DMA distance", "value": f"{dist_50dma_pct:+.1f}%", "ok": dma_ok, "rationale": dma_msg})
    else:
        checks.append({"name": "50DMA distance", "value": "—", "ok": True,
                       "rationale": "50DMA unavailable (need 50 days of history)."})

    # 5) 20DMA proximity (short-term tactical)
    if dist_20dma_pct is not None:
        sm_ok, sm_msg = True, f"NIFTY {dist_20dma_pct:+.1f}% from 20DMA — short-term pulse "
        sm_msg += "intact." if -3 <= dist_20dma_pct <= 3 else (
            "stretched — wait for revert." if abs(dist_20dma_pct) > 5 else "in normal swing band.")
        if abs(dist_20dma_pct) > 5:
            sm_ok = False
        checks.append({"name": "20DMA pulse", "value": f"{dist_20dma_pct:+.1f}%", "ok": sm_ok, "rationale": sm_msg})

    # Combine into signal
    passed = sum(1 for c in checks if c["ok"])
    total  = len(checks)
    if passed >= total - 1:
        signal = "go"
    elif passed >= max(2, total - 2):
        signal = "caution"
    else:
        signal = "skip"

    # Suggested entry price
    if nb_price is None or nb_price <= 0:
        nb_price = round(spot / NIFTYBEES_RATIO, 2)

    # Limit price logic: if RSI extended, suggest a -0.5% pullback limit;
    # if pullback territory, suggest current LTP. Otherwise mid.
    if rsi is not None and rsi >= 65:
        limit_price = round(nb_price * 0.995, 2)
        limit_basis = "RSI elevated — scale in on small pullback."
    elif rsi is not None and rsi <= 40 and (dist_50dma_pct or 0) <= 0:
        limit_price = round(nb_price * 1.001, 2)
        limit_basis = "RSI/50DMA at support — buy at market or just above."
    else:
        limit_price = nb_price
        limit_basis = "Neutral regime — buy at current LTP."

    # Sizing: target 1 Nifty lot equivalent (lot_size × spot/NB ratio)
    shares_per_lot = int(round(NIFTYBEES_RATIO))   # rough: 100 NB ≈ 1 Nifty unit
    # Better: shares = round(capital / nb_price)
    shares = int(capital / nb_price) if nb_price > 0 else 0
    cost   = round(shares * nb_price, 0)

    return {
        "signal": signal,
        "passed": passed,
        "total":  total,
        "checks": checks,
        "nb_price": round(nb_price, 2),
        "nb_suggested_limit": limit_price,
        "limit_basis": limit_basis,
        "shares": shares,
        "cost":   cost,
        "rsi": rsi,
        "sma_50": sma50,
        "sma_20": sma20,
        "dist_50dma_pct": round(dist_50dma_pct, 2) if dist_50dma_pct is not None else None,
        "dist_20dma_pct": round(dist_20dma_pct, 2) if dist_20dma_pct is not None else None,
        "summary": _nb_summary(signal, shares, nb_price, limit_price, limit_basis),
    }


def _nb_summary(signal: str, shares: int, nb_price: float, limit_price: float, basis: str) -> str:
    head = {"go": "GO — signals favor entry.",
            "caution": "CAUTION — mixed signals; consider scaling in.",
            "skip": "SKIP — wait for cleaner setup."}.get(signal, signal)
    if shares <= 0:
        return f"{head}"
    return f"{head} Buy {shares:,} NB at ₹{nb_price:.2f} (limit ₹{limit_price:.2f}). {basis}"


# ─────────────────────────────────────────────────────────────────────────────
# CSP ranking (cash-secured put — Card 2)
# ─────────────────────────────────────────────────────────────────────────────

def _rank_csp_puts(spot: float, vix: float, chain: list, dte: int, capital: float) -> list:
    """Rank short OTM puts on income velocity + safety + cost-basis comfort.

    Score = annualised early-exit yield × P(stays OTM) × liquidity × comfort
    where comfort penalises strikes that, if assigned, leave a too-aggressive cost basis.
    """
    out: list = []
    safe_dte = max(dte, 1)
    for row in chain:
        strike = float(row["strike"])
        pe = row.get("pe", {})
        price = float(pe.get("price", 0) or 0)
        if strike >= spot or price <= 0.3:
            continue   # skip ITM puts

        otm_pct = (spot - strike) / spot * 100
        delta   = abs(float(pe.get("delta") or -0.30))
        iv_pct  = float(pe.get("iv") or vix)
        iv_dec  = max(iv_pct / 100.0, 0.05)
        oi      = int(pe.get("oi", 0) or 0)

        # Days to 50% premium decay (mirror of CC logic — for puts the BS form is symmetric in our model)
        days_to_50 = max(_csp_days_to_50pct(spot, strike, price, iv_dec, safe_dte), 1)

        captured        = price * 0.5
        early_yield_pct = (captured / spot) * (365.0 / days_to_50) * 100.0
        hold_yield_pct  = (price / spot) * (365.0 / safe_dte) * 100.0

        hold_frac  = days_to_50 / safe_dte
        prob_touch = min(1.0, 2.0 * delta * (hold_frac ** 0.5))
        prob_safe  = max(0.05, 1.0 - prob_touch)

        liq_factor = 0.6 + 0.4 * min(oi / 20000.0, 1.0)

        # Comfort: how much capital is needed if assigned, vs available capital.
        # Indian Nifty options are cash-settled, so "assignment" is a debit equal
        # to (strike - spot_at_expiry) × lot_size — but the cost-basis framing is
        # still useful for the trader thinking "would I want to own NB at K?"
        nb_basis_at_assign = strike / NIFTYBEES_RATIO
        comfort = 1.0
        if otm_pct < 1.5:
            comfort = 0.7   # strike too close to spot
        elif otm_pct > 8:
            comfort = 0.85  # too far; premium thin

        score = early_yield_pct * prob_safe * liq_factor * comfort

        why_good, why_caution = [], []
        if days_to_50 <= max(safe_dte // 3, 1):
            why_good.append(f"Fast theta decay — premium reaches 50% in ~{days_to_50}d.")
        elif days_to_50 <= safe_dte // 2:
            why_good.append(f"Reasonable theta — ~{days_to_50}d to 50% capture.")
        else:
            why_caution.append(f"Slow decay (~{days_to_50}d to 50%) — capital tied up longer.")

        if early_yield_pct >= 25:
            why_good.append(f"Strong income velocity ≈ {early_yield_pct:.1f}% p.a. on early exit.")
        elif early_yield_pct >= 12:
            why_good.append(f"Solid early-exit yield ≈ {early_yield_pct:.1f}% p.a.")
        else:
            why_caution.append(f"Modest yield (~{early_yield_pct:.1f}% p.a.); not worth the assignment risk.")

        if prob_safe >= 0.80:
            why_good.append(f"~{int(prob_safe * 100)}% odds the put expires worthless — clean cycle likely.")
        elif prob_safe >= 0.65:
            why_caution.append(f"~{int((1 - prob_safe) * 100)}% chance Nifty pierces {strike:.0f} during hold.")
        else:
            why_caution.append(f"High touch risk (~{int((1 - prob_safe) * 100)}%) — Nifty likely tests {strike:.0f}.")

        if oi >= 10000: why_good.append(f"Excellent liquidity (OI {oi:,}).")
        elif oi >= 1000: why_good.append(f"Reasonable OI {oi:,}.")
        else:            why_caution.append(f"Thin OI ({oi:,}) — wider spreads.")

        if otm_pct < 1.5:
            why_caution.append(f"Strike only {otm_pct:.1f}% OTM — small dip puts you ITM fast.")

        # Indian cash-settlement note vs assignment-style framing
        why_caution_assign = (
            f"If Nifty closes below {strike:.0f} at expiry, you owe "
            f"₹{(strike-spot)*0.0:.0f} cash-settled debit (Indian options are cash-settled — no NB delivery)."
        ) if False else (
            f"If Nifty closes below {strike:.0f} at expiry, settlement is in cash; "
            f"effective NB cost basis works out to ≈ ₹{nb_basis_at_assign:.2f}/share."
        )

        out.append({
            "strike":          strike,
            "premium":         round(price, 2),
            "bid":             round(float(pe.get("bid") or 0), 2),
            "ask":             round(float(pe.get("ask") or 0), 2),
            "otm_pct":         round(otm_pct, 2),
            "delta":           round(delta, 3),
            "iv":              round(iv_pct, 1),
            "oi":              oi,
            "days_to_50pct":   days_to_50,
            "early_yield_pct": round(early_yield_pct, 1),
            "hold_yield_pct":  round(hold_yield_pct, 1),
            "prob_safe_pct":   round(prob_safe * 100, 0),
            "score":           round(score, 2),
            "nb_basis_at_assign": round(nb_basis_at_assign, 2),
            "assignment_note": why_caution_assign,
            "summary": (
                f"{otm_pct:.1f}% OTM · Δ {delta:.2f} · "
                f"50% capture in ~{days_to_50}d · "
                f"≈{early_yield_pct:.1f}% p.a. · "
                f"{int(prob_safe * 100)}% odds expires worthless"
            ),
            "why_good":     why_good,
            "why_caution":  why_caution,
        })

    out.sort(key=lambda c: c["score"], reverse=True)
    for i, c in enumerate(out):
        c["rank"] = i + 1
    return out


def _csp_days_to_50pct(spot: float, strike: float, premium: float,
                       iv_decimal: float, dte: int) -> int:
    """Days for a short OTM put's value to decay to 50%, spot held flat."""
    if premium <= 0 or dte <= 0:
        return 0
    target = premium * 0.5
    for d in range(1, dte + 1):
        days_left = max(dte - d, 0)
        val = _bs_put(spot, strike, days_left / 365.0, iv_decimal)
        if val <= target:
            return d
    return dte


# ─────────────────────────────────────────────────────────────────────────────
# Hedge ranking (long protective put — Card 3)
# ─────────────────────────────────────────────────────────────────────────────

def _rank_hedge_puts(spot: float, expiry_chains: list[dict]) -> list:
    """Score long OTM puts as crash insurance.

    Goals:
      - Protect 5-10% OTM (sweet spot)
      - 60-120 DTE so we roll at 30 DTE without panic
      - Cheap per protected rupee
      - Decent OI for clean roll execution

    Score = (1 / cost_per_protected_rupee) × time_buffer × liquidity × otm_fit
    """
    out: list = []
    for ec in expiry_chains:
        expiry = ec["expiry"]
        dte    = ec["dte"]
        chain  = ec["chain"]
        if dte <= 30:
            continue   # already in roll-now zone; not a hedge candidate

        for row in chain:
            strike = float(row["strike"])
            pe = row.get("pe", {})
            price = float(pe.get("price", 0) or 0)
            if price <= 0.5:
                continue
            otm_pct = (spot - strike) / spot * 100
            if otm_pct < 3 or otm_pct > 12:
                continue   # hard band — tightened from 14 → 12 (deeper OTM almost never fires)

            iv  = float(pe.get("iv") or 14.0)
            oi  = int(pe.get("oi", 0) or 0)
            delta = abs(float(pe.get("delta") or -0.20))

            # Cost per protected rupee = premium / (strike value at full hedge)
            # Roughly: if Nifty drops to K, hedge protects (S0-K) per unit.
            protected_per_unit = max(spot - strike, 1.0)   # avoid div0
            cost_per_protected = price / protected_per_unit

            # Time buffer: how long until we'd need to roll (DTE - 30)
            time_buffer_days = max(dte - 30, 1)
            time_factor      = min(time_buffer_days / 60.0, 1.5)

            liq_factor = 0.5 + 0.5 * min(oi / 5000.0, 1.0)

            # ── Hit probability factor (probability hedge actually fires) ───
            # |delta| ≈ probability put finishes ITM at expiry.
            # Anchor: |Δ| 0.20 (≈ 7% OTM, 60d) gets full credit. Below that,
            # the score is penalised proportionally.
            hit_prob_pct = abs(delta) * 100.0           # = P(finishes ITM) %
            hit_factor   = min(abs(delta) / 0.20, 1.0)  # Δ 0.20 → 1.0; Δ 0.05 → 0.25

            # ── Sweet-spot bonus (soft band on top of probability factor) ───
            # 5-8% OTM is the practical sweet spot for monthly-cycle hedging.
            # Outside the sweet band, score is dampened but not zeroed.
            if   5 <= otm_pct <= 8:    sweet_spot_bonus = 1.00
            elif 4 <= otm_pct <= 10:   sweet_spot_bonus = 0.85
            else:                      sweet_spot_bonus = 0.65

            # ── DTE-quality factor (3-month is the sweet spot) ───────────────
            # Standard protective-wheel protocol: buy ~90 DTE puts and roll at
            # 30 DTE remaining. Sweet spot is ACTIVELY BOOSTED to dominate the
            # cheaper short-dated alternatives that score high on cost-per-₹.
            # Note: any DTE < 60 is hard-filtered upstream (dte ≥ 60 in /scan).
            if   dte < 60:    dte_quality = 0.20   # safety net (shouldn't trigger after filter)
            elif dte < 75:    dte_quality = 0.55   # 2-2.5 months — penalised
            elif dte <= 100:  dte_quality = 1.40   # ⭐ 75-100 DTE — actively BOOSTED above neutral
            elif dte <= 130:  dte_quality = 1.10   # ~3.5-4 months — still good
            elif dte <= 180:  dte_quality = 0.70   # 4-6 months — vega risk grows
            else:             dte_quality = 0.40   # > 6 months — vega bomb territory

            score = (1.0 / max(cost_per_protected, 0.001)) * time_factor * liq_factor * hit_factor * sweet_spot_bonus * dte_quality

            # Cost amortised per day of protection
            cost_per_day = price / max(dte, 1)

            # ── Math backing ───────────────────────────────────────────────
            # Greeks (compute via BS in case chain didn't return them)
            T_years = max(dte, 1) / 365.0
            iv_dec  = max(iv / 100.0, 0.05)
            grk     = _bs_put_greeks(spot, strike, T_years, iv_dec)

            # Scenario payoffs at expiry — what the hedge pays out for various
            # Nifty drops. Net payoff = max(K - S_T, 0) - premium.
            scenarios = []
            for drop_pct in (-3, -5, -8, -10, -15, -20):
                S_T   = spot * (1 + drop_pct / 100.0)
                gross = max(strike - S_T, 0.0)
                net   = gross - price
                scenarios.append({
                    "drop_pct": drop_pct,
                    "nifty":    round(S_T, 0),
                    "gross_payoff": round(gross, 2),
                    "net_payoff":   round(net, 2),
                    "in_the_money": gross > 0,
                })

            # Breakeven drop = % drop at which the hedge starts paying *net*
            # (i.e. K - S_T = premium → S_T = K - premium → drop = (S0 - (K-prem))/S0)
            breakeven_spot   = strike - price
            breakeven_drop_pct = (spot - breakeven_spot) / spot * 100.0

            # Annualised drag — what holding the hedge costs you per year as
            # a fraction of NB capital, assuming you roll continuously.
            rolls_per_year = 365.0 / max(dte, 1)
            annual_cost    = price * rolls_per_year
            annual_drag_pct = annual_cost / spot * 100.0

            # Hedge "efficiency": average net payoff at -5/-10/-15% drops
            # divided by premium. > 0 means the hedge wins on average across
            # those crash scenarios.
            crash_payoffs = [s["net_payoff"] for s in scenarios if s["drop_pct"] in (-5, -10, -15)]
            avg_crash_payoff = sum(crash_payoffs) / len(crash_payoffs) if crash_payoffs else 0.0
            efficiency = avg_crash_payoff / max(price, 0.01)

            # Implied 1-sigma move over the hedge's life (sanity check on K)
            sigma_1d_pct = iv_dec * _math.sqrt(T_years) * 100.0   # 1σ over T
            in_sigma_band = otm_pct <= sigma_1d_pct * 1.5

            why_good, why_caution = [], []

            # Sweet-spot commentary
            if 5 <= otm_pct <= 8:
                why_good.append(f"Sweet spot — {otm_pct:.1f}% OTM is the practical hedging band for monthly cycles.")
            elif 4 <= otm_pct <= 10:
                why_good.append(f"Acceptable fit — {otm_pct:.1f}% OTM, slightly outside the 5-8% sweet spot.")
            else:
                why_caution.append(f"{otm_pct:.1f}% OTM — outside the 4-10% practical band; only fires on tail events.")

            # Probability-based commentary (NEW)
            if hit_prob_pct >= 18:
                why_good.append(
                    f"Strong hit probability — {hit_prob_pct:.0f}% chance of finishing ITM. "
                    f"Hedge will fire roughly 1 in {round(100/max(hit_prob_pct,1)):.0f} cycles."
                )
            elif hit_prob_pct >= 10:
                why_good.append(
                    f"Reasonable hit rate — {hit_prob_pct:.0f}% chance of finishing ITM "
                    f"(~1 in {round(100/max(hit_prob_pct,1)):.0f} cycles)."
                )
            else:
                why_caution.append(
                    f"Low hit probability — only {hit_prob_pct:.0f}% chance of finishing ITM "
                    f"(~1 in {round(100/max(hit_prob_pct,1)):.0f} cycles). You may pay premium many times before this fires."
                )

            if cost_per_protected < 0.025:
                why_good.append(f"Cheap protection — pay ₹{cost_per_protected:.3f} per ₹1 of insured drop.")
            elif cost_per_protected < 0.04:
                why_good.append(f"Reasonable cost ₹{cost_per_protected:.3f} per ₹1 protected.")
            else:
                why_caution.append(f"Expensive — ₹{cost_per_protected:.3f} per ₹1 protected. Wait for IV cool-down or pick further OTM.")

            if 75 <= dte <= 100:
                why_good.append(
                    f"Optimal 3-month horizon — DTE {dte} sits in the protocol's sweet spot. "
                    f"You'll roll at DTE 30, giving a {time_buffer_days}d cushion."
                )
            elif 60 <= dte < 75:
                why_caution.append(
                    f"DTE {dte} — a bit short; you'll need to roll in {time_buffer_days}d. "
                    f"3-month (75-100 DTE) hedges are the protocol standard."
                )
            elif dte > 100 and dte <= 130:
                why_caution.append(
                    f"DTE {dte} — slightly long; capital ties up further than needed. 3-month is the sweet spot."
                )
            elif dte > 130:
                why_caution.append(
                    f"DTE {dte} — far hedge. Vega risk grows; you'd benefit more from a fresh 3-month roll."
                )
            elif dte > 30:
                why_caution.append(
                    f"DTE {dte} — short hedge, will need rolling soon (30 DTE trigger). "
                    f"Prefer 75-100 DTE for the standard 3-month horizon."
                )

            if oi >= 5000: why_good.append(f"Great OI {oi:,} — hedge rolls cleanly.")
            elif oi >= 1000: why_good.append(f"Adequate OI {oi:,}.")
            else: why_caution.append(f"Thin OI ({oi:,}) — risk of slippage on roll.")

            # Math-backing why lines
            if efficiency > 1.5:
                why_good.append(
                    f"Crash-test passes: average net payoff at -5/-10/-15% Nifty "
                    f"= ₹{avg_crash_payoff:.0f} per unit (~{efficiency:.1f}× the ₹{price:.0f} premium)."
                )
            elif efficiency > 0:
                why_good.append(
                    f"Positive crash payoff: average net at -5/-10/-15% drops "
                    f"= ₹{avg_crash_payoff:.0f} ({efficiency:.1f}× premium)."
                )
            else:
                why_caution.append(
                    f"Negative average crash payoff (₹{avg_crash_payoff:.0f}) — strike too low for the cost."
                )

            if annual_drag_pct < 1.5:
                why_good.append(
                    f"Annual drag only {annual_drag_pct:.2f}% of capital "
                    f"(₹{annual_cost:.0f}/year over {rolls_per_year:.1f} rolls)."
                )
            elif annual_drag_pct < 3.0:
                why_caution.append(
                    f"Annual drag {annual_drag_pct:.2f}% — meaningful bite into yield."
                )
            else:
                why_caution.append(
                    f"Heavy drag {annual_drag_pct:.2f}% / year — premium income may not cover hedge."
                )

            out.append({
                "expiry":        expiry,
                "dte":           dte,
                "strike":        strike,
                "premium":       round(price, 2),
                "bid":           round(float(pe.get("bid") or 0), 2),
                "ask":           round(float(pe.get("ask") or 0), 2),
                "otm_pct":       round(otm_pct, 2),
                "delta":         round(delta, 3),
                "iv":            round(iv, 1),
                "oi":            oi,
                "cost_per_protected": round(cost_per_protected, 4),
                "cost_per_day":  round(cost_per_day, 2),
                "time_buffer_days": time_buffer_days,
                "score":         round(score, 2),
                "summary": (
                    f"{otm_pct:.1f}% OTM · {dte}d · "
                    f"₹{price:.2f} (₹{cost_per_protected:.3f}/protected ₹) · "
                    f"OI {oi:,}"
                ),
                "why_good":     why_good,
                "why_caution":  why_caution,
                # ── Math fields ─────────────────────────────────────────
                "hit_prob_pct":         round(hit_prob_pct, 1),  # P(finishes ITM)
                "hit_factor":           round(hit_factor, 2),    # score multiplier
                "sweet_spot_bonus":     round(sweet_spot_bonus, 2),
                "dte_quality":          round(dte_quality, 2),   # NEW — 3-month preference
                "math": {
                    "spot":             spot,
                    "T_years":          round(T_years, 4),
                    "iv_decimal":       round(iv_dec, 4),
                    "delta":            round(grk["delta"], 4),
                    "gamma":            round(grk["gamma"], 6),
                    "theta_per_day":    round(grk["theta_per_day"], 3),
                    "vega_per_pct":     round(grk["vega_per_pct"], 3),
                    "breakeven_spot":   round(breakeven_spot, 1),
                    "breakeven_drop_pct": round(breakeven_drop_pct, 2),
                    "annual_cost":      round(annual_cost, 0),
                    "annual_drag_pct":  round(annual_drag_pct, 2),
                    "rolls_per_year":   round(rolls_per_year, 1),
                    "efficiency":       round(efficiency, 2),
                    "avg_crash_payoff": round(avg_crash_payoff, 0),
                    "sigma_1d_pct":     round(sigma_1d_pct, 2),
                    "in_sigma_band":    in_sigma_band,
                    "hit_prob_pct":     round(hit_prob_pct, 1),
                    "hit_factor":       round(hit_factor, 2),
                    "sweet_spot_bonus": round(sweet_spot_bonus, 2),
                    "cycles_per_hit":   round(100.0 / max(hit_prob_pct, 0.5), 0),
                    "scenarios":        scenarios,
                    "formulas": {
                        "price":            "Black-Scholes put: P = K·e^(-rT)·N(-d2) − S·N(-d1)",
                        "cost_per_protected": "premium / (S0 − K)  — ₹ paid per ₹1 of protection at K",
                        "breakeven":        "K − premium  — Nifty level at which gross payoff = premium",
                        "annual_drag":      "(premium / S0) × (365 / DTE) × 100  — % capital eaten/year",
                        "efficiency":       "avg(net payoff at −5%, −10%, −15%) / premium",
                        "hit_factor":       "min(|delta| / 0.20, 1.0)  — penalises low-probability hedges",
                        "sweet_spot":       "5-8% OTM = 1.00× · 4-10% = 0.85× · else 0.65×",
                        "dte_quality":      "75-100 DTE = 1.40× ⭐ (3-month sweet spot, actively boosted) · 60-75 = 0.55× · 100-130 = 1.10× · 130-180 = 0.70× · <60 hard-filtered, >180 = 0.40×",
                        "score":            "1/cost_per_protected × time_buffer × liquidity × hit_factor × sweet_spot × dte_quality",
                    },
                },
            })

    out.sort(key=lambda c: c["score"], reverse=True)
    for i, c in enumerate(out):
        c["rank"] = i + 1
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Roll Watch — what-if monitor (Card 5)
# ─────────────────────────────────────────────────────────────────────────────

def _what_if_alerts(
    csp_best: dict | None,
    cc_best:  dict | None,
    hedge_best: dict | None,
) -> dict:
    """Without actual open positions yet, this returns a 'what-if' monitor:
    if you took the recommended trades right now, here are the triggers we'd
    watch and where they currently sit."""
    alerts: list = []

    if csp_best:
        alerts.append({
            "leg": "CSP",
            "trigger": "Take-profit 50%",
            "current_value": f"₹{csp_best['premium']:.2f} (just opened)",
            "threshold": f"≤ ₹{csp_best['premium'] * 0.5:.2f}",
            "fires": False,
            "why": "Close at 50% premium decay — captures most theta with least gamma risk.",
        })
        alerts.append({
            "leg": "CSP",
            "trigger": "Roll-up at Δ ≥ 0.35",
            "current_value": f"Δ {csp_best['delta']:.2f}",
            "threshold": "≥ 0.35",
            "fires": csp_best["delta"] >= 0.35,
            "why": "Above 0.35, P(ITM) is rising fast — roll while still OTM is cheap.",
        })

    if cc_best:
        alerts.append({
            "leg": "CC",
            "trigger": "Take-profit 50%",
            "current_value": f"₹{cc_best['premium']:.2f} (just opened)",
            "threshold": f"≤ ₹{cc_best['premium'] * 0.5:.2f}",
            "fires": False,
            "why": "Same theta-capture logic as CSP — first 50% of decay is fastest.",
        })
        alerts.append({
            "leg": "CC",
            "trigger": "Roll-up at Δ ≥ 0.35",
            "current_value": f"Δ {cc_best['delta']:.2f}",
            "threshold": "≥ 0.35",
            "fires": cc_best["delta"] >= 0.35,
            "why": "Avoids the gamma trap as call goes ITM.",
        })

    if hedge_best:
        alerts.append({
            "leg": "Hedge",
            "trigger": "Roll at 30 DTE",
            "current_value": f"DTE {hedge_best['dte']}",
            "threshold": "≤ 30",
            "fires": hedge_best["dte"] <= 30,
            "why": "Below 30 DTE, gamma + theta both accelerate — preserve the put's hedging power.",
        })

    any_firing = any(a["fires"] for a in alerts)
    summary = "🔴 Action required — see firing alerts." if any_firing else (
        "🟢 All clear — no triggers active. Recommendations stable."
    )
    return {"any_firing": any_firing, "alerts": alerts, "summary": summary}


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

def _pick_default_capital(capital: float | None) -> float:
    if capital and capital > 0:
        return float(capital)
    return 1_800_000.0   # default: ₹18L (1 Nifty lot equivalent)


def _gather_expiries(underlying: str) -> list[str]:
    from api.routes.options import _get_expiries
    return _get_expiries(underlying) or []


def _scan_chain(underlying: str, expiry: str, spot: float) -> dict:
    return _get_chain(underlying, expiry, spot)


@router.get("/scan")
def scan_protected_wheel(
    underlying:        str   = "NIFTY",
    capital:           float = 0,
    csp_expiry:        str   = "",
    cc_expiry:         str   = "",
    has_nb_position:   bool  = False,
    nb_entry:          float = 0,
):
    """Single endpoint that fills all 5 cards.

    Inputs:
      - underlying:       NIFTY (only one supported in this iteration)
      - capital:          ₹ available — drives sizing on every card
      - csp_expiry:       optional override for CSP scan expiry (default: nearest)
      - cc_expiry:        optional override for CC scan expiry (default: nearest)
      - has_nb_position:  if true, CC card runs in "live" mode; else "preview"
      - nb_entry:         entry price for strike-floor on CC (₹/Nifty unit)
    """
    spot     = spot_for(underlying)
    nb_price = _niftybees_price(spot) or round(spot / NIFTYBEES_RATIO, 2)
    capital  = _pick_default_capital(capital)
    vix      = _fetch_vix()
    expiries = _gather_expiries(underlying)
    if not expiries:
        return {"error": "No expiries available for underlying"}

    # Pick default expiries when not provided
    csp_exp = csp_expiry or (expiries[0] if expiries else "")
    cc_exp  = cc_expiry  or (expiries[0] if expiries else "")

    # Pull two chains (CSP and CC may use the same expiry)
    csp_chain = _scan_chain(underlying, csp_exp, spot) if csp_exp else None
    cc_chain  = _scan_chain(underlying, cc_exp,  spot) if cc_exp  else None

    # Hedge: scan expiries 45+ DTE with a WIDE PE-only chain so we have
    # 5-10% OTM strikes (the standard chain tops out at ~2.5% OTM).
    hedge_chains_raw = []
    for exp in expiries[1:6]:
        try:
            cd = _fetch_hedge_chain(underlying, exp, spot)
            if cd and cd["dte"] >= 60 and cd["chain"]:
                hedge_chains_raw.append({"expiry": exp, "dte": cd["dte"], "chain": cd["chain"]})
        except Exception:
            continue
    # Fallback to standard chain if wide fetch returned nothing (e.g. offline)
    if not hedge_chains_raw:
        for exp in expiries[1:6]:
            try:
                cd = _scan_chain(underlying, exp, spot)
                if cd["dte"] >= 60:
                    hedge_chains_raw.append({"expiry": exp, "dte": cd["dte"], "chain": cd["chain"]})
            except Exception:
                continue

    # ATM IV from CSP chain (or CC chain) for richness signal
    atm_iv = 0.0
    if csp_chain:
        atm = csp_chain["atm_strike"]
        atm_row = next((r for r in csp_chain["chain"] if abs(float(r["strike"]) - atm) < 1), None)
        if atm_row:
            ce_iv = float(atm_row["ce"].get("iv") or 0)
            pe_iv = float(atm_row["pe"].get("iv") or 0)
            atm_iv = max(ce_iv, pe_iv)

    closes = _nifty_history_closes(80)

    # ── Card 1: NB Entry ────────────────────────────────────────────────────
    card_nb = _nb_entry_signal(spot, nb_price, vix, atm_iv, closes, capital)

    # ── Card 2: CSP ─────────────────────────────────────────────────────────
    csp_ranked = []
    if csp_chain:
        csp_ranked = _rank_csp_puts(spot, vix, csp_chain["chain"], csp_chain["dte"], capital)
    card_csp = {
        "available":        bool(csp_ranked),
        "selected_expiry":  csp_exp,
        "expiries":         expiries,
        "dte":              csp_chain["dte"] if csp_chain else 0,
        "best":             csp_ranked[0] if csp_ranked else None,
        "alternatives":     csp_ranked[1:5],
    }

    # ── Card 3: Hedge ───────────────────────────────────────────────────────
    hedge_ranked = _rank_hedge_puts(spot, hedge_chains_raw)
    card_hedge = {
        "available":   bool(hedge_ranked),
        "best":        hedge_ranked[0] if hedge_ranked else None,
        "alternatives": hedge_ranked[1:5],
        "current_hedge_status": None,   # hook for future position-tracking integration
    }

    # ── Card 4: CC ─────────────────────────────────────────────────────────
    cc_ranked = []
    if cc_chain:
        cc_ranked = _rank_strikes(spot, vix, cc_chain["chain"], cc_chain["dte"])
        # Apply strike floor: ≥ entry × 1.01 (only if NB held)
        if has_nb_position and nb_entry > 0:
            floor = nb_entry * 1.01
            for c in cc_ranked:
                c["above_floor"] = c["strike"] >= floor
            # Reorder so floor-respecting strikes rise to top
            cc_ranked.sort(key=lambda c: (-int(c.get("above_floor", True)), -c["score"]))
            for i, c in enumerate(cc_ranked):
                c["rank"] = i + 1
        else:
            for c in cc_ranked:
                c["above_floor"] = True
    floor_value = round(nb_entry * 1.01, 2) if (has_nb_position and nb_entry > 0) else None
    card_cc = {
        "available":        bool(cc_ranked),
        "preview_mode":     not has_nb_position,
        "selected_expiry":  cc_exp,
        "expiries":         expiries,
        "dte":              cc_chain["dte"] if cc_chain else 0,
        "best":             cc_ranked[0] if cc_ranked else None,
        "alternatives":     cc_ranked[1:5],
        "strike_floor":     floor_value,
        "strike_floor_basis": (
            f"K ≥ entry × 1.01 = ₹{floor_value:.2f}. Below this, you cap profits below "
            f"breakeven — assignment loss risk." if floor_value else
            "No NB held — preview mode. Floor activates once entry is locked."
        ),
    }

    # ── Card 5: Roll Watch ──────────────────────────────────────────────────
    card_monitor = _what_if_alerts(card_csp.get("best"),
                                   card_cc.get("best"),
                                   card_hedge.get("best"))

    return {
        "timestamp":       _dt.datetime.now().isoformat(timespec="seconds"),
        "underlying":      underlying,
        "spot":            spot,
        "nb_price":        nb_price,
        "vix":             vix,
        "atm_iv":          atm_iv if atm_iv > 0 else None,
        "expiries":        expiries,
        "capital":         capital,
        "cards": {
            "nb":      card_nb,
            "csp":     card_csp,
            "hedge":   card_hedge,
            "cc":      card_cc,
            "monitor": card_monitor,
        },
    }


@router.get("/best-cycle")
def best_cycle(
    underlying: str   = "NIFTY",
    capital:    float = 0,
    scan_expiries: int = 4,
):
    """Cross-leg orchestrator: pick the best end-to-end cycle plan TODAY.

    Returns ONE plan combining:
      - NB entry (now or limit)
      - Best CSP across the next `scan_expiries` chains
      - Best hedge put across all hedge candidates
      - Best CC for the same horizon as the CSP

    Score = csp_score × hedge_quality × cc_score (risk-adjusted product).
    """
    spot     = spot_for(underlying)
    nb_price = _niftybees_price(spot) or round(spot / NIFTYBEES_RATIO, 2)
    capital  = _pick_default_capital(capital)
    vix      = _fetch_vix()
    expiries = _gather_expiries(underlying)

    closes = _nifty_history_closes(80)
    atm_iv = 0.0
    nb_signal = None

    # Cross-expiry CSP scan: best CSP per expiry, pick winner
    best_csp = None
    best_cc  = None
    for exp in expiries[: max(1, scan_expiries)]:
        try:
            cd = _scan_chain(underlying, exp, spot)
        except Exception:
            continue
        if cd["dte"] <= 0:
            continue
        if atm_iv == 0:
            atm = cd["atm_strike"]
            atm_row = next((r for r in cd["chain"] if abs(float(r["strike"]) - atm) < 1), None)
            if atm_row:
                ce_iv = float(atm_row["ce"].get("iv") or 0)
                pe_iv = float(atm_row["pe"].get("iv") or 0)
                atm_iv = max(ce_iv, pe_iv)

        csps = _rank_csp_puts(spot, vix, cd["chain"], cd["dte"], capital)
        if csps:
            top = csps[0].copy()
            top["expiry"] = exp
            top["dte"]    = cd["dte"]
            if best_csp is None or top["score"] > best_csp["score"]:
                best_csp = top

        ccs = _rank_strikes(spot, vix, cd["chain"], cd["dte"])
        if ccs:
            top = ccs[0].copy()
            top["expiry"] = exp
            top["dte"]    = cd["dte"]
            if best_cc is None or top["score"] > best_cc["score"]:
                best_cc = top

    # Hedge scan — wide PE-only chain so we have 5-10% OTM strikes
    hedge_chains_raw = []
    for exp in expiries[1:6]:
        try:
            cd = _fetch_hedge_chain(underlying, exp, spot)
            if cd and cd["dte"] >= 60 and cd["chain"]:
                hedge_chains_raw.append({"expiry": exp, "dte": cd["dte"], "chain": cd["chain"]})
        except Exception:
            continue
    if not hedge_chains_raw:
        for exp in expiries[1:6]:
            try:
                cd = _scan_chain(underlying, exp, spot)
                if cd["dte"] >= 60:
                    hedge_chains_raw.append({"expiry": exp, "dte": cd["dte"], "chain": cd["chain"]})
            except Exception:
                continue
    hedge_ranked = _rank_hedge_puts(spot, hedge_chains_raw)
    best_hedge = hedge_ranked[0] if hedge_ranked else None

    nb_signal = _nb_entry_signal(spot, nb_price, vix, atm_iv, closes, capital)

    # Combined cycle metrics
    csp_prem = (best_csp or {}).get("premium", 0)
    cc_prem  = (best_cc  or {}).get("premium", 0)
    hedge_prem = (best_hedge or {}).get("premium", 0)
    hedge_dte  = (best_hedge or {}).get("dte", 90)
    cc_dte     = (best_cc  or {}).get("dte", 30)
    csp_dte    = (best_csp or {}).get("dte", 30)

    # Per-cycle gross premium (one CSP cycle + one CC cycle approximated to the same DTE)
    monthly_gross = (csp_prem + cc_prem) * (30.0 / max(csp_dte, 1))
    monthly_hedge_cost = hedge_prem * (30.0 / max(hedge_dte, 30))
    monthly_friction   = max(50, capital * 0.0001)   # rough STT/broker estimate
    monthly_net        = monthly_gross - monthly_hedge_cost - monthly_friction
    monthly_pct        = (monthly_net / capital * 100) if capital > 0 else 0

    plan_steps = []
    if nb_signal:
        plan_steps.append({
            "step": 1,
            "leg":  "NB Entry",
            "action": (
                f"BUY {nb_signal['shares']:,} NiftyBees @ ₹{nb_signal['nb_suggested_limit']:.2f} "
                f"(LTP ₹{nb_signal['nb_price']:.2f})"
            ) if nb_signal["shares"] else "Wait for capital deployment",
            "rationale": nb_signal["summary"],
        })
    if best_csp:
        plan_steps.append({
            "step": 2,
            "leg":  "CSP — Put Sell",
            "action": (
                f"SELL {best_csp['strike']:.0f} PE {best_csp['expiry']} @ "
                f"₹{best_csp['premium']:.2f} (Δ {best_csp['delta']:.2f})"
            ),
            "rationale": best_csp["summary"],
        })
    if best_hedge:
        plan_steps.append({
            "step": 3,
            "leg":  "Hedge — Put Buy",
            "action": (
                f"BUY {best_hedge['strike']:.0f} PE {best_hedge['expiry']} @ "
                f"₹{best_hedge['premium']:.2f} ({best_hedge['otm_pct']:.1f}% OTM, {best_hedge['dte']}d)"
            ),
            "rationale": best_hedge["summary"],
        })
    if best_cc:
        plan_steps.append({
            "step": 4,
            "leg":  "CC — Call Sell",
            "action": (
                f"SELL {best_cc['strike']:.0f} CE {best_cc['expiry']} @ "
                f"₹{best_cc['premium']:.2f} (Δ {best_cc['delta']:.2f})"
            ),
            "rationale": best_cc["summary"],
        })

    return {
        "timestamp":  _dt.datetime.now().isoformat(timespec="seconds"),
        "underlying": underlying,
        "spot":       spot,
        "nb_price":   nb_price,
        "vix":        vix,
        "capital":    capital,
        "nb_entry":   nb_signal,
        "best_csp":   best_csp,
        "best_hedge": best_hedge,
        "best_cc":    best_cc,
        "monthly_estimate": {
            "gross_premium":  round(monthly_gross, 0),
            "hedge_cost":     round(monthly_hedge_cost, 0),
            "friction":       round(monthly_friction, 0),
            "net":            round(monthly_net, 0),
            "net_pct":        round(monthly_pct, 2),
        },
        "plan_steps": plan_steps,
        "summary": (
            f"Today's cycle: target net ≈ {monthly_pct:.2f}% / month "
            f"(gross ₹{monthly_gross:,.0f} − hedge ₹{monthly_hedge_cost:,.0f} − fric ₹{monthly_friction:,.0f})"
        ),
    }
