import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { CommonModule, DecimalPipe } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ApiService } from '../../services/api.service';

type CategoryKey = 'performance' | 'valuation' | 'growth' | 'profitability' | 'entry_point' | 'dividend';
type SortKey = 'rank' | 'composite' | CategoryKey | 'ticker' | 'sub_sector';

/**
 * Scorecard — 6-category ranked leaderboard of stocks against their sub-sector peers.
 *
 * Workflow:
 *   1. User uploads a Tickertape CSV (20 cols: 4 anchors + 16 metrics).
 *   2. Preview modal shows column-mapping, warnings, sample rows.
 *   3. User confirms → server saves + merges into master CSV.
 *   4. Leaderboard re-renders with weights + filters live-adjustable.
 *   5. Click any row → drill-down detail with all 16 metrics vs sub-sector median.
 */
@Component({
  selector: 'app-scorecard',
  imports: [CommonModule, DecimalPipe, FormsModule],
  templateUrl: './scorecard.html',
  styleUrl: './scorecard.scss',
})
export class ScorecardComponent implements OnInit {
  private api = inject(ApiService);

  // ── State ───────────────────────────────────────────────────────────────
  summary    = signal<any | null>(null);
  rows       = signal<any[]>([]);
  available  = signal(false);
  loading    = signal(false);
  uploading  = signal(false);
  previewing = signal(false);
  error      = signal('');
  uploadInfo = signal<any | null>(null);

  // Preview-before-save (single-file flow)
  pendingFile = signal<File | null>(null);
  preview     = signal<any | null>(null);

  // Bulk-upload progress (multi-file flow)
  bulkProgress = signal<{done: number; total: number; current: string} | null>(null);

  // Persisted raw files
  files = signal<any[]>([]);

  // Detail modal
  selected = signal<any | null>(null);

  // Weights — start with defaults from server
  presets        = signal<any | null>(null);
  weights        = signal<Record<string, number>>({});
  activePreset   = signal<string>('quality');
  weightsExpanded = signal(false);

  // Filters
  search    = signal('');
  capTier   = signal<'all' | 'large' | 'mid' | 'small'>('all');
  subSector = signal<string>('');

  // Sort
  sortBy  = signal<SortKey>('rank');
  sortDir = signal<'asc' | 'desc'>('asc');

  // ── Constants ───────────────────────────────────────────────────────────
  readonly CATEGORIES: { key: CategoryKey; label: string; short: string; color: string }[] = [
    { key: 'performance',   label: 'Performance',   short: 'Perf',  color: '#4d9aff' },
    { key: 'valuation',     label: 'Valuation',     short: 'Val',   color: '#8b5cf6' },
    { key: 'growth',        label: 'Growth',        short: 'Grw',   color: '#10b981' },
    { key: 'profitability', label: 'Profitability', short: 'Prof',  color: '#f59e0b' },
    { key: 'entry_point',   label: 'Entry Point',   short: 'Entry', color: '#06b6d4' },
    { key: 'dividend',      label: 'Dividend',      short: 'Div',   color: '#ec4899' },
  ];

  readonly METRIC_GROUPS: { cat: CategoryKey; metrics: string[] }[] = [
    { cat: 'performance',   metrics: ['1Y Return', '5Y CAGR', 'Sharpe Ratio'] },
    { cat: 'valuation',     metrics: ['PE Ratio', 'PB Ratio', 'EV/EBITDA Ratio'] },
    { cat: 'growth',        metrics: ['5Y Historical Revenue Growth', '5Y Historical EPS Growth', '1Y Forward EPS Growth'] },
    { cat: 'profitability', metrics: ['ROCE', '5Y Avg Return on Equity', 'Net Profit Margin'] },
    { cat: 'entry_point',   metrics: ['% Away From 52W High', 'RSI – 14D'] },
    { cat: 'dividend',      metrics: ['Dividend Yield', 'Payout Ratio'] },
  ];

  // ── Lifecycle ───────────────────────────────────────────────────────────
  ngOnInit() {
    this.api.scorecardPresets().subscribe({
      next: (p) => {
        this.presets.set(p);
        this.weights.set({ ...(p?.default_weights || {}) });
        this.refreshAll();
      },
      error: () => this.refreshAll(),
    });
  }

