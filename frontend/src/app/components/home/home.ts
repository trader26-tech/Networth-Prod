import { Component, OnInit, OnDestroy, inject, signal, computed, effect, HostListener } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { DashboardService, Dashboard } from '../../services/dashboard.service';
import { RemindersService, ReminderFeed, ReminderItem } from '../../services/reminders.service';
import { FnoService, FnoHealth, FnoHealthIssue } from '../../services/fno.service';
import { MarketService } from '../../services/market.service';
import { ExportService, ExportChoice } from '../../services/export.service';
import { BuyPlanner } from '../buy-planner/buy-planner';
import { ReminderCalendar } from '../reminder-calendar/reminder-calendar';
import { HomeTax } from '../home-tax/home-tax';

type SellMode = 'full' | 'partial' | 'fixed';
type Strategy = 'fastest' | 'income' | 'balanced';
// cagr/yld = per-CLASS overrides; assetCagr/assetYld = per-ASSET (current holding) overrides.
interface GoalSettings {
  sellMode: Record<string, SellMode>;
  cagr: Record<string, number>; yld: Record<string, number>;
  assetCagr: Record<string, number>; assetYld: Record<string, number>;
}

// one distinct hue per asset class (no repeats)
const CLASS_COLOR: Record<string, string> = {
  apartments: '#4e8cff', land: '#2bb673', built: '#9b6dd6', stocks: '#f0883e',
  gold: '#e8b730', bonds: '#16b8a6', fd: '#00a3c4', ulip: '#e0598b', cash: '#94a3b8',
};
const PERSON_COLORS = ['#5b8def', '#7c3fbf', '#20c4a8', '#f0883e', '#2bb673', '#e8657d', '#e8b730'];

@Component({
  selector: 'app-home',
  standalone: true,
  imports: [CommonModule, FormsModule, BuyPlanner, RouterLink, ReminderCalendar, HomeTax],
  templateUrl: './home.html',
  styleUrl: './home.scss',
})
export class Home implements OnInit, OnDestroy {
  private api = inject(DashboardService);
  private remApi = inject(RemindersService);
  private fno = inject(FnoService);
  market = inject(MarketService);
  private exporter = inject(ExportService);

  data = signal<Dashboard | null>(null);
  // Chanakya — the whole-picture tax strategist (home dashboard only)
  taxOpen = signal(false);
  taxExpanded = signal(false);
  taxImg = '/chanakya-tax.png';
  taxImgOk = signal(true);
  closeTax() { this.taxOpen.set(false); this.taxExpanded.set(false); }
  reminders = signal<ReminderFeed | null>(null);
  remInr = RemindersService.inr;
  loading = signal(true);
  error = signal<string | null>(null);
  lastUpdated = signal<Date | null>(null);
  private _poll: any = null;

  inr = DashboardService.inr;
  inrFull = DashboardService.inrFull;
  /** signed compact ₹ (+ for gains, − for losses) — used by the F&O P&L tile */
  inrSigned = (v: number | null | undefined): string =>
    (v === null || v === undefined || isNaN(v as number)) ? '—'
      : (v > 0 ? '+' : '') + DashboardService.inr(v);
  pct = DashboardService.pct;
  color = (c: string) => CLASS_COLOR[c] || '#6b7190';
  personColor = (i: number) => PERSON_COLORS[i % PERSON_COLORS.length];

  /** Per-person monthly income (salary + rent + coupons), earners only — shown
   * on hover over the Monthly-income KPI. */
  incomeEarners = computed(() => {
    const d = this.data(); if (!d) return [];
    return d.by_person.filter(p => p.monthly_income > 0)
      .sort((a, b) => b.monthly_income - a.monthly_income);
  });

  /** ALL liabilities clubbed into one figure — loans + tenant advances to repay —
   *  with a grouped breakdown for the hover popover. null when nothing is owed. */
  liabilities = computed(() => {
    const d = this.data(); if (!d) return null;
    const L = d.liabilities, A = d.advances;
    const loanTotal = L?.outstanding ?? 0, advTotal = A?.total ?? 0;
    const total = loanTotal + advTotal;
    if (total <= 0) return null;
    return {
      total, emi: L?.monthly_emi ?? 0,
      count: (L?.active_count ?? 0) + (A?.count ?? 0),
      loanTotal, loanEmi: L?.monthly_emi ?? 0, loans: L?.by_person ?? [],
      advTotal, advances: A?.items ?? [],
    };
  });

  /** Next few pending reminders (already date-sorted server-side) — the
   * "who gets what & when" list on the dashboard reminders widget. */
  upcomingReminders = computed<ReminderItem[]>(() => {
    const f = this.reminders(); if (!f) return [];
    return f.items.filter(i => i.status === 'pending' && i.date >= f.today).slice(0, 6);
  });
  remRel(iso: string): string {
    const f = this.reminders(); if (!f) return '';
    const d = (s: string) => { const [y, m, dd] = s.split('-').map(Number); return new Date(y, m - 1, dd).getTime(); };
    const days = Math.round((d(iso) - d(f.today)) / 86400000);
    return days <= 0 ? 'today' : days === 1 ? 'tomorrow' : days < 30 ? `in ${days}d` : `in ${Math.round(days / 30)}mo`;
  }
  remShortDate(iso: string): string {
    const [y, m, dd] = iso.split('-').map(Number);
    return new Date(y, m - 1, dd).toLocaleDateString('en-IN', { day: 'numeric', month: 'short' });
  }

  // ── bell → F&O data-health center ───────────────────────────────────────────
  // The bell no longer nags about money (that's the dashboard money-calendar); it
  // flags what needs fixing to keep the data real: expired Kite logins (Ranjeev
  // shows twice — his account + the master price-feed app) and tradebooks not
  // caught up to the last trading day. Each can be fixed, marked done, or ignored.
  health = signal<FnoHealth | null>(null);
  showBell = signal(false);
  showIgnored = signal(false);
  healthBusy = signal<string | null>(null);

  private loadHealth() {
    this.fno.health().subscribe({ next: h => this.health.set(h), error: () => {} });
  }
  toggleBell() { this.showBell.update(v => !v); if (this.showBell()) this.showIgnored.set(false); }
  closeBell() { this.showBell.set(false); }
  healthCount = computed(() => this.health()?.count || 0);
  healthOk = computed(() => !!this.health()?.ok);

  dismissIssue(i: FnoHealthIssue, action: 'done' | 'ignore') {
    this.healthBusy.set(i.key);
    this.fno.dismissHealth(i.key, i.state_token, action).subscribe({
      next: h => { this.health.set(h); this.healthBusy.set(null); },
      error: () => this.healthBusy.set(null),
    });
  }
  restoreIssue(i: FnoHealthIssue) {
    this.healthBusy.set(i.key);
    this.fno.restoreHealth(i.key).subscribe({
      next: h => { this.health.set(h); this.healthBusy.set(null); },
      error: () => this.healthBusy.set(null),
    });
  }

