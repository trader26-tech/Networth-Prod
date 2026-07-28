import { Component, OnInit, inject, signal, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpErrorResponse } from '@angular/common/http';
import { OtherIncomeService, Income, IncomeInput, Frequency, LogInput, LogSummary } from '../../services/other-income.service';

type Draft = {
  owner: string; source: string; category: string; amount: number | null; currency: string;
  on_date: string; account: string; note: string; saveAsTemplate: boolean; frequency: Frequency;
};

const BLANK: Draft = {
  owner: '', source: '', category: '', amount: null, currency: 'INR', on_date: '',
  account: '', note: '', saveAsTemplate: false, frequency: 'monthly',
};

const FALLBACK_CATEGORIES = [
  'Dividend', 'Interest', 'Rental income', 'Bonus / Incentive', 'Capital gains',
  'Freelance / Consulting', 'Business income', 'Commission', 'Royalty', 'Pension',
  'Gift', 'Refund / Reimbursement', 'Cashback / Rewards', 'Maturity payout', 'Other',
];
const FALLBACK_CURRENCIES = ['INR', 'KWD', 'USD', 'AED', 'GBP', 'EUR', 'SGD'];
const CAT_COLORS = [
  '#2bb673', '#4e8cff', '#f0883e', '#9b6dd6', '#e0598b', '#00a3c4', '#e8b730',
  '#16b8a6', '#ef6f6c', '#7c83ff', '#46b450', '#d98c2b', '#5b7c99', '#c44ec4',
];

@Component({
  selector: 'app-other-income',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './other-income.html',
  styleUrl: './other-income.scss',
})
export class OtherIncome implements OnInit {
  private api = inject(OtherIncomeService);

  data = signal<LogSummary | null>(null);      // the selected month's actual log
  templates = signal<Income[]>([]);            // recurring reminders
  categories = signal<string[]>(FALLBACK_CATEGORIES);
  currencyList = signal<string[]>(FALLBACK_CURRENCIES);
  accountList = signal<string[]>([]);
  loading = signal(true);
  error = signal<string | null>(null);
  needsMigration = signal(false);

  showAdd = signal(false);
  addDraft = signal<Draft>({ ...BLANK });
  adding = signal(false);

  expandedId = signal<string | null>(null);
  editDraft = signal<Draft | null>(null);
  saving = signal(false);

  // filters
  search = signal('');
  catFilter = signal('');
  personFilter = signal('');

  // month
  viewMonth = signal<string>(this.curMonth());
  showReminders = signal(false);

  inr = OtherIncomeService.inr;
  inrFull = OtherIncomeService.inrFull;
  money = OtherIncomeService.money;

  readonly FREQS: { v: Frequency; label: string }[] = [
    { v: 'weekly', label: 'Weekly' }, { v: 'monthly', label: 'Monthly' },
    { v: 'quarterly', label: 'Quarterly' }, { v: 'half_yearly', label: 'Half-yearly' },
    { v: 'yearly', label: 'Yearly' },
  ];

  private curMonth(): string {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
  }
  monthLabel = computed(() => {
    const [y, m] = this.viewMonth().split('-').map(Number);
    return new Date(y, m - 1, 1).toLocaleDateString('en-IN', { month: 'long', year: 'numeric' });
  });
  isCurrentMonth = computed(() => this.viewMonth() === this.curMonth());
  private defaultDate(): string {
    if (this.isCurrentMonth()) {
      const d = new Date();
      return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    }
    return `${this.viewMonth()}-01`;
  }
  shiftMonth(delta: number) {
    const [y, m] = this.viewMonth().split('-').map(Number);
    const d = new Date(y, m - 1 + delta, 1);
    this.viewMonth.set(`${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`);
    this.loadMonth();
  }
  goToday() { this.viewMonth.set(this.curMonth()); this.loadMonth(); }

  items = computed(() => this.data()?.entries || []);
  hasData = computed(() => this.items().length > 0);
  remindersPending = computed(() => this.templates().filter(t => !t.added).length);

  people = computed(() => {
    const set = new Set<string>();
    this.items().forEach(e => e.owner && set.add(e.owner));
    return [...set].sort();
  });

