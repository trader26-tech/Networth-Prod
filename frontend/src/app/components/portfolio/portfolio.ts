import {
  Component, ElementRef, OnDestroy, OnInit, ViewChild, inject, signal, computed, effect,
} from '@angular/core';
import { CommonModule, DecimalPipe } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ApiService } from '../../services/api.service';
import { SnowflakeRadarComponent } from '../snowflake-radar/snowflake-radar';
import { SparklineComponent } from '../sparkline/sparkline';
import { StockDetailComponent } from '../stock-detail/stock-detail';

@Component({
  selector: 'app-portfolio',
  imports: [CommonModule, DecimalPipe, FormsModule, SnowflakeRadarComponent, SparklineComponent, StockDetailComponent],
  templateUrl: './portfolio.html',
  styleUrl: './portfolio.scss',
})
export class PortfolioComponent implements OnInit, OnDestroy {
  private api = inject(ApiService);
  private readonly CACHE_KEY = 'portfolio_cache_v1';
  private readonly LIVE_POLL_MS = 60_000;  // 60s — Kite LTP refresh cadence
  private livePollTimer: any = null;
  /** Sorted+joined ticker list from the last snowflake load. Skip the next
   *  load if it would resend the same set. */
  private _lastSnowflakeKey = '';
  /** True while a snowflake batch is in flight — prevents overlapping loads. */
  private _snowflakeInFlight = false;

  result      = signal<any | null>(null);
  loading     = signal(false);
  error       = signal('');
  fileName    = signal('');
  isDragging  = signal(false);
  refreshing  = signal(false);
  lastFile    = signal<File | null>(null);

  // ── Cache state ───────────────────────────────────────────────────────────
  isFromCache    = signal(false);
  cacheTimestamp = signal('');

  // ── Preview / format-fix state ────────────────────────────────────────────
  previewLoading = signal(false);
  preview        = signal<{ rows: any[][]; total_rows: number; total_cols: number } | null>(null);
  pendingFile    = signal<File | null>(null);
  skipRows       = signal(0);
  dropFirstCols  = signal(0);
  dropLastCols   = signal(0);

  // ── Small-position filter ─────────────────────────────────────────────────
  hideSmall          = signal(false);
  smallThresholdPct  = 0.5;          // positions below this % weight are "small"

  // ── History chart ─────────────────────────────────────────────────────────
  historyData    = signal<any[]>([]);
  historyLoading = signal(false);
  historyError   = signal('');
  historyRange   = signal<number>(84);   // days

  // ── Signals (buy / sell) ──────────────────────────────────────────────────
  signals        = signal<{ buys: any[]; sells: any[]; analyzed: number } | null>(null);
  signalsLoading = signal(false);
  signalsError   = signal('');

  // ── Snowflake fundamentals + live LTP ─────────────────────────────────────
  snowflakes        = signal<Map<string, any>>(new Map());
  snowflakeLoading  = signal(false);
  snowflakeError    = signal('');
  snowflakeMissing  = signal<string[]>([]);

  liveQuotes        = signal<Map<string, number>>(new Map());
  liveLastUpdate    = signal<string>('');
  liveConnected     = signal(false);

  // ── Stock-detail modal ────────────────────────────────────────────────────
  selectedRow       = signal<any | null>(null);
  openDetail(row: any)  { this.selectedRow.set(row); }
  closeDetail()         { this.selectedRow.set(null); }

  // ── 1Y price sparklines (yfinance, cached server-side) ───────────────────
  sparklines        = signal<Map<string, any>>(new Map());
  sparklinesLoading = signal(false);
  private _sparklineKeys = new Set<string>();   // tickers we've already requested

  // Bucket filter for the snowflake table. Defaults to Keep so the user lands
  // on their strongest positions instead of a wall of EXIT-tagged rows.
  // If the Keep bucket is empty after loading, we auto-fall-back to ALL.
  snowflakeBucket   = signal<'ALL' | 'KEEP_AND_ADD' | 'HOLD' | 'TRIM' | 'EXIT' | 'INSUFFICIENT_DATA'>('KEEP_AND_ADD');

  // True when the backend says the Snowflake universe isn't loaded on the
  // server (e.g. production deploy without the Tickertape CSV). UI shows a
  // friendly empty-state banner in that case.
  snowflakeUnavailable = signal(false);

  // Holdings joined with snowflake + live LTP, derived for the fundamentals table.
  fundamentalsRows = computed<any[]>(() => {
    const holdings = this.visibleHoldings();
    const snowMap  = this.snowflakes();
    const liveMap  = this.liveQuotes();
    const sparkMap = this.sparklines();
    const bucket   = this.snowflakeBucket();
    const rows = holdings.map((h: any) => {
      const sym = (h.symbol || h.share || '').toString().toUpperCase().trim();
      const snow = snowMap.get(sym) ?? null;
      const live = liveMap.get(sym);
      const spark = sparkMap.get(sym) ?? null;
      // Use live LTP if available, else fall back to whatever was loaded with the holding.
      const ltp = (live ?? h.current_price ?? snow?.close_price) || null;
      const live_value = ltp != null && h.qty != null ? ltp * h.qty : (h.current_value ?? null);
      const live_pnl   = ltp != null && h.avg_cmp != null && h.qty != null
        ? (ltp - h.avg_cmp) * h.qty : (h.pnl ?? null);
      const live_pnl_pct = ltp != null && h.avg_cmp ? ((ltp - h.avg_cmp) / h.avg_cmp) * 100 : (h.pnl_pct ?? null);
      return {
        symbol: sym,
        share: h.share || sym,
        sector: h.sector,
        qty: h.qty,
        avg_cmp: h.avg_cmp,
        weight_pct: h.weight_pct,
        ltp,
        live_value,
        live_pnl,
        live_pnl_pct,
        is_live: live != null,
        snow,
        spark,
      };
    });
    if (bucket === 'ALL') return rows;
    return rows.filter(r => r.snow?.bucket === bucket);
  });

