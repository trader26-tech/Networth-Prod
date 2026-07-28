import {
  Component, OnInit, OnDestroy, AfterViewInit,
  inject, signal, computed, effect,
  ElementRef, ViewChild
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ApiService } from '../../services/api.service';
import { StrategyBuilderService } from '../../services/strategy-builder.service';
import {
  analyzeStrategy, StrategyAnalysis, Leg
} from '../../utils/options-math';


export interface StrategyLeg extends Leg {
  id: number;
  symbol: string;
  expiry: string;
}

@Component({
  selector: 'app-strategy',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './strategy.html',
  styleUrl: './strategy.scss',
})
export class StrategyComponent implements OnInit, AfterViewInit, OnDestroy {
  @ViewChild('payoffChart') payoffRef!: ElementRef;
  @ViewChild('thetaChart') thetaRef!: ElementRef;

  api = inject(ApiService);
  builder = inject(StrategyBuilderService);
  pendingName = signal<string | null>(null);

  // Chain state
  underlyings = signal<any[]>([]);
  selectedUnderlying = signal('NIFTY');
  expiries = signal<string[]>([]);
  selectedExpiry = signal('');
  chainData = signal<any>(null);
  chainLoading = signal(false);

  // Builder state
  legs = signal<StrategyLeg[]>([]);
  nextId = 1;
  activeTab = signal<'legs' | 'analysis' | 'charts'>('legs');

  // Modal state
  showModal = signal(false);
  slInput = signal('');
  targetInput = signal('');
  useReal = signal(false);
  placing = signal(false);
  placeError = signal('');

  // Charts
  private payoffChart: any = null;
  private thetaChart: any = null;

  // Derived IV from selected legs (average of chain IVs)
  iv = computed(() => {
    const legs = this.legs();
    const chain = this.chainData();
    if (!legs.length || !chain) return 0.15;
    const ivs = legs
      .map(l => {
        const row = chain.chain.find((r: any) => r.strike === l.strike);
        if (!row) return null;
        const v = l.type === 'CE' ? row.ce.iv : row.pe.iv;
        return v != null ? v / 100 : null;
      })
      .filter((v): v is number => v !== null);
    return ivs.length ? ivs.reduce((a, b) => a + b, 0) / ivs.length : 0.15;
  });

  maxOi = computed(() => {
    const c = this.chainData();
    if (!c?.chain?.length) return 1;
    return Math.max(...c.chain.flatMap((r: any) => [r.ce.oi, r.pe.oi]), 1);
  });

  pcr = computed(() => {
    const c = this.chainData();
    if (!c?.chain?.length) return null;
    const ceOi = c.chain.reduce((s: number, r: any) => s + r.ce.oi, 0);
    const peOi = c.chain.reduce((s: number, r: any) => s + r.pe.oi, 0);
    return ceOi > 0 ? +(peOi / ceOi).toFixed(2) : null;
  });

  totalCeOi = computed(() => {
    const c = this.chainData();
    return c?.chain?.reduce((s: number, r: any) => s + r.ce.oi, 0) ?? 0;
  });

  totalPeOi = computed(() => {
    const c = this.chainData();
    return c?.chain?.reduce((s: number, r: any) => s + r.pe.oi, 0) ?? 0;
  });

  // Live analysis — updates instantly on leg change
  analysis = computed((): StrategyAnalysis | null => {
    const legs = this.legs();
    const chain = this.chainData();
    if (!legs.length || !chain) return null;
    return analyzeStrategy(legs, chain.spot, chain.dte, this.iv());
  });

  readonly templates = [
    { name: 'Straddle ↕', fn: () => this.applyTemplate('straddle') },
    { name: 'Strangle ↔', fn: () => this.applyTemplate('strangle') },
    { name: 'Iron Condor', fn: () => this.applyTemplate('condor') },
    { name: 'Bull Call ↑', fn: () => this.applyTemplate('bull_call') },
    { name: 'Bear Put ↓', fn: () => this.applyTemplate('bear_put') },
    { name: 'Long Call', fn: () => this.applyTemplate('long_call') },
    { name: 'Long Put', fn: () => this.applyTemplate('long_put') },
  ];

