-- ===========================================================================
-- Phase 2 backfill: assign all EXISTING rows to your owner account so your
-- current data stays visible once MULTI_USER_ISOLATED is on.
--
-- STEP 1: put your user id here. Find it after you sign in once in multi-user
--   mode (it's users.id for your email), or run:
--     select id, email from public.users order by created_at limit 5;
-- ===========================================================================
-- \set owner_id 'PASTE-YOUR-USER-ID'

-- Then run (replace the literal):

UPDATE public.apartment_documents SET user_id = 'PASTE-YOUR-USER-ID' WHERE user_id IS NULL;
UPDATE public.apartment_tenants SET user_id = 'PASTE-YOUR-USER-ID' WHERE user_id IS NULL;
UPDATE public.apartment_units SET user_id = 'PASTE-YOUR-USER-ID' WHERE user_id IS NULL;
UPDATE public.app_settings SET user_id = 'PASTE-YOUR-USER-ID' WHERE user_id IS NULL;
UPDATE public.bond_payment_status SET user_id = 'PASTE-YOUR-USER-ID' WHERE user_id IS NULL;
UPDATE public.bonds SET user_id = 'PASTE-YOUR-USER-ID' WHERE user_id IS NULL;
UPDATE public.brokerage_accounts SET user_id = 'PASTE-YOUR-USER-ID' WHERE user_id IS NULL;
UPDATE public.built_documents SET user_id = 'PASTE-YOUR-USER-ID' WHERE user_id IS NULL;
UPDATE public.built_properties SET user_id = 'PASTE-YOUR-USER-ID' WHERE user_id IS NULL;
UPDATE public.cash_funds SET user_id = 'PASTE-YOUR-USER-ID' WHERE user_id IS NULL;
UPDATE public.covered_call_positions SET user_id = 'PASTE-YOUR-USER-ID' WHERE user_id IS NULL;
UPDATE public.dividend_collected SET user_id = 'PASTE-YOUR-USER-ID' WHERE user_id IS NULL;
UPDATE public.dividend_tds SET user_id = 'PASTE-YOUR-USER-ID' WHERE user_id IS NULL;
UPDATE public.document_folders SET user_id = 'PASTE-YOUR-USER-ID' WHERE user_id IS NULL;
UPDATE public.expenses SET user_id = 'PASTE-YOUR-USER-ID' WHERE user_id IS NULL;
UPDATE public.family_members SET user_id = 'PASTE-YOUR-USER-ID' WHERE user_id IS NULL;
UPDATE public.fd_deposits SET user_id = 'PASTE-YOUR-USER-ID' WHERE user_id IS NULL;
UPDATE public.fno_accounts SET user_id = 'PASTE-YOUR-USER-ID' WHERE user_id IS NULL;
UPDATE public.fno_daily_pnl SET user_id = 'PASTE-YOUR-USER-ID' WHERE user_id IS NULL;
UPDATE public.fno_pnl_snapshots SET user_id = 'PASTE-YOUR-USER-ID' WHERE user_id IS NULL;
UPDATE public.fno_trades SET user_id = 'PASTE-YOUR-USER-ID' WHERE user_id IS NULL;
UPDATE public.gold_documents SET user_id = 'PASTE-YOUR-USER-ID' WHERE user_id IS NULL;
UPDATE public.gold_items SET user_id = 'PASTE-YOUR-USER-ID' WHERE user_id IS NULL;
UPDATE public.hedge_positions SET user_id = 'PASTE-YOUR-USER-ID' WHERE user_id IS NULL;
UPDATE public.income_receipts SET user_id = 'PASTE-YOUR-USER-ID' WHERE user_id IS NULL;
UPDATE public.land_documents SET user_id = 'PASTE-YOUR-USER-ID' WHERE user_id IS NULL;
UPDATE public.land_parcels SET user_id = 'PASTE-YOUR-USER-ID' WHERE user_id IS NULL;
UPDATE public.loan_payment_status SET user_id = 'PASTE-YOUR-USER-ID' WHERE user_id IS NULL;
UPDATE public.loans SET user_id = 'PASTE-YOUR-USER-ID' WHERE user_id IS NULL;
UPDATE public.option_strategy_bookings SET user_id = 'PASTE-YOUR-USER-ID' WHERE user_id IS NULL;
UPDATE public.option_strategy_legs SET user_id = 'PASTE-YOUR-USER-ID' WHERE user_id IS NULL;
UPDATE public.option_strategy_snapshots SET user_id = 'PASTE-YOUR-USER-ID' WHERE user_id IS NULL;
UPDATE public.option_strategy_trades SET user_id = 'PASTE-YOUR-USER-ID' WHERE user_id IS NULL;
UPDATE public.other_income SET user_id = 'PASTE-YOUR-USER-ID' WHERE user_id IS NULL;
UPDATE public.portfolio_screener_links SET user_id = 'PASTE-YOUR-USER-ID' WHERE user_id IS NULL;
UPDATE public.purchase_wishlist SET user_id = 'PASTE-YOUR-USER-ID' WHERE user_id IS NULL;
UPDATE public.reminder_overrides SET user_id = 'PASTE-YOUR-USER-ID' WHERE user_id IS NULL;
UPDATE public.salary_entries SET user_id = 'PASTE-YOUR-USER-ID' WHERE user_id IS NULL;
UPDATE public.stock_accounts SET user_id = 'PASTE-YOUR-USER-ID' WHERE user_id IS NULL;
UPDATE public.stock_dividend_meta SET user_id = 'PASTE-YOUR-USER-ID' WHERE user_id IS NULL;
UPDATE public.stock_dividends SET user_id = 'PASTE-YOUR-USER-ID' WHERE user_id IS NULL;
UPDATE public.stock_holdings SET user_id = 'PASTE-YOUR-USER-ID' WHERE user_id IS NULL;
UPDATE public.stock_trades SET user_id = 'PASTE-YOUR-USER-ID' WHERE user_id IS NULL;
UPDATE public.ulip_policies SET user_id = 'PASTE-YOUR-USER-ID' WHERE user_id IS NULL;
UPDATE public.vault_documents SET user_id = 'PASTE-YOUR-USER-ID' WHERE user_id IS NULL;