  snowflakeBucketCounts = computed<Record<string, number>>(() => {
    const counts: Record<string, number> = {
      KEEP_AND_ADD: 0, HOLD: 0, TRIM: 0, EXIT: 0, UNSCORED: 0,
    };
    const snowMap = this.snowflakes();
    for (const h of this.visibleHoldings()) {
      const sym = (h.symbol || h.share || '').toString().toUpperCase().trim();
      const snow = snowMap.get(sym);
      const key = snow?.bucket ?? 'UNSCORED';
      counts[key] = (counts[key] || 0) + 1;
    }
    return counts;
  });

  // ── Computed ──────────────────────────────────────────────────────────────
  trimmedPreview = computed(() => {
    const p = this.preview();
    if (!p) return null;
    const skip   = Math.max(0, this.skipRows());
    const dropF  = Math.max(0, this.dropFirstCols());
    const dropL  = Math.max(0, this.dropLastCols());
    const colEnd = dropL > 0 ? Math.max(dropF, p.total_cols - dropL) : p.total_cols;
    const slicedRows = p.rows.slice(skip).map(r => r.slice(dropF, colEnd));
    const header = slicedRows[0] ?? [];
    const body   = slicedRows.slice(1, 21);
    return {
      header,
      body,
      remaining_rows: Math.max(0, p.total_rows - skip - 1),
      remaining_cols: Math.max(0, colEnd - dropF),
      truncated: p.rows.length < p.total_rows,
    };
  });

  visibleHoldings = computed<any[]>(() => {
    const h = this.result()?.holdings ?? [];
    if (!this.hideSmall()) return h;
    return h.filter((hh: any) => hh.weight_pct >= this.smallThresholdPct);
  });

  hiddenCount = computed<number>(() => {
    const h = this.result()?.holdings ?? [];
    if (!this.hideSmall()) return 0;
    return h.filter((hh: any) => hh.weight_pct < this.smallThresholdPct).length;
  });

  // ── Chart tab toggle ─────────────────────────────────────────────────────
  chartTab = signal<'winners' | 'losers' | 'contrib'>('winners');

  @ViewChild('combinedCanvas')  combinedCanvasRef?:  ElementRef<HTMLCanvasElement>;
  @ViewChild('historyCanvas')   historyCanvasRef?:   ElementRef<HTMLCanvasElement>;
  @ViewChild('sectorCanvas')    sectorCanvasRef?:    ElementRef<HTMLCanvasElement>;

  // Only show the 2 top-line KPI cards
  filteredInsights = computed(() =>
    (this.result()?.insights ?? []).filter((i: any) => i.id === 'total_pnl' || i.id === 'current_value')
  );

  sectorBreakdown = computed<{ sector: string; value: number; pct: number }[]>(() => {
    const h = this.result()?.holdings ?? [];
    const map: Record<string, number> = {};
    h.forEach((hh: any) => { map[hh.sector] = (map[hh.sector] || 0) + (hh.current_value || 0); });
    const total = Object.values(map).reduce((a, b) => a + b, 0) || 1;
    return Object.entries(map)
      .map(([sector, value]) => ({ sector, value, pct: (value / total) * 100 }))
      .sort((a, b) => b.value - a.value);
  });

  constructor() {
    effect(() => {
      const r = this.result();
      if (r) setTimeout(() => this.drawAllCharts(), 60);
    });
    effect(() => {
      const tab = this.chartTab();
      const r   = this.result();
      if (r && tab) setTimeout(() => this.drawCombinedChart(), 30);
    });
    effect(() => {
      if (this.historyData().length) setTimeout(() => this.drawHistoryChart(), 30);
    });

    // When holdings appear, auto-fetch Snowflake scores + start live LTP polling.
    effect(() => {
      const r = this.result();
      if (!r?.holdings?.length) {
        this._stopLivePolling();
        return;
      }
      this.loadSnowflakesForHoldings();
      this._startLivePolling();
    });

    // Auto-fetch sparklines whenever the visible fundamentals rows change
    // (e.g. user switches bucket filter). Dedup is handled inside the loader.
    effect(() => {
      // Touch fundamentalsRows() so the effect re-runs when it changes.
      const rows = this.fundamentalsRows();
      if (rows.length) {
        // setTimeout 0 — avoid running inside the same change-detection tick.
        setTimeout(() => this.loadSparklinesForVisible(), 0);
      }
    });
  }

  ngOnInit() {
    // Restore the local copy first so the UI snaps in immediately, then
    // hydrate from the server in the background. If the server has newer
    // data, we overwrite — this is how a second device picks up holdings
    // uploaded from a different device.
    this._restoreFromCache();
    this._fetchSavedFromServer();
  }

