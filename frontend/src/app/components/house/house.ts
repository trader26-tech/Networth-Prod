import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { DashboardService, Position } from '../../services/dashboard.service';
import { HouseService } from '../../services/house.service';

// ── TVH Bay Villa A-64 — funding & loan planner ──────────────────────────────
// Model: a loan fills whatever your committed assets can't cover, *by date*.
// The minimum loan you need is the PEAK shortfall a timing-aware cash-flow
// simulation produces. Commit more/earlier assets → the loan shrinks. Whatever
// is still owed after every sale is repaid from salary surplus (amortised).
// Plan persists to Supabase (app_cache KV) via /api/house/plan; autosaved.

const PLAN_V = 5;

export type SourceKind = 'cash' | 'maturity' | 'sale-done' | 'sale' | 'income' | 'other';

export interface FundingSource {
  id: string;
  name: string;
  amount: number;                    // realisable proceeds (what lands in the account)
  cost?: number;                     // cost basis / capital invested — proceeds − cost = capital gain
  kind: SourceKind;
  committed: boolean;                // in the funding pool? off = still "on loan"
  arrangeBy?: string;                // ISO yyyy-mm-dd — when this money is ready
  eta?: string;                      // free-text timing note
  owner?: string;
  note?: string;
}

export interface Milestone {
  id: string;
  label: string;
  pct: string;
  dueDate?: string;                  // ISO yyyy-mm-dd
  dueKind: 'fixed' | 'deadline' | 'estimate';
  amount: number;
  preferLoan?: boolean;              // "fill by loan" — force this stage onto the loan
  paid?: boolean;
  note?: string;
}

export interface PlanData {
  v?: number;
  villaName: string;
  saleableSqft: number;
  bookingDate: string;
  handoverEta: string;
  builderCost: number;
  registration: number;
  loanRate: number;                  // annual % on the loan
  monthlyRepay: number;              // ₹/month available to repay the residual loan
  sources: FundingSource[];
  milestones: Milestone[];
}

const DEFAULT_PLAN: PlanData = {
  v: PLAN_V,
  villaName: 'TVH Bay Villa · A-64',
  saleableSqft: 3865,
  bookingDate: '13 Jul 2026',
  handoverEta: 'Q1 2028',
  builderCost: 39_475_457,
  registration: 3_562_791,
  loanRate: 9,
  monthlyRepay: 590_000,

  // Real assets you CAN commit. All start uncommitted → the loan starts full;
  // toggle each on ("use for house") and the loan shrinks.
  sources: [
    { id: 'cash', name: 'Liquid cash', amount: 8_000_000, kind: 'cash', committed: false,
      arrangeBy: '2026-07-13', eta: 'In hand now', owner: 'Family',
      note: 'Most flexible money — best used for the booking + 45-day payment.' },
    { id: 'ulip', name: 'ULIP maturity', amount: 6_000_000, cost: 2_500_000, kind: 'maturity', committed: false,
      arrangeBy: '2026-08-25', eta: 'Matures ~Aug 2026', owner: 'Ramprasad',
      note: 'Confirm the credit date — it must land before the 45-day deadline. Tax-free u/s 10(10D) if premium ≤ ₹2.5L/yr.' },
    { id: 'mahindra', name: 'Mahindra Central (sold)', amount: 2_300_000, cost: 2_261_600, kind: 'sale-done', committed: false,
      arrangeBy: '2026-08-20', eta: 'Sold — clearing', owner: 'Ramprasad',
      note: 'Land already sold for ₹23L. LTCG ≈ ₹39k.' },
    { id: 'arranged', name: 'Arranged funds', amount: 1_000_000, kind: 'cash', committed: false,
      arrangeBy: '2026-08-20', eta: 'By 20 Aug', owner: 'Family',
      note: 'The ₹10L you can arrange — cheque/transfer only, keep it white.' },
    { id: 'velachery', name: 'Sell: Velachery house', amount: 16_000_000, cost: 1_918_125, kind: 'sale', committed: false,
      arrangeBy: '2026-11-30', eta: 'Close Nov 26 – Jan 27', owner: 'Ramprasad',
      note: '1300 sqft house, market ₹1.5–1.8 Cr. §54 → ~₹0 tax. Get the §197 certificate before registration.' },
    { id: 'b3', name: 'Sell: K.K. Nagar B3 flat', amount: 13_000_000, cost: 7_000_000, kind: 'sale', committed: false,
      arrangeBy: '2027-04-30', eta: 'Close Feb – Apr 27', owner: 'Ramprasad',
      note: 'Non-performer: 4.1% CAGR, 2.4% yield. Quoted ₹1.30 Cr after brokerage. §54 → ~₹0 tax.' },
  ],

  milestones: [
    { id: 'booking', label: 'Booking advance', pct: '—', dueDate: '2026-07-13', dueKind: 'fixed', amount: 500_000 },
    { id: 'uds', label: 'UDS 50% + registration', pct: '50%', dueDate: '2026-08-27', dueKind: 'deadline',
      amount: 19_237_728 + 3_562_791,
      note: 'The crunch: ₹2.28 Cr within 45 days of booking (builder 50% + registration at the SRO).' },
    { id: 'foundation', label: 'Foundation', pct: '10%', dueDate: '2026-11-15', dueKind: 'estimate', amount: 3_947_546 },
    { id: 'gf', label: 'Ground-floor slab', pct: '10%', dueDate: '2027-02-15', dueKind: 'estimate', amount: 3_947_546 },
    { id: 'ff', label: 'First-floor slab', pct: '10%', dueDate: '2027-05-15', dueKind: 'estimate', amount: 3_947_546 },
    { id: 'sf', label: 'Second-floor slab', pct: '10%', dueDate: '2027-08-15', dueKind: 'estimate', amount: 3_947_546 },
    { id: 'mep', label: 'Plastering & MEP', pct: '5%', dueDate: '2027-12-15', dueKind: 'estimate', amount: 1_973_773 },
    { id: 'handover', label: 'Handover', pct: '5%', dueDate: '2028-03-15', dueKind: 'estimate', amount: 1_973_773 },
  ],
};

