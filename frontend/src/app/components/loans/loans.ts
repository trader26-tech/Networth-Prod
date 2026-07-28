import { Component, OnInit, inject, signal, computed, HostListener } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpErrorResponse } from '@angular/common/http';
import { LoansService, Loan, LoansSummary, LoanInput, LoanFreq, LoanPayStatus, FxCost } from '../../services/loans.service';

/** One computed installment on the repayment ladder (native + INR amounts). */
interface Installment {
  loan_id: string; lender: string; loan_type: string; owner: string | null; currency: string;
  date: string;            // YYYY-MM-DD
  day: number; month: string;   // YYYY-MM
  native: number | null; inr: number;
  defaultStatus: LoanPayStatus; // 'paid' for past installments, else 'pending'
}

type Draft = {
  owner: string; lender: string; loan_type: string; account_no: string; currency: string;
  original_amount: number | null; outstanding_principal: number | null; interest_rate: number | null;
  emi_amount: number | null; emi_frequency: LoanFreq;
  start_date: string; maturity_date: string; next_installment_date: string;
  installments_paid: number | null; installments_remaining: number | null; note: string;
};

const BLANK: Draft = {
  owner: '', lender: '', loan_type: 'Home Loan', account_no: '', currency: 'INR',
  original_amount: null, outstanding_principal: null, interest_rate: null,
  emi_amount: null, emi_frequency: 'monthly',
  start_date: '', maturity_date: '', next_installment_date: '',
  installments_paid: null, installments_remaining: null, note: '',
};

@Component({
  selector: 'app-loans',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './loans.html',
  styleUrl: './loans.scss',
})
export class Loans implements OnInit {
  private api = inject(LoansService);

  data = signal<LoansSummary | null>(null);
  loading = signal(true);
  error = signal<string | null>(null);
  needsMigration = signal(false);

  showAdd = signal(false);
  addDraft = signal<Draft>({ ...BLANK });
  adding = signal(false);

  expandedId = signal<string | null>(null);
  editDraft = signal<Draft | null>(null);
  saving = signal(false);
  preclosingId = signal<string | null>(null);

  inr = LoansService.inr;
  inrFull = LoansService.inrFull;
  pct = LoansService.pct;

  readonly LOAN_TYPES = ['Home Loan', 'Personal Loan', 'Vehicle Loan', 'Flexible Loan',
    'Gold Loan', 'Education Loan', 'Business Loan', 'Credit Line', 'Other'];
  readonly CURRENCIES = ['INR', 'KWD', 'USD', 'AED', 'SAR', 'QAR', 'OMR', 'BHD'];
  readonly FREQS: { v: LoanFreq; label: string }[] = [
    { v: 'monthly', label: 'Monthly' }, { v: 'quarterly', label: 'Quarterly' },
    { v: 'half_yearly', label: 'Half-yearly' }, { v: 'yearly', label: 'Yearly' },
  ];

  items = computed(() => this.data()?.loans || []);
  activeItems = computed(() => this.items().filter(l => !l.closed));
  closedItems = computed(() => this.items().filter(l => l.closed));
  hasData = computed(() => this.items().length > 0);

  // ── view switch: repayments timeline ⇄ cards (repayments first by default) ─────
  view = signal<'loans' | 'repayments'>('repayments');

  ngOnInit() { this.reload(); this.loadStatuses(); }

  reload() {
    this.loading.set(true);
    this.error.set(null);
    this.needsMigration.set(false);
    this.api.summary().subscribe({
      next: s => { this.data.set(s); this.loading.set(false); this.loadFxCosts(); },
      error: (e: HttpErrorResponse) => {
        this.loading.set(false);
        if (e.status === 503) { this.needsMigration.set(true); }
        else { this.error.set('Could not reach the API. Is the backend running?'); }
      },
    });
  }

