import { Component, OnInit, inject, signal, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpErrorResponse } from '@angular/common/http';
import {
  ApartmentsService, AptUnit, AptSummary, AptDocument, UnitInput, AptTenant, TenantInput,
} from '../../services/apartments.service';
import { ApartmentsTax } from '../apartments-tax/apartments-tax';

type TenantForm = {
  unitId: string; id?: string;
  name: string; phone: string; advance_paid: number | null;
  move_in_date: string; move_out_date: string; notes: string;
};

type Draft = {
  name: string;
  owner: string;
  location: string;
  area_sqft: number | null;
  rate_sqft: number | null;
  bought_date: string;
  bought_price: number | null;
  current_estimated_price: number | null;
  after_brokerage_price: number | null;
  monthly_rent: number | null;
  notes: string;
  sellable_on: string;
};

const BLANK: Draft = {
  name: '', owner: '', location: '', area_sqft: null, rate_sqft: null, bought_date: '',
  bought_price: null, current_estimated_price: null, after_brokerage_price: null,
  monthly_rent: null, notes: '', sellable_on: '',
};

/** Money-weighted appreciation CAGR (IRR) — respects each unit's own holding
 *  period: the rate r where Σ bought·(1+r)^years = Σ current. */
function moneyWeightedCagr(legs: { years: number; bought: number }[], totalCurrent: number): number | null {
  if (!legs.length || totalCurrent <= 0) return null;
  const fv = (r: number) => legs.reduce((s, l) => s + l.bought * Math.pow(1 + r, l.years), 0);
  let lo = -0.9499, hi = 5.0;
  if (fv(hi) < totalCurrent) return hi;
  if (fv(lo) > totalCurrent) return lo;
  for (let i = 0; i < 100; i++) { const mid = (lo + hi) / 2; if (fv(mid) < totalCurrent) lo = mid; else hi = mid; }
  return (lo + hi) / 2;
}

@Component({
  selector: 'app-apartments',
  standalone: true,
  imports: [CommonModule, FormsModule, ApartmentsTax],
  templateUrl: './apartments.html',
  styleUrl: './apartments.scss',
})
export class Apartments implements OnInit {
  private api = inject(ApartmentsService);

  // Ashish — the on-page apartment-tax assistant (rental income + Sec-54 capital gains)
  assistantOpen = signal(false);
  assistantExpanded = signal(false);   // compact popup ⇄ big drawer
  assistantImg = '/ashish-apartment-tax.png';
  assistantImgOk = signal(true);
  closeAssistant() { this.assistantOpen.set(false); this.assistantExpanded.set(false); }

  summary = signal<AptSummary | null>(null);
  units = signal<AptUnit[]>([]);
  loading = signal(true);
  error = signal<string | null>(null);
  needsMigration = signal(false);

  showAdd = signal(false);
  addDraft = signal<Draft>({ ...BLANK });
  adding = signal(false);

  expandedId = signal<string | null>(null);
  editDraft = signal<Draft | null>(null);
  saving = signal(false);
  uploadingFor = signal<string | null>(null);

  inr = ApartmentsService.inr;
  inrFull = ApartmentsService.inrFull;
  pct = ApartmentsService.pct;

  hasData = computed(() => this.units().length > 0);

