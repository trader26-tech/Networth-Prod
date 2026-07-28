import { Component, OnInit, EventEmitter, Output, computed, inject, input, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpErrorResponse } from '@angular/common/http';
import { EquityService, Dividend, DividendStatus, StockRow, HoldingDetail, CorpActionUpload, CorpActionImportResult, DividendSyncResult, TaxStatement, DivReconcileResult, DivReconcileMatch, DivStatementUpload } from '../../services/equity.service';
import { DividendStore } from '../../services/dividend-store';

@Component({
  selector: 'app-dividends',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './dividends.html',
  styleUrl: './dividends.scss',
})
export class Dividends implements OnInit {
  private store = inject(DividendStore);
  private api = inject(EquityService);

  // declared-dividend sync (reads NSE + BSE corporate actions)
  syncing = signal(false);
  syncMsg = signal<string | null>(null);
  syncMenu = signal(false);                        // the Sync dropdown
  syncOk = signal<Record<string, boolean>>({});    // per-exchange "synced ✓" this session

  // ── Sync result panel — shows EXACTLY which dividends changed, not just a count ──
  lastSync = signal<DividendSyncResult | null>(null);   // the result to render
  syncedAt = signal<string | null>(null);               // when this sync ran
  showSyncResult = signal(false);                        // the result panel is open
  // The dividend LIST is the main content; the summary (KPIs / by-person / by-month)
  // is collapsed by default so the table is visible immediately, even on short
  // screens. Tap "summary" to expand it.
  showSyncDetails = signal(false);
  toggleSyncDetails() { this.showSyncDetails.update(v => !v); }

  /** The server-side sync covers BOTH exchanges in one pass; `exchange` is just
   *  which row the user clicked (for the message). On success both show ✓. */
  syncDeclared(exchange?: string) {
    this.syncing.set(true); this.syncMsg.set(null); this.showSyncResult.set(false);
    this.api.syncDeclaredDividends().subscribe({
      next: r => {
        this.syncing.set(false);
        const parts: string[] = [];
        if (r.added_count) parts.push(`${r.added_count} new`);
        if (r.updated_count) parts.push(`${r.updated_count} refreshed`);
        if (r.pruned_count) parts.push(`${r.pruned_count} removed (sold)`);
        this.syncMsg.set(
          !r.source_reachable ? `${exchange || 'Exchange'} unreachable from the server — upload the CSV instead` :
          parts.length ? `Synced · ${parts.join(' · ')}${r.existing_count ? ` · ${r.existing_count} already tracked` : ''}` :
          r.existing_count ? `Up to date — all ${r.existing_count} declared dividends already tracked` :
          'Up to date — no new declared dividends');
        if (r.source_reachable) {
          this.syncOk.set({ NSE: true, BSE: true });
          this.lastSync.set(r);
          this.syncedAt.set(new Date().toISOString());
          // ALWAYS land on the NEW tab — that's what you came to see. When a sync
          // added nothing, it shows a clear "nothing new this time" instead.
          this.syncTableFilter.set('new');
          this.showSyncResult.set(true);          // reveal the breakdown
        }
        if (r.added_count || r.pruned_count || r.updated_count) this.store.load(true);
        setTimeout(() => this.syncMsg.set(null), 7000);
      },
      error: () => { this.syncing.set(false); this.syncMsg.set('Could not sync declared dividends'); },
    });
  }

  /** total ₹ across a set of sync items (added / updated) for the panel summary */
  syncSum(items: { amount: number }[] | undefined): number {
    return (items || []).reduce((a, b) => a + (b.amount || 0), 0);
  }
  /** did anything actually change on the last sync? */
  syncChanged = computed(() => {
    const r = this.lastSync();
    return !!r && (r.added_count > 0 || r.updated_count > 0 || r.pruned_count > 0);
  });
  /** jump the calendar to the month a synced dividend lands in */
  jumpToMonth(date: string) {
    const dt = new Date((date || '').slice(0, 10) + 'T00:00:00');
    if (isNaN(dt.getTime())) return;
    this.mode.set('calendar');
    this.viewYear.set(dt.getFullYear());
    this.viewMonth.set(dt.getMonth());
  }

  /** keys (base-symbol|ex-date) of what THIS sync just added / refreshed, so we
   *  can flag the matching calendar rows below. */
  private syncKeys = computed(() => {
    const r = this.lastSync();
    const key = (s: string, d: string) => this.base(s) + '|' + (d || '').slice(0, 10);
    const nw = new Set<string>(), rf = new Set<string>();
    for (const a of r?.added || []) nw.add(key(a.symbol, a.date));
    for (const u of r?.updated || []) rf.add(key(u.symbol, u.date));
    return { nw, rf, key };
  });

  /** The modal reads the SAME store the main calendar does (``scaledDivs``), from
   *  the current month onward, so every ₹ figure here reconciles exactly with the
   *  Dividends page — instead of the old sync-only subset that undercounted the
   *  month. Each row is flagged with what this sync did to it (new/refreshed). */
  syncRows = computed<(Dividend & { state: 'new' | 'refreshed' | 'tracked' })[]>(() => {
    const { nw, rf, key } = this.syncKeys();
    const from = this.todayKey.slice(0, 7);            // current month (YYYY-MM)
    return this.scaledDivs()
      .filter(d => (d.date || '').slice(0, 7) >= from)
      .map(d => ({ ...d, state: (nw.has(key(d.symbol, d.date)) ? 'new'
        : rf.has(key(d.symbol, d.date)) ? 'refreshed' : 'tracked') as 'new' | 'refreshed' | 'tracked' }))
      .sort((a, b) => (a.date || '').localeCompare(b.date || ''));
  });

  // ── table filter: All · New (added this sync) · Refreshed ────────────────────
  syncTableFilter = signal<'all' | 'new' | 'refreshed'>('all');
  setSyncFilter(f: 'all' | 'new' | 'refreshed') { this.syncTableFilter.set(f); }
  // Counts come straight from the sync result (immediate) rather than the store,
  // which only reloads a beat later.
  syncNewCount = computed(() => this.lastSync()?.added_count ?? 0);
  syncRefreshedCount = computed(() => this.lastSync()?.updated_count ?? 0);

