/**
 * Client-side options math: Black-Scholes, Greeks, PoP, EV, payoff, theta decay.
 * All computations happen in the browser for instant feedback.
 */

const R = 0.065; // 6.5% risk-free rate (RBI repo)

// ── Normal distribution ───────────────────────────────────────────────────────

function erf(x: number): number {
  const sign = x < 0 ? -1 : 1;
  const ax = Math.abs(x);
  const t = 1 / (1 + 0.3275911 * ax);
  const p = t * (0.254829592 + t * (-0.284496736 + t * (1.421413741 + t * (-1.453152027 + t * 1.061405429))));
  return sign * (1 - p * Math.exp(-ax * ax));
}

export function normCdf(x: number): number {
  return 0.5 * (1 + erf(x / Math.SQRT2));
}

function normPdf(x: number): number {
  return Math.exp(-0.5 * x * x) / Math.sqrt(2 * Math.PI);
}

// ── Black-Scholes ─────────────────────────────────────────────────────────────

export interface BSResult {
  price: number;
  delta: number;
  gamma: number;
  theta: number; // daily ₹
  vega: number;  // per 1% IV
}

export function blackScholes(S: number, K: number, T: number, sigma: number, type: 'CE' | 'PE'): BSResult {
  if (T <= 0 || sigma <= 0) {
    const intrinsic = type === 'CE' ? Math.max(S - K, 0) : Math.max(K - S, 0);
    return { price: intrinsic, delta: intrinsic > 0 ? (type === 'CE' ? 1 : -1) : 0, gamma: 0, theta: 0, vega: 0 };
  }
  const d1 = (Math.log(S / K) + (R + 0.5 * sigma * sigma) * T) / (sigma * Math.sqrt(T));
  const d2 = d1 - sigma * Math.sqrt(T);
  const discount = Math.exp(-R * T);

  const price = type === 'CE'
    ? S * normCdf(d1) - K * discount * normCdf(d2)
    : K * discount * normCdf(-d2) - S * normCdf(-d1);

  const delta = type === 'CE' ? normCdf(d1) : normCdf(d1) - 1;
  const gamma = normPdf(d1) / (S * sigma * Math.sqrt(T));
  const theta = type === 'CE'
    ? (-S * normPdf(d1) * sigma / (2 * Math.sqrt(T)) - R * K * discount * normCdf(d2)) / 365
    : (-S * normPdf(d1) * sigma / (2 * Math.sqrt(T)) + R * K * discount * normCdf(-d2)) / 365;
  const vega = S * normPdf(d1) * Math.sqrt(T) / 100;

  return { price: Math.max(price, 0.05), delta, gamma, theta, vega };
}

// ── Payoff at expiry ──────────────────────────────────────────────────────────

export interface Leg {
  type: 'CE' | 'PE';
  strike: number;
  transaction_type: 'BUY' | 'SELL';
  qty: number;
  lot_size: number;
  premium: number;
}

export function payoffAtSpot(legs: Leg[], spot: number): number {
  return legs.reduce((total, leg) => {
    const intrinsic = leg.type === 'CE' ? Math.max(spot - leg.strike, 0) : Math.max(leg.strike - spot, 0);
    const mult = leg.transaction_type === 'BUY' ? 1 : -1;
    return total + mult * (intrinsic - leg.premium) * leg.qty * leg.lot_size;
  }, 0);
}

export function payoffCurve(legs: Leg[], spot: number, points = 100): { spot: number; pnl: number }[] {
  const lo = spot * 0.8;
  const hi = spot * 1.2;
  const step = (hi - lo) / (points - 1);
  return Array.from({ length: points }, (_, i) => {
    const s = lo + i * step;
    return { spot: Math.round(s), pnl: Math.round(payoffAtSpot(legs, s)) };
  });
}

// ── Breakeven points ──────────────────────────────────────────────────────────

