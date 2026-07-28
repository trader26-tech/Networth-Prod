"""
Scorecard scoring engine.

Six categories rolled up from 16 metrics:

  Performance    → 1Y Return, 5Y CAGR, Sharpe Ratio
  Valuation      → PE Ratio, PB Ratio, EV/EBITDA Ratio
  Growth         → 5Y Hist Rev Growth, 5Y Hist EPS Growth, 1Y Fwd EPS Growth
  Profitability  → ROCE, 5Y Avg ROE, Net Profit Margin
  Entry Point    → % Away From 52W High, RSI – 14D
  Dividend       → Dividend Yield, Payout Ratio

Each metric → 0-100 score based on **peer percentile within sub-sector**
(falls back to absolute thresholds when the sub-sector has < 5 peers).
Categories average their metrics. Composite is the weighted sum of
categories — weights are user-adjustable, defaults below.

Two special-case metrics use a "sweet spot" function instead of monotone
percentile, because the extremes are both bad:

  • RSI – 14D     — too low = oversold/risky, too high = overbought
  • Payout Ratio  — too low = no income, > 70% = unsustainable
"""
from __future__ import annotations

import bisect
from collections import defaultdict
from typing import Iterable, Optional

from .ingest import METRIC_COLS, to_num

# ─── Category structure ──────────────────────────────────────────────────────

CATEGORIES: dict[str, list[str]] = {
    "performance":   ["1Y Return", "5Y CAGR", "Sharpe Ratio"],
    "valuation":     ["PE Ratio", "PB Ratio", "EV/EBITDA Ratio"],
    "growth":        ["5Y Historical Revenue Growth", "5Y Historical EPS Growth", "1Y Forward EPS Growth"],
    "profitability": ["ROCE", "5Y Avg Return on Equity", "Net Profit Margin"],
    "entry_point":   ["% Away From 52W High", "RSI – 14D"],
    "dividend":      ["Dividend Yield", "Payout Ratio"],
}

CATEGORY_LABELS = {
    "performance":   "Performance",
    "valuation":     "Valuation",
    "growth":        "Growth",
    "profitability": "Profitability",
    "entry_point":   "Entry Point",
    "dividend":      "Dividend",
}

# Quality-tilted defaults — optimized for long-term Indian compounding.
DEFAULT_WEIGHTS: dict[str, float] = {
    "profitability": 25.0,
    "growth":        20.0,
    "valuation":     20.0,
    "performance":   15.0,
    "entry_point":   10.0,
    "dividend":      10.0,
}

# Named preset modes
WEIGHT_PRESETS: dict[str, dict[str, float]] = {
    "quality": DEFAULT_WEIGHTS,
    "value":  {"profitability": 20, "growth": 15, "valuation": 30,
               "performance": 15, "entry_point": 10, "dividend": 10},
    "growth": {"profitability": 20, "growth": 30, "valuation": 10,
               "performance": 25, "entry_point": 10, "dividend":  5},
    "income": {"profitability": 25, "growth": 10, "valuation": 15,
               "performance": 15, "entry_point":  5, "dividend": 30},
}


# ─── Direction of each metric ─────────────────────────────────────────────────
# Higher = better
HIGHER_BETTER = {
    "1Y Return", "5Y CAGR", "Sharpe Ratio",
    "5Y Historical Revenue Growth", "5Y Historical EPS Growth", "1Y Forward EPS Growth",
    "ROCE", "5Y Avg Return on Equity", "Net Profit Margin",
    "% Away From 52W High",          # bigger gap from peak = healthier pullback entry
    "Dividend Yield",
}
# Lower = better
LOWER_BETTER = {"PE Ratio", "PB Ratio", "EV/EBITDA Ratio"}
# Sweet-spot — score peaks at `optimal`, falls off linearly
SWEET_SPOT: dict[str, tuple[float, float]] = {
    "RSI – 14D":    (40.0, 50.0),    # peak at 40; spread 50 means RSI 90 / 0 → score 0
    "Payout Ratio": (40.0, 60.0),    # peak at 40%; range 0–100% maps to scores
}

# Raised from 5 → 12: comparing only 5 stocks against each other inflates the
# winners (the "best of 5" is not necessarily good in absolute terms). Below
# this threshold we fall back to absolute thresholds instead of percentile.
MIN_PEERS_FOR_PERCENTILE = 12

# Floor on the per-metric peer-fill-rate used as weight inside a category.
# A metric filled for only 5 of 140 peers (fill_rate ≈ 0.04) is unreliable, so
# we down-weight it — but the floor keeps it from disappearing entirely when
# it's the only data we have on a stock.
FILL_RATE_FLOOR = 0.10

