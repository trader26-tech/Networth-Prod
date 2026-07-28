import {
  Component, Input, ElementRef, ViewChild, AfterViewInit, OnChanges, OnDestroy, signal
} from '@angular/core';
import { CommonModule } from '@angular/common';

export interface PayoffInput {
  type: 'CE' | 'PE' | 'BATMAN' | 'IC';
  K1: number; K2: number; K3: number;
  P1: number; P2: number; P3: number;
  // Batman-only (put BWB + call BWB)
  K4?: number; K5?: number; K6?: number;
  P4?: number; P5?: number; P6?: number;
  lot_size: number;
  charges: { total: number };
  spot: number;
  underlying?: string;
  expiry?: string;
}

@Component({
  selector: 'app-payoff-graph',
  standalone: true,
  imports: [CommonModule],
  template: `
    <div class="payoff-graph">
      <div class="pg-header">
        <div class="pg-title">Payoff at Expiry</div>
        <div class="pg-legend">
          <span class="lg-item">
            <span class="lg-line gross"></span> Gross P&L
          </span>
          <span class="lg-item">
            <span class="lg-line net"></span> After charges
          </span>
          <span class="lg-item">
            <span class="lg-line spot"></span> Current spot
          </span>
        </div>
      </div>
      <div class="pg-canvas-wrap" (mousemove)="onMove($event)" (mouseleave)="hover.set(null)">
        <canvas #canvas></canvas>
        @if (hover()) {
          <div class="pg-tooltip"
               [style.left.px]="hover()!.x + 12"
               [style.top.px]="hover()!.y - 18">
            <div class="tt-spot">Spot ₹{{ hover()!.spot | number:'1.2-2' }}</div>
            <div class="tt-pnl gross" [class.positive]="hover()!.gross >= 0" [class.negative]="hover()!.gross < 0">
              gross {{ hover()!.gross >= 0 ? '+' : '' }}₹{{ hover()!.gross | number:'1.0-0' }}
            </div>
            <div class="tt-pnl net" [class.positive]="hover()!.net >= 0" [class.negative]="hover()!.net < 0">
              net &nbsp;&nbsp;{{ hover()!.net >= 0 ? '+' : '' }}₹{{ hover()!.net | number:'1.0-0' }}
            </div>
          </div>
        }
      </div>
    </div>
  `,
  styles: [`
    .payoff-graph {
      background: var(--bg-primary);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 12px 14px;
      display: flex;
      flex-direction: column;
      gap: 8px;
    }
    .pg-header {
      display: flex; justify-content: space-between; align-items: center;
      flex-wrap: wrap; gap: 8px;
    }
    .pg-title {
      font-size: 11px; font-weight: 800; text-transform: uppercase;
      letter-spacing: 0.5px; color: var(--text-muted);
    }
    .pg-legend { display: flex; gap: 14px; flex-wrap: wrap; font-size: 10px; color: var(--text-muted); }
    .lg-item { display: inline-flex; align-items: center; gap: 5px; }
    .lg-line { width: 16px; height: 2px; display: inline-block; border-radius: 1px; }
    .lg-line.gross { background: #387ed1; }
    .lg-line.net { background: #fbbf24; border-bottom: 1px dashed #fbbf24; height: 0; }
    .lg-line.spot { background: #f97316; height: 12px; width: 1.5px; }
    .pg-canvas-wrap {
      position: relative; height: 280px; width: 100%;
    }
    .pg-canvas-wrap canvas { width: 100%; height: 100%; display: block; cursor: crosshair; }
    .pg-tooltip {
      position: absolute; pointer-events: none;
      background: #0d0e11; border: 1px solid var(--border);
      border-radius: 6px; padding: 6px 10px;
      box-shadow: 0 4px 12px rgba(0,0,0,0.5);
      font-size: 11px; line-height: 1.5; min-width: 130px;
      z-index: 10;
    }
    .tt-spot { color: var(--text-secondary); font-variant-numeric: tabular-nums; margin-bottom: 2px; padding-bottom: 2px; border-bottom: 1px solid var(--border); }
    .tt-pnl { font-weight: 700; font-variant-numeric: tabular-nums; font-family: monospace; font-size: 10.5px; }
    .tt-pnl.positive { color: #26a69a; }
    .tt-pnl.negative { color: #ef5350; }
  `]
})
export class PayoffGraphComponent implements AfterViewInit, OnChanges, OnDestroy {
  @ViewChild('canvas') canvasRef!: ElementRef<HTMLCanvasElement>;
  @Input() data!: PayoffInput;