  /** The newly-added dividends, built DIRECTLY from the sync result so they show
   *  instantly with their values — no wait for the store to reload (the reason the
   *  New tab could briefly look empty). */
  syncNewRows = computed<(Dividend & { state: 'new' })[]>(() => {
    return (this.lastSync()?.added || []).map(a => ({
      id: 'new|' + a.symbol + '|' + a.date,
      date: a.date, symbol: a.symbol, name: a.name || null,
      per_share: a.per_share, shares: a.shares, amount: a.amount,
      received: false, received_at: null, status: 'pending' as DividendStatus,
      currency: 'INR' as const, person: null, account_id: null, note: null,
      state: 'new' as const,
    })).sort((x, y) => (x.date || '').localeCompare(y.date || ''));
  });

  /** the rows actually shown in the table, per the active filter. */
  syncRowsView = computed(() => {
    const f = this.syncTableFilter();
    if (f === 'new') return this.syncNewRows();          // from the sync result → instant
    const rows = this.syncRows();
    return f === 'refreshed' ? rows.filter(r => r.state === 'refreshed') : rows;
  });

  /** Month-wise split — per-month totals equal the calendar's month totals. */
  syncByMonth = computed(() => {
    const rows = this.syncRows();
    const m: Record<string, { total: number; count: number; newAmt: number; newCount: number }> = {};
    for (const x of rows) {
      const k = (x.date || '').slice(0, 7);
      if (!k) continue;
      (m[k] ||= { total: 0, count: 0, newAmt: 0, newCount: 0 });
      m[k].total += x.amount || 0; m[k].count++;
      if (x.state === 'new') { m[k].newAmt += x.amount || 0; m[k].newCount++; }
    }
    const max = Math.max(1, ...Object.values(m).map(v => v.total));
    return Object.entries(m).sort(([a], [b]) => a.localeCompare(b))
      .map(([ym, v]) => ({ ym, label: this.ymLabel(ym), ...v, pct: v.total / max }));
  });

  /** Hero KPIs. ``thisMonth`` / ``fy`` are the SAME computeds the main page shows
   *  (``monthTotal`` / ``fyTotal``) so the two screens can never disagree. */
  syncKpis = computed(() => {
    const r = this.lastSync();
    if (!r) return null;
    const rows = this.syncRows();
    const upcoming = rows.reduce((a, b) => a + (b.amount || 0), 0);
    const next = rows.find(x => (x.date || '') >= this.todayKey && x.status !== 'received') || rows[0] || null;
    return {
      thisMonth: this.monthTotal(), monthLabel: this.monthLabel(),
      fy: this.fyTotal(), fyLabel: this.fyLabel(),
      upcoming, upcomingCount: rows.length, months: this.syncByMonth().length,
      addedAmt: this.syncSum(r.added), addedCount: r.added_count,
      refreshedCount: r.updated_count, trackedCount: rows.filter(x => x.state === 'tracked').length,
      removedCount: r.pruned_count, removedAmt: this.syncSum(r.pruned),
      nextDate: next?.date || null, nextAmt: next?.amount || 0, nextSym: next?.symbol || null,
    };
  });

  /** "Aug 2026" from a YYYY-MM key. */
  ymLabel(ym: string): string {
    const dt = new Date(ym + '-01T00:00:00');
    return isNaN(dt.getTime()) ? ym : dt.toLocaleDateString('en-IN', { month: 'short', year: 'numeric' });
  }
  /** the dividend's actual payment status — what "Status" means in the table. */
  stateLabel(s: string): string {
    return s === 'received' ? 'Received' : s === 'not_received' ? 'Missed' : 'Expected';
  }

  /** Per-person split of the upcoming dividends: each person's share (by shares
   *  held), plus how much this sync just added for them. */
  syncByPerson = computed(() => {
    const rows = this.syncRows();
    const hbs = this.holdingsBySym();
    const acc: Record<string, { total: number; changed: number }> = {};
    for (const x of rows) {
      const holders = hbs[this.base(x.symbol)] || [];
      const tq = holders.reduce((a, b) => a + b.qty, 0);
      const delta = x.state === 'new' ? (x.amount || 0) : 0;   // money this sync added
      if (tq) {
        for (const h of holders) {
          const w = h.qty / tq;
          (acc[h.person] ||= { total: 0, changed: 0 }).total += (x.amount || 0) * w;
          acc[h.person].changed += delta * w;
        }
      } else {
        (acc['Unassigned'] ||= { total: 0, changed: 0 }).total += x.amount || 0;
        acc['Unassigned'].changed += delta;
      }
    }
    return Object.entries(acc)
      .map(([person, v]) => ({ person, total: v.total, changed: v.changed, before: v.total - v.changed }))
      .sort((a, b) => b.total - a.total);
  });
  /** signed ₹ like "+₹1,240" / "−₹300" for the person-card delta chip */
  signedInr(v: number): string {
    const s = EquityService.inr(Math.abs(v));
    return (v >= 0 ? '+' : '−') + s;
  }

  // ── Corporate Actions CSV upload (manual declared-dividend import) ────────────
  importing = signal(false);
  lastImport = signal<CorpActionImportResult | null>(null);
  uploads = signal<CorpActionUpload[]>([]);
  showUploads = signal(false);
  expandedUpload = signal<string | null>(null);