  // ── add ──────────────────────────────────────────────────────────────────────
  openAdd() { this.addDraft.set({ ...BLANK }); this.showAdd.set(true); }
  closeAdd() { this.showAdd.set(false); }
  submitAdd() {
    const d = this.addDraft();
    if (!d.lender.trim()) return;
    this.adding.set(true);
    this.api.addItem(this.toInput(d)).subscribe({
      next: () => { this.adding.set(false); this.showAdd.set(false); this.reload(); },
      error: (e: HttpErrorResponse) => { this.adding.set(false); this.error.set(e.error?.detail || 'Could not save.'); },
    });
  }

  // ── inline edit ───────────────────────────────────────────────────────────────
  toggle(it: Loan) {
    if (this.expandedId() === it.id) { this.expandedId.set(null); this.editDraft.set(null); return; }
    this.expandedId.set(it.id);
    this.editDraft.set({
      owner: it.owner ?? '', lender: it.lender ?? '', loan_type: it.loan_type ?? 'Other',
      account_no: it.account_no ?? '', currency: it.currency ?? 'INR',
      original_amount: it.original_amount, outstanding_principal: it.outstanding_principal,
      interest_rate: it.interest_rate, emi_amount: it.emi_amount, emi_frequency: it.emi_frequency ?? 'monthly',
      start_date: (it.start_date ?? '').slice(0, 10), maturity_date: (it.maturity_date ?? '').slice(0, 10),
      next_installment_date: (it.next_installment_date ?? '').slice(0, 10),
      installments_paid: it.installments_paid, installments_remaining: it.installments_remaining, note: it.note ?? '',
    });
  }
  saveEdit(it: Loan) {
    const d = this.editDraft();
    if (!d) return;
    this.saving.set(true);
    this.api.updateItem(it.id, this.toInput(d)).subscribe({
      next: () => { this.saving.set(false); this.expandedId.set(null); this.editDraft.set(null); this.reload(); },
      error: () => { this.saving.set(false); this.error.set('Could not save changes.'); },
    });
  }

  // ── preclose / reopen ───────────────────────────────────────────────────────────
  preclose(it: Loan) {
    if (!confirm(`Pre-close (foreclose) "${it.lender} · ${it.loan_type}"?\nIt will stop counting against your net worth and income.`)) return;
    this.preclosingId.set(it.id);
    this.api.preclose(it.id).subscribe({
      next: () => { this.preclosingId.set(null); this.reload(); },
      error: () => { this.preclosingId.set(null); this.error.set('Could not pre-close.'); },
    });
  }
  reopen(it: Loan) {
    this.preclosingId.set(it.id);
    this.api.reopen(it.id).subscribe({
      next: () => { this.preclosingId.set(null); this.reload(); },
      error: () => { this.preclosingId.set(null); this.error.set('Could not reopen.'); },
    });
  }

  // ── delete + undo ─────────────────────────────────────────────────────────────
  pendingDelete = signal<{ item: Loan } | null>(null);
  private deleteTimer: any = null;
  remove(it: Loan) {
    this.finalizeDelete();
    if (this.expandedId() === it.id) { this.expandedId.set(null); this.editDraft.set(null); }
    this.data.update(s => s ? { ...s, loans: s.loans.filter(x => x.id !== it.id) } : s);
    this.pendingDelete.set({ item: it });
    this.deleteTimer = setTimeout(() => this.finalizeDelete(), 6000);
  }
  undoDelete() {
    if (!this.pendingDelete()) return;
    clearTimeout(this.deleteTimer); this.deleteTimer = null;
    this.pendingDelete.set(null);
    this.reload();
  }
  finalizeDelete() {
    const pend = this.pendingDelete();
    if (!pend) return;
    clearTimeout(this.deleteTimer); this.deleteTimer = null;
    this.pendingDelete.set(null);
    this.api.deleteItem(pend.item.id).subscribe({
      next: () => this.reload(),
      error: () => { this.error.set('Could not delete.'); this.reload(); },
    });
  }