# Absolute thresholds used as a fallback when the sub-sector is too small.
# Format: (poor, fair, good, great) → maps to 25/50/75/100 score. Inverted for LOWER_BETTER.
ABS_THRESHOLDS: dict[str, tuple[float, float, float, float]] = {
    "1Y Return":                     (-5,   8,   20,  40),
    "5Y CAGR":                       ( 5,  12,   18,  28),
    "Sharpe Ratio":                  (0.3, 0.7,  1.0, 1.5),
    "PE Ratio":                      (60,  35,   20,  12),    # lower better, so reversed
    "PB Ratio":                      ( 8,   5,    3,   1.5),
    "EV/EBITDA Ratio":               (30,  20,   12,   7),
    "5Y Historical Revenue Growth":  ( 3,   8,   14,  22),
    "5Y Historical EPS Growth":      ( 5,  10,   15,  25),
    "1Y Forward EPS Growth":         ( 0,   8,   15,  25),
    "ROCE":                          ( 8,  15,   22,  35),
    "5Y Avg Return on Equity":       ( 8,  14,   20,  28),
    "Net Profit Margin":             ( 3,   8,   14,  22),
    "% Away From 52W High":          ( 2,   8,   18,  30),
    "Dividend Yield":                ( 0.5, 1.5,  3,   5),
}


def _abs_score(metric: str, value: float) -> float:
    """Fallback absolute-threshold scoring when sub-sector is too small."""
    t = ABS_THRESHOLDS.get(metric)
    if not t:
        return 50.0
    poor, fair, good, great = t
    if metric in LOWER_BETTER:
        # Smaller value = better. Thresholds are listed worst→best so already inverted.
        if value <= great: return 100.0
        if value <= good:  return 75.0
        if value <= fair:  return 50.0
        if value <= poor:  return 25.0
        return 10.0
    else:
        if value >= great: return 100.0
        if value >= good:  return 75.0
        if value >= fair:  return 50.0
        if value >= poor:  return 25.0
        return 10.0


def _sweet_spot_score(value: float, optimal: float, spread: float) -> float:
    """Linear falloff from optimal. 0 if distance ≥ spread."""
    distance = abs(value - optimal)
    return round(max(0.0, 100.0 * (1 - distance / spread)), 1)


def _percentile(sorted_vals: list[float], value: float) -> float:
    """0–100 percentile (low side). N=10, value at index 7 → 70th pct."""
    if not sorted_vals:
        return 50.0
    idx = bisect.bisect_left(sorted_vals, value)
    return round(idx / len(sorted_vals) * 100.0, 1)


def _score_metric_value(metric: str, value: float, peer_vals: list[float]) -> float:
    """One metric → 0-100 score."""
    # Special cases first
    if metric in SWEET_SPOT:
        optimal, spread = SWEET_SPOT[metric]
        return _sweet_spot_score(value, optimal, spread)

    # Peer-relative when we have enough peers
    if len(peer_vals) >= MIN_PEERS_FOR_PERCENTILE:
        pct = _percentile(peer_vals, value)
        return pct if metric in HIGHER_BETTER else (100.0 - pct)

    # Fallback: absolute thresholds
    return _abs_score(metric, value)


# ─── Public API ──────────────────────────────────────────────────────────────

def compute_sector_peers(rows: list[dict]) -> tuple[dict[tuple[str, str], list[float]], dict[str, int]]:
    """Build (peer_vals, sector_sizes).

    • peer_vals    — {(sub_sector, metric): sorted_values} — only non-null values
    • sector_sizes — {sub_sector: total_stocks_in_sector} — used to compute
      the per-metric fill rate, which down-weights sparse columns.
    """
    groups: defaultdict = defaultdict(lambda: defaultdict(list))
    sector_size: dict[str, int] = defaultdict(int)
    for r in rows:
        sub = (r.get("Sub-Sector") or "").strip()
        if not sub:
            continue
        sector_size[sub] += 1
        for m in METRIC_COLS:
            v = to_num(r.get(m))
            if v is not None:
                groups[sub][m].append(v)
    out: dict[tuple[str, str], list[float]] = {}
    for sub, metrics in groups.items():
        for m, vals in metrics.items():
            out[(sub, m)] = sorted(vals)
    return out, dict(sector_size)


def _normalize_weights(weights: Optional[dict[str, float]]) -> dict[str, float]:
    """Ensure weights sum to 100 across the 6 categories (proportionally rescale)."""
    if not weights:
        weights = DEFAULT_WEIGHTS
    w = {k: max(0.0, float(weights.get(k, 0))) for k in CATEGORIES}
    total = sum(w.values())
    if total <= 0:
        return dict(DEFAULT_WEIGHTS)
    return {k: round(v / total * 100.0, 2) for k, v in w.items()}


