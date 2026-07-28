import { Component, OnDestroy, computed, inject, signal } from '@angular/core';
import { CommonModule, DecimalPipe } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ApiService } from '../../services/api.service';

interface PinnedBuy {
  id:     string;
  expiry: string;
  dte:    number;      // captured at pin time (decreases as days pass)
  strike: number;
  entry: {
    price:    number;
    iv:       number;
    delta:    number | null;
    pinnedAt: Date;
  };
  current: {
    price:     number | null;
    iv:        number | null;
    delta:     number | null;
    updatedAt: Date | null;
  };
}

/** NIFTY F&O lot size — hardcoded for v1 since we only support NIFTY. */
const NIFTY_LOT = 75;

@Component({
  selector: 'app-pe-scanner',
  imports: [CommonModule, DecimalPipe, FormsModule],
  templateUrl: './pe-scanner.html',
  styleUrl: './pe-scanner.scss',
})
export class PeScannerComponent implements OnDestroy {
  private api = inject(ApiService);

  // ── Shared ─────────────────────────────────────────────────────────────
  underlying = signal('NIFTY');
  lotSize    = signal(NIFTY_LOT);

  // ── Cheap (Phase 1 buy candidates) ────────────────────────────────────
  cheapMinDte = signal(30);  cheapMaxDte = signal(400);
  cheapMnyMin = signal(-15); cheapMnyMax = signal(0);
  cheapTopN   = signal(30);
  cWPrem  = signal(25); cWTheta = signal(25);
  cWSkew  = signal(25); cWTerm  = signal(25);
  cheapResult  = signal<any | null>(null);
  cheapLoading = signal(false);
  cheapError   = signal('');

  // ── Rich (Phase 2 sell candidates) ────────────────────────────────────
  richMinDte = signal(30);  richMaxDte = signal(180);
  richMnyMin = signal(-10); richMnyMax = signal(0);
  richTopN   = signal(50);
  rWYield = signal(25); rWPrem = signal(25);
  rWAtm   = signal(25); rWTerm = signal(25);
  richResult  = signal<any | null>(null);
  richLoading = signal(false);
  richError   = signal('');
  lastRichUpdate = signal<Date | null>(null);

  // ── Pinned positions ──────────────────────────────────────────────────
  pinnedBuys     = signal<PinnedBuy[]>([]);
  activePinId    = signal<string | null>(null);
  refreshingPins = signal(false);

  // ── Polling ───────────────────────────────────────────────────────────
  autoRefresh   = signal(true);
  pollInterval  = signal(60);
  nextPollIn    = signal(0);
  cheapCollapsed = signal(false);

  private pollHandle: any = null;
  private countdownHandle: any = null;

  ngOnDestroy() { this.stopPolling(); }

  // ══ Computed ══════════════════════════════════════════════════════════
  activePin = computed(() => {
    const id = this.activePinId();
    if (!id) return null;
    return this.pinnedBuys().find(p => p.id === id) || null;
  });

  cheapRows = computed(() => (this.cheapResult()?.candidates || []) as any[]);

  richRows = computed(() => {
    const all = (this.richResult()?.candidates || []) as any[];
    const pin = this.activePin();
    if (!pin) return all;
    return all.filter(r => r.expiry === pin.expiry);
  });

  /** Set of "expiry|strike" keys for pinned positions — for quick lookup. */
  private pinnedKeys = computed(() => {
    const set = new Set<string>();
    for (const p of this.pinnedBuys()) set.add(`${p.expiry}|${p.strike}`);
    return set;
  });

  // ══ Actions: cheap scan ═══════════════════════════════════════════════
  runCheapScan() {
    this.cheapError.set('');
    this.cheapLoading.set(true);
    this.api.cheapPeScan({
      underlying:    this.underlying(),
      min_dte:       this.cheapMinDte(),
      max_dte:       this.cheapMaxDte(),
      moneyness_min: this.cheapMnyMin(),
      moneyness_max: this.cheapMnyMax(),
      weights:       `${this.cWPrem()},${this.cWTheta()},${this.cWSkew()},${this.cWTerm()}`,
      top_n:         this.cheapTopN(),
    }).subscribe({
      next: (r) => {
        this.cheapResult.set(r);
        this.cheapLoading.set(false);
        this.updatePinPricesFromCandidates(r?.candidates || []);
      },
      error: (e) => {
        this.cheapError.set(e?.error?.detail || e?.message || 'Scan failed');
        this.cheapLoading.set(false);
      },
    });
  }