  // ── helpers ───────────────────────────────────────────────────────────────────
  private toInput(d: Draft): LoanInput {
    return {
      owner: d.owner.trim() || null, lender: d.lender.trim(), loan_type: d.loan_type,
      account_no: d.account_no.trim() || null, currency: (d.currency || 'INR').toUpperCase(),
      original_amount: d.original_amount, outstanding_principal: d.outstanding_principal,
      interest_rate: d.interest_rate, emi_amount: d.emi_amount, emi_frequency: d.emi_frequency,
      start_date: d.start_date || null, maturity_date: d.maturity_date || null,
      next_installment_date: d.next_installment_date || null,
      installments_paid: d.installments_paid, installments_remaining: d.installments_remaining,
      note: d.note.trim() || null,
    };
  }
  native(it: Loan, v: number | null | undefined): string {
    if (v === null || v === undefined) return '—';
    return it.currency === 'INR' ? this.inr(v) : LoansService.cur(v, it.currency);
  }
  fmtDate(iso: string | null): string {
    if (!iso) return '—';
    const d = new Date((iso + '').slice(0, 10) + 'T00:00:00');
    if (isNaN(d.getTime())) return iso;
    return d.toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' });
  }
  initials(s: string): string {
    return (s || '?').trim().split(/\s+/).slice(0, 2).map(w => w[0]).join('').toUpperCase();
  }

  // ══════════════════════════════════════════════════════════════════════════════
  //  REPAYMENTS — computed ladder, monthly timeline, calendar + mark-as-paid
  // ══════════════════════════════════════════════════════════════════════════════
  dow = ['S', 'M', 'T', 'W', 'T', 'F', 'S'];
  private _todayIso = (() => { const t = new Date(); return `${t.getFullYear()}-${String(t.getMonth() + 1).padStart(2, '0')}-${String(t.getDate()).padStart(2, '0')}`; })();
  private _todayKey(): string { return this._todayIso.slice(0, 7); }

  // ── per-installment paid / pending / unpaid marks ─────────────────────────────
  // keyed by `${loan_id}|${YYYY-MM-DD}`; anything absent falls back to the ladder's
  // default (past installments 'paid', future 'pending').
  paymentStatus = signal<Record<string, LoanPayStatus>>({});
  private static statusKey(loanId: string, date: string): string { return `${loanId}|${(date || '').slice(0, 10)}`; }
  /** Manual mark if set, else the ladder default for that installment. */
  statusOf(loanId: string, date: string, fallback: LoanPayStatus = 'pending'): LoanPayStatus {
    return this.paymentStatus()[Loans.statusKey(loanId, date)] || fallback;
  }
  private loadStatuses() {
    this.api.paymentStatuses().subscribe({
      next: rows => {
        const m: Record<string, LoanPayStatus> = {};
        for (const r of rows || []) if (r.loan_id && r.date) m[Loans.statusKey(r.loan_id, r.date)] = r.status;
        this.paymentStatus.set(m);
      },
      error: () => {},                 // table not migrated yet → everything uses defaults
    });
  }
  /** Mark a single installment paid / pending / unpaid — optimistic. */
  setPayStatus(loanId: string, date: string, status: LoanPayStatus) {
    const k = Loans.statusKey(loanId, date);
    const prev = this.paymentStatus();
    this.paymentStatus.update(m => { const n = { ...m }; if (status === 'pending') delete n[k]; else n[k] = status; return n; });
    this.api.setPaymentStatus(loanId, (date || '').slice(0, 10), status).subscribe({ error: () => this.paymentStatus.set(prev) });
  }

