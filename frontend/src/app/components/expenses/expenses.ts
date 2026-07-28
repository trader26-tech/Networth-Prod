import { Component, OnInit, inject, signal, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpErrorResponse } from '@angular/common/http';
import {
  ExpensesService, MonthSummary, MonthEntry, EntryInput, Region, Frequency,
  Overview, IncomeEntry, InboxList,
} from '../../services/expenses.service';
import { ExpenseInbox } from './inbox/expense-inbox';
import {
  ColumnBreakdown, SankeyFlow, BarItem, SankeyNode, SankeyLink,
  PERSON_COLORS, personColor, categoryColor, OUT_COLOR, IN_COLOR,
} from './charts/expense-charts';

type Draft = {
  region: Region; name: string; amount: number | null; category: string;
  recurring: boolean; owner: string; essential: boolean;
  is_subscription: boolean; payment_method: string; note: string; on_date: string; end_date: string;
};

function blank(region: Region): Draft {
  return {
    region, name: '', amount: null, category: '', recurring: false,
    owner: '', essential: true, is_subscription: false, payment_method: '', note: '', on_date: '', end_date: '',
  };
}

const FALLBACK_CATEGORIES = [
  'Housing / Rent', 'Groceries', 'Utilities', 'Internet & Phone', 'Transport & Fuel',
  'EMI / Loan', 'Insurance', 'Education', 'Healthcare', 'Subscriptions', 'Dining out',
  'Shopping', 'Domestic help', 'Entertainment', 'Travel', 'Personal care', 'Donations', 'Miscellaneous',
];

/** Which half of the page is showing: the finalized list, or the add / review desk. */
type Section = 'expenses' | 'update';
/** A chart-driven filter: what the user clicked to narrow the list. */
type ChartFilter =
  | { kind: 'person'; value: string }
  | { kind: 'spendCat'; value: string }
  | { kind: 'incomeCat'; value: string }
  | null;

@Component({
  selector: 'app-expenses',
  standalone: true,
  imports: [CommonModule, FormsModule, ExpenseInbox, ColumnBreakdown, SankeyFlow],
  templateUrl: './expenses.html',
  styleUrl: './expenses.scss',
})
export class Expenses implements OnInit {
  private api = inject(ExpensesService);

  data = signal<MonthSummary | null>(null);
  overview = signal<Overview | null>(null);
  inboxInfo = signal<InboxList | null>(null);
  categories = signal<string[]>(FALLBACK_CATEGORIES);
  incomeCategories = signal<string[]>([]);
  methodList = signal<string[]>([]);
  loading = signal(true);
  error = signal<string | null>(null);
  needsMigration = signal(false);
  endDateReady = signal(true);

  viewMonth = signal<string>(this.curMonth());

  // ── which half of the page + how the list is filtered ───────────────────────
  section = signal<Section>('expenses');
  search = signal('');
  chartFilter = signal<ChartFilter>(null);

  // extra filters (the popover) — all AND-combined with search + chart pick
  filtersOpen = signal(false);
  fRegion = signal<'both' | 'india' | 'kuwait'>('both');
  fRecurring = signal(false);
  fCategory = signal('');
  fOwner = signal('');
  fMin = signal<number | null>(null);
  fFrom = signal('');
  fTo = signal('');

  // manual quick-add (Update view)
  quick = signal<Draft>(blank('india'));
  quickRegion = signal<Region>('india');
  showMore = signal(false);
  adding = signal(false);

  // inline edit of a finalized row (Expenses view)
  editingId = signal<string | null>(null);
  editDraft = signal<Draft | null>(null);
  saving = signal(false);

  inr = ExpensesService.inr;
  inrFull = ExpensesService.inrFull;
  money = ExpensesService.money;

  readonly OWNERS = ['Ranjeev', 'Sanjeev', 'Ramprasad', 'Maha'];
  readonly OUT = OUT_COLOR;
  readonly IN = IN_COLOR;
  private readonly HUB = '#387ed1';
  private readonly PERSON_COLORS = PERSON_COLORS;

