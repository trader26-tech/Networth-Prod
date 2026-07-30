-- ============================================================================
-- networth.io — full Supabase schema
-- Generated from SUPABASE.md (all CREATE TABLE + migration blocks, in doc order).
-- Run this whole file once in: Supabase -> SQL Editor -> New query -> Run.
-- Idempotent: every statement uses IF NOT EXISTS, so re-running is safe.
--
-- EXCLUDED (do separately, only if you need them):
--   * pg_cron 'fno-minute-capture' heartbeat  -> needs a public app URL (prod only)
--   * the alternative 'service role full access' RLS policy -> we DISABLE RLS instead
-- ============================================================================


-- ---------------------------------------------------------------------------
-- [1] 2 · Create the table (1 minute)
-- ---------------------------------------------------------------------------
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

-- ---------------------------------------------------------------------------
-- [2] Already running an older version? Add the tags column
-- ---------------------------------------------------------------------------
ALTER TABLE covered_call_positions
  ADD COLUMN IF NOT EXISTS tags TEXT[] NOT NULL DEFAULT '{}';

CREATE INDEX IF NOT EXISTS idx_cc_tags
  ON covered_call_positions USING GIN (tags);

-- ---------------------------------------------------------------------------
-- [3] Hedges table (long protective puts)
-- ---------------------------------------------------------------------------
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

-- ---------------------------------------------------------------------------
-- [4] If you get a Row-Level Security error on insert
-- ---------------------------------------------------------------------------
ALTER TABLE hedge_positions DISABLE ROW LEVEL SECURITY;

-- ---------------------------------------------------------------------------
-- [5] Land net-worth tables (real-estate parcels + documents)
-- ---------------------------------------------------------------------------
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

-- ---------------------------------------------------------------------------
-- [6] Land net-worth tables (real-estate parcels + documents)
-- ---------------------------------------------------------------------------
ALTER TABLE land_parcels ADD COLUMN IF NOT EXISTS owner     TEXT;
ALTER TABLE land_parcels ADD COLUMN IF NOT EXISTS area_sqft NUMERIC;

-- ---------------------------------------------------------------------------
-- [7] Apartment net-worth tables (rented flats/units + documents)
-- ---------------------------------------------------------------------------
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