  // ══ Actions: rich scan ════════════════════════════════════════════════
  runRichScan(silent = false) {
    if (!silent) this.richLoading.set(true);
    this.richError.set('');
    const pin = this.activePin();
    const minDte = pin ? Math.max(1, pin.dte - 1) : this.richMinDte();
    const maxDte = pin ? pin.dte + 1              : this.richMaxDte();
    this.api.richPeScan({
      underlying:    this.underlying(),
      min_dte:       minDte,
      max_dte:       maxDte,
      moneyness_min: this.richMnyMin(),
      moneyness_max: this.richMnyMax(),
      weights:       `${this.rWYield()},${this.rWPrem()},${this.rWAtm()},${this.rWTerm()}`,
      top_n:         this.richTopN(),
    }).subscribe({
      next: (r) => {
        this.richResult.set(r);
        this.richLoading.set(false);
        this.lastRichUpdate.set(new Date());
        this.updatePinPricesFromCandidates(r?.candidates || []);
        // For pins on other expiries (not in this rich scan), pull their chains
        this.refreshOffActivePins(r?.expiries_scanned || []);
      },
      error: (e) => {
        this.richError.set(e?.error?.detail || e?.message || 'Scan failed');
        this.richLoading.set(false);
      },
    });
  }

  // ══ Pin lifecycle ═════════════════════════════════════════════════════
  selectBuy(row: any) {
    const key = `${row.expiry}|${row.strike}`;
    if (this.pinnedKeys().has(key)) {
      // Already pinned — just make it active
      const existing = this.pinnedBuys().find(p => `${p.expiry}|${p.strike}` === key);
      if (existing) this.setActive(existing.id);
      return;
    }
    const pin: PinnedBuy = {
      id:     `${row.expiry}-${row.strike}-${Date.now()}`,
      expiry: row.expiry,
      dte:    row.dte,
      strike: row.strike,
      entry: {
        price:    row.pe?.price ?? 0,
        iv:       row.pe?.iv ?? 0,
        delta:    row.pe?.delta ?? null,
        pinnedAt: new Date(),
      },
      current: {
        price:     row.pe?.price ?? null,
        iv:        row.pe?.iv ?? null,
        delta:     row.pe?.delta ?? null,
        updatedAt: new Date(),
      },
    };
    this.pinnedBuys.update(arr => [...arr, pin]);
    this.setActive(pin.id);
    this.cheapCollapsed.set(true);
    this.runRichScan();
    if (this.autoRefresh()) this.startPolling();
  }

  removePin(id: string) {
    const remaining = this.pinnedBuys().filter(p => p.id !== id);
    this.pinnedBuys.set(remaining);
    if (this.activePinId() === id) {
      const next = remaining[remaining.length - 1] || null;
      this.activePinId.set(next ? next.id : null);
    }
    if (remaining.length === 0) {
      this.stopPolling();
      this.cheapCollapsed.set(false);
    } else if (this.activePin()) {
      this.runRichScan();
    }
  }

  setActive(id: string) {
    if (this.activePinId() === id) return;
    this.activePinId.set(id);
    this.runRichScan();
  }

  // ══ Pin price refresh ═════════════════════════════════════════════════
  /** Walk scan candidates and update current price/iv/delta for any matching pins. */
  private updatePinPricesFromCandidates(candidates: any[]) {
    if (!candidates?.length || !this.pinnedBuys().length) return;
    const byKey = new Map<string, any>();
    for (const c of candidates) byKey.set(`${c.expiry}|${c.strike}`, c);
    const now = new Date();
    this.pinnedBuys.update(arr => arr.map(p => {
      const match = byKey.get(`${p.expiry}|${p.strike}`);
      if (!match) return p;
      return {
        ...p,
        current: {
          price:     match.pe?.price ?? p.current.price,
          iv:        match.pe?.iv ?? p.current.iv,
          delta:     match.pe?.delta ?? p.current.delta,
          updatedAt: now,
        },
      };
    }));
  }

  /** For pinned positions whose expiry wasn't covered by the latest rich scan,
   *  fetch the option chain for that expiry and update from it. */
  private refreshOffActivePins(scannedExpiries: string[]) {
    const scanned = new Set(scannedExpiries);
    const offExpiries = new Set<string>();
    for (const p of this.pinnedBuys()) {
      if (!scanned.has(p.expiry)) offExpiries.add(p.expiry);
    }
    if (offExpiries.size === 0) return;
    for (const exp of offExpiries) {
      this.api.getOptionChain(this.underlying(), exp).subscribe({
        next: (chain: any) => this.updatePinPricesFromChain(exp, chain),
        error: (e) => console.warn(`pin refresh failed for ${exp}:`, e),
      });
    }
  }

  /** Walk a single-expiry chain response and update matching pin prices. */
  private updatePinPricesFromChain(expiry: string, chain: any) {
    const rows = chain?.chain || [];
    const byStrike = new Map<number, any>();
    for (const r of rows) byStrike.set(Number(r.strike), r);
    const now = new Date();
    this.pinnedBuys.update(arr => arr.map(p => {
      if (p.expiry !== expiry) return p;
      const match = byStrike.get(p.strike);
      if (!match) return p;
      return {
        ...p,
        current: {
          price:     match.pe?.price ?? p.current.price,
          iv:        match.pe?.iv ?? p.current.iv,
          delta:     match.pe?.delta ?? p.current.delta,
          updatedAt: now,
        },
      };
    }));
  }

