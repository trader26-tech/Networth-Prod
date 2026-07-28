import { Component, ElementRef, HostListener, OnDestroy, OnInit, ViewChild, computed, effect, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router } from '@angular/router';
import { HttpErrorResponse } from '@angular/common/http';
import {
  FnoService, FnoSummary, FnoStrategyStats, FnoCalendar, FnoCalendarDay,
  FnoTrade, FnoLoginLog, FnoAccount, FnoOpenPositions, FnoOpenLeg,
  FnoTradebooks, FnoTradebookRec, FnoPnlStatement, FnoOptionChain, FnoOptionRow,
} from '../../services/fno.service';

type Range = 'today' | '1w' | '1m' | '6m' | '1y' | 'all';

const STRAT_LABEL: Record<string, string> = {
  sentinel: 'Sentinel · ATM Short Straddle',
  crude: 'Crude Oil',
  other: 'Other F&O',
};

interface ChartPt {
  x: number; v: number; label: string; day?: number;
  full?: string;                                                    // rich date(+time) w/ YEAR — tooltip title
  year?: number;                                                    // calendar year — x-axis boundary marks
  date?: string;                                                    // YYYY-MM-DD (daily points)
  by_account?: { account_id: string; label: string; person: string | null; total: number }[];
  by_strategy?: Record<string, number>;                            // per-strategy P&L that day (daily points)
}
// Hand-built SVG chart. The <svg> uses a 0–100 × 0–100 viewBox with
// preserveAspectRatio="none", so these percentage coords line up 1:1 with the
// HTML overlays (gridlines, crosshair, tooltip). The line is a single <path>
// with vector-effect="non-scaling-stroke" → a constant 2px width at every slope.
interface ChartGeom {
  empty: boolean;
  zeroPct: number;                              // y of ₹0, % from the top
  zeroFrac: number;                             // zeroPct / 100, for gradient stops
  areaPath: string;                             // SVG path `d` for the filled area
  linePath: string;                             // SVG path `d` for the SOLID line (active minutes)
  dashPath: string;                             // SVG path `d` for the DOTTED line (no-trade gaps)
  grid: { topPct: number; label: string }[];
  xticks: { leftPct: number; label: string; year?: string }[];
  pts: { xPct: number; yPct: number }[];        // hover targets
  last: { xPct: number; yPct: number; pos: boolean } | null;
}

// ── Black–Scholes — for the "today"/theta curve & the net-delta strip ─────────
// The backend gives no greeks, so we back implied vol out of each option's LIVE
// price, then reprice at other underlyings holding TODAY's time-to-expiry. That
// smooth curve sits between the current mark and the expiry hockey-stick, so you
// can see where you are today (theta) and where you'd be at expiry. cp: CE=+1, PE=−1.
const _SQRT2PI = Math.sqrt(2 * Math.PI);
function _ncdf(x: number): number {                     // standard normal CDF (A&S 26.2.17)
  const t = 1 / (1 + 0.2316419 * Math.abs(x));
  const d = Math.exp(-x * x / 2) / _SQRT2PI;
  const p = d * t * (0.319381530 + t * (-0.356563782 + t * (1.781477937 + t * (-1.821255978 + t * 1.330274429))));
  return x >= 0 ? 1 - p : p;
}
function bsPrice(cp: 1 | -1, S: number, K: number, t: number, r: number, sig: number): number {
  if (t <= 0 || sig <= 0) return Math.max(cp * (S - K), 0);      // intrinsic (expiry)
  const st = sig * Math.sqrt(t);
  const d1 = (Math.log(S / K) + (r + sig * sig / 2) * t) / st, d2 = d1 - st;
  return cp * (S * _ncdf(cp * d1) - K * Math.exp(-r * t) * _ncdf(cp * d2));
}
function bsDelta(cp: 1 | -1, S: number, K: number, t: number, r: number, sig: number): number {
  if (t <= 0 || sig <= 0) return cp === 1 ? (S > K ? 1 : 0) : (S < K ? -1 : 0);
  const st = sig * Math.sqrt(t);
  const d1 = (Math.log(S / K) + (r + sig * sig / 2) * t) / st;
  return cp === 1 ? _ncdf(d1) : _ncdf(d1) - 1;
}
function impliedVol(cp: 1 | -1, S: number, K: number, t: number, r: number, price: number): number | null {
  const intrinsic = Math.max(cp * (S - K), 0);
  if (t <= 0 || price <= intrinsic + 1e-4) return null;         // no time value → treat as expiry
  let lo = 1e-4, hi = 5;                                         // 0.01% … 500% vol
  for (let i = 0; i < 64; i++) {
    const mid = (lo + hi) / 2, p = bsPrice(cp, S, K, t, r, mid);
    if (Math.abs(p - price) < 1e-4) return mid;
    if (p > price) hi = mid; else lo = mid;
  }
  return (lo + hi) / 2;
}
const RISK_FREE = 0.065;                                          // India ~6.5% — shape is vol-dominated

// ── Payoff diagram (expiry P&L of the open book) ─────────────────────────────
interface PayoffLeg {
  key: string; tradingsymbol: string; account_label: string;
  root: string; type: 'CE' | 'PE' | 'FUT'; strike: number;
  qty: number; avg: number; mult: number; ltp: number | null; unrealized: number | null;
  cp: 1 | -1 | 0; tYears: number; iv: number | null;             // 0 = future; iv null → priced at intrinsic
  draft?: boolean;                                               // hypothetical "what-if" leg
}
// A hypothetical leg the user adds in the fullscreen "what-if" builder. Entered
// at the current market premium (avg = premium, so it opens at ₹0 P&L) and folded
// into the SAME payoff math as the real book.
interface DraftLeg {
  id: number; root: string; type: 'CE' | 'PE' | 'FUT';
  strike: number; qty: number; premium: number; mult: number;
  expiry?: string | null;                            // chain expiry (for the theta curve)
}
interface PayoffModel {
  root: string; spot: number | null; hasSpot: boolean;
  legs: PayoffLeg[]; pts: { s: number; pnl: number }[];
  hasToday: boolean;                                  // any option had a live IV → a time curve can be drawn
  xMin: number; xMax: number;
  maxProfit: number; maxLoss: number;                 // finite ₹ (see *Unlimited flags)
  profitUnlimited: boolean; lossUnlimited: boolean;
  maxProfitAt: number; maxLossAt: number;             // underlying price of each extreme
  breakevens: number[];
  currentPnl: number;                                 // live MTM (sum of legs' unrealized)
  expiryAtSpot: number | null;                        // expiry P&L if spot never moves
  netCredit: number; isCredit: boolean;               // premium received − paid
  capturedPct: number | null;                         // currentPnl / maxProfit
  expiry: string | null; dte: number | null;
  hasDraft: boolean;                                  // any what-if leg folded in
  basePts: { s: number; pnl: number }[] | null;       // expiry P&L of the REAL book only (ghost "before" line)
}

@Component({
  selector: 'app-fno',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './fno.html',
  styleUrl: './fno.scss',
})
export class Fno implements OnInit, OnDestroy {
  svc = inject(FnoService);
  private route = inject(ActivatedRoute);
  private router = inject(Router);

  Math = Math;                       // for clamp expressions in the template
  inr = FnoService.inr;
  inrS = FnoService.inrSigned;
  inrFull = FnoService.inrFull;      // exact value (no L/Cr) for the hover tooltip
  /** compact signed ₹ for the holder-card stat grid: −₹57.9k · ₹1.24L · ₹0 */
  kShort(v: number | null | undefined): string {
    if (v == null || isNaN(v)) return '—';
    const a = Math.abs(v), s = v < 0 ? '−' : v > 0 ? '+' : '';
    if (a >= 1e7) return `${s}₹${(a / 1e7).toFixed(2)}Cr`;
    if (a >= 1e5) return `${s}₹${(a / 1e5).toFixed(2)}L`;
    if (a >= 1e3) return `${s}₹${(a / 1e3).toFixed(1)}k`;
    return `${s}₹${Math.round(a)}`;
  }
  stratLabel(s: string): string { return STRAT_LABEL[s] || this.catMap().get(s)?.label || this.titleCase(s); }
  /** Short name for the compact cards (e.g. "Sentinel", or a custom "Ram"). */
  stratShort(s: string): string {
    return this.catMap().get(s)?.label || ({ sentinel: 'Sentinel', crude: 'Crude Oil', other: 'Other F&O' } as Record<string, string>)[s] || this.titleCase(s);
  }
  shortLabel(s: string): string { return ({ sentinel: 'Sentinel', crude: 'Crude', other: 'Other' } as Record<string, string>)[s] || this.titleCase(s); }
  private titleCase(s: string): string { return (s || '').replace(/\b\w/g, c => c.toUpperCase()); }

  // Colour for a strategy dot: fixed for the built-ins, else a stable pick from a
  // palette (hashed by name) so custom strategies like "ram" get a consistent hue.
  private static readonly STRAT_PALETTE = ['#7c3aed', '#0891b2', '#db2777', '#16a34a', '#ca8a04', '#dc2626', '#2563eb'];
  stratColor(key: string): string {
    const cat = this.catMap().get(key); if (cat?.color) return cat.color;
    const fixed: Record<string, string> = { sentinel: '#387ed1', crude: '#d97706', other: '#9097b4' };
    if (fixed[key]) return fixed[key];
    let h = 0; for (const c of key) h = (h * 31 + c.charCodeAt(0)) >>> 0;
    return Fno.STRAT_PALETTE[h % Fno.STRAT_PALETTE.length];
  }
  /** built-ins first (sentinel · crude · other), then any custom strategies A→Z */
  private orderStrats(keys: Iterable<string>): string[] {
    const rank: Record<string, number> = { sentinel: 0, crude: 1, other: 2 };
    return [...new Set(keys)].sort((a, b) =>
      (rank[a] ?? 3) - (rank[b] ?? 3) || a.localeCompare(b));
  }

  // ── strategy catalog (DB-backed list you can add to) ───────────────────────
  catalog = signal<{ key: string; label: string; color: string }[]>([]);
  catMap = computed(() => new Map(this.catalog().map(c => [c.key, c])));
  loadCatalog() { this.svc.getStrategyCatalog().subscribe({ next: c => this.catalog.set(c || []), error: () => {} }); }
  // add-strategy form (in the manager popover)
  stratMgrOpen = signal(false);
  newStratName = signal('');
  newStratColor = signal('#7c3aed');
  stratBusy = signal(false);
  customStrategies = computed(() => this.catalog().filter(c => !['sentinel', 'crude', 'other'].includes(c.key)));
  addNewStrategy() {
    const label = this.newStratName().trim();
    const key = label.toLowerCase();
    if (!label || this.stratBusy()) return;
    this.stratBusy.set(true);
    this.svc.addStrategy(key, label, this.newStratColor()).subscribe({
      next: c => { this.catalog.set(c || []); this.newStratName.set(''); this.stratBusy.set(false);
        this.notice.set(`Added strategy “${label}”`); setTimeout(() => this.notice.set(null), 2000); },
      error: (e: HttpErrorResponse) => { this.stratBusy.set(false); this._err(e, 'Could not add strategy'); },
    });
  }
  deleteStrategy(key: string) {
    if (this.stratBusy()) return;
    this.stratBusy.set(true);
    this.svc.removeStrategy(key).subscribe({
      next: c => { this.catalog.set(c || []); this.stratBusy.set(false); },
      error: (e: HttpErrorResponse) => { this.stratBusy.set(false); this._err(e, 'Could not remove strategy'); },
    });
  }

  // ── page state ────────────────────────────────────────────────────────────
  summary = signal<FnoSummary | null>(null);
  strategies = signal<FnoStrategyStats[]>([]);
  loginLog = signal<FnoLoginLog[]>([]);
  showLog = signal(false);
  error = signal<string | null>(null);
  notice = signal<string | null>(null);
  needsMigration = signal(false);

  // ── which accounts are shown in the dashboard (empty set = all) ────────────
  selectedAccts = signal<Set<string>>(this._loadSel());
  private _loadSel(): Set<string> {
    if (typeof localStorage === 'undefined') return new Set();
    try { const a = JSON.parse(localStorage.getItem('fno_sel_accts') || '[]'); return new Set(Array.isArray(a) ? a : []); }
    catch { return new Set(); }
  }
  private _persistSel() {
    if (typeof localStorage !== 'undefined') localStorage.setItem('fno_sel_accts', JSON.stringify([...this.selectedAccts()]));
  }
  /** ids to send to the API — empty means "all accounts" (no filter) */
  selectedIds = computed(() => [...this.selectedAccts()]);
  isAllSel = computed(() => this.selectedAccts().size === 0);
  /** is this account currently shown? (empty set = all shown) */
  isShown(id: string): boolean { return this.isAllSel() || this.selectedAccts().has(id); }
  selectAll() { this.selectedAccts.set(new Set()); this._persistSel(); this.reloadAll(); }
  toggleAcc(id: string) {
    const all = this.accounts().map(a => a.id);
    this.selectedAccts.update(s => {
      // "all" (empty sentinel) → the first click starts a FRESH selection with
      // just this account (not "all except this one"). Click more to add them;
      // both stay highlighted and the page shows their combined data.
      const cur = s.size ? new Set(s) : new Set<string>();
      cur.has(id) ? cur.delete(id) : cur.add(id);
      // none-left or every-one → collapse back to the "all" sentinel (empty set)
      return (cur.size === 0 || cur.size === all.length) ? new Set() : cur;
    });
    this._persistSel(); this.reloadAll();
  }
  accSelLabel = computed(() => {
    const ids = this.selectedAccts();
    if (!ids.size) return 'All accounts';
    if (ids.size === 1) {
      const a = this.accounts().find(x => x.id === [...ids][0]);
      if (!a) return '1 account';
      return a.person ? `${a.person} · ${a.account_label}` : a.account_label;   // holder · account
    }
    return `${ids.size} of ${this.accounts().length}`;
  });

  // live (per-second) — from the WebSocket, scoped to the selected accounts
  live = this.svc.live;
  liveOn = computed(() => this.svc.wsConnected() && !!this.live()?.market_open);
  liveAccts = computed(() => {
    const l = this.live();
    if (!l) return [];
    return this.isAllSel() ? l.accounts : l.accounts.filter(a => this.isShown(a.id));
  });
  /** any open position right now (in the selected accounts) → engine tracking LTPs */
  tradesActive = computed(() => this.liveAccts().some(a => a.positions.some(p => p.quantity !== 0)));

  // ── market-hours awareness ────────────────────────────────────────────────
  // "Today" is only a LIVE view when you hold an open position AND that
  // position's own market is trading. A closed market can't move the P&L, so
  // showing a live "Today" then is misleading — we fall back to All instead.
  private nowTick = signal(0);                 // bumped every 20s so the hours re-evaluate
  private readonly SEG_NAME: Record<string, string> =
    { equity: 'Indian F&O', commodity: 'Crude / MCX', currency: 'Currency' };
  /** Kite exchange → market segment */
  private segOf(ex: string): 'equity' | 'commodity' | 'currency' | 'other' {
    const e = (ex || '').toUpperCase();
    if (e === 'NFO' || e === 'BFO') return 'equity';
    if (e === 'MCX') return 'commodity';
    if (e === 'CDS' || e === 'BCD') return 'currency';
    return 'other';
  }
  /** current IST minute-of-day + weekday (same IST clock as todayKey) */
  private istHM(): { hm: number; weekday: number } {
    const ist = new Date(Date.now() + 330 * 60000);
    return { hm: ist.getUTCHours() * 60 + ist.getUTCMinutes(), weekday: ist.getUTCDay() };
  }
  /** is one segment's market trading right now? (IST, Mon–Fri) */
  private segOpen(seg: string): boolean {
    const { hm, weekday } = this.istHM();
    if (weekday === 0 || weekday === 6) return false;
    if (seg === 'equity') return hm >= 9 * 60 + 15 && hm <= 15 * 60 + 30;   // 09:15–15:30
    if (seg === 'commodity') return hm >= 9 * 60 && hm <= 23 * 60 + 55;     // 09:00–23:55 (MCX eve)
    if (seg === 'currency') return hm >= 9 * 60 && hm <= 17 * 60;           // 09:00–17:00
    return false;
  }
  /** segments the selected accounts currently hold open positions in */
  openSegments = computed(() => {
    const segs = new Set<string>();
    for (const p of this.openLegs()) if (p.qty !== 0) segs.add(this.segOf(p.exchange));
    return segs;
  });
  /** Today deserves a LIVE view: an open position in a market that's open now */
  todayLive = computed(() => {
    this.nowTick();                            // re-evaluate as the clock advances
    for (const s of this.openSegments()) if (this.segOpen(s)) return true;
    return false;
  });
  /** why Today isn't live (empty string ⇒ it IS live) — drives the hover popover */
  todayReason = computed(() => {
    this.nowTick();
    const segs = [...this.openSegments()];
    if (!segs.length) return 'No open positions — nothing to track live right now.';
    const closed = segs.filter(s => !this.segOpen(s)).map(s => this.SEG_NAME[s] || s);
    if (!closed.length) return '';
    const list = closed.length === 1 ? closed[0]
      : closed.slice(0, -1).join(', ') + ' & ' + closed.slice(-1);
    return `${list} ${closed.length > 1 ? 'markets are' : 'market is'} closed for the day.`;
  });
  /** the chart is on the live Today view (today explicitly selected, or default) */
  onToday = computed(() => this.range() === 'today' && (!this.selectedDay() || this.selectedDay() === this.todayKey));
  /** Today → the one intraday view (last hour). Also SELECTS today so the calendar,
   *  trade history & strategies focus today (the KPI bar stays live — see kpi()). */
  onTodayClick() {
    this.setRange('today');                     // (setRange clears selectedDay first)
    this.selectedDay.set(this.todayKey);        // …then pin today so the whole UI focuses it
    this.setZoom('1h');
  }

  /** Today's P&L — live number when the socket is on, else last stored. */
  todayPnl = computed(() => {
    const l = this.live();
    if (l && l.market_open && this.liveAccts().length) {
      return Math.round(this.liveAccts().reduce((s, a) => s + a.day_pnl, 0) * 100) / 100;
    }
    return this.summary()?.today_pnl ?? 0;
  });
  overallPnl = computed(() => this.summary()?.overall_pnl ?? 0);
  // live per-strategy day P&L, scoped to the SELECTED accounts (the WS payload's
  // global by_strategy would leak other accounts into a single-account view)
  private liveByStrat = computed<Record<string, number>>(() => {
    const m: Record<string, number> = {};
    for (const a of this.liveAccts())
      for (const [k, s] of Object.entries(a.by_strategy || {})) m[k] = (m[k] || 0) + (s.day_pnl || 0);
    return m;
  });
  // live per-strategy split: realized (BOOKED — stable, only jumps on a fill) vs
  // unrealized (moves every tick). day_pnl = realized + unrealized. Keeping them
  // apart is what stops "booked" from flickering every second.
  private liveStratSplit = computed<Record<string, { realized: number; unrealized: number }>>(() => {
    const m: Record<string, { realized: number; unrealized: number }> = {};
    for (const a of this.liveAccts())
      for (const [k, s] of Object.entries(a.by_strategy || {})) {
        const g = (m[k] ??= { realized: 0, unrealized: 0 });
        g.realized += s.realized || 0;
        g.unrealized += s.unrealized || 0;
      }
    return m;
  });
  heroStrats = computed(() => {
    const sum = this.summary();
    const marketOpen = !!this.live()?.market_open;
    const liveBy = this.liveByStrat();
    const keys = new Set<string>([...Object.keys(sum?.by_strategy || {}), ...Object.keys(liveBy)]);
    return this.orderStrats(keys).map(k => ({
      key: k, label: this.stratLabel(k),
      today: marketOpen ? (liveBy[k] ?? 0)
        : (this.strategies().find(s => s.strategy === k)?.today ?? 0),
      total: sum?.by_strategy?.[k] ?? 0,
    }));
  });

  livePositions = computed(() => {
    return this.liveAccts().flatMap(a => a.positions
      .filter(p => p.quantity !== 0)
      .map(p => ({ ...p, account_label: a.account_label })));
  });