  /** Pull the persisted snapshot from the API and replace local state if newer. */
  private _fetchSavedFromServer() {
    this.api.fetchSavedPortfolio().subscribe({
      next: (resp: any) => {
        if (!resp?.available || !resp?.result) return;
        const serverTime = resp.updated_at ? new Date(resp.updated_at).getTime() : 0;
        const localTime  = this.cacheTimestamp() ? new Date(this.cacheTimestamp()).getTime() : 0;
        // If the server snapshot is newer (or local is empty), adopt it.
        if (serverTime > localTime) {
          this.result.set(resp.result);
          this.cacheTimestamp.set(resp.updated_at || '');
          this.isFromCache.set(false);
          this.fileName.set(resp.file_name || this.fileName());
        }
      },
      error: () => { /* server unavailable or empty — keep local state */ },
    });
  }

  /** Push the analyzed result to the server so other devices can pick it up. */
  private _pushSavedToServer(result: any, fileName: string) {
    this.api.saveSavedPortfolio(result, fileName).subscribe({
      next: () => { /* silent — best effort */ },
      error: () => { /* swallow — local cache still holds it */ },
    });
  }

  // ── Cache helpers ─────────────────────────────────────────────────────────
  private _restoreFromCache() {
    try {
      const raw = localStorage.getItem(this.CACHE_KEY);
      if (!raw) return;
      const c = JSON.parse(raw);
      if (!c?.result) return;
      this.result.set(c.result);
      this.fileName.set(c.fileName || '');
      this.skipRows.set(c.skipRows ?? 0);
      this.dropFirstCols.set(c.dropFirstCols ?? 0);
      this.dropLastCols.set(c.dropLastCols ?? 0);
      this.isFromCache.set(true);
      this.cacheTimestamp.set(c.savedAt || '');
      if (c.fileB64 && c.fileName) {
        this.lastFile.set(this._b64ToFile(c.fileB64, c.fileName));
      }
    } catch { /* ignore */ }
  }

  private async _saveToCache(file: File, result: any, fmtOpts: { skipRows: number; dropFirstCols: number; dropLastCols: number }) {
    try {
      const fileB64 = await this._fileToB64(file);
      const savedAt = new Date().toISOString();
      localStorage.setItem(this.CACHE_KEY, JSON.stringify({ result, fileName: file.name, ...fmtOpts, savedAt, fileB64 }));
      this.isFromCache.set(false);
      this.cacheTimestamp.set(savedAt);
    } catch { /* ignore */ }
  }

  private _clearCache() {
    try { localStorage.removeItem(this.CACHE_KEY); } catch { /* ignore */ }
    this.isFromCache.set(false);
    this.cacheTimestamp.set('');
  }

  private _fileToB64(file: File): Promise<string> {
    return new Promise((resolve, reject) => {
      const r = new FileReader();
      r.onload  = () => { const s = r.result as string; resolve(s.includes(',') ? s.split(',')[1] : s); };
      r.onerror = reject;
      r.readAsDataURL(file);
    });
  }

  private _b64ToFile(b64: string, name: string): File {
    const bin = atob(b64);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    return new File([bytes], name, { type: 'application/octet-stream' });
  }

  // ── File handling ─────────────────────────────────────────────────────────
  onFileSelected(e: Event) {
    const input = e.target as HTMLInputElement;
    if (input.files?.length) this.uploadFile(input.files[0]);
  }

  onDragOver(e: DragEvent) { e.preventDefault(); this.isDragging.set(true); }
  onDragLeave(e: DragEvent) { e.preventDefault(); this.isDragging.set(false); }
  onDrop(e: DragEvent) {
    e.preventDefault(); this.isDragging.set(false);
    if (e.dataTransfer?.files?.length) this.uploadFile(e.dataTransfer.files[0]);
  }

  uploadFile(file: File) {
    if (!file.name.match(/\.(xlsx|xls|csv)$/i)) {
      this.error.set('Please upload an .xlsx, .xls or .csv file'); return;
    }
    this.fileName.set(file.name);
    this.pendingFile.set(file);
    this.skipRows.set(0); this.dropFirstCols.set(0); this.dropLastCols.set(0);
    this.previewLoading.set(true); this.error.set(''); this.result.set(null);
    this.api.previewPortfolio(file).subscribe({
      next: (p) => { this.preview.set(p); this.previewLoading.set(false); },
      error: (e) => { this.error.set(e?.error?.detail || 'Could not parse the file'); this.previewLoading.set(false); },
    });
  }

  onSkipRowsChange(v: any)      { this.skipRows.set(this._clampInt(v, 0, 20)); }
  onDropFirstColsChange(v: any) { this.dropFirstCols.set(this._clampInt(v, 0, 20)); }
  onDropLastColsChange(v: any)  { this.dropLastCols.set(this._clampInt(v, 0, 20)); }
  bumpSkipRows(d: number)       { this.skipRows.update(v => Math.max(0, Math.min(20, v + d))); }
  bumpDropFirstCols(d: number)  { this.dropFirstCols.update(v => Math.max(0, Math.min(20, v + d))); }
  bumpDropLastCols(d: number)   { this.dropLastCols.update(v => Math.max(0, Math.min(20, v + d))); }
  resetFormatFixes()            { this.skipRows.set(0); this.dropFirstCols.set(0); this.dropLastCols.set(0); }
  private _clampInt(v: any, lo: number, hi: number): number {
    const n = Math.round(Number(v));
    return Number.isFinite(n) ? Math.max(lo, Math.min(hi, n)) : lo;
  }

