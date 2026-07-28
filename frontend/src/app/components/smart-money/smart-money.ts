import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { CommonModule, DecimalPipe } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ApiService } from '../../services/api.service';

/**
 * Smart Money dashboard.
 *
 *  • Upload a CSV chunk of bulk / block / SAST disclosures (≤ 5,000 rows).
 *  • Server merges + dedupes against a master file.
 *  • Dashboard then renders:
 *      - header KPIs (total trades, parties, ₹ flowed, date range)
 *      - hottest stocks ranked by net inflow / breadth / activity / sells
 *      - category split (FII vs DII vs MF vs Promoter vs Other)
 *      - top parties by activity (foundation for a leaderboard later)
 *      - one-click drill-down per stock (all trades + daily flow chart)
 *
 *  All graphs are pure SVG, no chart library. Colors follow the global
 *  green/red/blue palette from the rest of the app.
 */
@Component({
  selector: 'app-smart-money',
  imports: [CommonModule, DecimalPipe, FormsModule],
  templateUrl: './smart-money.html',
  styleUrl: './smart-money.scss',
})
export class SmartMoneyComponent implements OnInit {
  private api = inject(ApiService);

  // ── State ───────────────────────────────────────────────────────────────
  summary       = signal<any | null>(null);
  hottest       = signal<any[]>([]);
  categories    = signal<any[]>([]);
  parties       = signal<any[]>([]);
  selectedStock = signal<any | null>(null);

  loading       = signal(false);
  uploading     = signal(false);
  previewing    = signal(false);
  error         = signal('');
  uploadInfo    = signal<any | null>(null);

  // Preview-before-save state — null when no preview pending
  pendingFile   = signal<File | null>(null);
  preview       = signal<any | null>(null);
  /** Live mapping the user is editing (canonical_field → original CSV column).
   *  Sent to the backend as `mapping_override` on re-preview and save.        */
  editMapping   = signal<Record<string, string>>({});

  // ── Filters ─────────────────────────────────────────────────────────────
  period = signal<number>(90);                    // days
  sortBy = signal<'net_flow' | 'buy_value' | 'net_breadth' | 'deal_count' | 'net_flow_neg'>('net_flow');

  PERIODS = [7, 30, 90, 180, 365, 0];

  PERIOD_LABEL = (d: number) =>
    d === 0 ? 'All time' : d === 7 ? '7d' : d === 30 ? '30d'
    : d === 90 ? '90d' : d === 180 ? '6m' : d === 365 ? '1y' : `${d}d`;

  SORT_OPTIONS: {key: any; label: string; sub: string}[] = [
    { key: 'net_flow',     label: 'Top Buying',  sub: 'highest net ₹ inflow' },
    { key: 'net_flow_neg', label: 'Top Selling', sub: 'highest net ₹ outflow' },
    { key: 'net_breadth',  label: 'Breadth',     sub: 'most unique buyers vs sellers' },
    { key: 'buy_value',    label: 'Money In',    sub: 'highest total ₹ bought' },
    { key: 'deal_count',   label: 'Most Active', sub: 'most deals' },
  ];

  // ── Lifecycle ───────────────────────────────────────────────────────────
  ngOnInit() {
    this.refreshAll();
  }

  refreshAll() {
    this.loading.set(true);
    this.error.set('');
    this.api.smartMoneySummary().subscribe({
      next:  (r) => this.summary.set(r),
      error: (e) => this.error.set(e?.error?.detail || 'Failed to load summary'),
    });
    this._loadHottest();
    this._loadCategories();
    this._loadParties();
  }

  setPeriod(d: number)     { this.period.set(d); this._loadHottest(); this._loadCategories(); this._loadParties(); }
  setSortBy(s: any)        { this.sortBy.set(s); this._loadHottest(); }

  private _loadHottest() {
    this.api.smartMoneyHottest({ days: this.period() || undefined, limit: 25, sortBy: this.sortBy() }).subscribe({
      next: (r) => { this.hottest.set(r?.rows ?? []); this.loading.set(false); },
      error: ()  => { this.hottest.set([]); this.loading.set(false); },
    });
  }
  private _loadCategories() {
    this.api.smartMoneyCategories(this.period() || undefined).subscribe({
      next: (r) => this.categories.set(r?.categories ?? []),
      error: () => this.categories.set([]),
    });
  }
  private _loadParties() {
    this.api.smartMoneyParties({ days: this.period() || undefined, limit: 15 }).subscribe({
      next: (r) => this.parties.set(r?.rows ?? []),
      error: () => this.parties.set([]),
    });
  }