export function findBreakevens(curve: { spot: number; pnl: number }[]): number[] {
  const bes: number[] = [];
  for (let i = 1; i < curve.length; i++) {
    if ((curve[i - 1].pnl < 0 && curve[i].pnl >= 0) || (curve[i - 1].pnl >= 0 && curve[i].pnl < 0)) {
      // Linear interpolation
      const ratio = Math.abs(curve[i - 1].pnl) / (Math.abs(curve[i - 1].pnl) + Math.abs(curve[i].pnl));
      bes.push(Math.round(curve[i - 1].spot + ratio * (curve[i].spot - curve[i - 1].spot)));
    }
  }
  return bes;
}

// ── Probability of Profit ─────────────────────────────────────────────────────

export function probabilityOfProfit(legs: Leg[], spot: number, T: number, sigma: number): number {
  if (T <= 0 || sigma <= 0) return 0;
  const lo = spot * 0.4;
  const hi = spot * 2.5;
  const n = 500;
  const step = (hi - lo) / n;
  const muT = Math.log(spot) + (R - sigma * sigma / 2) * T;
  const sigmaT = sigma * Math.sqrt(T);
  let prob = 0;
  for (let i = 0; i < n; i++) {
    const s = lo + i * step + step / 2;
    if (s <= 0) continue;
    if (payoffAtSpot(legs, s) > 0) {
      const logPdf = -0.5 * Math.pow((Math.log(s) - muT) / sigmaT, 2);
      const pdf = Math.exp(logPdf) / (s * sigmaT * Math.sqrt(2 * Math.PI));
      prob += pdf * step;
    }
  }
  return Math.min(Math.max(prob, 0), 1);
}

// ── Expected Value ────────────────────────────────────────────────────────────

export function expectedValue(legs: Leg[], spot: number, T: number, sigma: number): number {
  if (T <= 0 || sigma <= 0) return payoffAtSpot(legs, spot);
  const lo = spot * 0.4;
  const hi = spot * 2.5;
  const n = 500;
  const step = (hi - lo) / n;
  const muT = Math.log(spot) + (R - sigma * sigma / 2) * T;
  const sigmaT = sigma * Math.sqrt(T);
  let ev = 0;
  for (let i = 0; i < n; i++) {
    const s = lo + i * step + step / 2;
    if (s <= 0) continue;
    const logPdf = -0.5 * Math.pow((Math.log(s) - muT) / sigmaT, 2);
    const pdf = Math.exp(logPdf) / (s * sigmaT * Math.sqrt(2 * Math.PI));
    ev += payoffAtSpot(legs, s) * pdf * step;
  }
  return ev;
}

// ── Net Greeks ────────────────────────────────────────────────────────────────

export interface NetGreeks {
  delta: number;
  gamma: number;
  theta: number; // daily ₹ for entire position
  vega: number;  // ₹ per 1% IV move for entire position
}

export function netGreeks(legs: Leg[], spot: number, T: number, sigma: number): NetGreeks {
  return legs.reduce((acc, leg) => {
    const bs = blackScholes(spot, leg.strike, T, sigma, leg.type);
    const mult = leg.transaction_type === 'SELL' ? -1 : 1;
    const qty = leg.qty * leg.lot_size;
    return {
      delta: acc.delta + mult * bs.delta * qty,
      gamma: acc.gamma + mult * bs.gamma * qty,
      theta: acc.theta + mult * bs.theta * qty,
      vega: acc.vega + mult * bs.vega * qty,
    };
  }, { delta: 0, gamma: 0, theta: 0, vega: 0 });
}

// ── Theta decay curve ─────────────────────────────────────────────────────────

