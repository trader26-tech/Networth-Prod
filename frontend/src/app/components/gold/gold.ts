import { Component, OnInit, inject, signal, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpErrorResponse } from '@angular/common/http';
import {
  GoldService, GoldItem, GoldDocument, GoldInput, MetalPrices, MetalPerformance, MetalType,
} from '../../services/gold.service';

type Draft = {
  name: string;
  owner: string;
  metal_type: MetalType;
  weight_g: number | null;
  purity_pct: number | null;
  manual_value: number | null;
  purchase_date: string;
  purchase_price: number | null;
  location: string;
  notes: string;
  sellable_on: string;
};

const BLANK: Draft = {
  name: '', owner: '', metal_type: 'gold', weight_g: null, purity_pct: 91.6,
  manual_value: null, purchase_date: '', purchase_price: null, location: '', notes: '', sellable_on: '',
};

@Component({
  selector: 'app-gold',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './gold.html',
  styleUrl: './gold.scss',
})
export class Gold implements OnInit {
  private api = inject(GoldService);

  items = signal<GoldItem[]>([]);
  prices = signal<MetalPrices | null>(null);
  perf = signal<MetalPerformance | null>(null);
  loading = signal(true);
  error = signal<string | null>(null);
  needsMigration = signal(false);
  refreshingPrice = signal(false);

  showAdd = signal(false);
  addDraft = signal<Draft>({ ...BLANK });
  adding = signal(false);

  expandedId = signal<string | null>(null);
  editDraft = signal<Draft | null>(null);
  saving = signal(false);
  uploadingFor = signal<string | null>(null);

  inr = GoldService.inr;
  inrFull = GoldService.inrFull;
  pct = GoldService.pct;

  readonly goldPurities = [
    { label: '24K · 99.9%', val: 99.9 }, { label: '22K · 91.6%', val: 91.6 },
    { label: '18K · 75%', val: 75 }, { label: '14K · 58.3%', val: 58.3 },
  ];
  readonly silverPurities = [
    { label: 'Fine · 99.9%', val: 99.9 }, { label: 'Sterling · 92.5%', val: 92.5 },
  ];

  hasData = computed(() => this.items().length > 0);

  // ── Local-rate override (e.g. Chennai retail = spot + duty/GST) ────────────────
  rate22kOverride = signal<number | null>(this.readNum('gold_rate22k'));
  silverOverride = signal<number | null>(this.readNum('silver_rate'));
  rateOpen = signal(false);

  private readNum(k: string): number | null {
    if (typeof localStorage === 'undefined') return null;
    const n = +(localStorage.getItem(k) || '');
    return isFinite(n) && n > 0 ? n : null;
  }
  private writeNum(k: string, v: number | null) {
    if (typeof localStorage === 'undefined') return;
    if (v != null) localStorage.setItem(k, String(v)); else localStorage.removeItem(k);
  }
  setRate22k(v: number | null) {
    const n = (v == null || isNaN(v as number) || (v as number) <= 0) ? null : v;
    this.rate22kOverride.set(n); this.writeNum('gold_rate22k', n);
  }
  setSilverRate(v: number | null) {
    const n = (v == null || isNaN(v as number) || (v as number) <= 0) ? null : v;
    this.silverOverride.set(n); this.writeNum('silver_rate', n);
  }
  resetRates() { this.setRate22k(null); this.setSilverRate(null); }
  usingLocalRate = computed(() => this.rate22kOverride() != null || this.silverOverride() != null);

  /** Spot reference (raw live), used as placeholders. */
  spot22k = computed(() => { const g = this.prices()?.gold_24k_per_g; return g == null ? null : g * 0.916; });
  spotSilver = computed(() => this.prices()?.silver_per_g ?? null);

