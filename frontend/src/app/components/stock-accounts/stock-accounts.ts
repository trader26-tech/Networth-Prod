import { Component, OnInit, inject, signal, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpErrorResponse } from '@angular/common/http';
import {
  BrokerageService, BrokerageSummary, AccountRow, AccountInput,
} from '../../services/brokerage.service';

type Draft = AccountInput & { id?: string };

const EMPTY: Draft = {
  member: '', broker: '', start_date: '', start_amount: 0, end_date: '', end_amount: 0, note: '', sellable_on: '',
};

@Component({
  selector: 'app-stock-accounts',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './stock-accounts.html',
  styleUrl: './stock-accounts.scss',
})
export class StockAccounts implements OnInit {
  private api = inject(BrokerageService);

  summary = signal<BrokerageSummary | null>(null);
  loading = signal(true);
  error = signal<string | null>(null);
  needsMigration = signal(false);

  // add/edit form
  showForm = signal(false);
  editingId = signal<string | null>(null);
  draft = signal<Draft>({ ...EMPTY });

  inr = BrokerageService.inr;
  inrFull = BrokerageService.inrFull;
  pct = BrokerageService.pct;

  hasData = computed(() => (this.summary()?.count || 0) > 0);

  ngOnInit() { this.load(); }

  load() {
    this.loading.set(true);
    this.error.set(null);
    this.needsMigration.set(false);
    this.api.summary().subscribe({
      next: s => { this.summary.set(s); this.loading.set(false); },
      error: (e: HttpErrorResponse) => {
        this.loading.set(false);
        if (e.status === 503) this.needsMigration.set(true);
        else this.error.set('Could not reach the API. Is the backend running?');
      },
    });
  }

  openAdd() {
    this.editingId.set(null);
    this.draft.set({ ...EMPTY });
    this.showForm.set(true);
  }
  openEdit(a: AccountRow) {
    this.editingId.set(a.id);
    this.draft.set({
      id: a.id, member: a.member, broker: a.broker,
      start_date: (a.start_date || '').slice(0, 10), start_amount: a.start_amount,
      end_date: (a.end_date || '').slice(0, 10), end_amount: a.end_amount, note: a.note || '',
      sellable_on: (a as any).sellable_on ?? '',
    });
    this.showForm.set(true);
  }
  closeForm() { this.showForm.set(false); }
  setField<K extends keyof Draft>(key: K, val: Draft[K]) { this.draft.update(d => ({ ...d, [key]: val })); }

  canSave = computed(() => {
    const d = this.draft();
    return !!d.member.trim() && !!d.start_date && !!d.end_date
      && +d.start_amount > 0 && +d.end_amount >= 0;
  });
  // live CAGR preview inside the form
  previewCagr = computed<number | null>(() => {
    const d = this.draft();
    const s = +d.start_amount, e = +d.end_amount;
    if (!d.start_date || !d.end_date || s <= 0) return null;
    const yrs = (new Date(d.end_date).getTime() - new Date(d.start_date).getTime()) / (365 * 864e5);
    if (yrs <= 0) return null;
    return Math.pow(e / s, 1 / yrs) - 1;
  });

  save() {
    if (!this.canSave()) return;
    const d = this.draft();
    const payload: AccountInput = {
      member: d.member.trim(), broker: (d.broker || '').trim(),
      start_date: d.start_date, start_amount: +d.start_amount,
      end_date: d.end_date, end_amount: +d.end_amount, note: (d.note || '').trim(),
      sellable_on: d.sellable_on || null,
    };
    const id = this.editingId();
    const req = id ? this.api.update(id, payload) : this.api.create(payload);
    req.subscribe({
      next: () => { this.showForm.set(false); this.load(); },
      error: (e: HttpErrorResponse) => {
        if (e.status === 503) this.needsMigration.set(true);
        else this.error.set('Could not save the account.');
      },
    });
  }

  remove(a: AccountRow) {
    if (!confirm(`Delete ${a.member}'s ${a.broker || 'account'}?`)) return;
    this.api.remove(a.id).subscribe({
      next: () => this.load(),
      error: () => this.error.set('Could not delete.'),
    });
  }

  /** Compact span like "5.2 yrs" / "8 mo" between two ISO dates. */
  spanText(a: string | null, b: string | null): string {
    if (!a || !b) return '—';
    const yrs = (new Date(b.slice(0, 10)).getTime() - new Date(a.slice(0, 10)).getTime()) / (365 * 864e5);
    if (isNaN(yrs) || yrs <= 0) return '—';
    if (yrs < 1) return Math.max(1, Math.round(yrs * 12)) + ' mo';
    return yrs.toFixed(1) + ' yrs';
  }

  fmtDate(d: string | null): string {
    if (!d) return '—';
    const dt = new Date(d.slice(0, 10));
    return isNaN(dt.getTime()) ? d : dt.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' });
  }
  cagrClass(v: number | null | undefined): string { return v == null ? '' : (v >= 0 ? 'pos' : 'neg'); }
}
