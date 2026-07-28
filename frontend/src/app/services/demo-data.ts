/**
 * Dummy data for DEMO MODE. Every value here is fictional ("the Kapoor family")
 * and is served entirely from the browser — see demo.interceptor.ts. None of the
 * real user's data is ever read or exposed while demo mode is on.
 *
 * Dates are computed relative to *today* so the sample always looks current.
 */

// ── date helpers (relative to now, so the demo never looks stale) ──────────────
const NOW = new Date();
const iso = (d: Date) => d.toISOString().slice(0, 10);
const ym = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
const addMonths = (n: number, base = NOW) => { const d = new Date(base); d.setMonth(d.getMonth() + n); return d; };
const addDays = (n: number, base = NOW) => { const d = new Date(base); d.setDate(d.getDate() + n); return d; };
const yearsAgo = (y: number) => { const d = new Date(NOW); d.setFullYear(d.getFullYear() - y); return d; };
const THIS_PERIOD = ym(NOW);

const FX = { ok: true, source: 'demo', updated_at: NOW.toISOString(),
  inr_per: { INR: 1, USD: 86.2, EUR: 93.1, GBP: 109.4, AED: 23.5, SGD: 63.8 } };

// ── DASHBOARD AGGREGATE (/dashboard/summary) ──────────────────────────────────
function dashboard() {
  const positions = [
    { asset_class: 'apartments', class_label: 'Apartments', name: 'Marina Heights · 3BHK', owner: 'Arjun', value: 14200000, realisable: 13700000, invested: 9500000, cagr: 0.118, monthly_income: 42000, liquidity_tier: 5, liquidity_days: 120, liquidity_label: 'Illiquid', divisible: false, sub: 'Bandra West' },
    { asset_class: 'apartments', class_label: 'Apartments', name: 'Green Acres · 2BHK', owner: 'Priya', value: 8600000, realisable: 8300000, invested: 6200000, cagr: 0.094, monthly_income: 26000, liquidity_tier: 5, liquidity_days: 120, liquidity_label: 'Illiquid', divisible: false, sub: 'Whitefield' },
    { asset_class: 'land', class_label: 'Land', name: 'Hosur Plot · 2400 sqft', owner: 'Arjun', value: 5400000, realisable: 5200000, invested: 2800000, cagr: 0.151, monthly_income: 0, liquidity_tier: 5, liquidity_days: 180, liquidity_label: 'Illiquid', divisible: true, sub: 'Hosur' },
    { asset_class: 'stocks', class_label: 'Stocks', name: 'Zerodha · Equity', owner: 'Arjun', value: 6850000, realisable: 6850000, invested: 4100000, cagr: 0.187, monthly_income: 0, liquidity_tier: 2, liquidity_days: 3, liquidity_label: 'T+2 days', divisible: true, sub: null },
    { asset_class: 'stocks', class_label: 'Stocks', name: 'Groww · Equity', owner: 'Priya', value: 2950000, realisable: 2950000, invested: 2200000, cagr: 0.132, monthly_income: 0, liquidity_tier: 2, liquidity_days: 3, liquidity_label: 'T+2 days', divisible: true, sub: null },
    { asset_class: 'bonds', class_label: 'Bonds', name: 'Corporate NCD ladder', owner: 'Arjun', value: 2150000, realisable: 2080000, invested: 2000000, cagr: 0.108, monthly_income: 18500, liquidity_tier: 3, liquidity_days: 30, liquidity_label: 'Weeks', divisible: true, sub: '5 bonds' },
    { asset_class: 'fd', class_label: 'Fixed Deposits', name: 'HDFC + ICICI FDs', owner: 'Priya', value: 1850000, realisable: 1850000, invested: 1750000, cagr: 0.071, monthly_income: 10800, liquidity_tier: 3, liquidity_days: 7, liquidity_label: 'Days', divisible: true, sub: '3 FDs' },
    { asset_class: 'gold', class_label: 'Gold & Silver', name: 'Jewellery + coins', owner: 'Priya', value: 2480000, realisable: 2380000, invested: 1550000, cagr: 0.121, monthly_income: 0, liquidity_tier: 4, liquidity_days: 14, liquidity_label: 'Weeks', divisible: true, sub: '310 g gold' },
    { asset_class: 'ulip', class_label: 'ULIP', name: 'HDFC Click2Wealth', owner: 'Arjun', value: 1320000, realisable: 1280000, invested: 1100000, cagr: 0.089, monthly_income: 0, liquidity_tier: 4, liquidity_days: 30, liquidity_label: 'Lock-in', divisible: false, sub: null },
    { asset_class: 'cash', class_label: 'Cash & Funds', name: 'Savings + wallet', owner: 'Arjun', value: 1450000, realisable: 1450000, invested: 1450000, cagr: null, monthly_income: 0, liquidity_tier: 1, liquidity_days: 0, liquidity_label: 'Instant', divisible: true, sub: null },
  ];
  const net = positions.reduce((a, p) => a + p.value, 0);
  const realisable = positions.reduce((a, p) => a + p.realisable, 0);
  const invested = positions.reduce((a, p) => a + (p.invested || 0), 0);
  const monthly_income = positions.reduce((a, p) => a + p.monthly_income, 0) + 95000 /* salary net into income view */;

  const classMap = new Map<string, any>();
  for (const p of positions) {
    const c = classMap.get(p.asset_class) || { asset_class: p.asset_class, label: p.class_label, value: 0, monthly_income: 0, count: 0, cagr: p.cagr, pct: 0, liquidity_tier: p.liquidity_tier, liquidity_label: p.liquidity_label };
    c.value += p.value; c.monthly_income += p.monthly_income; c.count += 1; classMap.set(p.asset_class, c);
  }
  const by_class = Array.from(classMap.values()).map(c => ({ ...c, pct: c.value / net })).sort((a, b) => b.value - a.value);

  const personMap = new Map<string, any>();
  for (const p of positions) {
    const m = personMap.get(p.owner) || { person: p.owner, value: 0, monthly_income: 0, count: 0, class_count: 0, cagr: 0.13, pct: 0, _classes: new Set() };
    m.value += p.value; m.monthly_income += p.monthly_income; m.count += 1; m._classes.add(p.asset_class); personMap.set(p.owner, m);
  }
  const by_person = Array.from(personMap.values()).map(m => ({ person: m.person, value: m.value, monthly_income: m.monthly_income, count: m.count, class_count: m._classes.size, cagr: m.cagr, pct: m.value / net })).sort((a, b) => b.value - a.value);

  const liquidity = [
    { tier: 1, label: 'Instant (cash)', value: 1450000, realisable: 1450000, days: 0 },
    { tier: 2, label: 'T+2 days (stocks)', value: 9800000, realisable: 9800000, days: 3 },
    { tier: 3, label: 'Days–weeks (FD/bonds)', value: 4000000, realisable: 3930000, days: 14 },
    { tier: 4, label: 'Weeks (gold/ULIP)', value: 3800000, realisable: 3660000, days: 21 },
    { tier: 5, label: 'Illiquid (property)', value: 28200000, realisable: 27200000, days: 120 },
  ];

  const income_due = {
    period: THIS_PERIOD,
    items: [
      { key: 'sal-arjun', kind: 'salary', label: 'Salary · Arjun', owner: 'Arjun', amount: 260000, received: true },
      { key: 'sal-priya', kind: 'salary', label: 'Salary · Priya', owner: 'Priya', amount: 185000, received: true },
      { key: 'rent-marina', kind: 'rent', label: 'Rent · Marina Heights', owner: 'Arjun', amount: 42000, received: true },
      { key: 'rent-green', kind: 'rent', label: 'Rent · Green Acres', owner: 'Priya', amount: 26000, received: false },
      { key: 'cpn-ncd', kind: 'interest', label: 'NCD coupon', owner: 'Arjun', amount: 18500, received: false },
      { key: 'fd-int', kind: 'interest', label: 'FD payout · ICICI', owner: 'Priya', amount: 10800, received: false },
    ],
    expected: 542300, received: 487000, pending: 55300, pending_count: 3,
  };

  const salary = {
    monthly_total: 445000, annual_total: 5340000, count: 2, currencies: ['INR'], has_foreign: false,
    by_person: [
      { person: 'Arjun', monthly_inr: 260000, annual_inr: 3120000, count: 1, currencies: ['INR'] },
      { person: 'Priya', monthly_inr: 185000, annual_inr: 2220000, count: 1, currencies: ['INR'] },
    ],
    entries: [], fx: FX,
  };

  const expenses = {
    monthly_total: 168000, annual_total: 2016000, essential: 109000, discretionary: 59000,
    subscriptions_total: 4200, subscriptions_count: 6, count: 14,
    by_category: [
      { category: 'Housing & EMI', monthly_inr: 62000, pct: 0.369 },
      { category: 'Food & Groceries', monthly_inr: 28000, pct: 0.167 },
      { category: 'Education', monthly_inr: 24000, pct: 0.143 },
      { category: 'Transport', monthly_inr: 16000, pct: 0.095 },
      { category: 'Lifestyle', monthly_inr: 21000, pct: 0.125 },
      { category: 'Utilities', monthly_inr: 12800, pct: 0.076 },
      { category: 'Subscriptions', monthly_inr: 4200, pct: 0.025 },
    ],
    by_person: [{ person: 'Arjun', monthly_inr: 96000 }, { person: 'Priya', monthly_inr: 72000 }],
  };

  return {
    net_worth: net, realisable_value: realisable, invested, total_gain: net - invested,
    portfolio_cagr: 0.142, monthly_income, annual_income: monthly_income * 12,
    monthly_expenses: 168000, annual_expenses: 2016000, monthly_surplus: 445000 + 95000 - 168000,
    savings_rate: 0.62, surplus_expenses: 0, planner_committed: 0, spent_this_month: 142500,
    position_count: positions.length, positions, by_class, by_person, liquidity, income_due,
    salary, expenses, spent: { ...expenses, monthly_total: 142500, annual_total: 1710000 },
    other_income: { monthly_total: 14500, annual_total: 174000, by_person: [{ person: 'Arjun', monthly_inr: 9000 }, { person: 'Priya', monthly_inr: 5500 }] },
  };
}