  /** Top spending categories this month — shown on hover over the Spent KPI. */
  expenseCats = computed(() => (this.data()?.spent?.by_category || []).slice(0, 8));

  /** dividends received by a person this year (0 if none) */
  personDividend(name: string): number {
    return this.data()?.dividends?.by_person.find(p => p.person === name)?.this_year || 0;
  }

  /** Breakdown behind the Monthly-surplus KPI so the math is auditable.
   * Surplus = recurring income (asset income + salary + other) − recurring expenses. */
  surplusParts = computed(() => {
    const d = this.data(); if (!d) return null;
    const salary = d.salary?.monthly_total || 0;
    const other = d.other_income?.monthly_total || 0;
    const dividends = d.dividends?.monthly || 0;
    const income = d.monthly_income || 0;
    const asset = income - salary - other - dividends;   // positions: rent, coupons, FD interest
    // surplus = income − actual spent this month − planned-buy set-asides − loan EMIs
    const spent = d.spent_this_month || 0;
    const goals = d.planner_committed || 0;
    const emi = d.liabilities?.monthly_emi || 0;
    return { asset, salary, other, dividends, income, spent, goals, emi, surplus: income - spent - goals - emi };
  });

  // goal planner inputs
  goalCr = signal<number>(100);
  reqMonthly = signal<number>(500000);
  targetYears = signal<number>(15);

  // cash planner inputs
  cashNeed = signal<number>(2000000);
  cashWithinDays = signal<number>(0);    // 0 = any

  // person detail modal
  selectedPerson = signal<string | null>(null);
  photoUploading = signal(false);

  /** The dashboard reminder widget hides "what income is coming" noise (rent,
   * salary, other income). The money calendar now shows EVERY cashflow — salary,
   * rent, other income, dividends, bond/FD payouts, loan EMIs and expenses — so
   * it's a complete "what came in / went out this month" view. */
  readonly dashHideCategories: string[] = [];
  // full reallocation plan modal
  showPlan = signal(false);
  // inflation-adjusted withdrawal
  incomeGrows = signal(false);
  inflationPct = signal(8);

  // goal strategy + settings
  strategy = signal<'fastest' | 'income' | 'balanced'>('balanced');
  showSettings = signal(false);
  goalSettings = signal<GoalSettings>(this.readGS());

  readonly STRATS = [
    { key: 'fastest',  label: 'Fastest to corpus', icon: '🚀' },
    { key: 'income',   label: 'Max income',        icon: '💰' },
    { key: 'balanced', label: 'Balanced',          icon: '⚖️' },
  ] as const;
  readonly SELL_MODES = [
    { key: 'full', label: 'Sell fully' },
    { key: 'partial', label: 'Sell part' },
    { key: 'fixed', label: 'Fixed lots' },
  ] as const;

  private readGS(): GoalSettings {
    const def: GoalSettings = {
      sellMode: { land: 'full', apartments: 'full', built: 'full', stocks: 'partial', bonds: 'partial', gold: 'fixed' },
      cagr: {}, yld: {}, assetCagr: {}, assetYld: {},
    };
    if (typeof localStorage === 'undefined') return def;
    try {
      const s = JSON.parse(localStorage.getItem('goal_settings') || 'null');
      if (s) return { sellMode: { ...def.sellMode, ...s.sellMode }, cagr: { ...s.cagr }, yld: { ...s.yld },
        assetCagr: { ...s.assetCagr }, assetYld: { ...s.assetYld } };
    } catch {}
    return def;
  }
  settingsMode = signal<'class' | 'asset'>('class');
  private saveTimer: any;
  private writeGS(g: GoalSettings) {
    this.goalSettings.set(g);
    if (typeof localStorage !== 'undefined') localStorage.setItem('goal_settings', JSON.stringify(g));
    // persist to the DB (debounced so typing doesn't spam the API)
    clearTimeout(this.saveTimer);
    this.saveTimer = setTimeout(() => this.api.saveSettings(g).subscribe({ error: () => {} }), 600);
  }
  setSellMode(cls: string, mode: 'full' | 'partial' | 'fixed') {
    const g = this.goalSettings();
    this.writeGS({ ...g, sellMode: { ...g.sellMode, [cls]: mode } });
  }
  setCagr(cls: string, pctVal: number | null) {
    const g = this.goalSettings(); const cagr = { ...g.cagr };
    if (pctVal == null || isNaN(pctVal)) delete cagr[cls]; else cagr[cls] = +pctVal / 100;
    this.writeGS({ ...g, cagr });
  }
  setYield(cls: string, pctVal: number | null) {
    const g = this.goalSettings(); const yld = { ...g.yld };
    if (pctVal == null || isNaN(pctVal)) delete yld[cls]; else yld[cls] = +pctVal / 100;
    this.writeGS({ ...g, yld });
  }

  /** default value-growth CAGR from data (null = unknown, e.g. gold without a date). */
  private defCagr(cls: string): number | null {
    if (cls === 'bonds') return 0;   // principal is flat; the coupon is income
    return this.data()?.by_class.find(c => c.asset_class === cls)?.cagr ?? null;
  }
  /** default income yield from data (annual income ÷ value). */
  private defYield(cls: string): number {
    const c = this.data()?.by_class.find(x => x.asset_class === cls);
    return c && c.value > 0 ? (c.monthly_income * 12) / c.value : 0;
  }
  /** effective (possibly overridden) value-growth CAGR + income yield per CLASS (generic). */
  effCagr(cls: string): number | null { const o = this.goalSettings().cagr[cls]; return o != null ? o : this.defCagr(cls); }
  effYield(cls: string): number { const o = this.goalSettings().yld[cls]; return o != null ? o : this.defYield(cls); }

