# Networth MCP Server — connect Claude to your finances

This lets a Claude client (claude.ai custom connector on your **Max** plan) read your
family's live financial data over MCP. **No LLM API bill** — your Claude plan is the
brain; this server just exposes the data + (later) the funding/tax engine as tools.

It runs **inside the existing app** on Railway at `/mcp`, gated by **Google sign-in**
+ an **email allowlist**, so only you can reach it.

Phase 0 tools: `whoami` (confirms access) and `financial_snapshot` (net worth, assets
by class & person, income, liabilities). The funding/tax tools come next.

---

## One-time setup (~10 min)

### Step 0 — Create a Google OAuth client
Google is the sign-in. You need a Client ID + Secret.

1. Go to <https://console.cloud.google.com> → create/select any project.
2. **APIs & Services → OAuth consent screen**:
   - User type **External** → fill app name (e.g. "Networth MCP") + your email.
   - Scopes: add `openid`, `email`, `profile`.
   - **Test users**: add `ranjeevfortrade@gmail.com` (keeps it in testing mode — fine for personal use; no Google review needed).
3. **APIs & Services → Credentials → Create credentials → OAuth client ID**:
   - Application type: **Web application**.
   - **Authorized redirect URI** → add exactly:
     ```
     https://web-production-0cd5c.up.railway.app/auth/callback
     ```
     (`web-production-0cd5c.up.railway.app` = the domain you open the app at, e.g. `networth-production.up.railway.app`.)
   - Create → copy the **Client ID** and **Client Secret**.

### Step 1 — Set Railway env vars
In Railway → your service → **Variables**, add:

| Variable | Value |
|---|---|
| `GOOGLE_MCP_CLIENT_ID` | the Client ID from Step 0 |
| `GOOGLE_MCP_CLIENT_SECRET` | the Client Secret from Step 0 |
| `MCP_BASE_URL` | `https://web-production-0cd5c.up.railway.app` — **root domain, no `/mcp`** |
| `MCP_ALLOWED_EMAILS` | `ranjeevfortrade@gmail.com` *(optional; this is already the default)* |