// ── BONDS (/bonds/summary) ────────────────────────────────────────────────────
function bonds() {
  const mk = (issuer: string, owner: string, broker: string, invested: number, ytm: number, rating: string, monthsLeft: number, monthly: number) => {
    const mat = addMonths(monthsLeft);
    const schedule: any[] = [];
    for (let i = 1; i <= Math.min(monthsLeft, 12); i++) {
      const d = addMonths(i); const last = i === monthsLeft;
      schedule.push({ date: iso(d), interest: monthly, principal: last ? invested : 0, total: monthly + (last ? invested : 0) });
    }
    return {
      owner, broker, issuer, bond_type: 'Corporate NCD', isin: 'INE000A0' + Math.floor(invested % 10000),
      rating, tax_free: false, face_value: 1000, quantity: invested / 1000, buy_price: 1000,
      coupon_rate: ytm * 100 - 0.4, coupon_freq: 'monthly', repayment_type: 'bullet',
      purchase_date: iso(yearsAgo(1)), first_payment_date: iso(addMonths(1)), maturity_date: iso(mat),
      ytm_input: ytm * 100, note: 'Demo bond', sellable_on: null,
      id: 'demo-' + issuer.replace(/\W/g, ''), invested, face_total: invested,
      annual_income: monthly * 12, annual_income_net: monthly * 12 * 0.9, monthly_income: monthly, monthly_income_net: monthly * 0.9,
      ytm, current_yield: ytm, capital_recovered: 0, capital_recovered_pct: 0,
      future_principal: invested, principal_outstanding: invested, years_to_maturity: monthsLeft / 12,
      next_payment: { date: iso(addMonths(1)), amount: monthly }, schedule,
    };
  };
  const list = [
    mk('Muthoot Fincorp', 'Arjun', 'Wint Wealth', 500000, 0.112, 'AA-', 14, 4670),
    mk('IIFL Finance', 'Arjun', 'Wint Wealth', 500000, 0.105, 'AA', 22, 4375),
    mk('Navi Finserv', 'Arjun', 'Wint Wealth', 400000, 0.110, 'A+', 18, 3670),
    mk('Edelweiss Housing', 'Priya', 'GoldenPi', 350000, 0.108, 'A+', 28, 3150),
    mk('Krazybee', 'Priya', 'GoldenPi', 250000, 0.118, 'A', 11, 2460),
  ];
  const total_invested = list.reduce((a, b) => a + b.invested, 0);
  const total_monthly = list.reduce((a, b) => a + b.monthly_income, 0);
  const months: Record<string, any> = {};
  for (const b of list) for (const p of b.schedule) {
    const k = p.date.slice(0, 7);
    const m = months[k] || (months[k] = { month: k, total: 0, interest: 0, principal: 0, tds: 0, net: 0, capital_recovered: 0, capital_recovered_pct: 0, count: 0, payments: [] });
    const tds = p.interest * 0.1;
    m.total += p.total; m.interest += p.interest; m.principal += p.principal; m.tds += tds; m.net += p.total - tds; m.count++;
    m.payments.push({ date: p.date, issuer: b.issuer, owner: b.owner, broker: b.broker, tax_free: false, interest: p.interest, principal: p.principal, total: p.total, tds, net: p.total - tds });
  }
  const payment_schedule = Object.values(months).sort((a: any, z: any) => a.month.localeCompare(z.month));
  return {
    bonds: list,
    members: [
      { member: 'Arjun', invested: 1400000, monthly_income: 12715, monthly_income_net: 11443, bonds: 3, ytm: 0.109 },
      { member: 'Priya', invested: 600000, monthly_income: 5610, monthly_income_net: 5049, bonds: 2, ytm: 0.112 },
    ],
    payments_by_account: [
      { owner: 'Arjun', broker: 'Wint Wealth', invested: 1400000, monthly_income: 12715, monthly_income_net: 11443, interest_12m: 152580, principal_12m: 0, net_12m: 137322, bonds: 3 },
      { owner: 'Priya', broker: 'GoldenPi', invested: 600000, monthly_income: 5610, monthly_income_net: 5049, interest_12m: 67320, principal_12m: 0, net_12m: 60588, bonds: 2 },
    ],
    count: list.length, member_count: 2, account_count: 2,
    total_invested, total_monthly_income: total_monthly, total_monthly_income_net: total_monthly * 0.9,
    total_annual_income: total_monthly * 12, total_annual_income_net: total_monthly * 12 * 0.9,
    total_maturity_value: total_invested, total_capital_recovered: 0,
    portfolio_ytm: 0.109, combined_rating: { score: 6.3, label: 'AA-', weighted: total_invested, max: 8, rated_pct: 1 },
    avg_coupon: 10.4, avg_years_to_maturity: 1.6, taxfree_invested: 0,
    payment_schedule, yearly_cashflow: [],
  };
}

