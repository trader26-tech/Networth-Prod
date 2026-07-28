import { Component, OnInit, inject, signal, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { ApiService } from '../../services/api.service';
import { StrategyBuilderService, PendingLeg } from '../../services/strategy-builder.service';
import { PayoffGraphComponent } from './payoff-graph.component';

interface LegLiq {
  oi: number; vol: number;
  bid: number; ask: number;
  bid_qty: number; ask_qty: number;
  bid_qty_total: number; ask_qty_total: number;
  side: 'BUY' | 'SELL';
  available_qty: number;
  lots: number;
}

interface BwbResult {
  type: 'CE' | 'PE';
  K1: number; K2: number; K3: number;
  P1: number; P2: number; P3: number;          // fillable bid/ask prices
  P1_ltp: number; P2_ltp: number; P3_ltp: number;
  sym1: string; sym2: string; sym3: string;
  _underlying?: string;
  _expiry?: string;
  _dte?: number;
  _spot?: number;
  iv1: number | null; iv2: number | null; iv3: number | null;
  leg_liq: [LegLiq, LegLiq, LegLiq];
  net_credit: number;
  max_profit: number;
  max_loss: number;
  capital: number;
  lot_size: number;
  wing_skew: 'left' | 'right';
  left_wing: number;
  right_wing: number;
  min_oi: number;
  min_volume: number;
  max_spread_pct: number;
  liquidity_score: number;
  liquidity_tier: 'excellent' | 'good' | 'fair' | 'poor';
  fillable: boolean;
  lots_available: number;
  rr_ratio: number;
  pnl_below: number;
  pnl_peak: number;
  pnl_above: number;
  atm_distance: number;
  pop: number;             // 0-1
  expected_value: number;  // ₹ gross
  iv_used_pct: number;
  charges: {
    brokerage: number; stt: number; exchange: number;
    sebi: number; stamp: number; gst: number; total: number;
  };
  net_max_profit: number;
  net_max_loss: number;
  net_expected_value: number;
}

interface BatmanLegLiq {
  oi: number; vol: number;
  bid: number; ask: number;
  bid_qty_total: number; ask_qty_total: number;
  side: 'BUY' | 'SELL';
  available_qty: number;
  lots: number;
}

interface BatmanResult {
  K1: number; K2: number; K3: number;  // put strikes
  K4: number; K5: number; K6: number;  // call strikes
  P1: number; P2: number; P3: number;
  P4: number; P5: number; P6: number;
  P1_ltp: number; P2_ltp: number; P3_ltp: number;
  P4_ltp: number; P5_ltp: number; P6_ltp: number;
  sym1: string; sym2: string; sym3: string;
  sym4: string; sym5: string; sym6: string;
  _underlying?: string;
  _expiry?: string;
  _dte?: number;
  _spot?: number;
  iv1: number | null; iv2: number | null; iv3: number | null;
  iv4: number | null; iv5: number | null; iv6: number | null;
  leg_liq: [BatmanLegLiq, BatmanLegLiq, BatmanLegLiq, BatmanLegLiq, BatmanLegLiq, BatmanLegLiq];
  lots_available: number;
  net_credit: number;
  max_profit: number;
  max_loss: number;
  capital: number;
  lot_size: number;
  pnl_far_left: number;
  pnl_left_peak: number;
  pnl_middle: number;
  pnl_right_peak: number;
  pnl_far_right: number;
  left_inner_wing: number;
  left_outer_wing: number;
  right_inner_wing: number;
  right_outer_wing: number;
  min_oi: number;
  min_volume: number;
  max_spread_pct: number;
  liquidity_score: number;
  liquidity_tier: 'excellent' | 'good' | 'fair' | 'poor';
  fillable: boolean;
  rr_ratio: number;
  atm_distance_left: number;
  atm_distance_right: number;
  pop: number;
  expected_value: number;
  iv_used_pct: number;
  charges: {
    brokerage: number; stt: number; exchange: number;
    sebi: number; stamp: number; gst: number; total: number;
  };
  net_max_profit: number;
  net_max_loss: number;
  net_expected_value: number;
}

interface IcLegLiq {
  oi: number; vol: number;
  bid: number; ask: number;
  bid_qty_total: number; ask_qty_total: number;
  side: 'BUY' | 'SELL';
  available_qty: number;
  lots: number;
}

interface IcResult {
  K1: number; K2: number; K3: number; K4: number;
  P1: number; P2: number; P3: number; P4: number;
  P1_ltp: number; P2_ltp: number; P3_ltp: number; P4_ltp: number;
  sym1: string; sym2: string; sym3: string; sym4: string;
  _underlying?: string; _expiry?: string; _dte?: number; _spot?: number;
  iv1: number | null; iv2: number | null; iv3: number | null; iv4: number | null;
  leg_liq: [IcLegLiq, IcLegLiq, IcLegLiq, IcLegLiq];
  lots_available: number;
  net_credit: number;
  max_profit: number;
  max_loss: number;
  capital: number;
  lot_size: number;
  pnl_far_left: number;
  pnl_profit_zone: number;
  pnl_far_right: number;
  profit_zone_width: number;
  profit_zone_pct: number;
  left_wing_width: number;
  right_wing_width: number;
  wing_width: number;
  // Delta info (steps 1 & 2 of the 10-step IC algorithm)
  delta_short_put: number;
  delta_short_call: number;
  // Management levels (steps 8, 9, 10)
  be_lower: number;
  be_upper: number;
  profit_target_50: number;
  stop_loss_2x: number;
  credit_ratio: number;
  atm_distance_left: number;
  atm_distance_right: number;
  min_oi: number;
  min_volume: number;
  max_spread_pct: number;
  liquidity_score: number;
  liquidity_tier: 'excellent' | 'good' | 'fair' | 'poor';
  fillable: boolean;
  rr_ratio: number;
  pop: number;
  expected_value: number;
  iv_used_pct: number;
  charges: {
    brokerage: number; stt: number; exchange: number;
    sebi: number; stamp: number; gst: number; total: number;
  };
  net_max_profit: number;
  net_max_loss: number;
  net_expected_value: number;
}

interface StrategyDef {
  id: string;
  name: string;
  category: string;
  description: string;
  structure: { role: string; action: string; qty: number; type: string; desc: string }[];
  best_when: string;
  available: boolean;
}

@Component({
  selector: 'app-scanner',
  standalone: true,
  imports: [CommonModule, FormsModule, PayoffGraphComponent],
  templateUrl: './scanner.html',
  styleUrl: './scanner.scss',
})
export class ScannerComponent implements OnInit {
  api = inject(ApiService);
  builder = inject(StrategyBuilderService);
  router = inject(Router);

  strategies = signal<StrategyDef[]>([]);
  selectedStrategy = signal<string>('bwb');
  expandedResultId = signal<string | null>(null);
  graphOpenIds = signal<Set<string>>(new Set());
  showStrategyDetails = signal(true);

  underlyings = signal<any[]>([]);
  underlying = signal('NIFTY');
  expiries = signal<string[]>([]);
  expiry = signal('');

  maxLoss = signal(0);          // ₹ acceptable max loss
  minProfit = signal(1000);     // ₹ min max-profit
  minOi = signal(10000);        // OI threshold per leg
  minVolume = signal(100);      // day volume per leg (skipped on weekends)
  maxSpreadPct = signal(5.0);   // max bid-ask spread % per leg
  maxAtmPct = signal(5.0);      // max strike distance from spot, in % of spot
  minLots = signal(5);          // min lots available across all 3 legs (full L1-L5 depth)
  minPop = signal(0);           // 0-100 min PoP %
  minEv = signal(-999999);      // ₹ min expected value
  autoRelax = signal(false);    // auto-loosen filters in scan-all if no match
  sortBy = signal<string[]>(['pop', 'ev']);

  readonly sortOptions = [
    { value: 'pop', label: 'Highest Probability of Profit' },
    { value: 'ev', label: 'Best Expected Value (PoP × P&L)' },
    { value: 'profit', label: 'Highest Max Profit' },
    { value: 'rr', label: 'Best Risk:Reward Ratio' },
    { value: 'liquidity', label: 'Best Liquidity Score' },
    { value: 'lots', label: 'Most Lots Fillable' },
    { value: 'capital', label: 'Lowest Capital Required' },
    { value: 'loss', label: 'Lowest Max Loss' },
  ];
  showOnlyRiskFree = signal(false);
  showAdvancedFilters = signal(false);

  appliedFilters = signal<any>(null);

  scanMode = signal<'single' | 'all'>('single');
  scanLog = signal<any[]>([]);
  relaxedTo = signal<string>('');

  // Pagination
  page = signal(1);
  perPage = signal(30);
  total = signal(0);
  totalPages = signal(1);

  loading = signal(false);
  scanned = signal(0);
  found = signal(0);
  spot = signal(0);
  atmStrike = signal(0);
  dte = signal(0);
  results = signal<BwbResult[]>([]);
  error = signal('');

  // ── Futures calculator ────────────────────────────────────────────────────
  futuresData    = signal<any>(null);
  futuresLoading = signal(false);
  futuresRate    = signal(6.5);
  futuresDivYield = signal(1.3);

  filtered = computed(() => {
    let all = this.results();
    if (this.showOnlyRiskFree()) all = all.filter(r => r.max_loss === 0);
    return all;
  });

  bestProfit = computed(() => {
    const all = this.filtered();
    return all.length ? Math.max(...all.map(r => r.max_profit)) : 0;
  });

  // ── Batman signals ─────────────────────────────────────────────────────────
  batmanResults = signal<BatmanResult[]>([]);

  batmanFiltered = computed(() => {
    let all = this.batmanResults();
    if (this.showOnlyRiskFree()) all = all.filter(r => r.max_loss === 0);
    return all;
  });

  batmanBestProfit = computed(() => {
    const all = this.batmanFiltered();
    return all.length ? Math.max(...all.map(r => r.max_profit)) : 0;
  });

  // Active (strategy-aware) helpers used in template
  activeFilteredLength = computed(() => {
    const s = this.selectedStrategy();
    if (s === 'batman') return this.batmanFiltered().length;
    if (s === 'iron_condor') return this.icFiltered().length;
    return this.filtered().length;
  });
  activeBestProfit = computed(() => {
    const s = this.selectedStrategy();
    if (s === 'batman') return this.batmanBestProfit();
    if (s === 'iron_condor') return this.icBestProfit();
    return this.bestProfit();
  });

  batmanExpandedId = signal<string | null>(null);
  batmanGraphOpenIds = signal<Set<string>>(new Set());

  // ── Iron Condor signals ────────────────────────────────────────────────────
  icResults = signal<IcResult[]>([]);

  icFiltered = computed(() => {
    let all = this.icResults();
    if (this.showOnlyRiskFree()) all = all.filter(r => r.max_loss === 0);
    return all;
  });

  icBestProfit = computed(() => {
    const all = this.icFiltered();
    return all.length ? Math.max(...all.map(r => r.max_profit)) : 0;
  });

  icExpandedId = signal<string | null>(null);

  ngOnInit() {
    this.api.listStrategies().subscribe(d => this.strategies.set(d.strategies || []));
    this.api.getOptionUnderlyings().subscribe(u => {
      this.underlyings.set(u);
      this.loadExpiries();
    });
  }

  selectStrategy(id: string) {
    if (id === 'futures') {
      this.selectedStrategy.set('futures');
      this.loadFutures();
      return;
    }
    const s = this.strategies().find(x => x.id === id);
    if (!s || !s.available) return;
    this.selectedStrategy.set(id);
    // Clear previous strategy results when switching
    this.results.set([]);
    this.batmanResults.set([]);
    this.icResults.set([]);
    this.scan();
  }

  loadFutures() {
    this.futuresLoading.set(true);
    this.api.getFuturesPrice(this.underlying(), this.futuresRate(), this.futuresDivYield()).subscribe({
      next: (d) => { this.futuresData.set(d); this.futuresLoading.set(false); },
      error: () => this.futuresLoading.set(false),
    });
  }

  futuresFmt(v: number, d = 0) {
    return v?.toLocaleString('en-IN', { minimumFractionDigits: d, maximumFractionDigits: d });
  }

  selectedStrategyDef(): StrategyDef | undefined {
    return this.strategies().find(x => x.id === this.selectedStrategy());
  }

  toggleResultExpanded(key: string) {
    this.expandedResultId.update(cur => cur === key ? null : key);
  }
  resultKey(r: BwbResult): string {
    return r.sym1 + r.sym2 + r.sym3;
  }

  isGraphOpen(key: string): boolean {
    return this.graphOpenIds().has(key);
  }
  toggleGraph(key: string) {
    this.graphOpenIds.update(set => {
      const next = new Set(set);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  }
  graphInputFor(r: BwbResult): any {
    return {
      type: r.type,
      K1: r.K1, K2: r.K2, K3: r.K3,
      P1: r.P1, P2: r.P2, P3: r.P3,
      lot_size: r.lot_size,
      charges: r.charges,
      spot: r._spot ?? this.spot(),
      underlying: r._underlying,
      expiry: r._expiry,
    };
  }

  loadExpiries() {
    this.api.getOptionExpiries(this.underlying()).subscribe(e => {
      this.expiries.set(e);
      if (e.length) { this.expiry.set(e[0]); this.scan(); }
    });
  }

  scan(resetPage: boolean = true) {
    if (this.selectedStrategy() === 'batman') { this.scanBatmanSingle(resetPage); return; }
    if (this.selectedStrategy() === 'iron_condor') { this.scanIcSingle(resetPage); return; }
    if (!this.expiry()) return;
    this.scanMode.set('single');
    this.scanLog.set([]);
    if (resetPage) this.page.set(1);
    this.loading.set(true);
    this.error.set('');
    this.api.scanBwb(this.underlying(), this.expiry(), {
      maxLoss: this.maxLoss(),
      minProfit: this.minProfit(),
      minOi: this.minOi(),
      minVolume: this.minVolume(),
      maxSpreadPct: this.maxSpreadPct(),
      maxAtmPct: this.maxAtmPct(),
      minLots: this.minLots(),
      minPop: this.minPop() / 100,
      minEv: this.minEv(),
      sortBy: this.sortBy().join(','),
      page: this.page(),
      perPage: this.perPage(),
    }).subscribe({
      next: d => {
        this.loading.set(false);
        this.scanned.set(d.scanned);
        this.found.set(d.found);
        this.spot.set(d.spot);
        this.atmStrike.set(d.atm_strike);
        this.dte.set(d.dte);
        this.results.set(d.results || []);
        this.appliedFilters.set(d.filters);
        this.total.set(d.total ?? d.found ?? 0);
        this.totalPages.set(d.total_pages ?? 1);
      },
      error: (e) => {
        this.loading.set(false);
        this.error.set(e?.error?.detail ?? 'Scan failed');
      },
    });
  }

  scanBatmanSingle(resetPage: boolean = true) {
    if (!this.expiry()) return;
    this.scanMode.set('single');
    this.scanLog.set([]);
    if (resetPage) this.page.set(1);
    this.loading.set(true);
    this.error.set('');
    this.api.scanBatman(this.underlying(), this.expiry(), {
      maxLoss: this.maxLoss() === 0 ? 5000 : this.maxLoss(),  // Batman default: allow small bounded risk
      minProfit: this.minProfit(),
      minOi: this.minOi(),
      minVolume: this.minVolume(),
      maxSpreadPct: this.maxSpreadPct(),
      maxAtmPct: Math.max(this.maxAtmPct(), 8.0),  // Batman spans both sides
      minLots: this.minLots(),
      minPop: this.minPop() / 100,
      minEv: this.minEv(),
      sortBy: this.sortBy().join(','),
      page: this.page(),
      perPage: this.perPage(),
    }).subscribe({
      next: d => {
        this.loading.set(false);
        this.scanned.set(d.scanned);
        this.found.set(d.found);
        this.spot.set(d.spot);
        this.atmStrike.set(d.atm_strike);
        this.dte.set(d.dte);
        this.batmanResults.set(d.results || []);
        this.appliedFilters.set(d.filters);
        this.total.set(d.total ?? d.found ?? 0);
        this.totalPages.set(d.total_pages ?? 1);
      },
      error: (e) => {
        this.loading.set(false);
        this.error.set(e?.error?.detail ?? 'Batman scan failed');
      },
    });
  }

  setSortLevel(idx: number, v: string) {
    this.sortBy.update(arr => {
      const next = [...arr];
      next[idx] = v;
      return next;
    });
    this.scan();
  }
  addSortLevel() {
    const used = new Set(this.sortBy());
    const next = this.sortOptions.map(o => o.value).find(v => !used.has(v));
    if (next) {
      this.sortBy.update(arr => [...arr, next]);
      this.scan();
    }
  }
  removeSortLevel(idx: number) {
    if (this.sortBy().length <= 1) return;
    this.sortBy.update(arr => arr.filter((_, i) => i !== idx));
    this.scan();
  }
  moveSortLevel(idx: number, dir: -1 | 1) {
    const newIdx = idx + dir;
    if (newIdx < 0 || newIdx >= this.sortBy().length) return;
    this.sortBy.update(arr => {
      const next = [...arr];
      [next[idx], next[newIdx]] = [next[newIdx], next[idx]];
      return next;
    });
    this.scan();
  }
  sortLabel(v: string): string {
    return this.sortOptions.find(o => o.value === v)?.label ?? v;
  }
  setMinLots(v: number) {
    this.minLots.set(v);
    this.scan();
  }

  scanAll(resetPage: boolean = true) {
    if (this.selectedStrategy() === 'batman') { this.scanAllBatman(resetPage); return; }
    if (this.selectedStrategy() === 'iron_condor') { this.scanAllIc(resetPage); return; }
    this.scanMode.set('all');
    if (resetPage) this.page.set(1);
    this.loading.set(true);
    this.error.set('');
    this.results.set([]);
    this.scanLog.set([]);
    this.relaxedTo.set('');
    this.api.scanBwbAll({
      maxLoss: this.maxLoss(),
      minProfit: this.minProfit(),
      minOi: this.minOi(),
      minVolume: this.minVolume(),
      maxSpreadPct: this.maxSpreadPct(),
      maxAtmPct: this.maxAtmPct(),
      minLots: this.minLots(),
      minPop: this.minPop() / 100,
      minEv: this.minEv(),
      sortBy: this.sortBy().join(','),
      autoRelax: true,                  // always on for the "best trade" button
      expiriesPerUnderlying: 10,        // scan ALL near-term expiries
      page: this.page(),
      perPage: this.perPage(),
    }).subscribe({
      next: d => {
        this.loading.set(false);
        this.scanned.set(d.scanned);
        this.found.set(d.found);
        this.results.set(d.results || []);
        this.scanLog.set(d.scan_log || []);
        this.appliedFilters.set(d.filters);
        this.relaxedTo.set(d.relaxed_to || '');
        this.total.set(d.total ?? d.found ?? 0);
        this.totalPages.set(d.total_pages ?? 1);
        if (d.relaxed_to && d.relaxed_to !== 'user filters') {
          this.showOnlyRiskFree.set(false);
        }
      },
      error: (e) => {
        this.loading.set(false);
        this.error.set(e?.error?.detail ?? 'Scan failed');
      },
    });
  }

  scanAllBatman(resetPage: boolean = true) {
    this.scanMode.set('all');
    if (resetPage) this.page.set(1);
    this.loading.set(true);
    this.error.set('');
    this.batmanResults.set([]);
    this.scanLog.set([]);
    this.relaxedTo.set('');
    this.api.scanBatmanAll({
      maxLoss: this.maxLoss() === 0 ? 5000 : this.maxLoss(),
      minProfit: this.minProfit(),
      minOi: this.minOi(),
      minVolume: this.minVolume(),
      maxSpreadPct: this.maxSpreadPct(),
      maxAtmPct: Math.max(this.maxAtmPct(), 8.0),
      minLots: this.minLots(),
      minPop: this.minPop() / 100,
      minEv: this.minEv(),
      sortBy: this.sortBy().join(','),
      autoRelax: true,
      expiriesPerUnderlying: 10,
      page: this.page(),
      perPage: this.perPage(),
    }).subscribe({
      next: d => {
        this.loading.set(false);
        this.scanned.set(d.scanned);
        this.found.set(d.found);
        this.batmanResults.set(d.results || []);
        this.scanLog.set(d.scan_log || []);
        this.appliedFilters.set(d.filters);
        this.relaxedTo.set(d.relaxed_to || '');
        this.total.set(d.total ?? d.found ?? 0);
        this.totalPages.set(d.total_pages ?? 1);
        if (d.relaxed_to && d.relaxed_to !== 'user filters') {
          this.showOnlyRiskFree.set(false);
        }
      },
      error: (e) => {
        this.loading.set(false);
        this.error.set(e?.error?.detail ?? 'Batman scan failed');
      },
    });
  }

  scanIcSingle(resetPage: boolean = true) {
    if (!this.expiry()) return;
    this.scanMode.set('single');
    this.scanLog.set([]);
    if (resetPage) this.page.set(1);
    this.loading.set(true);
    this.error.set('');
    this.api.scanIronCondor(this.underlying(), this.expiry(), {
      maxLoss: this.maxLoss() === 0 ? 10000 : this.maxLoss(),
      minProfit: this.minProfit(),
      minOi: this.minOi(),
      minVolume: this.minVolume(),
      maxSpreadPct: this.maxSpreadPct(),
      maxAtmPct: this.maxAtmPct(),
      minLots: this.minLots(),
      minPop: this.minPop() / 100,
      minEv: this.minEv(),
      sortBy: this.sortBy().join(','),
      page: this.page(),
      perPage: this.perPage(),
    }).subscribe({
      next: d => {
        this.loading.set(false);
        this.scanned.set(d.scanned);
        this.found.set(d.found);
        this.spot.set(d.spot);
        this.atmStrike.set(d.atm_strike);
        this.dte.set(d.dte);
        this.icResults.set(d.results || []);
        this.appliedFilters.set(d.filters);
        this.total.set(d.total ?? d.found ?? 0);
        this.totalPages.set(d.total_pages ?? 1);
      },
      error: (e) => {
        this.loading.set(false);
        this.error.set(e?.error?.detail ?? 'Iron Condor scan failed');
      },
    });
  }

  scanAllIc(resetPage: boolean = true) {
    this.scanMode.set('all');
    if (resetPage) this.page.set(1);
    this.loading.set(true);
    this.error.set('');
    this.icResults.set([]);
    this.scanLog.set([]);
    this.relaxedTo.set('');
    this.api.scanIronCondorAll({
      maxLoss: this.maxLoss() === 0 ? 10000 : this.maxLoss(),
      minProfit: this.minProfit(),
      minOi: this.minOi(),
      minVolume: this.minVolume(),
      maxSpreadPct: this.maxSpreadPct(),
      maxAtmPct: this.maxAtmPct(),
      minLots: this.minLots(),
      minPop: this.minPop() / 100,
      minEv: this.minEv(),
      sortBy: this.sortBy().join(','),
      autoRelax: true,
      expiriesPerUnderlying: 10,
      page: this.page(),
      perPage: this.perPage(),
    }).subscribe({
      next: d => {
        this.loading.set(false);
        this.scanned.set(d.scanned);
        this.found.set(d.found);
        this.icResults.set(d.results || []);
        this.scanLog.set(d.scan_log || []);
        this.appliedFilters.set(d.filters);
        this.relaxedTo.set(d.relaxed_to || '');
        this.total.set(d.total ?? d.found ?? 0);
        this.totalPages.set(d.total_pages ?? 1);
        if (d.relaxed_to && d.relaxed_to !== 'user filters') {
          this.showOnlyRiskFree.set(false);
        }
      },
      error: (e) => {
        this.loading.set(false);
        this.error.set(e?.error?.detail ?? 'Iron Condor scan failed');
      },
    });
  }

  applyPreset(preset: 'strict' | 'normal' | 'loose') {
    if (preset === 'strict') {
      this.minOi.set(50000); this.minVolume.set(500);
      this.maxSpreadPct.set(2.0); this.maxAtmPct.set(3.0);
      this.minLots.set(10); this.minPop.set(80); this.minEv.set(0);
      this.maxLoss.set(0); this.minProfit.set(1000);
      this.showOnlyRiskFree.set(true);
    } else if (preset === 'normal') {
      this.minOi.set(10000); this.minVolume.set(100);
      this.maxSpreadPct.set(5.0); this.maxAtmPct.set(5.0);
      this.minLots.set(5); this.minPop.set(60); this.minEv.set(-1000);
      this.maxLoss.set(2000); this.minProfit.set(1000);
      this.showOnlyRiskFree.set(false);
    } else {
      this.minOi.set(1000); this.minVolume.set(0);
      this.maxSpreadPct.set(15.0); this.maxAtmPct.set(15.0);
      this.minLots.set(1); this.minPop.set(0); this.minEv.set(-999999);
      this.maxLoss.set(15000); this.minProfit.set(500);
      this.showOnlyRiskFree.set(false);
    }
    this.scan();
  }

  lotsTier(lots: number): { label: string; cls: string } {
    if (lots >= 20) return { label: 'Deep', cls: 'lots-deep' };
    if (lots >= 10) return { label: 'Liquid', cls: 'lots-liquid' };
    if (lots >= 5) return { label: 'OK', cls: 'lots-ok' };
    if (lots >= 1) return { label: 'Thin', cls: 'lots-thin' };
    return { label: 'None', cls: 'lots-none' };
  }

  onUnderlyingChange(v: string) { this.underlying.set(v); this.loadExpiries(); }
  onExpiryChange(v: string) { this.expiry.set(v); this.scan(); }

  applyToBuilder(r: BwbResult) {
    const legs: PendingLeg[] = [
      { symbol: r.sym1, strike: r.K1, type: r.type, transaction_type: 'BUY', qty: 1, premium: r.P1 },
      { symbol: r.sym2, strike: r.K2, type: r.type, transaction_type: 'SELL', qty: 2, premium: r.P2 },
      { symbol: r.sym3, strike: r.K3, type: r.type, transaction_type: 'BUY', qty: 1, premium: r.P3 },
    ];
    const u = r._underlying ?? this.underlying();
    const e = r._expiry ?? this.expiry();
    this.builder.load({
      name: `BWB ${r.type} ${r.K1}/${r.K2}/${r.K3}`,
      underlying: u,
      expiry: e,
      legs,
    });
    this.router.navigate(['/strategy']);
  }

  // ── Mini payoff bar normalization ──────────────────────────────────────────
  payoffSegments(r: BwbResult) {
    const max = Math.max(r.pnl_below, r.pnl_peak, r.pnl_above);
    const min = Math.min(r.pnl_below, r.pnl_peak, r.pnl_above, 0);
    const range = Math.max(max - min, 1);
    return {
      below: { pnl: r.pnl_below, height: ((r.pnl_below - min) / range) * 100 },
      peak: { pnl: r.pnl_peak, height: ((r.pnl_peak - min) / range) * 100 },
      above: { pnl: r.pnl_above, height: ((r.pnl_above - min) / range) * 100 },
    };
  }

  abs(v: number): number { return Math.abs(v); }

  riskBadge(r: BwbResult): { label: string; cls: string } {
    if (r.max_loss === 0) return { label: 'RISK-FREE', cls: 'badge-rf' };
    if (r.rr_ratio >= 5) return { label: 'HIGH R:R', cls: 'badge-high' };
    return { label: 'LOW RISK', cls: 'badge-low' };
  }

  liqLabel(tier: string): string {
    return ({ excellent: 'Excellent', good: 'Good', fair: 'Fair', poor: 'Poor' } as any)[tier] || tier;
  }

  popTier(pop: number): { label: string; cls: string } {
    if (pop >= 0.85) return { label: 'Very High', cls: 'pop-high' };
    if (pop >= 0.65) return { label: 'High', cls: 'pop-good' };
    if (pop >= 0.45) return { label: 'Moderate', cls: 'pop-mid' };
    return { label: 'Low', cls: 'pop-low' };
  }

  fmtPop(p: number): string { return (p * 100).toFixed(1) + '%'; }

  goToPage(p: number) {
    if (p < 1 || p > this.totalPages()) return;
    this.page.set(p);
    if (this.scanMode() === 'all') this.scanAll(false);
    else this.scan(false);
  }
  setPerPage(v: number) {
    this.perPage.set(v);
    this.page.set(1);
    if (this.scanMode() === 'all') this.scanAll(false);
    else this.scan(false);
  }

  // ── Batman helpers ─────────────────────────────────────────────────────────

  batmanResultKey(r: BatmanResult): string {
    return r.sym1 + r.sym2 + r.sym3 + r.sym4 + r.sym5 + r.sym6;
  }

  isBatmanGraphOpen(key: string): boolean { return this.batmanGraphOpenIds().has(key); }

  toggleBatmanGraph(key: string) {
    this.batmanGraphOpenIds.update(set => {
      const next = new Set(set);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  }

  toggleBatmanExpanded(key: string) {
    this.batmanExpandedId.update(cur => cur === key ? null : key);
  }

  batmanGraphInputFor(r: BatmanResult): any {
    return {
      type: 'BATMAN',
      K1: r.K1, K2: r.K2, K3: r.K3,
      P1: r.P1, P2: r.P2, P3: r.P3,
      K4: r.K4, K5: r.K5, K6: r.K6,
      P4: r.P4, P5: r.P5, P6: r.P6,
      lot_size: r.lot_size,
      charges: r.charges,
      spot: r._spot ?? this.spot(),
      underlying: r._underlying,
      expiry: r._expiry,
    };
  }

  batmanPayoffZones(r: BatmanResult) {
    const vals = [r.pnl_far_left, r.pnl_left_peak, r.pnl_middle, r.pnl_right_peak, r.pnl_far_right];
    const max = Math.max(...vals);
    const min = Math.min(...vals, 0);
    const range = Math.max(max - min, 1);
    return [
      { label: `<${r.K1}`, pnl: r.pnl_far_left, height: ((r.pnl_far_left - min) / range) * 100 },
      { label: `@${r.K2} ★`, pnl: r.pnl_left_peak, height: ((r.pnl_left_peak - min) / range) * 100 },
      { label: `${r.K3}–${r.K4}`, pnl: r.pnl_middle, height: ((r.pnl_middle - min) / range) * 100 },
      { label: `@${r.K5} ★`, pnl: r.pnl_right_peak, height: ((r.pnl_right_peak - min) / range) * 100 },
      { label: `>${r.K6}`, pnl: r.pnl_far_right, height: ((r.pnl_far_right - min) / range) * 100 },
    ];
  }

  batmanRiskBadge(r: BatmanResult): { label: string; cls: string } {
    if (r.max_loss === 0) return { label: 'RISK-FREE', cls: 'badge-rf' };
    if (r.rr_ratio >= 5) return { label: 'HIGH R:R', cls: 'badge-high' };
    return { label: 'LOW RISK', cls: 'badge-low' };
  }

  // ── Iron Condor helpers ────────────────────────────────────────────────────

  icResultKey(r: IcResult): string {
    return r.sym1 + r.sym2 + r.sym3 + r.sym4;
  }

  toggleIcExpanded(key: string) {
    this.icExpandedId.update(cur => cur === key ? null : key);
  }

  icGraphInputFor(r: IcResult): any {
    return {
      type: 'IC',
      K1: r.K1, K2: r.K2, K3: r.K3, K4: r.K4,
      P1: r.P1, P2: r.P2, P3: r.P3, P4: r.P4,
      lot_size: r.lot_size,
      charges: r.charges,
      spot: r._spot ?? this.spot(),
      underlying: r._underlying,
      expiry: r._expiry,
    };
  }

  icPayoffZones(r: IcResult) {
    const vals = [r.pnl_far_left, r.pnl_profit_zone, r.pnl_far_right];
    const max = Math.max(...vals);
    const min = Math.min(...vals, 0);
    const range = Math.max(max - min, 1);
    return [
      { label: `<${r.K2}`, pnl: r.pnl_far_left,    height: ((r.pnl_far_left    - min) / range) * 100 },
      { label: `${r.K2}–${r.K3}`, pnl: r.pnl_profit_zone, height: ((r.pnl_profit_zone - min) / range) * 100 },
      { label: `>${r.K3}`, pnl: r.pnl_far_right,   height: ((r.pnl_far_right   - min) / range) * 100 },
    ];
  }

  icRiskBadge(r: IcResult): { label: string; cls: string } {
    if (r.max_loss === 0) return { label: 'RISK-FREE', cls: 'badge-rf' };
    if (r.rr_ratio >= 3) return { label: 'HIGH R:R', cls: 'badge-high' };
    return { label: 'LOW RISK', cls: 'badge-low' };
  }

  applyIcToBuilder(r: IcResult) {
    const legs = [
      { symbol: r.sym1, strike: r.K1, type: 'PE', transaction_type: 'BUY',  qty: 1, premium: r.P1 },
      { symbol: r.sym2, strike: r.K2, type: 'PE', transaction_type: 'SELL', qty: 1, premium: r.P2 },
      { symbol: r.sym3, strike: r.K3, type: 'CE', transaction_type: 'SELL', qty: 1, premium: r.P3 },
      { symbol: r.sym4, strike: r.K4, type: 'CE', transaction_type: 'BUY',  qty: 1, premium: r.P4 },
    ];
    const u = r._underlying ?? this.underlying();
    const e = r._expiry ?? this.expiry();
    this.builder.load({ name: `Iron Condor ${r.K2}/${r.K3}`, underlying: u, expiry: e, legs: legs as any });
    this.router.navigate(['/strategy']);
  }

  applyBatmanToBuilder(r: BatmanResult) {
    const legs = [
      { symbol: r.sym1, strike: r.K1, type: 'PE', transaction_type: 'BUY', qty: 1, premium: r.P1 },
      { symbol: r.sym2, strike: r.K2, type: 'PE', transaction_type: 'SELL', qty: 2, premium: r.P2 },
      { symbol: r.sym3, strike: r.K3, type: 'PE', transaction_type: 'BUY', qty: 1, premium: r.P3 },
      { symbol: r.sym4, strike: r.K4, type: 'CE', transaction_type: 'BUY', qty: 1, premium: r.P4 },
      { symbol: r.sym5, strike: r.K5, type: 'CE', transaction_type: 'SELL', qty: 2, premium: r.P5 },
      { symbol: r.sym6, strike: r.K6, type: 'CE', transaction_type: 'BUY', qty: 1, premium: r.P6 },
    ];
    const u = r._underlying ?? this.underlying();
    const e = r._expiry ?? this.expiry();
    this.builder.load({ name: `Batman ${r.K2}/${r.K5}`, underlying: u, expiry: e, legs: legs as any });
    this.router.navigate(['/strategy']);
  }
}