  /** Hero totals derived from the loaded units — always matches the list (no
   *  separate /summary request, so it can't race to zero on first load). */
  heroSummary = computed(() => {
    const us = this.units();
    let invested = 0, current = 0, rent = 0, docs = 0, legCurrent = 0;
    let advance = 0;
    const advanceItems: { tenant: string; unit: string; amount: number }[] = [];
    const legs: { years: number; bought: number }[] = [];
    const now = Date.now();
    for (const u of us) {
      invested += u.bought_price || 0;
      current += u.current_estimated_price || 0;
      rent += u.monthly_rent || 0;
      docs += u.documents?.length || 0;
      for (const t of (u.tenants || [])) {
        if (t.active && t.advance_paid && t.advance_paid > 0) {
          advance += t.advance_paid;
          advanceItems.push({ tenant: t.name || 'Tenant', unit: u.name || 'Apartment', amount: t.advance_paid });
        }
      }
      if (u.bought_price && u.bought_price > 0 && u.current_estimated_price &&
          u.current_estimated_price > 0 && u.bought_date) {
        const years = (now - Date.parse(u.bought_date)) / (365.25 * 86400000);
        if (years > 0) { legs.push({ years, bought: u.bought_price }); legCurrent += u.current_estimated_price; }
      }
    }
    const gain = current - invested;
    const annualRent = rent * 12;
    const appr = moneyWeightedCagr(legs, legCurrent);
    const rentOnCost = invested ? annualRent / invested : null;
    return {
      unit_count: us.length,
      invested, current_value: current,
      total_gain: gain, total_gain_pct: invested ? gain / invested : null,
      monthly_rent: rent,
      gross_yield: current ? annualRent / current : null,
      blended_total_cagr: appr != null ? appr + (rentOnCost || 0) : rentOnCost,
      document_count: docs,
      advance_repay: advance,
      advance_count: advanceItems.length,
      advance_items: advanceItems.sort((a, b) => b.amount - a.amount),
    };
  });

  // ── Filters / search / sort ──────────────────────────────────────────────────
  search = signal('');
  ownerFilter = signal('');
  sort = signal<'value' | 'rent' | 'total' | 'newest' | 'oldest' | 'name'>('value');

  owners = computed(() => {
    const set = new Set<string>();
    for (const u of this.units()) if (u.owner) set.add(u.owner);
    return Array.from(set).sort();
  });

  hasFilters = computed(() => !!this.search().trim() || !!this.ownerFilter());

  private static readonly SORTERS: Record<string, (a: AptUnit, b: AptUnit) => number> = {
    value:  (a, b) => (b.current_estimated_price ?? -Infinity) - (a.current_estimated_price ?? -Infinity),
    rent:   (a, b) => (b.monthly_rent ?? -Infinity) - (a.monthly_rent ?? -Infinity),
    total:  (a, b) => (b.total_cagr ?? -Infinity) - (a.total_cagr ?? -Infinity),
    newest: (a, b) => (Date.parse(b.bought_date || '') || 0) - (Date.parse(a.bought_date || '') || 0),
    oldest: (a, b) => (Date.parse(a.bought_date || '') || 0) - (Date.parse(b.bought_date || '') || 0),
    name:   (a, b) => a.name.localeCompare(b.name),
  };

  filtered = computed(() => {
    const q = this.search().trim().toLowerCase();
    const owner = this.ownerFilter();
    const list = this.units().filter(u => {
      if (owner && (u.owner || '') !== owner) return false;
      if (!q) return true;
      return [u.name, u.location, u.owner, u.notes].some(v => (v || '').toLowerCase().includes(q));
    });
    return [...list].sort(Apartments.SORTERS[this.sort()]);
  });

  clearFilters() { this.search.set(''); this.ownerFilter.set(''); }

  // ── Row selection → aggregated metrics for just the ticked apartments ────────
  selectMode = signal(false);            // checkboxes hidden until toggled on
  selectedIds = signal<Set<string>>(new Set());

  toggleSelectMode() {
    const on = !this.selectMode();
    this.selectMode.set(on);
    if (!on) this.selectedIds.set(new Set());   // leaving select mode clears ticks
  }

  isSelected(id: string): boolean { return this.selectedIds().has(id); }

  toggleSelect(id: string) {
    this.selectedIds.update(s => {
      const n = new Set(s);
      n.has(id) ? n.delete(id) : n.add(id);
      return n;
    });
  }

  allFilteredSelected = computed(() => {
    const rows = this.filtered();
    return rows.length > 0 && rows.every(u => this.selectedIds().has(u.id));
  });

  toggleSelectAll() {
    const ids = this.filtered().map(u => u.id);
    const allSel = this.allFilteredSelected();
    this.selectedIds.update(s => {
      const n = new Set(s);
      ids.forEach(id => allSel ? n.delete(id) : n.add(id));
      return n;
    });
  }