  // ── per-asset (current holding) future-growth overrides ──────────────────────
  posKey(p: { asset_class: string; name: string; owner: string }): string { return `${p.asset_class}|${p.name}|${p.owner}`; }
  /** future growth for a specific holding: per-asset override → else its class rate. */
  effCagrFor(p: any): number | null { const o = this.goalSettings().assetCagr[this.posKey(p)]; return o != null ? o : this.effCagr(p.asset_class); }
  effYieldFor(p: any): number { const o = this.goalSettings().assetYld[this.posKey(p)]; return o != null ? o : this.effYield(p.asset_class); }
  setAssetCagr(p: any, pctVal: number | null) {
    const g = this.goalSettings(); const m = { ...g.assetCagr };
    if (pctVal == null || isNaN(pctVal)) delete m[this.posKey(p)]; else m[this.posKey(p)] = +pctVal / 100;
    this.writeGS({ ...g, assetCagr: m });
  }
  setAssetYield(p: any, pctVal: number | null) {
    const g = this.goalSettings(); const m = { ...g.assetYld };
    if (pctVal == null || isNaN(pctVal)) delete m[this.posKey(p)]; else m[this.posKey(p)] = +pctVal / 100;
    this.writeGS({ ...g, assetYld: m });
  }
  assetCagrPct(p: any): number | null { const v = this.effCagrFor(p); return v == null ? null : Math.round(v * 1000) / 10; }
  assetYieldPct(p: any): number { return Math.round(this.effYieldFor(p) * 1000) / 10; }
  hasAssetOverride(p: any): boolean { const k = this.posKey(p); const g = this.goalSettings(); return g.assetCagr[k] != null || g.assetYld[k] != null; }
  /** A class's value-weighted effective rate across its holdings' per-asset overrides. */
  private classEff(cls: string, kind: 'cagr' | 'yld'): number | null {
    const d = this.data(); const fallback = kind === 'cagr' ? this.effCagr(cls) : this.effYield(cls);
    if (!d) return fallback;
    const ps = d.positions.filter(p => p.asset_class === cls);
    let w = 0, c = 0;
    ps.forEach(p => { const v = kind === 'cagr' ? this.effCagrFor(p) : this.effYieldFor(p); if (v == null) return; w += p.value; c += p.value * v; });
    return w > 0 ? c / w : fallback;
  }

  /** classes present, for the settings editor. */
  classRows = computed(() => {
    const d = this.data(); if (!d) return [];
    return d.by_class.map(c => ({ asset_class: c.asset_class, label: c.label }));
  });
  cagrPct(cls: string): number | null { const v = this.effCagr(cls); return v == null ? null : Math.round(v * 1000) / 10; }
  yieldPct(cls: string): number { return Math.round(this.effYield(cls) * 1000) / 10; }

  // ── settings-input drafts (so typing isn't reformatted mid-keystroke) ────────
  cagrDraft: Record<string, number | null> = {};
  yldDraft: Record<string, number | null> = {};
  openSettings() {
    const d = this.data();
    this.cagrDraft = {}; this.yldDraft = {};
    (d?.by_class || []).forEach(c => { this.cagrDraft[c.asset_class] = this.cagrPct(c.asset_class); this.yldDraft[c.asset_class] = this.yieldPct(c.asset_class); });
    (d?.positions || []).forEach(p => { const k = this.posKey(p); this.cagrDraft[k] = this.assetCagrPct(p); this.yldDraft[k] = this.assetYieldPct(p); });
    this.showSettings.set(true);
  }
  onCagrClass(cls: string, v: number | null) { this.cagrDraft[cls] = v; this.setCagr(cls, v); }
  onYieldClass(cls: string, v: number | null) { this.yldDraft[cls] = v; this.setYield(cls, v); }
  onCagrAsset(p: any, v: number | null) { this.cagrDraft[this.posKey(p)] = v; this.setAssetCagr(p, v); }
  onYieldAsset(p: any, v: number | null) { this.yldDraft[this.posKey(p)] = v; this.setAssetYield(p, v); }
  /** real holdings for the per-asset editor — largest holding first.
   * Sorted by a STABLE key (value), not by the growth rate you're editing, so
   * the list doesn't re-order under your cursor every time you type a value. */
  assetRows = computed(() => {
    const d = this.data(); if (!d) return [];
    return d.positions.map((p, i) => ({ ...p, i }))
      .filter(p => p.value > 0)
      .sort((a, b) => b.value - a.value);
  });

  ngOnInit() {
    this.load();
    this.loadHealth();
    this.remApi.feed().subscribe({ next: f => this.reminders.set(f), error: () => {} });
    // live net-worth refresh while the market is open (stocks move intraday)
    this._poll = setInterval(() => this.tick(), 60000);
    // saved asset assumptions from the DB win over the local cache
    this.api.getSettings().subscribe({
      next: s => {
        if (s && (s.sellMode || s.cagr || s.yld || s.assetCagr || s.assetYld)) {
          const def = this.readGS();
          const merged: GoalSettings = {
            sellMode: { ...def.sellMode, ...(s.sellMode || {}) },
            cagr: { ...(s.cagr || {}) }, yld: { ...(s.yld || {}) },
            assetCagr: { ...(s.assetCagr || {}) }, assetYld: { ...(s.assetYld || {}) },
          };
          this.goalSettings.set(merged);
          if (typeof localStorage !== 'undefined') localStorage.setItem('goal_settings', JSON.stringify(merged));
        }
      },
      error: () => {},
    });
  }

  ngOnDestroy() { if (this._poll) clearInterval(this._poll); }

  /** Read the Gold page's local ₹/gram overrides so the dashboard gold/silver
   *  values match what the Gold tab shows. `gold_rate22k` is a 22K rate → convert
   *  to a 24K-equivalent (÷0.916) for the backend. */
  private goldOverride(): { gold24k?: number; silver?: number } {
    if (typeof localStorage === 'undefined') return {};
    const out: { gold24k?: number; silver?: number } = {};
    const r22 = +(localStorage.getItem('gold_rate22k') || '');
    const sil = +(localStorage.getItem('silver_rate') || '');
    if (r22 > 0) out.gold24k = r22 / 0.916;
    if (sil > 0) out.silver = sil;
    return out;
  }

  load() {
    this.loading.set(true); this.error.set(null);
    const g = this.goldOverride();
    this.api.summary(g.gold24k, g.silver).subscribe({
      next: d => { this.data.set(d); this.loading.set(false); this.lastUpdated.set(new Date()); },
      error: () => { this.loading.set(false); this.error.set('Could not load the dashboard.'); },
    });
  }

  /** Reload the dashboard without the full-screen loading flash (keeps the
   *  person modal open) — used after an avatar change. */
  private refreshSilent() {
    const g = this.goldOverride();
    this.api.summary(g.gold24k, g.silver).subscribe({
      next: d => { this.data.set(d); this.lastUpdated.set(new Date()); }, error: () => {},
    });
  }

  /** Pick a photo for a person → upload → refresh so the new avatar shows. */
  onPhotoPicked(ev: Event, name: string) {
    const input = ev.target as HTMLInputElement;
    const file = input.files?.[0];
    if (!file || !name) return;
    this.photoUploading.set(true);
    this.api.uploadPersonPhoto(name, file).subscribe({
      next: () => { this.photoUploading.set(false); this.refreshSilent(); },
      error: () => { this.photoUploading.set(false); this.error.set('Could not upload the photo.'); },
    });
    input.value = '';
  }

  /** Remove a person's photo → refresh. */
  removePhoto(name: string) {
    if (!name) return;
    this.photoUploading.set(true);
    this.api.removePersonPhoto(name).subscribe({
      next: () => { this.photoUploading.set(false); this.refreshSilent(); },
      error: () => { this.photoUploading.set(false); },
    });
  }