  cancelPreview() {
    this.preview.set(null); this.pendingFile.set(null);
    this.fileName.set(''); this.error.set('');
  }

  confirmAndAnalyze() {
    const file = this.pendingFile();
    if (!file) return;
    this.lastFile.set(file);
    this.loading.set(true); this.error.set('');
    const fmtOpts = { skipRows: this.skipRows(), dropFirstCols: this.dropFirstCols(), dropLastCols: this.dropLastCols() };
    this.api.analyzePortfolio(file, fmtOpts).subscribe({
      next: (r) => {
        this.result.set(r);
        this.loading.set(false);
        this.preview.set(null); this.pendingFile.set(null);
        this._saveToCache(file, r, fmtOpts);
        // Push to server so other devices pick it up — best effort.
        this._pushSavedToServer(r, file.name);
        this.historyData.set([]); this.signals.set(null);
      },
      error: (e) => {
        this.error.set(e?.error?.detail || 'Upload failed — make sure the backend is running and Kite is connected');
        this.loading.set(false);
      },
    });
  }

  refreshPrices() {
    const f = this.lastFile();
    if (!f) return;
    this.refreshing.set(true);
    this.api.analyzePortfolio(f, {
      skipRows: this.skipRows(), dropFirstCols: this.dropFirstCols(), dropLastCols: this.dropLastCols(),
    }).subscribe({
      next: (r) => {
        this.result.set(r);
        this.refreshing.set(false);
        // Refresh also updates the server snapshot — fresh prices for other devices.
        this._pushSavedToServer(r, f.name);
      },
      error: () => this.refreshing.set(false),
    });
  }

  clear() {
    this.result.set(null); this.fileName.set(''); this.lastFile.set(null);
    this.pendingFile.set(null); this.preview.set(null); this.error.set('');
    this.historyData.set([]); this.signals.set(null);
    this.resetFormatFixes(); this._clearCache();
    // Also clear the cross-device snapshot — otherwise other phones still see the old upload.
    this.api.clearSavedPortfolio().subscribe({ error: () => { /* best effort */ } });
  }

  // ── History ───────────────────────────────────────────────────────────────
  loadHistory() {
    const r = this.result();
    if (!r) return;
    const refs = (r.holdings as any[])
      .filter((h: any) => h.instrument_token)
      .map((h: any) => ({ instrument_token: h.instrument_token, qty: h.qty, avg_cost: h.avg_cost }));
    if (!refs.length) { this.historyError.set('No instrument tokens — reconnect Kite'); return; }
    this.historyLoading.set(true); this.historyError.set('');
    this.api.portfolioHistory(refs, this.historyRange()).subscribe({
      next: (res) => { this.historyData.set(res.series || []); this.historyLoading.set(false); },
      error: (e) => { this.historyError.set(e?.error?.detail || 'History unavailable'); this.historyLoading.set(false); },
    });
  }

  setHistoryRange(days: number) {
    this.historyRange.set(days);
    if (this.historyData().length) this.loadHistory();
  }

  // ── Signals ───────────────────────────────────────────────────────────────
  loadSignals() {
    const r = this.result();
    if (!r) return;
    const holdings = (r.holdings as any[]).filter((h: any) => h.instrument_token);
    if (!holdings.length) { this.signalsError.set('No instrument tokens — reconnect Kite'); return; }
    this.signalsLoading.set(true); this.signalsError.set('');
    this.api.portfolioSignals(
      holdings.map((h: any) => ({
        instrument_token: h.instrument_token,
        symbol: h.matched_symbol || h.share,
        qty: h.qty,
        avg_cost: h.avg_cost,
        invested: h.invested,
        current_value: h.current_value,
        pnl_pct: h.pnl_pct,
      })),
      r.stats.total_value,
    ).subscribe({
      next: (res) => { this.signals.set(res); this.signalsLoading.set(false); },
      error: (e) => { this.signalsError.set(e?.error?.detail || 'Signals unavailable'); this.signalsLoading.set(false); },
    });
  }

  // ── Formatters ────────────────────────────────────────────────────────────
  fmtRs(v: number): string {
    const a = Math.abs(v);
    const s = v < 0 ? '−' : v > 0 ? '+' : '';
    if (a >= 1e7) return `${s}₹${(a / 1e7).toFixed(2)}Cr`;
    if (a >= 1e5) return `${s}₹${(a / 1e5).toFixed(2)}L`;
    if (a >= 1e3) return `${s}₹${(a / 1e3).toFixed(1)}K`;
    return `${s}₹${a.toFixed(0)}`;
  }
  fmtRsPlain(v: number): string {
    return '₹' + Math.round(v).toLocaleString('en-IN');
  }
  fmtInsightVal(ins: any): string {
    if (!ins) return '';
    switch (ins.format) {
      case 'rupees':         return this.fmtRs(ins.value);
      case 'percent':        return (ins.value >= 0 ? '+' : '') + ins.value.toFixed(2) + '%';
      case 'count_holdings': return ins.value + (ins.value === 1 ? ' holding' : ' holdings');
      default:               return ins.value.toLocaleString('en-IN', { maximumFractionDigits: 0 });
    }
  }