  filtered = computed(() => {
    const q = this.search().trim().toLowerCase();
    const cf = this.catFilter(); const pf = this.personFilter();
    return this.items().filter(e => {
      if (cf && (e.category || '') !== cf) return false;
      if (pf && (e.owner || '') !== pf) return false;
      if (!q) return true;
      return [e.source, e.category, e.owner, e.account, e.note].some(v => (v || '').toLowerCase().includes(q));
    });
  });
  hasFilters = computed(() => !!this.search().trim() || !!this.catFilter() || !!this.personFilter());

  // ── category pie (from the month's actual log) ────────────────────────────────
  catColorMap = computed(() => {
    const map: Record<string, string> = {};
    (this.data()?.by_category || []).forEach((c, i) => map[c.category] = CAT_COLORS[i % CAT_COLORS.length]);
    return map;
  });
  hoveredCat = signal<string | null>(null);
  pieCats = computed(() => {
    const pf = this.personFilter(); const map = this.catColorMap();
    const rows = this.items().filter(e => !pf || (e.owner || '') === pf);
    const byCat: Record<string, number> = {};
    rows.forEach(e => { const c = e.category || 'Uncategorised'; byCat[c] = (byCat[c] || 0) + (e.amount_inr || 0); });
    const total = Object.values(byCat).reduce((s, v) => s + v, 0) || 1;
    return Object.entries(byCat)
      .map(([category, amt]) => ({ category, monthly_inr: amt, pct: amt / total, color: map[category] || '#8a93a3' }))
      .sort((a, b) => b.monthly_inr - a.monthly_inr);
  });
  pieTotal = computed(() => this.pieCats().reduce((s, c) => s + c.monthly_inr, 0));
  catDonut = computed(() => {
    const cats = this.pieCats(); const R = 52, C = 2 * Math.PI * R; let off = 0;
    const segs = cats.map(c => { const len = c.pct * C; const seg = { ...c, dash: `${len} ${C - len}`, offset: -off }; off += len; return seg; });
    return { R, C, segs };
  });
  activeCat = computed(() => this.hoveredCat() || this.catFilter());
  activeSeg = computed(() => { const a = this.activeCat(); return a ? this.pieCats().find(c => c.category === a) || null : null; });
  catColor(cat: string | null): string { return this.catColorMap()[(cat || '').trim()] || '#8a93a3'; }
  inrPer(cur: string): number { const t = this.data()?.fx?.inr_per; return (t && t[(cur || 'INR').toUpperCase()]) || (cur?.toUpperCase() === 'INR' ? 1 : 0) || 1; }
  isForeign(cur: string): boolean { return (cur || 'INR').toUpperCase() !== 'INR'; }
  amountInrPreview(d: Draft): number { return (Number(d.amount || 0)) * this.inrPer(d.currency); }

  ngOnInit() {
    this.api.meta().subscribe({
      next: m => { if (m?.categories?.length) this.categories.set(m.categories); if (m?.currencies?.length) this.currencyList.set(m.currencies); },
      error: () => {},
    });
    this.reload();
  }

  reload() { this.loadMonth(); this.api.accounts().subscribe({ next: r => this.accountList.set(r.accounts || []), error: () => {} }); }

  loadMonth() {
    this.loading.set(true); this.error.set(null); this.needsMigration.set(false);
    const mo = this.viewMonth();
    this.api.log(mo).subscribe({
      next: s => { this.data.set(s); this.loading.set(false); },
      error: (e: HttpErrorResponse) => {
        this.loading.set(false);
        if (e.status === 503) this.needsMigration.set(true);
        else this.error.set('Could not reach the API. Is the backend running?');
      },
    });
    this.api.templates(mo).subscribe({ next: r => this.templates.set(r.templates || []), error: () => {} });
  }

  // ── add a log entry ───────────────────────────────────────────────────────────
  openAdd() { this.addDraft.set({ ...BLANK, on_date: this.defaultDate() }); this.showAdd.set(true); }
  closeAdd() { this.showAdd.set(false); }
  submitAdd() {
    const d = this.addDraft();
    if (!d.source.trim()) return;
    this.adding.set(true);
    this.api.addLog(this.toLogInput(d)).subscribe({
      next: () => {
        if (d.saveAsTemplate) {
          this.api.addTemplate(this.toTemplateInput(d)).subscribe({ next: () => {}, error: () => {} });
        }
        this.adding.set(false); this.showAdd.set(false); this.loadMonth();
      },
      error: (e: HttpErrorResponse) => { this.adding.set(false); this.error.set(e.error?.detail || 'Could not save.'); },
    });
  }