### Step 2 — Deploy
The code is already committed. Pushing triggers the Railway build. When it's up, the
deploy logs should show:
```
✓ MCP server mounted at /mcp (Google OAuth)
```
(If you see `ⓘ MCP server disabled…` the env vars aren't set yet.)

### Step 3 — Sanity-check (optional)
```bash
curl https://web-production-0cd5c.up.railway.app/.well-known/oauth-protected-resource/mcp
```
Should return JSON with `"resource": "https://web-production-0cd5c.up.railway.app/mcp"`.

### Step 4 — Add it to Claude (any surface)

The connector URL is the same everywhere: `https://web-production-0cd5c.up.railway.app/mcp`

| Where | How |
|---|---|
| **claude.ai / Cowork** | Settings → Connectors → Add custom connector → paste the URL |
| **Claude Desktop** | Settings → Connectors → Add custom connector → paste the URL |
| **Claude Code** | `claude mcp add --transport http networth https://web-production-0cd5c.up.railway.app/mcp` |

Sign in with Google once per surface. **You should not have to sign in again** —
the OAuth state (client registrations + issued tokens) lives in Supabase
(`app_cache`, encrypted with a key derived from the Google client secret), so it
survives redeploys and is shared across replicas. Every new chat on an
already-connected surface just works; a brand-new surface needs one sign-in.

**One connect lasts the whole session (and beyond).** The connection is held by two
tokens: a FastMCP **access token** (issued for **24 h** by default, so a day-long
chat never even has to refresh) and a **refresh token** (persisted in Supabase with
a 1-year fallback, so day-2+ silent refresh keeps the connector live without a new
sign-in). Both are set in `api/mcp_server.py`; the boot log prints
`✓ MCP connection lifetime: access token 24 h · refresh persisted`. To change the
access-token window, set `MCP_ACCESS_TOKEN_TTL_SECONDS` (e.g. `604800` for a week).

If sign-ins *do* keep coming back, check the boot log for
`✓ MCP auth state: Supabase` — the warning variant means the `app_cache` migration
hasn't been run and state is on the container's disk, which Railway wipes. A
`⚠  MCP: this fastmcp build ignores token-lifetime settings` line means the deployed
fastmcp is too old for the 24 h access token (it still works, just refreshes ~hourly).

### Stop the "allow this tool?" prompts (one-time)

The per-tool approval popups are a **client-side** setting, not something the server
controls — so flip it once in whichever surface you use:

- **claude.ai / Cowork**: the first time a Networth tool runs it asks to allow it —
  pick **"Allow always for this connector"** (not "Allow once"). After that, every
  Networth tool runs without a prompt. If you already clicked "once", open
  **Settings → Connectors → Networth** and set its tool permission to **Always allow**.
- **Claude Code**: add the tools to the allowlist so they never prompt, e.g. in
  `.claude/settings.json` under `permissions.allow`:
  `"mcp__networth"` (allows every Networth tool). This repo already runs with broad
  Bash allows; adding the `mcp__networth` wildcard makes the connector equally quiet.

All Networth tools are read-or-stage only — nothing writes to your ledger without an
explicit **Approve** in the Expenses tab — so "Always allow" is safe.

### Old step 4 — Add it to claude.ai
1. claude.ai → **Settings → Connectors → Add custom connector**.
2. **Name**: `Networth`  ·  **URL**: `https://web-production-0cd5c.up.railway.app/mcp`
3. Claude opens the Google sign-in → sign in as `ranjeevfortrade@gmail.com` → approve.
4. In a chat, make sure the **Networth** connector is enabled, then try:
   - *"Call whoami on Networth"* → should return your email + `authorised: true`.
   - *"Get my financial_snapshot"* → net worth, assets by class/person, income, liabilities.

If `whoami` says access denied, the signed-in Google email isn't in `MCP_ALLOWED_EMAILS`.

---

## Sending a bank statement in from Claude (expenses)

Hand Claude an account statement (in Cowork, the desktop app, anywhere the
**Networth** connector is on) and say:

> *"Categorise this statement and send it to my Networth expenses."*

**Use the fast path for a real statement.** Writing a JSON object per transaction
is what makes a 130-row import take minutes — it is thousands of output tokens.
`submit_statement_lines()` takes one compact line per row instead:

```
2026-07-24|285|D|UPI-ZEPTO-ZEPTOONLINE@YBL-620566734991
2026-07-24|1210.50|C|NEFT CR-AUBL0002011-LAXMI INDIA FINANCE
```

`date|amount|D or C|narration` — and that's all. **No merchant name, no owner, no
category, no confidence**: the app derives the merchant from the narration,
categorises from its rules + how you have filed that merchant before, and works out
whose it is. Append a 5th `|Category` field only where Claude specifically
disagrees. Measured on 127 rows: ~1,800 output tokens instead of ~6,500+, every row
categorised, 52 of them straight from merchant memory.

For an .xls/.csv the fastest route of all is the app's own **Import from statement**
button — that parses server-side and costs no model tokens at all.

What happens:
1. Claude calls **`expense_categories()`** → gets THIS app's exact category list
   (expense + income), the four family members, and the categorisation rules. It
   does not invent categories; anything it isn't sure about is left blank for you.
2. Claude calls **`submit_expense_transactions(transactions=[…], account="HDFC ••4321", owner="Ranjeev")`**
   with one row per line of the statement — date, amount, direction
   (`debit` = spend, `credit` = income), merchant name, raw narration, category
   and a 0–1 `confidence`.
3. The rows land in the **review inbox**, not the ledger. Open the app →
   **Expenses** → the *"From Claude"* banner. Filter (needs review / duplicates /
   low confidence / by category / by person / by date / by amount), fix anything
   wrong inline, tick, and hit **Approve** — only then do debits become one-time
   expenses and credits become other-income entries.
4. **`expense_inbox_status()`** tells Claude what's still waiting;
   **`expense_month("2026-07")`** reads back what actually got logged, by category
   and person, plus the cash in hand.

**Whose expense is it** — a statement line rarely says. The app works it out, best
signal first, and records which one won so the review screen can show its reasoning:

| source | how it decided |
|---|---|
| `claude` | the model set `owner` explicitly (it read the context) |
| `narration` | a family member is named in the statement text (`NEFT DR SANJEEV COLLEGE FEE`) |
| `history` | this merchant has consistently been that person's before (≥2 rows, ≥60% agree) |
| `account` | the statement's account belongs to them (matched against the Cash tab) |
| `category` | this category is overwhelmingly one person's (≥3 rows, ≥70% agree) |
| `default` | nothing matched — the batch default. **Flagged as a guess.** |

Rows on `category`/`default`/nothing are marked `owner_guess` and counted in the
**"Whose?"** filter chip, so you can sweep every unattributed row in one pass and
bulk-assign. Setting an owner yourself always wins, and it teaches the next import
(history is learned from your approved ledger). `expense_categories()` also hands
Claude `accounts_by_owner` and `merchant_owner_hints` so it can attribute up front —
with the standing instruction that a wrong owner is worse than no owner.

**Merchant memory** — file a merchant once and it stays filed that way, *whatever
the amount*. Precedence when a row arrives:

`explicit rule` → `learned from your approved ledger` → `Claude's per-row guess` → blank

- Correcting a category in the review screen writes the rule **and** re-files every
  other pending row from that merchant in the same batch ("also filed 4 more…").
- `merchant_memory()` lists every rule + everything learned, so Claude can agree
  with the app before it sends anything; `expense_categories()` returns the same as
  `merchant_category_memory`.
- `remember_merchant(merchant, category, owner?)` sets a rule directly — for
  "Zepto is always groceries". Pass an empty category to clear it.
- Matching is on the merchant name only. ₹45 or ₹98,750 from the same shop both
  land in the same category.

Safety properties worth knowing:
- **Nothing is written without your approval** — the MCP tool can only stage.
- **Re-sending the same statement is safe.** Rows already imported, already in the
  ledger (same date + amount + direction), or repeated inside the batch are
  flagged as duplicates and are excluded from "approve everything".
- **Bad rows can't slip through.** A row missing a date, amount or category is
  blocked from approval and shown with what it needs.
- Staging lives in the `app_cache` KV table — if that migration hasn't been run,
  batches sit on the container's local disk and are lost on redeploy (the tool
  returns a warning and the review screen shows a banner).