const COST_ROWS: { label: string; amount: number; sub?: boolean }[] = [
  { label: 'Villa (3,865 sqft × ₹9,000)', amount: 34_785_000 },
  { label: 'Additional land + lift', amount: 2_555_000 },
  { label: 'GST 5%', amount: 1_867_000 },
  { label: 'Corpus + deposits + legal', amount: 268_457 },
  { label: 'Total to builder', amount: 39_475_457, sub: true },
  { label: 'Registration + stamp + misc', amount: 3_562_791 },
  { label: 'All-in cost', amount: 43_038_248, sub: true },
];

// ── engine types ─────────────────────────────────────────────────────────────
export interface FundingLeg { sourceId: string; amount: number; }   // sourceId 'loan' = loan
export interface TimelineEvent {
  kind: 'in' | 'out';
  date: string;
  label: string;
  amount: number;
  draw: number;               // loan drawn at this event (payments)
  repay: number;              // loan repaid at this event (fund arrivals)
  balance: number;            // loan balance after this event
  cashInHand: number;         // uncommitted cash still in the pool after this event
  peak: boolean;              // is this the peak-loan moment?
  // payments
  stageId?: string;
  legs?: FundingLeg[];
  pct?: string;
  dueKind?: 'fixed' | 'deadline' | 'estimate';
  paid?: boolean;
  preferLoan?: boolean;
  // arrivals
  sourceId?: string;
  srcKind?: SourceKind;
}
interface Amort { months: number; interest: number; payoffDate: string; feasible: boolean; }

@Component({
  selector: 'app-house',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './house.html',
  styleUrl: './house.scss',
})
export class House implements OnInit {
  private api = inject(HouseService);
  private dash = inject(DashboardService);

  inr = DashboardService.inr;
  inrFull = DashboardService.inrFull;
  costRows = COST_ROWS;

  // One-time Supabase table that makes the plan durable across devices & deploys.
  readonly APP_CACHE_SQL = 'CREATE TABLE IF NOT EXISTS app_cache (\n  key         TEXT PRIMARY KEY,\n  value       JSONB NOT NULL,\n  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()\n);';

  plan = signal<PlanData>(structuredClone(DEFAULT_PLAN));
  view = signal<'sources' | 'timeline'>('timeline');
  saveState = signal<'idle' | 'saving' | 'saved'>('idle');
  durable = signal(true);
  notice = signal<string | null>(null);
  showCosts = signal(false);

