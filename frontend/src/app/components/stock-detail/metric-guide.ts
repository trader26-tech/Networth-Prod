/**
 * Metric education catalog — for each ratio shown in the stock-detail modal:
 *   • what it means,
 *   • the acceptable ranges,
 *   • what each range tells you about the company.
 *
 * Keys match Tickertape's export column names exactly.
 */

export type MetricBand = {
  /** Upper bound of this band (inclusive). Use Infinity for the top band. */
  to: number;
  /** Short label shown on the range bar ("Cheap", "Fair", "Expensive"). */
  label: string;
  /** Severity color: green | blue | amber | red. */
  tone: 'green' | 'blue' | 'amber' | 'red' | 'grey';
};

export type MetricSpec = {
  /** Friendly display name (overrides the column name). */
  label?: string;
  /** Axis the metric belongs to (for grouping). */
  axis: 'value' | 'future' | 'past' | 'health' | 'dividend' | 'governance' | 'meta';
  /** Suffix after the value, e.g. "%", "×", "Cr". */
  unit?: string;
  /** Higher numbers are better? Defaults to true. */
  higherBetter?: boolean;
  /** One-paragraph plain-English description. */
  desc: string;
  /** Explicit formula so the user can verify how it's computed. */
  formula?: string;
  /** What good looks like for *Indian* equities. */
  bands: MetricBand[];
  /** What to read into the current value. Receives the value. */
  interpret?: (v: number) => string;
};

const fmtMoney = (v: number) => `₹${v.toLocaleString('en-IN', { maximumFractionDigits: 0 })} Cr`;