  sectorColor(sector: string): string {
    const map: Record<string, string> = {
      'Financials': '#6366f1', 'IT': '#06b6d4', 'Pharma': '#10b981',
      'Healthcare': '#34d399', 'Auto': '#f59e0b', 'FMCG': '#f97316',
      'Metals': '#94a3b8', 'Oil & Gas': '#64748b', 'Power': '#facc15',
      'Telecom': '#a855f7', 'Cement': '#78716c', 'Infra & Engg': '#0ea5e9',
      'Consumer': '#ec4899', 'Chemicals': '#84cc16', 'Realty': '#e879f9',
      'Capital Goods': '#22d3ee', 'ETF': '#475569', 'Other': '#94a3b8',
    };
    return map[sector] || '#94a3b8';
  }

  // ── Signal detail helpers ─────────────────────────────────────────────────
  maRelPct(cur: number, ma: number | null): string {
    if (!ma || ma <= 0) return '';
    const pct = ((cur - ma) / ma) * 100;
    return (pct >= 0 ? '+' : '') + pct.toFixed(1) + '%';
  }

  starScore(score: number, max: number): string {
    const filled = Math.min(score, max);
    return '★'.repeat(filled) + '☆'.repeat(Math.max(0, max - filled));
  }

  // ══════════════════════════════════════════════════════════════════════════
  // CHARTS
  // ══════════════════════════════════════════════════════════════════════════
  drawAllCharts() {
    this.drawCombinedChart();
    this.drawSectorPie();
  }

  drawSectorPie() {
    const sectors = this.sectorBreakdown();
    if (!sectors.length || !this.sectorCanvasRef?.nativeElement) return;
    const canvas = this.sectorCanvasRef.nativeElement;
    const setup = this._setupCanvas(canvas, 220);
    if (!setup) return;
    const { ctx, W, H } = setup;

    const R = Math.min(H, W * 0.4) * 0.44;
    const cx = W * 0.32;
    const cy = H / 2;
    const inner = R * 0.52;

    let angle = -Math.PI / 2;
    sectors.forEach(s => {
      const slice = (s.pct / 100) * Math.PI * 2;
      ctx.beginPath();
      ctx.moveTo(cx, cy);
      ctx.arc(cx, cy, R, angle, angle + slice);
      ctx.closePath();
      ctx.fillStyle = this.sectorColor(s.sector);
      ctx.fill();
      angle += slice;
    });

    // Donut hole
    ctx.beginPath();
    ctx.arc(cx, cy, inner, 0, Math.PI * 2);
    ctx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue('--bg-card').trim() || '#1e293b';
    ctx.fill();

    // Centre label
    ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    ctx.font = `700 11px system-ui`;
    ctx.fillStyle = '#94a3b8';
    ctx.fillText(`${sectors.length} sectors`, cx, cy);

    // Legend
    const legX = W * 0.62;
    const rowH = Math.min(22, (H - 16) / Math.min(sectors.length, 10));
    ctx.textAlign = 'left'; ctx.textBaseline = 'middle';
    sectors.slice(0, 10).forEach((s, i) => {
      const y = 10 + i * rowH + rowH / 2;
      ctx.fillStyle = this.sectorColor(s.sector);
      ctx.beginPath(); ctx.arc(legX - 8, y, 4, 0, Math.PI * 2); ctx.fill();
      ctx.fillStyle = '#64748b'; ctx.font = `500 10.5px system-ui`;
      const label = s.sector.length > 14 ? s.sector.slice(0, 13) + '…' : s.sector;
      ctx.fillText(label, legX, y);
      ctx.fillStyle = '#94a3b8'; ctx.textAlign = 'right'; ctx.font = `700 10.5px system-ui`;
      ctx.fillText(s.pct.toFixed(1) + '%', W - 4, y);
      ctx.textAlign = 'left';
    });
    ctx.textBaseline = 'alphabetic';
  }

  drawCombinedChart() {
    const tab = this.chartTab();
    const r   = this.result();
    if (!r || !this.combinedCanvasRef?.nativeElement) return;
    if (tab === 'winners') {
      const items = r.charts?.winners || [];
      this._drawHorizontalBars(this.combinedCanvasRef.nativeElement, items, '#10b981',
        (d: any) => d.share, (d: any) => d.pnl_pct, (d: any) => '+' + d.pnl_pct.toFixed(1) + '%');
    } else if (tab === 'losers') {
      const items = r.charts?.losers || [];
      this._drawHorizontalBars(this.combinedCanvasRef.nativeElement, items, '#ef4444',
        (d: any) => d.share, (d: any) => Math.abs(d.pnl_pct), (d: any) => d.pnl_pct.toFixed(1) + '%');
    } else {
      this._drawContribCanvas(this.combinedCanvasRef.nativeElement, r.charts?.contributors || []);
    }
  }

  private _setupCanvas(canvas: HTMLCanvasElement, height = 280): { ctx: CanvasRenderingContext2D; W: number; H: number } | null {
    const ctx = canvas.getContext('2d');
    if (!ctx) return null;
    const dpr = window.devicePixelRatio || 1;
    const W = canvas.parentElement?.clientWidth ?? 400;
    const H = height;
    canvas.width = W * dpr; canvas.height = H * dpr;
    canvas.style.width = W + 'px'; canvas.style.height = H + 'px';
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    return { ctx, W, H };
  }

