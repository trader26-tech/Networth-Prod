import { Component, OnInit, OnDestroy, inject, signal, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { ApiService } from '../../services/api.service';
import { getStrategyById, StrategyMeta } from '../../shared/strategy-registry';

interface Check { name: string; value: string; ok: boolean; rationale: string; }
interface NBCard {
  signal: 'go' | 'caution' | 'skip';
  passed: number; total: number; checks: Check[];
  nb_price: number; nb_suggested_limit: number; limit_basis: string;
  shares: number; cost: number;
  rsi: number | null; sma_50: number | null; sma_20: number | null;
  dist_50dma_pct: number | null; dist_20dma_pct: number | null;
  summary: string;
}
interface CSPCard {
  available: boolean;
  selected_expiry: string; expiries: string[]; dte: number;
  best: any | null; alternatives: any[];
}
interface HedgeCard {
  available: boolean;
  best: any | null; alternatives: any[];
  current_hedge_status: any | null;
}
interface CCCard {
  available: boolean; preview_mode: boolean;
  selected_expiry: string; expiries: string[]; dte: number;
  best: any | null; alternatives: any[];
  strike_floor: number | null; strike_floor_basis: string;
}
interface MonitorAlert {
  leg: string; trigger: string; current_value: string; threshold: string;
  fires: boolean; why: string;
}
interface MonitorCard {
  any_firing: boolean; alerts: MonitorAlert[]; summary: string;
}
interface ScanResponse {
  timestamp: string; underlying: string; spot: number; nb_price: number;
  vix: number; atm_iv: number | null;
  expiries: string[]; capital: number;
  cards: { nb: NBCard; csp: CSPCard; hedge: HedgeCard; cc: CCCard; monitor: MonitorCard };
}
interface BestCyclePlanStep { step: number; leg: string; action: string; rationale: string; }
interface BestCycleResponse {
  timestamp: string; spot: number; nb_price: number; vix: number; capital: number;
  nb_entry: NBCard | null;
  best_csp: any | null; best_hedge: any | null; best_cc: any | null;
  monthly_estimate: { gross_premium: number; hedge_cost: number;
                      friction: number; net: number; net_pct: number };
  plan_steps: BestCyclePlanStep[]; summary: string;
}

@Component({
  selector: 'app-protected-wheel',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink],
  templateUrl: './protected-wheel.html',
  styleUrl: './protected-wheel.scss',
})
export class ProtectedWheelComponent implements OnInit, OnDestroy {
  private api = inject(ApiService);
  meta: StrategyMeta = getStrategyById('protected-wheel')!;

  // ── State ───────────────────────────────────────────────────────────────
  loading      = signal(false);
  error        = signal('');
  scan         = signal<ScanResponse | null>(null);

  capital      = signal(1_800_000);
  cspExpiry    = signal('');
  ccExpiry     = signal('');
  hasNbPos     = signal(false);
  nbEntry      = signal(0);
  hasHedge     = signal(false);   // user toggles when they buy/expire hedge
  hasCspOpen   = signal(false);   // user toggles when CSP is open
  hasCcOpen    = signal(false);   // user toggles when CC is open
  phase        = signal<'A' | 'B'>('A');   // A = sell CSP · B = sell CC

  bestLoading  = signal(false);
  bestCycle    = signal<BestCycleResponse | null>(null);
  bestModalOpen = signal(false);

  // Auto-refresh
  private refreshTimer: any = null;

  // Computed convenience — all defensive against missing fields
  spot         = computed(() => this.scan()?.spot ?? 0);
  nbPrice      = computed(() => this.scan()?.nb_price ?? 0);
  vix          = computed(() => this.scan()?.vix ?? 0);
  cardNB       = computed(() => this.scan()?.cards?.nb ?? null);
  cardCSP      = computed(() => this.scan()?.cards?.csp ?? null);
  cardHedge    = computed(() => this.scan()?.cards?.hedge ?? null);
  cardCC       = computed(() => this.scan()?.cards?.cc ?? null);
  cardMonitor  = computed(() => this.scan()?.cards?.monitor ?? null);

  // ── Cycle position ─────────────────────────────────────────────────────
  // Steps: 1 NB Entry → 2 Hedge Buy → 3 Sell CSP → 4 Manage/Roll → 5 Sell CC → 6 Manage/Roll
  // We collapse 4 and 6 into "Manage" sub-states tied to which income leg is open.
  cycleStep = computed<number>(() => {
    if (!this.hasNbPos()) return 1;
    if (!this.hasHedge()) return 2;
    if (this.phase() === 'A') return this.hasCspOpen() ? 4 : 3;
    return this.hasCcOpen() ? 6 : 5;
  });
  cycleStepLabel = computed(() => {
    switch (this.cycleStep()) {
      case 1: return 'Step 1 · BUY NIFTYBEES';
      case 2: return 'Step 2 · BUY HEDGE PUT';
      case 3: return 'Step 3 · SELL CSP';
      case 4: return 'Step 4 · MANAGE / ROLL CSP';
      case 5: return 'Step 5 · SELL COVERED CALL';
      case 6: return 'Step 6 · MANAGE / ROLL CC';
      default: return '';
    }
  });
  // Which income leg is "active" right now (the one card that should be big)
  activeIncomeLeg = computed<'CSP' | 'CC'>(() => this.phase() === 'A' ? 'CSP' : 'CC');