// ── APARTMENTS ────────────────────────────────────────────────────────────────
function apartmentUnits() {
  return [
    aptUnit('Marina Heights · 3BHK', 'Arjun', 'Bandra West, Mumbai', 1180, 9500000, 14200000, 13700000, 42000, 8),
    aptUnit('Green Acres · 2BHK', 'Priya', 'Whitefield, Bengaluru', 1020, 6200000, 8600000, 8300000, 26000, 6),
  ];
}
function aptUnit(name: string, owner: string, location: string, sqft: number, bought: number, cur: number, real: number, rent: number, heldY: number): any {
  const bdate = yearsAgo(heldY);
  const gain = cur - bought, cagr = Math.pow(cur / bought, 1 / heldY) - 1;
  return {
    id: 'demo-' + name.replace(/\W/g, ''), name, owner, location, area_sqft: sqft,
    bought_date: iso(bdate), bought_price: bought, current_estimated_price: cur, after_brokerage_price: real,
    monthly_rent: rent, notes: 'Sample property', sellable_on: null, created_at: iso(bdate), updated_at: iso(NOW),
    documents: [], tenants: [{ id: 't1', apartment_id: 'demo', name: owner === 'Arjun' ? 'R. Sharma' : 'K. Nair', phone: '98••••••21', advance_paid: rent * 3, move_in_date: iso(yearsAgo(2)), move_out_date: null, notes: null, created_at: iso(yearsAgo(2)), tenancy_years: 2, active: true }],
    current_tenant: { id: 't1', apartment_id: 'demo', name: owner === 'Arjun' ? 'R. Sharma' : 'K. Nair', phone: '98••••••21', advance_paid: rent * 3, move_in_date: iso(yearsAgo(2)), move_out_date: null, notes: null, created_at: iso(yearsAgo(2)), tenancy_years: 2, active: true },
    holding_years: heldY, gain, gain_pct: gain / bought, cagr, net_cagr: cagr - 0.01,
    annual_rent: rent * 12, rent_yield: (rent * 12) / cur, rent_yield_on_cost: (rent * 12) / bought,
    total_cagr: cagr + 0.03, rate_per_sqft: Math.round(cur / sqft), bought_rate_per_sqft: Math.round(bought / sqft),
  };
}
function apartmentSummary() {
  const u = apartmentUnits();
  const invested = u.reduce((a, x) => a + x.bought_price, 0), cur = u.reduce((a, x) => a + x.current_estimated_price, 0);
  const real = u.reduce((a, x) => a + x.after_brokerage_price, 0), rent = u.reduce((a, x) => a + x.monthly_rent, 0);
  return { unit_count: u.length, invested, current_value: cur, realisable_value: real, total_gain: cur - invested, total_gain_pct: (cur - invested) / invested, monthly_rent: rent, annual_rent: rent * 12, gross_yield: (rent * 12) / cur, blended_appreciation_cagr: 0.108, blended_total_cagr: 0.138, document_count: 4 };
}