  /** Silent net-worth refresh on the timer — only while the market is open and
   *  the tab is visible (picks up live stock prices). */
  private tick() {
    if (!this.market.isOpen() || !this.data()) return;
    if (typeof document !== 'undefined' && document.visibilityState !== 'visible') return;
    const g = this.goldOverride();
    this.api.summary(g.gold24k, g.silver).subscribe({ next: d => { this.data.set(d); this.lastUpdated.set(new Date()); }, error: () => {} });
  }
  updatedLabel(): string {
    const d = this.lastUpdated();
    return d ? d.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' }) : '';
  }

  // ── allocation donut ─────────────────────────────────────────────────────────
  donut = computed(() => {
    const d = this.data(); if (!d || !d.net_worth) return null;
    const R = 52, C = 2 * Math.PI * R;
    let off = 0;
    const segs = d.by_class.map(c => {
      const len = c.pct * C;
      const seg = { ...c, dash: `${len} ${C - len}`, offset: -off, color: this.color(c.asset_class) };
      off += len;
      return seg;
    });
    return { R, C, segs };
  });

  // ── income received / expense paid checks (optimistic — instant UI) ───────────
  alertTab = signal<'income' | 'expense'>('income');

  toggleReceipt(it: { key: string; received: boolean; amount: number }) {
    const next = !it.received;
    this.data.update(d => {
      if (!d) return d;
      const items = d.income_due.items.map(x => x.key === it.key ? { ...x, received: next } : x);
      const received = items.filter(x => x.received).reduce((s, x) => s + x.amount, 0);
      const pending_count = items.filter(x => !x.received).length;
      return { ...d, income_due: { ...d.income_due, items, received, pending: d.income_due.expected - received, pending_count } };
    });
    this.api.setReceipt(it.key, next, it.amount).subscribe({ error: () => this.load() });
  }

  /** What makes up "Liquid now" — cash + things realisable within ~3 days
   * (gold, FD), by class. Stocks are excluded (market-timing risk). */
  liquidNow = computed(() => {
    const d = this.data(); if (!d) return { rows: [] as any[], total: 0 };
    const byClass: Record<string, { asset_class: string; label: string; realisable: number; days: number }> = {};
    d.positions.filter(p => p.liquidity_tier <= 1 && p.asset_class !== 'stocks').forEach(p => {
      const k = p.asset_class;
      if (!byClass[k]) byClass[k] = { asset_class: k, label: p.class_label, realisable: 0, days: p.liquidity_days };
      byClass[k].realisable += p.realisable;
    });
    const rows = Object.values(byClass).sort((a, b) => b.realisable - a.realisable);
    return { rows, total: rows.reduce((s, r) => s + r.realisable, 0) };
  });

  /** value-growth as a fraction (current vs purchase), or null when cost unknown. */
  growthOf(invested: number | null, value: number): number | null {
    return invested != null && invested > 0 ? (value - invested) / invested : null;
  }

  /** Condensed recommendation for the goal card — derived from the same auto
   * recommendation the simulator shows. */
  plan = computed(() => {
    const d = this.data(); const s = this.simResult(); if (!d || !s) return null;
    const gs = this.goalSettings();
    const sells = d.positions.map((p, i) => ({ p, i })).filter(x => this.simSells().has(x.i)).map(({ p }) => ({
      name: p.name, asset_class: p.asset_class, class_label: p.class_label,
      cagr: this.effCagr(p.asset_class), sell: p.realisable,
      mode: gs.sellMode[p.asset_class] || 'full',
    }));
    const buys = [...this.simBuys()].map(cls => {
      const c = d.by_class.find(x => x.asset_class === cls);
      return { label: c?.label || cls, asset_class: cls, amount: s.perBuy,
        why: this.effYield(cls) > 0.001 ? 'growth + income' : 'compounds your corpus' };
    });
    const status = (sells.length === 0 && s.reachOk && s.incomeOk) ? 'ontrack' : (s.reachOk ? 'plan' : 'unreachable');
    return { status, sells, buys, blended: s.cAfter, simInc: s.incAfter, newYears: s.yearsAfter,
      years: s.years, target: s.target, curC: s.cNow };
  });

  // ── interactive full-plan modal ──────────────────────────────────────────────
  donutR = 52;
  simSells = signal<Set<number>>(new Set());
  simBuys = signal<Set<string>>(new Set());

  private corpusCagrOf(cls: string, _cagr?: number | null): number | null {
    return this.effCagr(cls);
  }

  /** Total return = value-growth + income yield; per-asset where set, else class. */
  private totalReturn(cls: string): number { return (this.effCagr(cls) ?? 0) + this.effYield(cls); }
  private totalReturnFor(p: any): number { return (this.effCagrFor(p) ?? 0) + this.effYieldFor(p); }

  /** Candidate assets to sell — ranked by each holding's TOTAL future return, weakest first. */
  sellOptions = computed(() => {
    const d = this.data(); if (!d) return [];
    return d.positions.map((p, i) => ({ ...p, i, futCagr: this.effCagrFor(p) }))
      .sort((a, b) => this.totalReturnFor(a) - this.totalReturnFor(b));
  });
  /** Candidate classes to reinvest into — with their (editable) growth & yield. */
  buyOptions = computed(() => {
    const d = this.data(); if (!d) return [];
    return d.by_class.map(c => ({
      asset_class: c.asset_class, label: c.label,
      cagr: this.effCagr(c.asset_class), yield: this.effYield(c.asset_class),
      isIncome: this.effYield(c.asset_class) > 0.001,
    })).sort((a, b) => ((b.cagr ?? -1) + (b.yield)) - ((a.cagr ?? -1) + (a.yield)));
  });

  constructor() {
    // re-plan whenever the goal sliders (or data) change — the recommended
    // sells & reinvests vary with the target corpus / horizon / income.
    effect(() => {
      const d = this.data();
      this.goalCr(); this.targetYears(); this.reqMonthly();   // track
      if (!d) return;
      const rec = this.recommend();
      this.simSells.set(rec.sells);
      this.simBuys.set(rec.buys);
    }, { allowSignalWrites: true });
  }

  openPlanModal() { this.showPlan.set(true); }