  hover = signal<{ x: number; y: number; spot: number; gross: number; net: number } | null>(null);

  private resizeObs?: ResizeObserver;
  private layout = {
    padTop: 18, padRight: 14, padBottom: 30, padLeft: 64,
    width: 0, height: 0,
    spotMin: 0, spotMax: 0, spotRange: 0,
    pnlMin: 0, pnlMax: 0, pnlRange: 0,
  };

  ngAfterViewInit() {
    this.resizeObs = new ResizeObserver(() => this.draw());
    if (this.canvasRef?.nativeElement) {
      this.resizeObs.observe(this.canvasRef.nativeElement);
    }
    this.draw();
  }

  ngOnChanges() {
    setTimeout(() => this.draw(), 0);
  }

  ngOnDestroy() {
    this.resizeObs?.disconnect();
  }

  // ── P&L formula at any spot ────────────────────────────────────────────────
  private pnlAt(S: number): number {
    const d = this.data;
    if (!d) return 0;
    if (d.type === 'BATMAN') {
      const putPnl  = Math.max(d.K1 - S, 0) - 2 * Math.max(d.K2 - S, 0) + Math.max(d.K3 - S, 0)
                    + (-d.P1 + 2 * d.P2 - d.P3);
      const callPnl = Math.max(S - d.K4!, 0) - 2 * Math.max(S - d.K5!, 0) + Math.max(S - d.K6!, 0)
                    + (-d.P4! + 2 * d.P5! - d.P6!);
      return (putPnl + callPnl) * d.lot_size;
    }
    if (d.type === 'IC') {
      const nc = -d.P1 + d.P2 + d.P3 - d.P4!;
      return (Math.max(d.K1 - S, 0) - Math.max(d.K2 - S, 0)
            - Math.max(S - d.K3, 0) + Math.max(S - d.K4!, 0)
            + nc) * d.lot_size;
    }
    const netCredit = -d.P1 + 2 * d.P2 - d.P3;
    if (d.type === 'CE') {
      return (Math.max(S - d.K1, 0) - 2 * Math.max(S - d.K2, 0) + Math.max(S - d.K3, 0)
              + netCredit) * d.lot_size;
    }
    return (Math.max(d.K1 - S, 0) - 2 * Math.max(d.K2 - S, 0) + Math.max(d.K3 - S, 0)
            + netCredit) * d.lot_size;
  }

  // Spot → canvas X
  private xOf(S: number): number {
    const L = this.layout;
    return L.padLeft + ((S - L.spotMin) / L.spotRange) * (L.width - L.padLeft - L.padRight);
  }
  // P&L → canvas Y (flipped: high pnl = low y)
  private yOf(P: number): number {
    const L = this.layout;
    return L.padTop + (1 - (P - L.pnlMin) / L.pnlRange) * (L.height - L.padTop - L.padBottom);
  }

  // ── Main draw ──────────────────────────────────────────────────────────────
  private draw() {
    const cv = this.canvasRef?.nativeElement;
    if (!cv || !this.data) return;
    const ctx = cv.getContext('2d');
    if (!ctx) return;

    const dpr = window.devicePixelRatio || 1;
    const W = cv.clientWidth, H = cv.clientHeight;
    if (W === 0 || H === 0) return;
    cv.width = W * dpr; cv.height = H * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);

    const d = this.data;
    const charges = d.charges?.total ?? 0;