// ── GOLD ──────────────────────────────────────────────────────────────────────
function goldItems() {
  const g = (name: string, owner: string, type: 'gold' | 'silver', wt: number, price: number, loc: string) => ({
    id: 'demo-' + name.replace(/\W/g, ''), name, owner, metal_type: type, weight_g: wt, purity_pct: type === 'gold' ? 91.6 : 92.5,
    manual_value: null, purchase_date: iso(yearsAgo(3)), purchase_price: price, location: loc, notes: null, sellable_on: null,
    created_at: iso(yearsAgo(3)), updated_at: iso(NOW), documents: [],
  });
  return [
    g('Wedding necklace set', 'Priya', 'gold', 120, 620000, 'Bank locker'),
    g('Gold coins (10g ×8)', 'Arjun', 'gold', 80, 410000, 'Home safe'),
    g('Bangles (pair)', 'Priya', 'gold', 110, 540000, 'Bank locker'),
    g('Silver pooja set', 'Priya', 'silver', 1400, 110000, 'Home safe'),
  ];
}
function goldPrices() { return { gold_24k_per_g: 7850, silver_per_g: 96.5, usd_inr: 86.2, spot_gold_usd_oz: 2840, spot_silver_usd_oz: 34.8, ok: true, source: 'demo', updated_at: NOW.toISOString() }; }
function goldPerformance() {
  const labels = Array.from({ length: 12 }, (_, i) => ym(addMonths(-11 + i)));
  return { ok: true, gold: { '1y': 0.214, '3y': 0.158, '5y': 0.131 }, silver: { '1y': 0.182, '3y': 0.121, '5y': 0.097 },
    series: { labels, gold: labels.map((_, i) => 6600 + i * 110), silver: labels.map((_, i) => 82 + i * 1.2) }, updated_at: NOW.toISOString() };
}

// ── LAND ──────────────────────────────────────────────────────────────────────
function landParcels() {
  const p = (name: string, owner: string, loc: string, sqft: number, bought: number, cur: number, heldY: number) => {
    const cagr = Math.pow(cur / bought, 1 / heldY) - 1;
    return { id: 'demo-' + name.replace(/\W/g, ''), name, owner, location: loc, area_sqft: sqft, bought_date: iso(yearsAgo(heldY)), bought_price: bought, current_estimated_price: cur, after_brokerage_price: Math.round(cur * 0.97), notes: null, sellable_on: null, created_at: iso(yearsAgo(heldY)), updated_at: iso(NOW), documents: [], holding_years: heldY, gain: cur - bought, gain_pct: (cur - bought) / bought, net_gain: cur - bought, cagr, net_cagr: cagr - 0.005, rate_per_sqft: Math.round(cur / sqft), bought_rate_per_sqft: Math.round(bought / sqft) };
  };
  return [p('Hosur Plot', 'Arjun', 'Hosur, TN', 2400, 2800000, 5400000, 5), p('Devanahalli Plot', 'Priya', 'Devanahalli, KA', 1800, 2100000, 3600000, 4)];
}
function landSummary() {
  const p = landParcels();
  const invested = p.reduce((a, x) => a + x.bought_price, 0), cur = p.reduce((a, x) => a + x.current_estimated_price, 0);
  return { parcel_count: p.length, invested, current_value: cur, realisable_value: Math.round(cur * 0.97), total_gain: cur - invested, total_gain_pct: (cur - invested) / invested, blended_cagr: 0.151, document_count: 3 };
}

// ── STOCKS (brokerage) ────────────────────────────────────────────────────────
function brokerage() {
  const acc = (member: string, broker: string, start: number, end: number, heldY: number) => {
    const cagr = Math.pow(end / start, 1 / heldY) - 1;
    return { id: 'demo-' + member + broker, member, broker, start_date: iso(yearsAgo(heldY)), start_amount: start, end_date: iso(NOW), end_amount: end, note: '', sellable_on: null, years: heldY, gain: end - start, gain_pct: (end - start) / start, cagr };
  };
  const accounts = [acc('Arjun', 'Zerodha', 4100000, 6850000, 4), acc('Priya', 'Groww', 2200000, 2950000, 3), acc('Rohan', 'Upstox', 850000, 1180000, 2)];
  const total_invested = accounts.reduce((a, x) => a + x.start_amount, 0), total_current = accounts.reduce((a, x) => a + x.end_amount, 0);
  return {
    accounts,
    members: [
      { member: 'Arjun', start_amount: 4100000, end_amount: 6850000, gain: 2750000, gain_pct: 0.671, cagr: 0.137, accounts: 1, brokers: ['Zerodha'] },
      { member: 'Priya', start_amount: 2200000, end_amount: 2950000, gain: 750000, gain_pct: 0.341, cagr: 0.103, accounts: 1, brokers: ['Groww'] },
      { member: 'Rohan', start_amount: 850000, end_amount: 1180000, gain: 330000, gain_pct: 0.388, cagr: 0.179, accounts: 1, brokers: ['Upstox'] },
    ],
    count: accounts.length, member_count: 3, total_invested, total_current, total_gain: total_current - total_invested,
    total_gain_pct: (total_current - total_invested) / total_invested, combined_cagr: 0.142,
    best: { member: 'Rohan', broker: 'Upstox', cagr: 0.179 }, worst: { member: 'Priya', broker: 'Groww', cagr: 0.103 },
    earliest_start: iso(yearsAgo(4)), latest_end: iso(NOW),
  };
}

