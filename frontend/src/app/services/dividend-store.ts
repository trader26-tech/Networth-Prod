import { Injectable, computed, inject, signal } from '@angular/core';
import { HttpErrorResponse } from '@angular/common/http';
import { Observable, tap } from 'rxjs';
import { EquityService, Dividend, DividendStatus } from './equity.service';

/**
 * Single reactive source of dividend events + per-symbol meta, shared by the
 * calendar (`<app-dividends>`) and the holdings-page panel so a change in one
 * place (mark received, add expected, edit "previous years") shows in the other
 * immediately. Mutations are optimistic and reconciled with the server reply.
 */
@Injectable({ providedIn: 'root' })
export class DividendStore {
  private api = inject(EquityService);

  readonly divs = signal<Dividend[]>([]);
  readonly meta = signal<Record<string, number | null>>({});   // symbol → manual prev-years
  readonly tds = signal<Record<string, number>>({});           // person → TDS rate ('' = default)
  readonly collected = signal<Record<string, number>>({});     // 'SYMBOL|person' → collected override
  // Per-account reconciliation working-state ('dvId|accId' → {rate, received tick}),
  // set before "Add as received". Lives on the singleton store (survives navigation)
  // and is persisted to the durable KV (survives refresh) so marks aren't lost.
  readonly recon = signal<Record<string, { ps: number | null; received: boolean }>>({});
  readonly loaded = signal(false);
  readonly needsMigration = signal(false);

  private static ckey(symbol: string, person: string): string {
    return `${DividendStore.base(symbol)}|${(person || '').trim()}`;
  }
  /** Manual collected override for a (stock, person), or null if none. */
  collectedFor(symbol: string, person: string): number | null {
    const v = this.collected()[DividendStore.ckey(symbol, person)];
    return v == null ? null : v;
  }

  static readonly DEFAULT_TDS = 0.10;
  /** Global default TDS rate (the '' row), falling back to 10%. */
  defaultTds = computed(() => this.tds()[''] ?? DividendStore.DEFAULT_TDS);
  /** TDS rate for a person: their override, else the global default. */
  tdsFor(person: string | null | undefined): number {
    const t = this.tds();
    const p = (person || '').trim();
    return t[p] ?? t[''] ?? DividendStore.DEFAULT_TDS;
  }

  /** Normalize a symbol: uppercase + drop the NSE series suffix (ITC-BE → ITC). */
  static base(s: string): string { return (s || '').toUpperCase().replace(/-[A-Z]{1,2}$/, ''); }

  /** Ensure a row carries a valid 3-state status (deriving it from the legacy
   * `received` boolean for older rows) with `received` kept in sync. */
  private static withStatus(row: Dividend): Dividend {
    const st: DividendStatus =
      row.status === 'received' || row.status === 'pending' || row.status === 'not_received'
        ? row.status : (row.received === true ? 'received' : 'pending');
    const currency = row.currency === 'USD' ? 'USD' : 'INR';
    return { ...row, status: st, received: st === 'received', currency };
  }

  /** Indian financial year start (Apr 1) for a YYYY-MM-DD date. */
  static fyStart(dateStr: string): number {
    const [y, m] = (dateStr || '').split('-').map(Number);
    if (!y) return 0;
    return (m && m >= 4) ? y : y - 1;
  }

  private _inFlight = false;
  load(force = false) {
    if ((this.loaded() || this._inFlight) && !force) return;
    this._inFlight = true;
    this.api.dividends().subscribe({
      next: d => { this.divs.set((d || []).map(x => DividendStore.withStatus(x))); this.loaded.set(true); this._inFlight = false; },
      error: (e: HttpErrorResponse) => { if (e.status === 503) this.needsMigration.set(true); this.loaded.set(true); this._inFlight = false; },
    });
    this.api.dividendMeta().subscribe({
      next: rows => {
        const m: Record<string, number | null> = {};
        for (const r of rows || []) if (r.prev_years != null) m[(r.symbol || '').toUpperCase()] = r.prev_years;
        this.meta.set(m);
      },
      error: () => {},
    });
    this.api.dividendTds().subscribe({
      next: rows => {
        const t: Record<string, number> = {};
        for (const r of rows || []) if (r.rate != null) t[(r.person || '').trim()] = r.rate;
        this.tds.set(t);
      },
      error: () => {},
    });
    this.api.dividendCollected().subscribe({
      next: rows => {
        const c: Record<string, number> = {};
        for (const r of rows || []) if (r.collected != null) c[DividendStore.ckey(r.symbol, r.person)] = r.collected;
        this.collected.set(c);
      },
      error: () => {},
    });
    this.api.dividendRecon().subscribe({ next: blob => this.recon.set(blob || {}), error: () => {} });
  }