  clearSelection() { this.selectedIds.set(new Set()); }

  /** Aggregated metrics over the selected apartments — money-weighted
   *  appreciation IRR, plus portfolio rent yield-on-cost for the total CAGR. */
  selStats = computed(() => {
    const sel = this.units().filter(u => this.selectedIds().has(u.id));
    let invested = 0, current = 0, rent = 0, legCurrent = 0;
    const legs: { years: number; bought: number }[] = [];
    const now = Date.now();
    for (const u of sel) {
      invested += u.bought_price || 0;
      current += u.current_estimated_price || 0;
      rent += u.monthly_rent || 0;
      if (u.bought_price && u.bought_price > 0 && u.current_estimated_price &&
          u.current_estimated_price > 0 && u.bought_date) {
        const years = (now - Date.parse(u.bought_date)) / (365.25 * 86400000);
        if (years > 0) { legs.push({ years, bought: u.bought_price }); legCurrent += u.current_estimated_price; }
      }
    }
    const gain = current - invested;
    const annualRent = rent * 12;
    const appreciation = moneyWeightedCagr(legs, legCurrent);
    const rentOnCost = invested ? annualRent / invested : null;
    const total = appreciation != null ? appreciation + (rentOnCost || 0) : rentOnCost;
    return {
      count: sel.length,
      invested, current, gain,
      gain_pct: invested ? gain / invested : null,
      monthly_rent: rent,
      gross_yield: current ? annualRent / current : null,
      appreciation_cagr: appreciation,
      total_cagr: total,
    };
  });

  readonly migrationSql = `CREATE TABLE IF NOT EXISTS apartment_units (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  owner TEXT,
  location TEXT,
  area_sqft NUMERIC,
  bought_date DATE,
  bought_price NUMERIC,
  current_estimated_price NUMERIC,
  after_brokerage_price NUMERIC,
  monthly_rent NUMERIC,
  notes TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS apartment_documents (
  id TEXT PRIMARY KEY,
  apartment_id TEXT NOT NULL REFERENCES apartment_units(id) ON DELETE CASCADE,
  filename TEXT NOT NULL,
  mime_type TEXT,
  size BIGINT,
  storage_path TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS apartment_tenants (
  id TEXT PRIMARY KEY,
  apartment_id TEXT NOT NULL REFERENCES apartment_units(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  phone TEXT,
  advance_paid NUMERIC,
  move_in_date DATE,
  move_out_date DATE,
  notes TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_apt_created ON apartment_units (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_apt_docs_unit ON apartment_documents (apartment_id);
CREATE INDEX IF NOT EXISTS idx_apt_tenant_unit ON apartment_tenants (apartment_id);`;

  copied = signal(false);

  ngOnInit() { this.reload(); }

  reload() {
    this.loading.set(true);
    this.error.set(null);
    this.needsMigration.set(false);
    this.api.units().subscribe({
      next: us => { this.units.set(us); this.loading.set(false); },
      error: (e: HttpErrorResponse) => {
        this.loading.set(false);
        if (e.status === 503) { this.needsMigration.set(true); }
        else { this.error.set('Could not reach the API. Is the backend running?'); }
      },
    });
    this.api.summary().subscribe({ next: s => this.summary.set(s), error: () => {} });
  }

  openAdd() { this.addDraft.set({ ...BLANK }); this.showAdd.set(true); }
  closeAdd() { this.showAdd.set(false); }

  submitAdd() {
    const d = this.addDraft();
    if (!d.name.trim()) return;
    this.adding.set(true);
    this.api.addUnit(this.toInput(d)).subscribe({
      next: () => { this.adding.set(false); this.showAdd.set(false); this.reload(); },
      error: (e: HttpErrorResponse) => {
        this.adding.set(false);
        this.error.set(e.error?.detail || 'Could not save the unit.');
      },
    });
  }