// ── STOCKS (live equity holdings — /equity/*) ─────────────────────────────────
function equityHoldings() {
  const H = (account_id: string, person: string, account_label: string, broker: string,
             symbol: string, name: string, isin: string, quantity: number,
             avg_price: number, last_price: number, dpct: number) => {
    const invested = quantity * avg_price, value = quantity * last_price, pnl = value - invested;
    const day_change = Math.round(value - value / (1 + dpct / 100));
    return { account_id, person, account_label, broker, symbol, name, isin, exchange: 'NSE',
      quantity, avg_price, last_price, invested, value, pnl,
      pnl_pct: invested ? pnl / invested : null, day_change, day_change_pct: dpct, priced: true };
  };
  return [
    H('eq-zer', 'Arjun', 'Zerodha · Equity', 'Zerodha', 'RELIANCE', 'Reliance Industries', 'INE002A01018', 180, 2350, 2980, 1.2),
    H('eq-zer', 'Arjun', 'Zerodha · Equity', 'Zerodha', 'HDFCBANK', 'HDFC Bank', 'INE040A01034', 220, 1480, 1712, 0.6),
    H('eq-zer', 'Arjun', 'Zerodha · Equity', 'Zerodha', 'TCS', 'Tata Consultancy Services', 'INE467B01029', 90, 3300, 4120, -0.4),
    H('eq-zer', 'Arjun', 'Zerodha · Equity', 'Zerodha', 'INFY', 'Infosys', 'INE009A01021', 260, 1320, 1885, 0.9),
    H('eq-grw', 'Priya', 'Groww · Equity', 'Groww', 'ICICIBANK', 'ICICI Bank', 'INE090A01021', 300, 880, 1284, 1.1),
    H('eq-grw', 'Priya', 'Groww · Equity', 'Groww', 'ITC', 'ITC', 'INE154A01025', 700, 360, 486, -0.2),
    H('eq-grw', 'Priya', 'Groww · Equity', 'Groww', 'ASIANPAINT', 'Asian Paints', 'INE021A01026', 60, 3100, 2885, -1.3),
    H('eq-ups', 'Rohan', 'Upstox · Imported', 'Upstox', 'TATAMOTORS', 'Tata Motors', 'INE155A01022', 500, 620, 982, 2.1),
    H('eq-ups', 'Rohan', 'Upstox · Imported', 'Upstox', 'BAJFINANCE', 'Bajaj Finance', 'INE296A01024', 22, 6800, 7455, 0.5),
  ];
}

function equity() {
  const hd = equityHoldings();
  const dpctOf = (value: number, dc: number) => (value - dc) ? (dc / (value - dc)) * 100 : 0;

  // aggregate per symbol → stocks rows
  const symMap = new Map<string, any>();
  for (const h of hd) {
    const s = symMap.get(h.symbol) || { symbol: h.symbol, name: h.name, isin: h.isin, exchange: h.exchange,
      last_price: h.last_price, quantity: 0, invested: 0, value: 0, day_change: 0,
      day_change_pct: h.day_change_pct, accounts: 0, priced: true };
    s.quantity += h.quantity; s.invested += h.invested; s.value += h.value; s.day_change += h.day_change; s.accounts += 1;
    symMap.set(h.symbol, s);
  }
  const stocks = Array.from(symMap.values()).map(s => {
    const pnl = s.value - s.invested;
    return { ...s, avg_price: s.invested / s.quantity, pnl, pnl_pct: s.invested ? pnl / s.invested : null };
  }).sort((a, b) => b.value - a.value);

  // aggregate per account
  const meta: Record<string, any> = {
    'eq-zer': { person: 'Arjun', broker: 'Zerodha', account_label: 'Zerodha · Equity', kind: 'live', status: 'connected', connected: true, api_key_hint: 'ujsk••••' },
    'eq-grw': { person: 'Priya', broker: 'Groww', account_label: 'Groww · Equity', kind: 'live', status: 'connected', connected: true, api_key_hint: '1anr••••' },
    'eq-ups': { person: 'Rohan', broker: 'Upstox', account_label: 'Upstox · Imported', kind: 'imported', status: 'imported', connected: false, api_key_hint: null },
  };
  const accMap = new Map<string, any>();
  for (const h of hd) {
    const a = accMap.get(h.account_id) || { id: h.account_id, ...meta[h.account_id], last_synced: NOW.toISOString(), sellable_on: null, value: 0, invested: 0, day_change: 0, holdings: 0 };
    a.value += h.value; a.invested += h.invested; a.day_change += h.day_change; a.holdings += 1; accMap.set(h.account_id, a);
  }
  const accounts = Array.from(accMap.values()).map(a => ({ ...a, pnl: a.value - a.invested,
    pnl_pct: a.invested ? (a.value - a.invested) / a.invested : null, day_change_pct: dpctOf(a.value, a.day_change) }));

  // aggregate per person
  const perMap = new Map<string, any>();
  for (const h of hd) {
    const p = perMap.get(h.person) || { person: h.person, value: 0, invested: 0, day_change: 0, holdings: 0 };
    p.value += h.value; p.invested += h.invested; p.day_change += h.day_change; p.holdings += 1; perMap.set(h.person, p);
  }
  const by_person = Array.from(perMap.values()).map(p => ({ ...p, pnl: p.value - p.invested,
    pnl_pct: p.invested ? (p.value - p.invested) / p.invested : null, day_change_pct: dpctOf(p.value, p.day_change) }))
    .sort((a, b) => b.value - a.value);

  const total_value = hd.reduce((a, h) => a + h.value, 0);
  const total_invested = hd.reduce((a, h) => a + h.invested, 0);
  const total_pnl = total_value - total_invested;
  const day_change = hd.reduce((a, h) => a + h.day_change, 0);
  const byDay = [...stocks].sort((a, b) => b.day_change_pct - a.day_change_pct);
  const best = byDay[0], worst = byDay[byDay.length - 1], top = stocks[0];
  return {
    accounts, by_person, stocks, holdings_detail: hd,
    account_count: accounts.length, connected_count: accounts.filter(a => a.connected).length, holding_count: hd.length,
    total_value, total_invested, total_pnl, total_pnl_pct: total_invested ? total_pnl / total_invested : null,
    day_change, day_change_pct: dpctOf(total_value, day_change),
    best_today: best ? { symbol: best.symbol, day_change_pct: best.day_change_pct, value: best.value } : null,
    worst_today: worst ? { symbol: worst.symbol, day_change_pct: worst.day_change_pct, value: worst.value } : null,
    top_holding: top ? { symbol: top.symbol, value: top.value } : null,
    price_source: 'demo',
  };
}

