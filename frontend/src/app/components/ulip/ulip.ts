import { Component, OnInit, inject, signal, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpErrorResponse } from '@angular/common/http';
import { UlipService, UlipPolicy, UlipSummary, UlipInput, Frequency, FundType } from '../../services/ulip.service';

type Draft = {
  owner: string; insurer: string; plan_name: string; policy_number: string; life_assured: string;
  start_date: string; policy_term_years: number | null; premium_paying_term_years: number | null;
  premium_amount: number | null; premium_frequency: Frequency; sum_assured: number | null;
  fund_value: number | null; fund_type: FundType; note: string;
  sellable_on: string;
};

const BLANK: Draft = {
  owner: '', insurer: '', plan_name: '', policy_number: '', life_assured: '',
  start_date: '', policy_term_years: null, premium_paying_term_years: null,
  premium_amount: null, premium_frequency: 'yearly', sum_assured: null,
  fund_value: null, fund_type: 'equity', note: '', sellable_on: '',
};

@Component({
  selector: 'app-ulip',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './ulip.html',
  styleUrl: './ulip.scss',
})
export class Ulip implements OnInit {
  private api = inject(UlipService);

  data = signal<UlipSummary | null>(null);
  insurerList = signal<string[]>([]);
  loading = signal(true);
  error = signal<string | null>(null);
  needsMigration = signal(false);

  showAdd = signal(false);
  addDraft = signal<Draft>({ ...BLANK });
  adding = signal(false);

  expandedId = signal<string | null>(null);
  editDraft = signal<Draft | null>(null);
  saving = signal(false);

  inr = UlipService.inr;
  inrFull = UlipService.inrFull;
  pct = UlipService.pct;

  readonly FREQS: { v: Frequency; label: string }[] = [
    { v: 'monthly', label: 'Monthly' }, { v: 'quarterly', label: 'Quarterly' },
    { v: 'half_yearly', label: 'Half-yearly' }, { v: 'yearly', label: 'Yearly' },
    { v: 'single', label: 'Single premium' },
  ];
  readonly FUND_TYPES: { v: FundType; label: string }[] = [
    { v: 'equity', label: 'Equity' }, { v: 'balanced', label: 'Balanced' },
    { v: 'debt', label: 'Debt' }, { v: 'other', label: 'Other' },
  ];

  items = computed(() => this.data()?.policies || []);
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
    this.api.insurers().subscribe({ next: r => this.insurerList.set(r.insurers || []), error: () => {} });
  }

  // ── add ──────────────────────────────────────────────────────────────────────
  openAdd() { this.addDraft.set({ ...BLANK }); this.showAdd.set(true); }
  closeAdd() { this.showAdd.set(false); }
  submitAdd() {
    const d = this.addDraft();
    if (!d.plan_name.trim()) return;
    this.adding.set(true);
    this.api.addItem(this.toInput(d)).subscribe({
      next: () => { this.adding.set(false); this.showAdd.set(false); this.reload(); },
      error: (e: HttpErrorResponse) => { this.adding.set(false); this.error.set(e.error?.detail || 'Could not save.'); },
    });
  }

  // ── inline edit ───────────────────────────────────────────────────────────────
  toggle(it: UlipPolicy) {
    if (this.expandedId() === it.id) { this.expandedId.set(null); this.editDraft.set(null); return; }
    this.expandedId.set(it.id);
    this.editDraft.set({
      owner: it.owner ?? '', insurer: it.insurer ?? '', plan_name: it.plan_name ?? '',
      policy_number: it.policy_number ?? '', life_assured: it.life_assured ?? '',
      start_date: it.start_date ?? '', policy_term_years: it.policy_term_years,
      premium_paying_term_years: it.premium_paying_term_years, premium_amount: it.premium_amount,
      premium_frequency: it.premium_frequency ?? 'yearly', sum_assured: it.sum_assured,
      fund_value: it.fund_value, fund_type: it.fund_type ?? 'equity', note: it.note ?? '',
      sellable_on: it.sellable_on ?? '',
    });
  }
  saveEdit(it: UlipPolicy) {
    const d = this.editDraft();
    if (!d) return;
    this.saving.set(true);
    this.api.updateItem(it.id, this.toInput(d)).subscribe({
      next: () => { this.saving.set(false); this.expandedId.set(null); this.editDraft.set(null); this.reload(); },
      error: () => { this.saving.set(false); this.error.set('Could not save changes.'); },
    });
  }

  // ── delete + undo ─────────────────────────────────────────────────────────────
  pendingDelete = signal<{ item: UlipPolicy } | null>(null);
  private deleteTimer: any = null;
  remove(it: UlipPolicy) {
    this.finalizeDelete();
    if (this.expandedId() === it.id) { this.expandedId.set(null); this.editDraft.set(null); }
    this.data.update(s => s ? { ...s, policies: s.policies.filter(x => x.id !== it.id) } : s);
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
  private toInput(d: Draft): UlipInput {
    return {
      owner: d.owner.trim() || null,
      insurer: d.insurer.trim() || null,
      plan_name: d.plan_name.trim(),
      policy_number: d.policy_number.trim() || null,
      life_assured: d.life_assured.trim() || null,
      start_date: d.start_date || null,
      policy_term_years: d.policy_term_years,
      premium_paying_term_years: d.premium_paying_term_years,
      premium_amount: d.premium_amount,
      premium_frequency: d.premium_frequency,
      sum_assured: d.sum_assured,
      fund_value: d.fund_value,
      fund_type: d.fund_type,
      note: d.note.trim() || null,
      sellable_on: d.sellable_on || null,
    };
  }
  freqLabel(f: Frequency): string { return this.FREQS.find(x => x.v === f)?.label || f; }
  fundLabel(f: FundType | null): string { return this.FUND_TYPES.find(x => x.v === f)?.label || (f || '—'); }
  cagrClass(v: number | null): string { return v == null ? '' : (v >= 0 ? 'pos' : 'neg'); }
  fmtDate(iso: string | null): string {
    if (!iso) return '—';
    const d = new Date(iso + 'T00:00:00');
    if (isNaN(d.getTime())) return iso;
    return d.toLocaleDateString('en-IN', { month: 'short', year: 'numeric' });
  }
  /** how far through the premium-paying term, 0..1 */
  premiumProgress(it: UlipPolicy): number {
    if (!it.premiums_total_count) return 0;
    return Math.min(1, it.premiums_paid_count / it.premiums_total_count);
  }
}