    // Domain: spot ±10% (or wider to include all strikes)
    const allK = d.type === 'BATMAN'
      ? [d.K1, d.K2, d.K3, d.K4!, d.K5!, d.K6!, d.spot]
      : d.type === 'IC'
      ? [d.K1, d.K2, d.K3, d.K4!, d.spot]
      : [d.K1, d.K2, d.K3, d.spot];
    const minK = Math.min(...allK);
    const maxK = Math.max(...allK);
    const span = Math.max(maxK - minK, d.spot * 0.05);
    const spotMin = Math.min(d.spot * 0.92, minK - span * 0.15);
    const spotMax = Math.max(d.spot * 1.08, maxK + span * 0.15);

    // Sample P&L curve
    const N = 200;
    const xs: number[] = [], gross: number[] = [], net: number[] = [];
    for (let i = 0; i <= N; i++) {
      const S = spotMin + (i / N) * (spotMax - spotMin);
      xs.push(S);
      const g = this.pnlAt(S);
      gross.push(g);
      net.push(g - charges);
    }

    const pnlMin = Math.min(...net) * 1.1;
    const pnlMax = Math.max(...gross) * 1.1;

    this.layout = {
      padTop: 18, padRight: 14, padBottom: 30, padLeft: 64,
      width: W, height: H,
      spotMin, spotMax, spotRange: spotMax - spotMin,
      pnlMin, pnlMax, pnlRange: Math.max(pnlMax - pnlMin, 1),
    };

    const L = this.layout;
    const plotL = L.padLeft, plotR = W - L.padRight;
    const plotT = L.padTop,  plotB = H - L.padBottom;

    // Background
    ctx.fillStyle = '#0d0e11';
    ctx.fillRect(plotL, plotT, plotR - plotL, plotB - plotT);

    // ── Y-axis labels & gridlines ────────────────────────────────────────────
    ctx.font = '10px -apple-system, sans-serif';
    ctx.textAlign = 'right';
    ctx.textBaseline = 'middle';
    ctx.strokeStyle = 'rgba(255,255,255,0.04)';
    ctx.lineWidth = 1;
    const yTicks = this.niceTicks(pnlMin, pnlMax, 5);
    for (const t of yTicks) {
      const y = this.yOf(t);
      ctx.beginPath();
      ctx.moveTo(plotL, y); ctx.lineTo(plotR, y);
      ctx.stroke();
      ctx.fillStyle = '#5a5e72';
      ctx.fillText(this.fmtPnl(t), plotL - 6, y);
    }

    // Zero line (bold)
    const yZero = this.yOf(0);
    ctx.strokeStyle = 'rgba(255,255,255,0.18)';
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(plotL, yZero); ctx.lineTo(plotR, yZero);
    ctx.stroke();

    // ── X-axis: strike markers and a few spot ticks ──────────────────────────
    ctx.strokeStyle = 'rgba(255,255,255,0.06)';
    ctx.lineWidth = 1;
    ctx.fillStyle = '#5a5e72';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'top';
    const xTicks = this.niceTicks(spotMin, spotMax, 6);
    for (const t of xTicks) {
      const x = this.xOf(t);
      ctx.beginPath();
      ctx.setLineDash([2, 4]);
      ctx.moveTo(x, plotT); ctx.lineTo(x, plotB);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = '#5a5e72';
      ctx.fillText(Math.round(t).toString(), x, plotB + 6);
    }

    // ── Profit zone (green fill) and loss zone (red fill) for gross ─────────
    const buildPath = (ys: number[], clipBelow: boolean) => {
      ctx.beginPath();
      ctx.moveTo(this.xOf(xs[0]), yZero);
      for (let i = 0; i <= N; i++) {
        const yC = this.yOf(ys[i]);
        // For profit fill, only path the part above zero
        const yUse = clipBelow ? Math.min(yC, yZero) : Math.max(yC, yZero);
        ctx.lineTo(this.xOf(xs[i]), yUse);
      }
      ctx.lineTo(this.xOf(xs[N]), yZero);
      ctx.closePath();
    };

