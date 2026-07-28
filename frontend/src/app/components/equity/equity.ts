import { Component, OnInit, OnDestroy, inject, signal, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpErrorResponse } from '@angular/common/http';
import { EquityService, EquitySummary, EquityAccount, StockRow, HoldingDetail, Dividend, DividendStatus, IndexQuote, DivReconcileResult, DivStatementUpload, HarvestRecord, HarvestPreview } from '../../services/equity.service';
import { MarketService } from '../../services/market.service';
import { Dividends } from '../dividends/dividends';
import { Perf } from '../perf/perf';
import { StockTax } from '../stock-tax/stock-tax';
import { DividendStore } from '../../services/dividend-store';

/** A payout event = all dividend records that share a date + currency, shown as
 *  one collapsible row and expanded to their per-account split. */
interface DivGroup { key: string; date: string; currency: 'INR' | 'USD'; dvs: Dividend[]; }

/** Withholding the bank deducts on dividends. Real Indian TDS is 10% above
 * ₹5k/yr, but the planning sheet this mirrors uses a flat 20% — kept here as a
 * single editable constant. */
const DIV_TAX_RATE = 0.20;

@Component({
  selector: 'app-equity',
  standalone: true,
  imports: [CommonModule, FormsModule, Dividends, Perf, StockTax],
  templateUrl: './equity.html',
  styleUrl: './equity.scss',
})
export class Equity implements OnInit, OnDestroy {
  // Kuber — the on-page stock-tax assistant (embedded FAB, like Bhoomi on Land)
  assistantOpen = signal(false);
  assistantExpanded = signal(false);   // compact popup ⇄ big drawer
  assistantImg = '/kuber-stock-tax.png';
  assistantImgOk = signal(true);
  closeAssistant() { this.assistantOpen.set(false); this.assistantExpanded.set(false); }

  private api = inject(EquityService);
  private divStore = inject(DividendStore);
  market = inject(MarketService);

  summary = signal<EquitySummary | null>(null);
  indices = signal<IndexQuote[]>([]);          // headline market indices for the hero
  loading = signal(true);
  error = signal<string | null>(null);
  needsMigration = signal(false);

  // live polling (only while the market is open + tab visible)
  lastUpdated = signal<Date | null>(null);     // when we last polled
  lastChanged = signal<Date | null>(null);     // when the VALUE last actually changed
  justRefreshed = signal(false);               // brief flash when fresh data lands
  now = signal(Date.now());                     // ticks each second → live "x ago" labels
  private _prevSig = '';
  private _poll: any = null;
  private _clock: any = null;
  private readonly POLL_MS = 20000;

  inr = EquityService.inr;
  pct = EquityService.pct;

  // add-account form
  showAdd = signal(false);
  draft = signal({ broker: 'kite', person: '', account_label: '', api_key: '', api_secret: '', import_broker: 'icici' });
  adding = signal(false);

  // file-import (upload + preview + confirm)
  uploadFor = signal<{ id: string; label: string } | null>(null);
  selectedFile: File | null = null;
  previewRows = signal<any[] | null>(null);
  previewMeta = signal<{ count: number; matched: number } | null>(null);
  previewing = signal(false);
  importingFile = signal(false);
  brokerName(b: string): string {
    return ({ icici: 'ICICI Direct', ibkr: 'IBKR', motilal: 'Motilal Oswal', kite: 'Zerodha', zebu: 'Zebu' } as any)[b] || b;
  }

  // connect / re-login modal (shared by "add" and "re-login")
  connectModal = signal<{ id: string; login_url: string; label: string } | null>(null);
  requestToken = signal('');
  connecting = signal(false);
  loggingInId = signal<string | null>(null);   // account whose Kite login popup is open
  loggingOffId = signal<string | null>(null);  // account being logged off
  refreshingId = signal<string | null>(null);

  // account picker (Tickertape-style dropdown in the header)
  pickerOpen = signal(false);
  // inline row edit (label + person) inside the picker
  editingAcct = signal<string | null>(null);
  editDraft = signal({ person: '', account_label: '', api_key: '', api_secret: '' });
  savingEdit = signal(false);

  // account selection (multi) — empty set = all accounts; drives the whole page
  accountSel = signal<Set<string>>(new Set());
  isAllAcc = computed(() => this.accountSel().size === 0);
  selectedIdsArr = computed(() => [...this.accountSel()]);
  selectedAccounts = computed<EquityAccount[]>(() => {
    const s = this.accountSel();
    return s.size ? this.accounts().filter(a => s.has(a.id)) : this.accounts();
  });
  accSelLabel = computed(() => {
    const s = this.accountSel();
    if (!s.size) return 'All accounts';
    if (s.size === 1) { const a = this.accounts().find(x => s.has(x.id)); return a ? a.account_label : '1 account'; }
    return `${s.size} accounts`;
  });

  /** distinct people across accounts — powers the person datalist for quick linking */
  personOptions = computed(() => Array.from(new Set(this.accounts().map(a => a.person).filter(Boolean))) as string[]);

  ngOnInit() {
    this.load();
    this.loadHarvest();                                               // LTCG booked / room left per person
    this.loadIndices();                                               // headline market indices
    this.loadManual();                                                // manual LTP overrides
    this.divStore.load();                                             // shared with the calendar
    this._poll = setInterval(() => this.tick(), this.POLL_MS);
    this._clock = setInterval(() => this.now.set(Date.now()), 1000);  // live relative labels
    // Refresh account connection status when the tab regains focus — so an F&O
    // logoff (which also clears this person's Stocks session) shows up here even
    // when the market is closed (the poll above is market-hours only).
    if (typeof document !== 'undefined') {
      document.addEventListener('visibilitychange', this._onVisible);
      window.addEventListener('focus', this._onVisible);
    }
  }
  private _onVisible = () => {
    if (typeof document !== 'undefined' && document.visibilityState !== 'visible') return;
    if (this.needsMigration() || !this.summary()) return;
    this.api.summary().subscribe({ next: s => this._applySummary(s), error: () => {} });
  };
  ngOnDestroy() {
    if (this._poll) clearInterval(this._poll); if (this._clock) clearInterval(this._clock);
    this.stopConnPoll();
    if (typeof document !== 'undefined') {
      document.removeEventListener('visibilitychange', this._onVisible);
      window.removeEventListener('focus', this._onVisible);
    }
  }

  /** Apply a freshly-fetched summary, detecting whether the VALUE actually moved
   *  (vs the poll just returning the same numbers) so we can show a real
   *  "updated" time + flash — not merely "we polled". */
  private _applySummary(s: EquitySummary): void {
    const sig = Math.round((s.total_value || 0) * 100) + ':' + Math.round((s.day_change || 0) * 100);
    const changed = this._prevSig !== '' && sig !== this._prevSig;
    if (changed || this._prevSig === '') {
      this.lastChanged.set(new Date());
      if (changed) {
        this.justRefreshed.set(true);
        setTimeout(() => this.justRefreshed.set(false), 1400);
      }
    }
    this._prevSig = sig;
    this.summary.set(s);
    this.lastUpdated.set(new Date());
  }

  load() {
    this.loading.set(true); this.error.set(null); this.needsMigration.set(false);
    this.api.summary().subscribe({
      next: s => { this._applySummary(s); this.loading.set(false); },
      error: (e: HttpErrorResponse) => {
        this.loading.set(false);
        if (e.status === 503) this.needsMigration.set(true);
        else this.error.set('Could not reach the API. Is the backend running?');
      },
    });
  }

  /** Silent refresh on the timer — only when the market is open and the tab is
   *  visible. Updates values in place (no spinner) for a live feel. */
  private tick() {
    if (!this.market.isOpen()) return;
    if (typeof document !== 'undefined' && document.visibilityState !== 'visible') return;
    this.loadIndices();                                               // headline indices refresh with the market
    if (this.needsMigration() || !this.summary()) return;
    this.api.summary().subscribe({ next: s => this._applySummary(s), error: () => {} });
  }

  /** Pull the headline market indices for the hero (silent — keep old values on error). */
  loadIndices() {
    this.api.indices().subscribe({ next: r => this.indices.set(r?.indices || []), error: () => {} });
  }

  /** Relative time since the value last changed: "just now", "8s ago", "3m ago". */
  sinceChanged(): string {
    const d = this.lastChanged();
    if (!d) return '';
    const sec = Math.max(0, Math.round((this.now() - d.getTime()) / 1000));
    if (sec < 5) return 'just now';
    if (sec < 60) return `${sec}s ago`;
    const min = Math.floor(sec / 60);
    if (min < 60) return `${min}m ago`;
    return d.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' });
  }
  /** Full label for the live "updated" pill. While a market is open we show how
   *  long since the value actually changed; when closed we make clear the prices
   *  are the last close (not a 2-min-fresh quote). */
  updatedText(): string {
    if (!this.lastChanged()) return '';
    return this.market.isOpen()
      ? 'Prices updated ' + this.sinceChanged()
      : 'Markets closed · showing last close';
  }

  updatedLabel(): string {
    const d = this.lastUpdated();
    return d ? d.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '';
  }

  accounts = computed(() => this.summary()?.accounts || []);
  persons = computed(() => this.summary()?.by_person || []);

  // ── holdings filters / sort ─────────────────────────────────────────────────
  holdSearch = signal('');
  holdStatus = signal<Set<string>>(new Set());   // multi-select: profit/loss/up/down (empty = all)
  statusOn(s: string): boolean { return this.holdStatus().has(s); }
  toggleStatus(s: string) { this.holdStatus.update(set => { const n = new Set(set); n.has(s) ? n.delete(s) : n.add(s); return n; }); }
  holdPerson = signal('');
  holdBroker = signal('');
  holdExchange = signal('');
  sortKey = signal<'value' | 'pnl' | 'pnl_pct' | 'day_change' | 'day_change_pct' | 'quantity' | 'symbol' | 'div' | 'div_ps' | 'record'>('value');
  sortDir = signal<'asc' | 'desc'>('desc');
  toggleSort(k: 'value' | 'pnl' | 'pnl_pct' | 'day_change' | 'day_change_pct' | 'quantity' | 'symbol' | 'div' | 'div_ps' | 'record') {
    if (this.sortKey() === k) { this.sortDir.update(d => d === 'desc' ? 'asc' : 'desc'); }
    else { this.sortKey.set(k); this.sortDir.set(k === 'symbol' ? 'asc' : 'desc'); }
  }
  divOnly = signal(false);   // holdings filter: only stocks with a dividend plan
  sortArrow(k: string): string { return this.sortKey() === k ? (this.sortDir() === 'desc' ? ' ▼' : ' ▲') : ''; }