def score_stock(row: dict, peer_data: tuple[dict, dict], weights: dict) -> dict:
    """Score one stock against its sub-sector peers. Returns the rich detail
    object the UI uses for drill-down.

    Three confidence adjustments applied:
      1. peer_count < MIN_PEERS_FOR_PERCENTILE → fall back to absolute thresholds
      2. metric fill_rate weights the metric inside its category (sparse columns
         in a sub-sector contribute proportionally less)
      3. category score is discounted by √(tracked/total) so partial-data
         categories rank below full-data ones with equal raw scores.
    """
    peer_vals, sector_sizes = peer_data
    sub = (row.get("Sub-Sector") or "").strip()
    sector_n = max(1, sector_sizes.get(sub, 0))

    metric_scores: dict[str, dict] = {}
    for metric in METRIC_COLS:
        v = to_num(row.get(metric))
        peers = peer_vals.get((sub, metric), [])
        peer_count = len(peers)
        median = peers[peer_count // 2] if peer_count else None
        fill_rate = round(peer_count / sector_n, 3) if sector_n else 0.0
        used_percentile = peer_count >= MIN_PEERS_FOR_PERCENTILE
        if v is None:
            metric_scores[metric] = {
                "value": None, "score": None, "peer_count": peer_count,
                "sector_n": sector_n, "fill_rate": fill_rate,
                "median": median, "trackable": False,
                "scoring_mode": "percentile" if used_percentile else "absolute",
            }
            continue
        score = _score_metric_value(metric, v, peers)
        metric_scores[metric] = {
            "value": v, "score": round(score, 1),
            "peer_count": peer_count, "sector_n": sector_n, "fill_rate": fill_rate,
            "median": median, "trackable": True,
            "scoring_mode": "percentile" if used_percentile else "absolute",
        }

    category_scores: dict[str, dict] = {}
    for cat, metrics in CATEGORIES.items():
        weighted_sum = 0.0
        weight_sum   = 0.0
        n_tracked    = 0
        for m in metrics:
            ms = metric_scores[m]
            if ms["score"] is None:
                continue
            n_tracked += 1
            # Per-metric weight: higher peer-fill-rate = more reliable column.
            w = max(FILL_RATE_FLOOR, ms["fill_rate"])
            weighted_sum += ms["score"] * w
            weight_sum   += w
        if weight_sum > 0:
            raw = weighted_sum / weight_sum
            # Coverage discount: √(tracked/total). A category scored on 1-of-3
            # metrics gets ×0.58; 2-of-3 → ×0.82; 3-of-3 → ×1.00.
            coverage = n_tracked / len(metrics)
            adjusted = raw * (coverage ** 0.5)
            category_scores[cat] = {
                "score":     round(adjusted, 1),
                "raw_score": round(raw,      1),
                "tracked":   n_tracked,
                "total":     len(metrics),
                "coverage":  round(coverage, 2),
            }
        else:
            category_scores[cat] = {
                "score": None, "raw_score": None,
                "tracked": 0, "total": len(metrics), "coverage": 0.0,
            }

    # Weighted composite — re-normalize over categories that scored
    tracked_cats = [c for c, v in category_scores.items() if v["score"] is not None]
    active_w_sum = sum(weights[c] for c in tracked_cats)
    if tracked_cats and active_w_sum > 0:
        composite = sum(
            (category_scores[c]["score"] or 0) * (weights[c] / active_w_sum)
            for c in tracked_cats
        )
        composite = round(composite, 1)
    else:
        composite = None

    # Cap tier classification (large/mid/small) from market cap (₹ Cr)
    mcap = to_num(row.get("Market Cap"))
    if mcap is None:           tier = "unknown"
    elif mcap >= 20_000:       tier = "large"
    elif mcap >=  5_000:       tier = "mid"
    else:                      tier = "small"

    return {
        "name":            row.get("Name", ""),
        "ticker":          (row.get("Ticker") or "").strip().upper(),
        "sub_sector":      sub,
        "market_cap":      mcap,
        "cap_tier":        tier,
        "composite":       composite,
        "categories":      category_scores,
        "metrics":         metric_scores,
        "tracked_categories": len(tracked_cats),
    }


def score_universe(rows: list[dict], weights: Optional[dict] = None) -> list[dict]:
    """Score every row + sort by composite descending."""
    if not rows:
        return []
    w = _normalize_weights(weights)
    peers = compute_sector_peers(rows)
    scored = [score_stock(r, peers, w) for r in rows]
    scored.sort(
        key=lambda s: (s["composite"] is None, -(s["composite"] or 0)),
    )
    return scored


def summary(rows: list[dict]) -> dict:
    """Header KPIs for the dashboard."""
    if not rows:
        return {"available": False, "total": 0}
    sub_sectors: dict[str, int] = defaultdict(int)
    for r in rows:
        ss = (r.get("Sub-Sector") or "Other").strip()
        sub_sectors[ss] += 1
    sectors = sorted(sub_sectors.items(), key=lambda x: -x[1])
    return {
        "available":         True,
        "total":             len(rows),
        "distinct_sectors":  len(sub_sectors),
        "sectors":           [{"sub_sector": s, "count": c} for s, c in sectors],
    }