function equityPerformance() {
  const eq = equity();
  const n = 27, startV = Math.round(eq.total_invested * 0.98), endV = eq.total_value;
  const points = Array.from({ length: n }, (_, i) => {
    const t = i / (n - 1);
    return {
      date: iso(addDays(-7 * (n - 1 - i))),
      value: Math.round(startV + (endV - startV) * t + Math.sin(i * 0.9) * 38000),
      nifty: Math.round(21800 + (24850 - 21800) * t + Math.sin(i * 1.3) * 140),
    };
  });
  return { period: '1Y', points, symbols: eq.stocks.length, coverage: 1, total_value: endV,
    covered_value: endV, missing_value: 0, coverage_value_pct: 1, missing: [], missing_count: 0,
    start: points[0], end: points[points.length - 1], note: '' };
}

function equityDividends() {
  const d = (months: number, symbol: string, name: string, per_share: number, shares: number, person: string) => ({
    id: 'dv-' + symbol + months, date: iso(addMonths(-months)), symbol, name, per_share, shares,
    amount: Math.round(per_share * shares), person, account_id: null, note: null,
  });
  return [
    d(1, 'ITC', 'ITC', 6.5, 700, 'Priya'),
    d(3, 'RELIANCE', 'Reliance Industries', 9, 180, 'Arjun'),
    d(5, 'TCS', 'Tata Consultancy Services', 24, 90, 'Arjun'),
    d(7, 'ICICIBANK', 'ICICI Bank', 8, 300, 'Priya'),
    d(9, 'INFY', 'Infosys', 18, 260, 'Arjun'),
  ];
}

// ── FD ────────────────────────────────────────────────────────────────────────
function fd() {
  const f = (owner: string, bank: string, principal: number, rate: number, months: number, monthsIn: number) => ({
    id: 'demo-' + bank + principal, owner, bank, principal, interest_rate: rate, start_date: iso(addMonths(-monthsIn)), tenure_months: months,
    compounding_frequency: 'quarterly', payout_type: 'payout', payout_frequency: 'monthly', note: null, sellable_on: null,
    maturity_date: iso(addMonths(months - monthsIn)), matured: false, years_to_maturity: (months - monthsIn) / 12, progress: monthsIn / months,
    annual_interest: principal * rate / 100, effective_yield: rate / 100, tenure_years: months / 12, maturity_amount: Math.round(principal * (1 + rate / 100 * months / 12)),
    current_value: principal, interest_earned: Math.round(principal * rate / 100 * monthsIn / 12), monthly_income: Math.round(principal * rate / 100 / 12),
    payout_per_period: Math.round(principal * rate / 100 / 12), payouts_per_year: 12, dashboard_cagr: rate / 100,
  });
  const fds = [f('Priya', 'HDFC Bank', 800000, 7.1, 36, 10), f('Priya', 'ICICI Bank', 600000, 7.25, 24, 6), f('Arjun', 'SBI', 450000, 6.8, 60, 18)];
  const value = fds.reduce((a, x) => a + x.current_value, 0), monthly = fds.reduce((a, x) => a + x.monthly_income, 0);
  return { count: fds.length, value, principal: value, annual_interest: monthly * 12, monthly_income: monthly, avg_rate: 7.05, next_maturity: iso(addMonths(18)), fds };
}

// ── ULIP ──────────────────────────────────────────────────────────────────────
function ulip() {
  const policies = [{
    id: 'demo-ulip1', owner: 'Arjun', insurer: 'HDFC Life', plan_name: 'Click 2 Wealth', policy_number: 'HL••••3920', life_assured: 'Arjun',
    start_date: iso(yearsAgo(4)), policy_term_years: 15, premium_paying_term_years: 10, premium_amount: 150000, premium_frequency: 'yearly',
    sum_assured: 1500000, fund_value: 1320000, fund_type: 'equity', note: null, sellable_on: null,
    lock_in_end: iso(addMonths(12)), maturity_date: iso(addMonths(132)), locked: true, years_to_maturity: 11, years_to_lock_in_end: 1,
    premiums_paid_count: 4, premiums_total_count: 10, invested: 600000, total_premiums: 1500000, remaining_premiums: 900000, remaining_premiums_count: 6,
    fully_paid: false, annual_outflow: 150000, premiums_per_year: 1, gain: 720000, gain_pct: 1.2, xirr: 0.121,
  }];
  return { count: 1, fund_value: 1320000, invested: 600000, gain: 720000, gain_pct: 1.2, remaining_premiums: 900000, annual_outflow: 150000, sum_assured: 1500000, xirr: 0.121, policies };
}