  editStages = signal<Set<string>>(new Set());
  editSources = signal<Set<string>>(new Set());
  expanded = signal<string | null>(null);
  treeHover = signal<{ label: string; amount: number; loanFrac: number } | null>(null);

  ngOnInit() {
    this.api.getPlan().subscribe({
      next: env => {
        const p = this._valid(env.plan) ? env.plan as PlanData : this._localLoad();
        if (p) this.plan.set(this._migrate(p));
        this.durable.set(!!env.durable);
      },
      error: () => { const p = this._localLoad(); if (p) this.plan.set(this._migrate(p)); this.durable.set(false); },
    });
  }
  private _valid(p: any): boolean {
    return !!(p && p.v === PLAN_V && Array.isArray(p.sources) && Array.isArray(p.milestones));
  }
  private _migrate(p: PlanData): PlanData {
    p.sources.forEach(s => { if (s.committed === undefined) s.committed = false; });
    if (p.loanRate == null) p.loanRate = 9;
    if (p.monthlyRepay == null) p.monthlyRepay = 590_000;
    return p;
  }

  // ── basic totals ────────────────────────────────────────────────────────────
  totalCost = computed(() => this.plan().builderCost + this.plan().registration);
  totalPayments = computed(() => this.plan().milestones.reduce((s, m) => s + m.amount, 0));
  assetsTotal = computed(() => this.plan().sources.reduce((s, x) => s + x.amount, 0));
  assetsCommitted = computed(() => this.plan().sources.filter(s => s.committed).reduce((s, x) => s + x.amount, 0));
  paidToDate = computed(() => this.plan().milestones.filter(m => m.paid).reduce((s, m) => s + m.amount, 0));

  /** Capital gain on one source (proceeds − cost basis); 0 if no cost set. */
  gainOf(s: FundingSource): number { return s.cost != null && s.cost > 0 ? Math.round(s.amount - s.cost) : 0; }
  hasGain(s: FundingSource): boolean { return s.cost != null && s.cost > 0; }
  /** Total capital gains from COMMITTED sources (money coming in that is gain, not return of capital). */
  capitalGains = computed(() => this.plan().sources.filter(s => s.committed).reduce((t, s) => t + this.gainOf(s), 0));
  capitalGainsAll = computed(() => this.plan().sources.reduce((t, s) => t + this.gainOf(s), 0));
  scheduleDrift = computed(() => {
    const d = this.totalPayments() - this.totalCost();
    return Math.abs(d) <= 2 ? 0 : d;
  });

