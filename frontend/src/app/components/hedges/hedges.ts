import { Component, OnInit, OnDestroy, inject, signal, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { ApiService } from '../../services/api.service';

interface HedgeCandidate {
  expiry: string; dte: number; strike: number; premium: number;
  bid: number; ask: number; otm_pct: number; delta: number;
  iv: number; oi: number; cost_per_protected: number;
  cost_per_day: number; score: number; rank: number;
  hit_prob_pct?: number; hit_factor?: number; sweet_spot_bonus?: number;
  why_good: string[]; why_caution: string[];
  math: any; summary: string;
}

interface Hedge {
  id: string;
  strike: number; expiry: string; lots: number; lot_size: number;
  premium_paid: number; symbol?: string; notes?: string;
  status: 'open' | 'closed' | 'rolled';
  current_price?: number; unrealized_pnl?: number; dte?: number;
  should_roll?: boolean; tagged_strategies?: string[];
  created_at: string; closed_at?: string;
  realized_pnl?: number; close_price?: number;
  roll_status?: 'hold' | 'soon' | 'now';
  roll_message?: string;
}

@Component({
  selector: 'app-hedges',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink],
  templateUrl: './hedges.html',
  styleUrl: './hedges.scss',
})
export class HedgesComponent implements OnInit, OnDestroy {
  private api = inject(ApiService);

  loading       = signal(false);
  error         = signal('');
  hedges        = signal<Hedge[]>([]);
  spot          = signal(0);
  nbPrice       = signal(0);

  // Scanner state
  scanLoading   = signal(false);
  scanResult    = signal<HedgeCandidate[]>([]);
  scanBest      = signal<HedgeCandidate | null>(null);
  showScanner   = signal(false);
  scanFallback  = signal<string | null>(null);
  scanDiag      = signal<string | null>(null);
  scanAttempted = signal<any[]>([]);

  // Manual create form
  manualMode    = signal(false);
  formStrike    = signal(0);
  formExpiry    = signal('');
  formLots      = signal(1);
  formLotSize   = signal(75);
  formPremium   = signal(0);
  formSymbol    = signal('');

  // Filter
  filterStatus  = signal<'all' | 'open' | 'closed'>('open');

  filteredHedges = computed(() => {
    const f = this.filterStatus();
    if (f === 'all') return this.hedges();
    return this.hedges().filter(h => f === 'open' ? h.status === 'open' : h.status !== 'open');
  });

  countOpen   = computed(() => this.hedges().filter(h => h.status === 'open').length);
  countClosed = computed(() => this.hedges().filter(h => h.status !== 'open').length);
  countTotal  = computed(() => this.hedges().length);

  totalAnnualDrag = computed(() => {
    const open = this.hedges().filter(h => h.status === 'open');
    const totalCost = open.reduce((s, h) => s + h.premium_paid * h.lots * h.lot_size, 0);
    const avgDte = open.length ? open.reduce((s, h) => s + (h.dte || 0), 0) / open.length : 0;
    if (!avgDte) return null;
    const rollsPerYear = 365 / avgDte;
    const annual = totalCost * rollsPerYear;
    const spot = this.spot();
    if (!spot) return null;
    return { annual_cost: Math.round(annual), pct_of_spot: (annual / spot * 100).toFixed(2) };
  });

  // Roll modal state
  rollHedgeId   = signal<string | null>(null);
  rollClosePrice = signal(0);
  rollNewStrike = signal(0);
  rollNewExpiry = signal('');
  rollNewPremium = signal(0);

  // Close modal state
  closeHedgeId   = signal<string | null>(null);
  closeAtPrice   = signal(0);

  ngOnInit() {
    this.loadHedges();
  }

  ngOnDestroy() {}