  // ── carried-forward open positions (REST — works when the market is closed) ──
  // Legs not yet booked, marked to market via the paid price-feed. This
  // unrealized P&L is carried forward — it is NOT part of any day's realised P&L
  // (that books the day the leg is finally closed).
  openPos = signal<FnoOpenPositions | null>(null);
  openPosLoading = signal(false);
  openLegs = computed(() => this.openPos()?.positions || []);
  hasOpenPos = computed(() => (this.openPos()?.count ?? 0) > 0);
  // The "open · unrealized" number is derived from the SAME per-account source the
  // holder cards use (accountUnreal, from one all-accounts openPositions call), so
  // the chart headline, the Open Positions header and every card ALWAYS agree — no
  // few-rupee drift from two separately-timed calls. Falls back to the scoped
  // openPos total only until the per-account map has loaded.
  openUnrealized = computed(() => {
    const map = this.accountUnreal();
    const ids = Object.keys(map);
    if (!ids.length) return this.openPos()?.total_unrealized ?? 0;
    const scope = this.isAllSel() ? ids : this.selectedIds();
    return Math.round(scope.reduce((s, id) => s + (map[id] || 0), 0) * 100) / 100;
  });
  // LIVE mode → the card mirrors Kite's Positions screen: each leg's live P&L
  // (realized-today + unrealized) and a total that ties out to the chart & Kite.
  // Carried (market shut) → pure carry-forward unrealized, as before.
  openLiveMode = computed(() => !!this.openPos()?.live_mode);
  // The Open Positions headline is ALWAYS the overall unrealized (mark-to-market
  // vs average cost) — the same "open" number the account cards, chart and KPI
  // show, so every surface agrees. Today's live move (Kite's day P&L on these
  // legs) is a separate, clearly-labelled secondary stat, not the headline.
  openHeadline = computed(() => this.openUnrealized());
  openToday = computed(() => this.openPos()?.total_day_pnl ?? null);   // today's move (live)
  openTodayAbs = computed(() => Math.abs(this.openToday() ?? 0));
  // per-leg value the card shows: the leg's overall unrealized (matches the headline)
  legValue = (p: FnoOpenLeg) => p.unrealized ?? null;
  // whose account a position sits in — the person's name, else the account label
  legWhose = (p: FnoOpenLeg) => (p.person && p.person.trim()) || p.account_label || '—';
  legInitial = (p: FnoOpenLeg) => (this.legWhose(p) || '?').trim().charAt(0).toUpperCase();
  // verifiable breakdown of the "Open · unrealized" total: each still-open leg's
  // (ltp − avg) × qty, so the numbers add up in front of you. Shown on hover.
  openUnrealTip = computed(() => {
    const legs = this.openLegs().filter(p => !p.closed && p.qty && p.ltp != null && p.unrealized != null);
    if (!legs.length) return '';
    const lines = legs.map(p => {
      const dir = p.qty < 0 ? `sold ${p.avg} → ${p.ltp}` : `bought ${p.avg} → ${p.ltp}`;
      return `${p.tradingsymbol}  (${p.qty} · ${dir})  =  ${this.inrS(p.unrealized!)}`;
    });
    lines.push('─────────');
    lines.push(`Open · unrealized (unbooked m2m)  =  ${this.inrS(this.openUnrealized())}`);
    if (this.openLiveMode()) {
      lines.push('');
      lines.push('This is only the mark-to-market on the still-open qty.');
      lines.push("Today's P&L also includes intraday trades already booked —");
      lines.push('that full live P&L is the number on the Open Positions card.');
    }
    return lines.join('\n');
  });
  openInvested = computed(() => this.openPos()?.total_invested ?? 0);
  openUnpriced = computed(() => this.openPos()?.unpriced_count ?? 0);
  openExpiredHidden = computed(() => this.openPos()?.expired_count ?? 0);
  openReturnPct = computed(() => {
    const inv = this.openInvested();
    return inv > 0 ? this.openUnrealized() / inv : null;
  });
  // the price feed (paid Kite session) is down → nothing can be marked to market
  feedOffline = computed(() => { const o = this.openPos(); return !!o && o.count > 0 && o.feed_ok === false; });
  // the designated price-feed account (needs a live Kite login to fetch LTPs)
  priceFeedAccount = computed<FnoAccount | null>(() =>
    this.accounts().find(a => a.price_feed) || null);
  loadOpenPositions() {
    this.openPosLoading.set(true);
    this.svc.openPositions(this.selectedIds()).subscribe({
      next: o => {
        this.openPos.set(o);
        this.openPosLoading.set(false);
        // openPositions loads async (after loadSeries) and is scoped to the
        // selected accounts, so it's the authority on the TODAY chart: it knows
        // the real open book for THIS account. Re-decide on every load so an
        // account switch flips the chart correctly — the chart-gen guard drops
        // any stale loadSeries response that lands afterwards.
        // The TODAY chart is always the day-P&L timeline (coherent with the live
        // feed). openPositions is the authority on whether anything is LIVE right
        // now — and if nothing is (no open book, no active trade), we default to
        // the All (history) chart rather than show an idle intraday view.
        // Default to a LIVE Today only when an open position's market is actually
        // open; otherwise fall back to All (history). The user can still reach
        // today's stored curve via the Today popover's "See the day" / "Zoom".
        const todayView = !this.selectedDay() || this.selectedDay() === this.todayKey;
        if (todayView && this.range() === 'today' && !this._autoRanged) {
          if (!this.todayLive()) { this._autoRanged = true; this.range.set('all'); this.loadSeries(); }
        }
      },
      error: () => this.openPosLoading.set(false),
    });
  }

  // ── hero KPIs: CAGR (on pledged capital) + max drawdown ────────────────────
  pct(v: number | null | undefined): string {
    if (v == null || isNaN(v)) return '—';
    return `${v >= 0 ? '+' : '−'}${Math.abs(v * 100).toFixed(1)}%`;
  }
  cagr = computed(() => this.summary()?.cagr ?? null);
  cagrText = computed(() => {
    const c = this.cagr();
    if (!c) return '—';
    if (c.cagr == null) return '−100%';
    return this.pct(c.cagr);
  });
  /** CAGR including the carried-forward unrealized P&L (live open book marked
   *  to market). Same pledged denominator + period as the realized CAGR — only
   *  the P&L numerator changes. Live view only (open legs are live now). */
  cagrUnreal = computed(() => {
    const c = this.cagr();
    const pledgedV = this.pledged()?.value ?? 0;
    if (!c || pledgedV <= 0 || (this.selectedDay() && this.selectedDay() !== this.todayKey)) return null;
    const unreal = this.openUnrealized();
    const total = this.overallPnl() + unreal;
    const totRet = total / pledgedV;
    const days = c.days || 1;
    const cagr = totRet <= -1 ? -1 : Math.pow(1 + totRet, 365 / days) - 1;
    return { cagr, total_return: totRet, unreal, total };
  });
  cagrUnrealText = computed(() => {
    const c = this.cagrUnreal();
    if (!c) return '—';
    return c.cagr <= -1 ? '−100%' : this.pct(c.cagr);
  });
  pledged = computed(() => this.summary()?.pledged ?? null);

  // ── date-aware KPI bar ──────────────────────────────────────────────────────
  // Full daily P&L history (one row per trading day: day P&L + running cumulative)
  // so every KPI can be recomputed "as of" the day the user picks in the calendar.
  dailySeries = signal<{ t: string; day: number; pnl: number }[]>([]);
  loadDailySeries() {
    this.svc.series('all', undefined, this.selectedIds()).subscribe({
      next: s => this.dailySeries.set((s.points || []).map(p => ({ t: (p.t || '').slice(0, 10), day: p.day ?? 0, pnl: p.pnl ?? 0 }))),
      error: () => {},
    });
  }
  /** One object driving the whole KPI bar. When a past day is selected every
   *  value is recomputed cumulatively up to & including that day; otherwise it's
   *  the all-time / live view. */
  kpi = computed(() => {
    const day = this.selectedDay();
    const daily = this.dailySeries();
    // Selecting TODAY focuses the rest of the UI on today, but the KPI bar stays
    // the live aggregate (today IS the live view) — only a PAST day switches it.
    if (!day || day === this.todayKey) {
      return {
        isDay: false,
        dateLabel: this.fmtDateMed(this.todayKey),
        pnlLabel: 'Booked today',
        overall: this.overallPnl(),
        dayPnl: this.summary()?.today_pnl ?? 0,   // BOOKED today (realised) — not the live MTM
        daysTraded: this.summary()?.trading_days ?? daily.length,
        cagr: this.cagr()?.cagr ?? null,
        cagrText: this.cagrText(),
      };
    }
    const upto = daily.filter(p => p.t <= day);
    const cum = upto.length ? upto[upto.length - 1].pnl : 0;
    const dayPt = daily.find(p => p.t === day);
    const dayPnl = dayPt ? dayPt.day : (this.selectedDayData()?.total ?? 0);
    // CAGR as of that day: cumulative return on pledged, annualised over the span
    const pledged = this.pledged()?.value ?? 0;
    let cagr: number | null = null;
    if (pledged > 0 && upto.length) {
      const totRet = cum / pledged;
      const spanDays = Math.max(1, (Date.parse(day) - Date.parse(daily[0].t)) / 86400000);
      cagr = totRet <= -1 ? -1 : Math.pow(1 + totRet, 365 / spanDays) - 1;
    }
    return {
      isDay: true,
      dateLabel: this.fmtDateMed(day),
      pnlLabel: this.fmtDate(day) + ' P&L',
      overall: cum,
      dayPnl,
      daysTraded: upto.length,
      cagr,
      cagrText: cagr == null ? '—' : this.pct(cagr),
    };
  });

  // Brokerage + taxes (and other statement credits/debits) across ALL accounts —
  // imported from each account's Kite P&L statement.
  totalCharges = computed(() => this.accounts().reduce((s, a) => s + (a.pnl_charges ?? 0), 0));
  totalOther = computed(() => this.accounts().reduce((s, a) => s + (a.pnl_other ?? 0), 0));

  // Total P&L = booked (realised, gross) − charges + other + carried-forward
  // unrealized. NET of all charges, so it matches the sum of the per-holder cards.
  // The full breakdown shows on hover.
  totalPnl = computed(() =>
    this.kpi().overall - this.totalCharges() + this.totalOther() + this.openUnrealized());

  // pledged-capital override editing (drives the CAGR denominator)
  editPledged = signal(false);
  pledgedDraft = signal<number | null>(null);
  pledgedBusy = signal(false);
  openPledgedEdit() {
    this.pledgedDraft.set(this.pledged()?.value ?? null);
    this.editPledged.set(true);
  }
  savePledged() {
    const v = this.pledgedDraft();
    this.pledgedBusy.set(true);
    this.svc.setPledged(v && v > 0 ? v : null).subscribe({
      next: () => { this.pledgedBusy.set(false); this.editPledged.set(false); this.reloadAll(); },
      error: (e: HttpErrorResponse) => { this.pledgedBusy.set(false); this._err(e, 'Could not set pledged value'); },
    });
  }
  refreshPledgedNow() {
    this.pledgedBusy.set(true);
    this.svc.refreshPledged().subscribe({
      next: () => { this.pledgedBusy.set(false); this.editPledged.set(false); this.reloadAll(); },
      error: (e: HttpErrorResponse) => { this.pledgedBusy.set(false); this._err(e, 'Could not refresh from Kite'); },
    });
  }

  // ── accounts ──────────────────────────────────────────────────────────────
  accounts = computed<FnoAccount[]>(() => this.summary()?.accounts || []);
  showAdd = signal(false);
  showAddAdvanced = signal(false);          // per-account API key fields (advanced/rare)
  // open the add-account modal cleanly — default to the shared paid app (no key)
  openAdd() {
    this.draft.set({ account_label: '', person: '', api_key: '', api_secret: '' });
    this.showAddAdvanced.set(false);
    this.pickerOpen.set(false);
    this.showAdd.set(true);
  }
  toggleAddAdvanced() {
    const on = !this.showAddAdvanced();
    this.showAddAdvanced.set(on);
    if (!on) this.draft.update(d => ({ ...d, api_key: '', api_secret: '' }));   // collapse → clear keys
  }
  showTrades = signal(false);   // trade-history popup (expanded from Open Positions)
  draft = signal({ account_label: '', person: '', api_key: '', api_secret: '' });
  saving = signal(false);
  busyAcc = signal<string | null>(null);   // account id with an action running

  // ── account picker (top-right dropdown, like the Stocks page) ──────────────
  pickerOpen = signal(false);
  // ── viewbar dropdowns: pick the viewing date / the shown accounts ──────────
  dateOpen = signal(false);
  acctOpen = signal(false);
  // ── account-holder cards (replace the old KPI-bar viewbar) ─────────────────
  accountStats = signal<Record<string, { overall: number; today: number; days: number }>>({});
  loadAccountStats() {
    this.svc.series('all', undefined, []).subscribe({          // ALL accounts, ignoring the current filter
      next: s => {
        const stats: Record<string, { overall: number; today: number; days: number }> = {};
        for (const pt of (s.points || [])) {
          const isToday = (pt.t || '').slice(0, 10) === this.todayKey;
          for (const a of (pt.by_account || [])) {
            const g = (stats[a.account_id] ??= { overall: 0, today: 0, days: 0 });
            g.overall += a.total; g.days += 1;
            if (isToday) g.today += a.total;
          }
        }
        this.accountStats.set(stats);
      },
      error: () => {},
    });
  }
  // per-account carried-forward unrealized (all accounts, ignoring the filter) so
  // each holder card can show Total = booked + unrealized. Marked to market via feed.
  accountUnreal = signal<Record<string, number>>({});
  loadAccountUnreal() {
    this.svc.openPositions([]).subscribe({
      next: o => {
        const m: Record<string, number> = {};
        for (const p of (o.positions || [])) {
          if (p.unrealized != null) m[p.account_id] = (m[p.account_id] || 0) + p.unrealized;
        }
        this.accountUnreal.set(m);
      },
      error: () => {},
    });
  }
  /** one card per account holder: Total P&L (booked + unrealized) + booked + today. */
  accountCards = computed(() => {
    const stats = this.accountStats();
    const unreal = this.accountUnreal();
    const l = this.live();
    const liveToday: Record<string, number> = {};
    if (l?.market_open) for (const a of l.accounts) liveToday[a.id] = a.day_pnl;
    return this.accounts().map(a => {
      const st = stats[a.id] || { overall: 0, today: 0, days: 0 };
      const booked = st.overall;
      const un = unreal[a.id] || 0;
      // Brokerage + taxes from the imported P&L statement — they eat into the
      // BOOKED (realised) profit, so net booked = booked − charges + other.
      const charges = a.pnl_charges ?? null;
      const hasCharges = charges != null && charges > 0;
      const netBooked = hasCharges ? booked - charges! + (a.pnl_other ?? 0) : booked;
      return {
        id: a.id, person: a.person, label: a.account_label,
        booked, unreal: un, total: netBooked + un,
        charges: hasCharges ? charges! : 0, hasCharges, netBooked,
        other: hasCharges ? (a.pnl_other ?? 0) : 0,
        today: liveToday[a.id] ?? st.today, days: st.days,
        shown: this.isShown(a.id),
      };
    });
  });
  /** click a holder card → view only that account; click the active one → all. */
  pickAccount(id: string) {
    const cur = this.selectedAccts();
    if (cur.size === 1 && cur.has(id)) { this.selectAll(); return; }
    this.selectedAccts.set(new Set([id])); this._persistSel(); this.reloadAll();
  }
  editingAcct = signal<string | null>(null);
  editDraft = signal({ account_label: '', person: '', priceFeed: false, strategy: '', apiKey: '', apiSecret: '' });
  savingEdit = signal(false);
  tbExpanded = signal<string | null>(null);        // account whose tradebook panel is open inline
  tbData = signal<FnoTradebooks | null>(null);     // tradebooks + coverage for the open panel
  tbLoading = signal(false);

  // Vertical timeline: one node per tradebook, oldest → newest down the spine.
  // Each node states its own start & end date plainly; the connector ABOVE it
  // reports the relationship to the previous book (clean handoff, N-day gap, or
  // an overlap) so coverage is obvious at a glance.
  tbTimeline = computed(() => {
    const d = this.tbData();
    const books = (d?.tradebooks || []).filter(b => b.date_from && b.date_to);
    if (!books.length) return null;
    const palette = ['#6ea8fe', '#63e6be', '#ffd43b', '#ff8787', '#b197fc', '#ffa94d'];
    const toDay = (s: string) => Math.floor(new Date(s + 'T00:00:00Z').getTime() / 86400000);
    const sorted = [...books].sort((a, b) =>
      toDay(a.date_from!) - toDay(b.date_from!) || toDay(a.date_to!) - toDay(b.date_to!));

    let prevEnd: number | null = null;   // running max end across all earlier books
    const entries = sorted.map((b, i) => {
      const f = toDay(b.date_from!), t = toDay(b.date_to!);
      let link: { type: 'gap' | 'overlap' | 'join'; days: number } | null = null;
      if (prevEnd !== null) {
        const diff = f - prevEnd;                 // days between prev end and this start
        if (diff > 1) link = { type: 'gap', days: diff - 1 };
        else if (diff <= 0) link = { type: 'overlap', days: Math.min(prevEnd, t) - f + 1 };
        else link = { type: 'join', days: 0 };    // back-to-back
      }
      prevEnd = prevEnd === null ? t : Math.max(prevEnd, t);
      return { rec: b, color: palette[i % palette.length], days: t - f + 1, link };
    });

    // union of all covered days (overlaps counted once)
    const ivals = sorted.map(b => [toDay(b.date_from!), toDay(b.date_to!)] as const);
    let covered = 0, curEnd = -Infinity;
    for (const [s, e] of ivals) {
      const from = Math.max(s, curEnd + 1);
      if (e >= from) covered += e - from + 1;
      curEnd = Math.max(curEnd, e);
    }
    const hasGap = entries.some(e => e.link?.type === 'gap');
    const hasOverlap = entries.some(e => e.link?.type === 'overlap');
    return {
      entries, from: d!.coverage.date_from, to: d!.coverage.date_to,
      coveredDays: covered, hasGap, hasOverlap,
    };
  });

  personOptions = computed(() => {
    const s = new Set<string>();
    for (const a of this.accounts()) if (a.person) s.add(a.person);
    return [...s].sort();
  });
  // suggestions for the "pin to strategy" field: built-ins + any already in use
  strategyOptions = computed(() => {
    const s = new Set<string>(['sentinel', 'crude', 'other']);
    for (const c of this.catalog()) s.add(c.key);
    for (const a of this.accounts()) if (a.strategy) s.add(a.strategy);
    for (const c of this.stratCards()) s.add(c.key);
    return this.orderStrats(s);
  });

  showSecret = signal(false);
  openRowEdit(a: FnoAccount) {
    this.tbExpanded.set(null);
    this.showSecret.set(false);
    this.editDraft.set({ account_label: a.account_label, person: a.person || '', priceFeed: !!a.price_feed, strategy: a.strategy || '', apiKey: '', apiSecret: '' });
    this.editingAcct.set(a.id);
    // Reveal the saved credentials so you can view / copy them (into Stocks).
    this.svc.credentials(a.id).subscribe({
      next: c => this.editDraft.update(d => (this.editingAcct() === a.id
        ? { ...d, apiKey: c.api_key || '', apiSecret: c.api_secret || '' } : d)),
      error: () => {},
    });
  }
  setEditField(k: 'account_label' | 'person' | 'strategy' | 'apiKey' | 'apiSecret', v: string) { this.editDraft.update(d => ({ ...d, [k]: v })); }
  toggleEditFeed() { this.editDraft.update(d => ({ ...d, priceFeed: !d.priceFeed })); }
  saveEdit() {
    const id = this.editingAcct(); if (!id) return;
    const d = this.editDraft();
    if (!d.account_label.trim()) return;
    const acc = this.accounts().find(x => x.id === id);
    const feedChanged = d.priceFeed !== !!acc?.price_feed;
    const stratNew = d.strategy.trim().toLowerCase();
    const stratChanged = stratNew !== (acc?.strategy || '');
    const finish = () => { this.savingEdit.set(false); this.editingAcct.set(null); this.reloadAll(); };
    // chain the (optional) price-feed and strategy writes after the label/person save
    const afterFeed = () => {
      if (!stratChanged) { finish(); return; }
      this.svc.setAccountStrategy(id, stratNew || null).subscribe({ next: finish, error: finish });
    };
    this.savingEdit.set(true);
    this.svc.editAccount(id, {
      account_label: d.account_label.trim(), person: d.person.trim(),
      // only send keys when the user typed them (blank → keep the stored key)
      api_key: d.apiKey.trim() || undefined, api_secret: d.apiSecret.trim() || undefined,
    }).subscribe({
      next: () => {
        if (!feedChanged) { afterFeed(); return; }
        // one account is the feed at a time: turning it on here designates it;
        // turning it off clears the feed entirely.
        this.svc.setPriceFeed(d.priceFeed ? id : null).subscribe({ next: afterFeed, error: afterFeed });
      },
      error: (e: HttpErrorResponse) => { this.savingEdit.set(false); this._err(e, 'Could not save'); },
    });
  }