  private _drawHorizontalBars(canvas: HTMLCanvasElement, items: any[], color: string,
                              labelFn: (d: any) => string, valueFn: (d: any) => number,
                              valueText: (d: any) => string) {
    const setup = this._setupCanvas(canvas, 260);
    if (!setup) return;
    const { ctx, W, H } = setup;
    if (!items.length) {
      ctx.fillStyle = '#94a3b8'; ctx.font = '13px system-ui'; ctx.textAlign = 'center';
      ctx.fillText('No data', W / 2, H / 2); return;
    }
    const PAD = { top: 8, right: 90, bottom: 8, left: 130 };
    const cw = W - PAD.left - PAD.right;
    const rowH = (H - PAD.top - PAD.bottom) / items.length;
    const barH = Math.min(rowH * 0.6, 22);
    const maxV = Math.max(...items.map(d => Math.abs(valueFn(d))), 1);
    ctx.font = '11.5px system-ui'; ctx.textBaseline = 'middle';
    items.forEach((d, i) => {
      const cy = PAD.top + i * rowH + rowH / 2;
      const v = valueFn(d);
      const w = (Math.abs(v) / maxV) * cw;
      ctx.fillStyle = '#475569'; ctx.textAlign = 'right';
      const label = labelFn(d);
      ctx.fillText(label.length > 18 ? label.slice(0, 17) + '…' : label, PAD.left - 6, cy);
      const grad = ctx.createLinearGradient(PAD.left, 0, PAD.left + w, 0);
      grad.addColorStop(0, color); grad.addColorStop(1, color + '88');
      ctx.fillStyle = grad; ctx.fillRect(PAD.left, cy - barH / 2, w, barH);
      ctx.fillStyle = '#0f172a'; ctx.textAlign = 'left';
      ctx.fillText(valueText(d), PAD.left + w + 6, cy);
    });
    ctx.textBaseline = 'alphabetic';
  }

  private _drawContribCanvas(canvas: HTMLCanvasElement, items: any[]) {
    const setup = this._setupCanvas(canvas, 260);
    if (!setup) return;
    const { ctx, W, H } = setup;
    if (!items.length) {
      ctx.fillStyle = '#94a3b8'; ctx.font = '13px system-ui'; ctx.textAlign = 'center';
      ctx.fillText('No data', W / 2, H / 2); return;
    }
    const PAD = { top: 8, right: 110, bottom: 8, left: 130 };
    const cw = W - PAD.left - PAD.right;
    const rowH = (H - PAD.top - PAD.bottom) / items.length;
    const barH = Math.min(rowH * 0.6, 22);
    const maxAbs = Math.max(...items.map((d: any) => Math.abs(d.pnl)), 1);
    const cx0 = PAD.left + cw / 2;
    ctx.font = '11.5px system-ui'; ctx.textBaseline = 'middle';
    items.forEach((d: any, i: number) => {
      const cy = PAD.top + i * rowH + rowH / 2;
      const w = (Math.abs(d.pnl) / maxAbs) * (cw / 2);
      ctx.fillStyle = '#475569'; ctx.textAlign = 'right';
      const label = d.share.length > 18 ? d.share.slice(0, 17) + '…' : d.share;
      ctx.fillText(label, PAD.left - 6, cy);
      const positive = d.pnl >= 0;
      ctx.fillStyle = positive ? '#10b981' : '#ef4444';
      if (positive) ctx.fillRect(cx0, cy - barH / 2, w, barH);
      else          ctx.fillRect(cx0 - w, cy - barH / 2, w, barH);
      ctx.fillStyle = '#0f172a'; ctx.textAlign = 'left';
      ctx.fillText(this.fmtRs(d.pnl), positive ? cx0 + w + 6 : cx0 - w - 6 - 60, cy);
    });
    ctx.strokeStyle = 'rgba(0,0,0,0.18)'; ctx.lineWidth = 1; ctx.setLineDash([3, 3]);
    ctx.beginPath(); ctx.moveTo(cx0, PAD.top); ctx.lineTo(cx0, H - PAD.bottom); ctx.stroke();
    ctx.setLineDash([]); ctx.textBaseline = 'alphabetic';
  }