    // Profit zone (above zero)
    buildPath(gross, true);
    ctx.fillStyle = 'rgba(38,166,154,0.20)';
    ctx.fill();

    // Loss zone (below zero)
    buildPath(gross, false);
    ctx.fillStyle = 'rgba(239,83,80,0.18)';
    ctx.fill();

    // ── Strike vertical markers ──────────────────────────────────────────────
    const strikes = d.type === 'BATMAN' ? [
      { K: d.K1,  label: 'K1', sub: '+1 PE' },
      { K: d.K2,  label: 'K2', sub: '−2 PE' },
      { K: d.K3,  label: 'K3', sub: '+1 PE' },
      { K: d.K4!, label: 'K4', sub: '+1 CE' },
      { K: d.K5!, label: 'K5', sub: '−2 CE' },
      { K: d.K6!, label: 'K6', sub: '+1 CE' },
    ] : d.type === 'IC' ? [
      { K: d.K1,  label: 'K1', sub: '+1 PE' },
      { K: d.K2,  label: 'K2', sub: '−1 PE' },
      { K: d.K3,  label: 'K3', sub: '−1 CE' },
      { K: d.K4!, label: 'K4', sub: '+1 CE' },
    ] : [
      { K: d.K1, label: 'K1', sub: '+1' },
      { K: d.K2, label: 'K2', sub: '−2' },
      { K: d.K3, label: 'K3', sub: '+1' },
    ];
    ctx.strokeStyle = 'rgba(167,139,250,0.55)';
    ctx.lineWidth = 1;
    ctx.setLineDash([4, 3]);
    ctx.font = 'bold 9px -apple-system, sans-serif';
    for (const s of strikes) {
      const x = this.xOf(s.K);
      ctx.beginPath();
      ctx.moveTo(x, plotT); ctx.lineTo(x, plotB);
      ctx.stroke();
      ctx.fillStyle = '#a78bfa';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'top';
      ctx.fillText(s.label + ' ' + s.K, x, plotT + 2);
      ctx.fillStyle = '#5a5e72';
      ctx.font = '9px -apple-system, sans-serif';
      ctx.fillText(s.sub, x, plotT + 14);
      ctx.font = 'bold 9px -apple-system, sans-serif';
    }
    ctx.setLineDash([]);

    // ── Current spot vertical line (orange) ──────────────────────────────────
    const xSpot = this.xOf(d.spot);
    ctx.strokeStyle = '#f97316';
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(xSpot, plotT); ctx.lineTo(xSpot, plotB);
    ctx.stroke();
    // Spot label at top
    ctx.fillStyle = '#f97316';
    ctx.font = 'bold 10px -apple-system, sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'top';
    ctx.fillText('SPOT ' + d.spot.toFixed(0), xSpot, plotT - 14);

    // ── Net P&L curve (dashed, behind gross) ─────────────────────────────────
    ctx.strokeStyle = '#fbbf24';
    ctx.lineWidth = 1.5;
    ctx.setLineDash([5, 4]);
    ctx.beginPath();
    for (let i = 0; i <= N; i++) {
      const x = this.xOf(xs[i]), y = this.yOf(net[i]);
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    }
    ctx.stroke();
    ctx.setLineDash([]);

    // ── Gross P&L curve (solid blue, primary) ────────────────────────────────
    ctx.strokeStyle = '#387ed1';
    ctx.lineWidth = 2.5;
    ctx.beginPath();
    for (let i = 0; i <= N; i++) {
      const x = this.xOf(xs[i]), y = this.yOf(gross[i]);
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    }
    ctx.stroke();