  /** upload a BSE/NSE Corporate Actions CSV → dividends for the stocks you hold */
  importCorpActions(ev: Event) {
    const input = ev.target as HTMLInputElement;
    const file = input.files?.[0];
    input.value = '';
    if (!file) return;
    this.importing.set(true); this.syncMsg.set(null); this.lastImport.set(null);
    this.api.importCorpActions(file).subscribe({
      next: r => {
        this.importing.set(false);
        this.lastImport.set(r);
        const parts: string[] = [];
        if (r.added_count) parts.push(`${r.added_count} added`);
        if (r.updated_count) parts.push(`${r.updated_count} updated`);
        this.syncMsg.set(
          r.matched_count
            ? `Imported · ${parts.join(' · ')} for your holdings (of ${r.dividend_rows} declared)`
            : `No matches — none of the ${r.dividend_rows} declared dividends are stocks you hold`);
        if (r.matched_count) this.store.load(true);
        this.loadUploads();
        this.showUploads.set(true);
        if (r.upload) this.expandedUpload.set(r.upload.id);
        setTimeout(() => this.syncMsg.set(null), 9000);
      },
      error: (e: HttpErrorResponse) => {
        this.importing.set(false);
        this.syncMsg.set(e.status === 400
          ? (e.error?.detail || 'Could not read that CSV — upload the Corporate Actions export as-is')
          : e.status === 503 ? 'One-time setup needed — create the stock_dividends table first'
          : 'Import failed — please try again');
        setTimeout(() => this.syncMsg.set(null), 9000);
      },
    });
  }