  constructor() {
    effect(() => {
      const a = this.analysis();
      const tab = this.activeTab();
      if (a && tab === 'charts') {
        this._scheduleChartDraw(a);
      }
    });
  }

  ngOnInit() {
    const pending = this.builder.consume();
    if (pending) {
      this.selectedUnderlying.set(pending.underlying);
      this.selectedExpiry.set(pending.expiry);
      this.pendingName.set(pending.name);
    }
    this.api.getOptionUnderlyings().subscribe(u => {
      this.underlyings.set(u);
      if (pending) {
        this.api.getOptionExpiries(pending.underlying).subscribe(e => {
          this.expiries.set(e);
          this.api.getOptionChain(pending.underlying, pending.expiry).subscribe(d => {
            this.chainData.set(d);
            this.legs.set(pending.legs.map(l => ({
              id: this.nextId++,
              symbol: l.symbol,
              underlying: pending.underlying,
              expiry: pending.expiry,
              strike: l.strike,
              type: l.type,
              transaction_type: l.transaction_type,
              qty: l.qty,
              lot_size: d.lot_size,
              premium: l.premium,
            })));
            this.activeTab.set('analysis');
          });
        });
      } else {
        this.loadExpiries();
      }
    });
  }

  ngAfterViewInit() {}

  ngOnDestroy() {
    this.payoffChart?.remove();
    this.thetaChart?.remove();
  }

  // ── Chain loading ──────────────────────────────────────────────────────────

  loadExpiries() {
    this.api.getOptionExpiries(this.selectedUnderlying()).subscribe(e => {
      this.expiries.set(e);
      if (e.length > 0) { this.selectedExpiry.set(e[0]); this.loadChain(); }
    });
  }

  loadChain() {
    if (!this.selectedExpiry()) return;
    this.chainLoading.set(true);
    this.api.getOptionChain(this.selectedUnderlying(), this.selectedExpiry()).subscribe({
      next: d => { this.chainData.set(d); this.chainLoading.set(false); },
      error: () => this.chainLoading.set(false),
    });
  }

  onUnderlyingChange(v: string) { this.selectedUnderlying.set(v); this.loadExpiries(); }
  onExpiryChange(v: string) { this.selectedExpiry.set(v); this.loadChain(); }

  // ── Leg management ─────────────────────────────────────────────────────────

  addLeg(strike: number, type: 'CE' | 'PE', txn: 'BUY' | 'SELL') {
    const chain = this.chainData();
    if (!chain) return;
    const row = chain.chain.find((r: any) => r.strike === strike);
    if (!row) return;
    const opt = type === 'CE' ? row.ce : row.pe;
    this.legs.update(legs => [...legs, {
      id: this.nextId++,
      underlying: this.selectedUnderlying(),
      expiry: this.selectedExpiry(),
      symbol: opt.symbol,
      strike, type,
      transaction_type: txn,
      qty: 1,
      lot_size: chain.lot_size,
      premium: opt.price,
    }]);
    if (this.activeTab() === 'charts') this.activeTab.set('legs');
  }

  removeLeg(id: number) { this.legs.update(l => l.filter(x => x.id !== id)); }
  clearLegs() { this.legs.set([]); this.nextId = 1; }

  setLegQty(id: number, v: string) {
    const n = Math.max(1, parseInt(v) || 1);
    this.legs.update(l => l.map(x => x.id === id ? { ...x, qty: n } : x));
  }

  strategyName(): string {
    if (this.pendingName()) return this.pendingName()!;
    const t = this.templates.find(t => t.fn === this._lastTemplate);
    return t?.name.replace(/ [↕↔↑↓]/g, '') ?? 'Custom Strategy';
  }

  private _lastTemplate: any = null;

  // ── Templates ──────────────────────────────────────────────────────────────