  toggleTradebook(a: FnoAccount) {
    this.editingAcct.set(null);
    const opening = this.tbExpanded() !== a.id;
    this.tbExpanded.set(opening ? a.id : null);
    if (opening) this.loadTradebooks(a.id);
  }
  loadTradebooks(id: string) {
    this.tbData.set(null);
    this.tbLoading.set(true);
    this.svc.listTradebooks(id).subscribe({
      next: d => { this.tbData.set(d); this.tbLoading.set(false); },
      error: () => this.tbLoading.set(false),
    });
  }
  deleteOneTradebook(a: FnoAccount, tb: FnoTradebookRec) {
    if (!confirm(`Remove tradebook “${tb.name}” (${tb.date_from} → ${tb.date_to}, ${tb.count} fills)? Overlapping fills kept by other books stay.`)) return;
    this.busyAcc.set(a.id);
    this.svc.deleteOneTradebook(a.id, tb.id).subscribe({
      next: r => {
        this.busyAcc.set(null);
        this.tbData.set(r);                       // full payload (keeps statements)
        this.notice.set(`Removed “${tb.name}” · ${r.removed} fills cleared`);
        setTimeout(() => this.notice.set(null), 5000);
        this.reloadAll();
      },
      error: (e: HttpErrorResponse) => { this.busyAcc.set(null); this._err(e, 'Could not remove tradebook'); },
    });
  }
  deleteTradebookNow(a: FnoAccount) {
    if (!confirm(`Clear ALL tradebooks for “${a.account_label}”? Every imported fill is removed; live Kite trades stay.`)) return;
    this.busyAcc.set(a.id);
    this.svc.deleteTradebook(a.id).subscribe({
      next: r => {
        this.busyAcc.set(null);
        this.tbData.update(d => d ? { ...d, tradebooks: [], coverage: { date_from: null, date_to: null, fills: 0, books: 0 } } : d);
        this.notice.set(`All tradebooks removed · ${r.removed} fills cleared`);
        setTimeout(() => this.notice.set(null), 5000);
        this.reloadAll();                       // panel stays open → shows the empty state
      },
      error: (e: HttpErrorResponse) => { this.busyAcc.set(null); this._err(e, 'Could not delete tradebook'); },
    });
  }

  // ── manual token connect ─────────────────────────────────────────────────
  connectAcc = signal<string | null>(null);
  connectAccObj = computed<FnoAccount | null>(() =>
    this.accounts().find(a => a.id === this.connectAcc()) || null);
  tokenInput = signal('');
  accessTokenInput = signal('');
  showAdvanced = signal(false);
  connecting = signal(false);
  connectLoginUrl = signal('');       // Kite login URL for the account being connected (for incognito copy)
  linkCopied = signal(false);

  // ── chart (hand-built SVG · pan + wheel-zoom into a windowed viewport) ─────
  chartCanvas?: ElementRef<HTMLDivElement>;
  chartW = signal(700);                          // live canvas px width → responsive tick density
  private _ro?: ResizeObserver;
  @ViewChild('chartCanvas') set _cc(ref: ElementRef<HTMLDivElement> | undefined) {
    this.chartCanvas = ref;
    const el = ref?.nativeElement;
    if (!el) return;
    if (el.clientWidth) this.chartW.set(el.clientWidth);
    if (typeof ResizeObserver !== 'undefined') {
      this._ro?.disconnect();
      this._ro = new ResizeObserver(es => { const w = es[0]?.contentRect?.width; if (w) this.chartW.set(Math.round(w)); });
      this._ro.observe(el);
    }
  }
  range = signal<Range>('today');
  private _autoRanged = false;   // one-shot: empty, non-live "today" auto-falls back to All
  // 'today' is handled by its own market-aware pill, not this history dropdown
  ranges: { key: Range; label: string }[] = [
    { key: '1w', label: '1W' }, { key: '1m', label: '1M' },
    { key: '6m', label: '6M' }, { key: '1y', label: '1Y' }, { key: 'all', label: 'All' },
  ];
  /** the day the intraday chart is showing — shown in the chart subtitle */
  chartDayLabel = computed(() => {
    const d = this.selectedDay();
    const past = d && d !== this.todayKey;
    const dt = new Date((past ? d! : this.todayKey) + 'T00:00:00');
    const s = dt.toLocaleDateString('en-IN', { weekday: 'short', day: '2-digit', month: 'short', year: 'numeric' });
    return past ? s : ('Today · ' + s);
  });
  rangeLabel = computed(() => {
    if (this.selectedDay()) return this.fmtDate(this.selectedDay());
    return this.ranges.find(r => r.key === this.range())?.label || 'Today';
  });
  filterOpen = signal(false);
  chartMode = signal<'intraday' | 'daily'>('intraday');
  private seriesBase = signal<ChartPt[]>([]);
  private liveTail = signal<ChartPt[]>([]);
  chartStats = signal<{ label: string; value: string; cls?: string; tip?: string }[]>([]);
  hoverI = signal<number | null>(null);
  private lastLiveSec = 0;
  private lastTailVal: number | null = null;   // last P&L drawn — skip flat ticks
  // Every chart fetch (loadSeries / loadOpenChart) bumps this; a response only
  // paints if it's still the latest request. Kills the account-switch race where
  // a stale response (previous account) lands after a fresh one and overwrites it.
  private _chartGen = 0;
  chartOpenBook = signal(false);   // is the chart currently the open-unrealized graph?

  /** calendar day being inspected (chart + trade history follow it) */
  selectedDay = signal<string | null>(null);

  /** intraday zoom preset: whole session, or a window on the last 60 minutes */
  intradayZoom = signal<'full' | '1h'>('1h');   // the only intraday view now: today · last hour
  setZoom(z: 'full' | '1h') {
    this.intradayZoom.set(z);
    this.hoverI.set(null);
    if (z === '1h') this.applyLastHour(); else this.resetView();
  }
  /** right value-gutter width (px). Wide + a price pill ONLY in the zoomed
   *  Last-hour view; Full day and ranges use the whole width (value → footer). */
  valGutter = computed(() => this.chartMode() === 'intraday' && this.intradayZoom() === '1h' ? 68 : 0);

  // ── pan/zoom viewport — a [start,end] window as fractions of the x-range ────
  winStart = signal(0);
  winEnd = signal(1);
  private dragState: { x: number; w0: number; w1: number } | null = null;
  isZoomed = computed(() => this.winStart() > 0.001 || this.winEnd() < 0.999);
  resetView() { this.winStart.set(0); this.winEnd.set(1); this.hoverI.set(null); }

  private _applyWin(s: number, e: number) {
    const minW = 0.02;                             // max zoom = 2% of the range
    if (e - s < minW) { const mid = (s + e) / 2; s = mid - minW / 2; e = mid + minW / 2; }
    if (s < 0) { e -= s; s = 0; }
    if (e > 1) { s -= (e - 1); e = 1; }
    this.winStart.set(Math.max(0, +s.toFixed(5)));
    this.winEnd.set(Math.min(1, +e.toFixed(5)));
  }

  /** "Last hour" = a zoomed WINDOW into the day (not a data filter). The live
   *  intraday view already holds only ~an hour, so this shows the whole thing;
   *  on a longer series it zooms to the final hour, pannable from there. */
  applyLastHour() {
    const pts = this.chartData();
    if (pts.length < 2) { this.resetView(); return; }
    const span = pts[pts.length - 1].x - pts[0].x;
    if (span <= 3600) { this.resetView(); return; }      // under an hour → show all
    this.winStart.set(Math.max(0, +(1 - 3600 / span).toFixed(5)));
    this.winEnd.set(1);
    this.hoverI.set(null);
  }

  onWheel(ev: WheelEvent) {
    const el = this.chartCanvas?.nativeElement; if (!el) return;
    ev.preventDefault();
    const rect = el.getBoundingClientRect();
    const cursor = Math.max(0, Math.min(1, (ev.clientX - rect.left) / rect.width));
    const w0 = this.winStart(), w1 = this.winEnd(), w = w1 - w0;
    const at = w0 + cursor * w;                          // cursor position in full-range fraction
    // proportional to scroll amount → smooth on a trackpad, snappy on a wheel
    const factor = Math.min(3, Math.max(0.33, Math.exp(ev.deltaY * 0.0016)));
    const nw = Math.min(1, Math.max(0.015, w * factor));
    this._applyWin(at - cursor * nw, at - cursor * nw + nw);
    this.hoverI.set(null);
  }

  onPointerDown(ev: PointerEvent) {
    if (!this.isZoomed()) return;                        // nothing to pan when the whole range is shown
    if (ev.button !== 0 && ev.pointerType === 'mouse') return;
    this.dragState = { x: ev.clientX, w0: this.winStart(), w1: this.winEnd() };
    (ev.target as HTMLElement).setPointerCapture?.(ev.pointerId);
    this.hoverI.set(null);
  }
  onPointerMove(ev: PointerEvent) {
    const el = this.chartCanvas?.nativeElement; if (!el) return;
    if (this.dragState) {
      const rect = el.getBoundingClientRect();
      const w = this.dragState.w1 - this.dragState.w0;
      const dx = ((ev.clientX - this.dragState.x) / rect.width) * w;   // 1:1 with the pointer
      this._applyWin(this.dragState.w0 - dx, this.dragState.w1 - dx);
    } else {
      this.onChartMove(ev);
    }
  }
  onPointerUp() { this.dragState = null; }

  chartData = computed<ChartPt[]>(() => {
    if (this.chartMode() !== 'intraday') return this.seriesBase();
    const base = this.seriesBase();
    const lastX = base.length ? base[base.length - 1].x : 0;
    return [...base, ...this.liveTail().filter(p => p.x > lastX)];
  });

  private niceStep(span: number): number {
    if (span <= 0) return 1;
    const p = Math.pow(10, Math.floor(Math.log10(span / 4)));
    for (const m of [1, 2, 2.5, 5, 10]) if (span / (m * p) <= 5) return m * p;
    return 10 * p;
  }
  /** a round clock step (seconds) so a window shows ≈`target` time ticks */
  private niceTimeStep(spanSec: number, target: number): number {
    const S = [60, 120, 300, 600, 900, 1800, 3600, 7200, 14400, 21600, 43200]; // 1m … 12h
    for (const s of S) if (spanSec / s <= target) return s;
    return S[S.length - 1];
  }
  private fmtClock(epochSec: number): string {
    return new Date(epochSec * 1000).toLocaleTimeString('en-IN',
      { hour: '2-digit', minute: '2-digit', hour12: false, timeZone: 'Asia/Kolkata' });
  }

  // The intraday chart is a SMOOTH ROLLING WINDOW of the last ~hour — the DB only
  // keeps that hour of minute snapshots, so the axis simply fits the data. A gap
  // between captures wider than GAP_SEC means the book was flat (the engine only
  // snapshots while positions are open) → drawn as a dotted "no-trade, value
  // unchanged" bridge rather than a straight interpolation.
  private readonly GAP_SEC = 150;          // >2.5 min between marks ⇒ no-trade stretch

  /** the live intraday view (today, minute/second detail) vs a past day / range */
  private isTodayIntraday(): boolean {
    if (this.chartMode() !== 'intraday') return false;
    const day = this.selectedDay();
    return !day || day === this.todayKey;
  }

  cg = computed<ChartGeom>(() => {
    const intraday = this.isTodayIntraday();
    const pts = this.chartData();
    if (!pts.length) {
      return { empty: true, zeroPct: 100, zeroFrac: 1, areaPath: '', linePath: '', dashPath: '', grid: [], xticks: [], pts: [], last: null };
    }
    // viewport window into the x-range (pan/zoom); [0,1] = whole series
    const fullMin = pts[0].x, fullMax = pts[pts.length - 1].x > pts[0].x ? pts[pts.length - 1].x : pts[0].x + 1;
    const span = fullMax - fullMin;
    const winX0 = fullMin + this.winStart() * span;
    const winX1 = fullMin + this.winEnd() * span;
    const winSpan = (winX1 - winX0) || 1;
    const xP = (t: number) => +(((t - winX0) / winSpan) * 100).toFixed(3);

    let vMin: number, vMax: number;
    if (intraday) {
      // ABSOLUTE P&L axis: zooming changes only the TIME window, never the vertical
      // scale — so an hour that ticks 50k→51k reads as ₹51k near the top, not a
      // magnified ₹1k wiggle. Scale to the whole series and always anchor ₹0.
      const vs = pts.map(p => p.v);
      const hi = Math.max(0, ...vs), lo = Math.min(0, ...vs);
      const pad = (hi - lo) * 0.08 || 1;
      vMax = hi + pad;
      vMin = lo - (lo < 0 ? pad : 0);            // don't pad below ₹0 when all-positive
    } else {
      // daily/range: auto-scale to the VISIBLE window so panning a range reveals detail
      const eps = winSpan * 0.001;
      const visPts = pts.filter(p => p.x >= winX0 - eps && p.x <= winX1 + eps);
      const src = visPts.length ? visPts : pts;
      const vs = src.map(p => p.v);
      vMin = Math.min(...vs); vMax = Math.max(...vs);
      if (vMax - vMin < 1e-9) { vMax += Math.max(1, Math.abs(vMax) * 0.02); vMin -= Math.max(1, Math.abs(vMin) * 0.02); }
      const padV = (vMax - vMin) * 0.12;
      vMax += padV; vMin -= padV;
    }
    const yP = (v: number) => +(((vMax - v) / (vMax - vMin)) * 100).toFixed(3);
    const zeroPct = Math.max(0, Math.min(100, yP(0)));

    let P = pts.map(p => ({ xPct: xP(p.x), yPct: yP(p.v) }));
    if (P.length === 1) P = [{ xPct: 0, yPct: P[0].yPct }, { xPct: 100, yPct: P[0].yPct }];

    // Split the line into SOLID runs (captures ≤GAP apart = active, per-minute)
    // and DOTTED bridges wherever a gap means the book was flat — no trade, so
    // P&L held its value: carried horizontally, then reconnected. Only the live
    // intraday view marks gaps; daily ranges are one continuous line.
    const poly = (seg: { xPct: number; yPct: number }[]) =>
      seg.map((q, i) => `${i ? 'L' : 'M'}${q.xPct} ${q.yPct}`).join(' ');
    const solid: string[] = [];
    const dash: string[] = [];
    if (P.length === 1) {
      solid.push(poly(P));
    } else {
      let run = [P[0]];
      for (let i = 1; i < P.length; i++) {
        if (intraday && pts[i].x - pts[i - 1].x > this.GAP_SEC) {
          if (run.length > 1) solid.push(poly(run));
          dash.push(`M${P[i - 1].xPct} ${P[i - 1].yPct} L${P[i].xPct} ${P[i - 1].yPct} L${P[i].xPct} ${P[i].yPct}`);
          run = [P[i]];
        } else {
          run.push(P[i]);
        }
      }
      if (run.length > 1) solid.push(poly(run));
    }
    const linePath = solid.join(' ');
    const dashPath = dash.join(' ');
    const areaPath = `${poly(P)} L${P[P.length - 1].xPct} ${zeroPct} L${P[0].xPct} ${zeroPct} Z`;

    const step = this.niceStep(vMax - vMin);
    const grid: { topPct: number; label: string }[] = [];
    for (let v = Math.ceil(vMin / step) * step; v <= vMax + 1e-9; v += step) {
      grid.push({ topPct: yP(v), label: this.axisInr(v) });
    }

    // X-ticks — responsive density (≈one label per 80–110px, so they never
    // collide on a phone yet aren't sparse on a monitor).
    const xticks: { leftPct: number; label: string; year?: string }[] = [];
    if (intraday) {
      // ROUND clock marks (…09:15, 09:30, 10:00…) aligned to IST, at a step that
      // fits the visible time window — always legible, never arbitrary fills.
      const target = Math.max(3, Math.min(9, Math.floor(this.chartW() / 78)));
      const stepSec = this.niceTimeStep(winSpan, target);
      const IST = 5.5 * 3600;                                 // align steps to IST clock, not UTC
      const first = Math.ceil((winX0 + IST) / stepSec) * stepSec - IST;
      for (let t = first; t <= winX1 + 1; t += stepSec) {
        const xp = +(((t - winX0) / winSpan) * 100).toFixed(3);
        if (xp >= -0.2 && xp <= 100.2) xticks.push({ leftPct: xp, label: this.fmtClock(t) });
      }
    } else {
      // daily/range: real date points, year stamped only when it changes
      const vis = pts.map((p, i) => ({ label: p.label, xp: P[i].xPct, year: p.year }))
        .filter(o => o.xp >= -0.2 && o.xp <= 100.2);
      const m = vis.length, nt = Math.min(Math.max(3, Math.floor(this.chartW() / 104)), m);
      const seen = new Set<string>();
      let prevYear: number | undefined;
      for (let k = 0; k < nt; k++) {
        const o = vis[Math.round(k * (m - 1) / Math.max(1, nt - 1))];
        if (o && !seen.has(o.label)) {
          seen.add(o.label);
          const yr = o.year !== undefined && o.year !== prevYear ? String(o.year) : undefined;
          if (o.year !== undefined) prevYear = o.year;
          xticks.push({ leftPct: o.xp, label: o.label, year: yr });
        }
      }
    }

    const lastPt = P[P.length - 1];
    const lastInView = lastPt.xPct >= -0.5 && lastPt.xPct <= 100.5;
    return {
      empty: false, zeroPct, zeroFrac: zeroPct / 100, areaPath, linePath, dashPath, grid, xticks, pts: P,
      last: lastInView ? { xPct: Math.min(100, lastPt.xPct), yPct: lastPt.yPct, pos: pts[pts.length - 1].v >= 0 } : null,
    };
  });

  hoverPt = computed(() => {
    const i = this.hoverI();
    const pts = this.chartData();
    const g = this.cg();
    if (i == null || i < 0 || i >= pts.length || g.empty) return null;
    const s = g.pts[Math.min(i, g.pts.length - 1)];
    return { ...pts[i], xPct: s.xPct, yPct: s.yPct };
  });
  /** newest plotted value — shown in the right-axis pill */
  lastVal = computed(() => { const d = this.chartData(); return d.length ? d[d.length - 1].v : 0; });
  /** today's per-minute OPEN unrealized (the ₹52k book) — loaded alongside the
   *  day-P&L line purely so the hover can reveal the open P&L at that time. */
  private openSeriesPts = signal<{ x: number; v: number }[]>([]);
  loadOpenSeriesForHover() {
    this.svc.openSeries(this.selectedIds()).subscribe({
      next: os => this.openSeriesPts.set((os.points || []).map(p => ({ x: this.parseMinute(p.t), v: p.pnl }))),
      error: () => this.openSeriesPts.set([]),
    });
  }
  /** the OPEN P&L at the hovered time — forward-filled from the open-series, and
   *  the live current value at/after the latest mark. Shown in the hover tooltip. */
  hoverOpenPnl = computed<number | null>(() => {
    if (this.chartMode() !== 'intraday') return null;
    const hp = this.hoverPt(); if (!hp) return null;
    const pts = this.openSeriesPts(); if (!pts.length) return null;
    const x = hp.x;
    if (x >= pts[pts.length - 1].x - 1) return this.openUnrealized();   // at/after last mark → live
    let v = pts[0].v;
    for (const p of pts) { if (p.x <= x + 1) v = p.v; else break; }     // forward-fill
    return v;
  });
  /** on the open-unrealized chart: how the hovered point moved vs the session's
   *  first mark — the "since session open" figure in the hover side-panel. */
  hoverSince = computed(() => {
    if (!this.chartOpenBook()) return null;
    const hp = this.hoverPt(); if (!hp) return null;
    const base = this.seriesBase();
    return hp.v - (base.length ? base[0].v : 0);
  });