  /** Auto-pick what to sell & where to reinvest to hit the current sliders. */
  private recommend(): { sells: Set<number>; buys: Set<string> } {
    const d = this.data(); if (!d) return { sells: new Set(), buys: new Set() };
    const NW = d.net_worth, target = this.goalCr() * 1e7, years = Math.max(1, this.targetYears()), reqMonthly = this.reqMonthly();
    const requiredCagr = target > NW ? Math.pow(target / NW, 1 / years) - 1 : 0;
    const top = d.by_class.filter(c => c.cagr != null && c.asset_class !== 'bonds').sort((a, b) => b.cagr! - a.cagr!)[0];
    const incomeNeeded = reqMonthly > d.monthly_income + 1;
    const buys = new Set<string>(); if (top) buys.add(top.asset_class); if (incomeNeeded) buys.add('bonds');
    // laggards by each holding's TOTAL future return, and never sell a class we buy
    const growerTotal = top ? this.totalReturn(top.asset_class) : 0;
    const laggards = d.positions.map((p, i) => ({ ...p, i }))
      .filter(p => !buys.has(p.asset_class) && this.totalReturnFor(p) < growerTotal - 0.005)
      .sort((a, b) => this.totalReturnFor(a) - this.totalReturnFor(b));
    const sells = new Set<number>();
    const meets = () => { const r = this.simulate(sells, buys); return r.cAfter >= requiredCagr - 0.0015 && r.incAfter >= reqMonthly - 1; };
    if (!meets()) for (const lag of laggards) { sells.add(lag.i); if (meets()) break; }
    return { sells, buys };
  }
  toggleSimSell(i: number) { this.simSells.update(s => { const n = new Set(s); n.has(i) ? n.delete(i) : n.add(i); return n; }); }
  toggleSimBuy(c: string) { this.simBuys.update(s => { const n = new Set(s); n.has(c) ? n.delete(c) : n.add(c); return n; }); }
  isSimSell(i: number) { return this.simSells().has(i); }
  isSimBuy(c: string) { return this.simBuys().has(c); }

  private buildDonut(items: { asset_class: string; value: number }[]) {
    const R = this.donutR, C = 2 * Math.PI * R;
    const total = items.reduce((s, i) => s + Math.max(0, i.value), 0) || 1;
    let off = 0;
    return items.filter(i => i.value > 0).sort((a, b) => b.value - a.value).map(i => {
      const pct = i.value / total, len = pct * C;
      const seg = { asset_class: i.asset_class, value: i.value, pct, dash: `${len} ${C - len}`, offset: -off, color: this.color(i.asset_class) };
      off += len; return seg;
    });
  }

  /** Pure simulation. Each class grows at its value-CAGR and throws off income
   * at its yield. Freed capital is split equally across the reinvest picks, and
   * each pick both compounds (value-CAGR) and adds income (yield). */
  private simulate(sellIdx: Set<number>, buyClasses: Set<string>) {
    const d = this.data()!;
    const target = this.goalCr() * 1e7, years = Math.max(1, this.targetYears()), reqMonthly = this.reqMonthly();
    const eC = (c: string) => this.effCagr(c), eY = (c: string) => this.effYield(c);

    const NWnow = d.net_worth;
    // current corpus CAGR + income are position-based (per-asset growth where set)
    let cw = 0, cc = 0, incNow = 0;
    d.positions.forEach(p => { const cg = this.effCagrFor(p); if (cg != null) { cw += p.value; cc += p.value * cg; } incNow += p.value * this.effYieldFor(p) / 12; });
    const cNow = cw > 0 ? cc / cw : 0;

    const sold = d.positions.filter((_, i) => sellIdx.has(i));
    const freed = sold.reduce((s, p) => s + p.realisable, 0);
    const soldVal = sold.reduce((s, p) => s + p.value, 0);
    const soldInc = sold.reduce((s, p) => s + p.value * this.effYieldFor(p) / 12, 0);
    const soldCagr = sold.reduce((s, p) => s + p.value * (this.effCagrFor(p) ?? 0), 0) / (soldVal || 1);

    const buys = [...buyClasses];
    const perBuy = buys.length ? freed / buys.length : 0;

    // pie / allocation stays at class level
    const alloc = new Map<string, number>();
    d.by_class.forEach(c => alloc.set(c.asset_class, c.value));
    for (const s of sold) alloc.set(s.asset_class, (alloc.get(s.asset_class) || 0) - s.value);
    for (const b of buys) alloc.set(b, (alloc.get(b) || 0) + perBuy);

    // corpus CAGR after: kept holdings at their per-asset growth; new capital at the generic class rate
    let w2 = 0, c2 = 0;
    d.positions.forEach((p, i) => { if (sellIdx.has(i)) return; const cg = this.effCagrFor(p); if (cg != null) { w2 += p.value; c2 += p.value * cg; } });
    for (const b of buys) { const cg = eC(b); w2 += perBuy; if (cg != null) c2 += perBuy * cg; }
    const NWafter = NWnow - soldVal + freed;
    const cAfter = w2 > 0 ? c2 / w2 : cNow;
    const reinvestInc = buys.reduce((s, b) => s + perBuy * eY(b) / 12, 0);
    const incAfter = incNow - soldInc + reinvestInc;
    // realistic projection: corpus appreciates + earns income, you withdraw the
    // target income (optionally growing with inflation), surplus reinvests.
    const yieldRate = NWafter > 0 ? (incAfter * 12) / NWafter : 0;
    const inflation = this.incomeGrows() ? this.inflationPct() / 100 : 0;
    const proj = this.project(NWafter, cAfter, yieldRate, reqMonthly * 12, inflation, years, target);
    const yearsAfter = proj.reachYear;
    const depletedYear = proj.depletedYear;
    const withdrawAtHorizon = reqMonthly * Math.pow(1 + inflation, years);

    // split reinvest income (yield-bearing picks) vs pure-growth, for the KPIs
    const incomeBuys = buys.filter(b => eY(b) > 0.001);
    const toIncomeCapital = incomeBuys.length * perBuy;
    const toGrowthCapital = freed - toIncomeCapital;

    const labelOf = (cls: string) => d.by_class.find(c => c.asset_class === cls)?.label || cls;
    const classes = new Set<string>([...d.by_class.map(c => c.asset_class), ...alloc.keys()]);
    const rows: any[] = [];
    classes.forEach(cls => {
      const nowV = d.by_class.find(c => c.asset_class === cls)?.value || 0;
      const afterV = Math.max(0, alloc.get(cls) || 0);
      if (nowV <= 0 && afterV <= 0) return;
      rows.push({ asset_class: cls, label: labelOf(cls), color: this.color(cls), now: nowV, after: afterV, delta: afterV - nowV });
    });
    rows.sort((a, b) => b.after - a.after);
    const R = this.donutR, C = 2 * Math.PI * R; let off = 0;
    rows.forEach(r => { r.pct = NWafter > 0 ? r.after / NWafter : 0; });
    const pie = rows.filter(r => r.after > 0).map(r => {
      const len = r.pct * C; const seg = { ...r, dash: `${len} ${C - len}`, offset: -off }; off += len; return seg;
    });

    const incomeSurplus = incAfter - reqMonthly;
    const projectedNW = proj.valueAtHorizon;
    return {
      NWnow, NWafter, projectedNW, cNow, cAfter, incNow, incAfter, yearsAfter,
      target, years, reqMonthly, freed, soldCount: sold.length, buyCount: buys.length,
      soldCagr, perBuy, reinvestInc, toIncomeCapital, toGrowthCapital, incomeSurplus,
      depletedYear, withdrawAtHorizon, inflation,
      pie, allocRows: rows,
      reachOk: yearsAfter != null && yearsAfter <= years && depletedYear == null,
      incomeOk: incAfter >= reqMonthly - 1,
    };
  }

