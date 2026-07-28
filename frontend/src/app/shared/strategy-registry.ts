/**
 * Strategy registry — single source of truth for every strategy in the app.
 *
 * To add a new strategy:
 *   1. Add an entry to STRATEGIES below.
 *   2. Build the component and route it in app.routes.ts.
 *   3. The strategies-hub will pick it up automatically.
 *
 * Design rules:
 *   - Every strategy MUST have a unique `id`.
 *   - `status` controls whether it's clickable or shown as "coming soon".
 *   - `route` is the Angular path (no leading slash).
 *   - Keep `tagline` short (one line); `description` can be 2-3 sentences.
 */
export interface StrategyMeta {
  id:           string;
  name:         string;
  icon:         string;
  tagline:      string;
  description:  string;
  status:       'active' | 'coming-soon' | 'beta';
  route:        string;
  riskLevel:    'low' | 'moderate' | 'high';
  capitalMin:   string;        // e.g. "₹18 L+"
  expectedReturn: string;      // e.g. "0.8-1.0% / month"
  keyFeatures:  string[];      // bullet list shown on the card
  badge?:       string;        // optional small badge label, e.g. "NEW"
  themeColor:   string;        // accent color (CSS hex)
}

export const STRATEGIES: StrategyMeta[] = [
  {
    id:             'covered-call',
    name:           'Covered Call',
    icon:           '📈',
    tagline:        'Income from premium on stock you already own.',
    description:
      'Hold NiftyBees, sell out-of-the-money calls each cycle, collect premium. ' +
      'Take profit at 50%, roll up at delta 0.35. Production-ready with strike floor + delta-driven exits.',
    status:         'active',
    route:          'covered-call',
    riskLevel:      'moderate',
    capitalMin:     '₹18 L (1 lot Nifty)',
    expectedReturn: '0.8-1.0% / month net',
    keyFeatures: [
      'Live Kite integration',
      'Strike floor + delta-0.35 trigger',
      'Best-trade scanner across 4 expiries',
      '50% take-profit automation',
      'Full Indian charges + tax accounting',
    ],
    badge:          'LIVE',
    themeColor:     '#387ed1',
  },
  {
    id:             'protected-wheel',
    name:           'Protected Wheel',
    icon:           '🎯',
    tagline:        'Wheel + crash insurance — bounded drawdowns.',
    description:
      'CSP → assignment → CC → assignment cycle, with a continuously rolled 90-day OTM put as crash insurance. ' +
      'Best for retirees and steady-income seekers who want bounded downside.',
    status:         'active',
    route:          'protected-wheel',
    riskLevel:      'low',
    capitalMin:     '₹18-25 L',
    expectedReturn: '0.85-1.0% / month net',
    keyFeatures: [
      'Pre-market 6-check go/caution/skip scan',
      'Trade rationale on every strike',
      'Continuous 90-day protective put',
      'Strike floor at NB entry',
      '4-stop exit strategy with priority',
    ],
    badge:          'NEW',
    themeColor:     '#059669',
  },
  {
    id:             'short-strangle',
    name:           'Short Strangle',
    icon:           '⚡',
    tagline:        'Sell calls + puts simultaneously for max premium.',
    description:
      'Sell out-of-money call AND out-of-money put on the same expiry. Maximum premium income when underlying ' +
      'stays in the range. High-risk; suitable for low-volatility regimes only.',
    status:         'coming-soon',
    route:          'short-strangle',
    riskLevel:      'high',
    capitalMin:     '₹10-15 L margin',
    expectedReturn: '1.5-2.5% / month (if managed)',
    keyFeatures: [
      'Highest premium yield per cycle',
      'Defined-loss version available (Iron Condor)',
      'IV regime filtering required',
      'Active gamma management',
      'Pair with Wheel for diversification',
    ],
    themeColor:     '#d97706',
  },
];

export function getStrategyById(id: string): StrategyMeta | undefined {
  return STRATEGIES.find(s => s.id === id);
}