  axisInr(v: number): string {
    const a = Math.abs(v); const s = v < 0 ? '−' : '';
    // 2 decimals for L/Cr so adjacent gridlines stay distinct on a narrow range
    // (e.g. 1.36L vs 1.40L vs 1.45L — not all collapsing to "1.4L")
    if (a >= 1e7) return `${s}₹${(a / 1e7).toFixed(2)}Cr`;
    if (a >= 1e5) return `${s}₹${(a / 1e5).toFixed(2)}L`;
    if (a >= 1e3) return `${s}₹${(a / 1e3).toFixed(a >= 1e4 ? 0 : 1)}k`;
    return `${s}₹${Math.round(a)}`;
  }

  onChartMove(ev: MouseEvent) {
    const el = this.chartCanvas?.nativeElement;
    const g = this.cg();
    if (!el || g.empty || !g.pts.length) return;
    const rect = el.getBoundingClientRect();
    const xPct = (ev.clientX - rect.left) / rect.width * 100;
    let best = 0, bestD = Infinity;
    for (let i = 0; i < g.pts.length; i++) {
      const d = Math.abs(g.pts[i].xPct - xPct);
      if (d < bestD) { bestD = d; best = i; }
    }
    this.hoverI.set(best);
  }

  // ── calendar ──────────────────────────────────────────────────────────────
  // IST wall-clock as a Date whose UTC-fields read IST, rolled back before
  // 03:00 so "today" matches the 9AM-anchored backend trading session.
  private now = (() => {
    const ist = new Date(Date.now() + 330 * 60000);
    if (ist.getUTCHours() < 3) ist.setUTCDate(ist.getUTCDate() - 1);
    return ist;
  })();
  viewYear = signal(this.now.getUTCFullYear());
  viewMonth = signal(this.now.getUTCMonth());              // 0-based
  calData = signal<FnoCalendar | null>(null);
  weekdays = ['S', 'M', 'T', 'W', 'T', 'F', 'S'];
  monthLabel = computed(() => new Date(this.viewYear(), this.viewMonth(), 1)
    .toLocaleString('en-IN', { month: 'long', year: 'numeric' }));

  // ── trade history (every fill; follows the selected calendar day) ─────────
  allTrades = signal<FnoTrade[]>([]);
  tradesLoading = signal(false);
  tradeStrat = signal<string>('all');
  tradeQuery = signal('');
  private accLabels = computed<Record<string, string>>(() => {
    const m: Record<string, string> = {};
    for (const a of this.accounts()) m[a.id] = a.account_label;
    return m;
  });
  // "whose account" — prefer the person's name, fall back to the account label
  private accWhoseMap = computed<Record<string, string>>(() => {
    const m: Record<string, string> = {};
    for (const a of this.accounts()) m[a.id] = (a.person && a.person.trim()) || a.account_label;
    return m;
  });
  filteredTrades = computed<FnoTrade[]>(() => {
    const strat = this.tradeStrat();
    const q = this.tradeQuery().trim().toUpperCase();
    const day = this.selectedDay();
    let rows = this.allTrades();
    if (day) rows = rows.filter(t => (t.trade_date || '').slice(0, 10) === day);
    if (strat !== 'all') rows = rows.filter(t => t.strategy === strat);
    if (q) rows = rows.filter(t => (t.tradingsymbol || '').toUpperCase().includes(q));
    return [...rows].sort((a, b) =>
      (b.fill_ts || b.trade_date || '').localeCompare(a.fill_ts || a.trade_date || ''));
  });
  tradeCounts = computed(() => {
    const day = this.selectedDay();
    const c: Record<string, number> = { all: 0 };
    for (const t of this.allTrades()) {
      if (day && (t.trade_date || '').slice(0, 10) !== day) continue;
      c['all']++; c[t.strategy] = (c[t.strategy] || 0) + 1;
    }
    return c;
  });
  // filter chips for the trade-history popup — "All" + every strategy actually present
  tradePills = computed(() => {
    const c = this.tradeCounts();
    const keys = this.orderStrats(Object.keys(c).filter(k => k !== 'all'));
    return [{ key: 'all', label: 'All', count: c['all'] || 0 },
            ...keys.map(k => ({ key: k, label: this.stratShort(k), count: c[k] || 0 }))];
  });
  accLabel(id: string): string { return this.accLabels()[id] || ''; }
  accWhose(id: string): string { return this.accWhoseMap()[id] || this.accLabels()[id] || ''; }
  /** all stored fills are from today → history clearly needs a backfill */
  needsBackfill = computed(() => {
    const t = this.allTrades();
    return !t.length || t.every(x => (x.trade_date || '').slice(0, 10) === this.todayKey);
  });
  firstConnected = computed<FnoAccount | null>(() =>
    this.accounts().find(a => a.connected) || this.accounts()[0] || null);

  /** calendar row for the selected day (strategy chips in the history header) */
  selectedDayData = computed<FnoCalendarDay | null>(() => {
    const d = this.selectedDay();
    if (!d) return null;
    return (this.calData()?.days || []).find(x => x.date === d) || null;
  });

  /** Compact per-strategy cards for the SELECTED day (or today when none is
   *  picked): P&L, realized, open positions and total positions taken. Live
   *  numbers (realized/open) come from the socket for today; a completed past
   *  day is fully realized with nothing left open. */
  stratCards = computed(() => {
    const day = this.selectedDay();
    const isToday = !day || day === this.todayKey;
    const l = this.live();
    const marketLive = isToday && !!l?.market_open && this.liveAccts().length > 0;

    // trades on the effective day (selected day, or today by default) → distinct
    // instruments per strategy = positions taken that day
    const effDay = day || this.todayKey;
    const dayTrades = this.allTrades().filter(t => (t.trade_date || '').slice(0, 10) === effDay);
    const instr: Record<string, Set<string>> = {};
    for (const t of dayTrades) (instr[t.strategy] ??= new Set()).add(t.tradingsymbol);

    // live per-strategy realized / open / positions (only meaningful while the
    // market is open today), summed across the selected accounts
    const agg: Record<string, { realized: number; open: number; positions: number }> = {};
    if (marketLive) {
      for (const a of this.liveAccts()) {
        for (const [k, s] of Object.entries(a.by_strategy || {})) {
          const g = (agg[k] ??= { realized: 0, open: 0, positions: 0 });
          g.realized += s.realized; g.open += s.open_positions; g.positions += s.positions;
        }
      }
    }

    // carried-forward open exposure per strategy (marked-to-market, from the REST
    // open-positions endpoint) — gives "Unrealized" and an open-leg count that is
    // meaningful even when the market is closed.
    const openByStrat: Record<string, { unrealized: number; count: number }> = {};
    for (const s of this.openPos()?.by_strategy || []) openByStrat[s.strategy] = { unrealized: s.unrealized, count: s.count };

    // that day's P&L per strategy: the selected calendar day, else live (market
    // open), else the last-stored "today" from the strategy stats.
    const pnlMap: Record<string, number> = {};
    if (day && !isToday) {
      Object.assign(pnlMap, this.selectedDayData()?.by_strategy || {});
    } else if (marketLive) {
      // scope to the SELECTED accounts (not the global live.by_strategy) so a
      // single-account view shows only that account's strategies
      for (const a of this.liveAccts()) {
        for (const [k, s] of Object.entries(a.by_strategy || {})) {
          pnlMap[k] = (pnlMap[k] || 0) + (s.day_pnl || 0);
        }
      }
    } else {
      for (const s of this.strategies()) pnlMap[s.strategy] = s.today;
      if (this.selectedDayData()) Object.assign(pnlMap, this.selectedDayData()!.by_strategy);
    }

    const keys = this.orderStrats([
      ...Object.keys(instr), ...Object.keys(agg), ...Object.keys(openByStrat), ...Object.keys(pnlMap),
    ]);
    return keys.map(k => {
      const pnl = pnlMap[k] ?? 0;
      const realized = marketLive ? (agg[k]?.realized ?? pnl) : pnl;   // closed market/day → fully realized
      const carried = openByStrat[k];
      const unrealized = carried?.unrealized ?? 0;
      // open legs: live count while the market is open, else carried-forward count
      const open = marketLive ? (agg[k]?.open ?? 0) : (carried?.count ?? 0);
      const total = instr[k]?.size ?? agg[k]?.positions ?? 0;
      return { key: k, label: this.stratShort(k), pnl, realized, unrealized, open, total };
    });
  });

  // Current carried-forward unrealized P&L per strategy (live at-risk, "now").
  private carriedByStrat = computed<Record<string, number>>(() => {
    const m: Record<string, number> = {};
    for (const s of this.openPos()?.by_strategy || []) m[s.strategy] = s.unrealized;
    return m;
  });

  // Per-strategy booked P&L summed over the currently-selected chart RANGE
  // (1w/1m/6m/1y/all) — from the daily series' per-day by_strategy breakdown.
  private rangeStratBooked = computed<Record<string, number>>(() => {
    const m: Record<string, number> = {};
    for (const p of this.seriesBase()) {
      for (const [k, v] of Object.entries(p.by_strategy || {})) m[k] = (m[k] || 0) + (v || 0);
    }
    return m;
  });

  // ── strategy scope — per-strategy P&L IN SYNC with the P&L chart & calendar.
  //    Carried-forward unrealized is NOT part of any day's P&L (it only books
  //    when the leg is finally closed — see openPos comment), so it is NEVER
  //    folded into a *selected day's* total: doing that made a day's booked loss
  //    (−22k) net against the whole open book's lifetime m2m (+16.6k) into a
  //    misleading −5.4k that contradicted the chart. Carried unrealized only
  //    appears in the portfolio "to-date" view (no day picked), where it
  //    reconciles with the KPI bar's Total P&L.
  private stratScope = computed<{ booked: Record<string, number>; unreal: Record<string, number> }>(() => {
    const day = this.selectedDay();
    const r = this.range();
    const booked: Record<string, number> = {};
    let unreal: Record<string, number> = {};

    if (day) {
      if (day === this.todayKey && this.live()?.market_open && Object.keys(this.liveStratSplit()).length) {
        // today, live → BOOKED = realized (stable) and UNREALIZED = live m2m
        // (moves every tick), shown apart. booked + unreal = day_pnl = the chart's
        // live line, but booked no longer flickers and the unrealized is visible.
        for (const [k, v] of Object.entries(this.liveStratSplit())) {
          booked[k] = v.realized;
          unreal[k] = v.unrealized;
        }
      } else {
        // a past day (or market shut) → that day's stored booked P&L, no live m2m.
        Object.assign(booked, this.selectedDayData()?.by_strategy || {});   // matches the calendar cell
      }
    } else if (r === 'today') {
      // portfolio "to-date" view → each strategy's ALL-TIME booked P&L + live
      // carried unrealized, so the bars reconcile with the KPI bar's Total P&L.
      for (const s of this.strategies()) booked[s.strategy] = s.total;
      unreal = this.carriedByStrat();
    } else {
      // a range (1w/1m/6m/1y/all) → booked summed over the range + current at-risk
      Object.assign(booked, this.rangeStratBooked());
      unreal = this.carriedByStrat();
    }
    return { booked, unreal };
  });

  // Bullet-bar geometry — ₹0 pinned to the CENTRE, two segments only (booked +
  // unrealized) diverging from it, and ONE shared scale across all rows so their
  // magnitudes are directly comparable. No track fill, no notch, no tip.
  stratBars = computed(() => {
    const { booked: bMap, unreal: uMap } = this.stratScope();
    const keys = this.orderStrats([
      ...Object.keys(bMap), ...Object.keys(uMap),
    ]).filter(k => Math.abs(bMap[k] || 0) > 0.005 || Math.abs(uMap[k] || 0) > 0.005);

    // shared magnitude = the furthest any bar reaches from ₹0, across all rows —
    // booked end, unrealized end, or their stacked total, whichever is largest.
    const mag = Math.max(1, ...keys.map(k => {
      const b = bMap[k] || 0, u = uMap[k] || 0;
      return Math.max(Math.abs(b), Math.abs(u), Math.abs(b + u));
    })) * 1.08;

    const zero = 50, span = 46;                          // ₹0 centred, 46% of the track each side
    const x = (v: number) => zero + (v / mag) * span;

    return keys.map(k => {
      const booked = bMap[k] || 0;
      const unreal = uMap[k] || 0;
      const total = booked + unreal;
      const bx = x(booked);
      // booked segment always grows from the centre. The unrealized segment
      // stacks BEYOND booked when they share a sign (so the bar reads booked→total);
      // when they oppose, it diverges from the centre on its own side instead of
      // ploughing back through ₹0.
      const sameSide = Math.abs(booked) < 0.005 || (booked >= 0) === (unreal >= 0);
      let unrealLeft: number, unrealWidth: number;
      if (sameSide) {
        const tx = x(total);
        unrealLeft = Math.min(bx, tx); unrealWidth = Math.abs(tx - bx);
      } else {
        const ux = x(unreal);
        unrealLeft = Math.min(zero, ux); unrealWidth = Math.abs(ux - zero);
      }
      return {
        key: k, label: this.stratShort(k), color: this.stratColor(k), booked, unrealized: unreal, total,
        bookedLeft: Math.min(zero, bx), bookedWidth: Math.abs(bx - zero), bookedPos: booked >= 0,
        unrealLeft, unrealWidth, unrealPos: unreal >= 0,
        hasBooked: Math.abs(booked) > 0.005, hasUnreal: Math.abs(unreal) > 0.005,
      };
    });
  });

  // ── Payoff diagram ──────────────────────────────────────────────────────────
  // A Sensibull-style "P&L at expiry" hockey-stick for the OPEN book. It reads the
  // very same open legs the table shows, tick the checkboxes to chart just the ones
  // you care about (none ticked = the whole book). All ₹ figures use the leg's own
  // qty × avg × ₹-per-point multiplier, so crude (×100) and index options agree.
  payoffCanvas?: ElementRef<HTMLDivElement>;
  @ViewChild('payoffCanvas') set _pc(ref: ElementRef<HTMLDivElement> | undefined) { this.payoffCanvas = ref; }
  payoffRoot = signal<string | null>(null);       // underlying being charted (null = auto-pick)
  payoffHoverX = signal<number | null>(null);     // hovered underlying price (px→price)

  /** open legs that can be charted: live (non-closed) options w/ a strike, or futures */
  private payoffCandidates = computed<FnoOpenLeg[]>(() =>
    this.openLegs().filter(p => !p.closed && p.qty &&
      (p.opt_type === 'FUT' || ((p.opt_type === 'CE' || p.opt_type === 'PE') && p.strike != null))));
  /** the legs actually feeding the chart — the ticked ones, or all when none are ticked */
  private payoffLegsRaw = computed<FnoOpenLeg[]>(() => {
    const sel = this.selLegs();
    const cand = this.payoffCandidates();
    const picked = sel.size ? cand.filter(p => sel.has(this.legKey(p))) : cand;
    return picked;
  });
  /** underlyings present in the charted legs, most-legs-first (for the root switcher) */
  payoffRoots = computed<{ root: string; count: number }[]>(() => {
    const m = new Map<string, number>();
    for (const p of this.payoffLegsRaw()) m.set(p.root || '?', (m.get(p.root || '?') || 0) + 1);
    return [...m.entries()].map(([root, count]) => ({ root, count })).sort((a, b) => b.count - a.count);
  });
  activePayoffRoot = computed<string | null>(() => {
    const roots = this.payoffRoots();
    if (!roots.length) return null;
    const chosen = this.payoffRoot();
    return chosen && roots.some(r => r.root === chosen) ? chosen : roots[0].root;
  });
  setPayoffRoot(r: string) { this.payoffRoot.set(r); this.payoffHoverX.set(null); this.projDays.set(0); this.pfScrub.set(null); this.pfWinStart.set(0); this.pfWinEnd.set(1); this.resetDrafts(); }
  hasPayoff = computed(() => this.payoffCandidates().length > 0);

  /** the real legs feeding the chart (root-scoped, isolation-aware) — for the
   *  fullscreen instruments rail: symbol · side/qty · LTP · live movement */
  chartLegs = computed<FnoOpenLeg[]>(() => {
    const root = this.activePayoffRoot();
    if (!root) return [];
    const sel = this.selLegs();
    return this.payoffCandidates().filter(p =>
      (p.root || '?') === root && (!sel.size || sel.has(this.legKey(p))));
  });

  /** the charted position's live "today" move (sum of its legs' day P&L) — hero KPI */
  chartedToday = computed(() => {
    let has = false, s = 0;
    for (const l of this.chartLegs()) { if (l.day_pnl != null) { has = true; s += l.day_pnl; } }
    return has ? s : null;
  });

  /** padded price window [xMin,xMax] around a set of strikes + spot (payoff x-axis) */
  private priceWindow(strikes: number[], spot: number | null, fallback: number) {
    const pool = [...strikes, ...(spot && spot > 0 ? [spot] : [])];
    const lo = pool.length ? Math.min(...pool) : fallback;
    const hi = pool.length ? Math.max(...pool) : fallback;
    const mid = (spot && spot > 0) ? spot : ((lo + hi) / 2 || fallback || 1);
    const spread = Math.max(hi - lo, mid * 0.05);
    const half = spread * 0.7 + mid * 0.03;
    return { xMin: Math.max(0, Math.min(lo, mid) - half), xMax: Math.max(hi, mid) + half };
  }

  /** every underlying in the OPEN book (not just the charted one) → its own price
   *  slider + instruments group. The active root drives the chart; the rest switch
   *  the chart to themselves when scrubbed. */
  bookRoots = computed(() => {
    const active = this.activePayoffRoot();
    const map = new Map<string, FnoOpenLeg[]>();
    for (const p of this.payoffCandidates()) {
      const r = p.root || '?';
      const arr = map.get(r); if (arr) arr.push(p); else map.set(r, [p]);
    }
    const out = [...map.entries()].map(([root, legs]) => {
      const spot = this.rootSpot(root);
      const strikes = legs.filter(l => l.opt_type !== 'FUT' && l.strike != null).map(l => l.strike as number);
      const { xMin, xMax } = this.priceWindow(strikes, spot, legs[0]?.avg ?? 1);
      const net = legs.reduce((s, l) => s + (l.unrealized ?? 0), 0);
      let expiry: string | null = null;
      for (const l of legs) if (l.expiry && (!expiry || l.expiry < expiry)) expiry = l.expiry;
      const dte = expiry ? Math.max(0, Math.round((Date.parse(expiry + 'T15:30:00+05:30') - Date.now()) / 86400000)) : null;
      return { root, spot, hasSpot: spot != null && spot > 0, xMin, xMax, count: legs.length, legs, net, dte, active: root === active };
    });
    // stable order by leg-count (NOT active-first — else a slider jumps under the
    // cursor the moment scrubbing it makes its root active).
    return out.sort((a, b) => b.count - a.count || (a.root < b.root ? -1 : 1));
  });
  hasMultiRoot = computed(() => this.bookRoots().length > 1);

  rootStep(r: { xMin: number; xMax: number }): number {
    const raw = (r.xMax - r.xMin) / 240;
    return raw > 0 ? +raw.toPrecision(2) : 1;
  }
  /** best-effort underlying price for a root: backend spot → a futures leg's LTP →
   *  (last resort) the mid of its strikes, so a theta curve is ALWAYS drawable. */
  private rootSpot(root: string): number | null {
    const backend = (this.openPos()?.spots || {})[root];
    if (backend != null && backend > 0) return backend;
    const raw = this.payoffCandidates().filter(p => (p.root || '?') === root);
    const fut = raw.find(p => p.opt_type === 'FUT' && p.ltp != null);
    if (fut?.ltp) return fut.ltp;
    const ks = raw.filter(p => p.strike != null).map(p => p.strike as number);
    return ks.length ? (Math.min(...ks) + Math.max(...ks)) / 2 : null;
  }

