import { Component, OnInit, inject, signal, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import {
  NetworthService, Summary, Asset, Meta, Person,
} from '../../services/networth.service';

const CLASS_COLORS: Record<string, string> = {
  real_estate: '#6366f1', equity: '#ef4444', gold: '#f59e0b',
  fixed_deposit: '#10b981', ulip: '#8b5cf6', lic: '#06b6d4',
  post_office: '#84cc16', provident_fund: '#14b8a6', cash: '#22c55e',
  other: '#94a3b8', loan: '#dc2626',
};

/** Editable working copy of an asset (CAGR shown as a percentage for friendliness). */
interface AssetDraft {
  name: string;
  asset_class: string;
  ownerName: string;
  multiOwner: boolean;
  risk: 'low' | 'medium' | 'high';
  invested_value: number | null;
  current_value: number;
  purchase_date: string | null;
  monthly_income: number | null;
  cagrPct: number | null;
  notes: string | null;
}

@Component({
  selector: 'app-networth',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink],
  templateUrl: './networth.html',
  styleUrl: './networth.scss',
})
export class Networth implements OnInit {
  private api = inject(NetworthService);

  summary = signal<Summary | null>(null);
  assets = signal<Asset[]>([]);
  meta = signal<Meta | null>(null);
  loading = signal(true);
  error = signal<string | null>(null);

  selected = signal<Asset | null>(null);
  showAdd = signal(false);
  newAsset = signal<Partial<Asset> & { ownerName?: string }>(this.blankAsset());

  // inline edit draft for the currently-expanded asset
  draft = signal<AssetDraft | null>(null);
  saving = signal(false);

  hasData = computed(() => (this.summary()?.asset_count ?? 0) > 0 || this.assets().length > 0);
  inr = NetworthService.inr;

  ngOnInit() { this.reload(); }

  reload() {
    this.loading.set(true);
    this.api.meta().subscribe({ next: m => this.meta.set(m) });
    this.api.summary().subscribe({
      next: s => { this.summary.set(s); this.loading.set(false); },
      error: () => { this.error.set('Could not reach the API. Is the backend running?'); this.loading.set(false); },
    });
    this.api.assets().subscribe({ next: a => this.assets.set(a) });
  }

  classColor(key: string): string { return CLASS_COLORS[key] ?? '#94a3b8'; }

  classLabel(key: string): string {
    return this.meta()?.asset_classes.find(c => c.key === key)?.label ?? key;
  }

  // allocation bar segments (assets only, excluding liabilities)
  allocSegments = computed(() => {
    const s = this.summary();
    if (!s) return [];
    return s.by_class.filter(c => !c.liability && c.value > 0);
  });

  pct(value: number): number {
    const total = this.summary()?.total_assets ?? 0;
    return total > 0 ? (value / total) * 100 : 0;
  }

  cagrPct(c: number | null): string { return c === null || c === undefined ? '—' : (c * 100).toFixed(1) + '%'; }

  select(a: Asset) {
    if (this.selected()?.id === a.id) { this.selected.set(null); this.draft.set(null); return; }
    this.selected.set(a);
    this.draft.set(this.toDraft(a));
  }

  toDraft(a: Asset): AssetDraft {
    return {
      name: a.name,
      asset_class: a.asset_class,
      ownerName: a.owners?.[0]?.person ?? (this.people()[0]?.name ?? 'Sanjeev'),
      multiOwner: (a.owners?.length ?? 0) > 1,
      risk: a.risk,
      invested_value: a.invested_value,
      current_value: a.current_value,
      purchase_date: this.toDateInput(a.purchase_date),
      monthly_income: a.monthly_income,
      cagrPct: a.cagr != null ? +(a.cagr * 100).toFixed(2) : null,
      notes: a.notes,
    };
  }

  // backend stores dates as ISO ('2008-06-06') or free text; <input type=date> needs yyyy-mm-dd
  toDateInput(d: string | null): string | null {
    if (!d) return null;
    const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(d);
    return m ? `${m[1]}-${m[2]}-${m[3]}` : d;
  }

  // label adapts per class: "Rent" for property, "Interest" for FD/PO, else "Monthly income"
  incomeLabel(cls: string): string {
    if (cls === 'real_estate') return 'Rent (monthly)';
    if (cls === 'fixed_deposit' || cls === 'post_office') return 'Interest (monthly)';
    return 'Monthly income';
  }

  draftGain(): number | null {
    const d = this.draft();
    if (!d || d.invested_value == null) return null;
    return (Number(d.current_value) || 0) - Number(d.invested_value);
  }

  saveEdit() {
    const a = this.selected(); const d = this.draft();
    if (!a || !d) return;
    if (!d.name?.trim()) { this.error.set('Name is required.'); return; }
    const patch: Partial<Asset> = {
      name: d.name.trim(),
      asset_class: d.asset_class,
      current_value: Number(d.current_value) || 0,
      invested_value: d.invested_value != null && d.invested_value !== ('' as any) ? Number(d.invested_value) : null,
      monthly_income: d.monthly_income != null && d.monthly_income !== ('' as any) ? Number(d.monthly_income) : null,
      cagr: d.cagrPct != null && d.cagrPct !== ('' as any) ? Number(d.cagrPct) / 100 : null,
      purchase_date: d.purchase_date || null,
      risk: d.risk,
      notes: d.notes || null,
    };
    // only overwrite owners when it's a single-owner asset (don't clobber splits)
    if (!d.multiOwner) patch.owners = [{ person: d.ownerName, pct: 100 }];
    this.saving.set(true);
    this.api.updateAsset(a.id, patch).subscribe({
      next: () => { this.saving.set(false); this.selected.set(null); this.draft.set(null); this.reload(); },
      error: e => { this.saving.set(false); this.error.set(e?.error?.detail || 'Could not save changes.'); },
    });
  }