  /** Effective rate per gram of *pure* metal — local override if set, else live spot. */
  ratePerGram(metal: MetalType): number | null {
    const p = this.prices();
    if (metal === 'gold') {
      const o = this.rate22kOverride();
      if (o != null) return o / 0.916;                 // 22K override → 24K-equivalent
      return p?.gold_24k_per_g ?? null;
    }
    if (metal === 'silver') {
      const o = this.silverOverride();
      if (o != null) return o;
      return p?.silver_per_g ?? null;
    }
    return null;
  }
  gold24k = computed(() => this.ratePerGram('gold'));
  gold22k = computed(() => { const r = this.ratePerGram('gold'); return r == null ? null : r * 0.916; });
  silverRate = computed(() => this.ratePerGram('silver'));

  // ── Interactive metal-price chart ─────────────────────────────────────────────
  readonly cW = 340;
  readonly cH = 110;

  chartMetal = signal<'both' | 'gold' | 'silver'>('both');   // default: compare both
  chartMode = signal<'growth' | 'value'>('growth');
  chartYears = signal<1 | 3 | 5>(1);
  hoverIdx = signal<number | null>(null);
  filterOpen = signal(false);

  hasSeries = computed(() => (this.perf()?.series?.gold?.length ?? 0) > 1);
  /** Value mode only makes sense for a single metal (gold ₹ and silver ₹ share no axis). */
  effMode = computed<'growth' | 'value'>(() => this.chartMetal() === 'both' ? 'growth' : this.chartMode());
  activeMetals = computed<('gold' | 'silver')[]>(() =>
    this.chartMetal() === 'both' ? ['gold', 'silver'] : [this.chartMetal() as 'gold' | 'silver']);

  private sliceN(): number { return this.chartYears() * 12; }
  private rawOf(metal: 'gold' | 'silver'): number[] {
    const s = this.perf()?.series;
    if (!s) return [];
    const arr = metal === 'gold' ? s.gold : s.silver;
    return arr.slice(Math.max(0, arr.length - this.sliceN()));
  }
  slicedLabels = computed<string[]>(() => {
    const s = this.perf()?.series;
    if (!s) return [];
    return s.labels.slice(Math.max(0, s.labels.length - this.sliceN()));
  });
  private valuesOf(metal: 'gold' | 'silver'): number[] {
    const raw = this.rawOf(metal);
    if (raw.length < 2) return [];
    if (this.effMode() === 'value') return raw;
    const base = raw[0] || 1;
    return raw.map(v => (v / base - 1) * 100);
  }