  // ── Upload (two-step: preview first, user confirms, then save) ─────────
  /** Canonical fields we expose in the editable mapping UI, in display order. */
  readonly MAP_FIELDS: { k: string; l: string; essential: boolean }[] = [
    { k: 'stock',           l: 'Stock',                 essential: true  },
    { k: 'date',            l: 'Date',                  essential: true  },
    { k: 'party',           l: 'Party (Investor name)', essential: true  },
    { k: 'category',        l: 'Category',              essential: false },
    { k: 'txn_type',        l: 'Txn type (buy/sell)',   essential: true  },
    { k: 'price',           l: 'Avg trade price',       essential: false },
    { k: 'value',           l: 'Value traded',          essential: true  },
    { k: 'quantity',        l: 'Quantity',              essential: false },
    { k: 'holdings_change', l: 'Holdings change',       essential: false },
  ];

  onUploadChange(ev: Event) {
    const input = ev.target as HTMLInputElement;
    const file = input?.files?.[0];
    if (!file) return;
    this.pendingFile.set(file);
    this.preview.set(null);
    this.uploadInfo.set(null);
    this.error.set('');
    this.previewing.set(true);
    this.api.smartMoneyPreview(file).subscribe({
      next: (r) => {
        this.preview.set(r);
        // Initialize the editable mapping with what auto-detected.
        this.editMapping.set({ ...(r?.header_mapping || {}) });
        this.previewing.set(false);
      },
      error: (e) => {
        this.error.set(e?.error?.detail || e?.message || 'Preview failed');
        this.previewing.set(false);
        this.pendingFile.set(null);
      },
    });
    input.value = '';                        // allow re-upload of same file
  }

  /** User picked a different CSV column for a canonical field. */
  setMapping(canonical: string, csvColumn: string) {
    const next = { ...this.editMapping() };
    if (csvColumn === '') {
      delete next[canonical];                // "— Don't use —"
    } else {
      next[canonical] = csvColumn;
    }
    this.editMapping.set(next);
  }

  /** True if the user has modified the auto-detected mapping. */
  mappingDirty = computed(() => {
    const orig = this.preview()?.header_mapping || {};
    const edit = this.editMapping();
    const keys = new Set([...Object.keys(orig), ...Object.keys(edit)]);
    for (const k of keys) if (orig[k] !== edit[k]) return true;
    return false;
  });

  /** Re-run preview with the user's edited mapping override. */
  repreview() {
    const file = this.pendingFile();
    if (!file) return;
    this.previewing.set(true);
    this.api.smartMoneyPreview(file, this.editMapping()).subscribe({
      next: (r) => {
        this.preview.set(r);
        // Keep the user's edited mapping intact (don't clobber with auto-detect).
        this.previewing.set(false);
      },
      error: (e) => {
        this.error.set(e?.error?.detail || e?.message || 'Re-preview failed');
        this.previewing.set(false);
      },
    });
  }

  /** User cancelled at the preview step — discard. */
  cancelPreview() {
    this.pendingFile.set(null);
    this.preview.set(null);
    this.editMapping.set({});
  }

  /** User confirmed the preview looks right — commit it. */
  confirmUpload() {
    const file = this.pendingFile();
    if (!file) return;
    this.uploading.set(true);
    this.api.smartMoneyUpload(file, this.editMapping()).subscribe({
      next: (r) => {
        this.uploadInfo.set(r);
        this.uploading.set(false);
        this.pendingFile.set(null);
        this.preview.set(null);
        this.editMapping.set({});
        this.refreshAll();
      },
      error: (e) => {
        this.error.set(e?.error?.detail || 'Upload failed');
        this.uploading.set(false);
      },
    });
  }

  /** Pretty-format a parsed sample value for the preview table. */
  fmtPreview(v: any): string {
    if (v === null || v === undefined || v === '') return '—';
    return String(v);
  }

  // ── Drill-down ──────────────────────────────────────────────────────────
  openStock(ticker: string) {
    this.api.smartMoneyStock(ticker, this.period() || undefined).subscribe({
      next:  (r) => this.selectedStock.set(r),
      error: ()  => this.selectedStock.set(null),
    });
  }
  closeStock() { this.selectedStock.set(null); }

  // ── Derived / helpers ───────────────────────────────────────────────────
  /** Max absolute net_flow in the hottest list — used to scale bars. */
  hottestMaxAbs = computed(() => {
    const rows = this.hottest();
    if (!rows.length) return 0;
    if (this.sortBy() === 'net_breadth')
      return Math.max(...rows.map(r => Math.abs(r.net_breadth ?? 0)), 1);
    if (this.sortBy() === 'deal_count')
      return Math.max(...rows.map(r => r.deal_count ?? 0), 1);
    if (this.sortBy() === 'buy_value')
      return Math.max(...rows.map(r => r.buy_value ?? 0), 1);
    return Math.max(...rows.map(r => Math.abs(r.net_flow ?? 0)), 1);
  });

  /** What value drives the bar length for the current sort. */
  barValue(row: any): number {
    switch (this.sortBy()) {
      case 'net_breadth':  return row.net_breadth ?? 0;
      case 'deal_count':   return row.deal_count ?? 0;
      case 'buy_value':    return row.buy_value ?? 0;
      case 'net_flow_neg': return -(row.net_flow ?? 0);  // flip so most-sold has longest bar
      default:             return row.net_flow ?? 0;
    }
  }