  /** median backed-out IV of a set of legs, else a spot-scaled default (index-scale
   *  ~13%, stock-scale ~28%) — fills in a theta curve when a leg's IV won't back out */
  private fallbackIV(legs: { iv: number | null }[], spot: number): number {
    const got = legs.map(l => l.iv).filter((v): v is number => v != null).sort((a, b) => a - b);
    if (got.length) return got[Math.floor(got.length / 2)];
    return spot >= 5000 ? 0.14 : 0.28;
  }

  /** ₹ P&L of a whole underlying's book at `daysFwd` from now, priced at `price`
   *  (falls back to live MTM when there's no spot). Powers the per-underlying dock
   *  values so the ONE Today slider projects EVERY instrument, not just the chart. */
  private rootPnlAt(root: string, price: number | null, daysFwd: number): number {
    const now = Date.now();
    const raw = this.payoffCandidates().filter(p => (p.root || '?') === root);
    const spot = this.rootSpot(root);
    const legs: PayoffLeg[] = raw.map(p => {
      const type = (p.opt_type || 'FUT') as 'CE' | 'PE' | 'FUT';
      let tY = 0;
      if (p.expiry) tY = Math.max(0, (Date.parse(p.expiry + 'T15:30:00+05:30') - now) / (365 * 86400000));
      const cp = (type === 'CE' ? 1 : type === 'PE' ? -1 : 0) as 1 | -1 | 0;
      let iv: number | null = null;
      if (cp !== 0 && spot && spot > 0 && p.ltp != null && tY > 0)
        iv = impliedVol(cp as 1 | -1, spot, p.strike ?? 0, tY, RISK_FREE, p.ltp);
      return { key: '', tradingsymbol: '', account_label: '', root, type, strike: p.strike ?? 0,
        qty: p.qty, avg: p.avg, mult: p.multiplier ?? 1, ltp: p.ltp, unrealized: p.unrealized, cp, tYears: tY, iv };
    });
    if (spot && spot > 0) {                            // same fallback IV as the chart
      const fb = this.fallbackIV(legs, spot);
      for (const l of legs) if (l.cp !== 0 && l.iv == null && l.tYears > 0) l.iv = fb;
    }
    const s = price ?? spot;
    if (s == null) return legs.reduce((a, l) => a + (l.unrealized ?? 0), 0);
    return this.priceLegsAt(legs, s, daysFwd);
  }

  /** the dock's slider rows — one per underlying, each carrying its slider value,
   *  fill %, and P&L projected to the GLOBAL Today slider (theta applies to all). */
  dockRows = computed(() => {
    const active = this.activePayoffRoot();
    const pd = this.projDays();
    return this.bookRoots().map(r => {
      const price = r.active ? this.scrubPrice() : (r.spot ?? (r.xMin + r.xMax) / 2);
      const range = r.xMax - r.xMin || 1;
      const frac = r.active ? this.pfScrubFrac()
        : (r.spot != null ? Math.max(0, Math.min(1, (r.spot - r.xMin) / range)) : 0.5);
      const day = Math.max(0, Math.min(r.dte ?? 0, pd));
      const pnl = this.rootPnlAt(r.root, r.hasSpot ? price : null, day);
      return { ...r, price, frac, pnl };
    });
  });
  /** scrub a root's slider — switch the chart to it first if it isn't the active one */
  onRootScrub(r: { root: string }, v: any) {
    if (r.root !== this.activePayoffRoot()) this.pickRoot(r.root);
    this.setPfScrub(v);
  }

  /** furthest expiry across the whole book — the Today slider's range (so you can
   *  project every underlying, even ones expiring after the charted one) */
  maxDte = computed(() => Math.max(0, ...this.bookRoots().map(r => r.dte ?? 0)));
  /** whole-book P&L projected to the current Today slider (sum of every underlying) */
  dockTotal = computed(() => this.dockRows().reduce((s, r) => s + r.pnl, 0));

  // ── fullscreen controls: hide instruments, reset to now, refresh live ────────
  pfHideRail = signal(false);
  toggleRail() { this.pfHideRail.update(v => !v); }
  /** back to "now": today, live prices, no scrub — the whole book at this instant */
  resetProjection() { this.projDays.set(0); this.pfScrub.set(null); this.payoffHoverX.set(null); }
  anyProjection = computed(() => this.projDays() > 0 || this.pfScrub() != null);

  // ── time slider: project the book forward N days (theta decay) ───────────────
  projDays = signal(0);
  setProjDays(v: any) {
    const dte = this.maxDte();                        // clamp to the furthest expiry in the book
    this.projDays.set(Math.max(0, Math.min(dte, Math.round(Number(v) || 0))));
  }
  /** ₹ P&L of the charted book at underlying `s`, `daysFwd` days from now (BS theta decay) */
  priceLegsAt(legs: PayoffLeg[], s: number, daysFwd: number): number {
    return legs.reduce((sum, l) => {
      const t = Math.max(0, l.tYears - daysFwd / 365);
      let per: number;
      if (l.type === 'FUT') per = s - l.avg;
      else if (l.iv != null && t > 0) per = bsPrice(l.cp as 1 | -1, s, l.strike, t, RISK_FREE, l.iv) - l.avg;
      else per = Math.max((l.cp as number) * (s - l.strike), 0) - l.avg;
      return sum + l.qty * l.mult * per;
    }, 0);
  }
  /** which account(s) a root's legs sit in — shown under each row in the scope menu */
  accountsForRoot(root: string): string[] {
    const s = new Set<string>();
    for (const p of this.payoffCandidates()) if ((p.root || '?') === root && p.account_label) s.add(p.account_label);
    return [...s];
  }

  // ── "All ▾" scope picker: one control to show all trades, then choose inside ──
  pfMenuOpen = signal(false);
  togglePfMenu() { this.pfMenuOpen.update(v => !v); }
  closePfMenu() { this.pfMenuOpen.set(false); }
  /** every underlying in the open book (unfiltered by isolation) — most legs first */
  allRoots = computed<{ root: string; count: number }[]>(() => {
    const m = new Map<string, number>();
    for (const p of this.payoffCandidates()) m.set(p.root || '?', (m.get(p.root || '?') || 0) + 1);
    return [...m.entries()].map(([root, count]) => ({ root, count })).sort((a, b) => b.count - a.count);
  });
  /** the charted underlying's own legs — the checklist inside the menu */
  activeRootLegs = computed(() =>
    this.payoffCandidates().filter(p => (p.root || '?') === this.activePayoffRoot()));
  /** isolated legs within the charted underlying (0 = the whole underlying) */
  activeSelCount = computed(() => {
    const sel = this.selLegs();
    return this.activeRootLegs().filter(l => sel.has(this.legKey(l))).length;
  });
  /** "All" = largest underlying, nothing isolated */
  isAllScope = computed(() => this.payoffRoot() === null && this.selLegs().size === 0);
  pickAllRoots() { this.clearLegSel(); this.payoffRoot.set(null); this.payoffHoverX.set(null); this.projDays.set(0); this.pfScrub.set(null); this.pfWinStart.set(0); this.pfWinEnd.set(1); this.resetDrafts(); this.pfMenuOpen.set(false); }
  pickRoot(root: string) { this.clearLegSel(); this.setPayoffRoot(root); this.pfMenuOpen.set(false); if (this.pfFull()) { this.chainExpiry.set(''); this.ensureChain(); } }

  payoff = computed<PayoffModel | null>(() => {
    const root = this.activePayoffRoot();
    if (!root) return null;
    const now = Date.now();
    const legs: PayoffLeg[] = this.payoffLegsRaw()
      .filter(p => (p.root || '?') === root)
      .map(p => {
        const type = (p.opt_type || 'FUT') as 'CE' | 'PE' | 'FUT';
        // per-leg time-to-expiry in years (to 15:30 IST on the expiry day)
        let tYears = 0;
        if (p.expiry) tYears = Math.max(0, (Date.parse(p.expiry + 'T15:30:00+05:30') - now) / (365 * 86400000));
        return {
          key: this.legKey(p), tradingsymbol: p.tradingsymbol, account_label: p.account_label,
          root: p.root || root, type, strike: p.strike ?? 0, qty: p.qty, avg: p.avg, mult: p.multiplier ?? 1,
          ltp: p.ltp, unrealized: p.unrealized,
          cp: (type === 'CE' ? 1 : type === 'PE' ? -1 : 0) as 1 | -1 | 0, tYears, iv: null as number | null,
        };
      })
      .filter(l => l.type === 'FUT' || l.strike > 0);
    if (!legs.length) return null;

    // Keep a handle on the REAL book (for the ghost "before" curve), then fold in
    // any hypothetical "what-if" legs for this root. Each was entered at its market
    // premium (avg = premium → opens at ₹0 P&L) so the chart shows the true new shape.
    const realLegs = legs.slice();
    const dctx = this.draftCtx();
    const drafts = dctx ? this.draftLegs().filter(d => d.root === root) : [];
    for (const d of drafts) {
      const cp = (d.type === 'CE' ? 1 : d.type === 'PE' ? -1 : 0) as 1 | -1 | 0;
      legs.push({
        key: 'draft-' + d.id, tradingsymbol: '(what-if)', account_label: 'what-if',
        root, type: d.type, strike: d.strike, qty: d.qty, avg: d.premium, mult: d.mult,
        ltp: d.premium, unrealized: 0, cp,
        tYears: d.expiry ? Math.max(0, (Date.parse(d.expiry + 'T15:30:00+05:30') - now) / (365 * 86400000)) : dctx!.tYears,
        iv: null, draft: true,
      });
    }
    const hasDraft = drafts.length > 0;

    // best-effort spot (backend → futures LTP → strike-mid) so stock/commodity roots
    // — where the index-style spot feed may be absent — still get a "today" curve.
    const spot = this.rootSpot(root);
    const hasSpot = spot != null && spot > 0;
    const strikes = legs.filter(l => l.type !== 'FUT').map(l => l.strike);

    // Back implied vol out of each option's live price (needs a spot + a live LTP),
    // then fill any leg that WON'T back out (deep-ITM / stale price) with a fallback
    // IV — otherwise it decays as flat intrinsic and the theta curve never moves.
    if (hasSpot) {
      for (const l of legs) {
        if (l.cp !== 0 && l.ltp != null && l.tYears > 0)
          l.iv = impliedVol(l.cp as 1 | -1, spot!, l.strike, l.tYears, RISK_FREE, l.ltp);
      }
      const fb = this.fallbackIV(legs, spot!);
      for (const l of legs) if (l.cp !== 0 && l.iv == null && l.tYears > 0) l.iv = fb;
    }
    const hasToday = hasSpot && legs.some(l => l.cp !== 0 && l.tYears > 0);

    // per-unit EXPIRY payoff of one leg at underlying price s → total ₹ P&L
    const pnlAt = (s: number) => legs.reduce((sum, l) => {
      const per = l.type === 'FUT' ? (s - l.avg)
        : l.type === 'CE' ? (Math.max(s - l.strike, 0) - l.avg)
        : (Math.max(l.strike - s, 0) - l.avg);
      return sum + l.qty * l.mult * per;
    }, 0);

    // x-window: span every strike + the spot, padded so the wings are visible and
    // the current price sits comfortably inside (never clipped at an edge).
    const pool = [...strikes, ...(hasSpot ? [spot!] : [])];
    const loK = pool.length ? Math.min(...pool) : legs[0].avg;
    const hiK = pool.length ? Math.max(...pool) : legs[0].avg;
    const mid = hasSpot ? spot! : (loK + hiK) / 2 || legs[0].avg;
    const spread = Math.max(hiK - loK, mid * 0.05);
    const half = spread * 0.7 + mid * 0.03;
    const xMin = Math.max(0, Math.min(loK, mid) - half);
    const xMax = Math.max(hiK, mid) + half;

    const N = 220;
    const pts: { s: number; pnl: number }[] = [];
    for (let i = 0; i <= N; i++) { const s = xMin + (xMax - xMin) * i / N; pts.push({ s, pnl: pnlAt(s) }); }

    // exact extremes live at the kinks (strikes) or the window ends — payoff is
    // piecewise-linear, so sampling those points is precise, not approximate.
    const kinks = [xMin, xMax, ...strikes, ...(hasSpot ? [spot!] : [])].filter(s => s >= xMin && s <= xMax);
    let maxProfit = -Infinity, maxLoss = Infinity, maxProfitAt = mid, maxLossAt = mid;
    for (const s of kinks) {
      const v = pnlAt(s);
      if (v > maxProfit) { maxProfit = v; maxProfitAt = s; }
      if (v < maxLoss) { maxLoss = v; maxLossAt = s; }
    }
    // unbounded? — non-zero slope at either wing means the P&L runs off to ±∞
    const d = (xMax - xMin) * 0.01 || 1;
    const rightSlope = (pnlAt(xMax + d) - pnlAt(xMax)) / d;
    const leftSlope = (pnlAt(xMin) - pnlAt(xMin - d)) / d;
    const eps = 1e-6;
    const profitUnlimited = rightSlope > eps || leftSlope < -eps;
    const lossUnlimited = rightSlope < -eps || leftSlope > eps;

    // breakevens — sign changes along the sampled curve, linearly interpolated
    const breakevens: number[] = [];
    for (let i = 1; i < pts.length; i++) {
      const a = pts[i - 1], b = pts[i];
      if ((a.pnl <= 0 && b.pnl > 0) || (a.pnl >= 0 && b.pnl < 0)) {
        const t = a.pnl / (a.pnl - b.pnl);
        breakevens.push(a.s + (b.s - a.s) * t);
      }
    }

    // ghost "before" curve — the expiry P&L of the REAL book only, sampled on the
    // same x-grid so it overlays 1:1 with the with-draft curve.
    const basePnlAt = (s: number) => realLegs.reduce((sum, l) => {
      const per = l.type === 'FUT' ? (s - l.avg)
        : l.type === 'CE' ? (Math.max(s - l.strike, 0) - l.avg)
        : (Math.max(l.strike - s, 0) - l.avg);
      return sum + l.qty * l.mult * per;
    }, 0);
    const basePts = hasDraft ? pts.map(p => ({ s: p.s, pnl: basePnlAt(p.s) })) : null;

    const currentPnl = legs.reduce((s, l) => s + (l.unrealized ?? 0), 0);
    const netCredit = legs.filter(l => l.type !== 'FUT')
      .reduce((s, l) => s - l.qty * l.avg * l.mult, 0);      // short(qty<0) adds credit
    const expiryAtSpot = hasSpot ? pnlAt(spot!) : null;
    const capturedPct = (!profitUnlimited && maxProfit > 0) ? currentPnl / maxProfit : null;

    // nearest expiry across the charted legs → days-to-expiry countdown
    let expiry: string | null = null;
    for (const p of this.payoffLegsRaw()) {
      if ((p.root || '?') !== root || !p.expiry) continue;
      if (!expiry || p.expiry < expiry) expiry = p.expiry;
    }
    let dte: number | null = null;
    if (expiry) {
      const days = Math.round((Date.parse(expiry + 'T15:30:00+05:30') - Date.now()) / 86400000);
      dte = Math.max(0, days);
    }

    return {
      root, spot, hasSpot, legs, pts, hasToday, xMin, xMax,
      maxProfit: profitUnlimited ? Infinity : maxProfit,
      maxLoss: lossUnlimited ? -Infinity : maxLoss,
      profitUnlimited, lossUnlimited, maxProfitAt, maxLossAt,
      breakevens, currentPnl, expiryAtSpot, netCredit, isCredit: netCredit > 0.5,
      capturedPct, expiry, dte, hasDraft, basePts,
    };
  });

  // SVG geometry for the payoff chart (0–100 space; HTML overlays line up 1:1).
  payoffGeom = computed(() => {
    const m = this.payoff();
    if (!m) return null;
    const xR = m.xMax - m.xMin || 1;
    // Horizontal zoom: map a price to 0–100 within the visible [a,b] window
    // (fractions of the full price range). a=0,b=1 → whole range (no zoom).
    const va = this.pfWinStart(), vb = this.pfWinEnd(), vw = (vb - va) || 1;
    const xP = (s: number) => +((((s - m.xMin) / xR - va) / vw) * 100).toFixed(3);

    // y-window: cover the finite extremes, current P&L and ₹0, with headroom.
    const finiteVals = [0, m.currentPnl,
      ...(isFinite(m.maxProfit) ? [m.maxProfit] : []),
      ...(isFinite(m.maxLoss) ? [m.maxLoss] : []),
      ...m.pts.map(p => p.pnl)];
    let yMin = Math.min(...finiteVals), yMax = Math.max(...finiteVals);
    // An unbounded wing would crush the near-money action into a sliver — clip it
    // to ~3× the reference magnitude (the line then flattens at the edge, and the
    // "Unlimited" stat already tells the real story) so the payoff stays readable.
    const ref = Math.max(Math.abs(m.currentPnl),
      isFinite(m.maxProfit) ? Math.abs(m.maxProfit) : 0,
      isFinite(m.maxLoss) ? Math.abs(m.maxLoss) : 0, 1);
    if (m.lossUnlimited) yMin = Math.max(yMin, -3.2 * ref);
    if (m.profitUnlimited) yMax = Math.min(yMax, 3.2 * ref);
    if (yMax - yMin < 1) { yMax += 1; yMin -= 1; }
    const padY = (yMax - yMin) * 0.12;
    yMax += padY; yMin -= padY;
    const yR = yMax - yMin || 1;
    const yP = (v: number) => +(((yMax - v) / yR) * 100).toFixed(3);
    const zeroPct = Math.max(0, Math.min(100, yP(0)));

    const P = m.pts.map(p => ({ x: xP(p.s), y: yP(Math.max(yMin, Math.min(yMax, p.pnl))) }));
    const line = P.map((q, i) => `${i ? 'L' : 'M'}${q.x} ${q.y}`).join(' ');
    // profit fill = curve down to ₹0 baseline; loss fill = curve up to ₹0.
    const areaToZero = `${line} L${P[P.length - 1].x} ${zeroPct} L${P[0].x} ${zeroPct} Z`;

    // PROJECTED curve at "N days from now" (theta decay) — reprice with backed-out
    // IVs at a reduced time-to-expiry. N is the day slider (0 = today → the smooth
    // curve; N = DTE → the expiry hockey-stick). Same y-scale as the expiry line.
    const pd = Math.max(0, Math.min(m.dte ?? 0, this.projDays()));
    const clampY = (v: number) => Math.max(yMin, Math.min(yMax, v));
    const projLine = m.hasToday
      ? m.pts.map((p, i) => `${i ? 'L' : 'M'}${xP(p.s)} ${yP(clampY(this.priceLegsAt(m.legs, p.s, pd)))}`).join(' ')
      : null;
    const projAtSpot = m.hasSpot ? this.priceLegsAt(m.legs, m.spot!, pd) : null;
    const projSpotY = projAtSpot != null ? yP(clampY(projAtSpot)) : null;

    // ghost "before" expiry line — the real book without the what-if legs
    const baseLine = m.basePts
      ? m.basePts.map((p, i) => `${i ? 'L' : 'M'}${xP(p.s)} ${yP(clampY(p.pnl))}`).join(' ')
      : null;

    const yticks: { top: number; label: string }[] = [];
    const step = this.niceStep(yR);
    for (let v = Math.ceil(yMin / step) * step; v <= yMax + 1e-9; v += step)
      yticks.push({ top: yP(v), label: this.axisInr(v) });

    // x ticks: strikes + spot, deduped & sorted, then greedily thinned so labels
    // never overlap — the spot is kept first (highest priority), then strikes that
    // clear a minimum gap from every already-kept tick.
    const raw: { x: number; label: string; kind: string; prio: number }[] = [];
    if (m.hasSpot) raw.push({ x: xP(m.spot!), label: this.axisPrice(m.spot!), kind: 'spot', prio: 0 });
    const seenK = new Set<number>();
    for (const l of m.legs) if (l.type !== 'FUT' && !seenK.has(l.strike)) {
      seenK.add(l.strike);
      raw.push({ x: xP(l.strike), label: this.axisPrice(l.strike), kind: 'strike', prio: 1 });
    }
    const MIN_GAP = 8;                               // % of width between labels
    const kept: typeof raw = [];
    for (const t of [...raw].sort((a, b) => a.prio - b.prio || a.x - b.x)) {
      if (kept.every(k => Math.abs(k.x - t.x) >= MIN_GAP)) kept.push(t);
    }
    // ticks whose price falls outside the zoom window are dropped (they'd sit
    // off-canvas); keep a small margin so edge labels still show.
    const inView = (x: number) => x >= -1 && x <= 101;
    const xticks = kept.sort((a, b) => a.x - b.x).filter(t => inView(t.x));

    // price-scrub marker — a persistent "what-if underlying" dot the slider drives.
    // Only drawn once the user engages the slider (pfScrub != null); it rides the
    // projected/today curve when we have live IVs, else the expiry curve.
    const si = this.scrubInfo();
    const scrub = (si && this.pfScrub() != null)
      ? (() => { const pnl = si.projPnl != null ? si.projPnl : si.expiryPnl;
                 return { x: xP(si.price), price: si.price, pnl, y: yP(clampY(pnl)) }; })()
      : null;

    return {
      xP, yP, zeroPct, line, areaToZero, projLine, projAtSpot, projSpotY, projDays: pd, baseLine,
      yticks, xticks, yMin, yMax, scrub,
      spotX: m.hasSpot ? xP(m.spot!) : null,
      curPnlY: yP(Math.max(yMin, Math.min(yMax, m.currentPnl))),
      breakevens: m.breakevens.map(b => ({ x: xP(b), label: this.axisPrice(b) })).filter(b => inView(b.x)),
      maxProfitPt: isFinite(m.maxProfit) ? { x: xP(m.maxProfitAt), y: yP(m.maxProfit) } : null,
      maxLossPt: isFinite(m.maxLoss) ? { x: xP(m.maxLossAt), y: yP(m.maxLoss) } : null,
    };
  });