  // Filter monitor alerts to active leg + always-on hedge
  activeAlerts = computed(() => {
    const m = this.cardMonitor();
    if (!m) return [];
    const active = this.activeIncomeLeg();
    return m.alerts.filter(a => a.leg === active || a.leg === 'Hedge');
  });

  ngOnInit() {
    this.loadScan();
    // Refresh every 60s
    this.refreshTimer = setInterval(() => this.loadScan(true), 60_000);
  }

  ngOnDestroy() {
    if (this.refreshTimer) clearInterval(this.refreshTimer);
  }

  loadScan(silent = false) {
    if (!silent) this.loading.set(true);
    this.error.set('');
    this.api.scanProtectedWheel({
      capital:         this.capital(),
      csp_expiry:      this.cspExpiry() || undefined,
      cc_expiry:       this.ccExpiry()  || undefined,
      has_nb_position: this.hasNbPos(),
      nb_entry:        this.nbEntry() || undefined,
    } as any).subscribe({
      next: (r: ScanResponse) => {
        if (!r || !r.cards) {
          this.error.set('Backend returned an empty response. Restart the API server (uvicorn) to load the new /protected-wheel route.');
          this.loading.set(false);
          return;
        }
        this.scan.set(r);
        if (!this.cspExpiry() && r.cards?.csp?.selected_expiry) this.cspExpiry.set(r.cards.csp.selected_expiry);
        if (!this.ccExpiry()  && r.cards?.cc?.selected_expiry)  this.ccExpiry.set(r.cards.cc.selected_expiry);
        this.loading.set(false);
      },
      error: (e) => {
        const status = e?.status ? `(HTTP ${e.status}) ` : '';
        let detail   = e?.error?.detail ?? e?.message ?? 'Failed to load scan';
        if (Array.isArray(detail)) {
          // FastAPI validation errors come as a list of {loc, msg, type}
          detail = detail.map((d: any) => `${(d.loc || []).join('.')}: ${d.msg}`).join(' · ');
        } else if (typeof detail === 'object') {
          try { detail = JSON.stringify(detail); } catch { detail = String(detail); }
        }
        const hint = e?.status === 404
          ? ' — restart the backend (uvicorn) to register the new /protected-wheel route.'
          : '';
        this.error.set(`${status}${detail}${hint}`);
        this.loading.set(false);
      },
    });
  }

  changeCspExpiry(e: string) { this.cspExpiry.set(e); this.loadScan(); }
  changeCcExpiry(e: string)  { this.ccExpiry.set(e);  this.loadScan(); }

  toggleNbHeld(v: boolean) {
    this.hasNbPos.set(v);
    if (v && !this.nbEntry() && this.spot() > 0) this.nbEntry.set(this.spot());
    this.loadScan();
  }

  setPhase(p: 'A' | 'B') { this.phase.set(p); }
  toggleHedge(v: boolean)    { this.hasHedge.set(v); }
  toggleCspOpen(v: boolean)  { this.hasCspOpen.set(v); }
  toggleCcOpen(v: boolean)   { this.hasCcOpen.set(v); }

  setCapital(c: number) { this.capital.set(c); this.loadScan(); }
  setNbEntry(p: number) { this.nbEntry.set(p); this.loadScan(); }

  // ── Best Trade ──────────────────────────────────────────────────────────
  openBestCycle() {
    this.bestModalOpen.set(true);
    this.bestLoading.set(true);
    this.api.bestCycleProtectedWheel(this.capital()).subscribe({
      next: (r) => { this.bestCycle.set(r); this.bestLoading.set(false); },
      error: () => { this.bestLoading.set(false); },
    });
  }
  closeBestCycle() { this.bestModalOpen.set(false); }

  // ── Helpers ─────────────────────────────────────────────────────────────
  fmtRs(n: number | null | undefined): string {
    if (n == null || isNaN(n)) return '—';
    const sign = n < 0 ? '-' : '';
    const abs = Math.abs(n);
    if (abs >= 1e7) return `${sign}₹${(abs/1e7).toFixed(2)} Cr`;
    if (abs >= 1e5) return `${sign}₹${(abs/1e5).toFixed(2)} L`;
    if (abs >= 1e3) return `${sign}₹${(abs/1e3).toFixed(1)} k`;
    return `${sign}₹${abs.toFixed(0)}`;
  }
  fmtNum(n: number | null | undefined, dp = 0): string {
    if (n == null || isNaN(n)) return '—';
    return n.toLocaleString('en-IN', { minimumFractionDigits: dp, maximumFractionDigits: dp });
  }
  signalClass(s: string | undefined): string {
    if (!s) return '';
    return `sig-${s}`;
  }
  signalLabel(s: string | undefined): string {
    if (s === 'go') return '✓ GO';
    if (s === 'caution') return '⚠ CAUTION';
    if (s === 'skip') return '✕ SKIP';
    return '—';
  }
}