  // ── ladder math ───────────────────────────────────────────────────────────────
  private _stepFor(freq: LoanFreq): number {
    return freq === 'quarterly' ? 3 : freq === 'half_yearly' ? 6 : freq === 'yearly' ? 12 : 1;
  }
  /** Add `n` calendar months to a YYYY-MM-DD, clamping the day to the month length. */
  private _addMonths(iso: string, n: number): string {
    const [y, m, d] = iso.slice(0, 10).split('-').map(Number);
    const base = new Date(y, (m - 1) + n, 1);
    const dim = new Date(base.getFullYear(), base.getMonth() + 1, 0).getDate();
    const day = Math.min(d, dim);
    return `${base.getFullYear()}-${String(base.getMonth() + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
  }
  private _monthsBetween(a: string, b: string): number {
    const [ay, am] = a.slice(0, 7).split('-').map(Number);
    const [by, bm] = b.slice(0, 7).split('-').map(Number);
    return (by - ay) * 12 + (bm - am);
  }

  /** The full repayment ladder across every active loan, computed from EMI + dates. */
  schedule = computed<Installment[]>(() => {
    const out: Installment[] = [];
    for (const l of this.activeItems()) {
      if (!l.emi_amount && !l.emi_inr) continue;
      const freq = (l.emi_frequency || 'monthly') as LoanFreq;
      const step = this._stepFor(freq);
      const anchor = (l.next_installment_date || '').slice(0, 10) || (l.start_date || '').slice(0, 10);
      if (!anchor) continue;

      // how many installments behind us / ahead of us
      let paid = l.installments_paid ?? null;
      let remaining = l.installments_remaining ?? null;
      if (paid == null && l.start_date && l.next_installment_date)
        paid = Math.max(0, Math.round(this._monthsBetween(l.start_date, l.next_installment_date) / step));
      if (remaining == null && l.maturity_date && l.next_installment_date)
        remaining = Math.max(1, Math.round(this._monthsBetween(l.next_installment_date, l.maturity_date) / step) + 1);
      paid = Math.min(paid ?? 0, 600);
      remaining = Math.min(remaining ?? 12, 600);

      // `next` = the anchor when it's the upcoming installment; if we only had a
      // start date, roll forward past the already-paid installments to find it.
      const next = l.next_installment_date ? anchor : this._addMonths(anchor, step * (paid || 0));

      const dates: { date: string; def: LoanPayStatus }[] = [];
      for (let k = paid; k >= 1; k--) dates.push({ date: this._addMonths(next, -step * k), def: 'paid' });
      for (let k = 0; k < remaining; k++) dates.push({ date: this._addMonths(next, step * k), def: 'pending' });

      for (const { date, def } of dates) {
        out.push({
          loan_id: l.id, lender: l.lender, loan_type: l.loan_type, owner: l.owner, currency: l.currency,
          date, day: +date.slice(8, 10), month: date.slice(0, 7),
          native: l.emi_amount, inr: l.emi_inr || 0, defaultStatus: def,
        });
      }
    }
    return out.sort((a, b) => a.date.localeCompare(b.date));
  });

  /** Distinct months (sorted) that carry any installment. */
  monthsList = computed(() => {
    const set = new Set<string>();
    for (const p of this.schedule()) set.add(p.month);
    return Array.from(set).sort();
  });

  // ── month navigation ──────────────────────────────────────────────────────────
  selectedMonth = signal<string>('');
  activeMonth = computed(() => {
    const months = this.monthsList();
    if (!months.length) return '';
    const sel = this.selectedMonth();
    if (sel && months.includes(sel)) return sel;
    const now = this._todayKey();
    return months.find(m => m >= now) || months[months.length - 1];
  });
  selectBar(month: string) { this.selectedMonth.set(month); }
  canStepActive(delta: number): boolean {
    const m = this.monthsList(); const i = m.indexOf(this.activeMonth());
    return i >= 0 && i + delta >= 0 && i + delta < m.length;
  }
  stepActiveMonth(delta: number) {
    const m = this.monthsList(); const i = m.indexOf(this.activeMonth());
    if (i >= 0 && i + delta >= 0 && i + delta < m.length) this.selectedMonth.set(m[i + delta]);
  }

  // stable colour per loan for the timeline stack + share bar
  private _palette = ['#d94f4f', '#387ed1', '#e0892a', '#7c3aed', '#16a085', '#db2777',
                      '#0891b2', '#65a30d', '#9333ea', '#ca8a04', '#0d9488', '#e11d48'];
  loanColor(loanId: string): string {
    const ids = this.activeItems().map(l => l.id);
    const i = Math.max(0, ids.indexOf(loanId));
    return this._palette[i % this._palette.length];
  }
  segColor(i: number): string { return this._palette[i % this._palette.length]; }
  loanById(id: string): Loan | undefined { return this.items().find(l => l.id === id); }

  private winW = signal<number>(typeof window !== 'undefined' ? window.innerWidth : 1200);
  @HostListener('window:resize') onResize() {
    if (typeof window !== 'undefined') this.winW.set(window.innerWidth);
  }
  /** The whole repayment fits in the timeline — every month, bar-per-month,
   *  filling the width. Only on a very narrow phone do we cap it to a window
   *  around the current month so bars don't become invisible slivers. */
  private windowMonths(): string[] {
    const all = this.monthsList();
    const w = this.winW();
    const cap = w < 560 ? 24 : w < 820 ? 48 : Infinity;
    if (all.length <= cap) return all;
    const i = Math.max(0, all.indexOf(this.activeMonth()));
    const half = Math.floor(cap / 2);
    let start = Math.max(0, Math.min(i - half, all.length - cap));
    return all.slice(start, start + cap);
  }

  /** Monthly timeline (windowed): one bar per month, paid (green) vs due (amber). */
  repayChart = computed(() => {
    const months = this.windowMonths();
    if (!months.length) return null;
    const win = new Set(months);
    const agg = new Map<string, { paid: number; due: number; total: number }>();
    for (const p of this.schedule()) {
      if (!win.has(p.month)) continue;
      const st = this.statusOf(p.loan_id, p.date, p.defaultStatus);
      let a = agg.get(p.month);
      if (!a) { a = { paid: 0, due: 0, total: 0 }; agg.set(p.month, a); }
      if (st === 'paid') a.paid += p.inr; else a.due += p.inr;
      a.total += p.inr;
    }
    const max = Math.max(1, ...Array.from(agg.values()).map(a => a.total));
    const cols = months.map(m => {
      const a = agg.get(m) || { paid: 0, due: 0, total: 0 };
      return { month: m, label: this.monthLabel(m), paidH: a.paid / max * 100, dueH: a.due / max * 100, total: a.total };
    });
    // year axis: group consecutive months by calendar year
    const years: { year: string; count: number }[] = [];
    for (const m of months) {
      const y = m.slice(0, 4);
      const last = years[years.length - 1];
      if (last && last.year === y) last.count++; else years.push({ year: y, count: 1 });
    }
    return { months: cols, years };
  });

  /** Everything about the active month: calendar cells + per-loan installments. */
  monthDetail = computed(() => {
    const mk = this.activeMonth();
    if (!mk) return null;
    const rows = this.schedule().filter(p => p.month === mk);
    if (!rows.length) return null;

    const dayMap = new Map<number, { paid: number; due: number; unpaid: number; total: number; count: number }>();
    const loans: any[] = [];
    let paidT = 0, dueT = 0, totalT = 0;
    for (const p of rows) {
      const st = this.statusOf(p.loan_id, p.date, p.defaultStatus);
      totalT += p.inr;
      let dd = dayMap.get(p.day);
      if (!dd) { dd = { paid: 0, due: 0, unpaid: 0, total: 0, count: 0 }; dayMap.set(p.day, dd); }
      dd.total += p.inr; dd.count++;
      if (st === 'paid') { dd.paid += p.inr; paidT += p.inr; }
      else if (st === 'unpaid') { dd.unpaid += p.inr; dueT += p.inr; }
      else { dd.due += p.inr; dueT += p.inr; }
      loans.push({ loan_id: p.loan_id, lender: p.lender, loan_type: p.loan_type, owner: p.owner,
        currency: p.currency, native: p.native, inr: p.inr, day: p.day, date: p.date, status: st });
    }
    loans.sort((a, b) => b.inr - a.inr);
    const denom = totalT || 1;
    loans.forEach(l => l.pct = l.inr / denom);

    const [y, m] = mk.split('-').map(Number);
    const firstDow = new Date(y, m - 1, 1).getDay();
    const dim = new Date(y, m, 0).getDate();
    const dvals = Array.from(dayMap.values());
    const dayMax = Math.max(0, ...dvals.map(d => d.total));
    const level = (v: number): number => {
      if (v <= 0 || dayMax <= 0) return 0;
      const r = v / dayMax;
      return r <= 0.25 ? 1 : r <= 0.5 ? 2 : r <= 0.75 ? 3 : 4;
    };
    const cells: any[] = [];
    for (let i = 0; i < firstDow; i++) cells.push(null);
    for (let d = 1; d <= dim; d++) {
      const dd = dayMap.get(d);
      const total = dd ? dd.total : 0;
      const paid = dd ? dd.paid : 0, due = dd ? dd.due : 0, unpaid = dd ? dd.unpaid : 0;
      const awaiting = due + unpaid;
      let tone = 'bs-l0';
      if (paid > 0 && awaiting > 0) tone = 'mx' + (level(awaiting) || 1);                 // partly paid
      else if (unpaid > 0 && due === 0 && paid === 0) tone = 'rd' + (level(unpaid) || 1);  // unpaid → red
      else if (awaiting > 0 && paid === 0) tone = 'yl' + (level(awaiting) || 1);           // due → amber
      else if (paid > 0) tone = 'bs-l' + (level(paid) || 1);                               // paid → green
      cells.push({
        day: d, total, paid, due, unpaid, tone, count: dd ? dd.count : 0,
        isToday: `${mk}-${String(d).padStart(2, '0')}` === this._todayIso,
      });
    }
    return { month: mk, label: this.monthLabel(mk), total: totalT, paid: paidT, due: dueT,
             loans, cells, payCount: rows.length };
  });

  /** "To pay": installments due (on/before today) not yet marked paid. */
  pendingTotal = computed(() => {
    const today = this._todayIso;
    let sum = 0;
    for (const p of this.schedule()) {
      if (p.date > today) continue;
      if (this.statusOf(p.loan_id, p.date, p.defaultStatus) !== 'paid') sum += p.inr;
    }
    return sum;
  });

  // ── day modal — tap a calendar cell to mark each installment paid/unpaid ───────
  showDay = signal(false);
  dayKey = signal('');                    // YYYY-MM-DD being inspected
  openDay(month: string, day: number) {
    this.dayKey.set(`${month}-${String(day).padStart(2, '0')}`);
    this.showDay.set(true);
  }
  dayLabelFull = computed(() => {
    const dt = new Date(this.dayKey() + 'T00:00:00');
    return isNaN(dt.getTime()) ? this.dayKey() : dt.toLocaleDateString('en-IN', { weekday: 'short', day: '2-digit', month: 'short', year: 'numeric' });
  });
  dayPayments = computed(() => {
    const dk = this.dayKey();
    if (!dk) return [];
    return this.schedule().filter(p => p.date === dk).map(p => ({
      loan_id: p.loan_id, lender: p.lender, loan_type: p.loan_type, owner: p.owner,
      currency: p.currency, native: p.native, inr: p.inr,
      status: this.statusOf(p.loan_id, dk, p.defaultStatus),
    })).sort((a, b) => b.inr - a.inr);
  });
  dayTotals = computed(() => {
    let total = 0, paid = 0, due = 0, unpaid = 0;
    for (const p of this.dayPayments()) {
      total += p.inr;
      if (p.status === 'paid') paid += p.inr;
      else if (p.status === 'unpaid') unpaid += p.inr;
      else due += p.inr;
    }
    return { total, paid, due, unpaid, count: this.dayPayments().length };
  });
  markAllDay(status: LoanPayStatus) {
    const dk = this.dayKey();
    for (const p of this.dayPayments()) this.setPayStatus(p.loan_id, dk, status);
  }

  // ── formatting helpers ─────────────────────────────────────────────────────────
  monthLabel(mk: string): string {
    const [y, m] = mk.split('-').map(Number);
    const dt = new Date(y, m - 1, 1);
    return dt.toLocaleDateString('en-IN', { month: 'short', year: 'numeric' });
  }
  statusTip(c: { paid: number; due: number; unpaid: number; total: number }): string {
    const parts: string[] = [];
    if (c.paid > 0) parts.push('Paid ' + this.inrShort(c.paid));
    if (c.due > 0) parts.push('Due ' + this.inrShort(c.due));
    if (c.unpaid > 0) parts.push('Unpaid ' + this.inrShort(c.unpaid));
    return parts.join(' · ') || this.inrShort(c.total);
  }
  /** Compact ₹ for tight spots (calendar cells): ₹1.1L, ₹25k. */
  inrShort(v: number): string {
    const a = Math.abs(v || 0);
    const floor1 = (x: number) => (Math.floor(x * 10) / 10).toFixed(1);
    if (a >= 1e7) return '₹' + (a >= 1e8 ? Math.floor(v / 1e7).toString() : floor1(v / 1e7)) + 'Cr';
    if (a >= 1e5) return '₹' + floor1(v / 1e5) + 'L';
    if (a >= 1e3) return '₹' + floor1(v / 1e3) + 'k';
    return '₹' + Math.round(v);
  }

  // ══════════════════════════════════════════════════════════════════════════════
  //  REAL ₹-ADJUSTED INTEREST RATE — foreign-currency loan cost graph (Loans tab)
  // ══════════════════════════════════════════════════════════════════════════════
  fxCosts = signal<Record<string, FxCost>>({});
  private loadFxCosts() {
    for (const l of this.activeItems()) {
      if (l.currency === 'INR' || this.fxCosts()[l.id]) continue;
      this.api.fxCost(l.id).subscribe({
        next: fx => this.fxCosts.update(m => ({ ...m, [l.id]: fx })),
        error: () => {},
      });
    }
  }

  /** Foreign loans that have a usable ₹-cost graph, each with SVG geometry. */
  fxGraphs = computed(() => {
    const out: { loan: Loan; fx: FxCost; geom: any }[] = [];
    for (const l of this.activeItems()) {
      const fx = this.fxCosts()[l.id];
      if (fx?.available) { const geom = this.buildFxGeom(fx); if (geom) out.push({ loan: l, fx, geom }); }
    }
    return out;
  });

  /** Extra ₹ you now owe on the outstanding balance purely because the rupee
   *  fell since you borrowed = outstanding (native) × (fx_now − fx_start). */
  extraFxCost(loan: Loan, fx: FxCost): number {
    const outstanding = +(loan.outstanding_principal || 0);
    const start = fx.fx_start ?? 0, now = fx.fx_now ?? 0;
    return Math.max(0, outstanding * (now - start));
  }

  /** Which fx point is hovered, per loan (drives the tooltip + crosshair). */
  // FX-cost card is collapsed by default (the effective rate is the takeaway;
  // the month-by-month chart is opt-in).
  fxOpen = signal<Record<string, boolean>>({});
  isFxOpen(loanId: string): boolean { return !!this.fxOpen()[loanId]; }
  toggleFx(loanId: string) { this.fxOpen.update(m => ({ ...m, [loanId]: !m[loanId] })); }

  fxHover = signal<Record<string, number>>({});
  setFxHover(loanId: string, i: number) { this.fxHover.update(m => ({ ...m, [loanId]: i })); }
  clearFxHover(loanId: string) { this.fxHover.update(m => { const n = { ...m }; delete n[loanId]; return n; }); }
  /** Map the cursor's X over the plot to the nearest month and hover it. */
  onFxMove(ev: MouseEvent, g: any) {
    const el = ev.currentTarget as HTMLElement;
    const w = el.clientWidth; if (!w) return;
    const vbX = ev.offsetX / w * g.geom.W;                 // px → chart coords
    const inner = g.geom.W - g.geom.padL - g.geom.padR;
    const frac = inner ? (vbX - g.geom.padL) / inner : 0;
    const n = g.geom.pts.length;
    const i = Math.max(0, Math.min(n - 1, Math.round(frac * (n - 1))));
    this.setFxHover(g.loan.id, i);
  }

  /** Build the ₹-slide chart from the RAW monthly KWD/₹ path — history only, up
   *  to today. The line is the cumulative extra ₹ cost since you borrowed
   *  ((fx_t/fx_0 − 1)×100) — a smooth, real curve (no artificial jump, no
   *  projection). Fully computed here so all markers/hover live on the frontend. */
  private buildFxGeom(fx: FxCost) {
    const s = fx.series || [];
    if (s.length < 2) return null;
    const fx0 = s[0].fx;
    if (!fx0) return null;
    const W = 520, H = 170, padL = 42, padR = 16, padT = 16, padB = 26;
    const n = s.length;
    const pts0 = s.map((p, i) => ({ i, month: p.month, fx: p.fx, cost: (p.fx / fx0 - 1) * 100 }));
    const costs = pts0.map(p => p.cost);
    const lo = Math.min(0, ...costs);
    const hi = Math.max(...costs);
    const yMin = lo - Math.max(0.5, (hi - lo) * 0.08);
    const yMax = hi + Math.max(0.8, (hi - lo) * 0.14);
    const x = (i: number) => padL + (W - padL - padR) * (n === 1 ? 0 : i / (n - 1));
    const y = (v: number) => padT + (H - padT - padB) * (1 - (v - yMin) / (yMax - yMin || 1));

    // everything is expressed as % of the plot box so the whole chart is plain
    // CSS-positioned divs (no SVG). The plot keeps the W:H aspect ratio, so a
    // segment's length/angle computed in these coords render true on screen.
    const pctX = (px: number) => +(px / W * 100).toFixed(3);
    const pctY = (px: number) => +(px / H * 100).toFixed(3);
    const pts = pts0.map(p => ({
      ...p, lPct: pctX(x(p.i)), tPct: pctY(y(p.cost)),
      label: this.monthLabel(p.month), fxLabel: p.fx.toFixed(2),
    }));
    // the line as rotated segments between consecutive points
    const segs: { lPct: number; tPct: number; wPct: number; angle: number }[] = [];
    for (let k = 0; k < n - 1; k++) {
      const dxv = x(pts0[k + 1].i) - x(pts0[k].i);
      const dyv = y(pts0[k + 1].cost) - y(pts0[k].cost);
      segs.push({ lPct: pts[k].lPct, tPct: pts[k].tPct, wPct: pctX(Math.hypot(dxv, dyv)),
                  angle: +(Math.atan2(dyv, dxv) * 180 / Math.PI).toFixed(2) });
    }
    // area = clip-path polygon under the line, down to the baseline
    const baseYPct = pctY(y(Math.max(0, yMin)));
    const areaClip = `polygon(${pts.map(p => `${p.lPct}% ${p.tPct}%`).join(', ')}, ` +
      `${pts[n - 1].lPct}% ${baseYPct}%, ${pts[0].lPct}% ${baseYPct}%)`;

    // y gridlines (cost %)
    const span = yMax - yMin;
    const step = span > 24 ? 6 : span > 14 ? 4 : span > 7 ? 3 : span > 3 ? 2 : 1;
    const grid: { tPct: number; v: number }[] = [];
    const g0 = Math.ceil(yMin / step) * step;
    for (let v = g0; v <= yMax; v += step) grid.push({ tPct: pctY(y(v)), v });

    // x date labels — start, ~mid, now
    const midI = Math.round((n - 1) / 2);
    const xlabels = [0, midI, n - 1].filter((v, k, a) => a.indexOf(v) === k)
      .map(i => ({ lPct: pctX(x(i)), t: i === n - 1 ? 'now' : this.monthLabel(s[i].month) }));

    return {
      W, H, padL, padR, segs, areaClip, grid, xlabels, pts,
      zeroPct: pctY(y(0)), gridLeftPct: pctX(padL), gridWPct: pctX(W - padL - padR),
      plotTopPct: pctY(padT), plotBotPct: pctY(H - padB),
      startPt: pts[0], nowPt: pts[n - 1],
    };
  }
}