  /** Live result of the current selections — drives the modal hero + pie. */
  simResult = computed(() => {
    if (!this.data()) return null;
    return this.simulate(this.simSells(), this.simBuys());
  });

  // ── year-by-year income schedule ─────────────────────────────────────────────
  thisYear = new Date().getFullYear();
  showIncome = signal(false);
  expandedYear = signal<number | null>(null);
  toggleYear(y: number) { this.expandedYear.update(c => (c === y ? null : y)); }

  incomeSchedule = computed(() => {
    const s = this.simResult(); if (!s) return null;
    const horizon = Math.max(1, this.targetYears());
    // Income rises only because the asset's VALUE compounds (rent tracks value) —
    // no fresh capital needed. Bonds' principal is flat, so their coupon stays flat.
    const classes = s.allocRows
      .map(r => ({ asset_class: r.asset_class, label: r.label, color: r.color, value: r.after, growth: this.classEff(r.asset_class, 'cagr') ?? 0, yld: this.classEff(r.asset_class, 'yld') ?? 0 }))
      .filter(c => c.yld > 0.0005 && c.value > 0);
    if (!classes.length) return null;

    const rows: any[] = [];
    let maxTotal = 0, grand = 0;
    for (let y = 0; y < horizon; y++) {
      const per = classes.map(c => {
        const val = c.value * Math.pow(1 + c.growth, y);   // value compounds → income rises with it
        const annual = val * c.yld;
        return { asset_class: c.asset_class, label: c.label, color: c.color, growth: c.growth, annual, monthly: annual / 12 };
      }).filter(p => p.annual > 0).sort((a, b) => b.annual - a.annual);
      const total = per.reduce((a, p) => a + p.annual, 0);
      maxTotal = Math.max(maxTotal, total); grand += total;
      rows.push({ calYear: this.thisYear + y, yearIdx: y + 1, total, monthly: total / 12, per });
    }
    rows.forEach(r => r.per.forEach((p: any) => p.widthPct = maxTotal > 0 ? (p.annual / maxTotal) * 100 : 0));
    return { rows, maxTotal, grand, firstMonthly: rows[0].monthly, lastMonthly: rows[rows.length - 1].monthly };
  });

  /** Year-by-year drawdown: corpus appreciates + earns income, you withdraw a
   * (possibly inflation-growing) amount; surplus reinvests, shortfall sells. */
  private project(nw: number, growth: number, yld: number, withdraw0: number, inflation: number, horizon: number, target: number) {
    let corpus = nw, withdraw = withdraw0;
    let reachYear: number | null = corpus >= target ? 0 : null;
    let depletedYear: number | null = null;
    let valueAtHorizon = nw;
    const cap = Math.max(horizon, 80);
    for (let y = 1; y <= cap; y++) {
      const income = corpus * yld;
      corpus = corpus * (1 + growth) + income - withdraw;
      if (corpus <= 0 && depletedYear == null) { depletedYear = y; corpus = 0; }
      if (corpus >= target && reachYear == null) reachYear = y;
      if (y === horizon) valueAtHorizon = Math.max(0, corpus);
      withdraw *= (1 + inflation);
      if (corpus <= 0) break;
    }
    if (depletedYear != null && depletedYear <= horizon) valueAtHorizon = 0;
    return { reachYear, depletedYear, valueAtHorizon };
  }

  private yearsTo(target: number, nw: number, cagr: number): number | null {
    if (target <= nw) return 0;
    if (cagr <= 0) return null;
    return Math.log(target / nw) / Math.log(1 + cagr);
  }

  // ── person detail ────────────────────────────────────────────────────────────
  personDetail = computed(() => {
    const name = this.selectedPerson(); const d = this.data();
    if (!name || !d) return null;
    const assets = d.positions.filter(p => p.owner === name).sort((a, b) => b.value - a.value);
    const person = d.by_person.find(p => p.person === name) || null;
    // salary isn't a position (it has no asset value) — pull it in separately so
    // the modal shows where this person's income actually comes from.
    const salary = (d.salary?.entries || []).filter((e: any) => e.person === name);
    const salaryMonthly = salary.reduce((s: number, e: any) => s + (e.monthly_inr || 0), 0);
    // purchase value + value-growth for the header (like the Stocks page's gain%)
    let invested = 0, value = 0, hasInv = false;
    for (const a of assets) { value += a.value || 0; if (a.invested != null) { invested += a.invested; hasInv = true; } }
    return { person, assets, salary, salaryMonthly,
             invested: hasInv ? invested : null, value, growth: hasInv ? this.growthOf(invested, value) : null };
  });

  // ── person modal: search / filter chips / sortable table + summary ──────────
  pmSearch = signal('');
  pmClasses = signal<Set<string>>(new Set());     // selected asset classes (empty = all)
  pmIncomeOnly = signal(false);
  pmSort = signal<'value' | 'purchase' | 'income' | 'name'>('value');
  pmSortDir = signal<'asc' | 'desc'>('desc');
  /** per-column widths (px) — draggable via the handle on each header edge.
   *  Cells beyond the width just truncate ("first few words…"). */
  pmColW = signal<Record<string, number>>({ name: 185, class: 120, income: 115, cagr: 85, value: 120 });
  cw(k: string): number { return this.pmColW()[k] || 100; }
  /** table width = exactly the sum of the columns, so resizing one column never
   *  reflows the others (no forced "fill the container" redistribution). */
  pmTableW = computed(() => ['name', 'class', 'income', 'cagr', 'value'].reduce((s, k) => s + this.cw(k), 0));
  private _rzKey: string | null = null;
  private _rzX = 0; private _rzW = 0;
  private _rzMove = (e: any) => {
    if (!this._rzKey) return;
    if (e.touches) e.preventDefault();                 // don't scroll while dragging
    const x = e.touches ? e.touches[0].clientX : e.clientX;
    const w = Math.max(46, this._rzW + (x - this._rzX));
    this.pmColW.update(m => ({ ...m, [this._rzKey!]: Math.round(w) }));
  };
  private _rzUp = () => {
    this._rzKey = null;
    document.removeEventListener('mousemove', this._rzMove);
    document.removeEventListener('mouseup', this._rzUp);
    document.removeEventListener('touchmove', this._rzMove, true);
    document.removeEventListener('touchend', this._rzUp);
    document.body.style.userSelect = '';
  };
  startResize(ev: any, key: string) {
    ev.preventDefault(); ev.stopPropagation();
    this._rzKey = key;
    this._rzX = ev.touches ? ev.touches[0].clientX : ev.clientX;
    this._rzW = this.cw(key);
    document.addEventListener('mousemove', this._rzMove);
    document.addEventListener('mouseup', this._rzUp);
    document.addEventListener('touchmove', this._rzMove, { passive: false, capture: true } as any);
    document.addEventListener('touchend', this._rzUp);
    document.body.style.userSelect = 'none';
  }