  // ── Reconciliation state — mutate + persist (debounced) so in-progress
  //    per-account rate/tick survive refresh, navigation, and reverts ──────────
  reconAt(key: string): { ps: number | null; received: boolean } | undefined { return this.recon()[key]; }
  setRecon(key: string, val: { ps: number | null; received: boolean }) {
    this.recon.update(m => ({ ...m, [key]: val }));
    this.persistRecon();
  }
  /** Drop every account line of a dividend (called once it's logged). */
  clearReconFor(dvId: string) {
    this.recon.update(m => { const n = { ...m }; for (const k of Object.keys(n)) if (k.startsWith(dvId + '|')) delete n[k]; return n; });
    this.persistRecon();
  }
  private _reconTimer: ReturnType<typeof setTimeout> | null = null;
  private persistRecon() {
    if (this._reconTimer) clearTimeout(this._reconTimer);
    this._reconTimer = setTimeout(() => {
      this._reconTimer = null;
      this.api.saveDividendRecon(this.recon()).subscribe({ error: () => {} });
    }, 500);
  }

  /** Add a dividend; folds the server row into local state on success. The
   * caller subscribes for the saving spinner / error handling. */
  add(body: { date: string; symbol: string; name?: string; per_share: number; shares: number; received?: boolean; status?: DividendStatus; currency?: 'INR' | 'USD'; person?: string; account_id?: string; note?: string }): Observable<Dividend> {
    return this.api.addDividend(body).pipe(
      tap(row => this.divs.update(list => [...list, DividendStore.withStatus(row)])),
    );
  }

  setReceived(id: string, received: boolean) { this.update(id, { status: received ? 'received' : 'pending' }); }
  /** Set the 3-state status directly (pending | received | not_received). */
  setStatus(id: string, status: DividendStatus) { this.update(id, { status }); }

  /** Edit any field(s) of a dividend — optimistic, reconciled with the server.
   * `amount` is re-derived locally too so totals update instantly. */
  update(id: string, patch: { date?: string; per_share?: number; shares?: number; received?: boolean; status?: DividendStatus; currency?: 'INR' | 'USD'; person?: string }) {
    const prev = this.divs();
    this.divs.set(prev.map(d => {
      if (d.id !== id) return d;
      const next = DividendStore.withStatus({ ...d, ...patch } as Dividend);
      next.amount = (patch.per_share ?? d.per_share) * (patch.shares ?? d.shares);
      return next;
    }));
    this.api.updateDividend(id, patch).subscribe({
      next: row => this.divs.update(list => list.map(d => d.id === id ? DividendStore.withStatus({ ...d, ...row }) : d)),
      error: () => this.divs.set(prev),                                   // roll back
    });
  }

  remove(id: string) {
    const prev = this.divs();
    this.divs.set(prev.filter(d => d.id !== id));                         // optimistic
    this.api.deleteDividend(id).subscribe({ error: () => this.divs.set(prev) });
  }

  setMeta(symbol: string, prevYears: number | null) {
    const sym = symbol.toUpperCase();
    this.meta.update(m => { const n = { ...m }; if (prevYears == null) delete n[sym]; else n[sym] = prevYears; return n; });
    this.api.setDividendMeta(sym, prevYears).subscribe({ error: () => {} });
  }

  setTds(person: string, rate: number | null) {
    const p = (person || '').trim();
    this.tds.update(t => { const n = { ...t }; if (rate == null) delete n[p]; else n[p] = rate; return n; });
    this.api.setDividendTds(p, rate).subscribe({ error: () => {} });
  }

  setCollected(symbol: string, person: string, collected: number | null) {
    const k = DividendStore.ckey(symbol, person);
    this.collected.update(c => { const n = { ...c }; if (collected == null) delete n[k]; else n[k] = collected; return n; });
    this.api.setDividendCollected(DividendStore.base(symbol), (person || '').trim(), collected).subscribe({ error: () => {} });
  }

  // ── derived views ──────────────────────────────────────────────────────────
  /** "To receive" total — only genuinely pending money (excludes not-received). */
  pendingTotal = computed(() => this.divs().filter(d => d.status === 'pending').reduce((a, b) => a + b.amount, 0));

  /** Events for one symbol (base-normalized), newest record-date first. */
  forSymbol(symbol: string): Dividend[] {
    const k = DividendStore.base(symbol);
    return this.divs().filter(d => DividendStore.base(d.symbol) === k)
      .sort((a, b) => (b.date || '').localeCompare(a.date || ''));
  }
}
