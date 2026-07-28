"""
Snowflake scorer — 6-axis fundamental scoring for Indian equities.

Stdlib only. Reads tickertape_universe.csv, computes per-stock scores,
returns a list of dicts ready for UI / JSON export.

Axes (each scored 0-6):
  Value      — current valuation vs peers/history
  Future     — forward growth expectations
  Past       — historical track record + profitability consistency
  Health     — balance sheet strength + cash quality
  Dividend   — yield + sustainability
  Governance — Indian-specific: promoter conduct + fraud signals

Hard exclusions apply before composite scoring (pledged>25, illiquid, etc.).
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent / "data"
UNIVERSE_CSV = DATA_DIR / "tickertape_universe.csv"


# ---------- Numeric parsing ----------------------------------------------------

def num(val: Any) -> float | None:
    """Parse a numeric cell. Returns None for empty/non-numeric."""
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


# ---------- Cap tier ----------------------------------------------------------

def cap_tier(market_cap_cr: float | None) -> str:
    """Tickertape's Market Cap is in Crores."""
    if market_cap_cr is None:
        return "unknown"
    if market_cap_cr >= 20_000:
        return "large"
    if market_cap_cr >= 5_000:
        return "mid"
    return "small"


WEIGHTS = {
    "large":   {"value": 0.20, "future": 0.15, "past": 0.15, "health": 0.20, "dividend": 0.15, "governance": 0.15},
    "mid":     {"value": 0.15, "future": 0.25, "past": 0.20, "health": 0.15, "dividend": 0.05, "governance": 0.20},
    "small":   {"value": 0.10, "future": 0.35, "past": 0.15, "health": 0.15, "dividend": 0.00, "governance": 0.25},
    "unknown": {"value": 0.15, "future": 0.20, "past": 0.20, "health": 0.20, "dividend": 0.05, "governance": 0.20},
}


# ---------- Axis scorers -------------------------------------------------------
# Each scorer returns (score_0_to_6, n_available, n_passed).
# Sub-checks with missing data are skipped, not counted as 0.

def _axis(checks: list[bool | None]) -> tuple[float | None, int, int, bool]:
    """Score one axis. Returns (score, available, passed, trackable).

    An axis is *trackable* only when at least 2 of its sub-checks have real
    data. Below that, the score isn't statistically meaningful — we return
    None and the UI shows "n/a" rather than a misleading 0/6."""
    applicable = [c for c in checks if c is not None]
    n = len(applicable)
    passed = sum(1 for c in applicable if c)
    trackable = n >= 2
    if not trackable:
        return None, n, passed, False
    score = passed / n * 6.0
    return round(score, 2), n, passed, True


# ---------- Peer-relative sub-check helper -----------------------------------
from api.fundamentals.sector_stats import (
    beats_peers, peer_summary, is_exempt, RELATIVE_METRICS,
)


def _peer_or_abs(
    value,
    sub_sector,
    metric,
    stats,
    *,
    abs_check,
    threshold: str = "median",
) -> bool | None:
    """Score a sub-check using peer-relative comparison when sector stats are
    available; fall back to absolute threshold otherwise.

    Returns None if the metric isn't applicable to this sector (exempted) or
    the underlying value is missing — _axis() then drops it from the score.
    """
    if value is None:
        return None
    if is_exempt(sub_sector, metric):
        return None
    rel = beats_peers(value, sub_sector, metric, stats, threshold=threshold)
    if rel is not None:
        return rel
    return abs_check(value)


def score_value(r: dict, sub_sector: str | None, stats: dict) -> tuple[float | None, int, int, bool]:
    pe        = num(r.get("PE Ratio"))
    pb        = num(r.get("PB Ratio"))
    ev_ebitda = num(r.get("EV/EBITDA Ratio"))
    ps        = num(r.get("PS Ratio"))
    div_yield = num(r.get("Dividend Yield"))
    fwd_pe    = num(r.get("Forward PE Ratio"))
    return _axis([
        _peer_or_abs(pe,        sub_sector, "PE Ratio",        stats, abs_check=lambda v: 0 < v < 30),
        _peer_or_abs(pb,        sub_sector, "PB Ratio",        stats, abs_check=lambda v: 0 < v < 5),
        _peer_or_abs(ev_ebitda, sub_sector, "EV/EBITDA Ratio", stats, abs_check=lambda v: 0 < v < 15),
        _peer_or_abs(ps,        sub_sector, "PS Ratio",        stats, abs_check=lambda v: 0 < v < 5),
        _peer_or_abs(div_yield, sub_sector, "Dividend Yield",  stats, abs_check=lambda v: v > 1.0),
        # Forward PE < trailing PE means earnings expected to grow — universal, no peer comparison
        (fwd_pe < pe) if fwd_pe is not None and pe is not None and pe > 0 else None,
    ])