  /** % width 0..100 — clamped so a single outlier doesn't dwarf everything. */
  barPct(row: any): number {
    const v   = this.barValue(row);
    const max = this.hottestMaxAbs() || 1;
    return Math.min(100, Math.max(0, (Math.abs(v) / max) * 100));
  }

  isPositive(row: any): boolean {
    if (this.sortBy() === 'net_flow_neg') return false;
    if (this.sortBy() === 'net_breadth')  return (row.net_breadth ?? 0) >= 0;
    return (row.net_flow ?? 0) >= 0;
  }

  // ── Compact ₹ formatter ─────────────────────────────────────────────────
  /** Convert a raw rupee amount to "₹X Cr" / "₹X L" / etc. for compact display. */
  fmtRs(val: number | null | undefined): string {
    if (val == null || isNaN(val as any)) return '—';
    const n = Number(val);
    const abs = Math.abs(n);
    if (abs >= 1_00_00_000) return `${n < 0 ? '−' : ''}₹${(abs / 1_00_00_000).toFixed(2)} Cr`;
    if (abs >= 1_00_000)    return `${n < 0 ? '−' : ''}₹${(abs / 1_00_000).toFixed(2)} L`;
    if (abs >= 1_000)       return `${n < 0 ? '−' : ''}₹${(abs / 1_000).toFixed(1)}K`;
    return `${n < 0 ? '−' : ''}₹${Math.round(abs).toLocaleString('en-IN')}`;
  }

  // ── Category donut math ─────────────────────────────────────────────────
  donutTotal = computed(() =>
    this.categories().reduce((sum, c) => sum + (c.buy ?? 0), 0)
  );

  /** Build SVG arc segments for the donut. Each segment in the order given,
   *  with a stable color palette so consecutive renders keep colors. */
  donutSegments = computed(() => {
    const total = this.donutTotal();
    if (!total) return [];
    const palette = ['#2dbb7d', '#4d9aff', '#d9a441', '#e0556b', '#a78bfa',
                     '#06b6d4', '#f97316', '#10b981', '#8b5cf6', '#94a3b8'];
    let cumulative = 0;
    const r  = 60;
    const cx = 80, cy = 80;
    return this.categories()
      .filter(c => (c.buy ?? 0) > 0)
      .sort((a, b) => (b.buy ?? 0) - (a.buy ?? 0))
      .map((c, i) => {
        const frac = (c.buy ?? 0) / total;
        const start = cumulative;
        cumulative += frac;
        const startAngle = start  * 2 * Math.PI - Math.PI / 2;
        const endAngle   = cumulative * 2 * Math.PI - Math.PI / 2;
        const x1 = cx + r * Math.cos(startAngle), y1 = cy + r * Math.sin(startAngle);
        const x2 = cx + r * Math.cos(endAngle),   y2 = cy + r * Math.sin(endAngle);
        const largeArc = frac > 0.5 ? 1 : 0;
        const d = `M ${cx} ${cy} L ${x1.toFixed(2)} ${y1.toFixed(2)} ` +
                  `A ${r} ${r} 0 ${largeArc} 1 ${x2.toFixed(2)} ${y2.toFixed(2)} Z`;
        return {
          category: c.category,
          buy: c.buy,
          sell: c.sell,
          net: c.net,
          pct: frac * 100,
          color: palette[i % palette.length],
          d,
        };
      });
  });

  // ── Daily-flow sparkline path for the drill-down modal ──────────────────
  drilldownPath = computed(() => {
    const s = this.selectedStock();
    const pts = s?.daily_flow as Array<{date: string; net: number}> | undefined;
    if (!pts || pts.length < 2) return '';
    const w = 480, h = 110, pad = 6;
    const ys = pts.map(p => p.net);
    const min = Math.min(...ys), max = Math.max(...ys);
    const range = (max - min) || 1;
    const stepX = (w - pad * 2) / (pts.length - 1);
    let d = '';
    pts.forEach((p, i) => {
      const x = pad + i * stepX;
      const y = pad + (1 - (p.net - min) / range) * (h - pad * 2);
      d += (i === 0 ? 'M' : 'L') + ' ' + x.toFixed(2) + ' ' + y.toFixed(2) + ' ';
    });
    return d;
  });

  drilldownZeroLine = computed(() => {
    const s = this.selectedStock();
    const pts = s?.daily_flow as Array<{date: string; net: number}> | undefined;
    if (!pts || pts.length < 2) return null;
    const w = 480, h = 110, pad = 6;
    const ys = pts.map(p => p.net);
    const min = Math.min(...ys), max = Math.max(...ys);
    if (min >= 0 || max <= 0) return null;
    const y = pad + (1 - (0 - min) / ((max - min) || 1)) * (h - pad * 2);
    return { y, w };
  });
}