  applyTemplate(name: string) {
    const chain = this.chainData();
    if (!chain) return;
    this.legs.set([]);
    const c = chain.chain;
    const atmIdx = c.findIndex((r: any) => r.atm);
    if (atmIdx < 0) return;
    const atm = c[atmIdx];
    const up1 = c[atmIdx + 1];
    const up2 = c[atmIdx + 2];
    const dn1 = c[atmIdx - 1];
    const dn2 = c[atmIdx - 2];

    const add = (row: any, type: 'CE' | 'PE', txn: 'BUY' | 'SELL') => {
      if (!row) return;
      const opt = type === 'CE' ? row.ce : row.pe;
      this.legs.update(l => [...l, {
        id: this.nextId++, symbol: opt.symbol,
        underlying: this.selectedUnderlying(), expiry: this.selectedExpiry(),
        strike: row.strike, type, transaction_type: txn,
        qty: 1, lot_size: chain.lot_size, premium: opt.price,
      }]);
    };

    switch (name) {
      case 'straddle':    add(atm, 'CE', 'SELL'); add(atm, 'PE', 'SELL'); break;
      case 'strangle':    add(up1, 'CE', 'SELL'); add(dn1, 'PE', 'SELL'); break;
      case 'condor':
        add(up1,'CE','SELL'); add(up2,'CE','BUY');
        add(dn1,'PE','SELL'); add(dn2,'PE','BUY'); break;
      case 'bull_call':   add(atm,'CE','BUY');  add(up1,'CE','SELL'); break;
      case 'bear_put':    add(atm,'PE','BUY');  add(dn1,'PE','SELL'); break;
      case 'long_call':   add(atm,'CE','BUY'); break;
      case 'long_put':    add(atm,'PE','BUY'); break;
    }
  }

  // ── Tabs ───────────────────────────────────────────────────────────────────

  setTab(t: 'legs' | 'analysis' | 'charts') {
    this.activeTab.set(t);
    if (t === 'charts') {
      const a = this.analysis();
      if (a) this._scheduleChartDraw(a);
    }
  }

  private _scheduleChartDraw(a: StrategyAnalysis, attempt = 0) {
    setTimeout(() => {
      if (this.payoffRef?.nativeElement) {
        this.drawCharts(a);
      } else if (attempt < 5) {
        this._scheduleChartDraw(a, attempt + 1);
      }
    }, 80 + attempt * 50);
  }

  // ── Charts ─────────────────────────────────────────────────────────────────

  drawCharts(a: StrategyAnalysis) {
    const lc = (window as any).LightweightCharts;
    if (!lc) return;
    this.drawPayoff(lc, a);
    this.drawTheta(lc, a);
  }

  private drawPayoff(lc: any, a: StrategyAnalysis) {
    if (!this.payoffRef?.nativeElement) return;
    if (this.payoffChart) { this.payoffChart.remove(); this.payoffChart = null; }
    this.payoffChart = lc.createChart(this.payoffRef.nativeElement, {
      autoSize: true,
      layout: { background: { color: '#1a1d24' }, textColor: '#8b8fa8' },
      grid: { vertLines: { color: '#2a2d3a' }, horzLines: { color: '#2a2d3a' } },
      rightPriceScale: { borderColor: '#2a2d3a' },
      timeScale: { visible: false },
    });

    const LS = lc.LineSeries;

    // Full P&L line
    const full = LS
      ? this.payoffChart.addSeries(LS, { color: '#387ed1', lineWidth: 2, priceLineVisible: false })
      : this.payoffChart.addLineSeries({ color: '#387ed1', lineWidth: 2 });
    full.setData(a.payoffCurve.map((p, i) => ({ time: (i + 1) as any, value: p.pnl })));

    // Zero baseline
    const zero = LS
      ? this.payoffChart.addSeries(LS, { color: 'rgba(255,255,255,0.15)', lineWidth: 1, lineStyle: 2 })
      : this.payoffChart.addLineSeries({ color: 'rgba(255,255,255,0.15)', lineWidth: 1 });
    zero.setData(a.payoffCurve.map((_, i) => ({ time: (i + 1) as any, value: 0 })));

    this.payoffChart.timeScale().fitContent();
  }