  drawHistoryChart() {
    const data = this.historyData();
    if (!this.historyCanvasRef?.nativeElement || !data.length) return;
    const canvas = this.historyCanvasRef.nativeElement;
    const setup = this._setupCanvas(canvas, 200);
    if (!setup) return;
    const { ctx, W, H } = setup;

    const PAD = { top: 20, right: 16, bottom: 36, left: 70 };
    const cw = W - PAD.left - PAD.right;
    const ch = H - PAD.top - PAD.bottom;

    const pnls = data.map((d: any) => d.pnl);
    const minP = Math.min(...pnls);
    const maxP = Math.max(...pnls);
    const range = maxP - minP || 1;
    const n = data.length;

    const toX = (i: number) => PAD.left + (i / Math.max(n - 1, 1)) * cw;
    const toY = (v: number) => PAD.top + ch - ((v - minP) / range) * ch;
    const zero = toY(0);

    // Filled area under line (green / red from zero)
    const zeroY = Math.max(PAD.top, Math.min(PAD.top + ch, toY(0)));
    ctx.beginPath();
    ctx.moveTo(toX(0), zeroY);
    data.forEach((d: any, i: number) => { ctx.lineTo(toX(i), toY(d.pnl)); });
    ctx.lineTo(toX(n - 1), zeroY);
    ctx.closePath();
    const lastPnl = pnls[pnls.length - 1] ?? 0;
    ctx.fillStyle = lastPnl >= 0 ? 'rgba(16,185,129,0.12)' : 'rgba(239,68,68,0.12)';
    ctx.fill();

    // Zero line
    ctx.strokeStyle = 'rgba(0,0,0,0.12)'; ctx.lineWidth = 1; ctx.setLineDash([4, 4]);
    ctx.beginPath(); ctx.moveTo(PAD.left, zero); ctx.lineTo(PAD.left + cw, zero); ctx.stroke();
    ctx.setLineDash([]);

    // PnL line
    ctx.beginPath();
    data.forEach((d: any, i: number) => {
      i === 0 ? ctx.moveTo(toX(i), toY(d.pnl)) : ctx.lineTo(toX(i), toY(d.pnl));
    });
    ctx.strokeStyle = lastPnl >= 0 ? '#10b981' : '#ef4444';
    ctx.lineWidth = 2; ctx.stroke();

    // Y-axis labels
    ctx.fillStyle = '#64748b'; ctx.font = '10px system-ui'; ctx.textAlign = 'right';
    ctx.textBaseline = 'middle';
    [minP, (minP + maxP) / 2, maxP].forEach(v => {
      ctx.fillText(this.fmtRs(v), PAD.left - 6, toY(v));
    });

    // X-axis date labels (show ~5 dates)
    ctx.textBaseline = 'top'; ctx.textAlign = 'center';
    const step = Math.max(1, Math.floor(n / 5));
    for (let i = 0; i < n; i += step) {
      const d = data[i].date?.slice(5); // "MM-DD"
      ctx.fillText(d, toX(i), PAD.top + ch + 6);
    }
    if ((n - 1) % step !== 0) {
      ctx.fillText(data[n - 1].date?.slice(5), toX(n - 1), PAD.top + ch + 6);
    }
    ctx.textBaseline = 'alphabetic';

    // Last value bubble
    const lx = toX(n - 1), ly = toY(lastPnl);
    ctx.beginPath(); ctx.arc(lx, ly, 5, 0, Math.PI * 2);
    ctx.fillStyle = lastPnl >= 0 ? '#10b981' : '#ef4444'; ctx.fill();
  }

  // ── Snowflake fundamentals integration ────────────────────────────────────
  private _holdingsTickers(): string[] {
    const h = this.result()?.holdings ?? [];
    const set = new Set<string>();
    for (const hh of h) {
      const sym = (hh.symbol || hh.share || '').toString().toUpperCase().trim();
      if (sym) set.add(sym);
    }
    return [...set];
  }

  /**
   * Lazy-fetch 1Y sparklines for the rows currently visible in the snowflake
   * table. Tracks already-requested keys so we don't pound the endpoint when
   * the user pages between buckets. Server caches for 6h.
   *
   * Each chunk merges into the signal via `signal.update()` — this is what
   * makes concurrent calls safe. Without it, overlapping calls (e.g. user
   * switches bucket while previous batch is in flight) would each snapshot
   * the map, mutate locally, and overwrite each other on completion —
   * which used to make sparklines flash in and vanish.
   */
  loadSparklinesForVisible() {
    const visible = this.fundamentalsRows()
      .map(r => r.symbol)
      .filter((s: string) => !!s && !this._sparklineKeys.has(s));
    if (!visible.length) return;
    // Mark as requested up-front so subsequent renders don't re-request.
    visible.forEach(s => this._sparklineKeys.add(s));
    // Chunk in 100s — yfinance batch is fast but mega-requests time out.
    const chunks: string[][] = [];
    for (let i = 0; i < visible.length; i += 100) chunks.push(visible.slice(i, i + 100));
    this.sparklinesLoading.set(true);
    let done = 0;
    const finish = () => {
      done++;
      if (done >= chunks.length) this.sparklinesLoading.set(false);
    };
    chunks.forEach((chunk) => {
      this.api.sparklines(chunk).subscribe({
        next: (resp: any) => {
          const sparks = resp?.sparklines ?? {};
          if (!Object.keys(sparks).length) return;
          // Atomic merge — never overwrite work from a concurrent call.
          this.sparklines.update((curr) => {
            const next = new Map(curr);
            for (const [k, v] of Object.entries(sparks)) {
              next.set(k.toUpperCase(), v);
            }
            return next;
          });
        },
        error: () => {
          // Clear these keys so a retry (e.g. on next bucket switch) is allowed.
          chunk.forEach(s => this._sparklineKeys.delete(s));
          finish();
        },
        complete: finish,
      });
    });
  }