  toggle(u: AptUnit) {
    if (this.expandedId() === u.id) { this.expandedId.set(null); this.editDraft.set(null); return; }
    this.expandedId.set(u.id);
    this.editDraft.set({
      name: u.name ?? '',
      owner: u.owner ?? '',
      location: u.location ?? '',
      area_sqft: u.area_sqft,
      rate_sqft: (u.area_sqft && u.current_estimated_price != null)
        ? Math.round(u.current_estimated_price / u.area_sqft) : null,
      bought_date: u.bought_date ?? '',
      bought_price: u.bought_price,
      current_estimated_price: u.current_estimated_price,
      after_brokerage_price: u.after_brokerage_price,
      monthly_rent: u.monthly_rent,
      notes: u.notes ?? '',
      sellable_on: u.sellable_on ?? '',
    });
  }

  saveEdit(u: AptUnit) {
    const d = this.editDraft();
    if (!d) return;
    this.saving.set(true);
    this.api.updateUnit(u.id, this.toInput(d)).subscribe({
      next: updated => {
        this.saving.set(false);
        this.units.update(list => list.map(x => x.id === u.id ? updated : x));
        this.refreshSummary();
      },
      error: () => { this.saving.set(false); this.error.set('Could not save changes.'); },
    });
  }

  // ── Delete (optimistic, one-click, with Undo) ────────────────────────────────
  pendingDelete = signal<{ unit: AptUnit; index: number } | null>(null);
  private deleteTimer: any = null;

  remove(u: AptUnit) {
    this.finalizeDelete();
    const list = this.units();
    const index = Math.max(0, list.findIndex(x => x.id === u.id));
    this.units.set(list.filter(x => x.id !== u.id));
    if (this.expandedId() === u.id) this.expandedId.set(null);
    this.pendingDelete.set({ unit: u, index });
    this.deleteTimer = setTimeout(() => this.finalizeDelete(), 6000);
  }

  undoDelete() {
    const pend = this.pendingDelete();
    if (!pend) return;
    clearTimeout(this.deleteTimer); this.deleteTimer = null;
    const list = [...this.units()];
    list.splice(Math.min(pend.index, list.length), 0, pend.unit);
    this.units.set(list);
    this.pendingDelete.set(null);
  }

  finalizeDelete() {
    const pend = this.pendingDelete();
    if (!pend) return;
    clearTimeout(this.deleteTimer); this.deleteTimer = null;
    this.pendingDelete.set(null);
    this.api.deleteUnit(pend.unit.id).subscribe({
      next: () => this.refreshSummary(),
      error: () => {
        this.units.update(l => {
          const copy = [...l]; copy.splice(Math.min(pend.index, copy.length), 0, pend.unit); return copy;
        });
        this.error.set('Could not delete the unit.');
      },
    });
  }

  // ── Documents ────────────────────────────────────────────────────────────────
  onPickFiles(u: AptUnit, ev: Event) {
    const input = ev.target as HTMLInputElement;
    if (input.files?.length) this.uploadFiles(u, Array.from(input.files));
    input.value = '';
  }

  onDrop(u: AptUnit, ev: DragEvent) {
    ev.preventDefault();
    if (ev.dataTransfer?.files?.length) this.uploadFiles(u, Array.from(ev.dataTransfer.files));
  }

  private uploadFiles(u: AptUnit, files: File[]) {
    this.uploadingFor.set(u.id);
    let remaining = files.length;
    files.forEach(f => {
      this.api.uploadDocument(u.id, f).subscribe({
        next: doc => {
          this.units.update(list => list.map(x =>
            x.id === u.id ? { ...x, documents: [doc, ...x.documents] } : x));
          if (--remaining === 0) { this.uploadingFor.set(null); this.refreshSummary(); }
        },
        error: (e: HttpErrorResponse) => {
          if (--remaining === 0) this.uploadingFor.set(null);
          this.error.set(e.error?.detail || `Could not upload ${f.name}.`);
        },
      });
    });
  }