  private drawTheta(lc: any, a: StrategyAnalysis) {
    if (!this.thetaRef?.nativeElement) return;
    if (this.thetaChart) { this.thetaChart.remove(); this.thetaChart = null; }
    this.thetaChart = lc.createChart(this.thetaRef.nativeElement, {
      autoSize: true,
      layout: { background: { color: '#1a1d24' }, textColor: '#8b8fa8' },
      grid: { vertLines: { color: '#2a2d3a' }, horzLines: { color: '#2a2d3a' } },
      rightPriceScale: { borderColor: '#2a2d3a' },
      timeScale: { visible: false },
    });

    const LS = lc.LineSeries;
    const series = LS
      ? this.thetaChart.addSeries(LS, { color: '#f59e0b', lineWidth: 2, priceLineVisible: false })
      : this.thetaChart.addLineSeries({ color: '#f59e0b', lineWidth: 2 });

    // Reverse so x=0 is entry (most DTE), x=last is expiry (0 DTE)
    const data = [...a.thetaCurve].reverse();
    series.setData(data.map((p, i) => ({ time: (i + 1) as any, value: p.pnl })));

    const zero = LS
      ? this.thetaChart.addSeries(LS, { color: 'rgba(255,255,255,0.15)', lineWidth: 1, lineStyle: 2 })
      : this.thetaChart.addLineSeries({ color: 'rgba(255,255,255,0.15)', lineWidth: 1 });
    zero.setData(data.map((_, i) => ({ time: (i + 1) as any, value: 0 })));

    this.thetaChart.timeScale().fitContent();
  }

  // ── Execute modal ──────────────────────────────────────────────────────────

  openModal() {
    const a = this.analysis();
    if (a?.exit) {
      this.targetInput.set(String(Math.abs(a.exit.targetAbs)));
      if (a.exit.strategyType === 'theta_seller') {
        this.slInput.set(String(Math.round(a.netCredit * -1)));
      }
    }
    this.placeError.set('');
    this.showModal.set(true);
  }

  closeModal() { this.showModal.set(false); }

  setUseReal(v: boolean) { this.useReal.set(v); }

  confirmExecute() {
    const legs = this.legs();
    const chain = this.chainData();
    if (!legs.length || !chain) return;

    const sl = this.slInput() ? parseFloat(this.slInput()) : null;
    const target = this.targetInput() ? parseFloat(this.targetInput()) : null;

    // Enforce sl as negative
    const slAmount = sl != null ? (sl > 0 ? -sl : sl) : null;
    const targetAmount = target != null ? Math.abs(target) : null;

    this.placing.set(true);
    this.placeError.set('');

    this.api.executeStrategy({
      strategy_name: this.strategyName(),
      underlying: this.selectedUnderlying(),
      expiry: this.selectedExpiry(),
      spot_at_entry: chain.spot,
      legs: legs.map(l => ({
        symbol: l.symbol,
        type: l.type,
        strike: l.strike,
        transaction_type: l.transaction_type,
        qty: l.qty,
        lot_size: l.lot_size,
        premium: l.premium,
      })),
      sl_amount: slAmount,
      target_amount: targetAmount,
      use_real: this.useReal(),
    }).subscribe({
      next: () => {
        this.placing.set(false);
        this.showModal.set(false);
        this.clearLegs();
        this.activeTab.set('legs');
      },
      error: (e: any) => {
        this.placing.set(false);
        this.placeError.set(e?.error?.detail ?? 'Execution failed');
      },
    });
  }

  // ── Helpers ────────────────────────────────────────────────────────────────

  netCredit(): number {
    return this.legs().reduce((s, l) =>
      s + (l.transaction_type === 'SELL' ? 1 : -1) * l.premium * l.qty * l.lot_size, 0);
  }

  fmtPop(v: number): string { return (v * 100).toFixed(1) + '%'; }
  fmtRr(v: number): string {
    if (!isFinite(v)) return '∞';
    return v.toFixed(2) + ':1';
  }
  abs(v: number): number { return Math.abs(v); }
}
