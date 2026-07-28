"""
Peer-relative scoring helpers.

The Snowflake scorer used universal thresholds (e.g. "PE < 30 = cheap") which
breaks across sectors — a PE of 30 is expensive for PSU mining and cheap for
high-growth IT services. This module computes per-(sub-sector, metric)
medians + quartiles so each stock can be scored against its actual peer
group, not a global rule of thumb.

Two outputs:
  • `compute_sector_stats(rows)` → dict keyed by (sub_sector, metric) holding
     the sorted list of peer values. ~5,700 stocks × ~20 metrics ≈ 115K
     floats; in-memory is fine.
  • `peer_summary(value, sub_sector, metric, stats)` → dict with median, q1,
     q3, percentile, peer_count, direction. Used both for scoring decisions
     and for UI display in the metric guide.

Sectors where some metrics are structurally inapplicable (banks have D/E in
the single digits by design) get auto-exempted via `is_exempt()`.
"""
from __future__ import annotations

import bisect
from collections import defaultdict
from typing import Optional


# Metrics where peer-relative scoring works better than absolute thresholds.
# Forward-looking and per-share metrics are excluded — they don't normalize
# usefully across peers.
RELATIVE_METRICS: tuple[str, ...] = (
    # Valuation — direction: lower is better
    "PE Ratio", "PB Ratio", "EV/EBITDA Ratio", "PS Ratio", "Forward PE Ratio",

    # Profitability — direction: higher is better
    "Return on Equity", "5Y Avg Return on Equity", "ROCE",
    "Net Profit Margin", "5Y Avg Net Profit Margin", "EBITDA Margin",

    # Growth — direction: higher is better
    "5Y Historical Revenue Growth", "5Y Historical EPS Growth",
    "1Y Forward Revenue Growth", "1Y Forward EPS Growth",

    # Health — mixed; D/E lower-better, Interest Coverage higher-better
    "Debt to Equity", "Interest Coverage Ratio",

    # Dividend
    "Dividend Yield",
)


# True = "higher is better" (default). False = "lower is better".
HIGHER_BETTER: dict[str, bool] = {
    "PE Ratio":          False,
    "PB Ratio":          False,
    "EV/EBITDA Ratio":   False,
    "PS Ratio":          False,
    "Forward PE Ratio":  False,
    "Debt to Equity":    False,
}


# Sectors where certain metrics are structurally inapplicable. These are
# matched as case-insensitive substrings against the Sub-Sector field, so
# "Private Banks", "Public Sector Banks" all match "Bank".
SECTOR_EXEMPTIONS: dict[str, set[str]] = {
    "Bank": {
        "Debt to Equity", "Quick Ratio", "Current Ratio",
        "Interest Coverage Ratio",
    },
    "NBFC": {
        "Debt to Equity", "Quick Ratio", "Current Ratio",
        "Interest Coverage Ratio",
    },
    "Asset Management": {
        "Debt to Equity",
    },
    "Insurance": {
        "Debt to Equity", "Quick Ratio", "Current Ratio",
    },
    "Stock Broking": {
        "Debt to Equity", "Quick Ratio", "Current Ratio",
    },
    "Specialized Finance": {
        "Debt to Equity", "Quick Ratio", "Current Ratio",
    },
    "Housing Finance": {
        "Debt to Equity", "Quick Ratio", "Current Ratio",
    },
}


def is_exempt(sub_sector: Optional[str], metric: str) -> bool:
    """True if `metric` doesn't apply to `sub_sector` and should be skipped."""
    if not sub_sector:
        return False
    s = sub_sector.lower()
    for tag, exempts in SECTOR_EXEMPTIONS.items():
        if tag.lower() in s and metric in exempts:
            return True
    return False


def _num(val) -> Optional[float]:
    if val is None:
        return None
    s = str(val).strip()
    if not s or s in {"—", "-", "NA", "N/A", "null"}:
        return None
    s = s.replace(",", "").rstrip("%")
    try:
        return float(s)
    except ValueError:
        return None


def compute_sector_stats(rows: list[dict]) -> dict[tuple[str, str], list[float]]:
    """Group rows by Sub-Sector and return sorted values for each metric.

    Sub-sectors with < 5 stocks for a given metric are dropped — too small
    for a meaningful median, scorer falls back to absolute thresholds.
    """
    groups: defaultdict = defaultdict(lambda: defaultdict(list))
    for r in rows:
        sub = (r.get("Sub-Sector") or "").strip()
        if not sub:
            continue
        for m in RELATIVE_METRICS:
            v = _num(r.get(m))
            if v is not None:
                groups[sub][m].append(v)

    out: dict[tuple[str, str], list[float]] = {}
    for sub, metrics in groups.items():
        for m, vals in metrics.items():
            if len(vals) >= 5:                     # need ≥ 5 peers
                out[(sub, m)] = sorted(vals)
    return out


def peer_summary(
    value: Optional[float],
    sub_sector: Optional[str],
    metric: str,
    stats: dict[tuple[str, str], list[float]],
) -> Optional[dict]:
    """Return peer-context info for a single (stock, metric) pair.

    None when:
      • value is missing,
      • sub_sector isn't in the stats (too few peers), or
      • metric isn't in the peer-relative set.
    """
    if value is None or not sub_sector:
        return None
    sorted_vals = stats.get((sub_sector, metric))
    if not sorted_vals:
        return None
    n = len(sorted_vals)
    # Rank gives bottom-up percentile (0 = smallest, 100 = largest)
    rank = bisect.bisect_left(sorted_vals, value)
    pct_low = round(rank / n * 100, 1)
    higher_better = HIGHER_BETTER.get(metric, True)
    # Percentile from the "good" side: for higher-better metrics, a high rank
    # is good; for lower-better, a low rank is good. We expose the value as
    # a 0–100 score where 100 = best in peer group.
    score_pct = pct_low if higher_better else round(100.0 - pct_low, 1)
    return {
        "value":       value,
        "median":      sorted_vals[n // 2],
        "q1":          sorted_vals[n // 4],
        "q3":          sorted_vals[3 * n // 4],
        "min":         sorted_vals[0],
        "max":         sorted_vals[-1],
        "peer_count":  n,
        "percentile":  pct_low,        # 0–100 from low side
        "score_pct":   score_pct,      # 0–100 where 100 = best vs peers
        "direction":   "higher" if higher_better else "lower",
    }


def beats_peers(
    value: Optional[float],
    sub_sector: Optional[str],
    metric: str,
    stats: dict[tuple[str, str], list[float]],
    *,
    threshold: str = "median",        # "median" or "q3"/"q1" for stricter
) -> Optional[bool]:
    """Returns True if `value` is on the favourable side of the peer median
    (or quartile, if set). None if no peer data for this sub-sector/metric."""
    summary = peer_summary(value, sub_sector, metric, stats)
    if summary is None:
        return None
    higher_better = HIGHER_BETTER.get(metric, True)
    if threshold == "median":
        ref = summary["median"]
    elif threshold == "q3":
        ref = summary["q3"]
    elif threshold == "q1":
        ref = summary["q1"]
    else:
        ref = summary["median"]
    return (value >= ref) if higher_better else (value <= ref)