  // ── month helpers ──────────────────────────────────────────────────────────
  private curMonth(): string {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
  }
  monthLabel = computed(() => {
    const [y, m] = this.viewMonth().split('-').map(Number);
    return new Date(y, m - 1, 1).toLocaleDateString('en-IN', { month: 'long', year: 'numeric' });
  });
  isCurrentMonth = computed(() => this.viewMonth() === this.curMonth());
  shiftMonth(delta: number) {
    const [y, m] = this.viewMonth().split('-').map(Number);
    const d = new Date(y, m - 1 + delta, 1);
    this.viewMonth.set(`${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`);
    this.primeQuickDate();
    this.load();
  }
  goToday() { this.viewMonth.set(this.curMonth()); this.primeQuickDate(); this.load(); }

  // ── derived data ─────────────────────────────────────────────────────────────
  entries = computed(() => this.data()?.entries || []);
  incomeEntries = computed<IncomeEntry[]>(() => this.overview()?.income.entries || []);
  total = computed(() => this.data()?.total_inr || 0);
  incomeInr = computed(() => this.overview()?.income.total_inr ?? 0);
  indiaInr = computed(() => this.data()?.india_inr || 0);
  kuwaitInr = computed(() => this.data()?.kuwait_inr || 0);
  recurringInr = computed(() => this.data()?.recurring_inr || 0);
  onetimeInr = computed(() => this.data()?.onetime_inr || 0);
  inboxPending = computed(() => this.inboxInfo()?.pending_rows ?? 0);
  pendingPushes = computed(() => (this.inboxInfo()?.batches || []).filter(b => b.status === 'pending').length);

  // ── money in hand, split India vs Kuwait — the number the user cares about ────
  /** A cash account is Kuwait's if it's held in KWD or the place/label says Kuwait. */
  private isKuwaitCash(a: { currency?: string; where?: string | null; label?: string | null }): boolean {
    if ((a.currency || '').toUpperCase() === 'KWD') return true;
    const s = ((a.where || '') + ' ' + (a.label || '')).toLowerCase();
    return s.includes('kuwait') || s.includes('kwd');
  }
  inHandTotal = computed(() => this.overview()?.in_hand.total_inr ?? 0);
  inHandKuwait = computed(() =>
    (this.overview()?.in_hand.entries || []).filter(a => this.isKuwaitCash(a))
      .reduce((s, a) => s + (a.balance_inr || 0), 0));
  inHandIndia = computed(() => Math.max(0, this.inHandTotal() - this.inHandKuwait()));
  inHandAccounts = computed(() => this.overview()?.in_hand.accounts ?? 0);

  // ── income = spent + left over: the month's money as one part-to-whole bar ────
  /** Left over = what came in minus what went out this month (never below 0 for
   *  the bar; a negative would mean you outspent your income — flagged separately). */
  leftOver = computed(() => Math.max(0, this.incomeInr() - this.total()));
  /** True when spending exceeded income this month — the bar caps at 100% spent
   *  and we say so, rather than drawing a nonsensical negative segment. */
  overspent = computed(() => this.total() > this.incomeInr() + 0.5);
  /** the two segments of the income bar, as % of income (spend first) */
  spendPct = computed(() => {
    const inc = this.incomeInr();
    if (inc <= 0) return this.total() > 0 ? 100 : 0;
    return Math.min(100, (this.total() / inc) * 100);
  });
  leftPct = computed(() => Math.max(0, 100 - this.spendPct()));
  /** share of income saved, for the headline (e.g. "22% saved") */
  savedPct = computed(() => {
    const inc = this.incomeInr();
    return inc > 0 ? Math.round((this.leftOver() / inc) * 100) : 0;
  });

  personColor(name: string): string { return personColor(name); }
  personInitial(name: string): string { return (name || '?').charAt(0); }
  catColor(cat: string | null): string { return categoryColor(cat || 'Uncategorised', this.categories()); }