  refreshAll() {
    this.loading.set(true);
    this.api.scorecardSummary().subscribe({
      next: (r) => this.summary.set(r),
      error: () => this.summary.set(null),
    });
    this.loadFiles();
    this.loadLeaderboard();
  }

  loadFiles() {
    this.api.scorecardFiles().subscribe({
      next: (r) => this.files.set(r?.files ?? []),
      error: () => this.files.set([]),
    });
  }

  deleteFile(name: string) {
    if (!confirm(`Delete ${name} and rebuild master?`)) return;
    this.api.scorecardDeleteFile(name).subscribe({
      next: () => this.refreshAll(),
      error: (e) => this.error.set(e?.error?.detail || 'Delete failed'),
    });
  }

  loadLeaderboard() {
    this.loading.set(true);
    this.error.set('');
    this.api.scorecardLeaderboard({
      weights:   this.weights(),
      subSector: this.subSector() || undefined,
      capTier:   this.capTier() === 'all' ? undefined : this.capTier(),
      search:    this.search().trim() || undefined,
    }).subscribe({
      next: (r) => {
        this.rows.set(r?.rows ?? []);
        this.available.set(!!r?.available);
        this.loading.set(false);
      },
      error: (e) => {
        this.error.set(e?.error?.detail || 'Failed to load leaderboard');
        this.loading.set(false);
      },
    });
  }

  // ── Upload ──────────────────────────────────────────────────────────────
  // Single file → preview modal, user confirms, then save.
  // Multiple files → skip preview, upload sequentially with progress bar.
  onUploadChange(ev: Event) {
    const input = ev.target as HTMLInputElement;
    const list = input?.files;
    if (!list || !list.length) return;
    const files = Array.from(list);
    input.value = '';

    if (files.length === 1) {
      this._previewSingle(files[0]);
    } else {
      this._uploadBulk(files);
    }
  }

  private _previewSingle(file: File) {
    this.pendingFile.set(file);
    this.preview.set(null);
    this.uploadInfo.set(null);
    this.error.set('');
    this.previewing.set(true);
    this.api.scorecardPreview(file).subscribe({
      next: (r) => { this.preview.set(r); this.previewing.set(false); },
      error: (e) => {
        this.error.set(e?.error?.detail || e?.message || 'Preview failed');
        this.previewing.set(false);
        this.pendingFile.set(null);
      },
    });
  }

  private _uploadBulk(files: File[]) {
    this.error.set('');
    this.uploadInfo.set(null);
    this.uploading.set(true);
    this.bulkProgress.set({ done: 0, total: files.length, current: files[0].name });
    const results: any[] = [];

    const next = (idx: number) => {
      if (idx >= files.length) {
        this.uploading.set(false);
        this.bulkProgress.set(null);
        const last = results[results.length - 1];
        this.uploadInfo.set({
          saved_as:    `${files.length} files`,
          rows_in_file: results.reduce((s, r) => s + (r?.rows_in_file || 0), 0),
          unique_rows:  last?.unique_rows ?? 0,
          files_processed: last?.files_processed ?? 0,
          duplicates_dropped: last?.duplicates_dropped ?? 0,
        });
        this.refreshAll();
        return;
      }
      const f = files[idx];
      this.bulkProgress.set({ done: idx, total: files.length, current: f.name });
      this.api.scorecardUpload(f).subscribe({
        next: (r) => {
          results.push(r);
          next(idx + 1);
        },
        error: (e) => {
          this.error.set(`Failed on ${f.name}: ${e?.error?.detail || e?.message || 'upload error'}`);
          this.uploading.set(false);
          this.bulkProgress.set(null);
          this.refreshAll();
        },
      });
    };
    next(0);
  }

  cancelPreview() {
    this.pendingFile.set(null);
    this.preview.set(null);
  }

  confirmUpload() {
    const file = this.pendingFile();
    if (!file) return;
    this.uploading.set(true);
    this.api.scorecardUpload(file).subscribe({
      next: (r) => {
        this.uploadInfo.set(r);
        this.uploading.set(false);
        this.pendingFile.set(null);
        this.preview.set(null);
        this.refreshAll();
      },
      error: (e) => {
        this.error.set(e?.error?.detail || 'Upload failed');
        this.uploading.set(false);
      },
    });
  }