  /** hovered point on the payoff curve → price + expiry P&L there (tooltip).
   *  payoffHoverX is a fraction of the *canvas* width, which maps into the zoom
   *  window before it becomes a price along the full range. */
  payoffHover = computed(() => {
    const m = this.payoff(); const x = this.payoffHoverX();
    if (!m || x == null) return null;
    const frac = this.pfWinStart() + x * (this.pfWinEnd() - this.pfWinStart());   // full-range fraction
    const s = m.xMin + (m.xMax - m.xMin) * frac;
    // interpolate P&L from the sampled curve
    const N = m.pts.length - 1; const f = Math.max(0, Math.min(N, frac * N));
    const i = Math.floor(f), a = m.pts[i], b = m.pts[Math.min(N, i + 1)];
    const pnl = a.pnl + (b.pnl - a.pnl) * (f - i);
    // projected (today / selected-day) P&L at the same price, when we have IVs
    const pd = Math.max(0, Math.min(m.dte ?? 0, this.projDays()));
    const projPnl = m.hasToday ? this.priceLegsAt(m.legs, s, pd) : null;
    return { price: s, pnl, projPnl, xPct: +(x * 100).toFixed(3) };
  });
  onPayoffMove(ev: MouseEvent) {
    const el = this.payoffCanvas?.nativeElement; if (!el) return;
    const r = el.getBoundingClientRect();
    this.payoffHoverX.set(Math.max(0, Math.min(1, (ev.clientX - r.left) / r.width)));
  }
  clearPayoffHover() { this.payoffHoverX.set(null); }

  // ── payoff chart · fullscreen ───────────────────────────────────────────────
  pfFull = signal(false);
  togglePfFull() { this.pfFull.update(v => !v); if (this.pfFull()) { this.payoffHoverX.set(null); this.ensureChain(); } }
  @HostListener('document:keydown.escape') onEsc() {
    if (this.pfFull()) this.pfFull.set(false);
    else if (this.pfZoomed()) this.resetPfView();
  }

  // ── payoff chart · horizontal zoom + pan (a [start,end] window, fractions) ───
  pfWinStart = signal(0);
  pfWinEnd = signal(1);
  pfZoomed = computed(() => this.pfWinStart() > 0.001 || this.pfWinEnd() < 0.999);
  resetPfView() { this.pfWinStart.set(0); this.pfWinEnd.set(1); this.payoffHoverX.set(null); }
  private _applyPfWin(s: number, e: number) {
    const minW = 0.05;                              // max zoom ≈ 5% of the price range
    if (e - s < minW) { const mid = (s + e) / 2; s = mid - minW / 2; e = mid + minW / 2; }
    if (s < 0) { e -= s; s = 0; }
    if (e > 1) { s -= (e - 1); e = 1; }
    this.pfWinStart.set(Math.max(0, +s.toFixed(5)));
    this.pfWinEnd.set(Math.min(1, +e.toFixed(5)));
  }
  private _zoomPf(factor: number) {
    const s = this.pfWinStart(), e = this.pfWinEnd(), mid = (s + e) / 2;
    const nw = Math.min(1, Math.max(0.05, (e - s) * factor));
    this._applyPfWin(mid - nw / 2, mid + nw / 2);
    this.payoffHoverX.set(null);
  }
  pfZoomIn() { this._zoomPf(0.6); }
  pfZoomOut() { this._zoomPf(1 / 0.6); }
  onPayoffWheel(ev: WheelEvent) {
    const el = this.payoffCanvas?.nativeElement; if (!el) return;
    ev.preventDefault();
    const rect = el.getBoundingClientRect();
    const cursor = Math.max(0, Math.min(1, (ev.clientX - rect.left) / rect.width));
    const w0 = this.pfWinStart(), w1 = this.pfWinEnd(), w = w1 - w0;
    const at = w0 + cursor * w;                      // cursor position in full-range fraction
    const factor = Math.min(3, Math.max(0.33, Math.exp(ev.deltaY * 0.0016)));
    const nw = Math.min(1, Math.max(0.05, w * factor));
    this._applyPfWin(at - cursor * nw, at - cursor * nw + nw);
    this.payoffHoverX.set(null);
  }
  private pfDrag: { x: number; w0: number; w1: number } | null = null;
  onPayoffDown(ev: PointerEvent) {
    if (!this.pfZoomed()) return;                    // nothing to pan at full range
    if (ev.button !== 0 && ev.pointerType === 'mouse') return;
    this.pfDrag = { x: ev.clientX, w0: this.pfWinStart(), w1: this.pfWinEnd() };
    (ev.target as HTMLElement).setPointerCapture?.(ev.pointerId);
    this.payoffHoverX.set(null);
  }
  onPayoffPointerMove(ev: PointerEvent) {
    const el = this.payoffCanvas?.nativeElement; if (!el) return;
    if (this.pfDrag) {
      const rect = el.getBoundingClientRect();
      const w = this.pfDrag.w1 - this.pfDrag.w0;
      const dx = ((ev.clientX - this.pfDrag.x) / rect.width) * w;   // 1:1 with the pointer
      this._applyPfWin(this.pfDrag.w0 - dx, this.pfDrag.w1 - dx);
    } else {
      this.onPayoffMove(ev);
    }
  }
  onPayoffUp() { this.pfDrag = null; }

  // ── payoff chart · price scrub slider ("what if the underlying were X?") ─────
  pfScrub = signal<number | null>(null);            // price fraction [0,1] of [xMin,xMax]; null = sits at spot
  /** effective scrub fraction — the explicit slider value, else the live spot */
  pfScrubFrac = computed(() => {
    const s = this.pfScrub();
    if (s != null) return s;
    const m = this.payoff();
    if (m?.hasSpot) return Math.max(0, Math.min(1, (m.spot! - m.xMin) / (m.xMax - m.xMin || 1)));
    return 0.5;
  });
  scrubPrice = computed(() => {
    const m = this.payoff(); if (!m) return 0;
    return m.xMin + (m.xMax - m.xMin) * this.pfScrubFrac();
  });
  /** step so the slider glides through ~240 stops across the price range */
  priceStep = computed(() => {
    const m = this.payoff(); if (!m) return 1;
    const raw = (m.xMax - m.xMin) / 240;
    return raw > 0 ? +raw.toPrecision(2) : 1;
  });
  setPfScrub(v: any) {
    const m = this.payoff(); if (!m) return;
    const xR = m.xMax - m.xMin || 1;
    this.pfScrub.set(Math.max(0, Math.min(1, (Number(v) - m.xMin) / xR)));
  }
  resetPfScrub() { this.pfScrub.set(null); }
  /** price + P&L (expiry and projected) at the scrubbed underlying — slider label */
  scrubInfo = computed(() => {
    const m = this.payoff(); if (!m) return null;
    const frac = this.pfScrubFrac();
    const price = m.xMin + (m.xMax - m.xMin) * frac;
    const N = m.pts.length - 1; const f = Math.max(0, Math.min(N, frac * N));
    const i = Math.floor(f), a = m.pts[i], b = m.pts[Math.min(N, i + 1)];
    const expiryPnl = a.pnl + (b.pnl - a.pnl) * (f - i);
    const pd = Math.max(0, Math.min(m.dte ?? 0, this.projDays()));
    const projPnl = m.hasToday ? this.priceLegsAt(m.legs, price, pd) : null;
    return { price, frac, expiryPnl, projPnl };
  });

  // ── "What-if" position builder (fullscreen) ─────────────────────────────────
  // Context derived from the REAL book of the charted root: live spot, nearest
  // expiry (→ time), an ATM IV surface (median of backed-out leg IVs), the ₹/pt
  // multiplier, and sensible lot/strike steps — so a hypothetical leg is priced &
  // sized correctly, not guessed.
  draftCtx = computed(() => {
    const root = this.activePayoffRoot();
    if (!root) return null;
    const real = this.payoffLegsRaw().filter(p => (p.root || '?') === root &&
      (p.opt_type === 'FUT' || ((p.opt_type === 'CE' || p.opt_type === 'PE') && p.strike != null)));
    if (!real.length) return null;
    const spot = (this.openPos()?.spots || {})[root] ?? null;
    const hasSpot = spot != null && spot > 0;
    let expiry: string | null = null;
    for (const p of real) if (p.expiry && (!expiry || p.expiry < expiry)) expiry = p.expiry;
    const tYears = expiry
      ? Math.max(1 / 365, (Date.parse(expiry + 'T15:30:00+05:30') - Date.now()) / (365 * 86400000))
      : 0.08;
    const mult = real[0].multiplier ?? 1;
    // lot step = gcd of the real legs' |qty| (true lot for mixed sizes; a multiple otherwise)
    const gcd = (a: number, b: number): number => b ? gcd(b, a % b) : a;
    const lotStep = real.reduce((acc, p) => acc ? gcd(acc, Math.abs(p.qty)) : Math.abs(p.qty), 0) || 1;
    // strike step = tightest gap between existing strikes; else a magnitude-based default
    const strikes = [...new Set(real.filter(p => p.strike != null).map(p => p.strike as number))].sort((a, b) => a - b);
    let strikeStep = 0;
    for (let i = 1; i < strikes.length; i++) { const dd = strikes[i] - strikes[i - 1]; if (dd > 0 && (!strikeStep || dd < strikeStep)) strikeStep = dd; }
    if (!strikeStep) { const base = spot ?? strikes[0] ?? 100; strikeStep = base >= 20000 ? 100 : base >= 5000 ? 50 : base >= 1000 ? 20 : Math.max(1, Math.round(base * 0.01)); }
    const atmStrike = hasSpot ? Math.round(spot! / strikeStep) * strikeStep : (strikes[0] ?? 0);
    const ivs: number[] = [];
    if (hasSpot) for (const p of real) {
      if ((p.opt_type === 'CE' || p.opt_type === 'PE') && p.strike != null && p.ltp != null) {
        const iv = impliedVol(p.opt_type === 'CE' ? 1 : -1, spot!, p.strike, tYears, RISK_FREE, p.ltp);
        if (iv) ivs.push(iv);
      }
    }
    ivs.sort((a, b) => a - b);
    const atmIV = ivs.length ? ivs[Math.floor(ivs.length / 2)] : 0.15;
    return { root, spot, hasSpot, expiry, tYears, mult, lotStep, strikeStep, atmStrike, atmIV };
  });

  draftLegs = signal<DraftLeg[]>([]);
  private _draftId = 1;
  // in-progress form
  draftType = signal<'CE' | 'PE' | 'FUT'>('CE');
  draftSide = signal<1 | -1>(-1);                   // Sell by default (premium-selling books)
  draftLots = signal(1);
  draftStrike = signal<number | null>(null);        // null → ATM
  private draftPremiumOverride = signal<number | null>(null);

  effDraftStrike = computed(() => this.draftStrike() ?? this.draftCtx()?.atmStrike ?? 0);
  /** BS-fair premium at the ATM IV — the honest "what you'd pay now" estimate */
  estDraftPremium = computed(() => {
    const c = this.draftCtx(); if (!c || !c.hasSpot) return 0;
    if (this.draftType() === 'FUT') return c.spot!;
    const cp = this.draftType() === 'CE' ? 1 : -1;
    return Math.max(0.05, bsPrice(cp, c.spot!, this.effDraftStrike(), c.tYears, RISK_FREE, c.atmIV));
  });
  draftPremiumShown = computed(() => this.draftPremiumOverride() ?? +this.estDraftPremium().toFixed(2));
  draftQty = computed(() => this.draftSide() * this.draftLots() * (this.draftCtx()?.lotStep ?? 1));

  setDraftType(t: 'CE' | 'PE' | 'FUT') { this.draftType.set(t); this.draftPremiumOverride.set(null); if (t === 'FUT') this.draftStrike.set(null); }
  setDraftSide(s: 1 | -1) { this.draftSide.set(s); }
  bumpLots(delta: number) { this.draftLots.set(Math.max(1, this.draftLots() + delta)); }
  setDraftLots(v: any) { const n = Math.round(Number(v)); this.draftLots.set(isFinite(n) && n > 0 ? n : 1); }
  bumpStrike(delta: number) { const c = this.draftCtx(); if (!c) return; this.draftStrike.set(Math.max(0, this.effDraftStrike() + delta * c.strikeStep)); this.draftPremiumOverride.set(null); }
  setDraftStrike(v: any) { const n = Number(v); this.draftStrike.set(isFinite(n) && n > 0 ? n : null); this.draftPremiumOverride.set(null); }
  setDraftPremium(v: any) { const n = Number(v); this.draftPremiumOverride.set(isFinite(n) && n > 0 ? n : null); }
  resetDraftPremium() { this.draftPremiumOverride.set(null); }
  premiumEdited = computed(() => this.draftPremiumOverride() != null);

  addDraft() {
    const c = this.draftCtx(); if (!c) return;
    const isFut = this.draftType() === 'FUT';
    this.draftLegs.update(a => [...a, {
      id: this._draftId++, root: c.root, type: this.draftType(),
      strike: isFut ? 0 : this.effDraftStrike(), qty: this.draftQty(),
      premium: this.draftPremiumShown(), mult: c.mult,
      expiry: this.pickedFromChain() ? (this.chainData()?.expiry || this.chainExpiry() || null) : null,
    }]);
    this.draftPremiumOverride.set(null);            // clear for the next pick
    this.draftStrike.set(null);
    this.pickedFromChain.set(false);
  }
  removeDraft(id: number) { this.draftLegs.update(a => a.filter(d => d.id !== id)); }
  clearDrafts() { this.draftLegs.set([]); }
  private resetDrafts() { this.draftLegs.set([]); this.draftStrike.set(null); this.draftPremiumOverride.set(null); this.pickedFromChain.set(false); this.chainOpen.set(false); }

  /** short premium/qty formatting for the draft chips */
  fmtQty(q: number): string { return Math.abs(q).toLocaleString('en-IN'); }

  // ── options-chain picker: pick a real option (correct live price) to add ─────
  chainOpen = signal(false);
  chainData = signal<FnoOptionChain | null>(null);
  chainLoading = signal(false);
  chainError = signal<string | null>(null);
  chainExpiries = signal<string[]>([]);
  chainExpiry = signal<string>('');
  pickedFromChain = signal(false);           // a chain option is currently selected for the form
  private _chainGen2 = 0;

  openChain() {
    if (!this.activePayoffRoot()) return;
    this.chainOpen.set(true);
    this.chainExpiry.set('');                          // '' → backend's nearest expiry; shown once loaded
    this.loadChainExpiries();
    this.loadChain();
  }
  closeChain() { this.chainOpen.set(false); }
  /** switch the drawer's underlying — also charts it so a picked leg lands there */
  setChainUnderlying(root: string) {
    if (root !== this.activePayoffRoot()) this.pickRoot(root);
    this.chainExpiry.set('');
    this.loadChainExpiries();
    this.loadChain();
  }
  setChainExpiry(exp: string) { this.chainExpiry.set(exp); this.loadChain(); }
  private loadChainExpiries() {
    const root = this.activePayoffRoot(); if (!root) return;
    this.svc.optionExpiries(root).subscribe({
      next: xs => this.chainExpiries.set(Array.isArray(xs) ? xs : []),
      error: () => this.chainExpiries.set([]),
    });
  }
  loadChain() {
    const root = this.activePayoffRoot(); if (!root) return;
    const spot = this.rootSpot(root) ?? 0;
    const exp = this.chainExpiry() || this.draftCtx()?.expiry || '';
    const gen = ++this._chainGen2;
    this.chainLoading.set(true); this.chainError.set(null);
    this.svc.optionChain(root, exp, spot).subscribe({
      next: c => { if (gen !== this._chainGen2) return; this.chainData.set(c); if (!this.chainExpiry() && c?.expiry) this.chainExpiry.set(c.expiry); this.chainLoading.set(false); },
      error: () => { if (gen !== this._chainGen2) return; this.chainData.set(null); this.chainError.set('Chain unavailable — need a live session for this underlying.'); this.chainLoading.set(false); },
    });
  }
  /** pick a CE/PE from the chain → fill the what-if leg with the REAL price */
  pickChainOption(row: FnoOptionRow, type: 'CE' | 'PE') {
    this.draftType.set(type);
    this.draftStrike.set(row.strike);
    const q = type === 'CE' ? row.ce : row.pe;
    this.draftPremiumOverride.set(+(Number(q?.price) || 0).toFixed(2));
    this.pickedFromChain.set(true);
    this.chainOpen.set(false);
  }
  /** pick a future on the drawer's underlying (entered at spot) */
  pickChainFuture() {
    this.draftType.set('FUT');
    this.draftStrike.set(null);
    this.draftPremiumOverride.set(null);
    this.pickedFromChain.set(true);
    this.chainOpen.set(false);
  }
  clearPicked() { this.pickedFromChain.set(false); this.draftStrike.set(null); this.draftPremiumOverride.set(null); }
  /** the chain's underlyings to switch between = the book's own roots */
  chainUnderlyings = computed(() => this.bookRoots().map(r => ({ root: r.root, active: r.active })));

  // ── chain filters + click-to-add (chart updates live) ───────────────────────
  chainType = signal<'both' | 'C' | 'P'>('both');        // show calls, puts, or both
  chainBand = signal<number>(14);                        // ATM ± N strikes (0 = all)
  setChainType(t: 'both' | 'C' | 'P') { this.chainType.set(t); }
  chainBandLabel = computed(() => this.chainBand() === 0 ? 'All strikes' : `ATM ±${this.chainBand()}`);
  cycleChainBand() { const o = [10, 16, 26, 0]; this.chainBand.set(o[(o.indexOf(this.chainBand()) + 1) % o.length]); }
  /** the chain rows after moneyness banding (calls/puts filter is column-level) */
  chainRows = computed<FnoOptionRow[]>(() => {
    const cd = this.chainData(); if (!cd) return [];
    const band = this.chainBand();
    if (band <= 0) return cd.chain;
    let ai = cd.chain.findIndex(r => r.strike === cd.atm_strike);
    if (ai < 0) ai = Math.floor(cd.chain.length / 2);
    return cd.chain.slice(Math.max(0, ai - band), ai + band + 1);
  });
  /** add a chain option straight to the what-if (uses the current Buy/Sell + Lots) */
  addChainLeg(row: FnoOptionRow, type: 'CE' | 'PE') {
    if (!this.draftCtx()) return;
    this.draftType.set(type);
    this.draftStrike.set(row.strike);
    const q = type === 'CE' ? row.ce : row.pe;
    this.draftPremiumOverride.set(+(Number(q?.price) || 0).toFixed(2));
    this.pickedFromChain.set(true);
    this.addDraft();
  }
  addChainFuture() {
    if (!this.draftCtx()) return;
    this.draftType.set('FUT'); this.draftStrike.set(null); this.draftPremiumOverride.set(null);
    this.pickedFromChain.set(true);
    this.addDraft();
  }
  /** ensure the chain is loaded for the charted root (called when fullscreen opens) */
  ensureChain() { if (!this.activePayoffRoot()) return; this.loadChainExpiries(); this.loadChain(); }

