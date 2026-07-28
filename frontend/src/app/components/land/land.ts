import { Component, OnInit, inject, signal, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpErrorResponse } from '@angular/common/http';
import {
  LandService, LandParcel, LandSummary, LandDocument, ParcelInput,
} from '../../services/land.service';
import { LandTax } from '../land-tax/land-tax';

/** Editable draft for the add-form and inline edit. */
type Draft = {
  name: string;
  owner: string;
  location: string;
  area_sqft: number | null;
  rate_sqft: number | null;   // editable; drives current_estimated_price = rate × area
  bought_date: string;
  bought_price: number | null;
  current_estimated_price: number | null;
  after_brokerage_price: number | null;
  notes: string;
  sellable_on: string;
};

const BLANK: Draft = {
  name: '', owner: '', location: '', area_sqft: null, rate_sqft: null, bought_date: '',
  bought_price: null, current_estimated_price: null, after_brokerage_price: null, notes: '', sellable_on: '',
};

/** Money-weighted portfolio CAGR (IRR): the single rate r at which every
 *  purchase, compounded from its own buy-date to today, sums to the current
 *  total value — correct for assets bought at different times. */
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
  selector: 'app-land',
  standalone: true,
  imports: [CommonModule, FormsModule, LandTax],
  templateUrl: './land.html',
  styleUrl: './land.scss',
})
export class Land implements OnInit {
  private api = inject(LandService);

  // Bhoomi — the on-page land-tax assistant
  assistantOpen = signal(false);
  assistantExpanded = signal(false);   // compact popup ⇄ big drawer
  assistantImg = '/bhoomi-land-tax.png';
  assistantImgOk = signal(true);
  closeAssistant() { this.assistantOpen.set(false); this.assistantExpanded.set(false); }

  summary = signal<LandSummary | null>(null);
  parcels = signal<LandParcel[]>([]);
  loading = signal(true);
  error = signal<string | null>(null);
  needsMigration = signal(false);

  // add form
  showAdd = signal(false);
  addDraft = signal<Draft>({ ...BLANK });
  adding = signal(false);

  // expanded / inline edit
  expandedId = signal<string | null>(null);
  editDraft = signal<Draft | null>(null);
  saving = signal(false);
  uploadingFor = signal<string | null>(null);

  inr = LandService.inr;
  inrFull = LandService.inrFull;
  pct = LandService.pct;

  hasData = computed(() => this.parcels().length > 0);

  /** Hero totals derived from the loaded parcels — always matches the list
   *  (no separate /summary request, so it can't race to zero on first load). */
  heroSummary = computed(() => {
    const ps = this.parcels();
    let invested = 0, current = 0, realisable = 0, docs = 0, legCurrent = 0;
    const legs: { years: number; bought: number }[] = [];
    const now = Date.now();
    for (const p of ps) {
      invested += p.bought_price || 0;
      current += p.current_estimated_price || 0;
      realisable += (p.after_brokerage_price ?? p.current_estimated_price) || 0;
      docs += p.documents?.length || 0;
      if (p.bought_price && p.bought_price > 0 && p.current_estimated_price &&
          p.current_estimated_price > 0 && p.bought_date) {
        const years = (now - Date.parse(p.bought_date)) / (365.25 * 86400000);
        if (years > 0) { legs.push({ years, bought: p.bought_price }); legCurrent += p.current_estimated_price; }
      }
    }
    const gain = current - invested;
    return {
      parcel_count: ps.length,
      invested, current_value: current, realisable_value: realisable,
      total_gain: gain, total_gain_pct: invested ? gain / invested : null,
      blended_cagr: moneyWeightedCagr(legs, legCurrent),
      document_count: docs,
    };
  });

  // ── Filters / search / sort ──────────────────────────────────────────────────
  search = signal('');
  ownerFilter = signal('');                                  // '' = all owners
  sort = signal<'value' | 'gain' | 'cagr' | 'newest' | 'oldest' | 'name'>('value');

  /** Distinct owners for the owner dropdown. */
  owners = computed(() => {
    const set = new Set<string>();
    for (const p of this.parcels()) if (p.owner) set.add(p.owner);
    return Array.from(set).sort();
  });

  hasFilters = computed(() => !!this.search().trim() || !!this.ownerFilter());