  pmShowFilter = signal(false);
  pmFilterCat = signal<'class' | 'type'>('class');
  /** fixed position for the popover (so the modal's overflow can't clip it). */
  pmFilterPos = signal<{ top: number; right: number } | null>(null);
  openPmFilter(btn: HTMLElement) {
    const open = !this.pmShowFilter();
    if (open && btn) {
      const r = btn.getBoundingClientRect();
      // clamp so a 340px popover never runs off the right/left edge of the screen
      const right = Math.max(8, Math.min(window.innerWidth - r.right, window.innerWidth - 348));
      this.pmFilterPos.set({ top: Math.round(r.bottom + 6), right: Math.round(right) });
    }
    this.pmShowFilter.set(open);
  }
  /** Close the filter popover when clicking anywhere outside it (replaces the old
   *  full-screen backdrop, which was intercepting clicks meant for the options). */
  @HostListener('document:click', ['$event'])
  onDocClick(e: MouseEvent) {
    const t = e.target as HTMLElement;
    if (this.showBell() && !t.closest('.bell-pop') && !t.closest('.rem-bell')) this.showBell.set(false);
    if (!this.pmShowFilter()) return;
    if (t.closest('.pm-fp') || t.closest('.pm-fbtn')) return;   // inside popover / on the toggle
    this.pmShowFilter.set(false);
  }

  /** Open a person's detail with a clean filter state. */
  openPerson(name: string) {
    this.pmReset(); this.pmShowFilter.set(false); this.pmFilterCat.set('class');
    // default the columns to fill the modal's content width (matches the toolbar
    // above), so the table isn't oddly narrower than the search/filter row.
    const vw = typeof window !== 'undefined' ? window.innerWidth : 900;
    const total = Math.max(560, Math.min(760, vw * 0.96) - 46);
    this.pmColW.set({
      name: Math.round(total * 0.29), class: Math.round(total * 0.185), income: Math.round(total * 0.185),
      cagr: Math.round(total * 0.14), value: Math.round(total * 0.20),
    });
    this.selectedPerson.set(name);
  }
  /** Reset filters + sort back to the original view (value, high → low). */
  pmReset() {
    this.pmSearch.set(''); this.pmClasses.set(new Set()); this.pmIncomeOnly.set(false);
    this.pmSort.set('value'); this.pmSortDir.set('desc');
  }
  pmToggleClass(c: string) { this.pmClasses.update(s => { const n = new Set(s); n.has(c) ? n.delete(c) : n.add(c); return n; }); }
  pmClearFilters() { this.pmSearch.set(''); this.pmClasses.set(new Set()); this.pmIncomeOnly.set(false); }
  pmToggleSort(k: 'value' | 'purchase' | 'income' | 'name') {
    if (this.pmSort() === k) this.pmSortDir.update(d => d === 'asc' ? 'desc' : 'asc');
    else { this.pmSort.set(k); this.pmSortDir.set(k === 'name' ? 'asc' : 'desc'); }
  }
  pmArrow(k: string): string { return this.pmSort() === k ? (this.pmSortDir() === 'desc' ? ' ▼' : ' ▲') : ''; }

  /** Salary/earned income folded in as rows (no asset value, but they carry the
   *  monthly income) so everything lives in one table under Assets. */
  pmAllRows = computed(() => {
    const pd = this.personDetail(); if (!pd) return [] as any[];
    const salary = (pd.salary as any[]).map(e => ({
      name: (e.note?.trim() || e.bank_account || 'Salary'), class_label: 'Salary', asset_class: '__salary',
      sub: e.bank_account || '', monthly_income: e.monthly_inr || 0, cagr: null, value: 0, isSalary: true,
    }));
    return [...(pd.assets as any[]), ...salary];
  });
  /** filtered assets (search + class + income-only); ordering is handled by
   *  pmByClass so the active column-sort applies to groups and their assets. */
  pmFiltered = computed(() => {
    let a = this.pmAllRows().slice();
    const q = this.pmSearch().trim().toLowerCase();
    if (q) a = a.filter(x => (x.name || '').toLowerCase().includes(q) || (x.class_label || '').toLowerCase().includes(q) || (x.sub || '').toLowerCase().includes(q));
    const cls = this.pmClasses(); if (cls.size) a = a.filter(x => cls.has(x.asset_class));
    if (this.pmIncomeOnly()) a = a.filter(x => (x.monthly_income || 0) > 0);
    return a;
  });
  /** distinct classes (incl. Salary) for the filter. */
  pmClassOptions = computed(() => {
    const seen = new Map<string, string>();
    for (const a of this.pmAllRows()) if (!seen.has(a.asset_class)) seen.set(a.asset_class, a.class_label || a.asset_class);
    return Array.from(seen, ([value, label]) => ({ value, label }));
  });
  pmActive = computed(() => !!(this.pmSearch() || this.pmClasses().size || this.pmIncomeOnly()));
  pmFilterCount = computed(() => this.pmClasses().size + (this.pmIncomeOnly() ? 1 : 0));
  /** summary of the currently-shown assets — powers the hero strip + Total row. */
  pmStats = computed(() => {
    const a = this.pmFiltered();
    let value = 0, income = 0, invested = 0, wv = 0, wc = 0, hasInv = false;
    for (const x of a as any[]) {
      value += x.value || 0; income += x.monthly_income || 0;
      if (x.invested != null) { invested += x.invested; hasInv = true; }
      if (x.cagr != null) { wv += x.value || 0; wc += (x.value || 0) * x.cagr; }
    }
    return { count: a.length, value, income, cagr: wv ? wc / wv : null,
             invested: hasInv ? invested : null, growth: hasInv ? this.growthOf(invested, value) : null };
  });

  // ── person modal: holdings split by asset class (like the Stocks page) ───────
  /** which class groups are expanded to reveal their individual assets. */
  pmExpanded = signal<Set<string>>(new Set());
  togglePmClass(k: string) { this.pmExpanded.update(s => { const n = new Set(s); n.has(k) ? n.delete(k) : n.add(k); return n; }); }
  /** a group shows its assets when expanded, or whenever a search is active. */
  isPmOpen(k: string): boolean { return this.pmExpanded().has(k) || !!this.pmSearch().trim(); }

