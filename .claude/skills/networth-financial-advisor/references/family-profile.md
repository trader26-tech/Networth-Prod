# Family profile & standing requirements

This is the **policy + goals** layer. **Live numbers (who holds what, balances, income) always come from the MCP tools** (`financial_snapshot`, `family_profile`, `list_holdings`, `cashflow`) — this file sets the *rules and targets* the advisor plans toward. When the app later exposes a live `family_profile` / `goals` tool, prefer it; keep this doc in sync as the source of truth for residency and goals.

## The people & residency (drives all tax)

- **4 individuals**, money split across **India and Kuwait**.
- **Ram — NON-RESIDENT INDIAN (NRI).** Apply all NRI rules in `tax-india.md`: TDS at source, NRE/NRO, DTAA (India–Kuwait), §197 for property, no basic-exemption offset / 87A. Kuwait salary not taxable in India while NRI.
- **The other three — RESIDENT individuals.** Resident rules: full slab regime with 87A rebate (new regime), ₹1.25L equity-LTCG exemption each, basic-exemption offset allowed.
- Get the **exact names, accounts, and balances from `financial_snapshot` / `family_profile`** — do not assume. Only the residency mapping (Ram = NRI, other 3 = resident) is fixed here.
- **Tax is computed per person.** The family's combined tax-free equity-LTCG harvesting capacity ≈ **₹1.25L × each holder with capacity** (residents + Ram).

## The goals (plan toward these; quantify every recommendation against them)

1. **Wealth target — reach ₹100 Crore net worth** in a target horizon *(horizon = to be confirmed with the user; ask if not stated).* Always answer "what does this move do to the ₹100 Cr date?" using `goal_projection`.
2. **Income target — sustainable ₹5,00,000 / month** (inflation-adjusted) from the portfolio (SWP + dividends + rent + coupons). Always answer "how much of the ₹5L/mo does this cover, and what's the gap?" using `income_plan`.
3. **Near-term — a house purchase** (evaluating options at different budgets). Fund it via the tax-efficient waterfall with **minimum tax and minimum damage to goals 1 & 2**.
4. **Investable capital** — when the user says "I have ₹X to deploy," feed it into `goal_projection` as the lump-sum / SIP and show the achievable path and required CAGR.

## Standing planning preferences

- **After-tax, risk-adjusted, goal-aligned** always beats headline return.
- **Protect compounders.** Prefer pledging/borrowing over selling long-held equity when the after-tax cost of the loan < the expected return being given up.
- **Use every rupee of tax-free headroom** (LTCG harvesting, NRE interest, SGB maturity, 10(10D)) before taking a taxable route.
- **Ram's cashflow timing** matters — NRI TDS locks up cash until the return is filed; factor it in.
- **Diversification & concentration** — flag when a single stock / F&O exposure / one asset class dominates.
- Show **best / base / worst** return bands; never a single deterministic promise.

## When the user asks a planning question, always resolve

- Whose money / which accounts (residency each) → tax path.
- Time horizon and required CAGR vs a sensible assumption (say 10–12% equity nominal, 6–7% debt, 6% inflation — but surface these as editable assumptions).
- The trade-off against **₹100 Cr** and **₹5L/mo**.
- Liquidity + lock-ins + penalties (from `liquidity_profile`).