  // ── the person bar chart: one bar per member, total ₹ spend, click to filter ──
  personBars = computed<BarItem[]>(() => {
    const agg = new Map<string, { inr: number; count: number }>();
    for (const e of this.entries()) {
      const name = e.owner && e.owner !== '—' ? e.owner : 'Unassigned';
      const cur = agg.get(name) || { inr: 0, count: 0 };
      cur.inr += e.month_inr || 0; cur.count += 1;
      agg.set(name, cur);
    }
    return [...agg.entries()]
      .map(([person, a]) => ({ key: person, label: person, value: a.inr, count: a.count, color: personColor(person) }))
      .filter(b => b.value > 0)
      .sort((x, y) => y.value - x.value);
  });
  hasPersonBars = computed(() => this.personBars().length > 0);

  // ── the money-flow Sankey: income categories → pool → spend categories ───────
  private foldTop(items: BarItem[], n: number): BarItem[] {
    const s = [...items].filter(i => i.value > 0).sort((a, b) => b.value - a.value);
    if (s.length <= n) return s;
    const tail = s.slice(n - 1);
    return [...s.slice(0, n - 1), { key: '__other__', label: `Other (${tail.length})`,
      value: tail.reduce((t, x) => t + x.value, 0), count: 0 }];
  }

  /** The flow keeps the FULL-family shape — a person pick highlights their share,
   *  it never reshapes the diagram. Search + region/date/etc. filters DO reshape it
   *  (except an active spend-/income-category pick, which would collapse it to one). */
  private flowSpendByCat = computed<BarItem[]>(() => {
    const agg = new Map<string, number>();
    for (const e of this.entriesFor('spendCat', /*ignorePerson*/ true)) {
      const k = (e.category || 'Uncategorised').trim() || 'Uncategorised';
      agg.set(k, (agg.get(k) || 0) + (e.month_inr || 0));
    }
    return [...agg.entries()].map(([k, v]) => ({ key: k, label: k, value: v }));
  });
  private flowIncomeByCat = computed<BarItem[]>(() => {
    const agg = new Map<string, number>();
    for (const e of this.incomeFor('incomeCat')) {
      const k = (e.category || 'Uncategorised').trim() || 'Uncategorised';
      agg.set(k, (agg.get(k) || 0) + (e.amount_inr || 0));
    }
    return [...agg.entries()].map(([k, v]) => ({ key: k, label: k, value: v }));
  });

  /** The exact spend-category order the Sankey shows (folded to top-6 + Other), so a
   *  person's highlight band aligns 1:1 with the ribbons — including the Other bucket. */
  private flowOutFolded = computed(() => this.foldTop(this.flowSpendByCat(), 6));

  /** One highlight per out-category link = the selected person's share of it. Built
   *  so it maps onto the SAME folded categories the flow drew, Other included. */
  personHighlight = computed<{ key: string; frac: number; color: string }[]>(() => {
    const f = this.chartFilter();
    if (f?.kind !== 'person') return [];
    const person = f.value;
    const folded = this.flowOutFolded();
    const shown = new Set(folded.map(o => o.key).filter(k => k !== '__other__'));
    // per-category totals and this person's part, from the same filtered set the flow used
    const catTotal = new Map<string, number>(), catMine = new Map<string, number>();
    for (const e of this.entriesFor('spendCat', true)) {
      const k = (e.category || 'Uncategorised').trim() || 'Uncategorised';
      const bucket = shown.has(k) ? k : '__other__';
      const amt = e.month_inr || 0;
      catTotal.set(bucket, (catTotal.get(bucket) || 0) + amt);
      const owner = e.owner && e.owner !== '—' ? e.owner : 'Unassigned';
      if (owner === person) catMine.set(bucket, (catMine.get(bucket) || 0) + amt);
    }
    const color = this.personColor(person);
    return folded.map(o => {
      const tot = catTotal.get(o.key) || 0, mine = catMine.get(o.key) || 0;
      return { key: 'out→cat:' + o.key, frac: tot > 0 ? mine / tot : 0, color };
    }).filter(h => h.frac > 0);
  });