    // ── Breakeven points (where gross crosses 0) ─────────────────────────────
    for (let i = 1; i <= N; i++) {
      const a = gross[i - 1], b = gross[i];
      if ((a < 0 && b >= 0) || (a >= 0 && b < 0)) {
        const t = a / (a - b);
        const sBE = xs[i - 1] + t * (xs[i] - xs[i - 1]);
        const xBE = this.xOf(sBE), yBE = yZero;
        ctx.fillStyle = '#fbbf24';
        ctx.beginPath();
        ctx.arc(xBE, yBE, 4, 0, Math.PI * 2);
        ctx.fill();
        ctx.strokeStyle = '#fff';
        ctx.lineWidth = 1;
        ctx.stroke();
        // Label below
        ctx.fillStyle = '#fbbf24';
        ctx.font = 'bold 9px -apple-system, sans-serif';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'top';
        ctx.fillText('BE ' + Math.round(sBE), xBE, yBE + 6);
      }
    }

    // ── Max P&L annotations ──────────────────────────────────────────────────
    const maxProfit = Math.max(...gross);
    const maxLoss = Math.min(...gross);
    const idxMax = gross.indexOf(maxProfit);
    const idxMin = gross.indexOf(maxLoss);

    if (maxProfit > 0) {
      const x = this.xOf(xs[idxMax]), y = this.yOf(maxProfit);
      ctx.fillStyle = '#26a69a';
      ctx.beginPath(); ctx.arc(x, y, 3, 0, Math.PI * 2); ctx.fill();
      ctx.fillStyle = '#26a69a';
      ctx.font = 'bold 10px -apple-system, sans-serif';
      ctx.textAlign = 'left';
      ctx.textBaseline = 'bottom';
      const lbl = 'Max +₹' + this.kfmt(maxProfit);
      const tx = Math.min(x + 6, plotR - 80);
      ctx.fillText(lbl, tx, y - 4);
    }
    if (maxLoss < 0) {
      const x = this.xOf(xs[idxMin]), y = this.yOf(maxLoss);
      ctx.fillStyle = '#ef5350';
      ctx.beginPath(); ctx.arc(x, y, 3, 0, Math.PI * 2); ctx.fill();
      ctx.font = 'bold 10px -apple-system, sans-serif';
      ctx.textAlign = 'left';
      ctx.textBaseline = 'top';
      const lbl = 'Max −₹' + this.kfmt(Math.abs(maxLoss));
      const tx = Math.min(x + 6, plotR - 80);
      ctx.fillText(lbl, tx, y + 4);
    }
  }

  // ── Hover ──────────────────────────────────────────────────────────────────
  onMove(ev: MouseEvent) {
    const cv = this.canvasRef?.nativeElement;
    if (!cv) return;
    const rect = cv.getBoundingClientRect();
    const x = ev.clientX - rect.left;
    const y = ev.clientY - rect.top;
    const L = this.layout;
    if (x < L.padLeft || x > L.width - L.padRight) {
      this.hover.set(null);
      return;
    }
    const spot = L.spotMin + ((x - L.padLeft) / (L.width - L.padLeft - L.padRight)) * L.spotRange;
    const g = this.pnlAt(spot);
    const n = g - (this.data.charges?.total ?? 0);
    this.hover.set({ x, y, spot, gross: g, net: n });
  }

  // ── Helpers ────────────────────────────────────────────────────────────────
  private niceTicks(min: number, max: number, count: number): number[] {
    const range = max - min;
    if (range === 0) return [min];
    const rawStep = range / count;
    const mag = Math.pow(10, Math.floor(Math.log10(rawStep)));
    const norm = rawStep / mag;
    const step = (norm < 1.5 ? 1 : norm < 3 ? 2 : norm < 7 ? 5 : 10) * mag;
    const start = Math.ceil(min / step) * step;
    const ticks: number[] = [];
    for (let v = start; v <= max + step * 0.001; v += step) ticks.push(v);
    return ticks;
  }
  private fmtPnl(v: number): string {
    if (Math.abs(v) >= 1000) return (v >= 0 ? '+' : '−') + '₹' + this.kfmt(Math.abs(v));
    return (v >= 0 ? '+' : '−') + '₹' + Math.round(Math.abs(v));
  }
  private kfmt(v: number): string {
    if (v >= 100000) return (v / 100000).toFixed(1) + 'L';
    if (v >= 1000) return (v / 1000).toFixed(1) + 'k';
    return Math.round(v).toString();
  }
}