  loadHedges() {
    this.loading.set(true);
    this.error.set('');
    this.api.listHedges().subscribe({
      next: (r) => {
        this.hedges.set(r.hedges || []);
        this.spot.set(r.spot || 0);
        this.nbPrice.set(r.nb_price || 0);
        this.loading.set(false);
      },
      error: (e) => {
        this.error.set(e?.error?.detail || e?.message || 'Failed to load');
        this.loading.set(false);
      },
    });
  }

  scanCandidates() {
    this.scanLoading.set(true);
    this.showScanner.set(true);
    this.api.scanHedgeCandidates().subscribe({
      next: (r) => {
        this.scanResult.set(r.candidates || []);
        this.scanBest.set(r.best || null);
        this.scanFallback.set(r.fallback_message || null);
        this.scanDiag.set(r.diagnostic || null);
        this.scanAttempted.set(r.expiries_attempted || []);
        this.scanLoading.set(false);
      },
      error: () => { this.scanLoading.set(false); },
    });
  }

  createFromCandidate(c: HedgeCandidate) {
    this.api.createHedge({
      strike:       c.strike,
      expiry:       c.expiry,
      lots:         1,
      lot_size:     75,
      premium_paid: c.premium,
      symbol:       '',
      notes:        `Created from scanner · score ${c.score}`,
      tagged_strategies: [],
    }).subscribe({
      next: () => {
        this.showScanner.set(false);
        this.loadHedges();
      },
    });
  }

  createManual() {
    if (!this.formStrike() || !this.formExpiry() || !this.formPremium()) {
      this.error.set('Strike, expiry and premium are required.');
      return;
    }
    this.api.createHedge({
      strike:       this.formStrike(),
      expiry:       this.formExpiry(),
      lots:         this.formLots(),
      lot_size:     this.formLotSize(),
      premium_paid: this.formPremium(),
      symbol:       this.formSymbol(),
      notes:        'Manually created',
      tagged_strategies: [],
    }).subscribe({
      next: () => {
        this.manualMode.set(false);
        this.formStrike.set(0); this.formExpiry.set('');
        this.formPremium.set(0); this.formSymbol.set('');
        this.loadHedges();
      },
    });
  }

  openCloseModal(h: Hedge) {
    this.closeHedgeId.set(h.id);
    this.closeAtPrice.set(h.current_price || h.premium_paid);
  }
  closeCloseModal() { this.closeHedgeId.set(null); }
  confirmClose() {
    const hid = this.closeHedgeId();
    if (!hid) return;
    this.api.closeHedge(hid, this.closeAtPrice()).subscribe({
      next: () => {
        this.closeHedgeId.set(null);
        this.loadHedges();
      },
    });
  }

  openRollModal(h: Hedge) {
    this.rollHedgeId.set(h.id);
    this.rollClosePrice.set(h.current_price || h.premium_paid);
    this.rollNewStrike.set(h.strike);
    this.rollNewExpiry.set('');
    this.rollNewPremium.set(0);
  }
  closeRollModal() { this.rollHedgeId.set(null); }
  confirmRoll() {
    const hid = this.rollHedgeId();
    if (!hid || !this.rollNewStrike() || !this.rollNewExpiry() || !this.rollNewPremium()) {
      return;
    }
    this.api.rollHedge(hid, {
      close_price:    this.rollClosePrice(),
      new_strike:     this.rollNewStrike(),
      new_expiry:     this.rollNewExpiry(),
      new_lots:       1,
      new_lot_size:   75,
      new_premium:    this.rollNewPremium(),
      transfer_tags:  true,
    }).subscribe({
      next: () => {
        this.rollHedgeId.set(null);
        this.loadHedges();
      },
    });
  }

  deleteHedge(h: Hedge) {
    if (!confirm(`Delete hedge ${h.strike} PE ${h.expiry}? This is permanent.`)) return;
    this.api.deleteHedge(h.id).subscribe({ next: () => this.loadHedges() });
  }

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
  pnlClass(n: number | null | undefined): string {
    if (n == null) return '';
    return n >= 0 ? 'pnl-pos' : 'pnl-neg';
  }
}