  removeDoc(u: AptUnit, doc: AptDocument) {
    if (!confirm(`Remove ${doc.filename}?`)) return;
    this.api.deleteDocument(doc.id).subscribe({
      next: () => {
        this.units.update(list => list.map(x =>
          x.id === u.id ? { ...x, documents: x.documents.filter(d => d.id !== doc.id) } : x));
        this.refreshSummary();
      },
      error: () => this.error.set('Could not remove the document.'),
    });
  }

  // ── rename a document (inline) ──────────────────────────────────────────────
  renamingDoc = signal<string | null>(null);
  renameText = signal('');
  startRename(doc: AptDocument) { this.renamingDoc.set(doc.id); this.renameText.set(doc.filename); }
  cancelRename() { this.renamingDoc.set(null); this.renameText.set(''); }
  saveRename(u: AptUnit, doc: AptDocument) {
    const name = this.renameText().trim();
    if (!name || name === doc.filename) { this.cancelRename(); return; }
    this.api.renameDocument(doc.id, name).subscribe({
      next: r => {
        this.units.update(list => list.map(x => x.id === u.id
          ? { ...x, documents: x.documents.map(d => d.id === doc.id ? { ...d, filename: r.filename } : d) } : x));
        this.cancelRename();
      },
      error: () => this.error.set('Could not rename the document.'),
    });
  }

  docUrl(doc: AptDocument): string { return this.api.documentUrl(doc.id); }

  // ── Tenants ──────────────────────────────────────────────────────────────────
  tenantForm = signal<TenantForm | null>(null);
  savingTenant = signal(false);

  openAddTenant(u: AptUnit) {
    this.tenantForm.set({ unitId: u.id, name: '', phone: '', advance_paid: null,
      move_in_date: '', move_out_date: '', notes: '' });
  }
  openEditTenant(u: AptUnit, t: AptTenant) {
    this.tenantForm.set({
      unitId: u.id, id: t.id, name: t.name ?? '', phone: t.phone ?? '',
      advance_paid: t.advance_paid, move_in_date: (t.move_in_date || '').slice(0, 10),
      move_out_date: (t.move_out_date || '').slice(0, 10), notes: t.notes ?? '',
    });
  }
  cancelTenant() { this.tenantForm.set(null); }

  saveTenant() {
    const f = this.tenantForm();
    if (!f || !f.name.trim()) return;
    const payload: TenantInput = {
      name: f.name.trim(), phone: f.phone.trim() || null, advance_paid: f.advance_paid,
      move_in_date: f.move_in_date || null, move_out_date: f.move_out_date || null,
      notes: f.notes.trim() || null,
    };
    this.savingTenant.set(true);
    const req = f.id ? this.api.updateTenant(f.id, payload) : this.api.addTenant(f.unitId, payload);
    req.subscribe({
      next: t => {
        this.savingTenant.set(false);
        this.tenantForm.set(null);
        this.units.update(list => list.map(u => {
          if (u.id !== f.unitId) return u;
          const others = (u.tenants || []).filter(x => x.id !== t.id);
          return this.applyTenants(u, [...others, t]);
        }));
      },
      error: (e: HttpErrorResponse) => {
        this.savingTenant.set(false);
        this.error.set(e.error?.detail || 'Could not save the tenant.');
      },
    });
  }

  removeTenant(u: AptUnit, t: AptTenant) {
    if (!confirm(`Remove tenant ${t.name}?`)) return;
    this.api.deleteTenant(t.id).subscribe({
      next: () => this.units.update(list => list.map(x =>
        x.id === u.id ? this.applyTenants(x, (x.tenants || []).filter(y => y.id !== t.id)) : x)),
      error: () => this.error.set('Could not remove the tenant.'),
    });
  }

  /** Re-sort tenants (active first, newest move-in first) and recompute current. */
  private applyTenants(u: AptUnit, tenants: AptTenant[]): AptUnit {
    const sorted = [...tenants].sort((a, b) =>
      (a.active === b.active)
        ? (Date.parse(b.move_in_date || '') || 0) - (Date.parse(a.move_in_date || '') || 0)
        : (a.active ? -1 : 1));
    return { ...u, tenants: sorted, current_tenant: sorted.find(t => t.active) || null };
  }