  // ── the cash-flow engine — one chronological timeline of money in & out, with
  //    the loan filling every gap by date and repaying as your sales land ──────
  analysis = computed(() => {
    const p = this.plan();
    const dk = (iso?: string, fb = 0) => {
      if (!iso) return fb;
      const [y, m, d] = iso.split('-').map(Number);
      return (y || 2000) * 10000 + (m || 1) * 100 + (d || 1);
    };
    type Ev = { t: number; kind: 'in' | 'out'; src?: FundingSource; stage?: Milestone; ord: number };
    const evs: Ev[] = [];
    p.sources.filter(s => s.committed).forEach((s, i) => evs.push({ t: dk(s.arrangeBy, 1), kind: 'in', src: s, ord: i }));
    p.milestones.forEach((m, i) => evs.push({ t: dk(m.dueDate, 20000000 + i), kind: 'out', stage: m, ord: i }));
    evs.sort((a, b) => a.t - b.t || (a.kind === b.kind ? 0 : a.kind === 'in' ? -1 : 1) || a.ord - b.ord);

    const buckets: { srcId: string; rem: number }[] = [];
    const poolSum = () => buckets.reduce((s, b) => s + b.rem, 0);
    let loan = 0, peak = 0;
    const events: TimelineEvent[] = [];

    for (const e of evs) {
      if (e.kind === 'in' && e.src) {
        buckets.push({ srcId: e.src.id, rem: e.src.amount });
        let repaid = 0;
        if (loan > 0.5) {
          let r = Math.min(loan, poolSum());
          repaid = r;
          for (const b of buckets) { if (r <= 0) break; const take = Math.min(b.rem, r); b.rem -= take; r -= take; }
          loan -= repaid;
        }
        events.push({
          kind: 'in', date: e.src.arrangeBy || '', label: e.src.name, sourceId: e.src.id, srcKind: e.src.kind,
          amount: e.src.amount, draw: 0, repay: Math.round(repaid), balance: Math.round(loan),
          cashInHand: Math.round(poolSum()), peak: false,
        });
      } else if (e.stage) {
        const m = e.stage;
        const legs: FundingLeg[] = [];
        let drawn = 0;
        if (m.preferLoan) {
          loan += m.amount; drawn = m.amount; legs.push({ sourceId: 'loan', amount: m.amount });
        } else {
          let need = m.amount;
          for (const b of buckets) {
            if (need <= 0) break; if (b.rem <= 0) continue;
            const take = Math.min(b.rem, need); b.rem -= take; need -= take;
            const ex = legs.find(l => l.sourceId === b.srcId); if (ex) ex.amount += take; else legs.push({ sourceId: b.srcId, amount: take });
          }
          if (need > 0.5) { loan += need; drawn = need; legs.push({ sourceId: 'loan', amount: need }); }
        }
        events.push({
          kind: 'out', date: m.dueDate || '', label: m.label, stageId: m.id, amount: m.amount, legs,
          draw: Math.round(drawn), repay: 0, balance: Math.round(loan), cashInHand: Math.round(poolSum()),
          pct: m.pct, dueKind: m.dueKind, paid: !!m.paid, preferLoan: !!m.preferLoan, peak: false,
        });
      }
      peak = Math.max(peak, loan);
    }
    // mark the event where the loan is at its peak
    let pk = -1, pkbal = -1;
    events.forEach((ev, i) => { if (ev.balance > pkbal) { pkbal = ev.balance; pk = i; } });
    if (pk >= 0 && pkbal > 0) events[pk].peak = true;

    const finalLoan = Math.round(loan);
    const leftoverCash = Math.round(poolSum());   // money still in hand after the last event
    const totalRepaid = events.reduce((s, e) => s + e.repay, 0);
    const lastDate = p.milestones.reduce((d, m) => (m.dueDate && m.dueDate > d ? m.dueDate : d), '2026-01-01');
    const amort = this._amort(finalLoan, p.loanRate, p.monthlyRepay, lastDate);
    return { events, peakLoan: Math.round(peak), finalLoan, leftoverCash, totalRepaid,
             assetsCommitted: this.assetsCommitted(), amort, lastDate };
  });

  /** Total loan you'll take across the whole plan (sum of every draw). */
  totalLoan = computed(() => this.analysis().events.reduce((s, e) => s + (e.draw || 0), 0));

  legsFor(id: string): FundingLeg[] {
    return this.analysis().events.find(e => e.stageId === id)?.legs || [];
  }
  mIndexById(id: string): number { return this.plan().milestones.findIndex(m => m.id === id); }
  srcStage(id: string): Milestone { return this.plan().milestones.find(m => m.id === id) || ({} as Milestone); }

  // ── payables treemap — every payment sized by ₹, split asset (blue) vs loan
  //    (amber). Colours: #387ed1 blue vs #e0892a amber = a CVD-safe pair. ──────
  private squarify(items: { value: number; item: any }[], rect: { x: number; y: number; w: number; h: number }) {
    const tiles: { x: number; y: number; w: number; h: number; item: any }[] = [];
    const total = items.reduce((s, i) => s + i.value, 0);
    if (total <= 0) return tiles;
    const area = rect.w * rect.h;
    const scaled = items.map(i => ({ item: i.item, area: (i.value / total) * area }));
    const free = { ...rect };
    let row: { item: any; area: number }[] = [];
    const worst = (r: { area: number }[], length: number) => {
      if (!r.length) return Infinity;
      const sum = r.reduce((s, x) => s + x.area, 0);
      const mx = Math.max(...r.map(x => x.area)), mn = Math.min(...r.map(x => x.area));
      return Math.max((length * length * mx) / (sum * sum), (sum * sum) / (length * length * mn));
    };
    const layout = (r: { item: any; area: number }[]) => {
      const sum = r.reduce((s, x) => s + x.area, 0);
      if (free.w >= free.h) {
        const colW = sum / free.h; let y = free.y;
        for (const c of r) { const h = c.area / colW; tiles.push({ x: free.x, y, w: colW, h, item: c.item }); y += h; }
        free.x += colW; free.w -= colW;
      } else {
        const rowH = sum / free.w; let x = free.x;
        for (const c of r) { const w = c.area / rowH; tiles.push({ x, y: free.y, w, h: rowH, item: c.item }); x += w; }
        free.y += rowH; free.h -= rowH;
      }
    };
    for (const s of scaled) {
      const length = Math.min(free.w, free.h);
      const withNew = [...row, s];
      if (row.length && worst(withNew, length) > worst(row, length)) { layout(row); row = [s]; }
      else row = withNew;
    }
    if (row.length) layout(row);
    return tiles;
  }

