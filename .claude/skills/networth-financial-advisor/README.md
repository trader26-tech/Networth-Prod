# Networth Financial Advisor — Claude Skill

This folder is a **Claude Agent Skill**: the *brain* of the family financial
advisor. Claude reads it and **executes the reasoning itself** (persona, tax
framework, output style, when to call which tool). It is **not** code we run — the
math lives in the Networth **MCP tools** (server-side, deterministic).

```
networth-financial-advisor/
├── SKILL.md                     # entry point (persona, golden rules, loop, output format)
└── references/
    ├── tax-india.md             # per-asset tax, resident vs NRI (Ram), FY2025-26 — accuracy core
    ├── family-profile.md        # stored requirements: goals (₹100 Cr, ₹5L/mo), residency policy
    └── frameworks.md            # funding waterfall, goal/income projection, scenario compare, output templates
```

## How the two halves fit

- **Skill (this folder)** = *how to think*. Claude loads it, follows the tax rules
  and frameworks, and formats the answer. Runs **inside Claude**.
- **MCP server** (`api/mcp_server.py`) = *the live data + the math*. Claude calls
  its tools; **the server computes** every number. Runs **on your server**.

The skill's Rule #1 and #2 forbid Claude from inventing numbers or doing tax/
compounding math in its head — so accuracy comes from the engine tools, and
judgment comes from the skill.

## One source of truth, synced everywhere via git

This skill lives in the repo at **`.claude/skills/networth-financial-advisor/`**.
Edit the markdown here, commit, and it propagates to every surface — no manual
re-upload:

| Surface | How it picks up this folder | On a future edit |
|---|---|---|
| **Claude Code / Claude Desktop** | Auto-discovered from `.claude/skills/` (this repo) or `~/.claude/skills/` | Just `git pull` — it reads the files directly. Zero migration. |
| **claude.ai connector** | The MCP server serves it **live** via the `advisor_playbook()` tool (reads these files at runtime) | Edit → **redeploy** → the connector returns the new content. No re-zip/upload. |
| **claude.ai uploaded Skill** (optional) | Zip this folder → Settings → Capabilities → Skills → upload | Manual re-upload (only if you want the native auto-activation UI) |

**Recommended:** rely on `advisor_playbook()` for claude.ai (auto-syncs on deploy)
and the native `.claude/skills/` discovery for Claude Code. The server's
`instructions` already tell Claude to call `advisor_playbook()` first on any
planning/tax/funding question, so it loads the latest brain automatically.

For fully hands-off claude.ai uploads, a CI step could push this folder via the
Anthropic **Skills API** on every git push — say the word and I'll wire it.

### First-time connector setup
1. Add the **Networth MCP connector** in claude.ai (Google sign-in, allow-listed email).
2. Ask a planning question — the server points Claude to `advisor_playbook()`, which
   loads this skill live, then drives the data + engine tools.

## MCP tool roadmap the skill is written against

The skill already references these; build them in `api/mcp_server.py` in order.
Today only `whoami` + `financial_snapshot` exist (Phase 0).

**Phase 1 — read tools (context) — ✅ BUILT:**
- `family_profile()` — 4 people, residency (Ramprasad/Ram = NRI; Maha/Ranjeev/Sanjeev = resident), live net worth + income, and the goals (₹100 Cr / ₹5L-mo) with the gap. Residency + goals are policy in `api/planner/advisor_profile.py`.
- `list_holdings(asset_class?)` — value/count/income/CAGR/liquidity per class (caveat: per-lot buy dates not fully captured — flag before a sale's LT/ST tax).
- `liquidity_profile()` — per-class speed→cash + standing friction notes (FD break, ULIP lock, pledge/LAS, NRI TDS + NRE/NRO repatriation) + the waterfall hint.
- `tax_position(fy?)` — per-person LTCG headroom left (₹1.25L each), booked gains from Tax P&L, F&O flag, resident-vs-NRI rules.
- `cashflow()` — monthly income by source (salary, rent, dividends, interest) + per person vs expenses & committed EMIs/SIPs → surplus; reconciles to the dashboard.
- `sellable_assets()` — **the granular edge**: per-holding sell candidates — per person, the loss-harvest set + the tax-free-LTCG set (winners fitting the ₹1.25L headroom) + taxable-above, plus bonds (tax-free flag), FDs (break cost), gold (SGB maturity tax-free). Recommends *specific holdings*, not asset classes. (Equity buy dates aren't stored → LT is assumed + flagged.)

**Focus: HOUSE FUNDING.** The generic projection tools (`goal_projection`,
`income_plan`) were removed — they produced off-topic ₹100 Cr / ₹5L-a-month
noise. The skill is now single-purpose: fund a specific house purchase (amount +
an attached payment schedule) tax-efficiently from live assets.

**Funding & tax engines — ✅ BUILT** (`api/planner/engines.py`):
- `funding_plan(amount_inr)` — tax-efficient waterfall: cash → tax-free harvest → LAS/pledge → loan-against-property → sell LT equity (last). Returns the mix, interest cost, shortfall.
- `tax_impact(items)` — exact per-person capital-gains tax (FY2025-26 rates + cess) using each person's ₹1.25L headroom; NRI-aware.

Also `sellable_assets()` now covers **all assets + liabilities**: equity (granular), real estate (per-parcel), bonds, FDs, gold, cash, ULIP, and loans.

**Phase 4 — synthesis & persistence:**
- `compare_scenarios([...])`
- a stored **Goals** object so progress is tracked month over month.

## Keeping it accurate

- `tax-india.md` is stamped **FY2025-26**; refresh it each Union Budget.
- When a rate in the skill and a number from `tax_impact` disagree, **the tool wins** (its rates are the maintained source) — the skill says so.
- Residency mapping (Ram = NRI, other 3 = resident) is fixed policy; balances/names come live from the tools.