  clearAll() {
    if (!confirm('Wipe all uploaded scorecard data? This cannot be undone.')) return;
    this.api.scorecardClear().subscribe({
      next: () => { this.uploadInfo.set(null); this.refreshAll(); },
      error: (e) => this.error.set(e?.error?.detail || 'Clear failed'),
    });
  }

  // ── Weights ─────────────────────────────────────────────────────────────
  setWeight(cat: string, val: number) {
    const w = { ...this.weights(), [cat]: Number(val) };
    this.weights.set(w);
    this.activePreset.set('custom');
  }

  weightTotal = computed(() =>
    this.CATEGORIES.reduce((s, c) => s + (this.weights()[c.key] || 0), 0)
  );

  applyPreset(name: string) {
    const presets = this.presets()?.presets;
    if (!presets || !presets[name]) return;
    this.weights.set({ ...presets[name] });
    this.activePreset.set(name);
    this.loadLeaderboard();
  }

  resetWeights() {
    const d = this.presets()?.default_weights;
    if (d) {
      this.weights.set({ ...d });
      this.activePreset.set('quality');
      this.loadLeaderboard();
    }
  }

  applyWeights() { this.loadLeaderboard(); }

  // ── Filters ─────────────────────────────────────────────────────────────
  setCapTier(t: 'all' | 'large' | 'mid' | 'small') {
    this.capTier.set(t);
    this.loadLeaderboard();
  }
  setSubSector(s: string) {
    this.subSector.set(s);
    this.loadLeaderboard();
  }
  onSearchChange(v: string) {
    this.search.set(v);
    // Debounced reload via signal — fire on enter or blur
  }
  applySearch() { this.loadLeaderboard(); }

  // ── Sorting ─────────────────────────────────────────────────────────────
  setSort(col: SortKey) {
    if (this.sortBy() === col) {
      this.sortDir.set(this.sortDir() === 'asc' ? 'desc' : 'asc');
    } else {
      this.sortBy.set(col);
      this.sortDir.set(col === 'rank' || col === 'ticker' || col === 'sub_sector' ? 'asc' : 'desc');
    }
  }

  sortedRows = computed(() => {
    const rows = [...this.rows()];
    const key  = this.sortBy();
    const dir  = this.sortDir() === 'asc' ? 1 : -1;
    rows.sort((a, b) => {
      let av: any, bv: any;
      if (key === 'rank' || key === 'ticker' || key === 'sub_sector') {
        av = a[key]; bv = b[key];
      } else if (key === 'composite') {
        av = a.composite ?? -1; bv = b.composite ?? -1;
      } else {
        av = a.categories?.[key]?.score ?? -1;
        bv = b.categories?.[key]?.score ?? -1;
      }
      if (typeof av === 'string') return av.localeCompare(bv) * dir;
      return ((av ?? 0) - (bv ?? 0)) * dir;
    });
    return rows;
  });

  // ── Detail modal ────────────────────────────────────────────────────────
  openStock(ticker: string) {
    this.api.scorecardStock(ticker, this.weights()).subscribe({
      next: (r) => this.selected.set(r),
      error: () => this.selected.set(null),
    });
  }
  closeStock() { this.selected.set(null); }

  // ── Helpers ─────────────────────────────────────────────────────────────
  /** All unique sub-sectors from the loaded master. */
  subSectorOptions = computed(() => {
    const s = this.summary()?.sectors as Array<{sub_sector: string; count: number}> | undefined;
    return s ? s.map(x => x.sub_sector) : [];
  });

  /** Color the score: 70+ green, 40–70 amber, <40 red. */
  scoreClass(s: number | null | undefined): string {
    if (s == null) return 'na';
    if (s >= 70) return 'good';
    if (s >= 40) return 'mid';
    return 'bad';
  }

  fmtScore(s: number | null | undefined): string {
    if (s == null) return '—';
    return s.toFixed(0);
  }