  private static readonly SORTERS: Record<string, (a: LandParcel, b: LandParcel) => number> = {
    value:  (a, b) => (b.current_estimated_price ?? -Infinity) - (a.current_estimated_price ?? -Infinity),
    gain:   (a, b) => (b.gain ?? -Infinity) - (a.gain ?? -Infinity),
    cagr:   (a, b) => (b.cagr ?? -Infinity) - (a.cagr ?? -Infinity),
    newest: (a, b) => (Date.parse(b.bought_date || '') || 0) - (Date.parse(a.bought_date || '') || 0),
    oldest: (a, b) => (Date.parse(a.bought_date || '') || 0) - (Date.parse(b.bought_date || '') || 0),
    name:   (a, b) => a.name.localeCompare(b.name),
  };

  /** Parcels after search + owner filter + sort — what the table renders. */
  filtered = computed(() => {
    const q = this.search().trim().toLowerCase();
    const owner = this.ownerFilter();
    const list = this.parcels().filter(p => {
      if (owner && (p.owner || '') !== owner) return false;
      if (!q) return true;
      return [p.name, p.location, p.owner, p.notes]
        .some(v => (v || '').toLowerCase().includes(q));
    });
    return [...list].sort(Land.SORTERS[this.sort()]);
  });

  clearFilters() { this.search.set(''); this.ownerFilter.set(''); }

  // ── Row selection → aggregated metrics for just the ticked parcels ───────────
  selectMode = signal(false);            // checkboxes hidden until toggled on
  selectedIds = signal<Set<string>>(new Set());