  /** Manual: refresh ALL pinned positions by hitting chain endpoints. */
  refreshAllPins() {
    const expiries = new Set<string>();
    for (const p of this.pinnedBuys()) expiries.add(p.expiry);
    if (expiries.size === 0) return;
    this.refreshingPins.set(true);
    let remaining = expiries.size;
    const done = () => {
      remaining--;
      if (remaining <= 0) this.refreshingPins.set(false);
    };
    for (const exp of expiries) {
      this.api.getOptionChain(this.underlying(), exp).subscribe({
        next: (chain: any) => { this.updatePinPricesFromChain(exp, chain); done(); },
        error: () => done(),
      });
    }
  }

  // ══ Polling ═══════════════════════════════════════════════════════════
  toggleAutoRefresh() {
    const next = !this.autoRefresh();
    this.autoRefresh.set(next);
    if (next && this.activePin()) this.startPolling();
    else this.stopPolling();
  }

  private startPolling() {
    this.stopPolling();
    this.nextPollIn.set(this.pollInterval());
    this.pollHandle = setInterval(() => {
      this.runRichScan(true);
      this.nextPollIn.set(this.pollInterval());
    }, this.pollInterval() * 1000);
    this.countdownHandle = setInterval(() => {
      const v = this.nextPollIn();
      this.nextPollIn.set(v > 0 ? v - 1 : this.pollInterval());
    }, 1000);
  }

  private stopPolling() {
    if (this.pollHandle)      { clearInterval(this.pollHandle);      this.pollHandle = null; }
    if (this.countdownHandle) { clearInterval(this.countdownHandle); this.countdownHandle = null; }
    this.nextPollIn.set(0);
  }

  // ══ Pin P&L helpers ═══════════════════════════════════════════════════
  pinPnl(p: PinnedBuy): number | null {
    if (p.current.price == null) return null;
    return p.current.price - p.entry.price;
  }

  pinPnlPct(p: PinnedBuy): number | null {
    if (p.current.price == null || p.entry.price === 0) return null;
    return (p.current.price - p.entry.price) / p.entry.price * 100;
  }

  pinPnlLot(p: PinnedBuy): number | null {
    const per = this.pinPnl(p);
    return per == null ? null : per * this.lotSize();
  }

  ageSince(d: Date): string {
    const sec = Math.floor((Date.now() - d.getTime()) / 1000);
    if (sec < 60)    return `${sec}s ago`;
    if (sec < 3600)  return `${Math.floor(sec / 60)}m ago`;
    if (sec < 86400) return `${Math.floor(sec / 3600)}h ago`;
    return `${Math.floor(sec / 86400)}d ago`;
  }

  // ══ Spread analysis (for rich rows when a pin is active) ═════════════
  isStructureB(row: any): boolean {
    const pin = this.activePin();
    return !!pin && row.strike < pin.strike;
  }
  netCredit(row: any): number | null {
    const pin = this.activePin();
    if (!pin) return null;
    return (row.pe?.price ?? 0) - pin.entry.price;
  }
  spreadWidth(row: any): number | null {
    const pin = this.activePin();
    if (!pin || row.strike >= pin.strike) return null;
    return pin.strike - row.strike;
  }

  // ══ Formatters ════════════════════════════════════════════════════════
  fmtPct(v: number | null | undefined, d = 2): string {
    if (v == null || isNaN(v as any)) return '—';
    return `${v >= 0 ? '+' : ''}${Number(v).toFixed(d)}%`;
  }
  fmtScore(v: number | null | undefined): string {
    if (v == null) return '—';
    return Number(v).toFixed(1);
  }
  fmtAnnYield(v: number | null | undefined): string {
    if (v == null || isNaN(v as any)) return '—';
    return `${(Number(v) * 100).toFixed(1)}%`;
  }
  fmtRs(v: number | null | undefined, d = 2): string {
    if (v == null || isNaN(v as any)) return '—';
    return `₹${Number(v).toFixed(d)}`;
  }
  fmtRsSigned(v: number | null | undefined, d = 2): string {
    if (v == null || isNaN(v as any)) return '—';
    const n = Number(v);
    return `${n >= 0 ? '+' : '−'}₹${Math.abs(n).toFixed(d)}`;
  }
  fmtRsSignedCompact(v: number | null | undefined): string {
    if (v == null || isNaN(v as any)) return '—';
    const n = Number(v);
    const a = Math.abs(n);
    const sign = n >= 0 ? '+' : '−';
    if (a >= 1_00_000) return `${sign}₹${(a / 1_00_000).toFixed(2)}L`;
    if (a >= 1_000)    return `${sign}₹${(a / 1_000).toFixed(1)}K`;
    return `${sign}₹${a.toFixed(0)}`;
  }
  fmtTime(d: Date | null): string {
    if (!d) return '—';
    return d.toLocaleTimeString();
  }
  scoreClass(v: number | null | undefined): string {
    if (v == null) return 'na';
    if (v >= 75) return 'good';
    if (v >= 50) return 'mid';
    if (v >= 25) return 'warn';
    return 'bad';
  }
  pnlClass(v: number | null | undefined): string {
    if (v == null) return 'na';
    if (v > 0) return 'positive';
    if (v < 0) return 'negative';
    return 'flat';
  }
}