def score_future(r: dict, sub_sector: str | None, stats: dict) -> tuple[float | None, int, int, bool]:
    """Forward growth rates work fine on absolute thresholds — 20% growth is
    20% growth regardless of which sector the company is in."""
    fwd_rev     = num(r.get("1Y Forward Revenue Growth"))
    fwd_eps     = num(r.get("1Y Forward EPS Growth"))
    hist_eps_1y = num(r.get("1Y Historical EPS Growth"))
    return _axis([
        (fwd_rev > 10)     if fwd_rev is not None     else None,
        (fwd_eps > 10)     if fwd_eps is not None     else None,
        (hist_eps_1y > 0)  if hist_eps_1y is not None else None,
    ])


def score_past(r: dict, sub_sector: str | None, stats: dict) -> tuple[float | None, int, int, bool]:
    roe     = num(r.get("Return on Equity"))
    avg_roe = num(r.get("5Y Avg Return on Equity"))
    roce    = num(r.get("ROCE"))
    npm     = num(r.get("Net Profit Margin"))
    avg_npm = num(r.get("5Y Avg Net Profit Margin"))
    rev5y   = num(r.get("5Y Historical Revenue Growth"))
    eps5y   = num(r.get("5Y Historical EPS Growth"))
    return _axis([
        _peer_or_abs(roe,     sub_sector, "Return on Equity",             stats, abs_check=lambda v: v > 15),
        _peer_or_abs(avg_roe, sub_sector, "5Y Avg Return on Equity",      stats, abs_check=lambda v: v > 15),
        _peer_or_abs(roce,    sub_sector, "ROCE",                         stats, abs_check=lambda v: v > 15),
        _peer_or_abs(npm,     sub_sector, "Net Profit Margin",            stats, abs_check=lambda v: v > 10),
        _peer_or_abs(avg_npm, sub_sector, "5Y Avg Net Profit Margin",     stats, abs_check=lambda v: v > 10),
        _peer_or_abs(rev5y,   sub_sector, "5Y Historical Revenue Growth", stats, abs_check=lambda v: v > 10),
        _peer_or_abs(eps5y,   sub_sector, "5Y Historical EPS Growth",     stats, abs_check=lambda v: v > 10),
    ])


def score_health(r: dict, sub_sector: str | None, stats: dict) -> tuple[float | None, int, int, bool]:
    """Health uses peer comparison for D/E + Interest Coverage with sector
    exemptions for banks/NBFCs/insurance (they have D/E by design). OCF and
    FCF are absolute — positive vs negative is unambiguous."""
    de    = num(r.get("Debt to Equity"))
    quick = num(r.get("Quick Ratio"))
    icr   = num(r.get("Interest Coverage Ratio"))
    ocf   = num(r.get("Operating Cash Flow"))
    fcf   = num(r.get("Free Cash Flow"))
    ni    = num(r.get("Net Income"))
    ocf_ni = (ocf / ni) if (ocf is not None and ni is not None and ni > 0) else None
    return _axis([
        _peer_or_abs(de,    sub_sector, "Debt to Equity",          stats, abs_check=lambda v: v < 1.0),
        _peer_or_abs(quick, sub_sector, "Quick Ratio",             stats, abs_check=lambda v: v > 1.0)
            if not is_exempt(sub_sector, "Quick Ratio") else None,
        _peer_or_abs(icr,   sub_sector, "Interest Coverage Ratio", stats, abs_check=lambda v: v > 3.0),
        (ocf > 0)    if ocf is not None    else None,
        (fcf > 0)    if fcf is not None    else None,
        (ocf_ni > 0.8) if ocf_ni is not None else None,
    ])