// ── SALARY ────────────────────────────────────────────────────────────────────
function salaryItems() {
  return [
    { id: 's1', person: 'Arjun', amount: 260000, currency: 'INR', frequency: 'monthly', bank_account: 'HDFC ••21', note: 'Product lead', monthly_native: 260000, monthly_inr: 260000, annual_inr: 3120000, inr_per_unit: 1, is_foreign: false },
    { id: 's2', person: 'Priya', amount: 185000, currency: 'INR', frequency: 'monthly', bank_account: 'ICICI ••07', note: 'Design manager', monthly_native: 185000, monthly_inr: 185000, annual_inr: 2220000, inr_per_unit: 1, is_foreign: false },
  ];
}

// ── EXPENSES / OTHER-INCOME log ───────────────────────────────────────────────
function expenseLog() {
  const e = (name: string, cat: string, amt: number, owner: string, ess: boolean, sub = false) => ({
    id: 'e-' + name.replace(/\W/g, ''), owner, name, category: cat, amount: amt, currency: 'INR', frequency: 'monthly', payment_method: 'UPI', is_subscription: sub, essential: ess, active: true, added: true, on_date: null, note: null, monthly_native: amt, monthly_inr: amt, annual_inr: amt * 12, amount_inr: amt, inr_per_unit: 1, is_foreign: false, one_time: false,
  });
  const entries = [e('Home loan EMI', 'Housing & EMI', 62000, 'Arjun', true), e('Groceries', 'Food & Groceries', 28000, 'Priya', true), e('School fees', 'Education', 24000, 'Arjun', true), e('Car fuel + service', 'Transport', 16000, 'Arjun', true), e('Dining & outings', 'Lifestyle', 21000, 'Priya', false), e('Electricity + water', 'Utilities', 12800, 'Priya', true), e('Netflix + Spotify + more', 'Subscriptions', 4200, 'Arjun', false, true)];
  const monthly = entries.reduce((a, x) => a + x.monthly_inr, 0);
  return {
    period: THIS_PERIOD, count: entries.length, monthly_total: monthly, annual_total: monthly * 12,
    essential: 142800, discretionary: 25200, subscriptions_total: 4200, subscriptions_count: 6, one_time_total: 0,
    by_category: [{ category: 'Housing & EMI', monthly_inr: 62000, pct: 0.369 }, { category: 'Food & Groceries', monthly_inr: 28000, pct: 0.167 }, { category: 'Education', monthly_inr: 24000, pct: 0.143 }, { category: 'Lifestyle', monthly_inr: 21000, pct: 0.125 }, { category: 'Transport', monthly_inr: 16000, pct: 0.095 }, { category: 'Utilities', monthly_inr: 12800, pct: 0.076 }, { category: 'Subscriptions', monthly_inr: 4200, pct: 0.025 }],
    by_person: [{ person: 'Arjun', monthly_inr: 96000 }, { person: 'Priya', monthly_inr: 72000 }],
    by_currency: [{ currency: 'INR', monthly_inr: monthly }], currencies: ['INR'], has_foreign: false, entries, fx: FX,
  };
}
function incomeLog() {
  const i = (source: string, cat: string, amt: number, owner: string) => ({
    id: 'i-' + source.replace(/\W/g, ''), owner, source, category: cat, amount: amt, currency: 'INR', frequency: 'monthly', account: 'HDFC ••21', active: true, added: true, on_date: null, note: null, monthly_native: amt, monthly_inr: amt, annual_inr: amt * 12, amount_inr: amt, inr_per_unit: 1, is_foreign: false, one_time: false,
  });
  const entries = [i('Dividends', 'Dividend', 9000, 'Arjun'), i('Freelance design', 'Business', 5500, 'Priya')];
  const monthly = entries.reduce((a, x) => a + x.monthly_inr, 0);
  return { period: THIS_PERIOD, count: entries.length, monthly_total: monthly, annual_total: monthly * 12, one_time_total: 0, by_category: [{ category: 'Dividend', monthly_inr: 9000, pct: 0.62 }, { category: 'Business', monthly_inr: 5500, pct: 0.38 }], by_person: [{ person: 'Arjun', monthly_inr: 9000 }, { person: 'Priya', monthly_inr: 5500 }], by_currency: [{ currency: 'INR', monthly_inr: monthly }], currencies: ['INR'], has_foreign: false, entries, fx: FX };
}

// ── CASH ──────────────────────────────────────────────────────────────────────
function cash() {
  const c = (owner: string, type: 'cash' | 'bank', where: string, label: string, bal: number) => ({ id: 'c-' + where.replace(/\W/g, ''), owner, type, where, account_label: label, balance: bal, currency: 'INR', as_of_date: iso(NOW), note: null, balance_inr: bal, inr_per_unit: 1, is_foreign: false });
  const entries = [c('Arjun', 'bank', 'HDFC Bank', 'Salary a/c', 920000), c('Priya', 'bank', 'ICICI Bank', 'Savings', 430000), c('Arjun', 'cash', 'Home', 'Wallet & home', 100000)];
  const total = entries.reduce((a, x) => a + x.balance_inr, 0);
  return { count: entries.length, total, cash: 100000, bank: 1350000, by_person: [{ person: 'Arjun', balance_inr: 1020000 }, { person: 'Priya', balance_inr: 430000 }], currencies: ['INR'], has_foreign: false, entries, fx: FX };
}