  /** The selected person's holdings grouped by asset class (built from the
   *  filtered rows so search + filter apply), each group carrying subtotals and
   *  its assets — mirrors the Stocks page's account-grouped table. */
  pmByClass = computed(() => {
    const rows = this.pmFiltered();
    const totalVal = rows.reduce((s, x: any) => s + (x.value || 0), 0);
    const groups = new Map<string, any>();
    for (const a of rows as any[]) {
      let g = groups.get(a.asset_class);
      if (!g) {
        g = { asset_class: a.asset_class, label: a.class_label || a.asset_class, isSalary: !!a.isSalary,
              color: a.isSalary ? '#16a085' : this.color(a.asset_class),
              assets: [], value: 0, invested: 0, income: 0, wv: 0, wc: 0, hasInv: false };
        groups.set(a.asset_class, g);
      }
      g.assets.push(a);
      g.value += a.value || 0; g.income += a.monthly_income || 0;
      if (a.invested != null) { g.invested += a.invested; g.hasInv = true; }
      if (a.cagr != null) { g.wv += a.value || 0; g.wc += (a.value || 0) * a.cagr; }
    }
    const out = Array.from(groups.values()).map(g => ({
      ...g, count: g.assets.length,
      cagr: g.wv ? g.wc / g.wv : null,
      invested: g.hasInv ? g.invested : null,
      growth: g.hasInv ? this.growthOf(g.invested, g.value) : null,
      pct: totalVal > 0 ? g.value / totalVal : 0,
    }));
    // the active column-sort drives both the group order and each group's assets
    const k = this.pmSort(); const dir = this.pmSortDir() === 'desc' ? -1 : 1;
    const key = (x: any) => k === 'purchase' ? (x.invested ?? -1e18)
      : k === 'income' ? (x.monthly_income ?? x.income ?? 0) : (x.value || 0);
    const cmp = (x: any, y: any) => (k === 'name'
      ? (x.name || x.label || '').localeCompare(y.name || y.label || '')
      : (key(x) - key(y))) * dir;
    out.forEach(g => g.assets.sort(cmp));
    return out.sort(cmp);
  });

  /** Person's class split for the allocation bar — always value-ordered so the
   *  bar stays stable regardless of the table's column-sort. */
  pmAlloc = computed(() => this.pmByClass().filter(g => g.value > 0)
    .slice().sort((a, b) => b.value - a.value));

  /** Salary amount in its own currency, e.g. "KWD 450" or "₹1,00,000". */
  salaryNative(e: any): string {
    if (!e || e.amount == null) return '—';
    const cur = (e.currency || 'INR').toUpperCase();
    if (cur === 'INR') return DashboardService.inrFull(e.amount);
    return `${cur} ${Math.round(e.amount).toLocaleString('en-IN')}`;
  }

  // ── urgent cash planner ──────────────────────────────────────────────────────
  cashPlan = computed(() => {
    const d = this.data(); if (!d) return null;
    const need = this.cashNeed();
    const within = this.cashWithinDays();
    let pool = [...d.positions];
    if (within > 0) pool = pool.filter(p => p.liquidity_days <= within);
    pool.sort((a, b) => (a.liquidity_tier - b.liquidity_tier) || ((a.cagr ?? 0) - (b.cagr ?? 0)));

    const picks: any[] = [];
    let raised = 0, maxDays = 0;
    for (const p of pool) {
      if (raised >= need) break;
      const remaining = need - raised;
      const sell = p.divisible ? Math.min(p.realisable, remaining) : p.realisable;
      picks.push({ ...p, sell: Math.round(sell) });
      raised += sell;
      maxDays = Math.max(maxDays, p.liquidity_days);
    }
    return {
      need, raised: Math.round(raised), shortfall: Math.max(0, Math.round(need - raised)),
      covered: raised >= need, maxDays, picks,
    };
  });

  // ── data export (Excel / PDF) ────────────────────────────────────────────────
  showExport = signal(false);
  exportBusy = signal<'' | 'excel' | 'pdf'>('');
  exportErr = signal<string | null>(null);
  /** which asset classes are ticked (default: all present). */
  exportClasses = signal<Set<string>>(new Set());
  /** which sections to include. */
  exportSummary = signal(true);
  exportHoldings = signal(true);
  exportByClass = signal(true);
  exportByPerson = signal(true);

  /** the classes available to pick, in display order (value desc). */
  exportOptions = computed(() => {
    const d = this.data(); if (!d) return [] as { asset_class: string; label: string; value: number; count: number }[];
    return d.by_class.slice().sort((a, b) => b.value - a.value)
      .map(c => ({ asset_class: c.asset_class, label: c.label, value: c.value, count: c.count }));
  });

  /** live count of assets covered by the current selection. */
  exportCount = computed(() => {
    const d = this.data(); if (!d) return 0;
    const sel = this.exportClasses();
    return d.positions.filter(p => sel.has(p.asset_class)).length;
  });
  /** live value covered by the current selection. */
  exportValue = computed(() => {
    const d = this.data(); if (!d) return 0;
    const sel = this.exportClasses();
    return d.by_class.filter(c => sel.has(c.asset_class)).reduce((s, c) => s + c.value, 0);
  });

  openExport() {
    // default: everything selected
    const d = this.data();
    this.exportClasses.set(new Set((d?.by_class || []).map(c => c.asset_class)));
    this.exportSummary.set(true); this.exportHoldings.set(true);
    this.exportByClass.set(true); this.exportByPerson.set(true);
    this.exportErr.set(null); this.exportBusy.set('');
    this.showExport.set(true);
  }
  closeExport() { if (!this.exportBusy()) this.showExport.set(false); }
  isExportClass(c: string) { return this.exportClasses().has(c); }
  toggleExportClass(c: string) {
    this.exportClasses.update(s => { const n = new Set(s); n.has(c) ? n.delete(c) : n.add(c); return n; });
  }
  exportSelectAll() { this.exportClasses.set(new Set(this.exportOptions().map(o => o.asset_class))); }
  exportSelectNone() { this.exportClasses.set(new Set()); }
  exportAllSelected(): boolean { return this.exportClasses().size === this.exportOptions().length; }

  private exportChoice(): ExportChoice {
    return {
      classes: new Set(this.exportClasses()),
      summary: this.exportSummary(), holdings: this.exportHoldings(),
      byClass: this.exportByClass(), byPerson: this.exportByPerson(),
    };
  }
  /** nothing meaningful to export (no classes, or every section off). */
  exportEmpty(): boolean {
    return this.exportClasses().size === 0 ||
      !(this.exportSummary() || this.exportHoldings() || this.exportByClass() || this.exportByPerson());
  }

  async runExport(kind: 'excel' | 'pdf') {
    const d = this.data(); if (!d || this.exportBusy() || this.exportEmpty()) return;
    this.exportBusy.set(kind); this.exportErr.set(null);
    try {
      const choice = this.exportChoice();
      if (kind === 'excel') await this.exporter.excel(d, choice);
      else await this.exporter.pdf(d, choice);
      this.showExport.set(false);
    } catch {
      this.exportErr.set('Could not build the file. Please try again.');
    } finally {
      this.exportBusy.set('');
    }
  }
}