def score_dividend(r: dict, sub_sector: str | None, stats: dict) -> tuple[float | None, int, int, bool]:
    """Yield is sector-dependent (utilities pay more than tech) — use peers.
    Payout ratio sweet-spot (20-70%) is universal."""
    dy     = num(r.get("Dividend Yield"))
    payout = num(r.get("Payout Ratio"))
    return _axis([
        (dy is not None and dy > 0) if dy is not None else None,
        _peer_or_abs(dy, sub_sector, "Dividend Yield", stats, abs_check=lambda v: v > 1.0),
        (20 <= payout <= 70) if payout is not None and payout > 0 else None,
        (payout > 0)         if payout is not None else None,
    ])


def score_governance(r: dict, sub_sector: str | None, stats: dict) -> tuple[float | None, int, int, bool]:
    """Governance signals are India-universal: 25% pledge is dangerous
    everywhere; 50% promoter holding is healthy everywhere. Keep absolute."""
    prom    = num(r.get("Promoter Holding"))
    pledge  = num(r.get("Pledged Promoter Holdings"))
    prom_chg = num(r.get("Promoter Holding Change – 6M ")) or num(r.get("Promoter Holding Change – 6M"))
    fii    = num(r.get("Foreign Institutional Holding"))
    ocf    = num(r.get("Operating Cash Flow"))
    ni     = num(r.get("Net Income"))
    ocf_ni = (ocf / ni) if (ocf is not None and ni is not None and ni > 0) else None
    return _axis([
        (30 <= prom <= 75) if prom is not None else None,
        (pledge < 10)      if pledge is not None else None,
        (prom_chg >= 0)    if prom_chg is not None else None,
        (fii > 0)          if fii is not None else None,
        (ocf_ni > 0.7)     if ocf_ni is not None else None,
    ])


# ---------- Hard exclusions ---------------------------------------------------

def hard_exclusion(r: dict, mcap: float | None) -> str | None:
    """Return a reason string if this stock should be force-EXIT regardless
    of its composite score.

    These are signals that are unambiguous in Indian markets and rarely
    false-positive on a single data point:

      • Promoter pledge > 25%       — distress; forced selling cascades
      • Debt / Equity > 3 (non-bank)— over-leverage; rate-shock vulnerability
      • Market cap < ₹100 Cr        — illiquidity / delisting risk

    Things we DON'T force-exit on (they appear as warnings instead, fed
    through the Health/Governance axes):

      • OCF / Net Income < 0.3 for a single year. A single low reading is
        often a working-capital cycle (inventory build in commodities,
        receivables ramp in growth phases) — not fraud. The fraud signal
        is sustained < 0.5 over 3+ years, which we can't validate from a
        single-quarter CSV snapshot. The Health axis already penalizes
        weak cash conversion.

      • Negative Free Cash Flow for a single year. Fine in capex-heavy
        growth cycles.
    """
    pledge = num(r.get("Pledged Promoter Holdings"))
    if pledge is not None and pledge > 25:
        return f"Promoter pledge {pledge:.0f}% > 25%"
    de = num(r.get("Debt to Equity"))
    if de is not None and de > 3:
        return f"Debt to Equity {de:.1f} > 3"
    if mcap is not None and mcap < 100:
        return f"Market Cap ₹{mcap:.0f} Cr — too illiquid to exit cleanly"
    return None


# ---------- Composite & bucketing ---------------------------------------------