  // ── unified filter panel (all chips/selects live here now) ──────────────────
  // A single "Filter" button opens a two-column popover: left = filter category,
  // right = its options. Categories are declared as data so adding one later is a
  // one-line change (strategy/registry pattern) — the template renders generically.
  showFilter = signal(false);
  filterCat = signal<'status' | 'dividends' | 'needsprice' | 'broker'>('status');
  /** dividend-month filter: show only stocks that paid/plan a dividend in Y-M. */
  divFilterYear = signal(0);
  divFilterMonth = signal(0);                       // 1–12, 0 = unset
  divMonthActive = computed(() => this.divFilterYear() > 0 && this.divFilterMonth() > 0);
  setDivMonth(y: number, m: number) { this.divFilterYear.set(+y || 0); this.divFilterMonth.set(+m || 0); }
  clearDivMonth() { this.divFilterYear.set(0); this.divFilterMonth.set(0); }
  /** base symbols that have a dividend in the selected month (null = no filter). */
  divPayersInMonth = computed<Set<string> | null>(() => {
    if (!this.divMonthActive()) return null;
    const pre = `${this.divFilterYear()}-${String(this.divFilterMonth()).padStart(2, '0')}`;
    const set = new Set<string>();
    for (const d of this.divStore.divs()) if ((d.date || '').startsWith(pre)) set.add(DividendStore.base(d.symbol));
    return set;
  });
  /** years that appear in the dividend log (for the month-filter year dropdown). */
  divYears = computed<number[]>(() => {
    const ys = new Set<number>();
    for (const d of this.divStore.divs()) { const y = +(d.date || '').slice(0, 4); if (y) ys.add(y); }
    ys.add(new Date().getFullYear());
    return Array.from(ys).sort((a, b) => b - a);
  });
  monthNames = ['', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

  /** left-column categories — needs-price/broker appear only when useful. */
  filterCats = computed(() => {
    const cats: { key: any; label: string; active: () => boolean }[] = [
      { key: 'status', label: 'Performance', active: () => this.holdStatus().size > 0 },
      { key: 'dividends', label: 'Dividends', active: () => this.divOnly() || this.divMonthActive() },
    ];
    if (this.hasNeedsPrice() || this.needsPrice()) cats.push({ key: 'needsprice', label: 'Needs price', active: () => this.needsPrice() });
    if (this.brokerOptions().length > 1) cats.push({ key: 'broker', label: 'Broker', active: () => !!this.holdBroker() });
    return cats;
  });
  /** count of active filters — drives the button badge. */
  activeFilterCount = computed(() => {
    let n = 0;
    if (this.holdStatus().size > 0) n++;
    if (this.divOnly()) n++;
    if (this.divMonthActive()) n++;
    if (this.needsPrice()) n++;
    if (this.holdBroker()) n++;
    return n;
  });

  /** Called from the calendar (child) — filter holdings to that month's payers. */
  onDivMonth(ev: { year: number; month: number }) {
    this.setDivMonth(ev.year, ev.month);
    this.showFilter.set(false);
    if (typeof document !== 'undefined')
      setTimeout(() => document.getElementById('holdings-top')?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 60);
  }

  // ── Per-stock dividend panel (click a holding row) ───────────────────────────
  // Same Supabase records the calendar uses (via DividendStore), scoped to the
  // stock: list each dividend (record date, ₹/share, gross, 20% TDS, net), mark
  // it received (green) or pending (yellow), add an expected one, and see the
  // FY summary + yield. "Previous years" = auto sum of older received dividends,
  // overridable with a manual figure. All shared live with the calendar.
  expanded = signal<string | null>(null);    // symbol whose dividend panel is open
  divTaxRate = DIV_TAX_RATE;
  divDraft = signal<{ date: string; per_share: number | null }>({ date: '', per_share: null });
  divErr = signal<string | null>(null);
  private _now = new Date();
  private curFyStart = this._now.getMonth() >= 3 ? this._now.getFullYear() : this._now.getFullYear() - 1;

  toggleExpand(sym: string) {
    const open = this.expanded() === sym ? null : sym;
    this.expanded.set(open);
    this.divErr.set(null);
    this.divAcct.set('');
    this.expandedDiv.set(null);
    if (open) {
      // prefill the ₹/share with this stock's last known rate (user can change it)
      this.divDraft.set({ date: this._todayStr(), per_share: this.lastDivPerShare(open) });
      // default to the tab that has something in it — Upcoming if there's any to log
      this.divTab.set(this.upcomingFor(open).length ? 'upcoming' : 'logged');
      // pre-fill the screener editor with the current link, or the standard derived URL
      const h = this.displayStocks().find(s => s.symbol === open);
      this.screenerDraft.set(h?.screener_url || `https://www.screener.in/company/${open.toUpperCase()}/`);
    } else {
      this.divDraft.set({ date: this._todayStr(), per_share: null });
    }
  }
  isExpanded(sym: string): boolean { return this.expanded() === sym; }

  // ── Screener.in links ────────────────────────────────────────────────────────
  // Linked logos carry a green dot and open Screener in one click. Setting or
  // changing a link happens in the expanded row (see toggleExpand → draft).
  screenerDraft = signal('');
  savingScreener = signal(false);

  hasScreener(h: StockRow): boolean { return !!(h.screener_url && h.screener_url.trim()); }

  /** Logo click: linked → open Screener directly; unlinked → do nothing here so
   *  the click bubbles to the row and expands it (where the editor lives). */
  onLogoClick(ev: Event, h: StockRow) {
    if (this.hasScreener(h)) {
      ev.stopPropagation();
      window.open(h.screener_url!, '_blank', 'noopener');
    }
  }

  saveScreener(h: StockRow) {
    const url = this.screenerDraft().trim();
    if (!url) return;
    this.savingScreener.set(true);
    this.api.setScreenerLink(h.symbol, url).subscribe({
      next: () => { h.screener_url = url; this.savingScreener.set(false); },   // optimistic
      error: () => this.savingScreener.set(false),
    });
  }
  clearScreener(h: StockRow) {
    this.api.setScreenerLink(h.symbol, null).subscribe({
      next: () => { h.screener_url = null; this.screenerDraft.set(`https://www.screener.in/company/${(h.symbol || '').toUpperCase()}/`); },
    });
  }
  private _todayStr(): string {
    const d = this._now, p = (n: number) => (n < 10 ? '0' + n : '' + n);
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
  }

  /** Every dividend record for a stock (newest first). */
  dividendsFor(sym: string): Dividend[] { return this.divStore.forSymbol(sym); }
  hasDiv(sym: string): boolean { return this.dividendsFor(sym).length > 0; }

  // ── 2-tab dividend view: Upcoming (all expanded, log per account) · Logged (collapsed accordion)
  divTab = signal<'upcoming' | 'logged'>('upcoming');
  expandedDiv = signal<string | null>(null);   // which logged row is open
  toggleDivRow(id: string) { this.expandedDiv.set(this.expandedDiv() === id ? null : id); }
  loggedFor(sym: string): Dividend[] { return this.dividendsFor(sym).filter(d => d.received); }
  upcomingFor(sym: string): Dividend[] { return this.dividendsFor(sym).filter(d => !d.received); }
  loggedTotal(sym: string): number { return this.loggedFor(sym).reduce((s, d) => s + d.amount, 0); }
  /** avg ₹/share actually received (weighted by shares → a true blended rate) */
  loggedAvgPs(sym: string): number {
    const l = this.loggedFor(sym); const sh = l.reduce((s, d) => s + (d.shares || 0), 0);
    return sh ? l.reduce((s, d) => s + (d.per_share || 0) * (d.shares || 0), 0) / sh : 0;
  }
  markLogged(dv: Dividend) { this.divStore.setStatus(dv.id, 'received'); }

  // ── Per-account reconciliation of an upcoming payout ─────────────────────────
  // Each account is handled INDEPENDENTLY: enter its ₹/share (total = ×its shares)
  // and mark it received or not. "Add as received" unlocks only once EVERY account
  // has a rate; logging then writes one per-account record and moves it to Logged.
  // The per-account rate/received marks live on the SINGLETON store (survive
  // navigation) and are persisted to the durable KV (survive refresh/revert).
  private rkey(dvId: string, accId: string): string { return dvId + '|' + accId; }
  private recon(dvId: string, accId: string, dvPs: number | null): { ps: number | null; received: boolean } {
    // Accounts start PENDING — you tick each as its dividend actually lands.
    return this.divStore.reconAt(this.rkey(dvId, accId)) ?? { ps: dvPs ?? null, received: false };
  }
  reconPs(dv: Dividend, accId: string): number | null { return this.recon(dv.id, accId, dv.per_share).ps; }
  reconReceived(dv: Dividend, accId: string): boolean { return this.recon(dv.id, accId, dv.per_share).received; }
  setReconPs(dv: Dividend, accId: string, v: any) {
    const ps = (v === '' || v == null || isNaN(+v)) ? null : Math.max(0, +v);
    const cur = this.recon(dv.id, accId, dv.per_share);
    this.divStore.setRecon(this.rkey(dv.id, accId), { ...cur, ps });
  }
  setReconReceived(dv: Dividend, accId: string, received: boolean) {
    const cur = this.recon(dv.id, accId, dv.per_share);
    this.divStore.setRecon(this.rkey(dv.id, accId), { ...cur, received });
  }
  /** Enter the TOTAL ₹ actually credited for an account → back-calc its ₹/share
   *  (total ÷ shares). Two-way: editing the rate or the total keeps the other in sync. */
  setReconTotal(dv: Dividend, acc: { id: string; shares: number }, v: any) {
    const total = (v === '' || v == null || isNaN(+v)) ? null : Math.max(0, +v);
    const ps = (total != null && acc.shares > 0) ? Math.round((total / acc.shares) * 1e6) / 1e6 : null;
    this.setReconPs(dv, acc.id, ps);
  }
  /** Same for a LOGGED record — type the total received, store the implied ₹/share. */
  setLoggedTotal(dv: Dividend, v: any) {
    const total = (v === '' || v == null || isNaN(+v)) ? 0 : Math.max(0, +v);
    const ps = (dv.shares || 0) > 0 ? Math.round((total / dv.shares) * 1e6) / 1e6 : 0;
    this.editDiv(dv, 'per_share', ps);
  }
  /** Accounts this upcoming row covers: all of them for a portfolio-level event,
   * or just the tagged one for a per-account (already-split) pending row. */
  reconAccounts(dv: Dividend, h: StockRow): { id: string; person: string; label: string; broker: string; shares: number }[] {
    const all = this.accountsFor(h.symbol);
    if (!dv.account_id) return all;
    const a = all.find(x => x.id === dv.account_id);
    return [a ?? { id: dv.account_id, person: dv.person || '—', label: this.divAcctLabel(dv) || dv.account_id, broker: '', shares: dv.shares }];
  }
  reconTotal(dv: Dividend, acc: { id: string; shares: number }): number { return (this.reconPs(dv, acc.id) || 0) * acc.shares; }
  // Display helpers — trim binary-float noise so the bound <input> shows a clean number
  // (e.g. total 157 → ÷11 → ×11 = 156.999997 would otherwise render). Bound via [value],
  // committed on (change), so typing is never overwritten mid-keystroke.
  reconPsView(dv: Dividend, accId: string): number | null {
    const ps = this.reconPs(dv, accId);
    return ps == null ? null : Math.round(ps * 1e4) / 1e4;
  }
  reconTotalView(dv: Dividend, acc: { id: string; shares: number }): number | null {
    const t = Math.round(this.reconTotal(dv, acc) * 100) / 100;
    return t > 0 ? t : null;
  }
  loggedTotalView(dv: Dividend): number | null {
    const t = Math.round((dv.per_share || 0) * dv.shares * 100) / 100;
    return t > 0 ? t : null;
  }
  /** every account has a positive rate → the log button unlocks */
  reconAllPriced(dv: Dividend, accts: { id: string }[]): boolean {
    return accts.length > 0 && accts.every(a => (this.reconPs(dv, a.id) || 0) > 0);
  }
  reconReceivedCount(dv: Dividend, accts: { id: string }[]): number {
    return accts.filter(a => this.reconReceived(dv, a.id)).length;
  }
  /** total ₹ of the accounts marked received (the amount that lands in Logged) */
  reconReceivedTotal(dv: Dividend, accts: { id: string; shares: number }[]): number {
    return accts.reduce((s, a) => s + (this.reconReceived(dv, a.id) ? this.reconTotal(dv, a) : 0), 0);
  }
  /** Log the reconciled payout: one record per account — RECEIVED ones go to
   * Logged; NOT-YET ones stay in Upcoming as their own per-account pending rows.
   * The original event is then dropped. Only unlocks once every account is priced. */
  logRecon(dv: Dividend, h: StockRow) {
    const accts = this.reconAccounts(dv, h);
    const received = accts.filter(a => this.reconReceived(dv, a.id));
    if (!received.length) return;                                  // nothing ticked → nothing to log
    if (received.some(a => (this.reconPs(dv, a.id) || 0) <= 0)) return;  // received rows must be priced
    const cur: 'INR' | 'USD' = this.isUsd(dv.currency) ? 'USD' : 'INR';
    let anyReceived = false;
    for (const a of accts) {
      const ps = this.reconPs(dv, a.id) || 0;
      const received = this.reconReceived(dv, a.id);
      if (received) anyReceived = true;
      const status: DividendStatus = received ? 'received' : 'pending';
      this.divStore.add({ date: dv.date, symbol: h.symbol, name: h.name || undefined,
        per_share: ps, shares: a.shares, status, received,
        currency: cur, account_id: a.id, person: a.person }).subscribe();
    }
    this.divStore.remove(dv.id);
    this.divStore.clearReconFor(dv.id);
    if (anyReceived) this.divTab.set('logged');
  }
  markUpcoming(dv: Dividend) { this.divStore.setStatus(dv.id, 'pending'); }

  // ── Payout grouping: ONE collapsible row per event (date+currency), expandable
  // to its per-account split. Keeps the list compact — a stock paid across 6
  // accounts is a single row, not six cards.
  expandedGroup = signal<string | null>(null);
  toggleGroup(key: string) { this.expandedGroup.set(this.expandedGroup() === key ? null : key); }
  isGroupOpen(key: string): boolean { return this.expandedGroup() === key; }
  private groupKey(d: Dividend): string { return (d.date || '') + '|' + (this.isUsd(d.currency) ? 'USD' : 'INR'); }
  private groupDivs(list: Dividend[]): DivGroup[] {
    const m = new Map<string, Dividend[]>();
    for (const d of list) { const k = this.groupKey(d); const g = m.get(k); if (g) g.push(d); else m.set(k, [d]); }
    return [...m.entries()]
      .map(([key, dvs]) => ({ key, date: dvs[0].date, currency: (this.isUsd(dvs[0].currency) ? 'USD' : 'INR') as 'INR' | 'USD', dvs }))
      .sort((a, b) => (b.date || '').localeCompare(a.date || ''));
  }
  upcomingGroups(sym: string): DivGroup[] { return this.groupDivs(this.upcomingFor(sym)); }
  loggedGroups(sym: string): DivGroup[] { return this.groupDivs(this.loggedFor(sym)); }

  /** Flatten a group into per-account {dv, acc} lines (a portfolio-level payout
   *  fans out to every account; a tagged one is a single line). */
  groupLines(g: DivGroup, h: StockRow): { dv: Dividend; acc: { id: string; person: string; label: string; broker: string; shares: number } }[] {
    const out: { dv: Dividend; acc: { id: string; person: string; label: string; broker: string; shares: number } }[] = [];
    for (const dv of g.dvs) for (const acc of this.reconAccounts(dv, h)) out.push({ dv, acc });
    return out;
  }
  // Upcoming-group aggregates (drive the collapsed row + footer)
  groupUpShares(g: DivGroup, h: StockRow): number { return this.groupLines(g, h).reduce((s, l) => s + l.acc.shares, 0); }
  groupUpAccounts(g: DivGroup, h: StockRow): number { return this.groupLines(g, h).length; }
  groupUpExpected(g: DivGroup, h: StockRow): number { return this.groupLines(g, h).reduce((s, l) => s + this.reconTotal(l.dv, l.acc), 0); }
  groupUpReceivedCount(g: DivGroup, h: StockRow): number { return this.groupLines(g, h).filter(l => this.reconReceived(l.dv, l.acc.id)).length; }
  groupUpReceivedTotal(g: DivGroup, h: StockRow): number { return this.groupLines(g, h).reduce((s, l) => s + (this.reconReceived(l.dv, l.acc.id) ? this.reconTotal(l.dv, l.acc) : 0), 0); }
  groupUpAllPriced(g: DivGroup, h: StockRow): boolean { const ls = this.groupLines(g, h); return ls.length > 0 && ls.every(l => (this.reconPs(l.dv, l.acc.id) || 0) > 0); }
  // Pending (yet to receive) vs received (ticked) split of a payout's account lines.
  groupPending(g: DivGroup, h: StockRow) { return this.groupLines(g, h).filter(l => !this.reconReceived(l.dv, l.acc.id)); }
  groupReceived(g: DivGroup, h: StockRow) { return this.groupLines(g, h).filter(l => this.reconReceived(l.dv, l.acc.id)); }
  // Collapsible "received" section per group.
  private recvdOpenKey = signal<string | null>(null);
  recvdOpen(key: string): boolean { return this.recvdOpenKey() === key; }
  toggleRecvd(key: string) { this.recvdOpenKey.set(this.recvdOpenKey() === key ? null : key); }
  /** Log the received accounts of a payout; any still-pending accounts stay in Upcoming. */
  logGroup(g: DivGroup, h: StockRow) { if (!this.groupUpReceivedCount(g, h)) return; for (const dv of [...g.dvs]) this.logRecon(dv, h); }
  // Logged-group aggregates
  groupShares(g: DivGroup): number { return g.dvs.reduce((s, d) => s + (d.shares || 0), 0); }
  groupLoggedTotal(g: DivGroup): number { return g.dvs.reduce((s, d) => s + (d.amount || 0), 0); }
  /** Set the date on every account line of a payout at once. */
  editGroupDate(g: DivGroup, v: string) { for (const dv of g.dvs) this.editDiv(dv, 'date', v); }
  /** Remove every account line of an upcoming payout. */
  removeGroup(g: DivGroup) { for (const dv of [...g.dvs]) this.removeDiv(dv); }
  /** "03 Jul 2026" from an ISO date. */
  fmtDivDate(d: string): string {
    if (!d) return '—';
    const dt = new Date(d);
    return isNaN(dt.getTime()) ? d : dt.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' });
  }

  /** Most recent (or upcoming) dividend record date for a stock, formatted. */
  recordDate(sym: string): string {
    const d = this.dividendsFor(sym)[0];        // forSymbol() is newest-first
    if (!d?.date) return '';
    const dt = new Date(d.date);
    return isNaN(dt.getTime()) ? '' : dt.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' });
  }
  /** raw ISO record date (sortable), '' when the stock pays no dividend. */
  recordDateRaw(sym: string): string { return this.dividendsFor(sym)[0]?.date || ''; }
  /** Per-share amount of the most recently declared dividend, or null if none. */
  lastDivPerShare(sym: string): number | null { return this.dividendsFor(sym)[0]?.per_share ?? null; }
  /** Currency of that latest dividend (for the ₹/$ prefix). */
  lastDivCurrency(sym: string): 'INR' | 'USD' { return this.dividendsFor(sym)[0]?.currency === 'USD' ? 'USD' : 'INR'; }
  divCount = computed(() => {
    const seen = new Set(this.divStore.divs().map(d => DividendStore.base(d.symbol)));
    return this.filteredStocks().filter(s => seen.has(DividendStore.base(s.symbol))).length;
  });

  /** Records for this stock in the current financial year. */
  private divThisFy(sym: string): Dividend[] {
    return this.dividendsFor(sym).filter(d => DividendStore.fyStart(d.date) === this.curFyStart);
  }
  /** Expected annual dividend (this FY, gross) — drives the column, sort, yield. */
  annualDividend(sym: string): number { return this.divThisFy(sym).reduce((a, b) => a + b.amount, 0); }
  /** this-FY dividend in the stock's native currency (per_share × shares). */
  annualDividendNative(sym: string): number { return this.divThisFy(sym).reduce((a, b) => a + (b.per_share || 0) * (b.shares || 0), 0); }

  /** Indian FY label for the open panel, e.g. "FY 2026–27". */
  fyLabel = `FY ${this.curFyStart}–${String(this.curFyStart + 1).slice(2)}`;
  private readonly FY_QUARTERS = [
    { label: 'Q1', sub: 'Apr–Jun', months: [4, 5, 6] },
    { label: 'Q2', sub: 'Jul–Sep', months: [7, 8, 9] },
    { label: 'Q3', sub: 'Oct–Dec', months: [10, 11, 12] },
    { label: 'Q4', sub: 'Jan–Mar', months: [1, 2, 3] },
  ];
  /** Per-quarter expected (what the company will pay) vs collected (what you got). */
  divQuarters(sym: string) {
    const fy = this.divThisFy(sym);
    return this.FY_QUARTERS.map(q => {
      const rows = fy.filter(d => q.months.includes(+(d.date.split('-')[1] || 0)));
      const expected = rows.reduce((a, b) => a + b.amount, 0);
      const collected = rows.filter(d => d.received).reduce((a, b) => a + b.amount, 0);
      return { ...q, expected, collected, pending: expected - collected, count: rows.length };
    });
  }

  private readonly _QN = ['Q1', 'Q2', 'Q3', 'Q4'];
  private readonly _QSUB = ['Jan–Mar', 'Apr–Jun', 'Jul–Sep', 'Oct–Dec'];
  /** Dividends over the TRAILING 4 calendar quarters (bar-graph data): gross per
   *  quarter, split received (green) vs pending (amber), scaled to the busiest bar. */
  divLast4Q(sym: string) {
    const evs = this.dividendsFor(sym);
    const today = new Date();
    const curQ = Math.floor(today.getMonth() / 3);           // 0..3
    const curY = today.getFullYear();
    const bars: { label: string; sub: string; gross: number; received: number; pending: number; count: number; hRecv: number; hPend: number }[] = [];
    let total = 0, recvTotal = 0;
    const raw: { q: number; y: number; gross: number; received: number; count: number }[] = [];
    for (let i = 3; i >= 0; i--) {
      let q = curQ - i, y = curY;
      while (q < 0) { q += 4; y--; }
      const months = [q * 3 + 1, q * 3 + 2, q * 3 + 3];
      const rows = evs.filter(d => {
        const [yy, mm] = (d.date || '').split('-').map(Number);
        return yy === y && months.includes(mm);
      });
      const gross = rows.reduce((a, b) => a + b.amount, 0);
      const received = rows.filter(d => d.received).reduce((a, b) => a + b.amount, 0);
      total += gross; recvTotal += received;
      raw.push({ q, y, gross, received, count: rows.length });
    }
    const max = Math.max(1, ...raw.map(r => r.gross));
    for (const r of raw) {
      bars.push({
        label: `${this._QN[r.q]} ’${String(r.y).slice(2)}`, sub: this._QSUB[r.q],
        gross: r.gross, received: r.received, pending: r.gross - r.received, count: r.count,
        hRecv: r.received / max * 100, hPend: (r.gross - r.received) / max * 100,
      });
    }
    return { bars, total, received: recvTotal, pending: total - recvTotal };
  }

  /** The (year, quarter) keys for the trailing 4 calendar quarters. */
  private _last4QKeys(): Set<string> {
    const today = new Date();
    const curQ = Math.floor(today.getMonth() / 3), curY = today.getFullYear();
    const s = new Set<string>();
    for (let i = 0; i < 4; i++) { let q = curQ - i, y = curY; while (q < 0) { q += 4; y--; } s.add(`${y}-${q}`); }
    return s;
  }
  /** This stock's dividend events within the trailing 4 quarters (matches the chart). */
  private last4QEvents(sym: string): Dividend[] {
    const keys = this._last4QKeys();
    return this.dividendsFor(sym).filter(d => {
      const [yy, mm] = (d.date || '').split('-').map(Number);
      return yy ? keys.has(`${yy}-${Math.floor((mm - 1) / 3)}`) : false;
    });
  }
  /** Per-person split over the SAME trailing-4-quarter window as the chart, so
   *  "how much they got" lines up with what's shown above. */
  divByPersonL4Q(sym: string) {
    const holders = this.holdersOf(sym);
    const totalQty = holders.reduce((a, h) => a + h.qty, 0);
    const evs = this.last4QEvents(sym);
    const gross = evs.reduce((a, b) => a + b.amount, 0);
    const received = evs.filter(d => d.received).reduce((a, b) => a + b.amount, 0);
    return holders.map(h => {
      const frac = totalQty ? h.qty / totalQty : 0;
      const g = gross * frac, rate = this.divStore.tdsFor(h.person);
      const override = this.divStore.collectedFor(sym, h.person);
      const rec = override != null ? override : received * frac;
      return { person: h.person, qty: h.qty, gross: g, received: rec, pending: g - rec, rate,
               net: rec * (1 - rate), manual: override != null };
    });
  }

  // per-person view + per-person TDS ───────────────────────────────────────────
  /** People who hold this stock (respects the account scope), qty descending. */
  holdersOf(sym: string): { person: string; qty: number }[] {
    const k = DividendStore.base(sym);
    const m: Record<string, number> = {};
    for (const hd of this.scopedDetail()) {
      if (DividendStore.base(hd.symbol) === k) m[hd.person || '—'] = (m[hd.person || '—'] || 0) + hd.quantity;
    }
    return Object.entries(m).filter(([, q]) => q > 0).map(([person, qty]) => ({ person, qty })).sort((a, b) => b.qty - a.qty);
  }

  /** Every account holding this stock (respects the scope) — person, broker label
   *  and shares — powers the per-account breakdown + one-tap dividend marking. */
  accountsFor(sym: string): { id: string; person: string; label: string; broker: string; shares: number }[] {
    const k = DividendStore.base(sym);
    const bk = this.accBroker();
    const m = new Map<string, { id: string; person: string; label: string; broker: string; shares: number }>();
    for (const hd of this.scopedDetail()) {
      if (DividendStore.base(hd.symbol) !== k || !hd.account_id) continue;
      let g = m.get(hd.account_id);
      if (!g) { g = { id: hd.account_id, person: hd.person || '—', label: hd.account_label || hd.account_id, broker: bk[hd.account_id] || '', shares: 0 }; m.set(hd.account_id, g); }
      g.shares += hd.quantity;
    }
    return [...m.values()].filter(a => a.shares > 0)
      .sort((a, b) => (a.person === b.person ? b.shares - a.shares : a.person.localeCompare(b.person)));
  }

  // ── which account a newly-added dividend is credited to ('' = the whole holding)
  divAcct = signal<string>('');
  setDivAcct(id: string) { this.divAcct.set(this.divAcct() === id ? '' : id); }   // tap again = back to all
  /** the account object the add-form is currently targeting (null = all accounts) */
  divAcctSel(sym: string) { const id = this.divAcct(); return id ? this.accountsFor(sym).find(a => a.id === id) || null : null; }
  /** shares the add-form will record: the chosen account's, or the full holding's */
  divAddShares(h: StockRow): number { const a = this.divAcctSel(h.symbol); return a ? a.shares : h.quantity; }
  /** account label to show on a saved dividend row, if it's tagged to one */
  divAcctLabel(dv: Dividend): string {
    if (!dv.account_id) return '';
    const a = this.accounts().find(x => x.id === dv.account_id);
    return a ? (a.account_label || a.person || '') : '';
  }
  /** This-FY dividend split across holders by share count, with each person's
   * TDS applied to their slice (different people, different TDS). */
  divByPerson(sym: string) {
    const holders = this.holdersOf(sym);
    const totalQty = holders.reduce((a, h) => a + h.qty, 0);
    const fy = this.divThisFy(sym);
    const gross = fy.reduce((a, b) => a + b.amount, 0);
    const received = fy.filter(d => d.received).reduce((a, b) => a + b.amount, 0);
    return holders.map(h => {
      const frac = totalQty ? h.qty / totalQty : 0;
      const g = gross * frac, rate = this.divStore.tdsFor(h.person);
      // collected = your manual override if set, else the auto share-split
      const override = this.divStore.collectedFor(sym, h.person);
      const rec = override != null ? override : received * frac;
      // "net" = what actually lands in the account = collected, less their TDS
      return { person: h.person, qty: h.qty, gross: g, received: rec, pending: g - rec, rate,
               net: rec * (1 - rate), manual: override != null };
    });
  }
  /** Override exactly what a person collected for this stock (blank = auto). */
  setCollectedFor(sym: string, person: string, v: any) {
    const c = +v;
    const val = (v === '' || v == null || isNaN(c) || c < 0) ? null : c;
    this.divStore.setCollected(sym, person, val);
  }
  /** Set the actual in-account amount for a person → back-derive & store their
   * TDS (rate = 1 − net/collected). Lets you just type the bank credit. */
  setNetFor(person: string, collected: number, v: any) {
    const net = +v;
    if (collected <= 0 || v === '' || v == null || isNaN(net) || net < 0) return;
    this.divStore.setTds((person || '').trim(), Math.max(0, Math.min(1, 1 - net / collected)));
  }
  /** A person's TDS as a percent for the input (null = using the default). */
  tdsPct(person: string): number | null {
    const t = this.divStore.tds()[(person || '').trim()];
    return t == null ? null : Math.round(t * 1000) / 10;
  }
  defaultTdsPct = computed(() => Math.round(this.divStore.defaultTds() * 1000) / 10);
  /** Round to a plain integer (no thousands separator) — safe for <input type=number>. */
  round0(v: number | null | undefined): number | null { return v == null || !v ? null : Math.round(v); }
  setTdsPct(person: string, v: any) {
    const n = (v === '' || v == null || isNaN(+v) || +v < 0) ? null : Math.min(100, +v) / 100;
    this.divStore.setTds((person || '').trim(), n);
  }

  /** Inline-edit a dividend record (blur/enter). Ignores blanks / non-positive. */
  editDiv(d: Dividend, field: 'date' | 'per_share' | 'shares', v: any) {
    if (field === 'date') { if (v) this.divStore.update(d.id, { date: v }); return; }
    const n = +v;
    if (v === '' || v == null || isNaN(n) || n <= 0) return;
    this.divStore.update(d.id, { [field]: n });
  }

  setDivDraft(k: 'date' | 'per_share', v: any) {
    this.divDraft.update(d => ({ ...d, [k]: k === 'per_share' ? (v === '' || v == null ? null : +v) : v }));
  }
  /** Add an expected (not-yet-received) dividend for this holding. */
  // ── currency helpers (US stocks pay dividends in USD) ───────────────────────
  isUsd(c: string | null | undefined): boolean { return (c || 'INR') === 'USD'; }
  curSym(c: string | null | undefined): string { return this.isUsd(c) ? '$' : '₹'; }
  /** per-share/price value in the stock's native currency ($ for US, ₹ otherwise). */
  px(h: StockRow, v: number): string {
    return this.isUsdRow(h) ? '$' + (v || 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : this.inr(v);
  }
  usdInr(): number { return this.summary()?.usd_inr || 85; }
  /** convert a ₹ amount to USD (for the US-stock hover). */
  toUsd(inr: number): number { return inr / this.usdInr(); }
  /** a dividend's native gross in its own currency (per_share × shares). */
  divNative(d: Dividend): number { return (d.per_share || 0) * (d.shares || 0); }
  /** A US stock — by currency (new backend) or exchange (robust fallback). */
  isUsdRow(h: StockRow): boolean {
    if (this.isUsd(h.currency)) return true;
    const e = (h.exchange || '').toUpperCase();
    return e === 'US' || e === 'USD' || e === 'NASDAQ' || e === 'NYSE' || e === 'ARCA' || e === 'BATS' || e === 'AMEX' || e === 'NYSEARCA';
  }
  /** the ₹ value shown in USD, for US stocks only ('' otherwise). */
  usdTip(h: StockRow, inr: number | null): string {
    if (!this.isUsdRow(h) || inr == null) return '';
    const usd = this.toUsd(inr);
    return (usd < 0 ? '−$' : '$') + Math.abs(usd).toLocaleString('en-US', { maximumFractionDigits: 2 });
  }
  /** Floating USD tooltip (fixed-position → never clipped by the table). */
  hoverTip = signal<{ x: number; y: number; text: string } | null>(null);
  showTip(ev: MouseEvent, text: string) {
    if (!text) { this.hoverTip.set(null); return; }
    const r = (ev.currentTarget as HTMLElement).getBoundingClientRect();
    this.hoverTip.set({ x: r.right, y: r.top, text });
  }
  hideTip() { this.hoverTip.set(null); }

  addExpected(h: StockRow) {
    const d = this.divDraft();
    const cur: 'INR' | 'USD' = this.isUsd(h.currency) ? 'USD' : 'INR';
    const acct = this.divAcctSel(h.symbol);          // null = the whole holding
    const shares = acct ? acct.shares : h.quantity;
    if (!d.date || !(d.per_share && d.per_share > 0) || !(shares > 0)) {
      this.divErr.set(`Enter a record date and ${this.curSym(cur)}/share.`); return;
    }
    this.divErr.set(null);
    this.divStore.add({ date: d.date, symbol: h.symbol, name: h.name || undefined,
                        per_share: d.per_share, shares, received: false, currency: cur,
                        ...(acct ? { account_id: acct.id, person: acct.person } : {}) }).subscribe({
      next: () => this.divDraft.set({ date: this._todayStr(), per_share: null }),
      error: (e: HttpErrorResponse) => this.divErr.set(e.status === 503
        ? 'One-time setup needed — create stock_dividends (see SUPABASE.md), then retry.'
        : 'Could not save — ' + this.msg(e)),
    });
  }
  toggleReceived(d: Dividend) { this.divStore.setReceived(d.id, !d.received); }
  removeDiv(d: Dividend) {
    if (!confirm(`Delete ${d.symbol} dividend (${this.inr(d.amount)})?`)) return;
    this.divStore.remove(d.id);
  }

  // previous-years: auto sum of older received dividends, with a manual override
  autoPrev(sym: string): number {
    const k = DividendStore.base(sym);
    return this.divStore.divs().filter(d => d.received && DividendStore.base(d.symbol) === k && DividendStore.fyStart(d.date) < this.curFyStart)
      .reduce((a, b) => a + b.amount, 0);
  }
  manualPrev(sym: string): number | null {
    const v = this.divStore.meta()[DividendStore.base(sym)];
    return v == null ? null : v;
  }
  effectivePrev(sym: string): number { const m = this.manualPrev(sym); return m != null ? m : this.autoPrev(sym); }
  setManualPrev(sym: string, v: number | null) {
    const n = (v == null || isNaN(v as number) || (v as number) < 0) ? null : Number(v);
    this.divStore.setMeta(DividendStore.base(sym), n);
  }

  /** FY summary for the expanded panel (gross / received / pending / TDS / yield). */
  divCalc(sym: string, value: number) {
    const fy = this.divThisFy(sym);
    const gross = fy.reduce((a, b) => a + b.amount, 0);
    const received = fy.filter(d => d.received).reduce((a, b) => a + b.amount, 0);
    const pending = gross - received;
    const tds = gross * this.divTaxRate;
    return {
      gross, received, pending, tds, net: gross - tds,
      count: fy.length, prevYears: this.effectivePrev(sym),
      yieldPct: value > 0 ? gross / value : null,
    };
  }

  /** Expected annual dividend across the currently filtered holdings. */
  divPipeline = computed(() => {
    let gross = 0, count = 0;
    for (const r of this.filteredStocks()) {
      const g = this.annualDividend(r.symbol);
      if (g > 0) { gross += g; count++; }
    }
    return { gross, net: gross * (1 - this.divTaxRate), count };
  });

  /** ₹ with up to 2 decimals, no Cr/L compaction — dividends are small numbers
   * where ₹0.58/share precision matters (EquityService.inr rounds to whole ₹). */
  divFmt(v: number | null | undefined): string {
    if (v == null || isNaN(v as number)) return '—';
    const a = Math.abs(v), sign = v < 0 ? '-' : '';
    const dp = a > 0 && a < 100 ? 2 : 0;
    return `${sign}₹${a.toLocaleString('en-IN', { minimumFractionDigits: dp, maximumFractionDigits: dp })}`;
  }

  // monogram "logo" for a stock — stable color from the symbol
  private LOGO_PALETTE = ['#387ed1', '#16a085', '#f39c12', '#8e44ad', '#e74c3c', '#0e8a6d', '#5c6bc0', '#e67e22', '#00897b', '#2980b9', '#27ae60', '#d4262d'];
  logoColor(sym: string): string {
    let h = 0; for (const c of sym || '') h = (h * 31 + c.charCodeAt(0)) >>> 0;
    return this.LOGO_PALETTE[h % this.LOGO_PALETTE.length];
  }
  initials(sym: string): string { return (sym || '?').replace(/[^A-Za-z0-9]/g, '').slice(0, 2).toUpperCase(); }
  logoUrl(isin: string | null | undefined): string | null {
    return isin ? `https://assets-netstorage.groww.in/stock-assets/logos/${isin}.png` : null;
  }
  onLogoErr(e: Event) { (e.target as HTMLImageElement).style.display = 'none'; }   // fall back to monogram
  clearHoldFilters() { this.holdSearch.set(''); this.holdStatus.set(new Set()); this.holdPerson.set(''); this.holdBroker.set(''); this.holdExchange.set(''); this.divOnly.set(false); this.needsPrice.set(false); this.clearDivMonth(); }
  holdFiltered = computed(() => !!(this.holdSearch() || this.holdStatus().size > 0 || this.holdBroker() || this.divOnly() || this.needsPrice() || this.divMonthActive()));

  // ── "Needs price" filter + inline manual LTP entry ──────────────────────────
  needsPrice = signal(false);
  toggleNeedsPrice() { this.needsPrice.update(v => !v); }

  /** Manual overrides, base-symbol keyed. This is the single source the inline
   *  inputs bind to, so a typed price shows instantly, stays editable, and never
   *  clears on a network round-trip. Loaded from the API, updated optimistically. */
  localManual = signal<Record<string, number>>({});
  loadManual() { this.api.manualPrices().subscribe({ next: m => this.localManual.set(m || {}), error: () => {} }); }
  private manualKey(sym: string) { return DividendStore.base(sym); }
  manualFor(sym: string): number | null {
    const v = this.localManual()[this.manualKey(sym)];
    return v == null ? null : v;
  }
  /** A row belongs in "Needs price" if it has no live price OR you've given it a
   *  manual one — so manual prices stay reachable and re-editable forever. */
  needsRow = (r: StockRow) => !r.priced || !!r.price_manual || this.manualFor(r.symbol) != null;
  unpricedCount = computed(() => this.mergedRows().filter(this.needsRow).length);
  hasNeedsPrice = computed(() => this.mergedRows().some(this.needsRow));

  setManualLocal(sym: string, v: number | null) {
    const k = this.manualKey(sym);
    const n = (v == null || isNaN(v as any) || (v as number) <= 0) ? null : Number(v);
    this.localManual.update(m => { const c = { ...m }; if (n == null) delete c[k]; else c[k] = n; return c; });
  }
  private _pxT: any = null;
  /** Persist an override in the background and softly re-value (debounced). Never
   *  blocks the input, so editing many prices stays instant and smooth. */
  saveManual(sym: string) {
    const price = this.manualFor(sym);
    this.api.setManualPrice(sym, price).subscribe({
      next: () => {
        if (this._pxT) clearTimeout(this._pxT);
        this._pxT = setTimeout(() => this.api.summary().subscribe({ next: s => this._applySummary(s), error: () => {} }), 300);
      },
      error: () => this.error.set('Could not save the price. Is the backend running?'),
    });
  }

  private accBroker = computed<Record<string, string>>(() => {
    const m: Record<string, string> = {}; for (const a of this.accounts()) m[a.id] = a.broker; return m;
  });
  brokerOptions = computed(() => Array.from(new Set(this.selectedAccounts().map(a => a.broker).filter(Boolean))) as string[]);
  exchangeOptions = computed(() => Array.from(new Set(this.scopedDetail().map(h => h.exchange).filter(Boolean))) as string[]);

  /** holdings_detail scoped to the selected accounts (empty selection = all) */
  private scopedDetail = computed<HoldingDetail[]>(() => {
    const s = this.summary(); if (!s) return [];
    const sel = this.accountSel();
    const rows = s.holdings_detail || [];
    return sel.size ? rows.filter(h => sel.has(h.account_id)) : rows;
  });

  private mergeDetail(rows: HoldingDetail[]): StockRow[] {
    const m = new Map<string, any>();
    for (const h of rows) {
      // merge by BASE symbol so a series-suffixed row (VAML-BE) folds into its
      // plain twin (VAML) — they're the same stock, just a different NSE series.
      const base = DividendStore.base(h.symbol || '');
      const key = base || h.name || '—';
      let g = m.get(key);
      if (!g) { g = { symbol: base || h.name || '—', name: h.name, isin: h.isin, exchange: h.exchange, last_price: h.last_price, day_change_pct: h.day_change_pct, quantity: 0, invested: 0, value: 0, day_change: 0, accIds: new Set<string>(), priced: h.priced, price_manual: !!h.price_manual, currency: h.currency || 'INR', screener_url: h.screener_url ?? null }; m.set(key, g); }
      if (!g.screener_url && h.screener_url) g.screener_url = h.screener_url;
      if (!g.name && h.name) g.name = h.name;
      if (!g.isin && h.isin) g.isin = h.isin;
      if (h.price_manual) g.price_manual = true;
      if (h.priced && !g.priced) { g.priced = true; g.last_price = h.last_price; g.day_change_pct = h.day_change_pct; }  // prefer a live quote
      g.quantity += h.quantity; g.invested += h.invested; g.value += h.value; g.day_change += h.day_change;
      if (h.account_id) g.accIds.add(h.account_id);
      g.avgNat = (g.avgNat || 0) + (h.quantity || 0) * (h.avg_price || 0);   // native-currency cost (USD for US stocks)
    }
    // avg_price is native (₹ for INR, $ for US) — display only; P&L uses `invested` (₹)
    return Array.from(m.values()).map(g => ({ ...g, accounts: g.accIds.size, avg_price: g.quantity ? g.avgNat / g.quantity : 0,
      pnl: g.value - g.invested, pnl_pct: g.invested ? g.value / g.invested - 1 : null }));
  }

  /** scoped + merged by stock (powers movers) */
  scopedMerged = computed<StockRow[]>(() => this.mergeDetail(this.scopedDetail()));

  /** holdings table base: scoped + toolbar person/broker/exchange, merged by stock */
  private mergedRows = computed<StockRow[]>(() => {
    let rows = this.scopedDetail();
    const per = this.holdPerson(); if (per) rows = rows.filter(h => h.person === per);
    const bk = this.holdBroker(); if (bk) { const m = this.accBroker(); rows = rows.filter(h => m[h.account_id] === bk); }
    const ex = this.holdExchange(); if (ex) rows = rows.filter(h => h.exchange === ex);
    return this.mergeDetail(rows);
  });

  /** + search + status + sort */
  filteredStocks = computed<StockRow[]>(() => {
    let rows = this.mergedRows();
    const q = this.holdSearch().trim().toUpperCase();
    if (q) rows = rows.filter(r => (r.symbol || '').toUpperCase().includes(q) || ((r as any).name || '').toUpperCase().includes(q));
    const st = this.holdStatus();
    if (st.size) rows = rows.filter(r =>
      (st.has('profit') && r.pnl > 0) || (st.has('loss') && r.pnl < 0) ||
      (st.has('up') && r.day_change_pct > 0) || (st.has('down') && r.day_change_pct < 0));
    if (this.divOnly()) rows = rows.filter(r => this.hasDiv(r.symbol));
    if (this.needsPrice()) rows = rows.filter(this.needsRow);
    const payers = this.divPayersInMonth();
    if (payers) rows = rows.filter(r => payers.has(DividendStore.base(r.symbol)));
    const k = this.sortKey(), dir = this.sortDir() === 'desc' ? -1 : 1;
    return [...rows].sort((a, b) => {
      if (k === 'symbol') return a.symbol.localeCompare(b.symbol) * dir;
      if (k === 'div') return (this.annualDividend(a.symbol) - this.annualDividend(b.symbol)) * dir;
      if (k === 'div_ps') return ((this.lastDivPerShare(a.symbol) ?? -Infinity) - (this.lastDivPerShare(b.symbol) ?? -Infinity)) * dir;
      if (k === 'record') return this.recordDateRaw(a.symbol).localeCompare(this.recordDateRaw(b.symbol)) * dir;
      return (((a as any)[k] ?? 0) - ((b as any)[k] ?? 0)) * dir;
    });
  });

  /** Aggregate stats over the currently-filtered stocks — powers the summary
   *  hero that appears while a filter is active, so the user sees the whole
   *  filtered slice at a glance (value, P&L, return, dividend yield, count). */
  filterStats = computed(() => {
    const rows = this.filteredStocks();
    let invested = 0, value = 0, pnl = 0, day = 0, div = 0, priced = 0;
    for (const r of rows) {
      invested += r.invested || 0;
      value += r.value || 0;
      pnl += r.pnl || 0;
      day += r.day_change || 0;
      div += this.annualDividend(r.symbol);
      if (r.priced) priced++;
    }
    return {
      count: rows.length,
      invested, value, pnl, day,
      pnlPct: invested > 0 ? pnl / invested : null,       // overall return
      dayPct: value - day > 0 ? day / (value - day) : null,
      div,                                                 // gross dividend income / yr
      netDiv: div * (1 - this.divStore.defaultTds()),      // in-bank after TDS
      divYield: value > 0 ? div / value : null,            // yield on current value
      unpriced: rows.length - priced,
    };
  });

  // today's top movers — scoped to selection, priced, 2 + 2
  topGainers = computed<StockRow[]>(() =>
    this.scopedMerged().filter(s => s.priced && s.day_change_pct > 0)
      .sort((a, b) => b.day_change_pct - a.day_change_pct).slice(0, 2));
  topLosers = computed<StockRow[]>(() =>
    this.scopedMerged().filter(s => s.priced && s.day_change_pct < 0)
      .sort((a, b) => a.day_change_pct - b.day_change_pct).slice(0, 2));

  // holdings truncation
  // ── tiered rendering — never mount all ~800 rows at once (that froze the tab).
  //  Grow the visible window in steps: 5 (preview) → 30 → 100 → everything.
  private readonly HOLD_TIERS = [30, 100, Infinity];
  holdLimit = signal(5);
  displayStocks = computed<StockRow[]>(() => {
    const all = this.filteredStocks();
    const lim = this.holdLimit();
    return lim >= all.length ? all : all.slice(0, lim);
  });
  showAllHoldings = computed(() => this.holdLimit() > 5);
  /** how many the next "show more" tap will reveal (capped at the real total) */
  nextHoldTier = computed(() => {
    const cur = this.holdLimit(); const total = this.filteredStocks().length;
    const t = this.HOLD_TIERS.find(x => x > cur) ?? Infinity;
    return Math.min(t, total);
  });

  /** Reveal the next tier of holdings (30 → 100 → all). */
  showMoreHoldings() { this.holdLimit.set(this.nextHoldTier()); }

  /** Collapse back to the 5-row preview and glide to the top of the list. */
  collapseHoldings(anchor?: HTMLElement) {
    this.holdLimit.set(5);
    if (anchor) { anchor.scrollIntoView({ behavior: 'smooth', block: 'start' }); }
    else if (typeof document !== 'undefined') {
      document.getElementById('holdings-top')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  }

  /** per-person rollup for the selected scope (powers the cards) */
  scopedPersons = computed(() => {
    const map: Record<string, any> = {};
    for (const h of this.scopedDetail()) {
      const p = h.person || '—';
      const g = map[p] || (map[p] = { person: p, value: 0, invested: 0, day: 0 });
      g.value += h.value; g.invested += h.invested; g.day += h.day_change;
    }
    return Object.values(map).map((g: any) => ({
      person: g.person, value: g.value, invested: g.invested, pnl: g.value - g.invested,
      pnl_pct: g.invested ? g.value / g.invested - 1 : null, day_change: g.day,
      day_change_pct: (g.value - g.day) ? g.value / (g.value - g.day) - 1 : 0,
    })).sort((a, b) => b.value - a.value);
  });

  // per-person detail modal
  personDetail = signal<string | null>(null);
  openPersonDetail(name: string) {
    this.personDetail.set(name);
    this.recResult.set(null); this.recError.set(null); this.recFileName.set(''); this.recDone.set(null);
    this.harvestUpAcc.set(null); this.harvestPreview.set(null); this.harvestErr.set(null);
    this.loadRecUploads();
    this.loadHarvest();
  }

  // ── Reconcile this person's dividends from their bank statement (in-dialog) ────
  recUploads = signal<DivStatementUpload[]>([]);
  recResult = signal<DivReconcileResult | null>(null);
  recBusy = signal(false);
  recError = signal<string | null>(null);
  recFileName = signal('');
  recAccepted = signal<Set<string>>(new Set());
  recDone = signal<number | null>(null);
  /** which slice of the parsed statement is shown (filter tags). */
  recFilter = signal<'action' | 'matched' | 'review' | 'already' | 'unmatched' | 'ignored' | 'all'>('action');
  setRecFilter(f: any) { this.recFilter.set(f); }

  /** Three focused filter tabs — the hero above carries the full breakdown. */
  recTabs = computed(() => {
    const r = this.recResult(); if (!r) return [];
    const c = r.counts;
    return [
      { key: 'action', label: 'To confirm', count: c.matched + c.review, tone: 'hi' },
      { key: 'review', label: 'Not sure', count: c.review, tone: 'rv' },
      { key: 'all', label: 'Everything', count: c.all_credits ?? c.credits, tone: 'mut' },
    ];
  });

  /** Hero roll-up — every bucket's count AND ₹, so the whole statement is summed
   *  up at a glance even though only 3 tabs remain. */
  recSummary = computed(() => {
    const r = this.recResult(); if (!r) return null;
    const sum = (arr: any[], f: (x: any) => number) => arr.reduce((a, b) => a + (f(b) || 0), 0);
    const cAmt = (m: any) => m.credit_amount, uAmt = (m: any) => m.amount;
    const matched = { n: r.matched.length, amt: sum(r.matched, cAmt) };
    const review = { n: r.review.length, amt: sum(r.review, cAmt) };
    const already = { n: r.already.length, amt: sum(r.already, cAmt) };
    const unmatched = { n: r.unmatched.length, amt: sum(r.unmatched, uAmt) };
    const ignored = { n: (r.ignored || []).length, amt: sum(r.ignored || [], uAmt) };
    const divCount = matched.n + review.n + already.n + unmatched.n;
    const divAmt = matched.amt + review.amt + already.amt + unmatched.amt;
    return {
      matched, review, already, unmatched, ignored,
      divCount, divAmt, allN: divCount + ignored.n, allAmt: divAmt + ignored.amt,
      toConfirmN: matched.n + review.n, toConfirmAmt: matched.amt + review.amt,
    };
  });

  /** Every parsed row for the active filter, one normalised shape for the list. */
  recRows = computed(() => {
    const r = this.recResult(); if (!r) return [];
    const f = this.recFilter();
    const fromMatch = (m: any, kind: string, actionable: boolean) => ({
      kind, date: m.credit_date, amount: m.credit_amount, narration: m.narration,
      name: m.name, symbol: m.symbol, loggedAmount: m.amount, amountDiff: m.amount_diff,
      div_id: m.div_id, actionable,
    });
    const fromCredit = (c: any, kind: string) => ({
      kind, date: c.date, amount: c.amount, narration: c.narration,
      name: null, symbol: null, loggedAmount: null, amountDiff: null, div_id: null, actionable: false,
    });
    const M = r.matched.map(m => fromMatch(m, 'matched', true));
    const R = r.review.map(m => fromMatch(m, 'review', true));
    const A = r.already.map(m => fromMatch(m, 'already', false));
    const U = r.unmatched.map(c => fromCredit(c, 'unmatched'));
    const I = (r.ignored || []).map(c => fromCredit(c, 'ignored'));
    const pick = f === 'action' ? [...M, ...R] : f === 'matched' ? M : f === 'review' ? R
      : f === 'already' ? A : f === 'unmatched' ? U : f === 'ignored' ? I : [...M, ...R, ...A, ...U, ...I];
    return pick.sort((a, b) => (a.date || '').localeCompare(b.date || ''));
  });
  recKindLabel(k: string): string {
    return k === 'matched' ? 'Confirmed' : k === 'review' ? 'Not sure' : k === 'already' ? 'Already'
      : k === 'unmatched' ? 'No match' : 'Not a dividend';
  }

  /** Upload history for whoever's dialog is open (their statements only). */
  personUploads = computed<DivStatementUpload[]>(() => {
    const p = this.personDetail();
    return this.recUploads().filter(u => !u.person || u.person === p);
  });
  loadRecUploads() { this.api.dividendReconcileUploads().subscribe({ next: u => this.recUploads.set(u || []), error: () => {} }); }

  onRecFile(ev: Event) {
    const input = ev.target as HTMLInputElement;
    const f = input.files?.[0]; input.value = '';
    if (!f) return;
    const person = this.personDetail() || '';
    this.recFileName.set(f.name);
    this.recBusy.set(true); this.recError.set(null); this.recResult.set(null); this.recDone.set(null);
    this.recFilter.set('action');
    this.api.dividendReconcilePreview(f, person).subscribe({
      next: r => {
        this.recBusy.set(false);
        this.recResult.set(r);
        this.recAccepted.set(new Set(r.matched.map(m => m.div_id)));   // pre-tick confident ones
      },
      error: (e: HttpErrorResponse) => {
        this.recBusy.set(false);
        this.recError.set(e.error?.detail || 'Could not read the statement. Export it as Excel/CSV and retry.');
      },
    });
  }
  isRecAccepted(m: any): boolean { return this.recAccepted().has(m.div_id); }
  toggleRec(m: any) { this.recAccepted.update(s => { const n = new Set(s); n.has(m.div_id) ? n.delete(m.div_id) : n.add(m.div_id); return n; }); }
  recAcceptedCount = computed(() => this.recAccepted().size);

  confirmRec() {
    const res = this.recResult(); if (!res) return;
    const accepted = [...res.matched, ...res.review].filter(m => this.isRecAccepted(m));
    const ids = accepted.map(m => m.div_id);
    if (!ids.length) { this.recResult.set(null); return; }
    const allDates = [...res.matched, ...res.review, ...res.already].map(m => m.credit_date)
      .concat(res.unmatched.map(u => u.date)).filter(Boolean).sort();
    const meta = {
      filename: this.recFileName() || undefined,
      date_from: allDates[0] || null, date_to: allDates[allDates.length - 1] || null,
      person: this.personDetail() || undefined,
      credits: res.counts.credits,
      amount: Math.round(accepted.reduce((s, m) => s + (m.credit_amount || 0), 0) * 100) / 100,
    };
    this.recBusy.set(true); this.recError.set(null);
    this.api.dividendReconcileConfirm(ids, meta).subscribe({
      next: r => {
        this.recBusy.set(false);
        this.recDone.set(r.marked);
        this.loadRecUploads();
        setTimeout(() => { this.recResult.set(null); this.recDone.set(null); this.recFileName.set(''); }, 1600);
      },
      error: (e: HttpErrorResponse) => { this.recBusy.set(false); this.recError.set('Could not save — ' + (e.error?.detail || 'try again.')); },
    });
  }
  fmtRecDay(d: string | null): string {
    if (!d) return '—';
    const dt = new Date((d || '').slice(0, 10));
    return isNaN(dt.getTime()) ? (d || '') : dt.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' });
  }
  fmtRecWhen(d: string | null): string {
    if (!d) return '—';
    const dt = new Date(d);
    return isNaN(dt.getTime()) ? d : dt.toLocaleDateString('en-IN', { day: '2-digit', month: 'short' }) + ', ' + dt.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' });
  }
  personTotals = computed(() => this.scopedPersons().find(p => p.person === this.personDetail()) || null);
  personAccounts = computed<EquityAccount[]>(() => {
    const n = this.personDetail(); if (!n) return [];
    const sel = this.accountSel();
    return this.accounts().filter(a => a.person === n && (!sel.size || sel.has(a.id)));
  });

  // ── LTCG harvesting: booked long-term gain vs the ₹1.25 L tax-free allowance ──
  // The FY is NEVER typed — it is read from the file you upload (its own period),
  // so a wrong year can't be entered. Records persist in Supabase (app_cache), so
  // every filed value is remembered and shows on the person cards.
  readonly LTCG_ALLOWANCE = 125000;
  harvestRecords = signal<HarvestRecord[]>([]);       // every filed account+FY
  /** which account's uploader is open inside the modal (null = none) */
  harvestUpAcc = signal<string | null>(null);
  harvestBusy = signal(false);
  harvestErr = signal<string | null>(null);
  harvestPreview = signal<HarvestPreview | null>(null);
  harvestFileName = signal('');

  /** the current Indian FY (Apr–Mar), the fallback when nothing is filed */
  private curFY(): string {
    const d = new Date();
    const y = d.getMonth() >= 3 ? d.getFullYear() : d.getFullYear() - 1;   // Apr = month 3
    return `${y}-${String(y + 1).slice(-2)}`;
  }

  /** The FY shown for ONE PERSON = the latest FY THEY have filed (across their
   *  accounts), else the current FY. Per-person, because different people may have
   *  filed different years — a global "latest" would blank out everyone else. */
  personFY(person: string): string {
    const accIds = new Set(this.accounts().filter(a => a.person === person).map(a => a.id));
    const filed = this.harvestRecords()
      .filter(r => accIds.has(r.account_id) && r.fy_label)
      .map(r => r.fy_label);
    return filed.length ? filed.sort((a, b) => b.localeCompare(a))[0] : this.curFY();
  }

  loadHarvest() {
    this.api.harvestAll().subscribe({
      next: r => this.harvestRecords.set(r.records || []),
      error: () => {},
    });
  }

  /** For a person KPI card: their LTCG booked + room left, and STCG booked + tax,
   *  for their latest filed FY, rolled up across accounts. `filed` false → nothing
   *  uploaded yet. */
  harvestForPerson(person: string): {
    booked: number; remaining: number; filed: boolean; fy: string;
    stBooked: number; stTax: number; totalTax: number;
  } {
    const fy = this.personFY(person);
    const accIds = new Set(this.accounts().filter(a => a.person === person).map(a => a.id));
    let booked = 0, stBooked = 0, stTax = 0, totalTax = 0, filed = false;
    for (const r of this.harvestRecords()) {
      if (r.fy_label === fy && accIds.has(r.account_id)) {
        booked += Math.max(0, r.lt_booked || 0);
        stBooked += r.st_booked || 0; stTax += r.st_tax || 0; totalTax += r.total_tax || 0;
        filed = true;
      }
    }
    return { booked, remaining: Math.max(0, this.LTCG_ALLOWANCE - booked), filed, fy,
             stBooked, stTax, totalTax };
  }

  /** the filed record for one account — the latest FY filed FOR THAT ACCOUNT */
  harvestFor(accId: string): HarvestRecord | null {
    const recs = this.harvestYears(accId);
    return recs.length ? recs[0] : null;
  }

  /** EVERY filed year for one account, newest FY first — so several years can be
   *  listed and managed per account. */
  harvestYears(accId: string): HarvestRecord[] {
    return this.harvestRecords()
      .filter(r => r.account_id === accId && r.fy_label)
      .sort((a, b) => (b.fy_label || '').localeCompare(a.fy_label || ''));
  }
  /** is this FY already on file for this account? (→ upload will replace it) */
  yearAlreadyFiled(accId: string, fyLabel: string): boolean {
    return this.harvestRecords().some(r => r.account_id === accId && r.fy_label === fyLabel);
  }

  /** the person-level roll-up shown in the modal hero */
  personHarvest = computed(() => {
    const n = this.personDetail(); if (!n) return null;
    const fy = this.personFY(n);
    const accs = this.personAccounts();
    let booked = 0, filed = 0, stBooked = 0, stTax = 0, ltTax = 0, totalTax = 0;
    for (const a of accs) {
      const rec = this.harvestRecords().find(r => r.account_id === a.id && r.fy_label === fy);
      if (rec) {
        booked += Math.max(0, rec.lt_booked || 0); filed++;
        stBooked += rec.st_booked || 0;
        stTax += rec.st_tax || 0; ltTax += rec.lt_tax || 0; totalTax += rec.total_tax || 0;
      }
    }
    // the ₹1.25 L allowance is PER PERSON per FY — headroom is one allowance minus
    // what they've booked across all their accounts
    const remaining = Math.max(0, this.LTCG_ALLOWANCE - booked);
    return { fy, booked, remaining, filed, accounts: accs.length,
             pct: Math.min(1, booked / this.LTCG_ALLOWANCE),
             stBooked, stTax, ltTax, totalTax };
  });

  openHarvestUpload(accId: string) {
    this.harvestUpAcc.set(this.harvestUpAcc() === accId ? null : accId);
    this.harvestPreview.set(null); this.harvestErr.set(null); this.harvestFileName.set('');
  }

  /** Upload → the server reads the file's own FY period; we file it under THAT FY.
   *  The user never picks a year, so it can't be wrong. */
  onHarvestFile(accId: string, ev: Event) {
    const input = ev.target as HTMLInputElement;
    const file = input.files?.[0]; if (!file) return;
    input.value = '';
    this.harvestBusy.set(true); this.harvestErr.set(null); this.harvestPreview.set(null);
    this.harvestFileName.set(file.name);
    this.api.harvestPreview(accId, file).subscribe({
      next: p => { this.harvestPreview.set(p); this.harvestBusy.set(false); },
      error: (e) => {
        this.harvestBusy.set(false);
        this.harvestErr.set(e?.error?.detail || 'Could not read that Tax P&L file.');
      },
    });
  }

  confirmHarvest(accId: string) {
    const p = this.harvestPreview(); if (!p) return;
    const fy = p.harvest.fy_label;                    // the FY read FROM the file
    this.harvestBusy.set(true);
    this.api.harvestSave(accId, fy, p.parsed, p.file_name).subscribe({
      next: r => {
        this.harvestBusy.set(false);
        this.harvestUpAcc.set(null); this.harvestPreview.set(null); this.harvestFileName.set('');
        // update in place immediately (upsert), so the account row + hero + person
        // card reflect it the instant you confirm — then reconcile from the server
        this.harvestRecords.update(list => {
          const rest = list.filter(x => x.id !== r.record.id);
          return [...rest, r.record];
        });
        this.loadHarvest();
      },
      error: (e) => { this.harvestBusy.set(false); this.harvestErr.set(e?.error?.detail || 'Could not save.'); },
    });
  }

  removeHarvest(accId: string, fyLabel: string) {
    if (!confirm(`Remove the filed Tax P&L for FY ${fyLabel}?`)) return;
    // drop it locally at once so the UI updates, then confirm with the server
    this.harvestRecords.update(list => list.filter(x => !(x.account_id === accId && x.fy_label === fyLabel)));
    this.api.harvestDelete(accId, fyLabel).subscribe({ next: () => this.loadHarvest(), error: () => this.loadHarvest() });
  }

  /** Hero totals for the selected scope (all accounts when nothing is selected). */
  viewTotals = computed(() => {
    if (!this.summary()) return null;
    let value = 0, invested = 0, day = 0; const syms = new Set<string>();
    for (const h of this.scopedDetail()) {
      value += h.value; invested += h.invested; day += h.day_change; syms.add(h.symbol || h.name || '—');
    }
    const prev = value - day;
    return { value, invested, pnl: value - invested, pnl_pct: invested ? value / invested - 1 : null,
             day_change: day, day_change_pct: prev ? value / prev - 1 : 0, holdings: syms.size };
  });

  // ── add account ─────────────────────────────────────────────────────────────
  openAdd() {
    this.pickerOpen.set(false);
    this.draft.set({ broker: 'kite', person: '', account_label: '', api_key: '', api_secret: '', import_broker: 'icici' });
    this.showAdd.set(true);
  }
  setField(k: string, v: string) { this.draft.update(d => ({ ...d, [k]: v })); }
  canAdd = computed(() => {
    const d = this.draft();
    if (d.broker === 'import') return !!d.person.trim() && !!d.account_label.trim() && !!d.import_broker;
    return !!d.person.trim() && !!d.account_label.trim() && !!d.api_key.trim() && !!d.api_secret.trim();
  });
  submitAdd() {
    if (!this.canAdd()) return;
    const d = this.draft();
    this.adding.set(true);
    if (d.broker === 'import') {
      this.api.createImported({ person: d.person, account_label: d.account_label, broker: d.import_broker }).subscribe({
        next: a => { this.adding.set(false); this.showAdd.set(false); this.load(); this.openUpload(a); },
        error: (e: HttpErrorResponse) => { this.adding.set(false); this.error.set('Could not create account — ' + this.msg(e)); },
      });
      return;
    }
    this.api.addAccount({ person: d.person, account_label: d.account_label, api_key: d.api_key, api_secret: d.api_secret }).subscribe({
      next: r => {
        this.adding.set(false); this.showAdd.set(false);
        this.startKiteLogin(r.id, r.account_label, r.login_url);
      },
      error: (e: HttpErrorResponse) => { this.adding.set(false); this.error.set('Could not add account — ' + this.msg(e)); },
    });
  }

  // ── connect ─────────────────────────────────────────────────────────────────
  /** Show the login dialog with the login link, and start polling so the account
   *  connects itself once you finish signing in (here or in any browser). */
  private startKiteLogin(id: string, label: string, login_url: string) {
    this.requestToken.set('');
    this.connectModal.set({ id, login_url, label });
    this.pollForConnection(id);
  }
  // Poll so the account connects itself here once you finish signing in ANYWHERE
  // (the login tab, or an Incognito window — which can't message this tab back).
  private _connPoll: any = null;
  private stopConnPoll() { if (this._connPoll) { clearInterval(this._connPoll); this._connPoll = null; } }
  private pollForConnection(id: string) {
    this.stopConnPoll();
    let tries = 0;
    this._connPoll = setInterval(() => {
      if (++tries > 100) { this.stopConnPoll(); return; }   // ~5 min
      this.api.accounts().subscribe({
        next: accs => {
          const a = (accs || []).find(x => x.id === id);
          if (a && a.status === 'connected') {
            this.stopConnPoll();
            this.loggingInId.set(null); this.connectModal.set(null);
            this.error.set(null); this.load();
          }
        },
        error: () => {},
      });
    }, 3000);
  }
  /** Open the login link in a new tab (you can paste it into Incognito instead). */
  openLogin() { const m = this.connectModal(); if (m?.login_url) window.open(m.login_url, '_blank'); }
  linkCopied = signal(false);
  copyLoginLink() {
    const m = this.connectModal(); if (!m?.login_url) return;
    navigator.clipboard?.writeText(m.login_url)
      .then(() => { this.linkCopied.set(true); setTimeout(() => this.linkCopied.set(false), 2500); })
      .catch(() => {});
  }
  doConnect() {
    const m = this.connectModal(); const tok = this.requestToken().trim();
    if (!m || !tok) return;
    this.connecting.set(true);
    this.api.connect(m.id, tok).subscribe({
      next: () => { this.connecting.set(false); this.connectModal.set(null); this.requestToken.set(''); this.load(); },
      error: (e: HttpErrorResponse) => { this.connecting.set(false); this.error.set('Connect failed — ' + this.msg(e)); },
    });
  }
  cancelConnect() { this.connectModal.set(null); this.requestToken.set(''); this.stopConnPoll(); }

  // ── refresh / re-login ──────────────────────────────────────────────────────
  refresh(a: EquityAccount) {
    this.refreshingId.set(a.id);
    this.api.refresh(a.id).subscribe({
      next: () => { this.refreshingId.set(null); this.load(); },
      error: (e: HttpErrorResponse) => {
        this.refreshingId.set(null);
        if (e.status === 401 || e.status === 409) this.login(a);   // token expired / not connected
        else this.error.set('Refresh failed — ' + this.msg(e));
      },
    });
  }
  /** Re-login an expired/pending Kite account — opens the dialog with the link. */
  login(a: EquityAccount) {
    this.loggingInId.set(a.id);
    this.api.loginUrl(a.id).subscribe({
      next: r => { this.loggingInId.set(null); this.startKiteLogin(a.id, a.account_label, r.login_url); },
      error: (e: HttpErrorResponse) => { this.loggingInId.set(null); this.error.set('Could not start login — ' + this.msg(e)); },
    });
  }
  /** Log off — flush this account's Kite token (and any linked F&O/Stocks account
   *  for the same Zerodha user) so you can log in again and re-test. */
  logOff(a: EquityAccount) {
    if (!confirm(`Log off ${a.account_label}?\n\nThis clears its Kite session — and any Stocks or F&O account for the same Zerodha user — so you can log in again.`)) return;
    this.loggingOffId.set(a.id);
    this.api.disconnect(a.id).subscribe({
      next: (r) => {
        this.loggingOffId.set(null);
        this.editingAcct.set(null);
        // Optimistic: flip the affected stock rows to "not connected" now.
        const cleared = new Set((r?.cleared || []).filter(c => c.source === 'stock').map(c => c.id).concat(a.id));
        this.summary.update(s => s ? { ...s, accounts: (s.accounts || []).map(x => cleared.has(x.id) ? { ...x, connected: false, status: 'expired' } : x) } : s);
        this.error.set(null);
        this.load();
      },
      error: (e: HttpErrorResponse) => { this.loggingOffId.set(null); this.error.set('Could not log off — ' + this.msg(e)); },
    });
  }

  remove(a: EquityAccount) {
    if (!confirm(`Remove ${a.account_label}? Its holdings will be deleted.`)) return;
    this.api.remove(a.id).subscribe({
      next: () => { this.accountSel.update(s => { const n = new Set(s); n.delete(a.id); return n; }); this.load(); },
      error: () => this.error.set('Could not remove the account.'),
    });
  }

  // ── account picker ──────────────────────────────────────────────────────────
  selectAll() { this.accountSel.set(new Set()); this.editingAcct.set(null); }
  toggleAcc(id: string) {
    this.accountSel.update(s => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });
    this.editingAcct.set(null);
  }
  brokerShort(b: string): string {
    return ({ kite: 'Z', motilal: 'MO', icici: 'IC', ibkr: 'IB', zebu: 'ZB' } as any)[(b || '').toLowerCase()] || (b || '?').slice(0, 2).toUpperCase();
  }

  // ── inline rename / link-to-person (inside the picker) ──────────────────────
  openRowEdit(a: EquityAccount) {
    this.editDraft.set({ person: a.person || '', account_label: a.account_label || '', api_key: '', api_secret: '' });
    this.editingAcct.set(a.id);
  }
  setEdit(k: 'person' | 'account_label' | 'api_key' | 'api_secret', v: string) { this.editDraft.update(d => ({ ...d, [k]: v })); }
  saveEdit() {
    const id = this.editingAcct(); const d = this.editDraft();
    if (!id || !d.person.trim() || !d.account_label.trim()) return;
    this.savingEdit.set(true);
    const body: any = { person: d.person.trim(), account_label: d.account_label.trim() };
    // Only send key/secret when filled — blank means "keep the stored value".
    if (d.api_key.trim()) body.api_key = d.api_key.trim();
    if (d.api_secret.trim()) body.api_secret = d.api_secret.trim();
    this.api.edit(id, body).subscribe({
      next: () => { this.savingEdit.set(false); this.editingAcct.set(null); this.load(); },
      error: (e: HttpErrorResponse) => { this.savingEdit.set(false); this.error.set('Could not save — ' + this.msg(e)); },
    });
  }

  // ── file import (upload → preview → confirm replace) ────────────────────────
  openUpload(a: { id: string; account_label?: string; label?: string }) {
    this.pickerOpen.set(false);
    this.uploadFor.set({ id: a.id, label: (a.account_label || a.label || '') });
    this.selectedFile = null; this.previewRows.set(null); this.previewMeta.set(null);
  }
  onFile(ev: Event) {
    const input = ev.target as HTMLInputElement;
    const f = input.files?.[0]; input.value = '';
    if (!f) return;
    this.selectedFile = f; this.doPreview();
  }
  private doPreview() {
    const m = this.uploadFor(); if (!m || !this.selectedFile) return;
    this.previewing.set(true); this.previewRows.set(null); this.previewMeta.set(null);
    this.api.importPreview(m.id, this.selectedFile).subscribe({
      next: r => { this.previewMeta.set({ count: r.count, matched: r.matched }); this.previewRows.set(r.holdings); this.previewing.set(false); },
      error: (e: HttpErrorResponse) => { this.previewing.set(false); this.error.set('Could not read the file — ' + this.msg(e)); },
    });
  }
  confirmImport() {
    const m = this.uploadFor(); if (!m || !this.selectedFile) return;
    this.importingFile.set(true);
    this.api.importFile(m.id, this.selectedFile).subscribe({
      next: () => { this.importingFile.set(false); this.cancelUpload(); this.load(); },
      error: (e: HttpErrorResponse) => { this.importingFile.set(false); this.error.set('Import failed — ' + this.msg(e)); },
    });
  }
  cancelUpload() { this.uploadFor.set(null); this.previewRows.set(null); this.previewMeta.set(null); this.selectedFile = null; }

  // ── helpers ─────────────────────────────────────────────────────────────────
  statusLabel(a: EquityAccount): string {
    if (a.kind === 'imported') return 'Imported';
    if (a.status === 'connected') return 'Connected';
    if (a.status === 'expired') return 'Login expired';
    return 'Not connected';
  }
  syncedLabel(a: EquityAccount): string {
    if (!a.last_synced) return 'never synced';
    const d = new Date(a.last_synced);
    return isNaN(d.getTime()) ? '' : 'synced ' + d.toLocaleString('en-IN', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' });
  }
  private msg(e: HttpErrorResponse): string {
    if (e.status === 0) return 'no response from the API.';
    const d = e.error?.detail ?? e.error?.message ?? e.error;
    return typeof d === 'string' ? d : `HTTP ${e.status}`;
  }

  migrationSql = `CREATE TABLE IF NOT EXISTS stock_accounts (
  id TEXT PRIMARY KEY, person TEXT, broker TEXT NOT NULL DEFAULT 'kite',
  account_label TEXT, kind TEXT NOT NULL DEFAULT 'connected',
  api_key TEXT, api_secret TEXT, access_token TEXT, kite_user_id TEXT,
  status TEXT DEFAULT 'pending', token_updated_at TIMESTAMPTZ,
  last_synced TIMESTAMPTZ, note TEXT, sellable_on DATE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW());
CREATE TABLE IF NOT EXISTS stock_holdings (
  id TEXT PRIMARY KEY,
  account_id TEXT NOT NULL REFERENCES stock_accounts(id) ON DELETE CASCADE,
  person TEXT, broker TEXT, account_label TEXT, symbol TEXT NOT NULL,
  exchange TEXT, isin TEXT, quantity NUMERIC, avg_price NUMERIC,
  last_synced TIMESTAMPTZ NOT NULL DEFAULT NOW());
CREATE INDEX IF NOT EXISTS idx_stock_holdings_account ON stock_holdings (account_id);`;
  copied = signal(false);
  copySql() { navigator.clipboard?.writeText(this.migrationSql).then(() => { this.copied.set(true); setTimeout(() => this.copied.set(false), 1500); }); }
}