export function thetaDecayCurve(legs: Leg[], spot: number, dte: number, sigma: number): { dte: number; pnl: number }[] {
  const result: { dte: number; pnl: number }[] = [];
  const entryValue = legs.reduce((s, leg) => {
    const mult = leg.transaction_type === 'SELL' ? -1 : 1;
    return s + mult * leg.premium * leg.qty * leg.lot_size;
  }, 0);

  for (let d = dte; d >= 0; d--) {
    const T = d / 365;
    const currentValue = legs.reduce((s, leg) => {
      const bs = blackScholes(spot, leg.strike, T, sigma, leg.type);
      const mult = leg.transaction_type === 'SELL' ? -1 : 1;
      return s + mult * bs.price * leg.qty * leg.lot_size;
    }, 0);
    // P&L = entryValue - currentValue (for credit strategies, entryValue > 0)
    // Actually: P&L = -(currentValue - entryValue) for sellers
    const pnl = -(currentValue - entryValue);
    result.push({ dte: d, pnl: Math.round(pnl) });
  }
  return result;
}

// ── Optimal exit recommendation ───────────────────────────────────────────────

export interface ExitRecommendation {
  exitDte: number | null;
  targetPct: number;
  targetAbs: number;
  rationale: string;
  strategyType: 'theta_seller' | 'theta_buyer' | 'directional';
}

export function optimalExit(legs: Leg[], dte: number): ExitRecommendation {
  const netCredit = legs.reduce((s, l) =>
    s + (l.transaction_type === 'SELL' ? 1 : -1) * l.premium * l.qty * l.lot_size, 0);

  const isSeller = netCredit > 0;
  const netAbs = Math.abs(netCredit);

  if (isSeller) {
    const exitDte = Math.max(2, Math.min(7, Math.round(dte * 0.2)));
    const targetAbs = Math.round(netCredit * 0.5);
    return {
      exitDte,
      targetPct: 50,
      targetAbs,
      rationale: `Exit at ${exitDte} DTE or ₹${targetAbs} profit (50%) — whichever comes first. ` +
        `Gamma risk spikes in the final week, eroding theta gains faster than they accrue.`,
      strategyType: 'theta_seller',
    };
  } else {
    const targetAbs = Math.round(netAbs * 0.5);
    return {
      exitDte: null,
      targetPct: 50,
      targetAbs,
      rationale: `Exit early if ₹${targetAbs} profit (50%) is achieved. ` +
        `Theta decay accelerates near expiry — every day held increases the cost of being wrong.`,
      strategyType: 'theta_buyer',
    };
  }
}

// ── Full analysis ─────────────────────────────────────────────────────────────

export interface StrategyAnalysis {
  payoffCurve: { spot: number; pnl: number }[];
  thetaCurve: { dte: number; pnl: number }[];
  breakevens: number[];
  maxProfit: number;
  maxLoss: number;
  pop: number;          // 0–1
  expectedValue: number;
  netCredit: number;
  greeks: NetGreeks;
  exit: ExitRecommendation;
  riskReward: number;   // |maxProfit / maxLoss|
}

export function analyzeStrategy(legs: Leg[], spot: number, dte: number, iv = 0.15): StrategyAnalysis {
  const T = dte / 365;
  const curve = payoffCurve(legs, spot);
  const bes = findBreakevens(curve);
  const pnls = curve.map(p => p.pnl);
  const maxProfit = Math.max(...pnls);
  const maxLoss = Math.min(...pnls);
  const netCredit = legs.reduce((s, l) =>
    s + (l.transaction_type === 'SELL' ? 1 : -1) * l.premium * l.qty * l.lot_size, 0);

  return {
    payoffCurve: curve,
    thetaCurve: thetaDecayCurve(legs, spot, dte, iv),
    breakevens: bes,
    maxProfit,
    maxLoss,
    pop: probabilityOfProfit(legs, spot, T, iv),
    expectedValue: expectedValue(legs, spot, T, iv),
    netCredit: Math.round(netCredit),
    greeks: netGreeks(legs, spot, T, iv),
    exit: optimalExit(legs, dte),
    riskReward: maxLoss !== 0 ? Math.abs(maxProfit / maxLoss) : Infinity,
  };
}