  monthFlow = computed<{ nodes: SankeyNode[]; links: SankeyLink[]; height: number }>(() => {
    const ins = this.foldTop(this.flowIncomeByCat(), 6);
    const outs = this.flowOutFolded();
    const inTotal = ins.reduce((t, x) => t + x.value, 0);
    const outTotal = outs.reduce((t, x) => t + x.value, 0);
    if (inTotal <= 0 && outTotal <= 0) return { nodes: [], links: [], height: 240 };

    const nodes: SankeyNode[] = [];
    const links: SankeyLink[] = [];
    const fromBalance = Math.max(0, outTotal - inTotal);
    const kept = Math.max(0, inTotal - outTotal);

    for (const i of ins) {
      nodes.push({ id: 'in:' + i.key, label: i.label, value: i.value, col: 0, color: IN_COLOR, kind: 'in',
                   clickable: i.key !== '__other__' });
      links.push({ from: 'in:' + i.key, to: 'pool', value: i.value });
    }
    if (fromBalance > 0) {
      nodes.push({ id: 'in:__balance__', label: 'From your balance', value: fromBalance, col: 0, color: '#9aa0b5', kind: 'in' });
      links.push({ from: 'in:__balance__', to: 'pool', value: fromBalance });
    }
    nodes.push({ id: 'pool', label: 'Money in', value: inTotal + fromBalance, col: 1, color: this.HUB, kind: 'hub' });
    if (outTotal > 0) {
      nodes.push({ id: 'out', label: 'Money out', value: outTotal, col: 2, color: OUT_COLOR, kind: 'out' });
      links.push({ from: 'pool', to: 'out', value: outTotal });
    }
    if (kept > 0) {
      nodes.push({ id: 'kept', label: 'Left over', value: kept, col: 2, color: IN_COLOR, kind: 'kept' });
      links.push({ from: 'pool', to: 'kept', value: kept });
    }
    for (const o of outs) {
      nodes.push({ id: 'cat:' + o.key, label: o.label, value: o.value, col: 3, color: OUT_COLOR, kind: 'out',
                   clickable: o.key !== '__other__' });
      links.push({ from: 'out', to: 'cat:' + o.key, value: o.value });
    }
    const rows = Math.max(ins.length + (fromBalance > 0 ? 1 : 0), outs.length, 2);
    return { nodes, links, height: Math.min(560, Math.max(260, 30 + rows * 48)) };
  });
  hasMonthFlow = computed(() => this.monthFlow().nodes.length > 0);

  /** the Sankey node id that the current chart-filter corresponds to (for highlight) */
  flowSelected = computed<string | null>(() => {
    const f = this.chartFilter();
    if (f?.kind === 'spendCat') return 'cat:' + f.value;
    if (f?.kind === 'incomeCat') return 'in:' + f.value;
    return null;
  });
  personSelected = computed<string | null>(() => {
    const f = this.chartFilter();
    return f?.kind === 'person' ? f.value : null;
  });

  // ── clicking the charts sets the chart-filter (combines AND with the rest) ────
  pickFlowNode(id: string) {
    if (id.startsWith('cat:')) {
      const v = id.slice(4);
      this.chartFilter.update(f => f?.kind === 'spendCat' && f.value === v ? null : { kind: 'spendCat', value: v });
    } else if (id.startsWith('in:') && id !== 'in:__balance__') {
      const v = id.slice(3);
      this.chartFilter.update(f => f?.kind === 'incomeCat' && f.value === v ? null : { kind: 'incomeCat', value: v });
    }
  }
  pickPerson(key: string | null) {
    if (!key) { this.chartFilter.set(null); return; }
    this.chartFilter.update(f => f?.kind === 'person' && f.value === key ? null : { kind: 'person', value: key });
  }
  clearChartFilter() { this.chartFilter.set(null); }

  /** Are we looking at income rows? Only when a money-in node is the active chart pick. */
  showingIncome = computed(() => this.chartFilter()?.kind === 'incomeCat');

