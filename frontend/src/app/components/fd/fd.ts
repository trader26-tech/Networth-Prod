import { Component, OnInit, inject, signal, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpErrorResponse } from '@angular/common/http';
import { FdService, FdDeposit, FdSummary, FdInput, PayoutType, Frequency } from '../../services/fd.service';

type Draft = {
  owner: string; bank: string; principal: number | null; interest_rate: number | null;
  start_date: string; tenure_years: number | null; tenure_months: number | null;
  compounding_frequency: Frequency; payout_type: PayoutType; payout_frequency: Frequency; note: string;
  sellable_on: string;
};

const BLANK: Draft = {
  owner: '', bank: '', principal: null, interest_rate: null, start_date: '',
  tenure_years: null, tenure_months: null, compounding_frequency: 'quarterly',
  payout_type: 'payout', payout_frequency: 'quarterly', note: '', sellable_on: '',
};

@Component({
  selector: 'app-fd',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './fd.html',
  styleUrl: './fd.scss',
})
export class Fd implements OnInit {
  private api = inject(FdService);

  data = signal<FdSummary | null>(null);
  bankList = signal<string[]>([]);
  loading = signal(true);
  error = signal<string | null>(null);
  needsMigration = signal(false);

  showAdd = signal(false);
  addDraft = signal<Draft>({ ...BLANK });
  adding = signal(false);

  expandedId = signal<string | null>(null);
  editDraft = signal<Draft | null>(null);
  saving = signal(false);

  inr = FdService.inr;
  inrFull = FdService.inrFull;
  pct = FdService.pct;

  readonly FREQS: { v: Frequency; label: string }[] = [
    { v: 'monthly', label: 'Monthly' }, { v: 'quarterly', label: 'Quarterly' },
    { v: 'half_yearly', label: 'Half-yearly' }, { v: 'yearly', label: 'Yearly' },
  ];
  readonly PAYOUT_TYPES: { v: PayoutType; label: string }[] = [
    { v: 'payout', label: 'Payout (regular interest)' },
    { v: 'cumulative', label: 'Cumulative (reinvested)' },
  ];

  items = computed(() => this.data()?.fds || []);
  hasData = computed(() => this.items().length > 0);

  ngOnInit() { this.reload(); }

  reload() {
    this.loading.set(true);
    this.error.set(null);
    this.needsMigration.set(false);
    this.api.summary().subscribe({
      next: s => { this.data.set(s); this.loading.set(false); },
      error: (e: HttpErrorResponse) => {
        this.loading.set(false);
        if (e.status === 503) { this.needsMigration.set(true); }
        else { this.error.set('Could not reach the API. Is the backend running?'); }
      },
    });
    this.api.banks().subscribe({ next: r => this.bankList.set(r.banks || []), error: () => {} });
  }

  // ── add ──────────────────────────────────────────────────────────────────────
  openAdd() { this.addDraft.set({ ...BLANK }); this.showAdd.set(true); }
  closeAdd() { this.showAdd.set(false); }
  submitAdd() {
    const d = this.addDraft();
    if (!d.bank.trim()) return;
    this.adding.set(true);
    this.api.addItem(this.toInput(d)).subscribe({
      next: () => { this.adding.set(false); this.showAdd.set(false); this.reload(); },
      error: (e: HttpErrorResponse) => { this.adding.set(false); this.error.set(e.error?.detail || 'Could not save.'); },
    });
  }

  // ── inline edit ───────────────────────────────────────────────────────────────
  toggle(it: FdDeposit) {
    if (this.expandedId() === it.id) { this.expandedId.set(null); this.editDraft.set(null); return; }
    this.expandedId.set(it.id);
    const tm = it.tenure_months || 0;
    this.editDraft.set({
      owner: it.owner ?? '', bank: it.bank ?? '', principal: it.principal, interest_rate: it.interest_rate,
      start_date: it.start_date ?? '', tenure_years: Math.floor(tm / 12) || null, tenure_months: (tm % 12) || null,
      compounding_frequency: it.compounding_frequency ?? 'quarterly',
      payout_type: it.payout_type ?? 'payout', payout_frequency: it.payout_frequency ?? 'quarterly', note: it.note ?? '',
      sellable_on: it.sellable_on ?? '',
    });
  }
  saveEdit(it: FdDeposit) {
    const d = this.editDraft();
    if (!d) return;
    this.saving.set(true);
    this.api.updateItem(it.id, this.toInput(d)).subscribe({
      next: () => { this.saving.set(false); this.expandedId.set(null); this.editDraft.set(null); this.reload(); },
      error: () => { this.saving.set(false); this.error.set('Could not save changes.'); },
    });
  }

  // ── delete + undo ─────────────────────────────────────────────────────────────
  pendingDelete = signal<{ item: FdDeposit } | null>(null);
  private deleteTimer: any = null;
  remove(it: FdDeposit) {
    this.finalizeDelete();
    if (this.expandedId() === it.id) { this.expandedId.set(null); this.editDraft.set(null); }
    this.data.update(s => s ? { ...s, fds: s.fds.filter(x => x.id !== it.id) } : s);
    this.pendingDelete.set({ item: it });
    this.deleteTimer = setTimeout(() => this.finalizeDelete(), 6000);
  }
  undoDelete() {
    const pend = this.pendingDelete();
    if (!pend) return;
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
  private toInput(d: Draft): FdInput {
    const months = (Number(d.tenure_years || 0) * 12) + Number(d.tenure_months || 0);
    return {
      owner: d.owner.trim() || null,
      bank: d.bank.trim(),
      principal: d.principal,
      interest_rate: d.interest_rate,
      start_date: d.start_date || null,
      tenure_months: months || null,
      compounding_frequency: d.compounding_frequency,
      payout_type: d.payout_type,
      payout_frequency: d.payout_frequency,
      note: d.note.trim() || null,
      sellable_on: d.sellable_on || null,
    };
  }
  freqLabel(f: Frequency): string { return this.FREQS.find(x => x.v === f)?.label || f; }
  tenureLabel(it: FdDeposit): string {
    const m = it.tenure_months || 0; const y = Math.floor(m / 12); const r = m % 12;
    return [y ? `${y}y` : '', r ? `${r}m` : ''].filter(Boolean).join(' ') || '—';
  }
  cagrClass(v: number | null): string { return v == null ? '' : (v >= 0 ? 'pos' : 'neg'); }
  fmtDate(iso: string | null): string {
    if (!iso) return '—';
    const d = new Date(iso + 'T00:00:00');
    if (isNaN(d.getTime())) return iso;
    return d.toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' });
  }
}