-- ---------------------------------------------------------------------------
-- [8] Apartment net-worth tables (rented flats/units + documents)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS apartment_tenants (
  id TEXT PRIMARY KEY,
  apartment_id TEXT NOT NULL REFERENCES apartment_units(id) ON DELETE CASCADE,
  name TEXT NOT NULL, phone TEXT, advance_paid NUMERIC,
  move_in_date DATE, move_out_date DATE, notes TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_apt_tenant_unit ON apartment_tenants (apartment_id);

-- ---------------------------------------------------------------------------
-- [9] App password gate (single shared password)
-- ---------------------------------------------------------------------------
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

-- ---------------------------------------------------------------------------
-- [10] Land + Build tables (self-built properties — land + construction legs)
-- ---------------------------------------------------------------------------
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

-- ---------------------------------------------------------------------------
-- [11] Gold / Silver tables (precious-metal pieces + documents)
-- ---------------------------------------------------------------------------
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

-- ---------------------------------------------------------------------------
-- [12] Gold / Silver tables (precious-metal pieces + documents)
-- ---------------------------------------------------------------------------
ALTER TABLE gold_items ADD COLUMN IF NOT EXISTS location TEXT;

-- ---------------------------------------------------------------------------
-- [13] Stock portfolio tables (live multi-account Kite holdings)
-- ---------------------------------------------------------------------------
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

-- ---------------------------------------------------------------------------
-- [14] Stock portfolio tables (live multi-account Kite holdings)
-- ---------------------------------------------------------------------------
ALTER TABLE stock_holdings ADD COLUMN IF NOT EXISTS name         TEXT;
ALTER TABLE stock_holdings ADD COLUMN IF NOT EXISTS currency     TEXT DEFAULT 'INR';
ALTER TABLE stock_holdings ADD COLUMN IF NOT EXISTS import_price NUMERIC;
ALTER TABLE stock_holdings ALTER COLUMN symbol DROP NOT NULL;

-- ---------------------------------------------------------------------------
-- [15] Dividend log (calendar + monthly totals)
-- ---------------------------------------------------------------------------
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

-- ---------------------------------------------------------------------------
-- [16] Document vault tables (nested folders + arbitrary files)
-- ---------------------------------------------------------------------------
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

-- ---------------------------------------------------------------------------
-- [17] Stocks tradebook table (equity trades from Zerodha)
-- ---------------------------------------------------------------------------
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

-- ---------------------------------------------------------------------------
-- [18] Brokerage accounts table (per-member start/end → CAGR)
-- ---------------------------------------------------------------------------
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

-- ---------------------------------------------------------------------------
-- [19] Bonds table (income, YTM, maturity)
-- ---------------------------------------------------------------------------
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

-- ---------------------------------------------------------------------------
-- [20] Bonds table (income, YTM, maturity)
-- ---------------------------------------------------------------------------
ALTER TABLE bonds ADD COLUMN IF NOT EXISTS first_payment_date DATE;
ALTER TABLE bonds ADD COLUMN IF NOT EXISTS ytm_input          NUMERIC;
ALTER TABLE bonds ADD COLUMN IF NOT EXISTS schedule           JSONB;
ALTER TABLE bonds ADD COLUMN IF NOT EXISTS sellable_on        DATE;

-- ---------------------------------------------------------------------------
-- [21] Bond payment status (received / pending / not-received)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS bond_payment_status (
  bond_id     TEXT NOT NULL,                 -- references bonds.id
  date        DATE NOT NULL,                 -- the payout date being marked
  status      TEXT NOT NULL DEFAULT 'pending', -- pending | received | not_received
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (bond_id, date)                -- one status per payment; enables upsert
);

-- backend uses the service-role key:
--   ALTER TABLE bond_payment_status DISABLE ROW LEVEL SECURITY;

-- ---------------------------------------------------------------------------
-- [22] Salary / income table (earned income)
-- ---------------------------------------------------------------------------
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

-- ---------------------------------------------------------------------------
-- [23] ULIP policies table (unit-linked insurance)
-- ---------------------------------------------------------------------------
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

-- ---------------------------------------------------------------------------
-- [24] Fixed Deposits table
-- ---------------------------------------------------------------------------
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

-- ---------------------------------------------------------------------------
-- [25] Monthly expenses table
-- ---------------------------------------------------------------------------
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

-- ---------------------------------------------------------------------------
-- [26] Other income table
-- ---------------------------------------------------------------------------
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

-- ---------------------------------------------------------------------------
-- [27] Buy-planner table
-- ---------------------------------------------------------------------------
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

-- ---------------------------------------------------------------------------
-- [28] Cash / funds table (liquid funds)
-- ---------------------------------------------------------------------------
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

-- ---------------------------------------------------------------------------
-- [29] Income receipts table (home dashboard + expense ticks)
-- ---------------------------------------------------------------------------
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

-- ---------------------------------------------------------------------------
-- [30] App settings table (dashboard goal/asset assumptions)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS app_settings (
  key         TEXT PRIMARY KEY,
  value       JSONB NOT NULL DEFAULT '{}'::jsonb,
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- backend uses the service-role key:
--   ALTER TABLE app_settings DISABLE ROW LEVEL SECURITY;

-- ---------------------------------------------------------------------------
-- [31] Options strategy tracker tables (live P&L + minute history + bookings)
-- ---------------------------------------------------------------------------
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

-- ---------------------------------------------------------------------------
-- [32] Already created the first version of these tables? Run this upgrade
-- ---------------------------------------------------------------------------
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

-- ---------------------------------------------------------------------------
-- [33] 6 · Auth tables (email-OTP sign-in)
-- ---------------------------------------------------------------------------
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

-- ---------------------------------------------------------------------------
-- [33b] Multi-user registry (email-validated sign-up)
-- ---------------------------------------------------------------------------
-- One row per person who can sign in. A user is created 'pending' when they
-- first request a code and flips to 'active' only after a correct OTP, so an
-- unverified email never yields a usable account. Written by the backend with
-- the service-role key (RLS disabled below), like the other auth tables.
create table if not exists public.users (
  id            text primary key,
  email         text unique not null,
  display_name  text,
  status        text not null default 'pending',   -- pending | active | suspended
  is_admin      boolean not null default false,
  created_at    timestamptz not null default now(),
  last_login    timestamptz
);
create index if not exists users_email_idx on public.users (lower(email));
alter table if exists public.users disable row level security;

-- ---------------------------------------------------------------------------
-- [34] REQUIRED · Durable KV (`app_cache`) — persists F&O statements, tradebooks, more
-- ---------------------------------------------------------------------------
-- Generic durable KV cache (used for the live-price snapshot; reusable)
CREATE TABLE IF NOT EXISTS app_cache (
  key         TEXT PRIMARY KEY,
  value       JSONB NOT NULL,
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- [35] Optional · Per-stock Screener.in links (`portfolio_screener_links`)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS portfolio_screener_links (
  symbol      TEXT PRIMARY KEY,
  url         TEXT NOT NULL,
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- [36] Bond payment status (`bond_payment_status`) — "mark received"
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS bond_payment_status (
  bond_id     TEXT NOT NULL,
  date        DATE NOT NULL,
  status      TEXT NOT NULL DEFAULT 'pending',
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (bond_id, date)   -- required for the (bond_id, date) upsert
);

-- ---------------------------------------------------------------------------
-- [37] Reminder overrides (`reminder_overrides`) — "move a date / mark done"
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS reminder_overrides (
  key         TEXT PRIMARY KEY,          -- the reminder's stable key (source:id:date)
  due_date    DATE,                      -- moved date, or NULL to keep the computed one
  status      TEXT,                      -- 'done' | 'skipped' | NULL (= pending default)
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- [38] Liabilities (loans) table
-- ---------------------------------------------------------------------------
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

-- ---------------------------------------------------------------------------
-- [39] Loan installment status (`loan_payment_status`) — "mark paid"
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS loan_payment_status (
  loan_id  TEXT NOT NULL,
  date     DATE NOT NULL,
  status   TEXT NOT NULL DEFAULT 'pending',   -- paid | pending | unpaid
  PRIMARY KEY (loan_id, date)
);

-- ---------------------------------------------------------------------------
-- [40] F&O tab (`fno_*`) — live Zerodha F&O P&L, strategies, calendar & minute history
-- ---------------------------------------------------------------------------
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

-- ---------------------------------------------------------------------------
-- [41] Disable Row Level Security on every app table
-- ---------------------------------------------------------------------------
-- The backend talks to Postgres with the SERVICE-ROLE key, which bypasses RLS
-- anyway. These statements make the schema work with the ANON key too, and
-- prevent the 'new row violates row-level security policy' insert errors that
-- SUPABASE.md warns about per-table.
--
-- This is safe here because the database is never exposed to browsers directly:
-- the Angular SPA only ever calls the FastAPI backend, which holds the key.
-- If you ever point a browser client straight at Supabase, re-enable RLS and
-- write real policies instead.

ALTER TABLE IF EXISTS public.covered_call_positions DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.hedge_positions DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.land_parcels DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.land_documents DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.apartment_units DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.apartment_documents DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.apartment_tenants DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.app_auth DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.built_properties DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.built_documents DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.gold_items DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.gold_documents DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.stock_accounts DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.stock_holdings DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.stock_dividends DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.stock_dividend_meta DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.dividend_tds DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.dividend_collected DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.document_folders DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.vault_documents DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.stock_trades DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.brokerage_accounts DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.bonds DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.bond_payment_status DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.salary_entries DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.ulip_policies DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.fd_deposits DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.expenses DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.other_income DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.purchase_wishlist DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.cash_funds DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.income_receipts DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.app_settings DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.option_strategy_legs DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.option_strategy_bookings DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.option_strategy_snapshots DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.option_strategy_trades DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.auth_otp DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.auth_devices DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.app_cache DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.portfolio_screener_links DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.reminder_overrides DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.loans DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.loan_payment_status DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.fno_accounts DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.fno_login_log DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.fno_trades DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.fno_daily_pnl DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.fno_pnl_snapshots DISABLE ROW LEVEL SECURITY;
