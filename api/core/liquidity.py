"""
Composite liquidity scoring for option legs.
"""
import math


def liquidity_score(legs: list[dict]) -> dict:
    """
    Composite liquidity score 0-100 across all legs.
    Uses OI (always meaningful), volume (when market open), bid-ask spread.

    Returns: {score, tier, min_oi, min_volume, max_spread_pct}
    """
    min_oi  = min(l.get("oi", 0)     for l in legs)
    min_vol = min(l.get("volume", 0) for l in legs)

    spreads = []
    for l in legs:
        bid = l.get("bid", 0) or 0
        ask = l.get("ask", 0) or 0
        mid = (bid + ask) / 2 if (bid > 0 and ask > 0) else l.get("price", 0)
        if mid > 0 and ask > bid > 0:
            spreads.append((ask - bid) / mid * 100)
        else:
            spreads.append(100.0)
    max_spread = max(spreads) if spreads else 100.0

    # OI: log scale, capped at 60 pts
    oi_pts     = min(60.0, max(0.0, math.log10(max(min_oi, 1)) * 15))
    # Volume: 0 when market is closed — only adds bonus, never penalises
    vol_pts    = min(20.0, math.log10(max(min_vol, 1)) * 5) if min_vol > 0 else 0
    # Spread: tight=20pts, >5%=0pts
    spread_pts = max(0.0, 20 - max_spread * 4)

    score = max(0, min(100, round(oi_pts + vol_pts + spread_pts)))

    if   score >= 70: tier = "excellent"
    elif score >= 45: tier = "good"
    elif score >= 25: tier = "fair"
    else:             tier = "poor"

    return {
        "score":          score,
        "tier":           tier,
        "min_oi":         min_oi,
        "min_volume":     min_vol,
        "max_spread_pct": round(max_spread, 2),
    }