  toggleSelectMode() {
    const on = !this.selectMode();
    this.selectMode.set(on);
    if (!on) this.selectedIds.set(new Set());
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
    return rows.length > 0 && rows.every(p => this.selectedIds().has(p.id));
  });

  toggleSelectAll() {
    const ids = this.filtered().map(p => p.id);
    const allSel = this.allFilteredSelected();
    this.selectedIds.update(s => {
      const n = new Set(s);
      ids.forEach(id => allSel ? n.delete(id) : n.add(id));
      return n;
    });
  }

  clearSelection() { this.selectedIds.set(new Set()); }

  /** Aggregated metrics over the selected parcels (money-weighted CAGR). */
  selStats = computed(() => {
    const sel = this.parcels().filter(p => this.selectedIds().has(p.id));
    let invested = 0, current = 0, legCurrent = 0;
    const legs: { years: number; bought: number }[] = [];
    const now = Date.now();
    for (const p of sel) {
      invested += p.bought_price || 0;
      current += p.current_estimated_price || 0;
      if (p.bought_price && p.bought_price > 0 && p.current_estimated_price &&
          p.current_estimated_price > 0 && p.bought_date) {
        const years = (now - Date.parse(p.bought_date)) / (365.25 * 86400000);
        if (years > 0) { legs.push({ years, bought: p.bought_price }); legCurrent += p.current_estimated_price; }
      }
    }
    const gain = current - invested;
    return {
      count: sel.length,
      invested, current, gain,
      gain_pct: invested ? gain / invested : null,
      cagr: moneyWeightedCagr(legs, legCurrent),
    };
  });

  /** Copy-paste migration SQL surfaced when Supabase is on but tables are missing. */
  readonly migrationSql = `CREATE TABLE IF NOT EXISTS land_parcels (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  owner TEXT,
  location TEXT,
  area_sqft NUMERIC,
  bought_date DATE,
  bought_price NUMERIC,
  current_estimated_price NUMERIC,
  after_brokerage_price NUMERIC,
  notes TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS land_documents (
  id TEXT PRIMARY KEY,
  land_id TEXT NOT NULL REFERENCES land_parcels(id) ON DELETE CASCADE,
  filename TEXT NOT NULL,
  mime_type TEXT,
  size BIGINT,
  storage_path TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_land_created ON land_parcels (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_land_docs_land ON land_documents (land_id);`;

  copied = signal(false);

  ngOnInit() { this.reload(); }

  reload() {
    this.loading.set(true);
    this.error.set(null);
    this.needsMigration.set(false);
    this.api.parcels().subscribe({
      next: ps => {
        this.parcels.set(ps);
        this.loading.set(false);
      },
      error: (e: HttpErrorResponse) => {
        this.loading.set(false);
        if (e.status === 503) { this.needsMigration.set(true); }
        else { this.error.set('Could not reach the API. Is the backend running?'); }
      },
    });
    this.api.summary().subscribe({ next: s => this.summary.set(s), error: () => {} });
  }

  // ── Add ─────────────────────────────────────────────────────────────────────
  openAdd() { this.addDraft.set({ ...BLANK }); this.showAdd.set(true); }
  closeAdd() { this.showAdd.set(false); }

  submitAdd() {
    const d = this.addDraft();
    if (!d.name.trim()) return;
    this.adding.set(true);
    this.api.addParcel(this.toInput(d)).subscribe({
      next: () => { this.adding.set(false); this.showAdd.set(false); this.reload(); },
      error: (e: HttpErrorResponse) => {
        this.adding.set(false);
        this.error.set(e.error?.detail || 'Could not save the parcel.');
      },
    });
  }

  // ── Expand / inline edit ─────────────────────────────────────────────────────
  toggle(p: LandParcel) {
    if (this.expandedId() === p.id) { this.expandedId.set(null); this.editDraft.set(null); return; }
    this.expandedId.set(p.id);
    this.editDraft.set({
      name: p.name ?? '',
      owner: p.owner ?? '',
      location: p.location ?? '',
      area_sqft: p.area_sqft,
      rate_sqft: (p.area_sqft && p.current_estimated_price != null)
        ? Math.round(p.current_estimated_price / p.area_sqft) : null,
      bought_date: p.bought_date ?? '',
      bought_price: p.bought_price,
      current_estimated_price: p.current_estimated_price,
      after_brokerage_price: p.after_brokerage_price,
      notes: p.notes ?? '',
      sellable_on: p.sellable_on ?? '',
    });
  }

  saveEdit(p: LandParcel) {
    const d = this.editDraft();
    if (!d) return;
    this.saving.set(true);
    this.api.updateParcel(p.id, this.toInput(d)).subscribe({
      next: updated => {
        this.saving.set(false);
        this.parcels.update(list => list.map(x => x.id === p.id ? updated : x));
        this.refreshSummary();
      },
      error: () => { this.saving.set(false); this.error.set('Could not save changes.'); },
    });
  }

  // ── Delete (optimistic, one-click, with Undo — no confirm dialog) ────────────
  pendingDelete = signal<{ parcel: LandParcel; index: number } | null>(null);
  private deleteTimer: any = null;

  remove(p: LandParcel) {
    this.finalizeDelete();                       // commit any prior pending first
    const list = this.parcels();
    const index = Math.max(0, list.findIndex(x => x.id === p.id));
    this.parcels.set(list.filter(x => x.id !== p.id));   // disappears immediately
    if (this.expandedId() === p.id) this.expandedId.set(null);
    this.pendingDelete.set({ parcel: p, index });
    this.deleteTimer = setTimeout(() => this.finalizeDelete(), 6000);
  }

  undoDelete() {
    const pend = this.pendingDelete();
    if (!pend) return;
    clearTimeout(this.deleteTimer); this.deleteTimer = null;
    const list = [...this.parcels()];
    list.splice(Math.min(pend.index, list.length), 0, pend.parcel);
    this.parcels.set(list);
    this.pendingDelete.set(null);
  }

  /** Commit the pending delete to the backend now (timeout, dismiss, or next delete). */
  finalizeDelete() {
    const pend = this.pendingDelete();
    if (!pend) return;
    clearTimeout(this.deleteTimer); this.deleteTimer = null;
    this.pendingDelete.set(null);
    this.api.deleteParcel(pend.parcel.id).subscribe({
      next: () => this.refreshSummary(),
      error: () => {                              // restore on failure
        this.parcels.update(l => {
          const copy = [...l]; copy.splice(Math.min(pend.index, copy.length), 0, pend.parcel); return copy;
        });
        this.error.set('Could not delete the parcel.');
      },
    });
  }

  // ── Documents ────────────────────────────────────────────────────────────────
  onPickFiles(p: LandParcel, ev: Event) {
    const input = ev.target as HTMLInputElement;
    if (input.files?.length) this.uploadFiles(p, Array.from(input.files));
    input.value = '';
  }

  onDrop(p: LandParcel, ev: DragEvent) {
    ev.preventDefault();
    if (ev.dataTransfer?.files?.length) this.uploadFiles(p, Array.from(ev.dataTransfer.files));
  }

  private uploadFiles(p: LandParcel, files: File[]) {
    this.uploadingFor.set(p.id);
    let remaining = files.length;
    files.forEach(f => {
      this.api.uploadDocument(p.id, f).subscribe({
        next: doc => {
          this.parcels.update(list => list.map(x =>
            x.id === p.id ? { ...x, documents: [doc, ...x.documents] } : x));
          if (--remaining === 0) { this.uploadingFor.set(null); this.refreshSummary(); }
        },
        error: (e: HttpErrorResponse) => {
          if (--remaining === 0) this.uploadingFor.set(null);
          this.error.set(e.error?.detail || `Could not upload ${f.name}.`);
        },
      });
    });
  }

  removeDoc(p: LandParcel, doc: LandDocument) {
    if (!confirm(`Remove ${doc.filename}?`)) return;
    this.api.deleteDocument(doc.id).subscribe({
      next: () => {
        this.parcels.update(list => list.map(x =>
          x.id === p.id ? { ...x, documents: x.documents.filter(d => d.id !== doc.id) } : x));
        this.refreshSummary();
      },
      error: () => this.error.set('Could not remove the document.'),
    });
  }

  // ── rename a document (inline) ──────────────────────────────────────────────
  renamingDoc = signal<string | null>(null);
  renameText = signal('');
  startRename(doc: LandDocument) { this.renamingDoc.set(doc.id); this.renameText.set(doc.filename); }
  cancelRename() { this.renamingDoc.set(null); this.renameText.set(''); }
  saveRename(p: LandParcel, doc: LandDocument) {
    const name = this.renameText().trim();
    if (!name || name === doc.filename) { this.cancelRename(); return; }
    this.api.renameDocument(doc.id, name).subscribe({
      next: u => {
        this.parcels.update(list => list.map(x => x.id === p.id
          ? { ...x, documents: x.documents.map(d => d.id === doc.id ? { ...d, filename: u.filename } : d) } : x));
        this.cancelRename();
      },
      error: () => this.error.set('Could not rename the document.'),
    });
  }

  docUrl(doc: LandDocument): string { return this.api.documentUrl(doc.id); }

  // ── Area ↔ rate ↔ present value linkage (area stays fixed) ───────────────────
  /** Edit the rate → present value becomes rate × area. */
  setRate(d: Draft, v: number | null) {
    d.rate_sqft = (v === null || v === undefined || isNaN(v as number)) ? null : v;
    if (d.rate_sqft !== null && d.area_sqft) {
      d.current_estimated_price = Math.round(d.rate_sqft * d.area_sqft);
    }
  }
  /** Edit the present value directly → back-fill the rate so they stay in sync. */
  setPresent(d: Draft, v: number | null) {
    d.current_estimated_price = (v === null || v === undefined || isNaN(v as number)) ? null : v;
    this.syncRate(d);
  }
  /** Edit the area → keep present value, recompute the rate. */
  setArea(d: Draft, v: number | null) {
    d.area_sqft = (v === null || v === undefined || isNaN(v as number)) ? null : v;
    this.syncRate(d);
  }
  private syncRate(d: Draft) {
    d.rate_sqft = (d.current_estimated_price !== null && d.area_sqft)
      ? Math.round(d.current_estimated_price / d.area_sqft) : null;
  }

  /** A Google Maps link for a location — pass-through if it's already a URL,
   *  otherwise a maps search for the address/place text. */
  mapsUrl(loc: string | null): string {
    const v = (loc || '').trim();
    if (!v) return '';
    if (/^https?:\/\//i.test(v)) return v;
    return 'https://www.google.com/maps/search/?api=1&query=' + encodeURIComponent(v);
  }

  /** What to show for a location cell — the address, or a friendly label if it's a raw URL. */
  locationLabel(loc: string | null): string {
    const v = (loc || '').trim();
    if (!v) return '';
    return /^https?:\/\//i.test(v) ? 'View on map' : v;
  }

  // ── helpers ──────────────────────────────────────────────────────────────────
  private toInput(d: Draft): ParcelInput {
    return {
      name: d.name.trim(),
      owner: d.owner.trim() || null,
      location: d.location.trim() || null,
      area_sqft: d.area_sqft,
      bought_date: d.bought_date || null,
      bought_price: d.bought_price,
      current_estimated_price: d.current_estimated_price,
      after_brokerage_price: d.after_brokerage_price,
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