  /** short price for the payoff axis (no ₹ decimals; k/L for large indices) */
  axisPrice(v: number): string {
    const a = Math.abs(v);
    if (a >= 1e5) return (v / 1e5).toFixed(2) + 'L';
    if (a >= 1e4) return (v / 1e3).toFixed(1) + 'k';
    return Math.round(v).toLocaleString('en-IN');
  }
  /** one-line human read of the exit situation, shown in the Exit Radar panel */
  exitAdvice = computed<{ tone: 'good' | 'warn' | 'info'; head: string; body: string } | null>(() => {
    const m = this.payoff();
    if (!m) return null;
    const cap = m.capturedPct;
    const dte = m.dte;
    const near = dte != null && dte <= 2;
    if (cap != null && cap >= 0.75)
      return { tone: 'good', head: 'Time to book it',
        body: `You've banked ${Math.round(cap * 100)}% of this book's max profit${dte != null ? ` with ${dte} day${dte === 1 ? '' : 's'} to expiry` : ''}. The last slice of profit carries the most risk — locking it in now is the high-probability play.` };
    if (cap != null && cap >= 0.5)
      return { tone: 'info', head: 'Past the halfway mark',
        body: `${Math.round(cap * 100)}% of max profit captured${dte != null ? `, ${dte} day${dte === 1 ? '' : 's'} left` : ''}. A common rule is to exit around 70–80% — you're close. Trail a stop or set an exit alert.` };
    if (near && m.isCredit)
      return { tone: 'warn', head: 'Expiry risk rising',
        body: `Only ${dte} day${dte === 1 ? '' : 's'} to expiry on a net-credit book. Gamma spikes into expiry — a move through your breakeven can swing P&L fast. Decide your exit now.` };
    if (m.currentPnl < 0 && m.isCredit)
      return { tone: 'warn', head: 'Underwater — mind the wings',
        body: `The book is ${this.inrS(m.currentPnl)} down. On a credit strategy the risk sits in the tails; watch the ${m.breakevens.length ? 'breakeven' : 'max-loss'} levels and cut if price threatens them.` };
    return { tone: 'info', head: 'Position is working',
      body: dte != null
        ? `${dte} day${dte === 1 ? '' : 's'} to expiry. ${m.isCredit ? 'Let theta do the work' : 'Give the move room'} and exit near your profit target.`
        : 'Add a target and watch the breakevens for your exit.' };
  });