  payablesTreemap = computed(() => {
    const outs = this.analysis().events.filter(e => e.kind === 'out' && e.amount > 0);
    const items = outs.map(e => {
      const loanAmt = (e.legs || []).find(l => l.sourceId === 'loan')?.amount || 0;
      return { value: e.amount, item: { label: e.label, amount: e.amount, loanFrac: e.amount > 0 ? loanAmt / e.amount : 0 } };
    });
    const W = 100, H = 56;
    return this.squarify(items, { x: 0, y: 0, w: W, h: H }).map(t => ({
      left: t.x / W * 100, top: t.y / H * 100, width: t.w / W * 100, height: t.h / H * 100,
      label: t.item.label, amount: t.item.amount, loanFrac: t.item.loanFrac,
      big: (t.w / W) * (t.h / H) > 0.045,
    }));
  });
  tileBg(loanFrac: number): string {
    const p = Math.round(Math.max(0, Math.min(1, loanFrac)) * 100);
    return `linear-gradient(to top, #e0892a 0 ${p}%, #387ed1 ${p}% 100%)`;
  }

  private _amort(principal: number, ratePct: number, monthly: number, fromISO: string): Amort {
    if (principal <= 0) return { months: 0, interest: 0, payoffDate: fromISO, feasible: true };
    const r = ratePct / 100 / 12;
    if (monthly <= principal * r) return { months: 0, interest: 0, payoffDate: '', feasible: false };
    let bal = principal, months = 0, interest = 0;
    while (bal > 0 && months < 600) { const i = bal * r; interest += i; bal += i - monthly; months++; }
    return { months, interest: Math.round(interest), payoffDate: this._addMonths(fromISO, months), feasible: true };
  }
  private _addMonths(iso: string, n: number): string {
    const [y, m] = (iso || '2026-01-01').split('-').map(Number);
    const total = (y * 12 + (m - 1)) + n;
    return `${Math.floor(total / 12)}-${String((total % 12) + 1).padStart(2, '0')}-15`;
  }

  // ── display helpers ──────────────────────────────────────────────────────────
  private static MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  fmtDate(iso?: string | null): string {
    if (!iso) return '';
    const [y, m, d] = iso.split('-').map(Number);
    if (!y || !m) return iso;
    return `${d || ''} ${House.MONTHS[m - 1]} ${y}`.trim();
  }
  fmtMonth(iso?: string | null): string {
    if (!iso) return '';
    const [y, m] = iso.split('-').map(Number);
    return `${House.MONTHS[(m || 1) - 1]} ${y}`;
  }
  dueLabel(m: Milestone): string {
    const d = this.fmtDate(m.dueDate);
    if (!d) return '—';
    return m.dueKind === 'estimate' ? '≈ ' + d : m.dueKind === 'deadline' ? 'By ' + d : d;
  }
  srcById(id: string): FundingSource | undefined { return this.plan().sources.find(s => s.id === id); }
  srcName(id: string): string { return id === 'loan' ? 'Loan' : (this.srcById(id)?.name.replace('Sell: ', '') || id); }
  kindColor(kind: SourceKind | undefined): string {
    const map: Record<SourceKind, string> = {
      cash: '#16a34a', maturity: '#0ea5e9', 'sale-done': '#8b5cf6', sale: '#387ed1', income: '#14b8a6', other: '#6b7190',
    };
    return kind ? map[kind] : '#6b7190';
  }
  legColor(sourceId: string): string { return sourceId === 'loan' ? '#f59e0b' : this.kindColor(this.srcById(sourceId)?.kind); }

  // ── per-card edit toggles ────────────────────────────────────────────────────
  editingStage(id: string) { return this.editStages().has(id); }
  toggleEditStage(id: string) { const s = new Set(this.editStages()); s.has(id) ? s.delete(id) : s.add(id); this.editStages.set(s); }
  editingSource(id: string) { return this.editSources().has(id); }
  toggleEditSource(id: string) { const s = new Set(this.editSources()); s.has(id) ? s.delete(id) : s.add(id); this.editSources.set(s); }
  toggleExpand(id: string) { this.expanded.set(this.expanded() === id ? null : id); }