  loadUploads() {
    this.api.corpActionUploads().subscribe({ next: u => this.uploads.set(u), error: () => {} });
  }
  toggleUploads() {
    const open = !this.showUploads();
    this.showUploads.set(open);
    if (open) this.loadUploads();
  }
  toggleUploadDetail(id: string) { this.expandedUpload.update(v => v === id ? null : id); }
  removeUpload(u: CorpActionUpload) {
    if (!confirm(`Forget the upload "${u.filename}"? The ${u.matched_count} dividend(s) it added stay in your log.`)) return;
    this.api.deleteCorpActionUpload(u.id).subscribe({ next: () => this.loadUploads(), error: () => {} });
  }
  /** compact "12 dividends · 3 bonus · 5 split" from the kinds map */
  kindsSummary(kinds: Record<string, number>): string {
    const order = ['dividend', 'bonus', 'split', 'buyback', 'rights', 'merger', 'other'];
    return order.filter(k => kinds?.[k]).map(k => `${kinds[k]} ${k}${kinds[k] > 1 && k !== 'dividend' && k !== 'rights' ? 's' : ''}`).join(' · ');
  }
  caDate(d: string | null): string {
    if (!d) return '—';
    const dt = new Date(d.length <= 10 ? d + 'T00:00:00' : d);
    return isNaN(dt.getTime()) ? d : dt.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' });
  }
  /** compact "12 Aug" — for the hero's next-ex-date stat */
  caDateShort(d: string | null): string {
    if (!d) return '—';
    const dt = new Date(d.length <= 10 ? d + 'T00:00:00' : d);
    return isNaN(dt.getTime()) ? d : dt.toLocaleDateString('en-IN', { day: '2-digit', month: 'short' });
  }
  caWhen(ts: string): string {
    const dt = new Date(ts);
    return isNaN(dt.getTime()) ? ts : dt.toLocaleString('en-IN', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' });
  }
  uploadTotal(u: CorpActionUpload): number {
    return (u.matched || []).reduce((a, b) => a + (b.amount || 0), 0);
  }

  // ── Tax P&L view (per-person, per-FY realized gains for filing) ─────────────
  view = signal<'dividends' | 'tax'>('dividends');
  taxStmts = signal<TaxStatement[]>([]);
  taxFy = signal<string>('');
  taxImporting = signal(false);
  taxMsg = signal<string | null>(null);
  ltcgExempt = signal(125000);
  kvDurable = signal(true);          // false → app_cache table missing; uploads are ephemeral
  private taxLoaded = false;

  setView(v: 'dividends' | 'tax') {
    this.view.set(v);
    if (v === 'tax' && !this.taxLoaded) { this.taxLoaded = true; this.loadTax(); }
  }
  loadTax() {
    this.api.taxStatements().subscribe({
      next: r => {
        this.taxStmts.set(r.statements || []);
        this.ltcgExempt.set(r.ltcg_exempt || 125000);
        this.kvDurable.set(r.durable !== false);
        const fys = this.taxFYs();
        if (!this.taxFy() || !fys.includes(this.taxFy())) this.taxFy.set(fys[0] || '');
      },
      error: () => {},
    });
  }
  importTaxPnl(ev: Event) {
    const input = ev.target as HTMLInputElement;
    const file = input.files?.[0]; input.value = '';
    if (!file) return;
    this.taxImporting.set(true); this.taxMsg.set(null);
    this.api.importTaxPnl(file).subscribe({
      next: r => {
        this.taxImporting.set(false);
        const s = r.statement;
        this.taxMsg.set(`Added ${s.person} · FY ${this.fyShort(s.fy)} · booked ${this.inr(s.total_booked)}`);
        this.taxLoaded = true; this.taxFy.set(s.fy); this.loadTax();
        setTimeout(() => this.taxMsg.set(null), 8000);
      },
      error: (e: HttpErrorResponse) => {
        this.taxImporting.set(false);
        this.taxMsg.set(e.error?.detail || 'Could not read that Tax P&L file');
        setTimeout(() => this.taxMsg.set(null), 8000);
      },
    });
  }
  deleteTaxPnl(s: TaxStatement) {
    if (!confirm(`Remove ${s.person}'s FY ${this.fyShort(s.fy)} tax statement?`)) return;
    this.api.deleteTaxPnl(s.client_id, s.fy).subscribe({ next: () => this.loadTax(), error: () => {} });
  }
  /** "2025-2026" → "25-26" */
  fyShort(fy: string): string {
    const m = (fy || '').match(/(\d{4})-(\d{4})/);
    return m ? `${m[1].slice(2)}–${m[2].slice(2)}` : fy;
  }
  taxFYs = computed(() => [...new Set(this.taxStmts().map(s => s.fy))].sort().reverse());
  taxForFy = computed(() => this.taxStmts().filter(s => s.fy === this.taxFy())
    .sort((a, b) => b.total_booked - a.total_booked));
  taxTotals = computed(() => {
    const rows = this.taxForFy();
    const sum = (f: (s: TaxStatement) => number) => rows.reduce((a, b) => a + f(b), 0);
    return {
      booked: sum(s => s.total_booked), stcg: sum(s => s.short_term), ltcg: sum(s => s.long_term),
      fno: sum(s => s.fno_gain), dividends: sum(s => s.dividends), freeLeft: sum(s => s.ltcg_free_left),
    };
  });
  /** Per-person LTCG tax-free headroom: how full each person's ₹1.25L/yr
   *  long-term-gains exemption is. Bar is scaled to the exemption itself, so the
   *  filled part = LTCG already booked and the empty part = what they can still
   *  harvest tax-free. (The exemption applies to LONG-TERM gains only — not to
   *  STCG / F&O / intraday, which is why this tracks long_term, not total_booked.) */
  taxBars = computed(() => {
    const ex = this.ltcgExempt();
    return this.taxForFy().map(r => {
      const used = Math.max(0, r.long_term);              // LTCG booked so far this FY
      return {
        person: r.person, booked: r.total_booked, ltcg: r.long_term, freeLeft: r.ltcg_free_left,
        usedPct: Math.min(100, (used / ex) * 100),
        over: used > ex,
      };
    });
  });
  /** % of a person's ₹1.25L LTCG exemption already used (booked long-term gains). */
  ltcgUsedPct(s: TaxStatement): number {
    return Math.min(100, Math.max(0, (s.long_term / this.ltcgExempt()) * 100));
  }
  /** current holdings — powers the symbol list + auto-fill of shares held */
  stocks = input<StockRow[]>([]);
  /** per-account holdings — to attribute a stock's dividend across accounts */
  holdings = input<HoldingDetail[]>([]);
  /** selected account ids (empty = all) — scopes dividends to those accounts' share */
  selectedAccountIds = input<string[]>([]);

  /** Ask the parent (Holdings page) to filter to the stocks paying a dividend
   *  in the calendar's currently-viewed month. */
  @Output() viewInHoldings = new EventEmitter<{ year: number; month: number }>();

  hover = signal<number | null>(null);          // hovered month in the trend chart

  /** normalize: uppercase + drop NSE series suffix so CHEMBONDCH-BE == CHEMBONDCH */
  private base(s: string): string { return (s || '').toUpperCase().replace(/-[A-Z]{1,2}$/, ''); }

  /** fraction of each stock's dividend that belongs to the selected accounts */
  private scaleBySym = computed(() => {
    const sel = new Set(this.selectedAccountIds());
    const tot: Record<string, number> = {}, sub: Record<string, number> = {};
    for (const h of this.holdings()) {
      const k = this.base(h.symbol);
      tot[k] = (tot[k] || 0) + h.quantity;
      if (!sel.size || sel.has(h.account_id)) sub[k] = (sub[k] || 0) + h.quantity;
    }
    const f: Record<string, number> = {};
    for (const s in tot) f[s] = tot[s] ? (sub[s] || 0) / tot[s] : (sel.size ? 0 : 1);
    return f;
  });

  /** dividends scaled to the selected accounts' share (= source rows when "all") */
  private scaledDivs = computed<Dividend[]>(() => {
    const f = this.scaleBySym(); const scoped = this.selectedAccountIds().length > 0;
    if (!scoped) return this.divs();
    return this.divs()
      .map(d => ({ ...d, amount: d.amount * (f[this.base(d.symbol)] ?? 0) }))
      .filter(d => d.amount > 0);
  });

  inr = EquityService.inr;

  /** shared with the holdings-page panel — one source of truth */
  divs = computed<Dividend[]>(() => this.store.divs());
  loading = computed(() => !this.store.loaded());
  error = signal<string | null>(null);

  private now = new Date();
  viewYear = signal(this.now.getFullYear());
  viewMonth = signal(this.now.getMonth());          // 0-based

  mode = signal<'calendar' | 'trend' | 'people'>('calendar');  // calendar ↔ trend ↔ per-person
  trendYear = signal(this.now.getFullYear());        // year shown in the line chart

  // log modal (doubles as the per-day view)
  showLog = signal(false);
  logDraft = signal<{ date: string; symbol: string; per_share: number | null; shares: number | null; status: DividendStatus }>(
    { date: '', symbol: '', per_share: null, shares: null, status: 'pending' });
  saving = signal(false);

  weekdays = ['S', 'M', 'T', 'W', 'T', 'F', 'S'];

  ngOnInit() { this.store.load(); }
  reload() { this.store.load(true); }

  // ── lookups ───────────────────────────────────────────────────────────────
  symbols = computed(() => this.stocks().map(s => s.symbol).filter(Boolean));
  private qtyMap = computed(() => {
    const m: Record<string, number> = {};
    for (const s of this.stocks()) m[s.symbol] = s.quantity;
    return m;
  });

  private p2(n: number) { return n < 10 ? '0' + n : '' + n; }
  private key(y: number, m: number, d: number) { return `${y}-${this.p2(m + 1)}-${this.p2(d)}`; }
  private todayKey = this.key(this.now.getFullYear(), this.now.getMonth(), this.now.getDate());

  monthLabel = computed(() => new Date(this.viewYear(), this.viewMonth(), 1)
    .toLocaleString('en-IN', { month: 'long', year: 'numeric' }));

  byDay = computed(() => {
    const m: Record<string, { total: number; received: number; pending: number; notrecv: number; items: Dividend[] }> = {};
    for (const d of this.scaledDivs()) {
      const k = (d.date || '').slice(0, 10);
      (m[k] ||= { total: 0, received: 0, pending: 0, notrecv: 0, items: [] });
      m[k].total += d.amount;
      if (d.status === 'received') m[k].received += d.amount;
      else if (d.status === 'not_received') m[k].notrecv += d.amount;
      else m[k].pending += d.amount;
      m[k].items.push(d);
    }
    return m;
  });

  /** brightest day (by total ₹) in the viewed month — one shared scale so the
   * green (received), yellow (pending) and blended (partial) intensities are
   * directly comparable: darker = more money. */
  private calMax = computed(() => Math.max(0, ...Object.entries(this.byDay())
    .filter(([k]) => k.startsWith(`${this.viewYear()}-${this.p2(this.viewMonth() + 1)}`))
    .map(([, v]) => v.total)));
  private level(total: number): number {
    const mx = this.calMax(); if (total <= 0 || mx <= 0) return 0;
    const r = total / mx;
    return r <= 0.25 ? 1 : r <= 0.5 ? 2 : r <= 0.75 ? 3 : 4;
  }

  /** 42-cell calendar grid (null = padding before the 1st) */
  cells = computed(() => {
    const y = this.viewYear(), mo = this.viewMonth();
    const first = new Date(y, mo, 1).getDay();
    const days = new Date(y, mo + 1, 0).getDate();
    const bd = this.byDay();
    type Cell = { d: number; key: string; total: number; received: number; pending: number; notrecv: number;
                  count: number; isToday: boolean; tone: string };
    const out: (Cell | null)[] = [];
    for (let i = 0; i < first; i++) out.push(null);
    for (let d = 1; d <= days; d++) {
      const k = this.key(y, mo, d); const e = bd[k];
      const received = e?.received || 0, pending = e?.pending || 0, notrecv = e?.notrecv || 0;
      const awaiting = pending + notrecv;             // everything not yet in
      // green = received · orange = pending (default) · red = not received · blend = partly
      let tone = 'dv-l0';
      if (received > 0 && awaiting > 0) tone = 'mx' + (this.level(awaiting) || 1);          // partly collected
      else if (notrecv > 0 && pending === 0 && received === 0) tone = 'rd' + (this.level(notrecv) || 1);  // not received → red
      else if (awaiting > 0 && received === 0) tone = 'yl' + (this.level(awaiting) || 1);   // awaiting → orange
      else if (received > 0) tone = 'dv-l' + (this.level(received) || 1);                   // received → green
      out.push({ d, key: k, total: e?.total || 0, received, pending, notrecv,
        count: e?.items.length || 0, isToday: k === this.todayKey, tone });
    }
    while (out.length % 7 !== 0) out.push(null);
    return out;
  });

  /** total still owed to you (across all time) — drives the header chip.
   * Only genuinely pending money; "not received" is excluded (you won't get it). */
  pendingTotal = computed(() => this.scaledDivs().filter(d => d.status === 'pending').reduce((a, b) => a + b.amount, 0));

  private monthSum(y: number, m: number) {
    const pre = `${y}-${this.p2(m + 1)}`;
    return this.scaledDivs().filter(d => (d.date || '').startsWith(pre)).reduce((a, b) => a + b.amount, 0);
  }
  monthTotal = computed(() => this.monthSum(this.viewYear(), this.viewMonth()));
  yearTotal = computed(() => {
    const pre = `${this.viewYear()}-`;
    return this.scaledDivs().filter(d => (d.date || '').startsWith(pre)).reduce((a, b) => a + b.amount, 0);
  });

  // ── collected (received-only) — what has actually landed, vs the total incl. pending ──
  /** dividends actually RECEIVED in the viewed month (green on the calendar). */
  monthCollected = computed(() => {
    const pre = `${this.viewYear()}-${this.p2(this.viewMonth() + 1)}`;
    return this.scaledDivs()
      .filter(d => d.status === 'received' && (d.date || '').startsWith(pre))
      .reduce((a, b) => a + b.amount, 0);
  });
  /** dividends actually RECEIVED across the viewed month's financial year. */
  fyCollected = computed(() => {
    const sy = this.fyStartYear();
    const from = `${sy}-04-01`, to = `${sy + 1}-03-31`;
    return this.scaledDivs()
      .filter(d => { const k = (d.date || '').slice(0, 10); return d.status === 'received' && k >= from && k <= to; })
      .reduce((a, b) => a + b.amount, 0);
  });

  // ── Financial year (India: 1 Apr → 31 Mar) — the tax-filing period. Follows the
  // viewed month: Apr–Dec belong to the FY that STARTS that year; Jan–Mar belong
  // to the FY that started the PREVIOUS April. So paging into an earlier year shows
  // that earlier financial year's total.
  private fyStartYear = computed(() => this.viewMonth() >= 3 ? this.viewYear() : this.viewYear() - 1);
  /** total dividends across the viewed month's financial year (1 Apr → 31 Mar) */
  fyTotal = computed(() => {
    const sy = this.fyStartYear();
    const from = `${sy}-04-01`, to = `${sy + 1}-03-31`;
    return this.scaledDivs()
      .filter(d => { const k = (d.date || '').slice(0, 10); return k >= from && k <= to; })
      .reduce((a, b) => a + b.amount, 0);
  });
  /** e.g. "FY 2026–27" */
  fyLabel = computed(() => { const sy = this.fyStartYear(); return `FY ${sy}–${String(sy + 1).slice(2)}`; });

  private MONTHS = ['1', '2', '3', '4', '5', '6', '7', '8', '9', '10', '11', '12'];
  private MONTHS_F = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

  // ── stacked "top payers" bar ────────────────────────────────────────────────
  selectedMonth = signal<number | null>(null);   // month focused in the trend chart (null = whole year)
  hoverSeg = signal<number | null>(null);          // hovered segment in the bar
  activeMonth = computed(() => this.hover() ?? this.selectedMonth() ?? -1);

  selectMonth(i: number) { this.selectedMonth.set(this.selectedMonth() === i ? null : i); this.hoverSeg.set(null); }

  /** green intensity: bigger payer → darker green (light #d8efd9 → dark #15722f) */
  private greenShade(r: number): string {
    const a = [216, 239, 217], b = [21, 114, 47];
    const t = Math.max(0, Math.min(1, r));
    return `rgb(${a.map((c, i) => Math.round(c + (b[i] - c) * t)).join(',')})`;
  }

  /** the period the stacked bar represents (calendar→month, trend→selected month or full year) */
  payerBar = computed(() => {
    const cal = this.mode() === 'calendar';
    let pre: string, label: string;
    if (cal) { pre = `${this.viewYear()}-${this.p2(this.viewMonth() + 1)}`; label = `${this.MONTHS_F[this.viewMonth()]} ${this.viewYear()}`; }
    else if (this.selectedMonth() != null) { const m = this.selectedMonth()!; pre = `${this.trendYear()}-${this.p2(m + 1)}`; label = `${this.MONTHS_F[m]} ${this.trendYear()}`; }
    else { pre = `${this.trendYear()}-`; label = `${this.trendYear()} · full year`; }

    const bySym: Record<string, number> = {};
    for (const d of this.scaledDivs().filter(d => (d.date || '').startsWith(pre))) bySym[d.symbol] = (bySym[d.symbol] || 0) + d.amount;
    const rows = Object.entries(bySym).map(([symbol, amt]) => ({ symbol, amt })).sort((a, b) => b.amt - a.amt);
    const total = rows.reduce((a, b) => a + b.amt, 0) || 1;
    const max = rows.length ? rows[0].amt : 1;
    const segments = rows.map(r => ({ symbol: r.symbol, amt: r.amt, pct: r.amt / total,
      color: this.greenShade(r.amt / max), lbl: r.amt / max > 0.5 ? '#ffffff' : '#114a22' }));
    return { label, total: rows.reduce((a, b) => a + b.amt, 0), segments };
  });

  /** symbol → accounts holding it (current), to attribute a dividend across people */
  private holdingsBySym = computed(() => {
    const m: Record<string, { person: string; qty: number }[]> = {};
    for (const h of this.holdings()) (m[this.base(h.symbol)] ||= []).push({ person: h.person, qty: h.quantity });
    return m;
  });

  /** hovered-month readout for the trend tooltip — total + per-person breakdown */
  hoverInfo = computed(() => {
    const h = this.hover(); if (h == null) return null;
    const pre = `${this.trendYear()}-${this.p2(h + 1)}`;
    const items = this.scaledDivs().filter(d => (d.date || '').startsWith(pre));
    const hbs = this.holdingsBySym();
    const byPerson: Record<string, number> = {};
    for (const d of items) {
      const accs = hbs[this.base(d.symbol)] || [];
      const tq = accs.reduce((a, b) => a + b.qty, 0);
      if (tq) for (const a of accs) byPerson[a.person] = (byPerson[a.person] || 0) + d.amount * a.qty / tq;
      else byPerson['Unassigned'] = (byPerson['Unassigned'] || 0) + d.amount;
    }
    const p = this.chart().pts[h];
    return {
      month: this.MONTHS_F[h], total: this.monthSum(this.trendYear(), h), count: items.length,
      xPct: (p.x / this.chart().W) * 100,
      people: Object.entries(byPerson).map(([name, amt]) => ({ name, amt })).sort((a, b) => b.amt - a.amt),
    };
  });

  /** 12 monthly totals for the year shown in the line chart */
  trendYearTotal = computed(() => {
    const pre = `${this.trendYear()}-`;
    return this.scaledDivs().filter(d => (d.date || '').startsWith(pre)).reduce((a, b) => a + b.amount, 0);
  });

  private niceCeil(x: number): number {
    if (x <= 0) return 1;
    const p = Math.pow(10, Math.floor(Math.log10(x))); const n = x / p;
    return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * p;
  }
  /** compact ₹ for axis ticks (₹1.2k / ₹3L) */
  axisLabel(v: number): string {
    if (v >= 1e7) return '₹' + (v / 1e7).toFixed(1) + 'Cr';
    if (v >= 1e5) return '₹' + (v / 1e5).toFixed(1) + 'L';
    if (v >= 1e3) return '₹' + Math.round(v / 1e3) + 'k';
    return '₹' + Math.round(v);
  }

  /** line-chart geometry for the selected year (12 points + axis + stats) */
  chart = computed(() => {
    const W = 340, H = 176, padL = 38, padR = 12, padT = 16, padB = 26;
    const innerW = W - padL - padR, innerH = H - padT - padB;
    const totals = this.MONTHS.map((_, m) => this.monthSum(this.trendYear(), m));
    const rawMax = Math.max(0, ...totals);
    const max = this.niceCeil(rawMax);
    const baseY = padT + innerH;
    const pts = totals.map((t, i) => ({
      x: padL + (i / 11) * innerW,
      y: padT + innerH * (1 - t / max),
      total: t, label: this.MONTHS_F[i], short: this.MONTHS[i],
      cur: this.trendYear() === this.now.getFullYear() && i === this.now.getMonth(),
    }));
    const line = pts.map((p, i) => `${i ? 'L' : 'M'}${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(' ');
    const area = `${line} L${pts[11].x.toFixed(1)} ${baseY} L${pts[0].x.toFixed(1)} ${baseY} Z`;
    const grid = [0, 0.5, 1].map(f => ({ y: padT + innerH * (1 - f), val: max * f }));
    const nonZero = totals.filter(t => t > 0).length;
    const sum = totals.reduce((a, b) => a + b, 0);
    const peakIdx = rawMax > 0 ? totals.indexOf(rawMax) : -1;
    return { W, H, pts, line, area, baseY, max, grid, peakIdx,
             avg: nonZero ? sum / nonZero : 0, months: nonZero };
  });

  prevMonth() { let m = this.viewMonth() - 1, y = this.viewYear(); if (m < 0) { m = 11; y--; } this.viewMonth.set(m); this.viewYear.set(y); }
  nextMonth() { let m = this.viewMonth() + 1, y = this.viewYear(); if (m > 11) { m = 0; y++; } this.viewMonth.set(m); this.viewYear.set(y); }
  thisMonth() { this.viewYear.set(this.now.getFullYear()); this.viewMonth.set(this.now.getMonth()); }
  prevYear() { this.trendYear.update(y => y - 1); this.selectedMonth.set(null); }
  nextYear() { this.trendYear.update(y => y + 1); this.selectedMonth.set(null); }

  // ── logging ─────────────────────────────────────────────────────────────────
  openLog(dayKey?: string) {
    this.error.set(null);
    // everything starts pending (orange) — you mark each entry as it resolves
    this.logDraft.set({ date: dayKey || this.todayKey, symbol: '', per_share: null, shares: null, status: 'pending' });
    this.showLog.set(true);
  }
  setLog(k: 'date' | 'symbol' | 'per_share' | 'shares' | 'status', v: any) {
    if (k === 'status') { this.logDraft.update(d => ({ ...d, status: v as DividendStatus })); return; }
    this.logDraft.update(d => ({ ...d, [k]: (k === 'per_share' || k === 'shares') ? (v === '' || v == null ? null : +v) : v }));
    if (k === 'symbol') {
      const q = this.qtyMap()[(v || '').toUpperCase()];
      if (q != null) this.logDraft.update(d => ({ ...d, shares: q }));
    }
  }
  amountPreview = computed(() => (this.logDraft().per_share || 0) * (this.logDraft().shares || 0));
  canSave = computed(() => {
    const d = this.logDraft();
    return !!d.date && !!d.symbol.trim() && (d.per_share || 0) > 0 && (d.shares || 0) > 0;
  });
  /** entries already logged on the date currently in the form */
  dayItems = computed(() => this.byDay()[this.logDraft().date]?.items || []);

  /** currency a symbol trades in (USD for US stocks), from the holdings feed. */
  symCurrency(sym: string): 'INR' | 'USD' {
    const k = this.base(sym);
    const s = this.stocks().find(x => this.base(x.symbol) === k);
    return s?.currency === 'USD' ? 'USD' : 'INR';
  }
  curSym(sym: string): string { return this.symCurrency(sym) === 'USD' ? '$' : '₹'; }
  isUsdSym(sym: string): boolean { return this.symCurrency(sym) === 'USD'; }
  usdInr = input<number>(85);

  save() {
    if (!this.canSave()) return;
    const d = this.logDraft();
    const st = this.stocks().find(s => s.symbol === d.symbol.trim().toUpperCase());
    this.saving.set(true);
    this.store.add({
      date: d.date, symbol: d.symbol.trim().toUpperCase(), name: (st as any)?.name || undefined,
      per_share: d.per_share!, shares: d.shares!, status: d.status, currency: this.symCurrency(d.symbol),
    }).subscribe({
      next: () => {
        this.saving.set(false);
        // keep the modal open on the same date for fast multi-entry; reset the rest
        this.logDraft.update(x => ({ ...x, symbol: '', per_share: null, shares: null }));
      },
      error: (e: HttpErrorResponse) => {
        this.saving.set(false);
        this.error.set(e.status === 503
          ? 'One-time setup needed — create the stock_dividends table (see SUPABASE.md), then retry.'
          : 'Could not save — ' + (e.error?.detail || `HTTP ${e.status}`));
      },
    });
  }
  /** set a single entry's status (pending · received · not received) */
  setStatus(d: Dividend, status: DividendStatus) { if (d.status !== status) this.store.setStatus(d.id, status); }

  // ── Reconcile dividends from a bank statement ─────────────────────────────────
  divReconcileOpen = signal(false);
  divReconcileBusy = signal(false);
  divReconcileError = signal<string | null>(null);
  divReconcileResult = signal<DivReconcileResult | null>(null);
  divReconcileFileName = signal('');
  divReconcilePerson = signal('');
  divReconcileAccepted = signal<Set<string>>(new Set());
  divReconcileDone = signal<number | null>(null);
  divUploads = signal<DivStatementUpload[]>([]);

  loadDivUploads() { this.api.dividendReconcileUploads().subscribe({ next: u => this.divUploads.set(u || []), error: () => {} }); }
  reconciledTill(person: string): string | null {
    let best: string | null = null;
    for (const u of this.divUploads()) {
      if (!u.date_to) continue;
      if (u.person && u.person !== person) continue;
      if (!best || u.date_to > best) best = u.date_to;
    }
    return best;
  }

  openDivReconcile(person = '') {
    this.divReconcilePerson.set(person);
    this.divReconcileOpen.set(true);
    this.divReconcileResult.set(null); this.divReconcileError.set(null);
    this.divReconcileFileName.set(''); this.divReconcileDone.set(null);
    if (!this.divUploads().length) this.loadDivUploads();
  }
  closeDivReconcile() { this.divReconcileOpen.set(false); }

  onDivReconcileFile(ev: Event) {
    const input = ev.target as HTMLInputElement;
    const f = input.files?.[0]; input.value = '';
    if (!f) return;
    this.divReconcileFileName.set(f.name);
    this.divReconcileBusy.set(true); this.divReconcileError.set(null); this.divReconcileResult.set(null); this.divReconcileDone.set(null);
    this.api.dividendReconcilePreview(f, this.divReconcilePerson()).subscribe({
      next: r => {
        this.divReconcileBusy.set(false);
        this.divReconcileResult.set(r);
        this.divReconcileAccepted.set(new Set(r.matched.map(m => m.div_id)));   // pre-tick confident ones
      },
      error: (e: HttpErrorResponse) => {
        this.divReconcileBusy.set(false);
        this.divReconcileError.set(e.error?.detail || 'Could not read the statement. Export it as Excel/CSV and retry.');
      },
    });
  }

  isDivAccepted(m: DivReconcileMatch): boolean { return this.divReconcileAccepted().has(m.div_id); }
  toggleDivAccept(m: DivReconcileMatch) {
    this.divReconcileAccepted.update(s => { const n = new Set(s); n.has(m.div_id) ? n.delete(m.div_id) : n.add(m.div_id); return n; });
  }
  divAcceptedCount = computed(() => this.divReconcileAccepted().size);

  confirmDivReconcile() {
    const res = this.divReconcileResult(); if (!res) return;
    const accepted = [...res.matched, ...res.review].filter(m => this.isDivAccepted(m));
    const ids = accepted.map(m => m.div_id);
    if (!ids.length) { this.closeDivReconcile(); return; }
    const allDates = [...res.matched, ...res.review, ...res.already].map(m => m.credit_date)
      .concat(res.unmatched.map(u => u.date)).filter(Boolean).sort();
    const meta = {
      filename: this.divReconcileFileName() || undefined,
      date_from: allDates[0] || null, date_to: allDates[allDates.length - 1] || null,
      person: this.divReconcilePerson() || undefined,
      credits: res.counts.credits,
      amount: Math.round(accepted.reduce((s, m) => s + (m.credit_amount || 0), 0) * 100) / 100,
    };
    this.divReconcileBusy.set(true); this.divReconcileError.set(null);
    this.api.dividendReconcileConfirm(ids, meta).subscribe({
      next: r => {
        this.divReconcileBusy.set(false);
        this.divReconcileDone.set(r.marked);
        this.store.load(true);           // refresh the dividend log → calendar/table turn green
        this.loadDivUploads();
        setTimeout(() => this.divReconcileOpen.set(false), 1400);
      },
      error: (e: HttpErrorResponse) => { this.divReconcileBusy.set(false); this.divReconcileError.set('Could not save — ' + (e.error?.detail || 'try again.')); },
    });
  }

  fmtDateTime(d: string | null): string {
    if (!d) return '—';
    const dt = new Date(d);
    return isNaN(dt.getTime()) ? d : dt.toLocaleDateString('en-IN', { day: '2-digit', month: 'short' }) + ', ' + dt.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' });
  }
  fmtDay(d: string | null): string {
    if (!d) return '—';
    const dt = new Date((d || '').slice(0, 10));
    return isNaN(dt.getTime()) ? (d || '') : dt.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' });
  }
  statusLabel(s: DividendStatus): string {
    return s === 'received' ? 'Dividend received' : s === 'not_received' ? 'Not received' : 'Expected dividend';
  }
  remove(d: Dividend) {
    if (!confirm(`Delete ${d.symbol} dividend (${this.inr(d.amount)})?`)) return;
    this.store.remove(d.id);
  }

  // ── per-symbol "received in previous years" (auto + manual override) ──────────
  /** Indian FY (Apr–Mar) that today falls in. */
  private curFyStart = this.now.getMonth() >= 3 ? this.now.getFullYear() : this.now.getFullYear() - 1;
  /** auto = Σ received dividends for this stock from financial years before now */
  autoPrev(symbol: string): number {
    const k = this.base(symbol);
    return this.divs().filter(d => d.received && this.base(d.symbol) === k && DividendStore.fyStart(d.date) < this.curFyStart)
      .reduce((a, b) => a + b.amount, 0);
  }
  manualPrev(symbol: string): number | null {
    const v = this.store.meta()[this.base(symbol)];
    return v == null ? null : v;
  }
  /** the figure we actually show: manual override wins over the auto sum */
  effectivePrev(symbol: string): number {
    const m = this.manualPrev(symbol);
    return m != null ? m : this.autoPrev(symbol);
  }
  setManualPrev(symbol: string, v: number | null) {
    const n = (v == null || isNaN(v as number) || (v as number) < 0) ? null : Number(v);
    this.store.setMeta(this.base(symbol), n);
  }

  // ── per-person breakdown (different holdings → different dividend) ────────────
  /** YYYY or YYYY-MM prefix for the period currently in view. */
  private periodPre = computed(() => {
    if (this.mode() === 'calendar') return `${this.viewYear()}-${this.p2(this.viewMonth() + 1)}`;
    if (this.mode() === 'people') return `${this.trendYear()}-`;
    if (this.selectedMonth() != null) return `${this.trendYear()}-${this.p2(this.selectedMonth()! + 1)}`;
    return `${this.trendYear()}-`;
  });
  periodLabel = computed(() => this.mode() === 'people' ? `${this.trendYear()}` : this.payerBar().label);

  /** Each person's dividend for the period: their slice of every stock (by share
   * count) split into received vs pending, then net of their own TDS. Uses the
   * full holdings (not account-scoped) so everyone shows. */
  byPerson = computed(() => {
    const pre = this.periodPre();
    const hbs = this.holdingsBySym();
    const acc: Record<string, { gross: number; received: number }> = {};
    for (const d of this.divs().filter(x => (x.date || '').startsWith(pre))) {
      const holders = hbs[this.base(d.symbol)] || [];
      const tq = holders.reduce((a, b) => a + b.qty, 0);
      if (tq) {
        for (const h of holders) {
          const share = d.amount * h.qty / tq;
          (acc[h.person] ||= { gross: 0, received: 0 }).gross += share;
          if (d.received) acc[h.person].received += share;
        }
      } else {
        (acc['Unassigned'] ||= { gross: 0, received: 0 }).gross += d.amount;
        if (d.received) acc['Unassigned'].received += d.amount;
      }
    }
    return Object.entries(acc).map(([person, v]) => {
      const rate = this.store.tdsFor(person === 'Unassigned' ? '' : person);
      // net = what actually reached the account = collected, less their TDS
      return { person, gross: v.gross, received: v.received, pending: v.gross - v.received, rate, net: v.received * (1 - rate) };
    }).sort((a, b) => b.gross - a.gross);
  });
  byPersonTotal = computed(() => this.byPerson().reduce((a, b) => a + b.gross, 0));
  byPersonReceived = computed(() => this.byPerson().reduce((a, b) => a + b.received, 0));
  byPersonPending = computed(() => this.byPerson().reduce((a, b) => a + b.pending, 0));
  byPersonNet = computed(() => this.byPerson().reduce((a, b) => a + b.net, 0));

  // per-person TDS (editable here too — shared with the holdings page)
  private tdsKey(person: string): string { return person === 'Unassigned' ? '' : (person || '').trim(); }
  tdsPct(person: string): number | null {
    const t = this.store.tds()[this.tdsKey(person)];
    return t == null ? null : Math.round(t * 1000) / 10;
  }
  defaultTdsPct = computed(() => Math.round(this.store.defaultTds() * 1000) / 10);
  setTdsPct(person: string, v: any) {
    const n = (v === '' || v == null || isNaN(+v) || +v < 0) ? null : Math.min(100, +v) / 100;
    this.store.setTds(this.tdsKey(person), n);
  }
  /** Type the actual amount that hit the account → back-derive & store TDS. */
  setNetFor(person: string, received: number, v: any) {
    const net = +v;
    if (received <= 0 || v === '' || v == null || isNaN(net) || net < 0) return;
    this.store.setTds(this.tdsKey(person), Math.max(0, Math.min(1, 1 - net / received)));
  }

  /** Inline-edit a dividend's per-share / shares from the day modal. */
  editDiv(d: Dividend, field: 'per_share' | 'shares', v: any) {
    const n = +v;
    if (v === '' || v == null || isNaN(n) || n <= 0) return;
    this.store.update(d.id, { [field]: n });
  }
}
