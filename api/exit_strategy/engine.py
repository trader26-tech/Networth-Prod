"""
Exit Strategy engine — score each holding on "exit conviction" 0-100.

Inputs:
  • A list of portfolio holdings (uploaded by the user — share name, qty,
    avg cost, current value, P/L %, weight in portfolio).
  • The Scorecard master CSV (5000+ stocks with 27 columns from Tickertape).

Output:
  • Each holding tagged with: conviction score, tier (exit/review/watch/keep),
    a list of specific reasons (hard flags + soft flags), and the raw signal
    values used to compute the score — so the user sees *why* something is
    flagged, not just "trust me, sell it."

Design principles:
  • Two-signal rule: a stock isn't tagged EXIT unless ≥ 2 independent
    reasons fire. Single-flag stocks get demoted to REVIEW.
  • Sector exemptions: banks/NBFCs/insurance skip D/E + Current Ratio + ICR
    (their balance sheets work differently).
  • Hard flags carry more weight than soft flags. Pledge > 50% alone is
    enough to flag for review even if everything else is clean.
  • This NEVER auto-sells. It only ranks candidates for human review.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Optional

from api.fundamentals.scorecard import ingest, scorer
from api.fundamentals.scorecard.ingest import to_num


# ─── Sector exemptions ───────────────────────────────────────────────────────
# Financial sub-sectors where balance-sheet ratios are not meaningful red
# flags (leverage is structural to the business).
FINANCIAL_SECTORS = {
    "Bank", "Private Banks", "Public Banks",
    "NBFC", "Consumer Finance", "Home Financing", "Specialized Finance",
    "Diversified Financials",
    "Asset Management", "Asset Management & Custody Banks",
    "Insurance",
    "Investment Banking & Brokerage",
    "Stock Exchanges & Ratings",
}


# ─── Name normalization for portfolio↔universe matching ──────────────────────

_NAME_SUFFIXES = [
    " ltd.", " ltd", " limited",
    " india", " (india)", " india ltd",
    " corporation", " corp.", " corp",
    " company", " co.", " co",
    " inc.", " inc",
    " & co.", " & co",
    " private limited", " pvt.", " pvt",
]
_NORM_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _normalize_name(s: str) -> str:
    """Lowercase, strip common corporate suffixes, drop punctuation/whitespace."""
    s = (s or "").lower().strip()
    # Iterate suffix removal until none match (e.g. "X India Ltd" → "X India" → "X")
    changed = True
    while changed:
        changed = False
        for suf in _NAME_SUFFIXES:
            if s.endswith(suf):
                s = s[:-len(suf)].strip()
                changed = True
                break
    return _NORM_NON_ALNUM.sub("", s)


def build_name_index(universe: list[dict]) -> dict[str, dict]:
    """Map both ticker (lowercased) and normalized-name to row."""
    idx: dict[str, dict] = {}
    for r in universe:
        ticker = (r.get("Ticker") or "").strip().lower()
        if ticker:
            idx[ticker] = r
        name_norm = _normalize_name(r.get("Name") or "")
        if name_norm:
            # Only set if not already mapped — earlier rows win (more specific names)
            idx.setdefault(name_norm, r)
    return idx


def find_match(query: str, index: dict[str, dict]) -> Optional[dict]:
    """Best-effort match: ticker first, then normalized name, then substring."""
    q = (query or "").strip()
    if not q:
        return None
    q_lower = q.lower()
    if q_lower in index:
        return index[q_lower]
    q_norm = _normalize_name(q)
    if q_norm and q_norm in index:
        return index[q_norm]
    # Substring fallback — only when query is reasonably long to avoid noise
    if q_norm and len(q_norm) >= 6:
        for k, v in index.items():
            if len(k) >= 6 and (q_norm in k or k in q_norm):
                return v
    return None


# ─── Per-holding evaluation ──────────────────────────────────────────────────

def _evaluate(holding: dict, row: dict, score: dict) -> dict:
    """Compute exit conviction + reasons for one holding."""
    reasons: list[dict] = []
    conviction = 0
    sub = (row.get("Sub-Sector") or "").strip()
    is_fin = sub in FINANCIAL_SECTORS

    composite = score.get("composite")
    pledge = to_num(row.get("Pledged Promoter Holdings"))
    de     = to_num(row.get("Debt to Equity"))
    cr     = to_num(row.get("Current Ratio"))
    icr    = to_num(row.get("Interest Coverage Ratio"))
    prom   = to_num(row.get("Promoter Holding"))
    fii6   = to_num(row.get("FII Holding Change – 6M"))
    vol    = to_num(row.get("3M Average Volume"))
    mcap   = to_num(row.get("Market Cap"))
    ret1y  = to_num(row.get("1Y Return"))
    away52 = to_num(row.get("% Away From 52W High"))

    # ─── HARD FLAGS — promoter pledging ──────────────────────────────────────
    if pledge is not None:
        if pledge >= 50:
            conviction += 40
            reasons.append({
                "kind": "hard", "severity": "critical", "category": "Pledge",
                "msg": f"Pledged {pledge:.0f}% — urgent exit signal",
            })
        elif pledge >= 25:
            conviction += 25
            reasons.append({
                "kind": "hard", "severity": "high", "category": "Pledge",
                "msg": f"Pledged {pledge:.0f}% > 25% threshold",
            })
        elif pledge >= 10:
            conviction += 8
            reasons.append({
                "kind": "soft", "severity": "low", "category": "Pledge",
                "msg": f"Pledged {pledge:.0f}% — monitor closely",
            })

    # ─── HARD FLAGS — balance sheet (skip for financials) ───────────────────
    if not is_fin:
        if de is not None and de > 3:
            conviction += 20
            reasons.append({
                "kind": "hard", "severity": "high", "category": "Leverage",
                "msg": f"D/E {de:.1f} > 3 — structurally over-leveraged",
            })
        if cr is not None and cr > 0 and cr < 1:
            conviction += 15
            reasons.append({
                "kind": "hard", "severity": "medium", "category": "Liquidity",
                "msg": f"Current Ratio {cr:.2f} < 1 — short-term liquidity stress",
            })
        if icr is not None:
            if icr < 0:
                conviction += 25
                reasons.append({
                    "kind": "hard", "severity": "critical", "category": "Interest Coverage",
                    "msg": "Negative interest coverage — operating losses can't service debt",
                })
            elif icr < 1.5:
                conviction += 20
                reasons.append({
                    "kind": "hard", "severity": "high", "category": "Interest Coverage",
                    "msg": f"Interest Coverage {icr:.1f}x < 1.5 — debt servicing fragile",
                })

    # ─── HARD FLAGS — liquidity ─────────────────────────────────────────────
    if mcap is not None and mcap > 0 and mcap < 100:
        conviction += 15
        reasons.append({
            "kind": "hard", "severity": "medium", "category": "Size",
            "msg": f"Market cap ₹{mcap:.0f}Cr < 100Cr — micro-cap risk",
        })
    if vol is not None and vol > 0 and vol < 5_000:
        conviction += 15
        reasons.append({
            "kind": "hard", "severity": "medium", "category": "Volume",
            "msg": f"3M avg vol {vol:,.0f} — hard to exit cleanly",
        })

    # ─── SOFT FLAGS — scorecard composite ───────────────────────────────────
    if composite is not None:
        if composite < 25:
            conviction += 20
            reasons.append({
                "kind": "soft", "severity": "high", "category": "Scorecard",
                "msg": f"Composite {composite:.0f} — bottom tier of peers",
            })
        elif composite < 40:
            conviction += 10
            reasons.append({
                "kind": "soft", "severity": "medium", "category": "Scorecard",
                "msg": f"Composite {composite:.0f} — below average vs peers",
            })

    # ─── SOFT FLAGS — institutional sentiment ──────────────────────────────
    if fii6 is not None:
        if fii6 <= -2.0:
            conviction += 15
            reasons.append({
                "kind": "soft", "severity": "high", "category": "FII",
                "msg": f"FIIs exited heavily — down {abs(fii6):.1f}% in 6M",
            })
        elif fii6 <= -1.0:
            conviction += 10
            reasons.append({
                "kind": "soft", "severity": "medium", "category": "FII",
                "msg": f"FIIs reducing — down {abs(fii6):.1f}% in 6M",
            })

    # ─── SOFT FLAGS — promoter conviction ──────────────────────────────────
    if prom is not None:
        if prom < 20:
            conviction += 10
            reasons.append({
                "kind": "soft", "severity": "medium", "category": "Promoter",
                "msg": f"Promoter holds only {prom:.0f}% — low founder conviction",
            })
        elif prom < 30:
            conviction += 5
            reasons.append({
                "kind": "soft", "severity": "low", "category": "Promoter",
                "msg": f"Promoter {prom:.0f}% — modest founder stake",
            })

    # ─── SOFT FLAGS — price action context ─────────────────────────────────
    if ret1y is not None and ret1y < -40:
        conviction += 5
        reasons.append({
            "kind": "soft", "severity": "low", "category": "Price",
            "msg": f"Down {abs(ret1y):.0f}% in 1Y — confirm fundamentals haven't recovered",
        })

    # ─── POSITION-MANAGEMENT flags ─────────────────────────────────────────
    weight = holding.get("weight_pct")  # 0..100
    if weight is not None and weight < 0.25:
        conviction += 10
        reasons.append({
            "kind": "position", "severity": "low", "category": "Size",
            "msg": f"Position {weight:.2f}% — noise weight, consolidate or exit",
        })

    pl_pct = holding.get("pl_pct")
    if pl_pct is not None and pl_pct < -50:
        conviction += 5
        reasons.append({
            "kind": "position", "severity": "low", "category": "P&L",
            "msg": f"Down {abs(pl_pct):.0f}% from entry — tax-loss harvesting candidate",
        })

    conviction = min(100, conviction)

    # ─── Tier ──────────────────────────────────────────────────────────────
    if conviction >= 70:
        tier = "exit"
    elif conviction >= 50:
        tier = "review"
    elif conviction >= 30:
        tier = "watch"
    else:
        tier = "keep"

    # Two-signal rule: don't show EXIT unless ≥ 2 hard reasons
    hard_count = sum(1 for r in reasons if r["kind"] == "hard")
    if tier == "exit" and hard_count < 2:
        tier = "review"

    return {
        "conviction": conviction,
        "tier":       tier,
        "reasons":    reasons,
        "hard_flags": hard_count,
        "signals": {
            "composite":      composite,
            "pledge":         pledge,
            "de":             de,
            "current_ratio":  cr,
            "icr":            icr,
            "promoter":       prom,
            "fii_change_6m":  fii6,
            "volume_3m":      vol,
            "mcap":           mcap,
            "ret_1y":         ret1y,
            "away_52w_high":  away52,
            "sub_sector":     sub,
            "is_financial":   is_fin,
        },
    }


# ─── Public API ──────────────────────────────────────────────────────────────

def analyze_holdings(holdings: list[dict]) -> dict:
    """Score every holding. Returns a single dict with ranked rows + tier counts.

    Each input holding should have:
      • share       (str)         — display name from broker
      • qty         (number)
      • avg_cost    (number)      — per-share cost basis (optional)
      • current_value (number)    — current ₹ value (optional)
      • pl_pct      (number)      — % P/L (optional)
      • weight_pct  (number)      — % of portfolio (optional)
    """
    universe = ingest.load_master()
    if not universe:
        return {
            "available":   False,
            "error":       "No scorecard data uploaded yet — upload your Tickertape CSV first.",
            "rows":        [],
            "tier_counts": {"exit": 0, "review": 0, "watch": 0, "keep": 0, "unknown": 0},
        }

    name_index = build_name_index(universe)
    peer_data  = scorer.compute_sector_peers(universe)
    w          = scorer._normalize_weights(None)  # default weights

    rows: list[dict] = []
    for h in holdings:
        share = h.get("share") or h.get("name") or ""
        match = find_match(share, name_index)
        if not match:
            rows.append({
                **h,
                "match_status": "not_found",
                "conviction":   None,
                "tier":         "unknown",
                "reasons":      [{
                    "kind": "info", "severity": "info", "category": "Match",
                    "msg":  "Not found in scorecard — re-upload CSV including this stock for analysis.",
                }],
                "signals":      {},
                "name":         share,
                "ticker":       "",
                "sub_sector":   "",
            })
            continue
        score = scorer.score_stock(match, peer_data, w)
        eval_ = _evaluate(h, match, score)
        rows.append({
            **h,
            "match_status": "matched",
            "name":         match.get("Name", share),
            "ticker":       (match.get("Ticker") or "").upper().strip(),
            "sub_sector":   (match.get("Sub-Sector") or "").strip(),
            "market_cap":   to_num(match.get("Market Cap")),
            "composite":    score.get("composite"),
            "categories":   score.get("categories"),
            **eval_,
        })

    # Rank: exit first, then by conviction desc
    tier_order = {"exit": 0, "review": 1, "watch": 2, "keep": 3, "unknown": 4}
    rows.sort(key=lambda r: (tier_order.get(r["tier"], 5), -(r.get("conviction") or 0)))

    counts: dict[str, int] = defaultdict(int)
    for r in rows:
        counts[r["tier"]] += 1

    return {
        "available":   True,
        "rows":        rows,
        "tier_counts": dict(counts),
        "total":       len(rows),
        "matched":     sum(1 for r in rows if r["match_status"] == "matched"),
    }