  // ── inline edit a log entry ────────────────────────────────────────────────────
  toggle(it: Income) {
    if (this.expandedId() === it.id) { this.expandedId.set(null); this.editDraft.set(null); return; }
    this.expandedId.set(it.id);
    this.editDraft.set({
      owner: it.owner ?? '', source: it.source ?? '', category: it.category ?? '', amount: it.amount,
      currency: it.currency ?? 'INR', on_date: it.on_date ?? '', account: it.account ?? '',
      note: it.note ?? '', saveAsTemplate: false, frequency: 'monthly',
    });
  }
  saveEdit(it: Income) {
    const d = this.editDraft(); if (!d) return;
    this.saving.set(true);
    this.api.updateLog(it.id, this.toLogInput(d)).subscribe({
      next: () => { this.saving.set(false); this.expandedId.set(null); this.editDraft.set(null); this.loadMonth(); },
      error: () => { this.saving.set(false); this.error.set('Could not save changes.'); },
    });
  }

  // ── delete (with undo) ──────────────────────────────────────────────────────────
  pendingDelete = signal<{ item: Income } | null>(null);
  private deleteTimer: any = null;
  remove(it: Income) {
    this.finalizeDelete();
    if (this.expandedId() === it.id) { this.expandedId.set(null); this.editDraft.set(null); }
    this.data.update(s => s ? { ...s, entries: s.entries.filter(x => x.id !== it.id) } : s);
    this.pendingDelete.set({ item: it });
    this.deleteTimer = setTimeout(() => this.finalizeDelete(), 6000);
  }
  undoDelete() { const p = this.pendingDelete(); if (!p) return; clearTimeout(this.deleteTimer); this.deleteTimer = null; this.pendingDelete.set(null); this.loadMonth(); }
  finalizeDelete() {
    const p = this.pendingDelete(); if (!p) return;
    clearTimeout(this.deleteTimer); this.deleteTimer = null; this.pendingDelete.set(null);
    this.api.deleteLog(p.item.id).subscribe({ next: () => this.loadMonth(), error: () => { this.error.set('Could not delete.'); this.loadMonth(); } });
  }

  // ── reminders (templates) ──────────────────────────────────────────────────────
  toggleReminderItem(t: Income) {
    this.templates.update(ts => ts.map(x => x.id === t.id ? { ...x, added: !x.added } : x));  // optimistic
    this.api.toggleReminder(t.id, this.viewMonth()).subscribe({ next: () => this.loadMonth(), error: () => this.loadMonth() });
  }
  removeTemplate(t: Income) {
    if (!confirm(`Delete the reminder "${t.source}"? (logged income stays)`)) return;
    this.api.deleteTemplate(t.id).subscribe({ next: () => this.loadMonth(), error: () => {} });
  }

  clearFilters() { this.search.set(''); this.catFilter.set(''); this.personFilter.set(''); }

  // ── helpers ───────────────────────────────────────────────────────────────────
  private toLogInput(d: Draft): LogInput {
    return {
      owner: d.owner.trim() || null, source: d.source.trim(), category: d.category.trim() || null,
      amount: d.amount, currency: (d.currency || 'INR').toUpperCase(), on_date: d.on_date || null,
      account: d.account.trim() || null, note: d.note.trim() || null,
    };
  }
  private toTemplateInput(d: Draft): IncomeInput {
    return {
      owner: d.owner.trim() || null, source: d.source.trim(), category: d.category.trim() || null,
      amount: d.amount, currency: (d.currency || 'INR').toUpperCase(), frequency: d.frequency,
      account: d.account.trim() || null, active: true, note: d.note.trim() || null,
    };
  }
  freqLabel(f: Frequency): string { return this.FREQS.find(x => x.v === f)?.label || f; }
  fmtDay(iso: string | null): string {
    if (!iso) return '—';
    const d = new Date(iso + 'T00:00:00');
    return isNaN(d.getTime()) ? '' : d.toLocaleDateString('en-IN', { day: 'numeric', month: 'short' });
  }
}