  fmtMetric(v: number | null | undefined): string {
    if (v == null || isNaN(v as any)) return '—';
    const n = Number(v);
    if (Math.abs(n) >= 1000) return n.toLocaleString('en-IN', { maximumFractionDigits: 0 });
    if (Math.abs(n) >= 10)   return n.toFixed(1);
    return n.toFixed(2);
  }

  fmtMcap(v: number | null | undefined): string {
    if (v == null) return '—';
    const n = Number(v);
    if (n >= 1_00_000) return `₹${(n / 1_00_000).toFixed(2)} L Cr`;
    if (n >= 1_000)    return `₹${(n / 1_000).toFixed(1)}K Cr`;
    return `₹${n.toFixed(0)} Cr`;
  }

  capTierLabel(t: string): string {
    return t === 'large' ? 'Large Cap' : t === 'mid' ? 'Mid Cap' : t === 'small' ? 'Small Cap' : '—';
  }

  fmtSize(b: number): string {
    if (b == null) return '—';
    if (b >= 1024 * 1024) return `${(b / 1024 / 1024).toFixed(1)} MB`;
    if (b >= 1024)        return `${(b / 1024).toFixed(0)} KB`;
    return `${b} B`;
  }

  fmtUploadedAt(iso: string): string {
    if (!iso) return '—';
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    const now = new Date();
    const sameDay = d.toDateString() === now.toDateString();
    if (sameDay) {
      return `Today ${d.getHours().toString().padStart(2, '0')}:${d.getMinutes().toString().padStart(2, '0')}`;
    }
    return d.toLocaleString('en-IN', { day: '2-digit', month: 'short', year: 'numeric',
                                       hour: '2-digit', minute: '2-digit' });
  }

  /** Get a category cell from a row */
  catScore(row: any, key: CategoryKey): number | null {
    return row?.categories?.[key]?.score ?? null;
  }
  catTracked(row: any, key: CategoryKey): { tracked: number; total: number } {
    const c = row?.categories?.[key];
    return { tracked: c?.tracked ?? 0, total: c?.total ?? 0 };
  }

  // ── Radar coordinates for the detail modal (6 category scores) ─────────
  radarPath = computed(() => {
    const s = this.selected();
    if (!s) return '';
    const cx = 130, cy = 130, R = 100;
    const pts: string[] = [];
    this.CATEGORIES.forEach((c, i) => {
      const score = s.categories?.[c.key]?.score ?? 0;
      const angle = (i / this.CATEGORIES.length) * 2 * Math.PI - Math.PI / 2;
      const r = (score / 100) * R;
      const x = cx + r * Math.cos(angle);
      const y = cy + r * Math.sin(angle);
      pts.push(`${x.toFixed(1)},${y.toFixed(1)}`);
    });
    return pts.join(' ');
  });

  radarAxes = computed(() => {
    const cx = 130, cy = 130, R = 100;
    return this.CATEGORIES.map((c, i) => {
      const angle = (i / this.CATEGORIES.length) * 2 * Math.PI - Math.PI / 2;
      const x = cx + R * Math.cos(angle);
      const y = cy + R * Math.sin(angle);
      const lx = cx + (R + 18) * Math.cos(angle);
      const ly = cy + (R + 18) * Math.sin(angle);
      return { x, y, lx, ly, label: c.short, key: c.key, color: c.color };
    });
  });

  radarGrid = [25, 50, 75, 100];

  /** Per-category dot positions for the radar chart in the detail modal. */
  radarDots = computed(() => {
    const s = this.selected();
    if (!s) return [];
    const cx = 130, cy = 130, R = 100;
    return this.CATEGORIES.map((c, i) => {
      const score = s.categories?.[c.key]?.score;
      if (score == null) return null;
      const angle = (i / this.CATEGORIES.length) * 2 * Math.PI - Math.PI / 2;
      const r = (score / 100) * R;
      return {
        key: c.key,
        color: c.color,
        x: cx + r * Math.cos(angle),
        y: cy + r * Math.sin(angle),
      };
    }).filter((d): d is { key: CategoryKey; color: string; x: number; y: number } => d !== null);
  });

  /** Detail modal metric percentile for the bar. Higher score = filled more. */
  metricPctFill(score: number | null | undefined): number {
    return score == null ? 0 : Math.max(0, Math.min(100, score));
  }
}