def _explain(r: dict, mcap: float | None, composite: float, bucket: str, exclusion: str | None) -> dict:
    """Produce strengths / warnings / blockers + plain-English summary so the
    UI can show the user WHY a stock is rated this way."""
    strengths: list[str] = []
    warnings:  list[str] = []
    blockers:  list[str] = []

    # ── Blockers (hard exclusions that forced EXIT) ──
    if exclusion:
        blockers.append(exclusion)

    # ── Governance / fraud red flags ──
    pledge = num(r.get("Pledged Promoter Holdings"))
    if pledge is not None:
        if pledge > 25:
            blockers.append(f"Promoter pledge {pledge:.0f}% — distress signal")
        elif pledge > 10:
            warnings.append(f"Promoter pledge {pledge:.0f}% — watch closely")
        elif pledge == 0:
            strengths.append("Zero promoter pledge")

    prom = num(r.get("Promoter Holding"))
    if prom is not None:
        if prom < 20:
            warnings.append(f"Low promoter holding {prom:.0f}% — weak skin in game")
        elif prom > 75:
            warnings.append(f"Very high promoter holding {prom:.0f}% — low float")
        elif 40 <= prom <= 70:
            strengths.append(f"Promoter holding {prom:.0f}% — strong skin in game")

    prom_chg = num(r.get("Promoter Holding Change – 6M ")) or num(r.get("Promoter Holding Change – 6M"))
    if prom_chg is not None:
        if prom_chg < -1:
            warnings.append(f"Promoter sold {abs(prom_chg):.1f}% in last 6M")
        elif prom_chg > 1:
            strengths.append(f"Promoter bought {prom_chg:.1f}% in last 6M")

    # ── Cash quality (fraud detector) ──
    ocf = num(r.get("Operating Cash Flow"))
    ni  = num(r.get("Net Income"))
    if ocf is not None and ni is not None and ni > 0:
        ratio = ocf / ni
        # NOTE: a single-year low reading can be working-capital noise (commodity
        # inventory build, receivables ramp). We surface it as a warning rather
        # than a hard EXIT — sustained < 0.5 over multiple years is the real
        # fraud signal, which we can't detect from a single CSV snapshot.
        if ratio < 0.3:
            warnings.append(
                f"Operating Cash Flow ÷ Net Income = {ratio:.2f}. "
                f"Profits aren't fully converting to cash this period — "
                f"often a working-capital cycle (inventory or receivables build) "
                f"but worth investigating before adding."
            )
        elif ratio < 0.5:
            warnings.append(
                f"Operating Cash Flow ÷ Net Income = {ratio:.2f}. "
                f"Cash conversion is weaker than ideal — check the cash-flow "
                f"statement for working-capital movements."
            )
        elif ratio < 0.8:
            warnings.append(
                f"Operating Cash Flow ÷ Net Income = {ratio:.2f}. "
                f"Cash quality slightly below the 0.8 benchmark; not concerning yet."
            )
        elif ratio > 1.0:
            strengths.append(
                f"Operating Cash Flow ÷ Net Income = {ratio:.2f} — "
                f"strong cash generation, profits backed by real cash."
            )

    # ── Leverage ──
    de = num(r.get("Debt to Equity"))
    if de is not None:
        if de > 2:
            blockers.append(
                f"Debt to Equity = {de:.2f}. Total debt is more than 2× shareholder "
                f"equity — heavy interest burden, sensitive to rate hikes and "
                f"demand slowdowns. Not safe to hold."
            )
        elif de > 1:
            warnings.append(
                f"Debt to Equity = {de:.2f}. Debt exceeds equity — elevated leverage. "
                f"Check interest coverage to confirm operating profit comfortably "
                f"services this debt."
            )
        elif de < 0.3:
            strengths.append(
                f"Debt to Equity = {de:.2f} — very low leverage. Highly resilient "
                f"to interest-rate and demand shocks."
            )

    icr = num(r.get("Interest Coverage Ratio"))
    if icr is not None and icr < 2:
        warnings.append(f"Interest coverage {icr:.1f}× — debt servicing tight")

    # ── Profitability ──
    roce = num(r.get("ROCE"))
    if roce is not None:
        if roce > 25:
            strengths.append(f"ROCE {roce:.0f}% — exceptional capital efficiency")
        elif roce > 15:
            strengths.append(f"ROCE {roce:.0f}% — strong returns on capital")
        elif roce < 8:
            warnings.append(f"ROCE {roce:.0f}% — low capital efficiency")

    roe = num(r.get("Return on Equity"))
    if roe is not None and roe < 0:
        blockers.append(f"ROE {roe:.0f}% — destroying equity")

    npm = num(r.get("Net Profit Margin"))
    if npm is not None and npm < 0:
        warnings.append(f"Net margin {npm:.1f}% — loss-making")

    # ── Growth ──
    rev5y = num(r.get("5Y Historical Revenue Growth"))
    if rev5y is not None:
        if rev5y > 20:
            strengths.append(f"5Y revenue CAGR {rev5y:.0f}% — strong growth")
        elif rev5y < 0:
            warnings.append(f"5Y revenue CAGR {rev5y:.0f}% — shrinking business")

    eps5y = num(r.get("5Y Historical EPS Growth"))
    if eps5y is not None and eps5y > 20:
        strengths.append(f"5Y EPS CAGR {eps5y:.0f}% — earnings compounding")

    fwd_rev = num(r.get("1Y Forward Revenue Growth"))
    if fwd_rev is not None and fwd_rev > 15:
        strengths.append(f"Forward revenue growth {fwd_rev:.0f}% — momentum intact")

    # ── Valuation ──
    pe = num(r.get("PE Ratio"))
    if pe is not None and pe > 0:
        if pe > 60:
            warnings.append(f"PE {pe:.0f} — priced for perfection")
        elif pe < 12:
            strengths.append(f"PE {pe:.0f} — reasonably valued")

    div = num(r.get("Dividend Yield"))
    if div is not None and div > 3:
        strengths.append(f"Dividend yield {div:.1f}% — pays you to wait")

    # ── Size ──
    if mcap is not None:
        if mcap < 100:
            warnings.append(f"Market cap ₹{mcap:.0f}Cr — illiquid")
        elif mcap > 100_000:
            strengths.append(f"Market cap ₹{mcap/1000:.0f}K Cr — large-cap stability")

    # ── Plain-English summary ──
    if bucket == "EXIT":
        if blockers:
            summary = f"Exit candidate. {blockers[0]}"
            if len(blockers) > 1:
                summary += f" Plus {len(blockers)-1} other red flag(s)."
        else:
            summary = (
                f"Fundamentals too weak to hold (composite {composite:.1f}/6). "
                "Capital better deployed elsewhere."
            )
    elif bucket == "TRIM":
        summary = (
            f"Reduce position. Composite {composite:.1f}/6 — below average for the cap tier. "
            "Either thesis is broken or valuation too rich for the quality."
        )
    elif bucket == "HOLD":
        summary = (
            f"Solid holding. Composite {composite:.1f}/6. Maintain weight; "
            "don't add at current valuation."
        )
    elif bucket == "KEEP_AND_ADD":
        summary = (
            f"High-conviction core. Composite {composite:.1f}/6 — above average across most axes. "
            "Consider increasing on price weakness."
        )
    else:
        summary = f"Composite {composite:.1f}/6."

    return {
        "summary":   summary,
        "strengths": strengths,
        "warnings":  warnings,
        "blockers":  blockers,
    }