// ── NETWORTH detail tab ───────────────────────────────────────────────────────
function networthSummary() {
  const d = dashboard();
  return {
    net_worth: d.net_worth, total_assets: d.net_worth, total_liabilities: 0, monthly_income: d.monthly_income, annual_income: d.annual_income,
    weighted_cagr: 0.142, asset_count: d.position_count,
    by_class: d.by_class.map((c: any) => ({ asset_class: c.asset_class, label: c.label, liability: false, value: c.value, monthly_income: c.monthly_income, asset_count: c.count, cagr: c.cagr, pct: c.pct })),
    by_person: d.by_person.map((p: any) => ({ person: p.person, value: p.value, monthly_income: p.monthly_income, asset_count: p.count, pct: p.pct })),
    people: [{ name: 'Arjun', risk_profile: 'moderate', color: '#387ed1' }, { name: 'Priya', risk_profile: 'conservative', color: '#16a085' }, { name: 'Rohan', risk_profile: 'aggressive', color: '#e0892a' }],
  };
}
function networthMeta() {
  return { asset_classes: [
    { key: 'apartments', label: 'Apartments', default_risk: 'low', liability: false },
    { key: 'land', label: 'Land', default_risk: 'low', liability: false },
    { key: 'stocks', label: 'Stocks', default_risk: 'high', liability: false },
    { key: 'bonds', label: 'Bonds', default_risk: 'low', liability: false },
    { key: 'gold', label: 'Gold & Silver', default_risk: 'medium', liability: false },
    { key: 'fd', label: 'Fixed Deposits', default_risk: 'low', liability: false },
    { key: 'ulip', label: 'ULIP', default_risk: 'medium', liability: false },
    { key: 'cash', label: 'Cash & Funds', default_risk: 'low', liability: false },
  ], risk_levels: ['low', 'medium', 'high'], import_fields: [{ key: 'name', label: 'Name' }, { key: 'value', label: 'Value' }] };
}

// ── router: path → dummy body ─────────────────────────────────────────────────
export function demoResponse(method: string, rawPath: string): any {
  const path = rawPath.split('?')[0].replace(/\/$/, '');
  const m = method.toUpperCase();

  // mutations: pretend success, persist nothing
  if (m !== 'GET') return { ok: true, demo: true };

  const routes: Record<string, () => any> = {
    '/dashboard/summary': dashboard,
    '/dashboard/settings': () => ({}),
    '/bonds/summary': bonds,
    '/apartments/summary': apartmentSummary,
    '/apartments/units': apartmentUnits,
    '/gold/items': goldItems,
    '/gold/prices': goldPrices,
    '/gold/performance': goldPerformance,
    '/land/summary': landSummary,
    '/land/parcels': landParcels,
    '/brokerage/summary': brokerage,
    // Stocks page (live equity holdings)
    '/equity/summary': equity,
    '/equity/accounts': () => equity().accounts,
    '/equity/dividends': equityDividends,
    '/equity/performance': equityPerformance,
    '/equity/performance/holdings': () => ({ holdings: [], date: iso(NOW) }),
    '/fd/summary': fd,
    '/fd/items': () => fd().fds,
    '/fd/banks': () => ({ banks: ['HDFC Bank', 'ICICI Bank', 'SBI', 'Axis Bank'] }),
    '/ulip/summary': ulip,
    '/ulip/items': () => ulip().policies,
    '/ulip/insurers': () => ({ insurers: ['HDFC Life', 'ICICI Pru', 'SBI Life', 'Max Life'] }),
    '/salary/items': salaryItems,
    '/salary/fx': () => FX,
    '/salary/currencies': () => ({ currencies: ['INR', 'USD', 'EUR', 'GBP', 'AED'] }),
    '/salary/banks': () => ({ banks: ['HDFC ••21', 'ICICI ••07'] }),
    '/expenses/log': expenseLog,
    '/expenses/templates': () => ({ period: THIS_PERIOD, templates: expenseLog().entries }),
    '/expenses/meta': () => ({ frequencies: ['monthly', 'yearly', 'one_time'], categories: ['Housing & EMI', 'Food & Groceries', 'Education', 'Transport', 'Lifestyle', 'Utilities', 'Subscriptions'], currencies: ['INR', 'USD'] }),
    '/expenses/methods': () => ({ methods: ['UPI', 'Credit card', 'Auto-debit', 'Cash'] }),
    '/other-income/log': incomeLog,
    '/other-income/templates': () => ({ period: THIS_PERIOD, templates: incomeLog().entries }),
    '/other-income/meta': () => ({ frequencies: ['monthly', 'yearly', 'one_time'], categories: ['Dividend', 'Interest', 'Rent', 'Business', 'Gift'], currencies: ['INR', 'USD'] }),
    '/other-income/accounts': () => ({ accounts: ['HDFC ••21', 'ICICI ••07'] }),
    '/cash/summary': cash,
    '/cash/wheres': () => ({ wheres: ['HDFC Bank', 'ICICI Bank', 'Home'] }),
    '/networth/summary': networthSummary,
    '/networth/meta': networthMeta,
    '/networth/assets': () => [],
    '/networth/people': () => networthSummary().people,
    '/networth/uploads': () => [],
    // misc app-shell calls
    '/account/funds': () => ({ cash: 1450000, pnl: 0, paper_mode: true, initial_capital: 1450000 }),
    '/auth/status': () => ({ connected: true }),
    '/auth/supabase-status': () => ({ connected: true }),
    '/auth/app-gate': () => ({ enabled: true }),
  };

  if (routes[path]) return routes[path]();
  // safe default — never a real call, never real data
  return path.match(/(items|parcels|units|list|accounts|people|assets|uploads)$/) ? [] : {};
}