  // ── the ONE filter predicate, shared by the list (search + chart + popover) ──
  private matchSearch(hay: string): boolean {
    const q = this.search().trim().toLowerCase();
    return !q || hay.toLowerCase().includes(q);
  }
  private passesExtra(region: Region, category: string | null, owner: string | null,
                      amtInr: number, onDate: string | null, recurring: boolean): boolean {
    if (this.fRegion() !== 'both' && region !== this.fRegion()) return false;
    if (this.fRecurring() && !recurring) return false;
    if (this.fCategory() && (category || 'Uncategorised') !== this.fCategory()) return false;
    if (this.fOwner() && (owner || 'Unassigned') !== this.fOwner()) return false;
    if (this.fMin() != null && amtInr < this.fMin()!) return false;
    if (this.fFrom() && (onDate || '') < this.fFrom()) return false;
    if (this.fTo() && (onDate || '') > this.fTo()) return false;
    return true;
  }

  /** SPEND rows after search + popover + chart-pick, but able to skip one chart
   *  dimension so a chart can show every value instead of collapsing to the pick.
   *  `skip: 'spendCat'` = ignore an active spend-category pick (person still applies). */
  private entriesFor(skip?: 'spendCat', ignorePerson = false): MonthEntry[] {
    const f = this.chartFilter();
    return this.entries().filter(e => {
      const owner = e.owner && e.owner !== '—' ? e.owner : 'Unassigned';
      if (!ignorePerson && f?.kind === 'person' && owner !== f.value) return false;
      if (f?.kind === 'spendCat' && skip !== 'spendCat' && (e.category || 'Uncategorised') !== f.value) return false;
      if (!this.matchSearch([e.name, e.category, e.owner, e.note].filter(Boolean).join(' '))) return false;
      return this.passesExtra(e.region, e.category, owner, e.month_inr || 0, e.on_date, e.recurring);
    });
  }
  private incomeFor(skip?: 'incomeCat'): IncomeEntry[] {
    const f = this.chartFilter();
    return this.incomeEntries().filter(e => {
      if (f?.kind === 'incomeCat' && skip !== 'incomeCat' && (e.category || 'Uncategorised') !== f.value) return false;
      if (!this.matchSearch([e.source, e.category, e.owner, e.note].filter(Boolean).join(' '))) return false;
      return true;
    });
  }

  /** finalized SPEND rows, after search + chart-pick + popover (all AND) */
  filteredEntries = computed<MonthEntry[]>(() =>
    this.entriesFor().sort((a, b) => (b.month_inr || 0) - (a.month_inr || 0)));

  /** finalized INCOME rows — only ever shown when a money-in node is picked */
  filteredIncome = computed<IncomeEntry[]>(() =>
    this.incomeFor().sort((a, b) => (b.amount_inr || 0) - (a.amount_inr || 0)));

  filteredTotal = computed(() => this.filteredEntries().reduce((s, e) => s + (e.month_inr || 0), 0));
  filteredCount = computed(() => this.filteredEntries().length);
  incomeTotal = computed(() => this.filteredIncome().reduce((s, e) => s + (e.amount_inr || 0), 0));

  /** a short human label for whatever chart-pick is active, for the clear chip */
  chartFilterLabel = computed(() => {
    const f = this.chartFilter();
    if (!f) return '';
    if (f.kind === 'person') return f.value;
    return f.value;
  });

  filtersActive = computed(() => this.fRegion() !== 'both' || this.fRecurring() || !!this.fCategory()
    || !!this.fOwner() || this.fMin() != null || !!this.fFrom() || !!this.fTo());
  filterCount = computed(() => [this.fRegion() !== 'both', this.fRecurring(), !!this.fCategory(),
    !!this.fOwner(), this.fMin() != null, !!this.fFrom() || !!this.fTo()].filter(Boolean).length);
  clearFilters() {
    this.fRegion.set('both'); this.fRecurring.set(false); this.fCategory.set('');
    this.fOwner.set(''); this.fMin.set(null); this.fFrom.set(''); this.fTo.set('');
  }
  anyFilterOn = computed(() => !!this.search() || !!this.chartFilter() || this.filtersActive());
  clearAll() { this.search.set(''); this.chartFilter.set(null); this.clearFilters(); }

  /** category options for the popover — the whole list plus anything spent on this month */
  catOptions = computed(() => {
    const set = new Set(this.categories());
    for (const e of this.entries()) if (e.category) set.add(e.category);
    return [...set].sort();
  });

  // ── section toggle ───────────────────────────────────────────────────────────
  setSection(s: Section) { this.section.set(s); if (s === 'update') this.primeQuickDate(); }

