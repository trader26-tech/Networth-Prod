"""
Strategy catalogue — declarative definitions served to the frontend.

To add a new strategy:
  1. Add an entry to STRATEGIES below (set available=False until the scanner is ready)
  2. Create api/scanners/<your_strategy>.py with scan_<name> and scan_<name>_all functions
  3. Register the router in api/routes/scanners.py
  4. Add the frontend card in scanner.html

That's it — no other file needs to change.
"""

STRATEGIES: dict[str, dict] = {
    "bwb": {
        "id": "bwb",
        "name": "Broken Wing Butterfly",
        "category": "income",
        "description": (
            "Asymmetric butterfly that collects premium with directional skew. "
            "K1 < K2 < K3 with K2-K1 ≠ K3-K2."
        ),
        "structure": [
            {"role": "K1", "action": "BUY",  "qty": 1, "type": "CE/PE", "desc": "Long lower-strike option"},
            {"role": "K2", "action": "SELL", "qty": 2, "type": "CE/PE", "desc": "Short middle (body)"},
            {"role": "K3", "action": "BUY",  "qty": 1, "type": "CE/PE", "desc": "Long upper-strike option"},
        ],
        "best_when": "Range-bound markets with directional bias. Net credit when wing-skew works in favour.",
        "available": True,
    },

    "iron_condor": {
        "id": "iron_condor",
        "name": "Iron Condor",
        "category": "income",
        "description": (
            "Sell OTM put spread (K1/K2) + OTM call spread (K3/K4). "
            "Short strikes chosen at 0.15–0.20 delta; equal-width wings; "
            "credit ≥ 1/3 of wing width. "
            "Profit when spot stays between K2 and K3 at expiry. "
            "Bounded max loss = wing_width − net_credit on either side."
        ),
        "structure": [
            {"role": "K1", "action": "BUY",  "qty": 1, "type": "PE", "desc": "Long far-OTM put (left protection)"},
            {"role": "K2", "action": "SELL", "qty": 1, "type": "PE", "desc": "Short OTM put @ ~0.15–0.20 Δ"},
            {"role": "K3", "action": "SELL", "qty": 1, "type": "CE", "desc": "Short OTM call @ ~0.15–0.20 Δ"},
            {"role": "K4", "action": "BUY",  "qty": 1, "type": "CE", "desc": "Long far-OTM call (right protection)"},
        ],
        "best_when": (
            "High IV Rank (>30), range-bound market, 30–45 DTE. "
            "Exit at 50% profit; stop-loss at 2× credit."
        ),
        "available": True,
    },

    "batman": {
        "id": "batman",
        "name": "Batman",
        "category": "income",
        "description": (
            "Double Broken Wing Butterfly = Put BWB (left) + Call BWB (right). "
            "Creates a Batman-shaped payoff with two profit peaks at K2 and K5. "
            "Bounded risk on both extremes."
        ),
        "structure": [
            {"role": "K1", "action": "BUY",  "qty": 1, "type": "PE", "desc": "Long far-OTM put (left tail protection)"},
            {"role": "K2", "action": "SELL", "qty": 2, "type": "PE", "desc": "Short OTM put — left profit peak ★"},
            {"role": "K3", "action": "BUY",  "qty": 1, "type": "PE", "desc": "Long near-ATM put (left inner wing)"},
            {"role": "K4", "action": "BUY",  "qty": 1, "type": "CE", "desc": "Long near-ATM call (right inner wing)"},
            {"role": "K5", "action": "SELL", "qty": 2, "type": "CE", "desc": "Short OTM call — right profit peak ★"},
            {"role": "K6", "action": "BUY",  "qty": 1, "type": "CE", "desc": "Long far-OTM call (right tail protection)"},
        ],
        "best_when": "Range-bound markets with low volatility. Gains when spot expires near K2 or K5.",
        "available": True,
    },

    # ── Coming soon ──────────────────────────────────────────────────────────

    "butterfly": {
        "id": "butterfly",
        "name": "Symmetric Butterfly",
        "category": "income",
        "description": "Equidistant-wing butterfly. K2-K1 == K3-K2. Highest reward at K2.",
        "structure": [
            {"role": "K1", "action": "BUY",  "qty": 1, "type": "CE/PE", "desc": "Long lower wing"},
            {"role": "K2", "action": "SELL", "qty": 2, "type": "CE/PE", "desc": "Short body"},
            {"role": "K3", "action": "BUY",  "qty": 1, "type": "CE/PE", "desc": "Long upper wing"},
        ],
        "best_when": "Strong conviction spot lands at K2 by expiry.",
        "available": False,
    },

    "bull_call_spread": {
        "id": "bull_call_spread",
        "name": "Bull Call Spread",
        "category": "directional",
        "description": "Buy ATM call + sell OTM call. Capped upside, capped downside. Bullish.",
        "structure": [
            {"role": "K1", "action": "BUY",  "qty": 1, "type": "CE", "desc": "Long lower-strike call"},
            {"role": "K2", "action": "SELL", "qty": 1, "type": "CE", "desc": "Short higher-strike call"},
        ],
        "best_when": "Moderately bullish view with limited risk tolerance.",
        "available": False,
    },

    "bear_put_spread": {
        "id": "bear_put_spread",
        "name": "Bear Put Spread",
        "category": "directional",
        "description": "Buy ATM put + sell OTM put. Capped downside, capped upside. Bearish.",
        "structure": [
            {"role": "K1", "action": "SELL", "qty": 1, "type": "PE", "desc": "Short lower-strike put"},
            {"role": "K2", "action": "BUY",  "qty": 1, "type": "PE", "desc": "Long higher-strike put"},
        ],
        "best_when": "Moderately bearish view with limited risk tolerance.",
        "available": False,
    },

    "iron_butterfly": {
        "id": "iron_butterfly",
        "name": "Iron Butterfly",
        "category": "income",
        "description": "Sell ATM straddle, buy OTM wings. Highest credit, tightest profit zone.",
        "structure": [
            {"role": "K1", "action": "BUY",  "qty": 1, "type": "PE", "desc": "Long lower put"},
            {"role": "K2", "action": "SELL", "qty": 1, "type": "PE", "desc": "Short ATM put"},
            {"role": "K2", "action": "SELL", "qty": 1, "type": "CE", "desc": "Short ATM call"},
            {"role": "K3", "action": "BUY",  "qty": 1, "type": "CE", "desc": "Long upper call"},
        ],
        "best_when": "Conviction spot will pin to K2. Earn the premium decay.",
        "available": False,
    },
}