export const METRIC_GUIDE: Record<string, MetricSpec> = {
  /* ────────────────────────────── VALUE ───────────────────────────────── */
  "PE Ratio": {
    axis: "value",
    higherBetter: false,
    desc: "Price-to-Earnings. How many years of current earnings would pay back the share price. " +
          "Cheaper isn't always better — a low PE can signal a stalling business; a high PE is fine if growth justifies it.",
    formula: "Current Share Price ÷ Earnings Per Share (TTM)",
    bands: [
      { to: 0,   label: "Loss-making", tone: "red" },
      { to: 15,  label: "Cheap",       tone: "green" },
      { to: 30,  label: "Fair",        tone: "blue" },
      { to: 60,  label: "Expensive",   tone: "amber" },
      { to: Infinity, label: "Priced for perfection", tone: "red" },
    ],
    interpret: (v) =>
      v < 0  ? "Negative — company is currently loss-making."
    : v < 15 ? `PE ${v.toFixed(0)} — looks reasonably priced. Check whether the business is actually growing.`
    : v < 30 ? `PE ${v.toFixed(0)} — typical for a healthy Indian compounder.`
    : v < 60 ? `PE ${v.toFixed(0)} — premium. Growth must materialize or this de-rates.`
    : `PE ${v.toFixed(0)} — priced for perfection. Any disappointment causes a sharp correction.`,
  },

  "PB Ratio": {
    axis: "value",
    higherBetter: false,
    desc: "Price-to-Book. Market value divided by net assets on the balance sheet. " +
          "PB < 1 means the market values the company at less than its accounting net worth — sometimes a bargain, sometimes a warning.",
    formula: "Current Share Price ÷ Book Value Per Share",
    bands: [
      { to: 1,   label: "Deep value",  tone: "green" },
      { to: 3,   label: "Fair",        tone: "blue" },
      { to: 5,   label: "Premium",     tone: "amber" },
      { to: Infinity, label: "Expensive", tone: "red" },
    ],
    interpret: (v) =>
      v < 1 ? `PB ${v.toFixed(1)} — trading below book. Either undervalued or business is impaired.`
    : v < 3 ? `PB ${v.toFixed(1)} — fairly valued on asset basis.`
    : v < 5 ? `PB ${v.toFixed(1)} — investors paying up for asset light / brand premium.`
    : `PB ${v.toFixed(1)} — pure intangibles / brand premium. High bar to keep justifying.`,
  },

  "EV/EBITDA Ratio": {
    axis: "value",
    higherBetter: false,
    desc: "Enterprise Value divided by EBITDA. Includes debt in the price tag — more honest than PE for leveraged businesses. " +
          "How many years of operating cash flow would an acquirer pay to buy the whole company.",
    formula: "(Market Cap + Total Debt − Cash) ÷ EBITDA",
    bands: [
      { to: 8,   label: "Cheap",       tone: "green" },
      { to: 15,  label: "Fair",        tone: "blue" },
      { to: 25,  label: "Expensive",   tone: "amber" },
      { to: Infinity, label: "Priced for perfection", tone: "red" },
    ],
    interpret: (v) =>
      v < 8  ? `EV/EBITDA ${v.toFixed(1)} — cheap on cash-flow basis. Verify quality is intact.`
    : v < 15 ? `EV/EBITDA ${v.toFixed(1)} — typical for Indian quality businesses.`
    : v < 25 ? `EV/EBITDA ${v.toFixed(1)} — paying up for quality / growth.`
    : `EV/EBITDA ${v.toFixed(1)} — expensive. Growth must compound at very high rates to justify.`,
  },

  "PS Ratio": {
    axis: "value",
    higherBetter: false,
    desc: "Price-to-Sales. Market cap divided by annual revenue. Useful when earnings are volatile or negative. " +
          "Interpretation depends heavily on industry: software 5–15× is normal, retail 0.3–1× is normal.",
    formula: "Market Cap ÷ Annual Revenue (TTM)",
    bands: [
      { to: 1,   label: "Cheap",     tone: "green" },
      { to: 3,   label: "Fair",      tone: "blue" },
      { to: 6,   label: "Expensive", tone: "amber" },
      { to: Infinity, label: "Premium", tone: "red" },
    ],
    interpret: (v) =>
      v < 1 ? `PS ${v.toFixed(1)} — cheap. Common for manufacturing / commodity businesses.`
    : v < 3 ? `PS ${v.toFixed(1)} — fair across most industries.`
    : v < 6 ? `PS ${v.toFixed(1)} — premium, expected for software / consumer brands.`
    : `PS ${v.toFixed(1)} — investors paying for very strong margin expansion ahead.`,
  },

  "Dividend Yield": {
    axis: "dividend",
    unit: "%",
    higherBetter: true,
    desc: "Annual dividend divided by current price. Yields >5% can signal genuine value OR distress — check if it's covered by free cash flow.",
    formula: "Annual Dividend Per Share ÷ Current Share Price × 100",
    bands: [
      { to: 0.5, label: "Low",         tone: "grey" },
      { to: 1.5, label: "Normal",      tone: "blue" },
      { to: 3,   label: "Attractive",  tone: "green" },
      { to: 5,   label: "Generous",    tone: "green" },
      { to: Infinity, label: "Suspiciously high", tone: "amber" },
    ],
    interpret: (v) =>
      v < 0.5 ? "Effectively zero yield — company reinvests everything."
    : v < 1.5 ? "Modest yield — typical for compounders that reinvest most earnings."
    : v < 3   ? `Yield ${v.toFixed(1)}% — attractive while you wait.`
    : v < 5   ? `Yield ${v.toFixed(1)}% — generous. Verify dividend sustainability via Payout Ratio + FCF.`
    : `Yield ${v.toFixed(1)}% — verify this isn't a distressed/falling-price phenomenon.`,
  },

  "Forward PE Ratio": {
    axis: "future",
    higherBetter: false,
    desc: "PE based on next-year forecast earnings. Compare to current PE: Forward PE < PE means earnings are expected to grow.",
    formula: "Current Share Price ÷ Estimated EPS for current FY",
    bands: [
      { to: 12,  label: "Cheap",     tone: "green" },
      { to: 25,  label: "Fair",      tone: "blue" },
      { to: 50,  label: "Expensive", tone: "amber" },
      { to: Infinity, label: "Priced for perfection", tone: "red" },
    ],
  },

  /* ────────────────────────────── FUTURE ──────────────────────────────── */
  "1Y Forward Revenue Growth": {
    axis: "future",
    unit: "%",
    desc: "Estimated revenue growth for the current financial year vs last year, from analyst consensus.",
    formula: "(Estimated Revenue this FY − Last FY Revenue) ÷ Last FY Revenue × 100",
    bands: [
      { to: 0,   label: "Shrinking",   tone: "red" },
      { to: 8,   label: "Slow",        tone: "amber" },
      { to: 15,  label: "Healthy",     tone: "blue" },
      { to: 25,  label: "Strong",      tone: "green" },
      { to: Infinity, label: "Exceptional", tone: "green" },
    ],
  },
  "1Y Forward EPS Growth": {
    axis: "future",
    unit: "%",
    desc: "Estimated earnings-per-share growth for the current year. Operating leverage often makes this higher than revenue growth.",
    formula: "(Estimated EPS this FY − Last FY EPS) ÷ Last FY EPS × 100",
    bands: [
      { to: 0,   label: "Falling",     tone: "red" },
      { to: 10,  label: "Slow",        tone: "amber" },
      { to: 20,  label: "Healthy",     tone: "blue" },
      { to: 35,  label: "Strong",      tone: "green" },
      { to: Infinity, label: "Exceptional", tone: "green" },
    ],
  },

  /* ─────────────────────────────── PAST ───────────────────────────────── */
  "Return on Equity": {
    axis: "past",
    unit: "%",
    desc: "Net profit divided by shareholders' equity. How efficiently the business turns owner capital into profit.",
    formula: "Net Profit ÷ Average Shareholders' Equity × 100",
    bands: [
      { to: 0,   label: "Destroying",  tone: "red" },
      { to: 8,   label: "Weak",        tone: "amber" },
      { to: 15,  label: "Average",     tone: "blue" },
      { to: 25,  label: "Strong",      tone: "green" },
      { to: Infinity, label: "Exceptional", tone: "green" },
    ],
    interpret: (v) =>
      v < 0  ? "Negative ROE — destroying shareholder equity. Major red flag."
    : v < 8  ? `ROE ${v.toFixed(0)}% — capital not earning much. Likely value trap unless turnaround story.`
    : v < 15 ? `ROE ${v.toFixed(0)}% — average for Indian markets.`
    : v < 25 ? `ROE ${v.toFixed(0)}% — quality compounder territory.`
    : `ROE ${v.toFixed(0)}% — exceptional, typical of best-in-class FMCG/IT services.`,
  },

  "5Y Avg Return on Equity": {
    axis: "past",
    unit: "%",
    desc: "Five-year average ROE. Tests consistency — anyone can have one good year; quality businesses sustain >15% over a full cycle.",
    formula: "Average of last 5 years' ROE values",
    bands: [
      { to: 0, label: "Negative", tone: "red" },
      { to: 8, label: "Weak", tone: "amber" },
      { to: 15, label: "Average", tone: "blue" },
      { to: 25, label: "Strong", tone: "green" },
      { to: Infinity, label: "Exceptional", tone: "green" },
    ],
  },

  "ROCE": {
    axis: "past",
    unit: "%",
    desc: "Return on Capital Employed. Operating profit divided by (equity + debt). The most important Indian profitability metric — " +
          "captures returns on ALL capital, not just owner equity. Must exceed cost of capital (~12% in India) to create value.",
    formula: "Earnings Before Interest & Tax ÷ (Total Equity + Total Debt) × 100",
    bands: [
      { to: 0,   label: "Destroying",  tone: "red" },
      { to: 8,   label: "Sub-WACC",    tone: "red" },
      { to: 15,  label: "Average",     tone: "amber" },
      { to: 25,  label: "Strong",      tone: "green" },
      { to: Infinity, label: "Exceptional", tone: "green" },
    ],
    interpret: (v) =>
      v < 0  ? "Negative ROCE — operations not even covering capital costs."
    : v < 8  ? `ROCE ${v.toFixed(0)}% — below India's cost of capital. Destroying value.`
    : v < 15 ? `ROCE ${v.toFixed(0)}% — earning back cost of capital, no more.`
    : v < 25 ? `ROCE ${v.toFixed(0)}% — strong moat / pricing power.`
    : `ROCE ${v.toFixed(0)}% — top decile. Look for what makes the moat durable.`,
  },

  "Net Profit Margin": {
    axis: "past",
    unit: "%",
    desc: "Net profit ÷ revenue. How much of every rupee of sales falls to the bottom line. Industry-dependent: software 20%+ is normal, retail 3% is normal.",
    formula: "Net Profit ÷ Total Revenue × 100",
    bands: [
      { to: 0,   label: "Loss-making", tone: "red" },
      { to: 5,   label: "Thin",        tone: "amber" },
      { to: 12,  label: "Normal",      tone: "blue" },
      { to: 20,  label: "Strong",      tone: "green" },
      { to: Infinity, label: "Exceptional", tone: "green" },
    ],
  },
  "5Y Avg Net Profit Margin": {
    axis: "past",
    unit: "%",
    desc: "Five-year margin average — consistency check. Expanding margins over time = pricing power. Compressing margins = competitive pressure.",
    formula: "Average of last 5 years' Net Profit Margin values",
    bands: [
      { to: 0, label: "Loss-making", tone: "red" },
      { to: 5, label: "Thin", tone: "amber" },
      { to: 12, label: "Normal", tone: "blue" },
      { to: 20, label: "Strong", tone: "green" },
      { to: Infinity, label: "Exceptional", tone: "green" },
    ],
  },
  "EBITDA Margin": {
    axis: "past",
    unit: "%",
    desc: "EBITDA divided by revenue. Operating cash-generation efficiency before financing decisions.",
    formula: "EBITDA ÷ Total Revenue × 100",
    bands: [
      { to: 5, label: "Weak", tone: "red" },
      { to: 12, label: "Thin", tone: "amber" },
      { to: 20, label: "Normal", tone: "blue" },
      { to: 30, label: "Strong", tone: "green" },
      { to: Infinity, label: "Exceptional", tone: "green" },
    ],
  },

  "5Y Historical Revenue Growth": {
    axis: "past",
    unit: "%",
    desc: "5-year compound annual revenue growth rate. Long-term growth track record.",
    formula: "((Revenue Year 5 ÷ Revenue Year 0) ^ (1/5) − 1) × 100  (compound annual)",
    bands: [
      { to: 0,   label: "Shrinking",   tone: "red" },
      { to: 6,   label: "Slow",        tone: "amber" },
      { to: 12,  label: "Healthy",     tone: "blue" },
      { to: 20,  label: "Strong",      tone: "green" },
      { to: Infinity, label: "Exceptional", tone: "green" },
    ],
  },
  "5Y Historical EPS Growth": {
    axis: "past",
    unit: "%",
    desc: "5-year compound EPS growth. Earnings compounding is the engine of long-term returns. >15% sustained = wealth creator.",
    formula: "((EPS Year 5 ÷ EPS Year 0) ^ (1/5) − 1) × 100  (compound annual)",
    bands: [
      { to: 0,   label: "Falling",     tone: "red" },
      { to: 8,   label: "Slow",        tone: "amber" },
      { to: 15,  label: "Healthy",     tone: "blue" },
      { to: 25,  label: "Strong",      tone: "green" },
      { to: Infinity, label: "Exceptional", tone: "green" },
    ],
  },

  /* ─────────────────────────────── HEALTH ─────────────────────────────── */
  "Debt to Equity": {
    axis: "health",
    higherBetter: false,
    desc: "Total debt divided by shareholders' equity. Indian large caps typically run <1; capital-intensive businesses (cement, infra) <1.5; >2 is risky.",
    formula: "Total Debt ÷ Shareholders' Equity",
    bands: [
      { to: 0.3, label: "Conservative", tone: "green" },
      { to: 1,   label: "Normal",       tone: "blue" },
      { to: 2,   label: "Elevated",     tone: "amber" },
      { to: Infinity, label: "Risky",   tone: "red" },
    ],
    interpret: (v) =>
      v < 0.3 ? `D/E ${v.toFixed(2)} — almost debt-free. Resilient.`
    : v < 1   ? `D/E ${v.toFixed(2)} — comfortable leverage.`
    : v < 2   ? `D/E ${v.toFixed(2)} — elevated. Watch interest coverage.`
    : `D/E ${v.toFixed(2)} — over-leveraged. Sensitive to rate hikes / demand slowdown.`,
  },
  "Quick Ratio": {
    axis: "health",
    desc: "Liquid assets (cash + receivables) divided by short-term liabilities. >1 means short-term bills covered by liquid assets.",
    formula: "(Current Assets − Inventory) ÷ Current Liabilities",
    bands: [
      { to: 0.7, label: "Tight",   tone: "red" },
      { to: 1,   label: "Borderline", tone: "amber" },
      { to: 2,   label: "Healthy", tone: "green" },
      { to: Infinity, label: "Cash-rich", tone: "blue" },
    ],
  },
  "Current Ratio": {
    axis: "health",
    desc: "Current assets divided by current liabilities. Includes inventory (unlike quick ratio). >1 means short-term solvent.",
    formula: "Current Assets ÷ Current Liabilities",
    bands: [
      { to: 1,   label: "Risk",    tone: "red" },
      { to: 1.5, label: "Adequate",tone: "amber" },
      { to: 3,   label: "Healthy", tone: "green" },
      { to: Infinity, label: "Conservative", tone: "blue" },
    ],
  },
  "Interest Coverage Ratio": {
    axis: "health",
    unit: "×",
    desc: "EBIT divided by interest expense. How many times over does operating profit cover debt servicing? <2 is dangerous; >5 is healthy.",
    formula: "Earnings Before Interest & Tax (EBIT) ÷ Interest Expense",
    bands: [
      { to: 2,   label: "Distress",  tone: "red" },
      { to: 5,   label: "Tight",     tone: "amber" },
      { to: 10,  label: "Healthy",   tone: "green" },
      { to: Infinity, label: "Very safe", tone: "blue" },
    ],
    interpret: (v) =>
      v < 2  ? `Interest coverage ${v.toFixed(1)}× — earnings barely cover interest. Any slowdown = default risk.`
    : v < 5  ? `Interest coverage ${v.toFixed(1)}× — manageable but elevated.`
    : v < 10 ? `Interest coverage ${v.toFixed(1)}× — comfortable.`
    : `Interest coverage ${v.toFixed(1)}× — very safe debt servicing.`,
  },

  "Operating Cash Flow": {
    axis: "health",
    desc: "Cash generated from core operations in the last fiscal year. Positive = business throws off real cash; negative = burning cash even when reporting profits.",
    formula: "Cash generated from core operations during the last fiscal year (₹ Cr)",
    bands: [
      { to: 0, label: "Burning cash", tone: "red" },
      { to: Infinity, label: "Positive", tone: "green" },
    ],
  },
  "Free Cash Flow": {
    axis: "health",
    desc: "Operating cash flow minus capex. The cash actually available to shareholders after maintaining/growing the business. " +
          "Negative FCF is fine in growth-phase capex cycles; sustained negative = capital trap.",
    formula: "Operating Cash Flow − Capital Expenditure (₹ Cr)",
    bands: [
      { to: 0, label: "Negative", tone: "amber" },
      { to: Infinity, label: "Positive", tone: "green" },
    ],
  },

  /* ─────────────────────────────── DIVIDEND ───────────────────────────── */
  "Payout Ratio": {
    axis: "dividend",
    unit: "%",
    desc: "Dividend per share divided by EPS. <30% = aggressive reinvestment; 30–60% = balanced; >70% = mature, low-growth; >100% = unsustainable.",
    formula: "Dividend Per Share ÷ Earnings Per Share × 100",
    bands: [
      { to: 0,   label: "No payout", tone: "grey" },
      { to: 30,  label: "Reinvesting", tone: "green" },
      { to: 60,  label: "Balanced",  tone: "blue" },
      { to: 80,  label: "High",      tone: "amber" },
      { to: Infinity, label: "Unsustainable", tone: "red" },
    ],
  },

  /* ─────────────────────────── GOVERNANCE (India) ─────────────────────── */
  "Promoter Holding": {
    axis: "governance",
    unit: "%",
    desc: "Percentage of shares owned by the promoter family / founding group. India-specific signal: 40–70% is the sweet spot. " +
          "Below 20% suggests weak control / takeover risk; above 75% means low public float and illiquidity.",
    formula: "Number of shares held by promoters ÷ Total shares × 100",
    bands: [
      { to: 20,  label: "Weak skin",    tone: "red" },
      { to: 40,  label: "Acceptable",   tone: "amber" },
      { to: 75,  label: "Strong skin",  tone: "green" },
      { to: Infinity, label: "Very high", tone: "amber" },
    ],
    interpret: (v) =>
      v < 20 ? `Promoter ${v.toFixed(0)}% — low skin in game. Decisions may not align with shareholders.`
    : v < 40 ? `Promoter ${v.toFixed(0)}% — moderate stake.`
    : v < 75 ? `Promoter ${v.toFixed(0)}% — strong alignment with minority shareholders.`
    : `Promoter ${v.toFixed(0)}% — very high; check public float for liquidity.`,
  },
  "Pledged Promoter Holdings": {
    axis: "governance",
    unit: "%",
    higherBetter: false,
    desc: "Percentage of promoter shares pledged as loan collateral. India's #1 distress predictor. " +
          "0% = clean; >10% = warning; >25% = forced-selling risk if stock falls (Anil Ambani, Future Group pattern).",
    formula: "Promoter shares pledged as collateral ÷ Total promoter shares × 100",
    bands: [
      { to: 0.01, label: "Clean",   tone: "green" },
      { to: 10,   label: "Low",     tone: "blue" },
      { to: 25,   label: "Warning", tone: "amber" },
      { to: Infinity, label: "Distress", tone: "red" },
    ],
    interpret: (v) =>
      v === 0     ? "Zero pledge — promoter not borrowing against shares. Best case."
    : v < 10      ? `Pledge ${v.toFixed(0)}% — low. Monitor for direction.`
    : v < 25      ? `Pledge ${v.toFixed(0)}% — elevated. Falling price triggers margin pressure.`
    : `Pledge ${v.toFixed(0)}% — distress signal. Promoter financially strained.`,
  },
  "Foreign Institutional Holding": {
    axis: "governance",
    unit: "%",
    desc: "Percentage of shares held by FIIs. High FII holding signals institutional confidence but also volatility — FIIs flee on macro shocks.",
    formula: "Shares held by FIIs ÷ Total shares × 100",
    bands: [
      { to: 1,   label: "Minimal",  tone: "grey" },
      { to: 10,  label: "Modest",   tone: "blue" },
      { to: 25,  label: "Significant", tone: "green" },
      { to: Infinity, label: "Heavy", tone: "amber" },
    ],
  },
  "Domestic Institutional Holding": {
    axis: "governance",
    unit: "%",
    desc: "Percentage held by Indian mutual funds + insurance companies. Stickier than FII money. Rising DII holding through a downcycle = bullish.",
    formula: "Shares held by Indian MFs + Insurance + Banks ÷ Total shares × 100",
    bands: [
      { to: 1,   label: "Minimal",  tone: "grey" },
      { to: 10,  label: "Modest",   tone: "blue" },
      { to: 25,  label: "Significant", tone: "green" },
      { to: Infinity, label: "Heavy", tone: "amber" },
    ],
  },

  /* ─────────────────────────────── META ───────────────────────────────── */
  "Market Cap": {
    axis: "meta",
    desc: "Total market value of the company's shares. In Crores (₹1 Cr = ₹10M).",
    formula: "Current Share Price × Total Number of Outstanding Shares",
    bands: [
      { to: 500,    label: "Micro cap", tone: "red" },
      { to: 5_000,  label: "Small cap", tone: "amber" },
      { to: 20_000, label: "Mid cap",   tone: "blue" },
      { to: Infinity, label: "Large cap", tone: "green" },
    ],
    interpret: (v) =>
      v < 500    ? `${fmtMoney(v)} — micro cap. Liquidity risk, lower-quality data.`
    : v < 5_000  ? `${fmtMoney(v)} — small cap. Higher growth potential and higher risk.`
    : v < 20_000 ? `${fmtMoney(v)} — mid cap. Balanced growth + stability.`
    : `${fmtMoney(v)} — large cap. Most stable; lower expected CAGR.`,
  },
};

export function metricBand(spec: MetricSpec, v: number): MetricBand | null {
  if (v == null || isNaN(v)) return null;
  for (const b of spec.bands) {
    if (v <= b.to) return b;
  }
  return spec.bands[spec.bands.length - 1] ?? null;
}