  // ── mutations (each schedules an autosave) ───────────────────────────────────
  private mut(fn: (p: PlanData) => void) {
    const p = structuredClone(this.plan());
    fn(p); p.v = PLAN_V;
    this.plan.set(p);
    this._scheduleSave();
  }
  setPlan<K extends keyof PlanData>(k: K, v: PlanData[K]) { this.mut(p => { (p as any)[k] = v; }); }
  setStage(i: number, k: keyof Milestone, v: any) { this.mut(p => { (p.milestones[i] as any)[k] = v; }); }
  setSource(i: number, k: keyof FundingSource, v: any) { this.mut(p => { (p.sources[i] as any)[k] = v; }); }

  toggleCommit(i: number) { this.mut(p => { p.sources[i].committed = !p.sources[i].committed; }); }
  commitAll(on: boolean) { this.mut(p => p.sources.forEach(s => s.committed = on)); }
  togglePreferLoan(i: number) { this.mut(p => { p.milestones[i].preferLoan = !p.milestones[i].preferLoan; }); }
  togglePaid(i: number) { this.mut(p => { p.milestones[i].paid = !p.milestones[i].paid; }); }

  addStage() {
    this.mut(p => p.milestones.push({ id: 'ms' + Date.now().toString(36), label: 'New payment', pct: '—', dueKind: 'estimate', amount: 0 }));
  }
  removeStage(i: number) {
    if (!confirm(`Delete "${this.plan().milestones[i].label}"?`)) return;
    const id = this.plan().milestones[i].id;
    this.mut(p => p.milestones.splice(i, 1));
    const s = new Set(this.editStages()); s.delete(id); this.editStages.set(s);
  }
  moveStage(i: number, dir: -1 | 1) {
    const j = i + dir; if (j < 0 || j >= this.plan().milestones.length) return;
    this.mut(p => { const [m] = p.milestones.splice(i, 1); p.milestones.splice(j, 0, m); });
  }

  addBlankSource() {
    const id = 'src' + Date.now().toString(36);
    this.mut(p => p.sources.push({ id, name: 'New source', amount: 0, kind: 'cash', committed: true, eta: '' }));
    const s = new Set(this.editSources()); s.add(id); this.editSources.set(s);
  }
  removeSource(i: number) {
    if (!confirm(`Remove "${this.plan().sources[i].name}"?`)) return;
    this.mut(p => p.sources.splice(i, 1));
  }

  // ── asset picker ─────────────────────────────────────────────────────────────
  showPicker = signal(false);
  pickerQuery = signal('');
  pickerClass = signal('');                      // '' = all classes
  assets = signal<Position[]>([]);
  assetsLoading = signal(false);
  abs = (n: number) => Math.abs(n);

