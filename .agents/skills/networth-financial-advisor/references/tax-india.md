# Indian taxation by asset class — resident vs NRI

**Stamp: FY 2025-26 (AY 2026-27). Post-Budget-2024 regime (changes effective 23 Jul 2024).**
Tax law changes every Union Budget. Treat these as the *rules & logic*; the **computed rupee tax must come from the `tax_impact` / `tax_position` engine tools**, whose rates are updatable in code. Where a case is an edge case, say "verify with a CA".

The family: **Ram = NRI** (non-resident). **The other three = Resident individuals.** Residency changes several of the columns below — never apply resident rules to Ram or vice-versa.

---

## Quick per-asset matrix

| Asset | Long-term after | LTCG rate (resident) | STCG rate | NRI differences (Ram) |
|---|---|---|---|---|
| Listed equity / equity MF / equity ETF | 12 months | **12.5%** on gains **> ₹1.25L/yr** (per person) | **20%** | Same rates; **TDS deducted at source** (12.5% LT / 20% ST). NRI gets the ₹1.25L exemption but **cannot** offset gains against unused basic-exemption limit. |
| Debt MF bought **after 1 Apr 2023** | — (no LT benefit) | **Slab rate** always (deemed short-term) | Slab | TDS ~30%+cess at source. |
| Debt MF bought **before 1 Apr 2023** | 24 months | 12.5% **without indexation** | Slab | TDS at source. |
| Physical gold / gold MF / gold ETF | 24 months | **12.5%** (no indexation) | Slab | TDS at source. |
| **Sovereign Gold Bond (SGB)** | see notes | **Redemption at maturity (8 yr) = TAX-FREE.** Secondary-market sale: 12.5% after 12m. Interest 2.5%/yr = slab. | Interest slab | NRIs **can't buy new** SGBs; may hold if bought while resident. Maturity redemption still tax-free. |
| Property / real estate | 24 months | **12.5% (no indexation)** OR **20% with indexation** — choose lower, for property acquired **before 23 Jul 2024**; post-that-date = 12.5% only | Slab | **TDS on the FULL SALE VALUE** — 12.5%+surcharge+cess (LT) or 30% (ST) — deducted by the buyer, **unless a §197 lower-TDS certificate**. Big cashflow trap. §54/54F/54EC exemptions available. |
| Fixed deposit (FD) interest | — | **Slab** (accrual) | — | **NRE FD interest = TAX-FREE** in India. **NRO FD interest = taxable, 30% TDS.** |
| Bonds — coupon | — | **Slab** | — | 30% TDS on NRO. Old tax-free PSU bonds: interest tax-free. |
| Listed bonds — capital gain | 12 months | 12.5% | Slab | TDS at source. |
| Dividends (stocks / MF) | — | **Slab** (10% TDS if >₹5k/yr per company) | — | **20% TDS** for NRIs (or DTAA rate with TRC+10F). |
| **F&O (futures & options)** | — | **Business income at SLAB** (non-speculative). Not capital gains. Expenses deductible; losses carry forward 8 yrs (timely ITR). | — | Same treatment; audit/return rules apply. Kuwait-DTAA may matter. |
| Intraday equity | — | **Speculative business income** at slab; losses only vs speculative gains (carry 4 yrs) | — | Same. |
| LIC / traditional endowment | — | Maturity **tax-free u/s 10(10D)** if premium ≤10% of sum assured (policies post-Apr-2012). Else taxable. | — | Same 10(10D) test. |
| ULIP | — | If annual premium **> ₹2.5L** (policies after Feb 2021) → **taxed like equity** (12.5%/20%). Else 10(10D) tax-free. | — | Same. |
| PPF / EPF / SSY | — | **Tax-free (EEE).** EPF taxable only on employee contribution interest above ₹2.5L/yr. | — | NRIs can't open new PPF; existing continues to maturity, tax-free. |
| NPS | — | 60% lump-sum tax-free at exit; 40% annuity → slab as pension. | — | Similar. |
| Cash / savings interest | — | Slab; §80TTA ₹10k deduction (residents) | — | NRO taxable; NRE tax-free. |

---

## Rules the engine and advice must honour

**Equity LTCG exemption is per person, per FY = ₹1.25L.** Four residents-worth is NOT ₹5L for one person — it's ₹1.25L *each*. Across the family's harvestable equity, the free headroom is roughly **₹1.25L × (number of holders with capacity)**. Ram (NRI) also gets ₹1.25L on his equity LTCG.

**Set-off & carry-forward order** (use when planning sales):
- STCL can offset STCG or LTCG. LTCL offsets only LTCG.
- Capital losses carry forward **8 years** (timely ITR).
- F&O (business) losses offset any income except salary; carry **8 years**.
- Speculative (intraday) losses: only vs speculative; carry **4 years**.

**Surcharge & cess:** add health-&-education cess **4%**. Surcharge on capital gains is **capped at 15%** for equity LTCG/STCG; other income surcharge slabs (10/15/25/37%, with 25% max under the new regime) apply above ₹50L/₹1Cr/₹2Cr/₹5Cr income. The engine applies these — don't approximate by hand.

**New vs old regime:** slab-rate items (FD/bond/dividend interest, debt, F&O, property STCG) depend on each person's chosen regime. Ask/what the engine holds; the new regime (FY25-26) is: 0–4L nil, 4–8L 5%, 8–12L 10%, 12–16L 15%, 16–20L 20%, 20–24L 25%, >24L 30% (with a rebate making income up to ₹12L effectively tax-free for residents). NRIs get slab but **no 87A rebate** and cannot use the basic-exemption shortfall against equity gains.

---

## NRI (Ram) — the parts that most change the plan

1. **TDS at source on gains, not just interest.** When Ram sells equity/property/MF, tax is withheld immediately — a *cashflow* hit even if the final liability is lower. For property, TDS is on the **whole sale value** unless he obtains a **§197 lower/nil-TDS certificate** in advance. Always plan the certificate ahead of a property sale.
2. **NRE vs NRO.** NRE (foreign-earned, e.g. Kuwait salary remitted) → **interest tax-free, freely repatriable**. NRO (India-sourced) → **taxable, 30% TDS, repatriation ≤ USD 1M/yr with 15CA/15CB**. Prefer funding from NRE where possible; watch repatriation caps when funding an India purchase from abroad.
3. **India–Kuwait DTAA.** Kuwait levies no personal income tax; the DTAA still governs TDS rates and prevents double taxation. Ram can claim the treaty rate (e.g. lower dividend TDS) only with a **TRC + Form 10F**. Kuwait salary is **not taxable in India while he's an NRI** — but would be if his residency flips to resident (watch the 182-day / 120-day rules).
4. **No basic-exemption offset** against equity LTCG/STCG, and **no 87A rebate** — so Ram's small gains are taxed from rupee one (above the ₹1.25L LTCG exemption), unlike the residents.
5. **§54 / §54F / §54EC** property-gain exemptions **are** available to NRIs — reinvest in an Indian residential house, or up to ₹50L in 54EC bonds, to defer/avoid LTCG. Relevant to the house purchase.

---

## Guardrails for the advisor

- Stamp every tax figure with the **FY** and note "rates change each Budget — verify with a CA".
- If asked about a niche case (RSU/ESOP vesting, gift tax, HUF, clubbing, GIFT City, FEMA nuances), give the framework and explicitly recommend a CA — don't state a precise rate you're unsure of.
- Prefer the **`tax_impact` tool's number** over any rate written here if they differ.