## Troubleshooting
- **"couldn't connect" after Google sign-in, logs loop `POST /token 200` → `invalid_token (401)` → `rotated`**: fixed. Cause was the MCP app's auth middleware (BearerAuthBackend, which runs the token-swap validation) not being served — the app now serves the *whole* MCP Starlette app for MCP paths, so tokens validate. If you still see it, confirm the deploy picked up the fix (logs show `✓ MCP combined — Claude connector live at /mcp`).
- **After every redeploy Claude asks you to sign in again**: expected for now. The issued-token store is in-memory, so a container restart clears it and Claude re-runs OAuth (quick). Persisting it (survive restarts) is a later hardening step.
- **`whoami` says access denied**: the Google email you signed in with isn't in `MCP_ALLOWED_EMAILS`.

## How it's wired (for future me)
- `api/mcp_server.py` — builds the FastMCP server (Google auth + allowlist + tools).
- `api/main.py` — when the env vars are set, an ASGI dispatcher (bottom of the file)
  serves the **whole** MCP Starlette app for MCP-owned paths and the FastAPI app for
  everything else, passing the scope through unchanged and bridging both lifespans.
  Serving the whole app (not just its routes) is essential — the token validation
  lives in the MCP app's auth middleware. OAuth endpoints (`/authorize`, `/token`,
  `/register`, `/consent`, `/auth/callback`, `/.well-known/*`) are at the **domain
  root**; the MCP endpoint is `/mcp`. `MCP_BASE_URL` = the root domain (path adds `/mcp`).
- Auth guard in `api/main.py` only gates `/api/*`, so MCP paths pass through to their
  own OAuth. Host/origin protection is off by default, so the Railway domain is fine.
- Expense staging: `api/expenses/inbox.py` (validation, de-dup, approve) with the
  routes under `/api/expenses/inbox/*` and the review UI in
  `frontend/src/app/components/expenses/inbox/`.
- Tools are read-only EXCEPT `submit_expense_transactions`, which can only write to
  the staging inbox — never the ledger. Adding a tool = a new `@mcp.tool` function in
  `api/mcp_server.py` that calls `_require_allowed()` first, then the existing stores.

## Roadmap
- **Phase 1** — `list_assets`, `capital_gains_tax` (read tools).
- **Phase 2** — `funding_plan`, `loan_options` + the India tax engine (the property use case).
- **Phase 3** — constraints, alternatives, methodology.