  constructor() {
    // live tick → per-second point on the intraday chart (display only; the
    // backend stores 1-minute snapshots, and only while trades are open)
    effect(() => {
      const l = this.live();
      if (!l || !l.market_open || !l.accounts.length) return;
      if (this.chartMode() !== 'intraday') return;
      const day = this.selectedDay();
      if (day && day !== this.todayKey) return;
      if (!this.tradesActive()) return;   // no OPEN trade in the selected accounts → don't invent points
      if (!this.todayLive()) return;      // the open position's market is shut → nothing live to draw
      // The open-book chart tracks the OPEN unrealized (openUnrealized(), refreshed
      // by the 20s open-positions poll); any other intraday view tracks live day P&L.
      // Reading openUnrealized() here also makes the effect refire when it changes.
      const openMode = this.chartOpenBook();
      const live = openMode
        ? this.openUnrealized()
        : this.liveAccts().reduce((s, a) => s + a.day_pnl, 0);
      const sec = Math.floor(Date.now() / 1000);
      if (sec === this.lastLiveSec) return;
      this.lastLiveSec = sec;
      const val = Math.round(live * 100) / 100;
      // Only advance the line when the P&L actually MOVES. A static value (market
      // shut, between MCX sessions, or a stale price feed) shouldn't keep drawing
      // new points — that just stretches a flat line rightward and looks "live"
      // when nothing is happening. Freeze at the last real move instead.
      if (this.lastTailVal !== null && val === this.lastTailVal) return;
      this.lastTailVal = val;
      const nowD = new Date();
      const label = nowD.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false, timeZone: 'Asia/Kolkata' });
      const full = this.fmtFull(nowD, 'sec', 'Asia/Kolkata');                                   // Wed, 08 Jul 2026 · 14:30:05
      this.liveTail.update(tail => {
        const next = [...tail, { x: sec, v: val, label, full }];
        // keep the tail light: full seconds for the last 10 min, minute-level before
        if (next.length > 1200) {
          const cut = sec - 600;
          const old = next.filter(p => p.x < cut);
          const recent = next.filter(p => p.x >= cut);
          const byMin: Record<number, ChartPt> = {};
          for (const p of old) byMin[Math.floor(p.x / 60)] = p;
          return [...Object.values(byMin).sort((a, b) => a.x - b.x), ...recent];
        }
        return next;
      });
    });
  }

  private _openChartTimer: any = null;
  private _clockTimer: any = null;
  ngOnInit() {
    this.svc.connectLive();
    this.reloadAll();
    // heartbeat so the market-hours UI (Today live/locked) re-evaluates on its own
    this._clockTimer = setInterval(() => this.nowTick.update(v => v + 1), 20000);
    // keep today live: every 20s re-mark the open book (for the KPI/footer) and
    // pull any newly-committed minute points into the day-P&L chart. The per-second
    // tail already advances the line between polls; this folds in the stored marks.
    this._openChartTimer = setInterval(() => {
      if (this.selectedDay() && this.selectedDay() !== this.todayKey) return;
      if (this.range() !== 'today') return;
      // Market shut → nothing new to mark; don't re-poll or reload the chart
      // (that only re-renders the same flat line and looks like live activity).
      if (!this.live()?.market_open) return;
      // Refresh the open book on ONE cadence: the per-account unreal (drives the
      // cards AND the chart's open number, one source → always in sync) plus the
      // scoped openPos (for the positions table). The live-tail effect appends the
      // updated openUnrealized() as the next point — no full chart reload, so the
      // user's zoom/pan is preserved.
      this.loadAccountUnreal();
      this.svc.openPositions(this.selectedIds()).subscribe({
        next: o => this.openPos.set(o),
        error: () => {},
      });
    }, 20000);
    // Kite login redirect lands here with ?kite_token= (see app.ts)
    this.route.queryParams.subscribe(params => {
      const token = params['kite_token'];
      if (!token) return;
      const accId = localStorage.getItem('fno_connect_acc');
      this.router.navigate([], { queryParams: {}, replaceUrl: true });
      if (!accId) return;
      localStorage.removeItem('fno_connect_acc');
      this.notice.set('Completing Kite login…');
      this.svc.connect(accId, { request_token: token }).subscribe({
        next: r => {
          this.connectAcc.set(null);
          this.notice.set(this._connectedMsg(r));
          setTimeout(() => this.notice.set(null), 8000);
          this.reloadAll();
        },
        error: (e: HttpErrorResponse) => this._err(e, 'Kite login failed'),
      });
    });
  }

  ngOnDestroy() {
    this.svc.disconnectLive();
    if (this._openChartTimer) clearInterval(this._openChartTimer);
    if (this._clockTimer) clearInterval(this._clockTimer);
    if (this._connPoll) clearInterval(this._connPoll);
    this._ro?.disconnect();
  }

  // After a login is started, poll the server so the account connects itself here
  // once you finish signing in ANYWHERE — the login tab, or your own Incognito
  // window (which can't message this tab back). Runs ~2 min, then gives up.
  private _connPoll: any = null;
  stopConnPoll() { if (this._connPoll) { clearInterval(this._connPoll); this._connPoll = null; } }
  private pollForConnection(accId: string) {
    this.stopConnPoll();
    let tries = 0;
    this._connPoll = setInterval(() => {
      if (++tries > 100) { this.stopConnPoll(); return; }   // ~5 min, plenty for a manual login
      this.svc.accounts().subscribe({
        next: accs => {
          const a = (accs || []).find(x => x.id === accId);
          if (a && a.connected) {
            this.stopConnPoll();
            this.busyAcc.set(null); this.connectAcc.set(null);
            localStorage.removeItem('fno_connect_acc');
            this._patchConnected(new Set([accId]), true);
            this.notice.set(`Connected${a.person ? ' — ' + a.person : ''} · token saved for the day`);
            setTimeout(() => this.notice.set(null), 6000);
            this.reloadAll();
          }
        },
        error: () => {},
      });
    }, 3000);
  }

  // ── loading ───────────────────────────────────────────────────────────────
  reloadAll() {
    // Dispatch the chart + open positions FIRST so the visible chart repaints
    // fast on an account switch; the hero, then the heavier/below-the-fold loads
    // (calendar, trades, per-account stats, log) follow.
    this.loadOpenPositions();
    this.loadSeries();
    this.svc.summary(this.selectedIds()).subscribe({
      next: s => { this.summary.set(s); this.needsMigration.set(false); },
      error: (e: HttpErrorResponse) => {
        if (e.status === 503) this.needsMigration.set(true);
        else this._err(e, 'Could not load F&O summary');
      },
    });
    this.svc.strategies(this.selectedIds()).subscribe({ next: s => this.strategies.set(s), error: () => {} });
    this.loadCalendar();
    this.loadDailySeries();
    this.loadAccountStats();
    this.loadCatalog();
    this.loadAccountUnreal();
    this.loadTrades();
    if (this.showLog()) this.loadLog();
  }

  loadCalendar() {
    this.svc.calendar(this.viewYear(), this.viewMonth() + 1, this.selectedIds()).subscribe({
      next: c => this.calData.set(c), error: () => {},
    });
  }

  loadTrades() {
    this.tradesLoading.set(true);
    this.svc.trades(undefined, undefined, this.selectedIds()).subscribe({
      next: t => { this.allTrades.set(t); this.tradesLoading.set(false); },
      error: () => this.tradesLoading.set(false),
    });
  }

  loadLog() {
    this.svc.loginLog(100).subscribe({ next: l => this.loginLog.set(l), error: () => {} });
  }
  toggleLog() { this.showLog.update(v => !v); if (this.showLog()) this.loadLog(); }

  private _err(e: HttpErrorResponse, prefix: string) {
    this.error.set(`${prefix} — ${e.error?.detail || 'HTTP ' + e.status}`);
    setTimeout(() => this.error.set(null), 8000);
  }

  // ── chart loading ─────────────────────────────────────────────────────────
  setRange(r: Range) {
    this.range.set(r);
    this.selectedDay.set(null);
    this.resetView();
    this.loadSeries();
  }

  private parseMinute(t: string): number {
    return Math.floor(Date.parse(t + (t.length <= 16 ? ':00+05:30' : '')) / 1000);
  }

  loadSeries() {
    const day = this.selectedDay();
    const r = this.range();
    this.hoverI.set(null);
    // The live tail is scoped to the CURRENT view (accounts + range + day). Any
    // call to loadSeries means that scope just changed, so drop the old tail —
    // otherwise the previous account's per-second wiggle lingers and keeps
    // growing, which looks like phantom "live" data on an account that has none.
    this.liveTail.set([]);
    this.lastLiveSec = 0;
    this.lastTailVal = null;
    const gen = ++this._chartGen;

    this.openSeriesPts.set([]);
    if (day || r === 'today') {
      const date = day && day !== this.todayKey ? day : undefined;
      // The intraday LINE = today's running P&L (day m2m). Alongside it we pull the
      // open-series so HOVERING reveals the OPEN unrealized value at that time.
      if (!date) this.loadOpenSeriesForHover();
      this.svc.series('today', date, this.selectedIds()).subscribe({
        next: s => {
          if (gen !== this._chartGen) return;   // a newer request already won
          this.chartOpenBook.set(false);
          this.chartMode.set('intraday');
          if (date) this.liveTail.set([]);      // a past day never gets a live tail
          const pts: ChartPt[] = (s.points || []).map(p => {
            const x = this.parseMinute(p.t); const d = new Date(x * 1000);
            return { x, v: p.pnl,
              label: d.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', hour12: false, timeZone: 'Asia/Kolkata' }),
              full: this.fmtFull(d, 'min', 'Asia/Kolkata'),                                     // Wed, 08 Jul 2026 · 14:30
            };
          });
          this.seriesBase.set(pts);
          // seed the flat-tick filter with the last stored value so the live tail
          // only draws once the P&L moves PAST it (no jump-to-now on a static feed)
          this.lastTailVal = pts.length ? pts[pts.length - 1].v : null;
          // Market shut with no data for today (no stored fills yet AND not live)
          // → open on the All view instead of an empty "today". When today HAS
          // data or is live, we stay on Today + last hour. One-shot: the user can
          // freely switch back to Today afterwards.
          if (!date && this.range() === 'today' && !pts.length && !this.liveOn() && !this._autoRanged) {
            this._autoRanged = true;
            this.range.set('all');
            this.loadSeries();
            return;
          }
          // intraday default = today · last hour (a zoomed window you can pan)
          if (!date && this.intradayZoom() === '1h') this.applyLastHour(); else this.resetView();
          // the day's P&L = the line's own end value (day_pnl: realised today +
          // today's mark-to-market move), so the footer NEVER disagrees with the
          // curve. "Open · unrealized" is the pure mark-to-market of the still-open
          // net qty (unbooked if you closed now) — distinct from today's P&L, which
          // also includes intraday round-trips already booked. In live mode the
          // Open Positions card headlines that full live P&L (= Kite's Positions
          // total = this curve), so the two views reconcile with the terminal.
          const dayVal = pts.length ? pts[pts.length - 1].v : (date ? 0 : (this.summary()?.today_pnl ?? 0));
          // a completed day has no minute points → the day-summary panel shows
          // the closing P&L, so leave the footer stats empty (no redundancy).
          this.chartStats.set(
            date
              ? (pts.length ? [
                  { label: `${date} P&L`, value: this.inrS(dayVal), cls: dayVal >= 0 ? 'pos' : 'neg' },
                  { label: 'Stored', value: `${pts.length} × 1min` },
                ] : [])
              : [
                  { label: 'Today’s P&L', value: this.inrS(dayVal), cls: dayVal >= 0 ? 'pos' : 'neg' },
                  { label: 'Stored', value: `${pts.length} × 1min` },
                ]);
        },
        error: () => {},
      });
      return;
    }

    this.svc.series(r, undefined, this.selectedIds()).subscribe({
      next: s => {
        if (gen !== this._chartGen) return;   // a newer request already won
        this.chartOpenBook.set(false);
        this.chartMode.set('daily');
        this.liveTail.set([]);
        const pts: ChartPt[] = (s.points || []).map((p, i) => {
          const dt = new Date(p.t + 'T00:00:00');
          return {
            x: i, v: p.pnl, day: p.day, date: p.t, by_account: p.by_account, by_strategy: p.by_strategy,
            label: dt.toLocaleDateString('en-IN', { day: '2-digit', month: 'short' }),          // 08 Jul
            full: this.fmtFull(dt),                                                             // Wed, 08 Jul 2026
            year: dt.getFullYear(),
          };
        });
        this.seriesBase.set(pts);
        const days = pts.map(p => p.day || 0);
        this.chartStats.set(pts.length ? [
          { label: 'Period P&L', value: this.inrS(pts[pts.length - 1].v), cls: pts[pts.length - 1].v >= 0 ? 'pos' : 'neg' },
          { label: 'Best day', value: this.inrS(Math.max(...days)), cls: 'pos' },
          { label: 'Worst day', value: this.inrS(Math.min(...days)), cls: 'neg' },
          { label: 'Days', value: String(pts.length) },
        ] : [{ label: 'Period P&L', value: 'No history in this range yet' }]);
      },
      error: () => {},
    });
  }

  /** Today's intraday graph of the OPEN-positions UNREALIZED P&L (the live worth
   *  of the open book vs average cost — the ₹52k number), marked to market by the
   *  engine every minute. The per-second tail (below, in the effect) appends the
   *  same openUnrealized() value, so the line stays ONE coherent metric — no more
   *  splicing day-P&L onto it (which caused the old vertical cliff). */
  loadOpenChart() {
    const gen = ++this._chartGen;
    this.chartOpenBook.set(true);
    this.chartMode.set('intraday');
    this.liveTail.set([]);
    this.lastLiveSec = 0;
    this.lastTailVal = null;
    this.svc.openSeries(this.selectedIds()).subscribe({
      next: s => {
        if (gen !== this._chartGen) return;   // a newer request already won
        const mk = (x: number, v: number, sec = false): ChartPt => {
          const d = new Date(x * 1000);
          return { x, v,
            label: d.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', hour12: false, timeZone: 'Asia/Kolkata' }),
            full: this.fmtFull(d, sec ? 'sec' : 'min', 'Asia/Kolkata') };
        };
        const pts: ChartPt[] = (s.points || []).map(p => mk(this.parseMinute(p.t), p.pnl));
        // pin the current live mark as the latest point so it's always fresh —
        // but ONLY when we actually have a live value (cur ≠ 0). When the feed is
        // down / the market is shut, openUnrealized() reads 0; appending that would
        // make the line cliff-drop to ₹0. Keep the last real mark instead.
        const nowX = Math.floor(Date.now() / 1000);
        const cur = this.openUnrealized();
        if (cur !== 0 && (!pts.length || nowX - pts[pts.length - 1].x >= 30)) pts.push(mk(nowX, cur, true));
        this.seriesBase.set(pts);
        this.lastTailVal = pts.length ? pts[pts.length - 1].v : null;
        if (this.intradayZoom() === '1h') this.applyLastHour(); else this.resetView();
        this.chartStats.set([]);   // the open value lives in the Open Positions card;
                                   // the hover side-panel shows time + change since open
      },
      error: () => {},
    });
  }

  // ── accounts actions ──────────────────────────────────────────────────────
  setDraft(k: 'account_label' | 'person' | 'api_key' | 'api_secret', v: string) {
    this.draft.update(d => ({ ...d, [k]: v }));
  }
  addAccount() {
    const d = this.draft();
    if (!d.account_label.trim()) return;
    this.saving.set(true);
    this.svc.addAccount({
      account_label: d.account_label.trim(), person: d.person.trim() || undefined,
      api_key: d.api_key.trim() || undefined, api_secret: d.api_secret.trim() || undefined,
    }).subscribe({
      next: acc => {
        this.saving.set(false);
        this.showAdd.set(false);
        this.draft.set({ account_label: '', person: '', api_key: '', api_secret: '' });
        this.reloadAll();
        if (acc.login_url) {
          localStorage.setItem('fno_connect_acc', acc.id);
          window.open(acc.login_url, '_blank');
          this.connectAcc.set(acc.id);
        }
      },
      error: (e: HttpErrorResponse) => { this.saving.set(false); this._err(e, 'Could not add the account'); },
    });
  }
  /** One-click login: opens Kite in a popup; the backend callback exchanges the
   *  token and this window connects automatically — no manual paste. On any
   *  failure (popup blocked / cancelled / different Zerodha user) we reveal the
   *  manual paste-a-token modal. Same flow as the Stocks page. */
  /** Simple: open the connect dialog which shows the login link. You open the
   *  link (here or any browser), log in on Kite, and this page connects itself. */
  login(acc: FnoAccount) { this.openConnect(acc); }
  /** Log off — flush this account's Kite token (and any linked Stocks/F&O account
   *  sharing the same api_key + Kite user) so you can log in again and re-test. */
  logOff(acc: FnoAccount) {
    if (!confirm(`Log off “${acc.account_label}”?\n\nThis clears its Kite session — and any Stocks or F&O account that shares the same API key — so you can log in again.`)) return;
    this.busyAcc.set(acc.id);
    this.svc.disconnect(acc.id).subscribe({
      next: (r) => {
        this.busyAcc.set(null);
        this.editingAcct.set(null);
        // Optimistic: flip the affected rows to "not connected" NOW, so the UI
        // updates instantly instead of waiting for the summary refetch.
        const cleared = new Set((r?.cleared || []).map(c => c.id).concat(acc.id));
        this._patchConnected(cleared, false);
        const n = r?.cleared?.length || 1;
        this.notice.set(`Logged off · ${n} account${n === 1 ? '' : 's'} cleared (Stocks + F&O). Tap Log in to reconnect.`);
        setTimeout(() => this.notice.set(null), 6000);
        this.reloadAll();
      },
      error: (e: HttpErrorResponse) => { this.busyAcc.set(null); this._err(e, 'Could not log off'); },
    });
  }
  /** Locally flip the connected flag on the given F&O account ids so the picker
   *  reacts immediately; the next summary refetch reconciles with the server. */
  private _patchConnected(ids: Set<string>, connected: boolean) {
    this.summary.update(s => s ? {
      ...s,
      accounts: (s.accounts || []).map(a => ids.has(a.id)
        ? { ...a, connected, status: connected ? 'connected' : 'expired' } : a),
    } : s);
  }
  /** Fallback for adding a DIFFERENT Zerodha account (Kite reuses the browser
   *  session): opens a modal to paste a token from an incognito Kite login. */
  // this account's own Kite app key, entered right in the connect modal
  connectKey = signal('');
  connectSecret = signal('');
  savingKey = signal(false);
  openConnect(acc: FnoAccount) {
    this.pickerOpen.set(false);        // close the account list → only the token modal shows
    this.connectAcc.set(acc.id);
    this.tokenInput.set(''); this.accessTokenInput.set(''); this.showAdvanced.set(false);
    this.linkCopied.set(false); this.connectLoginUrl.set('');
    this.connectKey.set(''); this.connectSecret.set('');
    this.svc.loginUrl(acc.id).subscribe({ next: r => this.connectLoginUrl.set(r.login_url), error: () => {} });
    this.pollForConnection(acc.id);    // once you log in via the link, this page connects itself
  }
  /** save this account's OWN Kite app key from the modal, then refresh the login
   *  link so "Copy link" uses that account's own app (not the shared one). */
  saveConnectKey(acc: FnoAccount) {
    const k = this.connectKey().trim(), s = this.connectSecret().trim();
    if (!k || !s) return;
    this.savingKey.set(true);
    this.svc.editAccount(acc.id, { api_key: k, api_secret: s }).subscribe({
      next: () => {
        this.savingKey.set(false);
        this.connectKey.set(''); this.connectSecret.set('');
        this.notice.set('API key saved — the login link below now uses this account’s own app.');
        setTimeout(() => this.notice.set(null), 5000);
        this.reloadAll();                                    // refresh the account's key status
        this.svc.loginUrl(acc.id).subscribe({ next: r => this.connectLoginUrl.set(r.login_url), error: () => {} });
      },
      error: (e: HttpErrorResponse) => { this.savingKey.set(false); this._err(e, 'Could not save the API key'); },
    });
  }
  /** Open this account's Kite login page in a new tab (works for the incognito
   *  flow too — the user just switches to the right Zerodha user there). */
  openConnectLoginPage() {
    const url = this.connectLoginUrl();
    if (url) window.open(url, '_blank');
  }
  copyLoginLink() {
    const url = this.connectLoginUrl();
    if (!url) return;
    const done = () => { this.linkCopied.set(true); setTimeout(() => this.linkCopied.set(false), 2500); };
    // clipboard API needs a secure context + permission; fall back to execCommand
    if (navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(url).then(done).catch(() => this._copyFallback(url, done));
    } else {
      this._copyFallback(url, done);
    }
  }
  private _copyFallback(text: string, done: () => void) {
    try {
      const ta = document.createElement('textarea');
      ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
      document.body.appendChild(ta); ta.focus(); ta.select();
      const ok = document.execCommand('copy');
      document.body.removeChild(ta);
      if (ok) done();
    } catch { /* the visible, selectable link is the last-resort fallback */ }
  }
  /** friendly "connected as X (routed to Y)" message from the connect response */
  private _connectedMsg(r: any): string {
    const ca = r?.connected_as;
    const who = ca ? (ca.user_name || ca.user_id || 'Zerodha') : 'Zerodha';
    const routed = r?.routed ? ` → this login was ${who}, so it connected “${ca?.account_label}”` : '';
    const synced = r?.trades_synced ? ` · ${r.trades_synced} trades synced` : '';
    return `Connected as ${who}${routed}${synced}`;
  }
  cancelConnect() { this.connectAcc.set(null); this.stopConnPoll(); }
  submitToken(acc: FnoAccount) {
    const rt = this.tokenInput().trim();
    const at = this.accessTokenInput().trim();
    if (!rt && !at) return;
    this.connecting.set(true);
    this.svc.connect(acc.id, at ? { access_token: at } : { request_token: rt }).subscribe({
      next: r => {
        this.connecting.set(false);
        this.connectAcc.set(null);
        localStorage.removeItem('fno_connect_acc');
        this.notice.set(this._connectedMsg(r) + ' · token saved for the day');
        setTimeout(() => this.notice.set(null), 8000);
        this.reloadAll();
      },
      error: (e: HttpErrorResponse) => { this.connecting.set(false); this._err(e, 'Kite connect failed'); },
    });
  }
  syncTrades(acc: FnoAccount) {
    this.busyAcc.set(acc.id);
    this.svc.syncTrades(acc.id).subscribe({
      next: r => {
        this.busyAcc.set(null);
        this.notice.set(r.added ? `${r.added} new trades synced` : 'Trades up to date');
        setTimeout(() => this.notice.set(null), 5000);
        this.reloadAll();
      },
      error: (e: HttpErrorResponse) => { this.busyAcc.set(null); this._err(e, 'Trade sync failed'); },
    });
  }
  removeAccount(acc: FnoAccount) {
    if (!confirm(`Remove "${acc.account_label}" and all its F&O history?`)) return;
    this.svc.removeAccount(acc.id).subscribe({
      next: () => this.reloadAll(),
      error: (e: HttpErrorResponse) => this._err(e, 'Could not remove the account'),
    });
  }
  importFile(acc: FnoAccount | null, ev: Event) {
    const input = ev.target as HTMLInputElement;
    const file = input.files?.[0];
    input.value = '';
    if (!file || !acc) return;
    this.busyAcc.set(acc.id);
    this.notice.set('Importing tradebook…');
    this.svc.importTradebook(acc.id, file).subscribe({
      next: r => {
        this.busyAcc.set(null);
        if (r.tradebooks) this.tbData.set(r);
        this.tbExpanded.set(acc.id);
        const range = r.date_from ? ` · ${r.date_from} → ${r.date_to}` : '';
        this.notice.set(`Tradebook added — ${r.rows} fills${range} (${r.added} new)` +
          (r.open_positions?.length ? ` · still open: ${r.open_positions.join(', ')}` : ''));
        setTimeout(() => this.notice.set(null), 9000);
        this.reloadAll();
      },
      error: (e: HttpErrorResponse) => { this.busyAcc.set(null); this._err(e, 'Tradebook import failed'); },
    });
  }
  /** Import a Zerodha P&L statement (authoritative booked P&L). Multiple are
   *  supported — each covers a period; an overlapping one replaces the old. */
  importStatement(acc: FnoAccount | null, ev: Event) {
    const input = ev.target as HTMLInputElement;
    const file = input.files?.[0];
    input.value = '';
    if (!file || !acc) return;
    this.busyAcc.set(acc.id);
    this.notice.set('Importing P&L statement…');
    this.svc.importPnlStatement(acc.id, file).subscribe({
      next: r => {
        this.busyAcc.set(null);
        this.tbData.set(r);                       // full payload incl. statements + coverage
        this.tbExpanded.set(acc.id);
        const rep = r.replaced ? ` · replaced ${r.replaced} overlapping` : '';
        this.notice.set(`P&L statement added — booked ${this.inrS(r.statement?.realized ?? 0)} for ${r.statement?.date_from} → ${r.statement?.date_to}${rep}`);
        setTimeout(() => this.notice.set(null), 9000);
        this.reloadAll();
      },
      error: (e: HttpErrorResponse) => { this.busyAcc.set(null); this._err(e, 'P&L statement import failed'); },
    });
  }
  /** Remove ONE statement — booked re-derives from the remaining ones + fills. */
  deleteOneStatement(acc: FnoAccount, st: FnoPnlStatement) {
    if (!confirm(`Remove P&L statement "${st.name}" (${st.date_from} → ${st.date_to})? Booked for that period reverts to the fill history.`)) return;
    this.busyAcc.set(acc.id);
    this.svc.deleteOnePnlStatement(acc.id, st.id).subscribe({
      next: r => {
        this.busyAcc.set(null);
        this.tbData.set(r);
        this.notice.set(`Removed "${st.name}".`);
        setTimeout(() => this.notice.set(null), 6000);
        this.reloadAll();
      },
      error: (e: HttpErrorResponse) => { this.busyAcc.set(null); this._err(e, 'Could not remove the statement'); },
    });
  }
  /** Clear ALL statements → revert booked entirely to the fill history. */
  deleteStatement(acc: FnoAccount) {
    if (!confirm(`Clear ALL P&L statements for "${acc.account_label}"? Booked reverts to the fill history.`)) return;
    this.busyAcc.set(acc.id);
    this.svc.deletePnlStatement(acc.id).subscribe({
      next: r => {
        this.busyAcc.set(null);
        this.tbData.set(r);
        this.notice.set('All P&L statements removed — booked reverted to the fill history.');
        setTimeout(() => this.notice.set(null), 6000);
        this.reloadAll();
      },
      error: (e: HttpErrorResponse) => { this.busyAcc.set(null); this._err(e, 'Could not remove the statements'); },
    });
  }
  statusCls(a: FnoAccount): string {
    return a.connected ? 'ok' : a.status === 'expired' ? 'bad' : 'idle';
  }
  statusText(a: FnoAccount): string {
    return a.connected ? 'Connected' : a.status === 'expired' ? 'Session expired' : 'Not logged in';
  }

  // ── calendar build + day selection ───────────────────────────────────────
  private p2(n: number) { return n < 10 ? '0' + n : '' + n; }
  todayKey = `${this.now.getUTCFullYear()}-${this.p2(this.now.getUTCMonth() + 1)}-${this.p2(this.now.getUTCDate())}`;

  private byDay = computed(() => {
    const m: Record<string, FnoCalendarDay> = {};
    for (const d of this.calData()?.days || []) m[d.date] = d;
    return m;
  });
  monthTotal = computed(() => this.calData()?.month_total ?? 0);

  private level(total: number, maxAbs: number): number {
    if (!total || maxAbs <= 0) return 0;
    const r = Math.abs(total) / maxAbs;
    return r <= 0.25 ? 1 : r <= 0.5 ? 2 : r <= 0.75 ? 3 : 4;
  }

  cells = computed(() => {
    const y = this.viewYear(), mo = this.viewMonth();
    const first = new Date(y, mo, 1).getDay();
    const daysIn = new Date(y, mo + 1, 0).getDate();
    const bd = this.byDay();
    const maxAbs = Math.max(0, ...Object.values(bd).map(d => Math.abs(d.total)));
    const sel = this.selectedDay();
    type Cell = { d: number; key: string; total: number; count: number; isToday: boolean; selected: boolean; tone: string; data: FnoCalendarDay | null };
    const out: (Cell | null)[] = [];
    for (let i = 0; i < first; i++) out.push(null);
    for (let d = 1; d <= daysIn; d++) {
      const k = `${y}-${this.p2(mo + 1)}-${this.p2(d)}`;
      const e = bd[k] || null;
      const lvl = e ? this.level(e.total, maxAbs) : 0;
      const tone = !e || !lvl ? 'z' : (e.total > 0 ? 'p' : 'n') + lvl;
      out.push({ d, key: k, total: e?.total || 0, count: e?.trades_count || 0,
                 isToday: k === this.todayKey, selected: k === sel, tone, data: e });
    }
    while (out.length % 7 !== 0) out.push(null);
    return out;
  });

  prevMonth() { let m = this.viewMonth() - 1, y = this.viewYear(); if (m < 0) { m = 11; y--; } this.viewMonth.set(m); this.viewYear.set(y); this.loadCalendar(); }
  nextMonth() { let m = this.viewMonth() + 1, y = this.viewYear(); if (m > 11) { m = 0; y++; } this.viewMonth.set(m); this.viewYear.set(y); this.loadCalendar(); }
  thisMonth() { this.viewYear.set(this.now.getUTCFullYear()); this.viewMonth.set(this.now.getUTCMonth()); this.loadCalendar(); }

  /** Click a calendar day → trade history + chart both focus that day. */
  selectDay(c: { key: string; data: FnoCalendarDay | null }) {
    if (this.selectedDay() === c.key) { this.clearDay(); return; }
    if (!c.data && !this.allTrades().some(t => (t.trade_date || '').slice(0, 10) === c.key)) return;
    this.selectedDay.set(c.key);
    this.resetView();
    this.loadSeries();
  }
  clearDay() {
    this.selectedDay.set(null);
    this.resetView();
    this.loadSeries();
  }
  /** The ONE central date command (KPI bar date picker). Picking any date makes
   *  the whole tab — chart, trade history, strategies — focus that day; picking
   *  today (or clearing) returns to the live view. Keeps the calendar in sync. */
  setDay(dateStr: string | null) {
    const d = (dateStr || '').slice(0, 10);
    if (!d || d >= this.todayKey) { this.clearDay(); return; }
    this.selectedDay.set(d);
    const [y, m] = d.split('-').map(Number);
    if (y && m && (y !== this.viewYear() || (m - 1) !== this.viewMonth())) {
      this.viewYear.set(y); this.viewMonth.set(m - 1); this.loadCalendar();
    }
    this.resetView();
    this.loadSeries();
  }
  dayStrats(d: FnoCalendarDay): { key: string; total: number }[] {
    return Object.entries(d.by_strategy).map(([key, total]) => ({ key, total }))
      .sort((a, b) => Math.abs(b.total) - Math.abs(a.total));
  }

  // ── bulk strategy assignment (Trade History + Open Positions) ──────────────
  selTrades = signal(new Set<string>());
  selLegs = signal(new Set<string>());
  bulkBusy = signal(false);
  legKey(p: FnoOpenLeg): string { return p.account_id + '|' + p.tradingsymbol; }
  isTradeSel(id: string): boolean { return this.selTrades().has(id); }
  isLegSel(p: FnoOpenLeg): boolean { return this.selLegs().has(this.legKey(p)); }
  toggleTrade(id: string) { const s = new Set(this.selTrades()); s.has(id) ? s.delete(id) : s.add(id); this.selTrades.set(s); }
  toggleLeg(p: FnoOpenLeg) { const k = this.legKey(p); const s = new Set(this.selLegs()); s.has(k) ? s.delete(k) : s.add(k); this.selLegs.set(s); }
  allTradesSel = computed(() => { const t = this.filteredTrades(); return t.length > 0 && t.every(x => this.selTrades().has(x.id)); });
  allLegsSel = computed(() => { const l = this.openLegs(); return l.length > 0 && l.every(x => this.selLegs().has(this.legKey(x))); });
  toggleAllTrades() { this.selTrades.set(this.allTradesSel() ? new Set() : new Set(this.filteredTrades().map(x => x.id))); }
  toggleAllLegs() { this.selLegs.set(this.allLegsSel() ? new Set() : new Set(this.openLegs().map(x => this.legKey(x)))); }
  clearTradeSel() { this.selTrades.set(new Set()); }
  clearLegSel() { this.selLegs.set(new Set()); }
  bulkSetTrades(strat: string) {
    const ids = [...this.selTrades()];
    if (!ids.length || this.bulkBusy()) return;
    this.bulkBusy.set(true);
    this.svc.bulkSetTradeStrategy(ids, strat).subscribe({
      next: r => {
        this.bulkBusy.set(false); this.selTrades.set(new Set());
        this.notice.set(`${r.updated} trade${r.updated === 1 ? '' : 's'} → ${strat}`); setTimeout(() => this.notice.set(null), 2200);
        this.reloadAll();
      },
      error: (e: HttpErrorResponse) => { this.bulkBusy.set(false); this._err(e, 'Could not bulk-assign'); },
    });
  }
  bulkSetLegs(strat: string) {
    const legs = this.openLegs().filter(p => this.selLegs().has(this.legKey(p)))
      .map(p => ({ account_id: p.account_id, tradingsymbol: p.tradingsymbol }));
    if (!legs.length || this.bulkBusy()) return;
    this.bulkBusy.set(true);
    this.svc.bulkSetLegStrategy(legs, strat).subscribe({
      next: r => {
        this.bulkBusy.set(false); this.selLegs.set(new Set());
        this.notice.set(`${r.updated} position${r.updated === 1 ? '' : 's'} → ${strat}`); setTimeout(() => this.notice.set(null), 2200);
        this.loadOpenPositions();
      },
      error: (e: HttpErrorResponse) => { this.bulkBusy.set(false); this._err(e, 'Could not bulk-assign'); },
    });
  }

  reclassify(t: FnoTrade, ev: Event) {
    const strategy = (ev.target as HTMLSelectElement).value;
    this.svc.setTradeStrategy(t.id, strategy).subscribe({
      next: () => this.reloadAll(),
      error: (e: HttpErrorResponse) => this._err(e, 'Could not reclassify'),
    });
  }
  // retag one open leg's strategy from the Open Positions table (per-leg override)
  reclassifyLeg(p: FnoOpenLeg, ev: Event) {
    const strategy = (ev.target as HTMLInputElement).value.trim();
    if (strategy === (p.strategy || '')) return;
    this.svc.setLegStrategy(p.account_id, p.tradingsymbol, strategy || null).subscribe({
      next: () => { this.loadOpenPositions(); this.notice.set(`“${p.tradingsymbol}” → ${strategy || 'auto'}`); setTimeout(() => this.notice.set(null), 2200); },
      error: (e: HttpErrorResponse) => this._err(e, 'Could not change strategy'),
    });
  }
  fmtDate(d: string | null): string {
    if (!d) return '—';
    const dt = new Date((d.length <= 10 ? d + 'T00:00:00' : d));
    return isNaN(dt.getTime()) ? d : dt.toLocaleDateString('en-IN', { day: '2-digit', month: 'short' });
  }
  /** chart-tooltip date: "Wed, 10 Dec 2025" (+ " · HH:MM[:SS]") — no locale double-comma */
  private fmtFull(d: Date, time?: 'min' | 'sec', tz?: string): string {
    const o = tz ? { timeZone: tz } : {};
    const wd = d.toLocaleDateString('en-IN', { weekday: 'short', ...o });
    const dmy = d.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric', ...o });
    if (!time) return `${wd}, ${dmy}`;
    const t = d.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', ...(time === 'sec' ? { second: '2-digit' } : {}), hour12: false, ...o });
    return `${wd}, ${dmy} · ${t}`;
  }
  fmtDayLong(d: string | null): string {
    if (!d) return '—';
    const dt = new Date(d + 'T00:00:00');
    return isNaN(dt.getTime()) ? d : dt.toLocaleDateString('en-IN', { weekday: 'short', day: '2-digit', month: 'long', year: 'numeric' });
  }
  fmtDateMed(d: string | null): string {
    if (!d) return '—';
    const dt = new Date(d + 'T00:00:00');
    return isNaN(dt.getTime()) ? d : dt.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' });
  }
  fmtTime(ts: string | null): string {
    if (!ts) return '—';
    const d = new Date(ts);
    return isNaN(d.getTime()) ? ts : d.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' });
  }
  fmtDt(ts: string | null): string {
    if (!ts) return '—';
    const d = new Date(ts);
    return isNaN(d.getTime()) ? ts : d.toLocaleString('en-IN', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' });
  }

  // ── strategy mini-bars (pure CSS: two half-tracks around a midline) ──────
  bars(s: FnoStrategyStats): { hPct: number; pos: boolean; title: string }[] {
    const max = Math.max(1, ...s.recent.map(r => Math.abs(r.total)));
    return s.recent.map(r => ({
      hPct: Math.max(5, (Math.abs(r.total) / max) * 100),
      pos: r.total >= 0,
      title: `${r.date} · ${this.inrS(r.total)}`,
    }));
  }
}
