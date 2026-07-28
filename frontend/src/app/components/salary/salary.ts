import { Component, OnInit, inject, signal, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpErrorResponse } from '@angular/common/http';
import { SalaryService, SalaryEntry, SalaryInput, FxRates, Frequency } from '../../services/salary.service';

type Draft = {
  person: string;
  amount: number | null;
  currency: string;
  frequency: Frequency;
  bank_account: string;
  note: string;
};

const BLANK: Draft = {
  person: '', amount: null, currency: 'INR', frequency: 'monthly', bank_account: '', note: '',
};

const FALLBACK_CURRENCIES = ['INR', 'KWD', 'USD', 'AED', 'GBP', 'EUR', 'SGD', 'AUD', 'CAD', 'QAR', 'SAR', 'OMR', 'BHD'];

@Component({
  selector: 'app-salary',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './salary.html',
  styleUrl: './salary.scss',
})
export class Salary implements OnInit {
  private api = inject(SalaryService);

  items = signal<SalaryEntry[]>([]);
  fx = signal<FxRates | null>(null);
  currencies = signal<string[]>(FALLBACK_CURRENCIES);
  banks = signal<string[]>([]);
  loading = signal(true);
  error = signal<string | null>(null);
  needsMigration = signal(false);
  refreshingFx = signal(false);

  showAdd = signal(false);
  addDraft = signal<Draft>({ ...BLANK });
  adding = signal(false);

  expandedId = signal<string | null>(null);
  editDraft = signal<Draft | null>(null);
  saving = signal(false);

  inr = SalaryService.inr;
  inrFull = SalaryService.inrFull;
  money = SalaryService.money;

  hasData = computed(() => this.items().length > 0);

  // ── totals ──────────────────────────────────────────────────────────────────
  summary = computed(() => {
    const xs = this.items();
    let monthly = 0; const people = new Set<string>(); const curs = new Set<string>();
    for (const e of xs) { monthly += e.monthly_inr || 0; people.add(e.person); curs.add(e.currency); }
    const foreign = [...curs].filter(c => c !== 'INR');
    return {
      count: xs.length, people: people.size,
      monthly_inr: monthly, annual_inr: monthly * 12,
      currencies: [...curs], foreign,
    };
  });

  /** INR for 1 unit of a currency, from the live table (1 if unknown / not loaded). */
  inrPer(cur: string): number {
    const t = this.fx()?.inr_per;
    return (t && t[(cur || 'INR').toUpperCase()]) || (cur?.toUpperCase() === 'INR' ? 1 : 0) || 1;
  }
  private monthlyNative(d: Draft): number {
    const a = +(d.amount || 0);
    return d.frequency === 'annual' ? a / 12 : a;
  }
  /** Live converted ₹/month preview for a draft (recomputes as you type). */
  monthlyInrPreview(d: Draft): number { return this.monthlyNative(d) * this.inrPer(d.currency); }
  isForeign(cur: string): boolean { return (cur || 'INR').toUpperCase() !== 'INR'; }
  rateNote(cur: string): string {
    const c = (cur || 'INR').toUpperCase();
    if (c === 'INR') return '';
    return `1 ${c} = ${SalaryService.inrFull(this.inrPer(c))}`;
  }

  ngOnInit() {
    this.loadFx();
    this.api.currencies().subscribe({ next: r => { if (r?.currencies?.length) this.currencies.set(r.currencies); }, error: () => {} });
    this.reload();
  }

  loadFx(refresh = false) {
    if (refresh) this.refreshingFx.set(true);
    this.api.fx(refresh).subscribe({
      next: f => { this.fx.set(f); this.refreshingFx.set(false); },
      error: () => { this.refreshingFx.set(false); },
    });
  }

  reload() {
    this.loading.set(true);
    this.error.set(null);
    this.needsMigration.set(false);
    this.api.items().subscribe({
      next: xs => { this.items.set(xs); this.loading.set(false); },
      error: (e: HttpErrorResponse) => {
        this.loading.set(false);
        if (e.status === 503) { this.needsMigration.set(true); }
        else { this.error.set('Could not reach the API. Is the backend running?'); }
      },
    });
    this.api.banks().subscribe({ next: r => this.banks.set(r.banks || []), error: () => {} });
  }