  cancelEdit() { this.selected.set(null); this.draft.set(null); }

  ownerLabel(a: Asset): string {
    if (!a.owners?.length) return '—';
    if (a.owners.length === 1 && a.owners[0].pct === 100) return a.owners[0].person;
    return a.owners.map(o => `${o.person} ${o.pct}%`).join(', ');
  }

  // --- estimated tax on withdrawal (rough, shown only on click) ---
  estTax(a: Asset): { rate: number; label: string; amount: number; note: string } | null {
    const gain = a.invested_value != null ? a.current_value - a.invested_value : null;
    const base = gain != null && gain > 0 ? gain : null;
    let rate = 0; let label = ''; let note = '';
    switch (a.asset_class) {
      case 'equity':
        rate = 0.125; label = 'LTCG @ 12.5% (over ₹1.25L exempt)';
        note = 'Assumes long-term holding; STT-paid equity/MF. STCG would be 20%.'; break;
      case 'real_estate':
      case 'gold':
        rate = 0.125; label = 'LTCG @ 12.5% (no indexation, post Jul-2024)';
        note = 'Long-term (>24m property / >24m gold). Indexation removed in 2024.'; break;
      case 'fixed_deposit':
      case 'post_office':
        rate = 0.30; label = 'Interest taxed at slab (assumed 30%)';
        note = 'FD/PO interest is fully taxable at your slab; no capital-gains concept.'; break;
      case 'ulip':
        rate = 0.125; label = 'ULIP gains @ 12.5% (if premium > ₹2.5L/yr)';
        note = 'ULIPs with annual premium ≤ ₹2.5L may be exempt u/s 10(10D).'; break;
      case 'lic':
        rate = 0; label = 'Often exempt u/s 10(10D)';
        note = 'Traditional LIC maturity is usually tax-free if conditions met.'; break;
      default:
        return null;
    }
    const amount = base != null ? base * rate : (a.asset_class === 'fixed_deposit' || a.asset_class === 'post_office'
      ? (a.current_value - (a.invested_value ?? a.current_value)) * rate : 0);
    return { rate, label, amount: Math.max(0, amount), note };
  }

  // --- quick add ---
  blankAsset(): Partial<Asset> & { ownerName?: string } {
    return { name: '', asset_class: 'equity', current_value: 0, risk: undefined, ownerName: 'Sanjeev', owners: [] };
  }
  openAdd() { this.newAsset.set(this.blankAsset()); this.showAdd.set(true); }
  saveAdd() {
    const n = this.newAsset();
    if (!n.name?.trim()) return;
    const payload: Partial<Asset> = {
      name: n.name, asset_class: n.asset_class, current_value: Number(n.current_value) || 0,
      invested_value: n.invested_value != null ? Number(n.invested_value) : null,
      monthly_income: n.monthly_income != null ? Number(n.monthly_income) : null,
      cagr: n.cagr != null ? Number(n.cagr) : null,
      owners: n.ownerName ? [{ person: n.ownerName, pct: 100 }] : [],
    };
    this.api.addAsset(payload).subscribe({ next: () => { this.showAdd.set(false); this.reload(); } });
  }

  remove(a: Asset, ev: Event) {
    ev.stopPropagation();
    if (!confirm(`Delete "${a.name}"?`)) return;
    this.api.deleteAsset(a.id).subscribe({ next: () => this.reload() });
  }

  // delete every asset in a class (e.g. all Real Estate)
  removeClass(key: string, ev: Event) {
    ev.stopPropagation();
    const cls = this.summary()?.by_class.find(c => c.asset_class === key);
    const n = cls?.asset_count ?? 0;
    if (!n) return;
    if (!confirm(`Delete all ${n} ${this.classLabel(key)} asset(s)? This cannot be undone.`)) return;
    this.api.bulkDelete({ asset_class: key }).subscribe({
      next: r => { this.selected.set(null); this.draft.set(null); this.reload(); },
      error: e => this.error.set(e?.error?.detail || 'Bulk delete failed.'),
    });
  }

  removeAll() {
    const n = this.assets().length;
    if (!n) return;
    if (!confirm(`Delete ALL ${n} assets? This wipes everything and cannot be undone.`)) return;
    this.api.bulkDelete({ all: true }).subscribe({
      next: () => { this.selected.set(null); this.draft.set(null); this.reload(); },
      error: e => this.error.set(e?.error?.detail || 'Bulk delete failed.'),
    });
  }

  people(): Person[] { return this.summary()?.people ?? []; }
  personColor(name: string): string {
    return this.people().find(p => p.name === name)?.color ?? '#94a3b8';
  }
}