  /** Years lived → "3 yrs 4 mo" / "8 mo". */
  tenancyLabel(years: number | null | undefined): string {
    if (years === null || years === undefined || years < 0) return '—';
    if (years < 1) { const m = Math.max(1, Math.round(years * 12)); return m + ' mo'; }
    const y = Math.floor(years), m = Math.round((years - y) * 12);
    const yl = `${y} yr${y > 1 ? 's' : ''}`;
    return m ? `${yl} ${m} mo` : yl;
  }

  fmtMonthYear(d: string | null): string {
    if (!d) return '—';
    const dt = new Date(d.slice(0, 10));
    return isNaN(dt.getTime()) ? d : dt.toLocaleDateString('en-IN', { month: 'short', year: 'numeric' });
  }

  // ── Area ↔ rate ↔ present value linkage ──────────────────────────────────────
  setRate(d: Draft, v: number | null) {
    d.rate_sqft = (v === null || v === undefined || isNaN(v as number)) ? null : v;
    if (d.rate_sqft !== null && d.area_sqft) {
      d.current_estimated_price = Math.round(d.rate_sqft * d.area_sqft);
    }
  }
  setPresent(d: Draft, v: number | null) {
    d.current_estimated_price = (v === null || v === undefined || isNaN(v as number)) ? null : v;
    this.syncRate(d);
  }
  setArea(d: Draft, v: number | null) {
    d.area_sqft = (v === null || v === undefined || isNaN(v as number)) ? null : v;
    this.syncRate(d);
  }
  private syncRate(d: Draft) {
    d.rate_sqft = (d.current_estimated_price !== null && d.area_sqft)
      ? Math.round(d.current_estimated_price / d.area_sqft) : null;
  }

  mapsUrl(loc: string | null): string {
    const v = (loc || '').trim();
    if (!v) return '';
    if (/^https?:\/\//i.test(v)) return v;
    return 'https://www.google.com/maps/search/?api=1&query=' + encodeURIComponent(v);
  }
  locationLabel(loc: string | null): string {
    const v = (loc || '').trim();
    if (!v) return '';
    return /^https?:\/\//i.test(v) ? 'View on map' : v;
  }

  private toInput(d: Draft): UnitInput {
    return {
      name: d.name.trim(),
      owner: d.owner.trim() || null,
      location: d.location.trim() || null,
      area_sqft: d.area_sqft,
      bought_date: d.bought_date || null,
      bought_price: d.bought_price,
      current_estimated_price: d.current_estimated_price,
      after_brokerage_price: d.after_brokerage_price,
      monthly_rent: d.monthly_rent,
      notes: d.notes.trim() || null,
      sellable_on: d.sellable_on || null,
    };
  }

  private refreshSummary() { this.api.summary().subscribe({ next: s => this.summary.set(s), error: () => {} }); }

  cagrClass(v: number | null): string {
    if (v === null || v === undefined) return '';
    return v >= 0 ? 'pos' : 'neg';
  }

  fmtBytes(n: number | null): string {
    if (!n) return '';
    if (n >= 1e6) return (n / 1e6).toFixed(1) + ' MB';
    if (n >= 1e3) return (n / 1e3).toFixed(0) + ' KB';
    return n + ' B';
  }

  fileGlyph(mime: string | null): string {
    const m = (mime || '').toLowerCase();
    if (m.includes('pdf')) return '📕';
    if (m.startsWith('image/')) return '🖼️';
    if (m.includes('sheet') || m.includes('excel') || m.includes('csv')) return '📊';
    if (m.includes('word') || m.includes('document')) return '📄';
    return '📎';
  }

  copySql() {
    navigator.clipboard?.writeText(this.migrationSql).then(() => {
      this.copied.set(true);
      setTimeout(() => this.copied.set(false), 2000);
    });
  }
}