def score_stock(r: dict, stats: dict | None = None) -> dict:
    mcap = num(r.get("Market Cap"))
    tier = cap_tier(mcap)
    sub_sector = (r.get("Sub-Sector") or "").strip() or None
    weights = WEIGHTS[tier]
    stats = stats or {}

    v_score, v_n, v_p, v_ok = score_value(r,      sub_sector, stats)
    f_score, f_n, f_p, f_ok = score_future(r,     sub_sector, stats)
    p_score, p_n, p_p, p_ok = score_past(r,       sub_sector, stats)
    h_score, h_n, h_p, h_ok = score_health(r,     sub_sector, stats)
    d_score, d_n, d_p, d_ok = score_dividend(r,   sub_sector, stats)
    g_score, g_n, g_p, g_ok = score_governance(r, sub_sector, stats)

    # ── Peer context for the UI ──────────────────────────────────────────────
    # For each peer-relative metric, attach where this stock sits in its
    # sub-sector. The metric-guide modal renders this as a comparison strip.
    peer_context: dict[str, dict] = {}
    for metric_name in RELATIVE_METRICS:
        val = num(r.get(metric_name))
        if val is None:
            continue
        if is_exempt(sub_sector, metric_name):
            peer_context[metric_name] = {"exempt": True, "value": val}
            continue
        summary = peer_summary(val, sub_sector, metric_name, stats)
        if summary:
            peer_context[metric_name] = summary

    axis_data = {
        "value":      {"score": v_score, "available": v_n, "passed": v_p, "trackable": v_ok},
        "future":     {"score": f_score, "available": f_n, "passed": f_p, "trackable": f_ok},
        "past":       {"score": p_score, "available": p_n, "passed": p_p, "trackable": p_ok},
        "health":     {"score": h_score, "available": h_n, "passed": h_p, "trackable": h_ok},
        "dividend":   {"score": d_score, "available": d_n, "passed": d_p, "trackable": d_ok},
        "governance": {"score": g_score, "available": g_n, "passed": g_p, "trackable": g_ok},
    }

    # Renormalize weights over trackable axes only so untracked ones don't drag the score to 0.
    trackable_axes = [k for k, v in axis_data.items() if v["trackable"]]
    n_trackable = len(trackable_axes)
    active_w = sum(weights[k] for k in trackable_axes)
    if trackable_axes and active_w > 0:
        composite: float | None = round(sum(
            (axis_data[k]["score"] or 0) * (weights[k] / active_w)
            for k in trackable_axes
        ), 2)
    elif trackable_axes:
        # All trackable axes happen to have zero weight for this cap tier — fall back to simple avg.
        composite = round(
            sum((axis_data[k]["score"] or 0) for k in trackable_axes) / len(trackable_axes), 2
        )
    else:
        composite = None

    # Hard exclusions
    exclusion = hard_exclusion(r, mcap)
    if exclusion:
        bucket = "EXIT"
        reason = f"Hard rule: {exclusion}"
    elif n_trackable < 3 or composite is None:
        # Too little data to make a defensible call (e.g. ETFs, suspended stocks)
        bucket = "INSUFFICIENT_DATA"
        reason = f"Only {n_trackable}/6 axes have enough data to score"
    else:
        if composite >= 4.5:
            bucket = "KEEP_AND_ADD"
            reason = f"Strong composite {composite:.2f}/6"
        elif composite >= 3.5:
            bucket = "HOLD"
            reason = f"Solid composite {composite:.2f}/6"
        elif composite >= 2.5:
            bucket = "TRIM"
            reason = f"Weak composite {composite:.2f}/6"
        else:
            bucket = "EXIT"
            reason = f"Poor composite {composite:.2f}/6"

    explanation = _explain(r, mcap, composite or 0.0, bucket, exclusion)

    return {
        "name": r.get("Name", ""),
        "ticker": r.get("Ticker", ""),
        "subsector": r.get("Sub-Sector", ""),
        "market_cap_cr": mcap,
        "cap_tier": tier,
        "close_price": num(r.get("Close Price")),
        "axes": axis_data,
        "n_trackable": n_trackable,
        "composite": composite,
        "bucket": bucket,
        "reason": reason,
        "summary":   explanation["summary"],
        "strengths": explanation["strengths"],
        "warnings":  explanation["warnings"],
        "blockers":  explanation["blockers"],
        "peer_context": peer_context,
        "raw": r,  # full row for detail view
    }


def score_universe(csv_path: Path = UNIVERSE_CSV) -> list[dict]:
    """Score every stock in the universe CSV. Returns [] (no exception) if the
    CSV isn't present — production environments may deploy the code without
    the data file. The endpoints surface this empty state as 503 cleanly.

    Two-pass:
      1. Read all rows and compute per-(sub-sector, metric) sorted-value
         lists. Stats are used in scoring + UI peer-context display.
      2. Score each row using those stats — peer-relative where possible,
         absolute fallback for tiny sub-sectors.
    """
    if not csv_path.exists():
        return []
    from api.fundamentals.sector_stats import compute_sector_stats

    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    stats = compute_sector_stats(rows)
    return [score_stock(r, stats) for r in rows]


if __name__ == "__main__":
    scored = score_universe()
    print(f"Scored {len(scored)} stocks")
    from collections import Counter
    buckets = Counter(s["bucket"] for s in scored)
    for b, n in buckets.most_common():
        print(f"  {b:>14}: {n:>5} ({n / len(scored) * 100:.1f}%)")
