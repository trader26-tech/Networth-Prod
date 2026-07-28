# Supabase setup — persistent store for covered-call positions

By default the app stores covered-call positions in `api/cc_positions.json`.
On Railway this **resets every deploy** because container filesystems are
ephemeral. Switching to Supabase fixes that — your data lives in cloud
Postgres and survives forever (free tier handles millions of rows).

## 1 · Create a Supabase project (2 minutes)

1. Go to <https://supabase.com> → **Start your project** → sign in with GitHub
2. **New project**:
   - Name: `zerodha-pro` (anything)
   - Database Password: pick a strong one (you won't need it for this app)
   - Region: pick the one closest to your Railway service (e.g. Mumbai if you're in India)
   - Plan: **Free** ($0)
3. Wait ~2 minutes for the project to spin up

## 2 · Create the table (1 minute)

In the Supabase dashboard:

1. Left sidebar → **SQL Editor** → **New query**
2. Paste the SQL below
3. Click **Run** (or `Cmd/Ctrl + Enter`)

```sql
-- Covered-call positions table
CREATE TABLE IF NOT EXISTS covered_call_positions (
  id                       TEXT PRIMARY KEY,
  name                     TEXT NOT NULL,
  status                   TEXT NOT NULL DEFAULT 'active',
  underlying               TEXT NOT NULL DEFAULT 'NIFTY',
  shares                   INTEGER NOT NULL DEFAULT 0,
  niftybees_entry_price    NUMERIC NOT NULL DEFAULT 0,
  niftybees_cost           NUMERIC NOT NULL DEFAULT 0,
  lots                     INTEGER NOT NULL DEFAULT 1,
  lot_size                 INTEGER NOT NULL DEFAULT 75,
  active_call              JSONB,
  call_history             JSONB NOT NULL DEFAULT '[]'::jsonb,
  total_premium_collected  NUMERIC NOT NULL DEFAULT 0,
  notes                    TEXT NOT NULL DEFAULT '',
  tags                     TEXT[] NOT NULL DEFAULT '{}',
  created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  closed_at                TIMESTAMPTZ
);

-- Index for the common "list active positions first" query
CREATE INDEX IF NOT EXISTS idx_cc_status_created
  ON covered_call_positions (status, created_at DESC);

-- Optional GIN index — speeds up filtering by tag if you build that later.
CREATE INDEX IF NOT EXISTS idx_cc_tags
  ON covered_call_positions USING GIN (tags);

-- This app uses the service-role key from the backend, so RLS isn't required.
-- If you want defence-in-depth (e.g. you'll later expose Supabase to a
-- multi-user frontend), enable RLS and write your own policies:
--   ALTER TABLE covered_call_positions ENABLE ROW LEVEL SECURITY;
```

### Already running an older version? Add the tags column

If you set Supabase up before the tagging feature, run this once in the
SQL Editor — `IF NOT EXISTS` makes it safe to re-run:

```sql
ALTER TABLE covered_call_positions
  ADD COLUMN IF NOT EXISTS tags TEXT[] NOT NULL DEFAULT '{}';

CREATE INDEX IF NOT EXISTS idx_cc_tags
  ON covered_call_positions USING GIN (tags);
```

Until you do this, the app will still create / update positions normally
(the backend strips `tags` from the payload and prints a one-line warning
in the logs), but tags won't persist across reloads.

You should see "Success. No rows returned." Switch to **Table Editor** in the
sidebar — `covered_call_positions` should now be listed (empty).

### Hedges table (long protective puts)

In the same SQL editor, run this to add the hedges table. It backs the
`/hedges` page (scan / create / close / roll / tag protective puts) so
hedges survive container redeploys, identically to CC positions.

```sql
-- Hedge positions (long protective puts; many-to-many tag with CC strategies)
CREATE TABLE IF NOT EXISTS hedge_positions (
  id                   TEXT PRIMARY KEY,
  created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  closed_at            TIMESTAMPTZ,
  status               TEXT NOT NULL DEFAULT 'open',     -- open / closed / rolled
  strike               NUMERIC NOT NULL,
  expiry               TEXT NOT NULL,                    -- 'YYYY-MM-DD'
  lots                 INTEGER NOT NULL DEFAULT 1,
  lot_size             INTEGER NOT NULL DEFAULT 75,
  premium_paid         NUMERIC NOT NULL,
  close_price          NUMERIC,
  realized_pnl         NUMERIC,
  symbol               TEXT NOT NULL DEFAULT '',
  notes                TEXT NOT NULL DEFAULT '',
  tagged_strategies    TEXT[] NOT NULL DEFAULT '{}',     -- CC position IDs this hedge protects
  rolled_from          TEXT                              -- prev hedge id when rolled
);

-- Common queries: list open hedges, lookup by tag
CREATE INDEX IF NOT EXISTS idx_hedge_status
  ON hedge_positions (status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_hedge_tags
  ON hedge_positions USING GIN (tagged_strategies);
```

After running, **Table Editor** should now list `hedge_positions` alongside
`covered_call_positions`. Both tables use the same `SUPABASE_URL` /
`SUPABASE_SERVICE_KEY` env vars — no additional secrets needed.

### If you get a Row-Level Security error on insert

```
postgrest.exceptions.APIError: new row violates row-level security policy
                                for table "hedge_positions"
```

Newer Supabase projects auto-enable RLS on new tables. Since this app uses
the service-role key from the backend (the browser never talks to Supabase
directly), you can safely disable RLS — same posture as `covered_call_positions`:

```sql
ALTER TABLE hedge_positions DISABLE ROW LEVEL SECURITY;
```

If you want to keep RLS on (e.g., you'll later expose Supabase to a
multi-user frontend), add a permissive policy for the service role instead:

```sql
CREATE POLICY "service role full access" ON hedge_positions
  FOR ALL USING (auth.role() = 'service_role') WITH CHECK (true);
```

### Land net-worth tables (real-estate parcels + documents)

These back the **Land** page (`/land`). Storage is **2NF**: one row per parcel
in `land_parcels`, and one row per uploaded file in `land_documents` (FK →
`land_parcels.id`). CAGR is **never stored** — the backend derives gross + net
CAGR from `bought_price`, `current_estimated_price`, `after_brokerage_price`
and `bought_date` at read time. Document *files* live in a private Storage
bucket called `land-docs`, which the backend **auto-creates** on first upload —
you don't need to make it by hand.

Run this once in **SQL Editor → New query**:

```sql
-- Land parcels (the facts about each piece of land)
CREATE TABLE IF NOT EXISTS land_parcels (
  id                       TEXT PRIMARY KEY,
  name                     TEXT NOT NULL,
  owner                    TEXT,
  location                 TEXT,
  area_sqft                NUMERIC,
  bought_date              DATE,
  bought_price             NUMERIC,
  current_estimated_price  NUMERIC,
  after_brokerage_price    NUMERIC,
  notes                    TEXT,
  created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at               TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Documents — separate table (2NF): each file depends only on its own id,
-- and references its parent parcel via land_id.
CREATE TABLE IF NOT EXISTS land_documents (
  id            TEXT PRIMARY KEY,
  land_id       TEXT NOT NULL REFERENCES land_parcels(id) ON DELETE CASCADE,
  filename      TEXT NOT NULL,
  mime_type     TEXT,
  size          BIGINT,
  storage_path  TEXT NOT NULL,        -- object path inside the `land-docs` bucket
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_land_created   ON land_parcels (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_land_docs_land ON land_documents (land_id);

-- Backend uses the service-role key, so RLS isn't required. If your project
-- auto-enabled RLS on the new tables and inserts fail, disable it:
--   ALTER TABLE land_parcels   DISABLE ROW LEVEL SECURITY;
--   ALTER TABLE land_documents DISABLE ROW LEVEL SECURITY;
```

After running, **Table Editor** should list `land_parcels` and
`land_documents`. The Land page will switch from a "run the migration" notice to
live Supabase storage automatically (no restart needed).

**Already created the tables before the `owner` column existed?** Run this once
— it's safe to re-run:

```sql
ALTER TABLE land_parcels ADD COLUMN IF NOT EXISTS owner     TEXT;
ALTER TABLE land_parcels ADD COLUMN IF NOT EXISTS area_sqft NUMERIC;
```

Until you do, the app still saves parcels (the backend drops the missing column
and logs a one-line hint); those fields just won't persist. Rate-per-sq-ft is
derived from `area_sqft` + `current_estimated_price` at read time (not stored).

### Apartment net-worth tables (rented flats/units + documents)

These back the **Apartments** page (`/apartments`). Same 2NF shape as Land, plus
a **monthly_rent** column — the page blends **price appreciation + rental yield**
into an overall CAGR (derived at read time, never stored). Files live in the
auto-created private bucket `apartment-docs`.

Run once in **SQL Editor → New query**:

```sql
CREATE TABLE IF NOT EXISTS apartment_units (
  id                       TEXT PRIMARY KEY,
  name                     TEXT NOT NULL,
  owner                    TEXT,
  location                 TEXT,
  area_sqft                NUMERIC,
  bought_date              DATE,
  bought_price             NUMERIC,
  current_estimated_price  NUMERIC,
  after_brokerage_price    NUMERIC,
  monthly_rent             NUMERIC,
  notes                    TEXT,
  created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at               TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS apartment_documents (
  id            TEXT PRIMARY KEY,
  apartment_id  TEXT NOT NULL REFERENCES apartment_units(id) ON DELETE CASCADE,
  filename      TEXT NOT NULL,
  mime_type     TEXT,
  size          BIGINT,
  storage_path  TEXT NOT NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Tenants: one row per tenancy. "Years lived" is derived at read time from the
-- move-in/move-out dates. A tenant with no move_out_date is the current tenant.
CREATE TABLE IF NOT EXISTS apartment_tenants (
  id            TEXT PRIMARY KEY,
  apartment_id  TEXT NOT NULL REFERENCES apartment_units(id) ON DELETE CASCADE,
  name          TEXT NOT NULL,
  phone         TEXT,
  advance_paid  NUMERIC,
  move_in_date  DATE,
  move_out_date DATE,                 -- NULL while the tenant is still living there
  notes         TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_apt_created     ON apartment_units (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_apt_docs_unit   ON apartment_documents (apartment_id);
CREATE INDEX IF NOT EXISTS idx_apt_tenant_unit ON apartment_tenants (apartment_id);

-- If RLS auto-enabled and inserts fail:
--   ALTER TABLE apartment_units     DISABLE ROW LEVEL SECURITY;
--   ALTER TABLE apartment_documents DISABLE ROW LEVEL SECURITY;
--   ALTER TABLE apartment_tenants   DISABLE ROW LEVEL SECURITY;
```

If `apartment_units` + `apartment_documents` already exist, add just the tenants table:

```sql
CREATE TABLE IF NOT EXISTS apartment_tenants (
  id TEXT PRIMARY KEY,
  apartment_id TEXT NOT NULL REFERENCES apartment_units(id) ON DELETE CASCADE,
  name TEXT NOT NULL, phone TEXT, advance_paid NUMERIC,
  move_in_date DATE, move_out_date DATE, notes TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_apt_tenant_unit ON apartment_tenants (apartment_id);
```

### App password gate (single shared password)

Protects the whole web app behind one password, stored **in plaintext** in
Supabase so you can read/change it any time in the Table Editor. The backend
reads it from `app_auth`; the frontend shows a lock screen until the right
password is entered (then remembers it on that device). No password row → the
gate is **disabled** (app open), so you can't lock yourself out by accident.

```sql
CREATE TABLE IF NOT EXISTS app_auth (
  id          INT PRIMARY KEY DEFAULT 1,
  password    TEXT NOT NULL,
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT  app_auth_single_row CHECK (id = 1)   -- only ever one row
);

-- Set / change your password here (plaintext, visible in Table Editor):
INSERT INTO app_auth (id, password)
VALUES (1, 'your-password-here')
ON CONFLICT (id) DO UPDATE
  SET password = EXCLUDED.password, updated_at = NOW();

-- backend uses the service-role key, so RLS isn't required:
-- ALTER TABLE app_auth DISABLE ROW LEVEL SECURITY;
```

- **Change the password later:** edit the `password` cell in Table Editor, or
  re-run the `INSERT … ON CONFLICT` above. It takes effect on the next unlock.
- **Turn the gate off:** `DELETE FROM app_auth;` (or blank the password).
- **Re-lock a device:** click the 🔒 button in the top bar.
- ⚠️ This is a single shared password kept in plaintext by design — fine for a
  personal dashboard, not for multi-user or sensitive multi-tenant use.

### Land + Build tables (self-built properties — land + construction legs)

Backs the **Land + Build** page (`/built`): a plot you bought, then built on
later. Because the land and the construction were paid at **different dates**,
returns use a **two-leg money-weighted IRR** (solved server-side, never stored):
`land·(1+r)^yrs_land + build·(1+r)^yrs_build = current`, plus rent on top. Files
live in the auto-created private bucket `built-docs`.

```sql
CREATE TABLE IF NOT EXISTS built_properties (
  id                       TEXT PRIMARY KEY,
  name                     TEXT NOT NULL,
  owner                    TEXT,
  location                 TEXT,
  area_sqft                NUMERIC,
  land_cost                NUMERIC,
  land_date                DATE,
  construction_cost        NUMERIC,
  construction_date        DATE,
  current_estimated_price  NUMERIC,
  after_brokerage_price    NUMERIC,
  monthly_rent             NUMERIC,
  notes                    TEXT,
  created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at               TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS built_documents (
  id            TEXT PRIMARY KEY,
  property_id   TEXT NOT NULL REFERENCES built_properties(id) ON DELETE CASCADE,
  filename      TEXT NOT NULL,
  mime_type     TEXT,
  size          BIGINT,
  storage_path  TEXT NOT NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_built_created ON built_properties (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_built_docs    ON built_documents (property_id);

-- If RLS auto-enabled and inserts fail:
--   ALTER TABLE built_properties DISABLE ROW LEVEL SECURITY;
--   ALTER TABLE built_documents  DISABLE ROW LEVEL SECURITY;
```

### Gold / Silver tables (precious-metal pieces + documents)

Backs the **Gold** page (`/gold`). Each piece stores raw facts — metal
(gold/silver/other), net weight, purity, optional purchase date/price, optional
manual value. **Current value is computed live** on the page from spot rates
(`/api/gold/prices`, free no-key feed) × weight × purity, so it re-values as the
metal price moves. CAGR shows only for pieces with both a purchase date and
price. Files live in the auto-created private bucket `gold-docs`.

```sql
CREATE TABLE IF NOT EXISTS gold_items (
  id              TEXT PRIMARY KEY,
  name            TEXT NOT NULL,
  owner           TEXT,
  metal_type      TEXT NOT NULL DEFAULT 'gold',   -- gold | silver | other
  weight_g        NUMERIC,                         -- net metal weight, grams
  purity_pct      NUMERIC,                         -- pure-metal %, e.g. 91.6 (22K), 99.9, 92.5
  manual_value    NUMERIC,                         -- override / value for 'other' pieces
  purchase_date   DATE,
  purchase_price  NUMERIC,
  location        TEXT,                            -- where the piece is kept (free text)
  notes           TEXT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS gold_documents (
  id            TEXT PRIMARY KEY,
  gold_id       TEXT NOT NULL REFERENCES gold_items(id) ON DELETE CASCADE,
  filename      TEXT NOT NULL,
  mime_type     TEXT,
  size          BIGINT,
  storage_path  TEXT NOT NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_gold_created ON gold_items (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_gold_docs    ON gold_documents (gold_id);

-- If RLS auto-enabled and inserts fail:
--   ALTER TABLE gold_items     DISABLE ROW LEVEL SECURITY;
--   ALTER TABLE gold_documents DISABLE ROW LEVEL SECURITY;
```

If `gold_items` already exists, add the location column:

```sql
ALTER TABLE gold_items ADD COLUMN IF NOT EXISTS location TEXT;
```

### Stock portfolio tables (live multi-account Kite holdings)

Back the modern **Stocks** page (`/stocks`). One row per brokerage account
(`stock_accounts`, holding the per-account Kite Connect **Personal** credentials
+ daily access token — server-only, never sent to the browser) and one row per
holding (`stock_holdings`, replaced on every sync). Prices are fetched live from
the free Yahoo feed on read, so no market data is stored.

```sql
CREATE TABLE IF NOT EXISTS stock_accounts (
  id               TEXT PRIMARY KEY,
  person           TEXT,
  broker           TEXT NOT NULL DEFAULT 'kite',
  account_label    TEXT,
  kind             TEXT NOT NULL DEFAULT 'connected',  -- connected | manual
  api_key          TEXT,
  api_secret       TEXT,
  access_token     TEXT,
  kite_user_id     TEXT,
  status           TEXT DEFAULT 'pending',             -- pending | connected | expired
  token_updated_at TIMESTAMPTZ,
  last_synced      TIMESTAMPTZ,
  note             TEXT,
  sellable_on      DATE,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS stock_holdings (
  id            TEXT PRIMARY KEY,
  account_id    TEXT NOT NULL REFERENCES stock_accounts(id) ON DELETE CASCADE,
  person        TEXT,
  broker        TEXT,
  account_label TEXT,
  symbol        TEXT,
  name          TEXT,
  exchange      TEXT,
  isin          TEXT,
  currency      TEXT DEFAULT 'INR',
  quantity      NUMERIC,
  avg_price     NUMERIC,
  import_price  NUMERIC,
  last_synced   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_stock_holdings_account ON stock_holdings (account_id);

-- If RLS auto-enabled and inserts fail:
--   ALTER TABLE stock_accounts DISABLE ROW LEVEL SECURITY;
--   ALTER TABLE stock_holdings DISABLE ROW LEVEL SECURITY;
```

If `stock_holdings` already exists (created before Excel import), add the new columns:

```sql
ALTER TABLE stock_holdings ADD COLUMN IF NOT EXISTS name         TEXT;
ALTER TABLE stock_holdings ADD COLUMN IF NOT EXISTS currency     TEXT DEFAULT 'INR';
ALTER TABLE stock_holdings ADD COLUMN IF NOT EXISTS import_price NUMERIC;
ALTER TABLE stock_holdings ALTER COLUMN symbol DROP NOT NULL;
```

> Security: `api_secret` and `access_token` live only in this table and are
> never returned by the API. The old `brokerage_accounts` table is left intact
> as a backup and stops being used once a live account is connected.

#### Dividend log (calendar + monthly totals)

Backs the **dividend logging** on the Stocks tab — when you get a dividend mail
("₹0.58/share"), you log the per-share amount for a stock on a date and we store
`shares × per_share` for that day.

Each row is one dividend event. `received` separates **booked** credits (green
on the calendar) from **expected/pending** ones you haven't been paid yet
(yellow); you flip it to "got it" from either the calendar or the per-stock
panel on the holdings page. New entries start **pending** by default.
`stock_dividend_meta` holds a per-symbol manual "received in previous years"
figure that overrides the auto sum of older booked dividends.

```sql
CREATE TABLE IF NOT EXISTS stock_dividends (
  id          TEXT PRIMARY KEY,
  date        DATE NOT NULL,
  symbol      TEXT NOT NULL,
  name        TEXT,
  per_share   NUMERIC NOT NULL,
  shares      NUMERIC NOT NULL,
  amount      NUMERIC NOT NULL,        -- per_share × shares (server-computed)
  received    BOOLEAN NOT NULL DEFAULT FALSE,  -- FALSE = pending (not yet paid)
  received_at TIMESTAMPTZ,
  person      TEXT,
  account_id  TEXT,
  note        TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW());
CREATE INDEX IF NOT EXISTS idx_stock_dividends_date ON stock_dividends (date);

-- Already have stock_dividends from an earlier version? Add the new columns:
ALTER TABLE stock_dividends ADD COLUMN IF NOT EXISTS received BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE stock_dividends ADD COLUMN IF NOT EXISTS received_at TIMESTAMPTZ;
-- Start everything as pending — you mark each "got it" when the money lands:
UPDATE stock_dividends SET received = FALSE, received_at = NULL;

-- When this dividend was first PULLED from an exchange page (auto-sync / CSV import).
-- Lets the sync popup show "already on your calendar since <date>" vs "just added".
-- Optional — the app degrades gracefully (drops the field) until you add it:
ALTER TABLE stock_dividends ADD COLUMN IF NOT EXISTS synced_at TIMESTAMPTZ;

-- Per-symbol extras (manual "previous years" override):
CREATE TABLE IF NOT EXISTS stock_dividend_meta (
  symbol      TEXT PRIMARY KEY,
  prev_years  NUMERIC,
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW());

-- Per-person dividend TDS rate (0–1). Row with person='' is the global default:
CREATE TABLE IF NOT EXISTS dividend_tds (
  person      TEXT PRIMARY KEY,
  rate        NUMERIC,
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW());

-- Per-(stock, person) "collected" override — correct the auto split-by-shares:
CREATE TABLE IF NOT EXISTS dividend_collected (
  symbol      TEXT NOT NULL,
  person      TEXT NOT NULL DEFAULT '',
  collected   NUMERIC,
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (symbol, person));

-- If RLS auto-enabled and inserts fail:
--   ALTER TABLE stock_dividends DISABLE ROW LEVEL SECURITY;
--   ALTER TABLE stock_dividend_meta DISABLE ROW LEVEL SECURITY;
--   ALTER TABLE dividend_tds DISABLE ROW LEVEL SECURITY;
```

### Document vault tables (nested folders + arbitrary files)

Backs the **Documents** page (`/documents`) — user-created nested folders
(Aadhaar, insurance, anything) holding any file type. Files live in the
auto-created private bucket `vault-docs` with opaque object keys
(`{folder_id}/{doc_id}`); the display name lives in the row so renames are a
metadata-only update. The Land/Apartments/Gold "linked" folders are assembled on
the frontend from those domains' own documents — nothing extra is stored here.

```sql
CREATE TABLE IF NOT EXISTS document_folders (
  id          TEXT PRIMARY KEY,
  name        TEXT NOT NULL,
  parent_id   TEXT REFERENCES document_folders(id) ON DELETE CASCADE,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS vault_documents (
  id            TEXT PRIMARY KEY,
  folder_id     TEXT NOT NULL REFERENCES document_folders(id) ON DELETE CASCADE,
  filename      TEXT NOT NULL,
  mime_type     TEXT,
  size          BIGINT,
  storage_path  TEXT NOT NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_document_folders_parent ON document_folders (parent_id);
CREATE INDEX IF NOT EXISTS idx_vault_documents_folder  ON vault_documents (folder_id);

-- If RLS auto-enabled and inserts fail:
--   ALTER TABLE document_folders DISABLE ROW LEVEL SECURITY;
--   ALTER TABLE vault_documents  DISABLE ROW LEVEL SECURITY;
```

### Stocks tradebook table (equity trades from Zerodha)

Backs the **Stocks** page (`/stocks`). You upload a Zerodha tradebook .xlsx;
each executed trade is stored, then holdings / P&L / **XIRR** and a Nifty
comparison are computed on read (FIFO, gross of charges). Re-uploading the same
tradebook is idempotent (dedup by `trade_id`).

```sql
CREATE TABLE IF NOT EXISTS stock_trades (
  id           TEXT PRIMARY KEY,
  owner        TEXT NOT NULL DEFAULT 'default',
  account      TEXT,                 -- broker client id (e.g. Zerodha VWM579)
  symbol       TEXT NOT NULL,
  isin         TEXT,
  trade_date   DATE,
  trade_type   TEXT NOT NULL,        -- buy | sell
  quantity     NUMERIC NOT NULL,
  price        NUMERIC NOT NULL,
  exchange     TEXT,
  trade_id     TEXT,
  order_time   TEXT,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_stocktrades_owner   ON stock_trades (owner);
CREATE INDEX IF NOT EXISTS idx_stocktrades_account ON stock_trades (account, trade_id);

-- backend uses the service-role key:
--   ALTER TABLE stock_trades DISABLE ROW LEVEL SECURITY;
```

### Brokerage accounts table (per-member start/end → CAGR)

Backs the **Stocks** page (`/stocks`). One row per family member's broker
account: the amount invested on a start date and what it's worth on an end
date. Per-account CAGR and the combined money-weighted return (XIRR) are
computed on read — no tradebook needed.

```sql
CREATE TABLE IF NOT EXISTS brokerage_accounts (
  id           TEXT PRIMARY KEY,
  member       TEXT NOT NULL,          -- family member (e.g. Maha)
  broker       TEXT,                   -- broker / account name (e.g. Zerodha)
  start_date   DATE NOT NULL,
  start_amount NUMERIC NOT NULL,       -- invested at the start
  end_date     DATE NOT NULL,
  end_amount   NUMERIC NOT NULL,       -- value now / at exit
  note         TEXT,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_brokerage_member ON brokerage_accounts (member);

-- backend uses the service-role key:
--   ALTER TABLE brokerage_accounts DISABLE ROW LEVEL SECURITY;
```

### Bonds table (income, YTM, maturity)

Backs the **Bonds** page (`/bonds`). One row per bond holding; monthly income,
YTM, the payout calendar and maturity ladder are computed on read.

```sql
CREATE TABLE IF NOT EXISTS bonds (
  id               TEXT PRIMARY KEY,
  owner            TEXT NOT NULL,        -- family member
  broker           TEXT,                 -- broker / account
  issuer           TEXT NOT NULL,        -- e.g. "REC Ltd 7.5% 2033"
  bond_type        TEXT,                 -- G-Sec | Corporate NCD | Tax-free | ...
  isin             TEXT,
  rating           TEXT,                 -- AAA, AA+, ...
  tax_free         BOOLEAN DEFAULT FALSE,
  face_value       NUMERIC,              -- per unit (e.g. 1000)
  quantity         NUMERIC,
  buy_price        NUMERIC,              -- per unit (handles premium/discount)
  coupon_rate      NUMERIC,              -- % p.a. (derived/fallback; effective-avg shown on read)
  coupon_freq      TEXT,                 -- monthly|quarterly|half_yearly|annual|cumulative|zero
  repayment_type   TEXT,                 -- bullet | amortizing
  purchase_date    DATE,
  first_payment_date DATE,               -- anchor for the generated/edited payout timeline
  maturity_date    DATE,
  redemption_value NUMERIC,              -- optional override (cumulative/zero-coupon)
  ytm_input        NUMERIC,              -- YTM the user enters (seed; % p.a.)
  schedule         JSONB,                -- editable per-period cashflows [{date, interest, principal}] — source of truth
  sellable_on      DATE,                 -- earliest sell date (buy planner)
  note             TEXT,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_bonds_owner ON bonds (owner);

-- backend uses the service-role key:
--   ALTER TABLE bonds DISABLE ROW LEVEL SECURITY;
```

If the `bonds` table already exists, add the new columns (the tab now generates
a schedule from YTM, then lets you edit each period's interest & principal):

```sql
ALTER TABLE bonds ADD COLUMN IF NOT EXISTS first_payment_date DATE;
ALTER TABLE bonds ADD COLUMN IF NOT EXISTS ytm_input          NUMERIC;
ALTER TABLE bonds ADD COLUMN IF NOT EXISTS schedule           JSONB;
ALTER TABLE bonds ADD COLUMN IF NOT EXISTS sellable_on        DATE;
```

#### Bond payment status (received / pending / not-received)

Powers the click-to-mark repayment **calendar** on the Bonds page (the same
green = received · orange = pending · red = not-received grading as the Dividends
calendar). Bond payouts are computed from each bond's `schedule`, so status can't
live on the payment — it's kept here, one row per payment the user has marked,
keyed by `(bond_id, date)`. Anything absent is treated as **pending** (expected).
The page degrades gracefully if this table is missing, so the migration is
optional until you want the marks to persist.

```sql
CREATE TABLE IF NOT EXISTS bond_payment_status (
  bond_id     TEXT NOT NULL,                 -- references bonds.id
  date        DATE NOT NULL,                 -- the payout date being marked
  status      TEXT NOT NULL DEFAULT 'pending', -- pending | received | not_received
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (bond_id, date)                -- one status per payment; enables upsert
);

-- backend uses the service-role key:
--   ALTER TABLE bond_payment_status DISABLE ROW LEVEL SECURITY;
```

### Salary / income table (earned income)

Backs the **Salary** page (`/salary`). One row per recurring income stream;
the INR value and per-month figure are computed on read, so a foreign-currency
salary (e.g. KWD) re-values automatically at the live rate.

```sql
CREATE TABLE IF NOT EXISTS salary_entries (
  id            TEXT PRIMARY KEY,
  person        TEXT NOT NULL,        -- earner (family member)
  amount        NUMERIC,              -- in the entry's own currency
  currency      TEXT DEFAULT 'INR',   -- INR | KWD | USD | ...
  frequency     TEXT DEFAULT 'monthly', -- monthly | annual
  bank_account  TEXT,                 -- which account it lands in (free label)
  note          TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_salary_person ON salary_entries (person);

-- backend uses the service-role key:
--   ALTER TABLE salary_entries DISABLE ROW LEVEL SECURITY;
```

### ULIP policies table (unit-linked insurance)

Backs the **ULIP** page (`/ulip`). One row per policy; lock-in end, maturity,
premiums paid/remaining, gain and XIRR are computed on read. The current fund
value is what counts toward net worth.

```sql
CREATE TABLE IF NOT EXISTS ulip_policies (
  id                         TEXT PRIMARY KEY,
  owner                      TEXT,                 -- policy holder (family member)
  insurer                    TEXT,                 -- e.g. HDFC Life, ICICI Pru
  plan_name                  TEXT NOT NULL,        -- e.g. "Click 2 Wealth"
  policy_number              TEXT,
  life_assured               TEXT,                 -- person insured (may differ from owner)
  start_date                 DATE,                 -- commencement
  policy_term_years          NUMERIC,              -- total term
  premium_paying_term_years  NUMERIC,              -- years you pay premiums
  premium_amount             NUMERIC,              -- per instalment
  premium_frequency          TEXT DEFAULT 'yearly',-- monthly|quarterly|half_yearly|yearly|single
  sum_assured                NUMERIC,              -- life cover
  fund_value                 NUMERIC,              -- current value (NAV × units)
  fund_type                  TEXT DEFAULT 'equity',-- equity|balanced|debt|other
  note                       TEXT,
  created_at                 TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at                 TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_ulip_owner ON ulip_policies (owner);

-- backend uses the service-role key:
--   ALTER TABLE ulip_policies DISABLE ROW LEVEL SECURITY;
```

### Fixed Deposits table

Backs the **FD** page (`/fd`). One row per deposit; maturity date/amount,
current value, interest income and the payout calendar are computed on read.
The current value counts toward net worth; payout interest feeds monthly income.

```sql
CREATE TABLE IF NOT EXISTS fd_deposits (
  id                     TEXT PRIMARY KEY,
  owner                  TEXT,                  -- depositor (family member)
  bank                   TEXT NOT NULL,         -- bank / NBFC
  principal              NUMERIC,               -- amount deposited
  interest_rate          NUMERIC,               -- % p.a.
  start_date             DATE,
  tenure_months          INTEGER,               -- total tenure in months
  compounding_frequency  TEXT DEFAULT 'quarterly', -- monthly|quarterly|half_yearly|yearly
  payout_type            TEXT DEFAULT 'payout',    -- payout (non-cumulative) | cumulative
  payout_frequency       TEXT DEFAULT 'quarterly', -- monthly|quarterly|half_yearly|yearly
  note                   TEXT,
  created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at             TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_fd_owner ON fd_deposits (owner);

-- backend uses the service-role key:
--   ALTER TABLE fd_deposits DISABLE ROW LEVEL SECURITY;
```

### Monthly expenses table

Backs the **Expenses** page (`/expenses`). One row per recurring expense; the
monthly ₹-equivalent (currency-converted + frequency-normalised) and the
category/person/subscription splits are computed on read. Powers the dashboard's
income − expenses surplus & savings rate.

```sql
CREATE TABLE IF NOT EXISTS expenses (
  id              TEXT PRIMARY KEY,
  owner           TEXT,                  -- who pays (family member)
  name            TEXT NOT NULL,         -- e.g. "Rent", "Netflix"
  category        TEXT,                  -- Housing, Groceries, Subscriptions, ...
  amount          NUMERIC,               -- in the entry's own currency
  currency        TEXT DEFAULT 'INR',    -- INR | KWD | ...
  frequency       TEXT DEFAULT 'monthly',-- weekly|monthly|quarterly|half_yearly|yearly|one_time
  payment_method  TEXT,                  -- bank / card / cash (free label)
  is_subscription BOOLEAN DEFAULT FALSE,
  essential       BOOLEAN DEFAULT TRUE,  -- essential vs discretionary
  active          BOOLEAN DEFAULT TRUE,  -- paused expenses are excluded from totals
  is_template     BOOLEAN DEFAULT TRUE,  -- TRUE = recurring reminder template; FALSE = actual logged expense
  template_id     TEXT,                  -- for a logged expense created from a reminder, the template's id
  on_date         DATE,                  -- logged expense: the date it occurred (or one-off date)
  note            TEXT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at      TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_expenses_owner ON expenses (owner);
CREATE INDEX IF NOT EXISTS idx_expenses_category ON expenses (category);

-- if the table already exists from an earlier version, add the new columns:
ALTER TABLE expenses ADD COLUMN IF NOT EXISTS on_date DATE;
ALTER TABLE expenses ADD COLUMN IF NOT EXISTS is_template BOOLEAN DEFAULT TRUE;
ALTER TABLE expenses ADD COLUMN IF NOT EXISTS template_id TEXT;

-- India/Kuwait redesign: region = currency (INR→India, KWD→Kuwait), recurring =
-- frequency≠one_time (carried forward every month). Only NEW column needed is
-- `end_date`, which lets you STOP a recurring expense from a month onward while
-- earlier months keep it. Everything else works without any migration.
ALTER TABLE expenses ADD COLUMN IF NOT EXISTS end_date TEXT;

-- backend uses the service-role key:
--   ALTER TABLE expenses DISABLE ROW LEVEL SECURITY;
```

### Other income table

Backs the **Other Income** page (`/other-income`). For income that isn't a
salary — dividends, interest, rent, bonuses, capital gains, gifts. One table
holds both recurring reminder templates (`is_template = TRUE`) and the actual
month-by-month logged receipts (`is_template = FALSE`). The ₹-equivalent
(currency-converted + frequency-normalised) and the category/person splits are
computed on read. Recurring rows feed the dashboard's monthly income & surplus.

```sql
CREATE TABLE IF NOT EXISTS other_income (
  id            TEXT PRIMARY KEY,
  owner         TEXT,                  -- who received it (family member)
  source        TEXT NOT NULL,         -- e.g. "INFY dividend", "Shop rent"
  category      TEXT,                  -- Dividend, Interest, Rental income, ...
  amount        NUMERIC,               -- in the entry's own currency
  currency      TEXT DEFAULT 'INR',    -- INR | KWD | ...
  frequency     TEXT DEFAULT 'monthly',-- weekly|monthly|quarterly|half_yearly|yearly|one_time
  account       TEXT,                  -- bank / broker it landed in (free label)
  active        BOOLEAN DEFAULT TRUE,  -- paused templates are excluded from totals
  is_template   BOOLEAN DEFAULT TRUE,  -- TRUE = recurring reminder template; FALSE = actual logged receipt
  template_id   TEXT,                  -- for a logged receipt created from a reminder, the template's id
  on_date       DATE,                  -- logged receipt: the date it was received
  note          TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_other_income_owner ON other_income (owner);
CREATE INDEX IF NOT EXISTS idx_other_income_category ON other_income (category);

-- backend uses the service-role key:
--   ALTER TABLE other_income DISABLE ROW LEVEL SECURITY;
```

### Buy-planner table

Backs the **Buy planner** card on the home dashboard (`/`). One row per thing
you want to buy (₹ price + a priority order), each financed either from your
monthly surplus (`finance_mode = 'income'`) or by selling specific assets
(`finance_mode = 'savings'`, with `finance_assets` = JSON list of asset
position-keys and `sold_assets` = the subset already sold). Each asset's *sell
date* lives on the asset's own table (see the `sellable_on` ALTERs below), so
there's no separate sell list. The scheduling runs client-side off these rows
plus the dashboard's monthly surplus & each asset's realisable value + sell date.

```sql
CREATE TABLE IF NOT EXISTS purchase_wishlist (
  id             TEXT PRIMARY KEY,
  name           TEXT NOT NULL,        -- e.g. "MacBook Air", "House"
  price          NUMERIC,              -- ₹
  priority       INTEGER DEFAULT 0,    -- lower = buy sooner (reorderable)
  finance_mode   TEXT DEFAULT 'income',-- income (monthly surplus) | savings (sell assets)
  finance_assets TEXT,                 -- JSON list of "asset_class|name|owner" keys to sell
  sold_assets    TEXT,                 -- JSON list of those keys already sold (collected)
  target_date    DATE,                 -- when you want to buy it
  monthly_contribution NUMERIC,        -- (legacy) ₹/mo set aside
  saved          NUMERIC,              -- ₹ actually set aside so far (you "add funds")
  bought         BOOLEAN DEFAULT FALSE,
  note           TEXT,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_wishlist_priority ON purchase_wishlist (priority);

-- The planner reads each asset's sell date from the asset's own table. Add the
-- column to every sellable-asset table (safe to re-run):
ALTER TABLE land_parcels       ADD COLUMN IF NOT EXISTS sellable_on DATE;
ALTER TABLE apartment_units    ADD COLUMN IF NOT EXISTS sellable_on DATE;
ALTER TABLE built_properties   ADD COLUMN IF NOT EXISTS sellable_on DATE;
ALTER TABLE gold_items         ADD COLUMN IF NOT EXISTS sellable_on DATE;
ALTER TABLE fd_deposits        ADD COLUMN IF NOT EXISTS sellable_on DATE;
ALTER TABLE ulip_policies      ADD COLUMN IF NOT EXISTS sellable_on DATE;
ALTER TABLE bonds              ADD COLUMN IF NOT EXISTS sellable_on DATE;
ALTER TABLE brokerage_accounts ADD COLUMN IF NOT EXISTS sellable_on DATE;

-- backend uses the service-role key:
--   ALTER TABLE purchase_wishlist DISABLE ROW LEVEL SECURITY;
```

> Upgrading from the earlier two-table planner? Drop the old sell list with
> `DROP TABLE IF EXISTS asset_sell_plan;` and add the new columns:
> `ALTER TABLE purchase_wishlist ADD COLUMN IF NOT EXISTS finance_mode TEXT DEFAULT 'income', ADD COLUMN IF NOT EXISTS finance_assets TEXT, ADD COLUMN IF NOT EXISTS sold_assets TEXT, ADD COLUMN IF NOT EXISTS target_date DATE, ADD COLUMN IF NOT EXISTS monthly_contribution NUMERIC, ADD COLUMN IF NOT EXISTS saved NUMERIC;`

### Cash / funds table (liquid funds)

Backs the **Cash** page (`/cash`). One row per cash stash or bank balance;
foreign balances (e.g. KWD) convert to ₹ live. Counts toward net worth and
"Liquid now".

```sql
CREATE TABLE IF NOT EXISTS cash_funds (
  id             TEXT PRIMARY KEY,
  owner          TEXT,                  -- whose funds
  type           TEXT DEFAULT 'bank',   -- cash | bank
  "where"        TEXT,                  -- bank name or location (quoted: reserved word)
  account_label  TEXT,                  -- e.g. ••1234
  balance        NUMERIC,               -- in its own currency
  currency       TEXT DEFAULT 'INR',    -- INR | KWD | ...
  as_of_date     DATE,                  -- when the balance was last updated
  note           TEXT,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_cash_owner ON cash_funds (owner);

-- backend uses the service-role key:
--   ALTER TABLE cash_funds DISABLE ROW LEVEL SECURITY;
```

### Income receipts table (home dashboard + expense ticks)

Backs the dashboard's "did the money actually arrive?" check **and** the expense
"paid this month" ticks — one row per confirmed receipt/payment per month.
`on_date` records the date an expense was actually paid (or the day it fell).

```sql
CREATE TABLE IF NOT EXISTS income_receipts (
  period      TEXT NOT NULL,      -- 'YYYY-MM'
  key         TEXT NOT NULL,      -- rent:apartments:<id> | coupon:<id>:<date> | expense:<id>:<period>
  received    BOOLEAN DEFAULT TRUE,
  amount      NUMERIC,
  on_date     DATE,               -- date the expense was paid / fell (NULL for income)
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (period, key)
);

-- if the table already exists from an earlier version, add the paid-date:
ALTER TABLE income_receipts ADD COLUMN IF NOT EXISTS on_date DATE;

-- backend uses the service-role key:
--   ALTER TABLE income_receipts DISABLE ROW LEVEL SECURITY;
```

### App settings table (dashboard goal/asset assumptions)

Persists the dashboard's asset assumptions (per-class value-growth CAGR, income
yield and sell rule) so they don't reset and follow you across devices. A
generic key → JSON store; the dashboard uses the key `goal`.

```sql
CREATE TABLE IF NOT EXISTS app_settings (
  key         TEXT PRIMARY KEY,
  value       JSONB NOT NULL DEFAULT '{}'::jsonb,
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- backend uses the service-role key:
--   ALTER TABLE app_settings DISABLE ROW LEVEL SECURITY;
```

### Options strategy tracker tables (live P&L + minute history + bookings)

Powers the **Options** page: the legs of the strategy you're tracking, one
combined-P&L snapshot per minute (the performance history), and each booking
(leg square-off) with its fill price. Legs/bookings come from manual entry today
and from Kite once API access is granted — same tables either way.

```sql
-- The legs of the tracked strategy (your trade).
CREATE TABLE IF NOT EXISTS option_strategy_legs (
  id           TEXT PRIMARY KEY,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  underlying   TEXT NOT NULL,
  expiry       DATE NOT NULL,
  strike       NUMERIC NOT NULL,
  opt_type     TEXT NOT NULL,           -- CE | PE
  side         TEXT NOT NULL,           -- BUY | SELL
  lots         INTEGER NOT NULL,
  lot_size     INTEGER NOT NULL,
  qty          INTEGER NOT NULL,
  entry_price  NUMERIC NOT NULL,
  entry_iv     NUMERIC,                 -- frozen at entry for the BS mark
  entry_spot   NUMERIC,
  status       TEXT NOT NULL DEFAULT 'open',   -- open | booked
  exit_price   NUMERIC,
  booked_at    TIMESTAMPTZ
);

-- Each square-off, with the price it was booked at (the chart markers).
CREATE TABLE IF NOT EXISTS option_strategy_bookings (
  id            TEXT PRIMARY KEY,
  leg_id        TEXT,
  ts            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  tradingsymbol TEXT,
  side          TEXT,
  qty           INTEGER,
  price         NUMERIC NOT NULL,
  realised      NUMERIC,
  trade_id      TEXT,                   -- Kite trade id (dedupe key in live mode)
  source        TEXT DEFAULT 'manual'
);

-- One combined-P&L row per minute = the per-day performance history (the graph).
CREATE TABLE IF NOT EXISTS option_strategy_snapshots (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  ts             TIMESTAMPTZ NOT NULL,
  day            DATE NOT NULL,            -- IST trading day (drives the day navigator)
  strategy_key   TEXT NOT NULL DEFAULT 'combined',
  booked_pnl     NUMERIC,                  -- realised (header number)
  unrealised_pnl NUMERIC,                  -- subheader number
  total_pnl      NUMERIC NOT NULL,
  spot           NUMERIC,
  margin_used    NUMERIC,
  cash_used      NUMERIC,                  -- opening cash − live cash (loss coverage)
  source         TEXT,
  leg_count      INTEGER
);
CREATE INDEX IF NOT EXISTS idx_option_snapshots_ts  ON option_strategy_snapshots (ts);
CREATE INDEX IF NOT EXISTS idx_option_snapshots_day ON option_strategy_snapshots (day);

-- Every fill (entries + square-offs) = the trades table + chart markers.
CREATE TABLE IF NOT EXISTS option_strategy_trades (
  id            TEXT PRIMARY KEY,
  trade_id      TEXT,                      -- Kite trade id (dedupe key)
  order_id      TEXT,
  ts            TIMESTAMPTZ NOT NULL,
  day           DATE NOT NULL,
  tradingsymbol TEXT,
  side          TEXT,                      -- BUY | SELL
  qty           INTEGER,
  price         NUMERIC,                   -- bought/sold price
  ltp           NUMERIC,                   -- LTP at capture
  realised      NUMERIC,
  source        TEXT DEFAULT 'kite'
);
CREATE INDEX IF NOT EXISTS idx_option_trades_day ON option_strategy_trades (day);

-- backend uses the service-role key:
--   ALTER TABLE option_strategy_legs      DISABLE ROW LEVEL SECURITY;
--   ALTER TABLE option_strategy_bookings  DISABLE ROW LEVEL SECURITY;
--   ALTER TABLE option_strategy_snapshots DISABLE ROW LEVEL SECURITY;
--   ALTER TABLE option_strategy_trades    DISABLE ROW LEVEL SECURITY;
```

#### Already created the first version of these tables? Run this upgrade

The snapshot table gained per-day + booked/unrealised + funds columns, and the
trades table is new. `CREATE TABLE IF NOT EXISTS` won't alter an existing table,
so run this once in Supabase → SQL Editor:

```sql
ALTER TABLE option_strategy_snapshots ADD COLUMN IF NOT EXISTS day            DATE;
ALTER TABLE option_strategy_snapshots ADD COLUMN IF NOT EXISTS booked_pnl     NUMERIC;
ALTER TABLE option_strategy_snapshots ADD COLUMN IF NOT EXISTS unrealised_pnl NUMERIC;
ALTER TABLE option_strategy_snapshots ADD COLUMN IF NOT EXISTS margin_used    NUMERIC;
ALTER TABLE option_strategy_snapshots ADD COLUMN IF NOT EXISTS cash_used      NUMERIC;
UPDATE option_strategy_snapshots SET day = ts::date WHERE day IS NULL;
CREATE INDEX IF NOT EXISTS idx_option_snapshots_day ON option_strategy_snapshots (day);

CREATE TABLE IF NOT EXISTS option_strategy_trades (
  id            TEXT PRIMARY KEY,
  trade_id      TEXT,
  order_id      TEXT,
  ts            TIMESTAMPTZ NOT NULL,
  day           DATE NOT NULL,
  tradingsymbol TEXT,
  side          TEXT,
  qty           INTEGER,
  price         NUMERIC,
  ltp           NUMERIC,
  realised      NUMERIC,
  source        TEXT DEFAULT 'kite'
);
CREATE INDEX IF NOT EXISTS idx_option_trades_day ON option_strategy_trades (day);
-- ALTER TABLE option_strategy_trades DISABLE ROW LEVEL SECURITY;
```

## 3 · Grab your keys

In the Supabase dashboard:

1. Left sidebar → **Project Settings** (gear icon) → **API**
2. Copy these two values:
   - **Project URL** → goes into `SUPABASE_URL`
   - **service_role key** → goes into `SUPABASE_SERVICE_KEY` (full read/write, server-only — never expose to the browser!)

> ⚠️ The `service_role` key bypasses RLS. Keep it on the backend only.
> The `anon` key is fine for `SUPABASE_KEY` if you have RLS policies set up.

## 4 · Add the env vars

### On Railway (production)

1. Open your service → **Variables** tab → **+ New Variable**
2. Add:
   ```
   SUPABASE_URL          = https://<project-ref>.supabase.co
   SUPABASE_SERVICE_KEY  = eyJhbG...your service-role key...
   ```
3. Click **Deploy** (or wait for auto-redeploy)

### Locally (development)

Edit your `.env`:

```
SUPABASE_URL=https://<project-ref>.supabase.co
SUPABASE_SERVICE_KEY=eyJhbG...
```

Restart the backend.

## 5 · Verify

When the app boots with Supabase configured, the deploy log will show:

```
✓ Supabase store active (https://xxxxx.supabase.co)
```

If you see this in your local terminal or Railway logs, you're done. Create a
new covered-call position from the UI — it'll appear in Supabase's Table
Editor immediately. Redeploy your container — the position is still there.

If you instead see no Supabase line and positions reset on redeploy, it
means the env vars weren't picked up. Double-check spelling (no typos in
`SUPABASE_URL` / `SUPABASE_SERVICE_KEY`).

## What's stored where

| Table column | What it holds |
|---|---|
| `id` | 8-char short uuid (matches the JSON store) |
| `name` | "Nifty CC — May 2026" — your label for the position |
| `status` | `active` or `closed` |
| `underlying` | Always `NIFTY` for now |
| `shares`, `niftybees_entry_price`, `niftybees_cost` | The NB leg |
| `lots`, `lot_size` | The option leg's size |
| `active_call` (JSONB) | Currently open short call, or NULL |
| `call_history` (JSONB) | Array of every closed cycle (with close_kind, nb_action, pnl, etc.) |
| `total_premium_collected` | Running sum of all premiums received |
| `notes` | User-entered notes |
| `tags` | TEXT[] array of user-applied labels (e.g. `{hedge, jun-cycle}`) |
| `created_at`, `closed_at` | Timestamps |

The two JSONB columns let us add new per-cycle fields (e.g. `close_kind`,
`nb_realised_pnl`) without ever migrating the schema.

## Switching back to JSON

Remove the `SUPABASE_URL` / `SUPABASE_SERVICE_KEY` env vars and redeploy. The
app will silently fall back to `api/cc_positions.json`. Existing Supabase data
isn't affected — it stays in the cloud, ready to be re-enabled.

## Cost

Supabase free tier:
- 500MB database (this app uses < 1MB even with 1000s of cycles)
- 2GB egress / month
- 50,000 monthly active users

You will not pay anything for this use case.

---

## 6 · Auth tables (email-OTP sign-in)

The email-OTP / trusted-device sign-in (`api/auth/`) persists pending codes and
trusted devices. Run this once in **Supabase → SQL Editor**. Until you do, the
app transparently falls back to local JSON (`api/auth_data/*.json`) — fine for
dev, but devices reset on every redeploy in production, so run it before going live.

```sql
-- Pending one-time codes (hashed; auto-replaced per email)
create table if not exists public.auth_otp (
  email       text primary key,
  code_hash   text not null,
  expires_at  timestamptz not null,
  attempts    int  not null default 0,
  created_at  timestamptz not null default now()
);

-- Trusted devices: long-lived cookie token (hashed) + PIN (hashed) + settings
create table if not exists public.auth_devices (
  device_id     text primary key,
  email         text not null,
  token_hash    text not null,
  pin_hash      text,
  pin_salt      text,
  pin_attempts  int  not null default 0,
  lock_minutes  int  not null default 10,
  expires_at    timestamptz not null,
  created_at    timestamptz not null default now(),
  last_used     timestamptz,
  revoked       boolean not null default false
);
create index if not exists auth_devices_email_idx on public.auth_devices (email);
```

These tables are written only by the backend using the **service-role key**, so
leave Row Level Security as-is (the service key bypasses RLS). No secrets are
stored in the clear — OTP codes, PINs, and the device token are all hashed.

### Required environment variables

| Variable | Purpose |
|---|---|
| `AUTH_SECRET` | Signs access tokens. Auto-generated + persisted if blank; set explicitly in prod to survive redeploys. |
| `RESEND_API_KEY` | Sends OTP emails via Resend. Blank → codes print to the server log (dev). |
| `OTP_FROM` | Sender address (default `onboarding@resend.dev`). |
| `OTP_ALLOWLIST` | Comma-separated emails allowed to sign in. |

---

## REQUIRED · Durable KV (`app_cache`) — persists F&O statements, tradebooks, more

⚠️ **Run this one.** `app_cache` is the durable key-value store behind a growing
list of features: the F&O **P&L statements** and **tradebook** history, the
**corporate-action** upload log, strategy pins, the price-feed selection, and the
live-quote snapshot. Without the table these all silently fall back to a local
JSON file (`api/portfolio_data/app_cache.json`) that survives a same-instance
restart but **NOT** Railway redeploys or a second replica — so imports appear to
work, then vanish on refresh (a different replica / after a deploy). Create it
once and everything KV-backed becomes durable and shared across deploys/replicas:

```sql
-- Generic durable KV cache (used for the live-price snapshot; reusable)
CREATE TABLE IF NOT EXISTS app_cache (
  key         TEXT PRIMARY KEY,
  value       JSONB NOT NULL,
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

Nothing else to configure — the app upserts to it automatically once it exists.

---

## Optional · Per-stock Screener.in links (`portfolio_screener_links`)

Clicking a holding's logo on the Stocks page opens its Screener.in page. Links
are derived automatically (`screener.in/company/{SYMBOL}/`) and any you edit are
saved. Without this table they persist to a local JSON file
(`api/portfolio_data/screener_links.json`) — fine locally, but **not** across
Railway redeploys. Create the table so your links survive deploys/replicas:

```sql
CREATE TABLE IF NOT EXISTS portfolio_screener_links (
  symbol      TEXT PRIMARY KEY,
  url         TEXT NOT NULL,
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

After creating it, click **Set links** on the Stocks page (or call
`POST /api/equity/screener-links/seed`) once to populate the ~800 derived links.

---

## Bond payment status (`bond_payment_status`) — "mark received"

Marking a bond coupon/principal payment received / not-received on the Bonds page
saves here. Without the table it falls back to a local JSON file
(`api/bonds_data/bond_payment_status.json`) — fine locally, but **not** across
Railway redeploys. Create the table so marks persist in the cloud:

```sql
CREATE TABLE IF NOT EXISTS bond_payment_status (
  bond_id     TEXT NOT NULL,
  date        DATE NOT NULL,
  status      TEXT NOT NULL DEFAULT 'pending',
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (bond_id, date)   -- required for the (bond_id, date) upsert
);
```

The app picks it up automatically once it exists — no restart needed.

---

## Reminder overrides (`reminder_overrides`) — "move a date / mark done"

The Reminders page shows a live, computed feed (bond payouts, FD interest, loan
EMIs, income & expenses). The only thing persisted is the small per-reminder
override a user applies — a **moved date** or a **done / skipped** tick. Without
the table it falls back to a local JSON file (`api/reminders_data/overrides.json`)
— fine locally, but **not** across Railway redeploys. Create it so tweaks stick:

```sql
CREATE TABLE IF NOT EXISTS reminder_overrides (
  key         TEXT PRIMARY KEY,          -- the reminder's stable key (source:id:date)
  due_date    DATE,                      -- moved date, or NULL to keep the computed one
  status      TEXT,                      -- 'done' | 'skipped' | NULL (= pending default)
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

The reminders feed renders fully even without this table — only the moved dates
and done ticks won't survive a cloud redeploy until it exists. No restart needed
once created.

---

## Liabilities (loans) table

The Loans / Liabilities page stores each loan (home, personal, vehicle, foreign,
flexible…) here. Without the table it falls back to a local JSON file
(`api/loans_data/loans.json`) — fine locally, but **invisible in production**
(Railway's filesystem is ephemeral and starts empty). Create it so loans persist
and show up in the cloud:

```sql
CREATE TABLE IF NOT EXISTS loans (
  id                      TEXT PRIMARY KEY,
  owner                   TEXT,
  lender                  TEXT NOT NULL,
  loan_type               TEXT,
  account_no              TEXT,
  currency                TEXT DEFAULT 'INR',
  original_amount         NUMERIC,
  outstanding_principal   NUMERIC,
  interest_rate           NUMERIC,
  emi_amount              NUMERIC,
  emi_frequency           TEXT DEFAULT 'monthly',
  start_date              DATE,
  maturity_date           DATE,
  next_installment_date   DATE,
  installments_paid       INTEGER,
  installments_remaining  INTEGER,
  status                  TEXT DEFAULT 'active',
  closed_on               DATE,
  note                    TEXT,
  created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_loans_owner ON loans (owner);
```

The app picks it up automatically once it exists — no restart needed.

### Loan installment status (`loan_payment_status`) — "mark paid"

The Loans → Repayments calendar lets you tap an installment to mark it **paid /
pending / unpaid**. The repayment ladder is computed from each loan's EMI + dates,
so there's no row to hang a status on — instead we keep one sparse row per marked
installment here. Without the table it falls back to a local JSON file
(`api/loans_data/loan_payment_status.json`) — fine locally, but resets on every
Railway deploy. Create it so marks persist in the cloud:

```sql
CREATE TABLE IF NOT EXISTS loan_payment_status (
  loan_id  TEXT NOT NULL,
  date     DATE NOT NULL,
  status   TEXT NOT NULL DEFAULT 'pending',   -- paid | pending | unpaid
  PRIMARY KEY (loan_id, date)
);
```

Only non-default (paid / unpaid) marks are stored; clearing back to pending
deletes the row. The app picks it up automatically once it exists.

---

## F&O tab (`fno_*`) — live Zerodha F&O P&L, strategies, calendar & minute history

The **F&O** tab connects one or more Zerodha accounts through the paid Kite
Connect app (`KITE_API_KEY`/`KITE_API_SECRET` in `.env` by default; a per-account
key/secret can also be stored). It tracks per-strategy P&L (Sentinel short
straddle, Crude oil, other), keeps a daily P&L calendar, stores a 1-minute P&L
snapshot series for the intraday chart, logs every Kite login, and keeps the raw
trade log (synced live from Kite + backfilled via a Console tradebook import).
Without these tables everything falls back to local JSON files under
`api/fno_data/` — fine locally, but wiped on every Railway deploy.

```sql
-- One row per connected Zerodha F&O account. api_key/api_secret may be blank →
-- the backend falls back to the paid app creds in the environment.
CREATE TABLE IF NOT EXISTS fno_accounts (
  id               TEXT PRIMARY KEY,
  person           TEXT,
  account_label    TEXT NOT NULL,
  api_key          TEXT,
  api_secret       TEXT,
  access_token     TEXT,
  kite_user_id     TEXT,
  user_name        TEXT,
  status           TEXT DEFAULT 'pending',    -- pending | connected | expired
  token_updated_at TIMESTAMPTZ,
  last_synced      TIMESTAMPTZ,
  note             TEXT,
  price_feed       BOOLEAN DEFAULT FALSE,     -- the ONE paid account whose Kite session provides live prices for all
  strategy         TEXT,                       -- pins ALL this account's non-crude P&L to one strategy label (e.g. 'ram')
  created_at       TIMESTAMPTZ DEFAULT NOW()
);
-- Existing installs: add the live-price-feed flag if the table predates it.
ALTER TABLE fno_accounts ADD COLUMN IF NOT EXISTS price_feed BOOLEAN DEFAULT FALSE;

-- Per-account strategy label. This is the SINGLE SOURCE OF TRUTH the live engine
-- and the daily rebuild READ (never compute) to label an account's P&L. It lives
-- on the account row so it can't be raced away like the old shared KV blob was.
-- Crude oil (MCX CRUDEOIL*) always stays 'crude'; a per-leg pin still wins for a
-- specific open leg. Set the labels for the current accounts in the same run:
ALTER TABLE fno_accounts ADD COLUMN IF NOT EXISTS strategy TEXT;
UPDATE fno_accounts SET strategy = 'sentinel' WHERE id = '3401406dbae9';  -- Ranjeev
UPDATE fno_accounts SET strategy = 'ram'      WHERE id = 'bbc9254a5a30';  -- Maha
UPDATE fno_accounts SET strategy = 'ram'      WHERE id = 'a1d174ded0b2';  -- Sanjeev

-- Audit log of every Kite login-related event (URL issued, connected, expiry…).
CREATE TABLE IF NOT EXISTS fno_login_log (
  id          TEXT PRIMARY KEY,
  account_id  TEXT,
  event       TEXT NOT NULL,                  -- login_url_issued | connected | login_failed | token_expired
  detail      TEXT,
  created_at  TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_fno_login_log_at ON fno_login_log (created_at DESC);

-- Raw F&O fills — synced from kite.trades() during the day and/or imported from
-- a Zerodha Console tradebook export. trade_id de-dupes both sources.
CREATE TABLE IF NOT EXISTS fno_trades (
  id               TEXT PRIMARY KEY,
  account_id       TEXT NOT NULL,
  trade_id         TEXT NOT NULL,
  order_id         TEXT,
  strategy         TEXT DEFAULT 'other',      -- sentinel | crude | other (auto-classified, editable)
  tradingsymbol    TEXT NOT NULL,
  exchange         TEXT,                      -- NFO | MCX | BFO …
  instrument_type  TEXT,                      -- CE | PE | FUT
  transaction_type TEXT,                      -- BUY | SELL
  quantity         NUMERIC NOT NULL DEFAULT 0,
  price            NUMERIC NOT NULL DEFAULT 0,
  product          TEXT,
  trade_date       DATE NOT NULL,
  fill_ts          TIMESTAMPTZ,
  source           TEXT DEFAULT 'kite',       -- kite | import
  UNIQUE (account_id, trade_id)
);
CREATE INDEX IF NOT EXISTS idx_fno_trades_date ON fno_trades (trade_date);

-- One row per account × day × strategy — the daily P&L calendar reads this.
-- Live rows (source='live') are written every minute from Kite positions while
-- the market is open; source='trades' rows are rebuilt from the trade log for
-- backfilled history and never overwrite a live row.
CREATE TABLE IF NOT EXISTS fno_daily_pnl (
  id           TEXT PRIMARY KEY,
  account_id   TEXT NOT NULL,
  date         DATE NOT NULL,
  strategy     TEXT NOT NULL DEFAULT 'other',
  realized     NUMERIC DEFAULT 0,
  unrealized   NUMERIC DEFAULT 0,
  total        NUMERIC NOT NULL DEFAULT 0,
  trades_count INTEGER DEFAULT 0,
  source       TEXT DEFAULT 'live',           -- live | trades
  updated_at   TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE (account_id, date, strategy)
);
CREATE INDEX IF NOT EXISTS idx_fno_daily_date ON fno_daily_pnl (date);

-- 1-minute P&L snapshots (the intraday chart's history; the live chart adds
-- per-second points on top from the WebSocket, which are NOT stored).
CREATE TABLE IF NOT EXISTS fno_pnl_snapshots (
  id          TEXT PRIMARY KEY,
  account_id  TEXT NOT NULL,
  ts          TIMESTAMPTZ NOT NULL,
  date        DATE NOT NULL,
  day_pnl     NUMERIC NOT NULL DEFAULT 0,
  by_strategy JSONB,
  UNIQUE (account_id, ts)
);
CREATE INDEX IF NOT EXISTS idx_fno_snapshots_date ON fno_pnl_snapshots (date);

-- The backend uses the service-role key, so RLS can stay off:
--   ALTER TABLE fno_accounts      DISABLE ROW LEVEL SECURITY;
--   ALTER TABLE fno_login_log     DISABLE ROW LEVEL SECURITY;
--   ALTER TABLE fno_trades        DISABLE ROW LEVEL SECURITY;
--   ALTER TABLE fno_daily_pnl     DISABLE ROW LEVEL SECURITY;
--   ALTER TABLE fno_pnl_snapshots DISABLE ROW LEVEL SECURITY;
```

The app picks the tables up automatically once they exist — no restart needed.

### Always-on minute capture (pg_cron heartbeat)

The backend already records a 1-minute P&L snapshot for every open position via a
background daemon — but that daemon only runs while the web process is awake. On
Railway a service can idle/sleep when nobody's using it, which freezes the
daemon, so minutes only get stored while someone has the app open. To capture
**every minute even when the app is closed**, drive the capture from Supabase
(always-on) with a per-minute cron that pings the auth-exempt endpoint
`GET /api/fno/cron/tick`. The endpoint refreshes positions, marks them to the
live LTP, writes the minute snapshot + that day's total, and prunes completed
days — all server-side, so it works with nobody watching.

```sql
-- run once, in the Supabase SQL editor
create extension if not exists pg_cron;
create extension if not exists pg_net;

-- (recommended) set FNO_CRON_KEY in Railway → Variables to any random string,
-- then put the SAME value in the URL below. If you skip it the endpoint is open.
select cron.schedule(
  'fno-minute-capture',
  '* 3-11 * * 1-5',                         -- every minute, 03:00–11:59 UTC = 08:30–17:29 IST, Mon–Fri
  $$
  select net.http_get(
    'https://YOUR-APP.up.railway.app/api/fno/cron/tick?key=YOUR_FNO_CRON_KEY'
  );
  $$
);
-- to change/stop it later:  select cron.unschedule('fno-minute-capture');
-- to inspect runs:          select * from cron.job_run_details order by end_time desc limit 20;
```

Notes:
- `net.http_get` returns immediately (async), so the cron never blocks the DB.
- The endpoint is idempotent — snapshots upsert on `(account, minute)`, so if the
  in-process daemon and the cron both fire the same minute they converge to one
  row. Outside market hours (or on weekends) the tick is a cheap no-op.
- Capture still needs a **valid Kite access token** for the day. Kite tokens
  expire every morning and re-login needs your 2FA, so log in once each morning;
  after that the deployed backend captures all day on its own.
- Not using Supabase pg_cron? Any per-minute HTTP pinger works the same
  (cron-job.org, an UptimeRobot 1-min monitor, a Railway cron service, …) — just
  hit the same URL.