  ngOnInit() {
    this.primeQuickDate();
    this.api.meta().subscribe({
      next: m => {
        if (m?.categories?.length) this.categories.set(m.categories);
        if (m?.income_categories?.length) this.incomeCategories.set(m.income_categories);
        if (m?.end_date_ready === false) this.endDateReady.set(false);
      },
      error: () => {},
    });
    this.api.methods().subscribe({ next: r => this.methodList.set(r.methods || []), error: () => {} });
    this.load();
  }

  load() {
    this.loading.set(true); this.error.set(null); this.needsMigration.set(false);
    this.api.overview(this.viewMonth()).subscribe({ next: o => this.overview.set(o), error: () => {} });
    this.api.inbox().subscribe({ next: i => this.inboxInfo.set(i), error: () => {} });
    this.api.month(this.viewMonth()).subscribe({
      next: s => {
        this.data.set(s); this.loading.set(false);
        if (s.end_date_ready === false) this.endDateReady.set(false);
      },
      error: (e: HttpErrorResponse) => {
        this.loading.set(false);
        if (e.status === 503) this.needsMigration.set(true);
        else this.error.set('Could not reach the API. Is the backend running?');
      },
    });
  }

  // ── each expense carries the real date it happened (persisted as on_date) ────
  private todayIso(): string {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  }
  defaultOnDate(): string {
    return this.isCurrentMonth() ? this.todayIso() : `${this.viewMonth()}-15`;
  }
  private primeQuickDate() { this.quick.update(q => ({ ...q, on_date: this.defaultOnDate() })); }

  setQuickRegion(r: Region) { this.quickRegion.set(r); this.quick.update(q => ({ ...q, region: r })); }
  curLabel(r: Region): string { return r === 'india' ? '₹' : 'KWD'; }

  // ── quick add (Update view) ───────────────────────────────────────────────────
  private toInput(d: Draft): EntryInput {
    return {
      region: d.region, name: d.name.trim(), category: d.category.trim() || null,
      amount: d.amount, recurring: d.recurring, frequency: d.recurring ? 'monthly' : 'one_time',
      owner: d.owner.trim() || null, essential: d.essential, is_subscription: d.is_subscription,
      payment_method: d.payment_method.trim() || null, note: d.note.trim() || null,
      on_date: d.on_date || null, end_date: d.recurring ? (d.end_date || null) : null,
    };
  }
  canAdd(): boolean {
    const q = this.quick();
    return !!q.name.trim() && q.amount != null && Number(q.amount) > 0;
  }
  submitQuick() {
    const d = this.quick();
    if (!d.name.trim() || d.amount == null || Number(d.amount) <= 0) return;
    const input = this.toInput(d);
    if (!input.recurring && !input.on_date && !this.isCurrentMonth()) input.on_date = `${this.viewMonth()}-15`;
    if (input.recurring && !input.on_date) input.on_date = `${this.viewMonth()}-01`;
    this.adding.set(true);
    this.api.addEntry(input).subscribe({
      next: () => {
        this.adding.set(false);
        this.quick.set({ ...blank(d.region), on_date: d.on_date || this.defaultOnDate() });
        this.showMore.set(false);
        this.load();
      },
      error: (e: HttpErrorResponse) => { this.adding.set(false); this.error.set(e.error?.detail || 'Could not save.'); },
    });
  }

