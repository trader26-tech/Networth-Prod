import { Component, OnInit, inject, signal, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpErrorResponse } from '@angular/common/http';
import { CashService, CashItem, CashSummary, CashInput, CashType } from '../../services/cash.service';

type Draft = {
  owner: string; type: CashType; where: string; account_label: string;
  balance: number | null; currency: string; as_of_date: string; note: string;
};

const BLANK: Draft = {
  owner: '', type: 'bank', where: '', account_label: '', balance: null, currency: 'INR', as_of_date: '', note: '',
};

const FALLBACK_CURRENCIES = ['INR', 'KWD', 'USD', 'AED', 'GBP', 'EUR', 'SGD'];

@Component({
  selector: 'app-cash',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './cash.html',
  styleUrl: './cash.scss',
})
export class Cash implements OnInit {
  private api = inject(CashService);

  data = signal<CashSummary | null>(null);
  whereList = signal<string[]>([]);
  currencyList = signal<string[]>(FALLBACK_CURRENCIES);
  loading = signal(true);
  error = signal<string | null>(null);
  needsMigration = signal(false);

  showAdd = signal(false);
  addDraft = signal<Draft>({ ...BLANK });
  adding = signal(false);

  expandedId = signal<string | null>(null);
  editDraft = signal<Draft | null>(null);
  saving = signal(false);

  inr = CashService.inr;
  inrFull = CashService.inrFull;
  money = CashService.money;

  items = computed(() => this.data()?.entries || []);
  hasData = computed(() => this.items().length > 0);

  inrPer(cur: string): number { const t = this.data()?.fx?.inr_per; return (t && t[(cur || 'INR').toUpperCase()]) || (cur?.toUpperCase() === 'INR' ? 1 : 0) || 1; }
  isForeign(cur: string): boolean { return (cur || 'INR').toUpperCase() !== 'INR'; }
  balPreview(d: Draft): number { return (Number(d.balance || 0)) * this.inrPer(d.currency); }

  ngOnInit() { this.reload(); }

  reload() {
    this.loading.set(true); this.error.set(null); this.needsMigration.set(false);
    this.api.summary().subscribe({
      next: s => { this.data.set(s); this.loading.set(false); },
      error: (e: HttpErrorResponse) => {
        this.loading.set(false);
        if (e.status === 503) this.needsMigration.set(true);
        else this.error.set('Could not reach the API. Is the backend running?');
      },
    });
    this.api.wheres().subscribe({ next: r => this.whereList.set(r.wheres || []), error: () => {} });
  }

  openAdd() { this.addDraft.set({ ...BLANK }); this.showAdd.set(true); }
  closeAdd() { this.showAdd.set(false); }
  submitAdd() {
    const d = this.addDraft();
    if (!d.where.trim() && d.balance == null) return;
    this.adding.set(true);
    this.api.addItem(this.toInput(d)).subscribe({
      next: () => { this.adding.set(false); this.showAdd.set(false); this.reload(); },
      error: (e: HttpErrorResponse) => { this.adding.set(false); this.error.set(e.error?.detail || 'Could not save.'); },
    });
  }

  toggle(it: CashItem) {
    if (this.expandedId() === it.id) { this.expandedId.set(null); this.editDraft.set(null); return; }
    this.expandedId.set(it.id);
    this.editDraft.set({
      owner: it.owner ?? '', type: it.type ?? 'bank', where: it.where ?? '', account_label: it.account_label ?? '',
      balance: it.balance, currency: it.currency ?? 'INR', as_of_date: it.as_of_date ?? '', note: it.note ?? '',
    });
  }
  saveEdit(it: CashItem) {
    const d = this.editDraft(); if (!d) return;
    this.saving.set(true);
    this.api.updateItem(it.id, this.toInput(d)).subscribe({
      next: () => { this.saving.set(false); this.expandedId.set(null); this.editDraft.set(null); this.reload(); },
      error: () => { this.saving.set(false); this.error.set('Could not save changes.'); },
    });
  }

  pendingDelete = signal<{ item: CashItem } | null>(null);
  private deleteTimer: any = null;
  remove(it: CashItem) {
    this.finalizeDelete();
    if (this.expandedId() === it.id) { this.expandedId.set(null); this.editDraft.set(null); }
    this.data.update(s => s ? { ...s, entries: s.entries.filter(x => x.id !== it.id) } : s);
    this.pendingDelete.set({ item: it });
    this.deleteTimer = setTimeout(() => this.finalizeDelete(), 6000);
  }
  undoDelete() { const p = this.pendingDelete(); if (!p) return; clearTimeout(this.deleteTimer); this.deleteTimer = null; this.pendingDelete.set(null); this.reload(); }
  finalizeDelete() {
    const p = this.pendingDelete(); if (!p) return;
    clearTimeout(this.deleteTimer); this.deleteTimer = null; this.pendingDelete.set(null);
    this.api.deleteItem(p.item.id).subscribe({ next: () => this.reload(), error: () => { this.error.set('Could not delete.'); this.reload(); } });
  }

  private toInput(d: Draft): CashInput {
    return {
      owner: d.owner.trim() || null, type: d.type, where: d.where.trim() || null,
      account_label: d.account_label.trim() || null, balance: d.balance,
      currency: (d.currency || 'INR').toUpperCase(), as_of_date: d.as_of_date || null, note: d.note.trim() || null,
    };
  }
  typeLabel(t: CashType): string { return t === 'cash' ? 'Cash in hand' : 'Bank'; }
  fmtDay(iso: string | null): string {
    if (!iso) return '—';
    const d = new Date(iso + 'T00:00:00');
    return isNaN(d.getTime()) ? '' : d.toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' });
  }
}