  /** Shared y-axis across all active series (so both metals compare fairly). */
  private axis = computed(() => {
    const all: number[] = [];
    for (const m of this.activeMetals()) all.push(...this.valuesOf(m));
    if (!all.length) return { min: 0, max: 1 };
    const min = Math.min(...all), max = Math.max(...all);
    return { min, max: max === min ? min + 1 : max };
  });
  private pathFor(values: number[], area: boolean): string {
    if (values.length < 2) return '';
    const { min, max } = this.axis(); const range = (max - min) || 1; const n = values.length;
    const pts = values.map((v, i) => [(i / (n - 1)) * this.cW, this.cH - ((v - min) / range) * this.cH]);
    let d = 'M' + pts.map(p => `${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(' L');
    if (area) d += ` L${this.cW},${this.cH} L0,${this.cH} Z`;
    return d;
  }

  /** One entry per active metal: its line/area paths, live rate, and growth over the range. */
  series = computed(() => {
    const single = this.activeMetals().length === 1;
    return this.activeMetals().map(m => {
      const raw = this.rawOf(m);
      const vals = this.valuesOf(m);
      return {
        metal: m,
        values: vals,
        line: this.pathFor(vals, false),
        area: single ? this.pathFor(vals, true) : '',
        rate: m === 'gold' ? this.gold24k() : this.silverRate(),
        growth: raw.length > 1 ? raw[raw.length - 1] / raw[0] - 1 : null,
      };
    });
  });

  // hover
  onChartMove(ev: MouseEvent, el: HTMLElement) {
    const n = this.slicedLabels().length;
    if (n < 2) { this.hoverIdx.set(null); return; }
    const rect = el.getBoundingClientRect();
    const ratio = Math.min(1, Math.max(0, (ev.clientX - rect.left) / rect.width));
    this.hoverIdx.set(Math.round(ratio * (n - 1)));
  }
  onChartLeave() { this.hoverIdx.set(null); }
  hoverX = computed<number | null>(() => {
    const i = this.hoverIdx(); const n = this.slicedLabels().length;
    return (i == null || n < 2) ? null : (i / (n - 1)) * this.cW;
  });
  hoverLeftPct = computed<number>(() => {
    const i = this.hoverIdx(); const n = this.slicedLabels().length;
    return (i == null || n < 2) ? 0 : (i / (n - 1)) * 100;
  });
  hoverInfo = computed(() => {
    const i = this.hoverIdx(); if (i == null) return null;
    const label = this.slicedLabels()[i] ?? '';
    const rows = this.activeMetals().map(m => ({ metal: m, price: this.rawOf(m)[i] ?? null }));
    return { label, rows };
  });
  markerY(values: number[]): number | null {
    const i = this.hoverIdx(); if (i == null || values.length < 2) return null;
    const { min, max } = this.axis(); const range = (max - min) || 1;
    return this.cH - ((values[i] - min) / range) * this.cH;
  }

  currentValue(it: GoldItem): number | null {
    if (it.manual_value != null) return it.manual_value;
    if (it.metal_type === 'other') return null;            // needs a manual value
    const rate = this.ratePerGram(it.metal_type);
    if (rate != null && it.weight_g && it.purity_pct) {
      return it.weight_g * (it.purity_pct / 100) * rate;
    }
    return null;
  }
  gainOf(it: GoldItem): number | null {
    const c = this.currentValue(it);
    return (c != null && it.purchase_price != null) ? c - it.purchase_price : null;
  }
  gainPctOf(it: GoldItem): number | null {
    const g = this.gainOf(it);
    return (g != null && it.purchase_price) ? g / it.purchase_price : null;
  }
  /** CAGR only when fully documented (date + price). */
  cagrOf(it: GoldItem): number | null {
    const c = this.currentValue(it);
    if (c == null || c <= 0 || !it.purchase_price || it.purchase_price <= 0 || !it.purchase_date) return null;
    const yrs = (Date.now() - Date.parse(it.purchase_date)) / (365.25 * 86400000);
    if (yrs <= 0) return null;
    return Math.pow(c / it.purchase_price, 1 / yrs) - 1;
  }

  /** Hero totals — all derived from the loaded items + live rates. */
  heroSummary = computed(() => {
    const items = this.items();
    let total = 0, goldVal = 0, silverVal = 0, otherVal = 0;
    let goldG = 0, silverG = 0, invested = 0, gainDoc = 0, docs = 0;
    const legs: { years: number; cost: number }[] = [];
    let legCurrent = 0;
    const now = Date.now();
    for (const it of items) {
      const c = this.currentValue(it) || 0;
      total += c;
      if (it.metal_type === 'gold') { goldVal += c; goldG += (it.weight_g || 0); }
      else if (it.metal_type === 'silver') { silverVal += c; silverG += (it.weight_g || 0); }
      else otherVal += c;
      docs += it.documents?.length || 0;
      if (it.purchase_price && it.purchase_price > 0) {
        invested += it.purchase_price;
        if (c > 0) gainDoc += c - it.purchase_price;
        if (it.purchase_date && c > 0) {
          const years = (now - Date.parse(it.purchase_date)) / (365.25 * 86400000);
          if (years > 0) { legs.push({ years, cost: it.purchase_price }); legCurrent += c; }
        }
      }
    }
    return {
      count: items.length,
      total_value: total,
      gold_value: goldVal, silver_value: silverVal, other_value: otherVal,
      gold_weight: goldG, silver_weight: silverG,
      invested, documented_gain: invested ? gainDoc : null,
      documented_gain_pct: invested ? gainDoc / invested : null,
      blended_cagr: moneyWeightedCagr(legs, legCurrent),
      document_count: docs,
    };
  });

  // ── Filters ────────────────────────────────────────────────────────────────
  search = signal('');
  metalFilter = signal<'' | MetalType>('');
  sort = signal<'value' | 'weight' | 'cagr' | 'newest' | 'name'>('value');

  hasFilters = computed(() => !!this.search().trim() || !!this.metalFilter());

  filtered = computed(() => {
    const q = this.search().trim().toLowerCase();
    const mf = this.metalFilter();
    const list = this.items().filter(it => {
      if (mf && it.metal_type !== mf) return false;
      if (!q) return true;
      return [it.name, it.owner, it.notes].some(v => (v || '').toLowerCase().includes(q));
    });
    const sorters: Record<string, (a: GoldItem, b: GoldItem) => number> = {
      value:  (a, b) => (this.currentValue(b) ?? -Infinity) - (this.currentValue(a) ?? -Infinity),
      weight: (a, b) => (b.weight_g ?? -Infinity) - (a.weight_g ?? -Infinity),
      cagr:   (a, b) => (this.cagrOf(b) ?? -Infinity) - (this.cagrOf(a) ?? -Infinity),
      newest: (a, b) => (Date.parse(b.created_at || '') || 0) - (Date.parse(a.created_at || '') || 0),
      name:   (a, b) => a.name.localeCompare(b.name),
    };
    return [...list].sort(sorters[this.sort()]);
  });
  clearFilters() { this.search.set(''); this.metalFilter.set(''); }

  // ── Selection ────────────────────────────────────────────────────────────────
  selectMode = signal(false);
  selectedIds = signal<Set<string>>(new Set());
  toggleSelectMode() { const on = !this.selectMode(); this.selectMode.set(on); if (!on) this.selectedIds.set(new Set()); }
  isSelected(id: string): boolean { return this.selectedIds().has(id); }
  toggleSelect(id: string) { this.selectedIds.update(s => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; }); }
  allFilteredSelected = computed(() => { const r = this.filtered(); return r.length > 0 && r.every(p => this.selectedIds().has(p.id)); });
  toggleSelectAll() { const ids = this.filtered().map(p => p.id); const all = this.allFilteredSelected(); this.selectedIds.update(s => { const n = new Set(s); ids.forEach(id => all ? n.delete(id) : n.add(id)); return n; }); }
  clearSelection() { this.selectedIds.set(new Set()); }
  selStats = computed(() => {
    const sel = this.items().filter(it => this.selectedIds().has(it.id));
    let total = 0, invested = 0, gain = 0, legCurrent = 0;
    const legs: { years: number; cost: number }[] = [];
    const now = Date.now();
    for (const it of sel) {
      const c = this.currentValue(it) || 0;
      total += c;
      if (it.purchase_price && it.purchase_price > 0) {
        invested += it.purchase_price;
        if (c > 0) gain += c - it.purchase_price;
        if (it.purchase_date && c > 0) {
          const years = (now - Date.parse(it.purchase_date)) / (365.25 * 86400000);
          if (years > 0) { legs.push({ years, cost: it.purchase_price }); legCurrent += c; }
        }
      }
    }
    return {
      count: sel.length, total_value: total, invested,
      gain: invested ? gain : null, gain_pct: invested ? gain / invested : null,
      cagr: moneyWeightedCagr(legs, legCurrent),
    };
  });

  ngOnInit() {
    this.loadPrices();
    this.api.performance().subscribe({ next: p => this.perf.set(p), error: () => {} });
    this.reload();
  }

  loadPrices(refresh = false) {
    if (refresh) this.refreshingPrice.set(true);
    this.api.prices(refresh).subscribe({
      next: p => { this.prices.set(p); this.refreshingPrice.set(false); },
      error: () => { this.refreshingPrice.set(false); },
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
  }

  openAdd() { this.addDraft.set({ ...BLANK }); this.showAdd.set(true); }
  closeAdd() { this.showAdd.set(false); }
  submitAdd() {
    const d = this.addDraft();
    if (!d.name.trim()) return;
    this.adding.set(true);
    this.api.addItem(this.toInput(d)).subscribe({
      next: () => { this.adding.set(false); this.showAdd.set(false); this.reload(); },
      error: (e: HttpErrorResponse) => { this.adding.set(false); this.error.set(e.error?.detail || 'Could not save.'); },
    });
  }

  toggle(it: GoldItem) {
    if (this.expandedId() === it.id) { this.expandedId.set(null); this.editDraft.set(null); return; }
    this.expandedId.set(it.id);
    this.editDraft.set({
      name: it.name ?? '', owner: it.owner ?? '', metal_type: it.metal_type ?? 'gold',
      weight_g: it.weight_g, purity_pct: it.purity_pct, manual_value: it.manual_value,
      purchase_date: it.purchase_date ?? '', purchase_price: it.purchase_price,
      location: it.location ?? '', notes: it.notes ?? '',
      sellable_on: it.sellable_on ?? '',
    });
  }
  /** When metal type changes, default a sensible purity. */
  onMetalChange(d: Draft, v: MetalType) {
    d.metal_type = v;
    if (v === 'gold' && (d.purity_pct == null)) d.purity_pct = 91.6;
    if (v === 'silver' && (d.purity_pct == null)) d.purity_pct = 92.5;
  }

  saveEdit(it: GoldItem) {
    const d = this.editDraft();
    if (!d) return;
    this.saving.set(true);
    this.api.updateItem(it.id, this.toInput(d)).subscribe({
      next: updated => { this.saving.set(false); this.items.update(l => l.map(x => x.id === it.id ? updated : x)); },
      error: () => { this.saving.set(false); this.error.set('Could not save changes.'); },
    });
  }

  // ── Delete + undo ──────────────────────────────────────────────────────────
  pendingDelete = signal<{ item: GoldItem; index: number } | null>(null);
  private deleteTimer: any = null;
  remove(it: GoldItem) {
    this.finalizeDelete();
    const list = this.items();
    const index = Math.max(0, list.findIndex(x => x.id === it.id));
    this.items.set(list.filter(x => x.id !== it.id));
    if (this.expandedId() === it.id) this.expandedId.set(null);
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

  // ── Documents ──────────────────────────────────────────────────────────────
  onPickFiles(it: GoldItem, ev: Event) {
    const input = ev.target as HTMLInputElement;
    if (input.files?.length) this.uploadFiles(it, Array.from(input.files));
    input.value = '';
  }
  onDrop(it: GoldItem, ev: DragEvent) {
    ev.preventDefault();
    if (ev.dataTransfer?.files?.length) this.uploadFiles(it, Array.from(ev.dataTransfer.files));
  }
  private uploadFiles(it: GoldItem, files: File[]) {
    this.uploadingFor.set(it.id);
    let remaining = files.length;
    files.forEach(f => {
      this.api.uploadDocument(it.id, f).subscribe({
        next: doc => {
          this.items.update(l => l.map(x => x.id === it.id ? { ...x, documents: [doc, ...x.documents] } : x));
          if (--remaining === 0) this.uploadingFor.set(null);
        },
        error: (e: HttpErrorResponse) => { if (--remaining === 0) this.uploadingFor.set(null); this.error.set(e.error?.detail || `Could not upload ${f.name}.`); },
      });
    });
  }
  removeDoc(it: GoldItem, doc: GoldDocument) {
    if (!confirm(`Remove ${doc.filename}?`)) return;
    this.api.deleteDocument(doc.id).subscribe({
      next: () => this.items.update(l => l.map(x => x.id === it.id ? { ...x, documents: x.documents.filter(d => d.id !== doc.id) } : x)),
      error: () => this.error.set('Could not remove the document.'),
    });
  }
  // ── rename a document (inline) ──────────────────────────────────────────────
  renamingDoc = signal<string | null>(null);
  renameText = signal('');
  startRename(doc: GoldDocument) { this.renamingDoc.set(doc.id); this.renameText.set(doc.filename); }
  cancelRename() { this.renamingDoc.set(null); this.renameText.set(''); }
  saveRename(it: GoldItem, doc: GoldDocument) {
    const name = this.renameText().trim();
    if (!name || name === doc.filename) { this.cancelRename(); return; }
    this.api.renameDocument(doc.id, name).subscribe({
      next: r => {
        this.items.update(l => l.map(x => x.id === it.id
          ? { ...x, documents: x.documents.map(d => d.id === doc.id ? { ...d, filename: r.filename } : d) } : x));
        this.cancelRename();
      },
      error: () => this.error.set('Could not rename the document.'),
    });
  }

  docUrl(doc: GoldDocument): string { return this.api.documentUrl(doc.id); }

  // ── helpers ────────────────────────────────────────────────────────────────
  private toInput(d: Draft): GoldInput {
    const other = d.metal_type === 'other';
    return {
      name: d.name.trim(),
      owner: d.owner.trim() || null,
      metal_type: d.metal_type,
      weight_g: other ? null : d.weight_g,
      purity_pct: other ? null : d.purity_pct,
      manual_value: d.manual_value,
      purchase_date: d.purchase_date || null,
      purchase_price: d.purchase_price,
      location: d.location.trim() || null,
      notes: d.notes.trim() || null,
      sellable_on: d.sellable_on || null,
    };
  }
  metalLabel(m: MetalType): string { return m === 'gold' ? 'Gold' : m === 'silver' ? 'Silver' : 'Other'; }

  valueTip(it: GoldItem): string {
    if (it.manual_value != null) return 'Manual value: ' + GoldService.inrFull(it.manual_value);
    if (it.metal_type === 'other') return 'Set a manual value for "Other" pieces';
    const rate = this.ratePerGram(it.metal_type);
    if (rate != null && it.weight_g && it.purity_pct) {
      return `${it.weight_g} g × ${it.purity_pct}% × ${GoldService.inrFull(rate)}/g (live ${this.metalLabel(it.metal_type)})`;
    }
    return 'Enter weight + purity to value from the live rate';
  }
  boughtTip(it: GoldItem): string {
    if (it.purchase_price == null) return 'No bill recorded';
    return 'Paid ' + GoldService.inrFull(it.purchase_price) + (it.purchase_date ? ' on ' + it.purchase_date : ' (no date)');
  }
  cagrClass(v: number | null): string { return v == null ? '' : (v >= 0 ? 'pos' : 'neg'); }
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
    return '📎';
  }
  ago(iso?: string): string {
    if (!iso) return '';
    const mins = Math.round((Date.now() - Date.parse(iso)) / 60000);
    if (isNaN(mins)) return '';
    if (mins < 1) return 'just now';
    if (mins < 60) return mins + 'm ago';
    return Math.round(mins / 60) + 'h ago';
  }
}

/** Money-weighted IRR over documented pieces (same engine as the property pages). */
function moneyWeightedCagr(legs: { years: number; cost: number }[], totalCurrent: number): number | null {
  if (!legs.length || totalCurrent <= 0) return null;
  const fv = (r: number) => legs.reduce((s, l) => s + l.cost * Math.pow(1 + r, l.years), 0);
  let lo = -0.9499, hi = 5.0;
  if (fv(hi) < totalCurrent) return hi;
  if (fv(lo) > totalCurrent) return lo;
  for (let i = 0; i < 100; i++) { const mid = (lo + hi) / 2; if (fv(mid) < totalCurrent) lo = mid; else hi = mid; }
  return (lo + hi) / 2;
}