  // ── inline edit (Expenses view) ───────────────────────────────────────────────
  startEdit(e: MonthEntry) {
    if (this.editingId() === e.id) { this.cancelEdit(); return; }
    this.editingId.set(e.id);
    this.editDraft.set({
      region: e.region, name: e.name ?? '', amount: e.amount, category: e.category ?? '',
      recurring: e.recurring,
      owner: e.owner ?? '', essential: e.essential !== false, is_subscription: !!e.is_subscription,
      payment_method: e.payment_method ?? '', note: e.note ?? '', on_date: e.on_date ?? '', end_date: e.end_date ?? '',
    });
  }
  cancelEdit() { this.editingId.set(null); this.editDraft.set(null); }
  saveEdit(e: MonthEntry) {
    const d = this.editDraft(); if (!d || !d.name.trim()) return;
    this.saving.set(true);
    this.api.updateEntry(e.id, this.toInput(d)).subscribe({
      next: () => { this.saving.set(false); this.cancelEdit(); this.load(); },
      error: () => { this.saving.set(false); this.error.set('Could not save changes.'); },
    });
  }
  stop(e: MonthEntry) {
    if (!confirm(`Stop "${e.name}" from ${this.monthLabel()} onward? Earlier months keep it.`)) return;
    this.api.stopEntry(e.id, this.viewMonth()).subscribe({
      next: (r) => { if (r && r.persisted === false) this.endDateReady.set(false); this.load(); },
      error: () => { this.error.set('Could not stop it.'); },
    });
  }

  // ── delete (with undo) ────────────────────────────────────────────────────────
  pendingDelete = signal<{ item: MonthEntry } | null>(null);
  private deleteTimer: any = null;
  remove(e: MonthEntry) {
    this.finalizeDelete();
    if (this.editingId() === e.id) this.cancelEdit();
    this.data.update(s => s ? { ...s, entries: s.entries.filter(x => x.id !== e.id) } : s);
    this.pendingDelete.set({ item: e });
    this.deleteTimer = setTimeout(() => this.finalizeDelete(), 6000);
  }
  undoDelete() { const p = this.pendingDelete(); if (!p) return; clearTimeout(this.deleteTimer); this.deleteTimer = null; this.pendingDelete.set(null); this.load(); }
  finalizeDelete() {
    const p = this.pendingDelete(); if (!p) return;
    clearTimeout(this.deleteTimer); this.deleteTimer = null; this.pendingDelete.set(null);
    this.api.deleteEntry(p.item.id).subscribe({ next: () => this.load(), error: () => { this.error.set('Could not delete.'); this.load(); } });
  }

  // ── income row delete (when the money-in view is showing) ─────────────────────
  removeIncome(e: IncomeEntry) {
    if (!confirm(`Delete “${e.source}” (${this.inrFull(e.amount_inr)})?`)) return;
    this.api.deleteIncome(e.id).subscribe({ next: () => this.load(), error: () => this.error.set('Could not delete that income.') });
  }

  // ── the embedded review desk fires these when Claude's pushes are approved ────
  onInboxImported() { this.load(); }
  onInboxClosed() { this.load(); }

  // ── misc formatting ────────────────────────────────────────────────────────────
  fmtDay(iso: string | null): string {
    if (!iso) return '—';
    const d = new Date(iso + 'T00:00:00');
    return isNaN(d.getTime()) ? '' : d.toLocaleDateString('en-IN', { day: 'numeric', month: 'short' });
  }
  fmtUntil(iso: string | null | undefined): string {
    if (!iso) return '';
    const d = new Date(iso.length <= 10 ? iso + 'T00:00:00' : iso);
    return isNaN(d.getTime()) ? '' : d.toLocaleDateString('en-IN', { month: 'short', year: 'numeric' });
  }
  fmtAdded(ts: string | null | undefined): string {
    if (!ts) return '';
    const d = new Date(ts.length <= 10 ? ts + 'T00:00:00' : ts);
    return isNaN(d.getTime()) ? '' : d.toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: '2-digit' });
  }
  recurDay(iso: string | null): string {
    const dd = (iso || '').length >= 10 ? +iso!.slice(8, 10) : NaN;
    if (!dd || isNaN(dd)) return '';
    const s = dd % 10, t = dd % 100;
    const suf = (s === 1 && t !== 11) ? 'st' : (s === 2 && t !== 12) ? 'nd' : (s === 3 && t !== 13) ? 'rd' : 'th';
    return `${dd}${suf}`;
  }
  migrationSql = 'ALTER TABLE expenses ADD COLUMN IF NOT EXISTS end_date text;';
  copied = signal(false);
  copySql() {
    navigator.clipboard?.writeText(this.migrationSql).then(() => {
      this.copied.set(true); setTimeout(() => this.copied.set(false), 1800);
    }).catch(() => {});
  }
}