  loadSnowflakesForHoldings(force: boolean = false) {
    const tickers = this._holdingsTickers();
    if (!tickers.length) return;
    // Dedup: skip if the same tickers are already loaded and not forced.
    const key = [...tickers].sort().join('|');
    if (!force && (this._snowflakeInFlight || key === this._lastSnowflakeKey)) {
      return;
    }
    this._snowflakeInFlight = true;
    this._lastSnowflakeKey = key;
    this.snowflakeLoading.set(true);
    this.snowflakeError.set('');
    // Batch in chunks of 200 to keep URLs reasonable.
    const chunks: string[][] = [];
    for (let i = 0; i < tickers.length; i += 200) chunks.push(tickers.slice(i, i + 200));
    const map = new Map<string, any>(this.snowflakes());
    const missing: string[] = [];
    const errors: string[] = [];
    let done = 0;
    // Hard timeout: if no chunk responds in 15s, give up rather than spin forever.
    const watchdog = setTimeout(() => {
      if (this.snowflakeLoading()) {
        this.snowflakeLoading.set(false);
        if (!this.snowflakeError()) {
          this.snowflakeError.set('Snowflake load timed out — backend slow or unreachable.');
        }
      }
    }, 15_000);
    const finishChunk = () => {
      done++;
      if (done >= chunks.length) {
        clearTimeout(watchdog);
        this.snowflakes.set(map);
        this.snowflakeMissing.set(missing);
        if (errors.length && map.size === 0) {
          this.snowflakeError.set(`All ${chunks.length} requests failed: ${errors[0]}`);
        } else if (errors.length) {
          this.snowflakeError.set(`${errors.length} of ${chunks.length} batches failed (partial data shown).`);
        }
        // If the user landed with the default "Keep" filter but they have no
        // Keep stocks in their portfolio, drop to "ALL" so they see *something*.
        if (this.snowflakeBucket() === 'KEEP_AND_ADD'
            && (this.snowflakeBucketCounts()['KEEP_AND_ADD'] || 0) === 0
            && this.visibleHoldings().length > 0) {
          this.snowflakeBucket.set('ALL');
        }
        this.snowflakeLoading.set(false);
        this._snowflakeInFlight = false;
      }
    };
    chunks.forEach((chunk) => {
      this.api.snowflakeByTickers(chunk).subscribe({
        next: (resp: any) => {
          // Backend signals empty universe (no CSV deployed) with available=false.
          if (resp && resp.available === false) {
            this.snowflakeUnavailable.set(true);
            return;  // don't try to merge — every holding will be in missing[]
          }
          this.snowflakeUnavailable.set(false);
          // Prefer `matches` (input → row) so we can look up by exactly the
          // string we sent (typically the long name). Fall back to row.ticker
          // for callers that pass NSE codes.
          if (resp?.matches && typeof resp.matches === 'object') {
            for (const [key, row] of Object.entries(resp.matches)) {
              const k = (key || '').toString().toUpperCase().trim();
              if (k) map.set(k, row as any);
              if ((row as any)?.ticker) {
                map.set(((row as any).ticker).toString().toUpperCase().trim(), row as any);
              }
            }
          } else {
            for (const row of (resp.rows ?? [])) {
              if (row?.ticker) map.set(row.ticker.toUpperCase(), row);
            }
          }
          for (const m of (resp.missing ?? [])) missing.push(m);
        },
        error: (e: any) => {
          const msg = e?.error?.detail || e?.message || (e?.status ? `HTTP ${e.status}` : String(e));
          errors.push(msg);
          finishChunk();
        },
        complete: finishChunk,
      });
    });
  }

  setSnowflakeBucket(b: string) {
    const valid = ['ALL', 'KEEP_AND_ADD', 'HOLD', 'TRIM', 'EXIT', 'INSUFFICIENT_DATA'] as const;
    if (valid.includes(b as any)) this.snowflakeBucket.set(b as any);
  }

  // ── Live LTP polling via Kite ─────────────────────────────────────────────
  private _startLivePolling() {
    this._stopLivePolling();
    const poll = () => this._pollLiveOnce();
    poll();
    this.livePollTimer = setInterval(poll, this.LIVE_POLL_MS);
  }
  private _stopLivePolling() {
    if (this.livePollTimer) { clearInterval(this.livePollTimer); this.livePollTimer = null; }
  }
  private _ltpInFlight = false;
  private _pollLiveOnce() {
    if (this._ltpInFlight) return;  // skip a tick if previous request is still pending
    // Only poll LTP for rows currently visible in the snowflake table
    // (after bucket filter applies). Keeps Kite requests small + fast.
    const visible = this.fundamentalsRows()
      .map(r => r.symbol)
      .filter((s: string) => !!s);
    const tickers = visible.length ? visible : this._holdingsTickers();
    if (!tickers.length) return;
    // Hard cap so Kite never gets a huge ltp() call regardless of filter state.
    const batch = tickers.slice(0, 200);
    this._ltpInFlight = true;
    const done = () => { this._ltpInFlight = false; };
    this.api.snowflakeLiveQuotes(batch).subscribe({
      next: (resp: any) => {
        const quotes = resp?.quotes ?? {};
        this.liveConnected.set(!!resp?.kite_connected);
        if (!Object.keys(quotes).length) return;
        const next = new Map<string, number>(this.liveQuotes());
        for (const [sym, q] of Object.entries(quotes)) {
          const ltp = (q as any)?.ltp;
          if (typeof ltp === 'number') next.set(sym.toUpperCase(), ltp);
        }
        this.liveQuotes.set(next);
        this.liveLastUpdate.set(new Date().toLocaleTimeString());
      },
      error: () => { done(); /* next tick will retry */ },
      complete: done,
    });
  }

  ngOnDestroy() { this._stopLivePolling(); }

  // Small formatter used by the fundamentals table.
  bucketLabel(b: string | undefined | null): string {
    if (!b) return 'Unscored';
    return b.replace('_', ' & ').replace(/\b\w/g, (c) => c.toUpperCase());
  }
}
