# Planning frameworks & output templates

These are the *methods* the advisor applies. The **numbers come from the engine tools**; this file is how to structure the thinking and the answer.

## 1. Tax-efficient funding waterfall (raise ₹X for a purchase)

Goal: raise the amount with the **lowest total cost** = `tax + penalty + lost future compounding + goal-disruption`. `funding_plan(amount)` returns the ranked mix; you explain and adjust. Default priority order, cheapest first:

1. **Idle cash / savings** (zero cost). NRE cash first for Ram if funding from abroad.
2. **Liquid / arbitrage / overnight funds** (minimal gain, near-cash).
3. **Harvest equity LTCG inside the free headroom** — sell just enough long-held equity so each person's gain stays ≤ their remaining ₹1.25L (zero tax). Spread across the 4 holders. **Call `sellable_assets()`** — it returns, per person, the exact loss-harvest set (book these to offset gains) and the tax-free-LTCG set (specific winners whose gain fits the headroom → 0 tax). **Always recommend the specific holdings it names, never "sell some equity."**
4. **Loan against securities (LAS) / pledge** — no sale, no tax, assets keep compounding; cost = interest (~9–10%). Best when expected portfolio return > loan rate.
5. **Gold loan / loan against LIC / SGB collateral** — cheap, no surrender, no tax event.
6. **Sell long-term equity above the headroom** — 12.5% LTCG. Sell the lowest-conviction / most-concentrated first; realise losses elsewhere to offset.
7. **Home loan (deliberate)** — for a house, a loan is often *optimal*: §24 interest deduction (₹2L/yr self-occupied, uncapped let-out) + §80C principal (₹1.5L) can beat liquidating compounders. Model it, don't dismiss it.
8. **Avoid unless forced:** STCG sales (20%), breaking FDs (penalty + interest reversal), surrendering LIC (loss of cover + tax), NRO sales that trigger 30% TDS and repatriation friction.

For **Ram (NRI)** specifically: prefer NRE cash / LAS / a home loan; if selling, pre-arrange the **§197 lower-TDS certificate** for property and budget for TDS lock-up; consider **§54/54EC** to shelter property LTCG.

## 2. Goal projection (reach ₹Y in N years / "I have ₹X to invest")

Use `goal_projection`. Report:
- **Required CAGR** to hit the target from today's corpus + planned monthly investment, and whether it's realistic (vs 10–12% equity nominal).
- Or, holding return fixed, the **required monthly SIP** or the **achievable date**.
- **Best / base / worst** bands (e.g. 14% / 11% / 8%).
- The dominant lever (more monthly investment vs longer horizon vs higher risk) and the honest gap.

## 3. Income plan (sustainable ₹5L/month)

Use `income_plan`. Report:
- The **corpus required** to throw off ₹5L/mo inflation-adjusted (safe-withdrawal + dividend + rent + coupon mix), and **how much current assets already cover**.
- The **gap** and the years/SIP to close it.
- A **drawdown design**: which buckets fund the monthly income (dividends + rent + coupons first, SWP from equity for the rest), tax-aware and per-person.

## 4. Compare scenarios (Option A vs Option B — e.g. two houses)

Use `compare_scenarios([A, B])`. Lead with a **side-by-side table**:

| | Option A | Option B |
|---|---|---|
| Budget | … | … |
| Funding mix | … | … |
| Tax cost | … | … |
| New monthly EMI / outflow | … | … |
| Effect on ₹5L/mo surplus | … | … |
| Delay to ₹100 Cr goal | … | … |
| Risk / liquidity left | … | … |

Then a one-line recommendation and the reasoning.

## Output templates

**Funding a purchase / raising cash:**
> **Recommendation:** *[one bold line].*
> **Plan:** 1) …  2) …  3) …  (per-person, exact ₹, from tools)
> **Tax & cost table** (source → raised → tax → penalty/interest → net)
> **Impact on goals:** ₹100 Cr date shifts by … · covers …% of the ₹5L/mo need.
> **Risks:** • … • …
> **Assumptions:** … · *Not SEBI-registered advice; verify with a CA.*

**Goal / income question:**
> **Answer:** *[will you make it — yes/no/with-changes].*
> **Numbers:** required CAGR / SIP / date, with best-base-worst bands.
> **What to change:** the biggest lever.
> **Assumptions + disclaimer.**

Always: bold the decision, tables for numbers, ₹ in L/Cr, per-person tax, and the goal impact. Never a wall of prose.