  // ── add ──────────────────────────────────────────────────────────────────────
  openAdd() { this.addDraft.set({ ...BLANK }); this.showAdd.set(true); }
  closeAdd() { this.showAdd.set(false); }
  submitAdd() {
    const d = this.addDraft();
    if (!d.person.trim()) return;
    this.adding.set(true);
    this.api.addItem(this.toInput(d)).subscribe({
      next: () => { this.adding.set(false); this.showAdd.set(false); this.reload(); },
      error: (e: HttpErrorResponse) => { this.adding.set(false); this.error.set(e.error?.detail || 'Could not save.'); },
    });
  }

  // ── inline edit ───────────────────────────────────────────────────────────────
  toggle(it: SalaryEntry) {
    if (this.expandedId() === it.id) { this.expandedId.set(null); this.editDraft.set(null); return; }
    this.expandedId.set(it.id);
    this.editDraft.set({
      person: it.person ?? '', amount: it.amount, currency: it.currency ?? 'INR',
      frequency: it.frequency ?? 'monthly', bank_account: it.bank_account ?? '', note: it.note ?? '',
    });
  }
  saveEdit(it: SalaryEntry) {
    const d = this.editDraft();
    if (!d) return;
    this.saving.set(true);
    this.api.updateItem(it.id, this.toInput(d)).subscribe({
      next: updated => {
        this.saving.set(false);
        this.items.update(l => l.map(x => x.id === it.id ? updated : x));
        this.expandedId.set(null); this.editDraft.set(null);
        this.api.banks().subscribe({ next: r => this.banks.set(r.banks || []), error: () => {} });
      },
      error: () => { this.saving.set(false); this.error.set('Could not save changes.'); },
    });
  }

  // ── delete + undo ─────────────────────────────────────────────────────────────
  pendingDelete = signal<{ item: SalaryEntry; index: number } | null>(null);
  private deleteTimer: any = null;
  remove(it: SalaryEntry) {
    this.finalizeDelete();
    const list = this.items();
    const index = Math.max(0, list.findIndex(x => x.id === it.id));
    this.items.set(list.filter(x => x.id !== it.id));
    if (this.expandedId() === it.id) { this.expandedId.set(null); this.editDraft.set(null); }
    this.pendingDelete.set({ item: it, index });
    this.deleteTimer = setTimeout(() => this.finalizeDelete(), 6000);
  }
  undoDelete() {
    const pend = this.pendingDelete();
    if (!pend) return;
    clearTimeout(this.deleteTimer); this.deleteTimer = null;
    const list = [...this.items()];
    list.splice(Math.min(pend.index, list.length), 0, pend.item);
    this.items.set(list);
    this.pendingDelete.set(null);
  }
  finalizeDelete() {
    const pend = this.pendingDelete();
    if (!pend) return;
    clearTimeout(this.deleteTimer); this.deleteTimer = null;
    this.pendingDelete.set(null);
    this.api.deleteItem(pend.item.id).subscribe({
      next: () => {},
      error: () => {
        this.items.update(l => { const c = [...l]; c.splice(Math.min(pend.index, c.length), 0, pend.item); return c; });
        this.error.set('Could not delete.');
      },
    });
  }

  // ── helpers ───────────────────────────────────────────────────────────────────
  private toInput(d: Draft): SalaryInput {
    return {
      person: d.person.trim(),
      amount: d.amount,
      currency: (d.currency || 'INR').toUpperCase(),
      frequency: d.frequency,
      bank_account: d.bank_account.trim() || null,
      note: d.note.trim() || null,
    };
  }
  freqLabel(f: Frequency): string { return f === 'annual' ? '/yr' : '/mo'; }
  ago(iso?: string): string {
    if (!iso) return '';
    const mins = Math.round((Date.now() - Date.parse(iso)) / 60000);
    if (isNaN(mins)) return '';
    if (mins < 1) return 'just now';
    if (mins < 60) return mins + 'm ago';
    return Math.round(mins / 60) + 'h ago';
  }
}