  openPicker() {
    this.showPicker.set(true); this.pickerQuery.set(''); this.pickerClass.set('');
    if (!this.assets().length && !this.assetsLoading()) {
      this.assetsLoading.set(true);
      this.dash.summary().subscribe({
        next: d => { this.assets.set(d.positions || []); this.assetsLoading.set(false); },
        error: () => { this.assetsLoading.set(false); this._toast('Could not load your assets.'); },
      });
    }
  }
  /** Asset classes present, with counts — powers the filter chips. */
  assetClasses = computed(() => {
    const m = new Map<string, { key: string; label: string; count: number }>();
    for (const a of this.assets()) {
      if ((a.value || 0) <= 0) continue;
      const e = m.get(a.asset_class) || { key: a.asset_class, label: a.class_label || a.asset_class, count: 0 };
      e.count++; m.set(a.asset_class, e);
    }
    return [...m.values()].sort((x, y) => y.count - x.count);
  });
  pickerCount = computed(() => this.assets().filter(a => (a.value || 0) > 0).length);
  filteredAssets = computed(() => {
    const q = this.pickerQuery().trim().toLowerCase();
    const cls = this.pickerClass();
    return this.assets().filter(a => (a.value || 0) > 0)
      .filter(a => !cls || a.asset_class === cls)
      .filter(a => !q || `${a.name} ${a.owner} ${a.class_label}`.toLowerCase().includes(q))
      .sort((a, b) => (b.value || 0) - (a.value || 0));
  });
  /** Capital gain on an asset in the picker (realisable − invested), null if unknown. */
  assetGain(a: Position): number | null {
    if (a.invested == null || a.invested <= 0) return null;
    return Math.round((a.realisable || a.value || 0) - a.invested);
  }
  private _assetName(a: Position) {
    const sellable = ['apartments', 'land', 'built', 'stocks', 'gold', 'bonds'].includes(a.asset_class);
    return (sellable ? 'Sell: ' : '') + a.name;
  }
  assetAdded(a: Position) { const k = this._assetName(a).toLowerCase(); return this.plan().sources.some(s => s.name.toLowerCase() === k); }
  private _assetKind(a: Position): SourceKind {
    if (a.asset_class === 'cash' || a.asset_class === 'fd') return 'cash';
    if (a.asset_class === 'ulip' || a.asset_class === 'lic') return 'maturity';
    if (['apartments', 'land', 'built', 'stocks', 'gold', 'bonds'].includes(a.asset_class)) return 'sale';
    return 'other';
  }
  addAsset(a: Position) {
    if (this.assetAdded(a)) return;
    const sellable = ['apartments', 'land', 'built', 'stocks', 'gold', 'bonds'].includes(a.asset_class);
    this.mut(p => p.sources.push({
      id: 'asset' + Date.now().toString(36) + Math.floor(a.value),
      name: this._assetName(a), amount: Math.round(a.realisable || a.value || 0),
      cost: sellable && a.invested != null ? Math.round(a.invested) : undefined,   // cost basis → capital gain
      kind: this._assetKind(a), committed: true, owner: a.owner || '',
      eta: a.liquidity_label || '', note: `${a.class_label}${a.monthly_income ? ' · earns ' + DashboardService.inr(a.monthly_income) + '/mo' : ''}`,
    }));
  }
  classColor(ac: string): string {
    const map: Record<string, string> = {
      apartments: '#387ed1', land: '#2bb673', built: '#9b6dd6', stocks: '#f0883e',
      gold: '#e8b730', bonds: '#14b8a6', fd: '#00a3c4', ulip: '#0ea5e9', lic: '#e0598b', cash: '#16a34a',
    };
    return map[ac] || '#6b7190';
  }

  // ── persistence (debounced autosave to Supabase KV) ──────────────────────────
  private _saveTimer: any = null;
  private _scheduleSave() {
    this._localSave(this.plan());
    this.saveState.set('saving');
    if (this._saveTimer) clearTimeout(this._saveTimer);
    this._saveTimer = setTimeout(() => this._save(), 900);
  }
  private _save() {
    this.api.savePlan(this.plan()).subscribe({
      next: r => { this.durable.set(!!r.durable); this.saveState.set('saved'); setTimeout(() => this.saveState.set('idle'), 1600); },
      error: () => { this.saveState.set('idle'); },
    });
  }
  copySql() {
    try { navigator.clipboard?.writeText(this.APP_CACHE_SQL); this._toast('SQL copied — paste into Supabase → SQL Editor → Run.'); }
    catch { this._toast('Select the SQL and copy it manually.'); }
  }
  recheckDurable() {
    this.api.getPlan().subscribe({
      next: env => {
        this.durable.set(!!env.durable);
        this._toast(env.durable ? '✓ Connected — the plan is now stored in Supabase.' : 'Table not found yet — run the SQL, then retry.');
      },
      error: () => this._toast('Could not reach the server.'),
    });
  }

  resetPlan() {
    if (!confirm('Reset the whole plan to the researched baseline? Your changes are lost.')) return;
    this.plan.set(structuredClone(DEFAULT_PLAN));
    this.editStages.set(new Set()); this.editSources.set(new Set());
    this._scheduleSave();
    this._toast('Reset to baseline.');
  }
  private _toast(m: string) { this.notice.set(m); setTimeout(() => this.notice.set(null), 4000); }
  private _localSave(p: PlanData) { try { localStorage.setItem('house_plan', JSON.stringify(p)); } catch {} }
  private _localLoad(): PlanData | null {
    try { const p = JSON.parse(localStorage.getItem('house_plan') || 'null'); return this._valid(p) ? p : null; } catch { return null; }
  }
}
