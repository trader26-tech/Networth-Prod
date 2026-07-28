import { Component, computed, effect, inject, input, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { EquityService, Performance } from '../../services/equity.service';

@Component({
  selector: 'app-perf',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './perf.html',
  styleUrl: './perf.scss',
})
export class Perf {
  private api = inject(EquityService);
  inr = EquityService.inr;
  pct = EquityService.pct;

  /** selected account ids (empty = all) — scopes the backtest */
  accountIds = input<string[]>([]);

  period = signal<'1mo' | '6mo' | '1y'>('1y');
  data = signal<Performance | null>(null);
  loading = signal(true);
  hover = signal<number | null>(null);
  showInfo = signal(false);
  refreshing = signal(false);   // hard recompute from CURRENT holdings (bypasses the ≤1h cache)

  // inspect: stock-by-stock valuation on a chosen date (data audit)
  inspectOpen = signal(false);
  inspectDate = signal('');
  inspectData = signal<any | null>(null);
  inspectLoading = signal(false);
  inspectSearch = signal('');

  constructor() {
    // reload whenever the period or the account selection changes (cached server-side)
    effect(() => { this.period(); this.accountIds(); this.load(); });
  }
  setPeriod(p: '1mo' | '6mo' | '1y') { this.period.set(p); }
  load() {
    this.loading.set(true); this.hover.set(null);
    this.api.performance(this.period(), this.accountIds()).subscribe({
      next: d => { this.data.set(d); this.loading.set(false); },
      error: () => { this.loading.set(false); },
    });
  }
  /** Recompute the curve from CURRENT holdings (after adding capital/trades):
   *  the same chart, re-valued back through each stock's history, bypassing the
   *  ≤1h server cache so the whole line reflects what you hold now. */
  refresh() {
    this.refreshing.set(true); this.hover.set(null);
    this.api.performance(this.period(), this.accountIds(), true).subscribe({
      next: d => { this.data.set(d); this.refreshing.set(false); },
      error: () => { this.refreshing.set(false); },
    });
  }

  private fmtDate(iso: string): string {
    const d = new Date(iso + 'T00:00:00');
    return d.toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: '2-digit' });
  }

  chart = computed(() => {
    const pts = this.data()?.points || [];
    if (pts.length < 2) return null;
    const v0 = pts[0].value || 1, n0 = pts[0].nifty || 1;
    const series = pts.map(p => ({ date: p.date, value: p.value, pr: p.value / v0 - 1, nr: p.nifty / n0 - 1 }));

    const W = 360, H = 188, padL = 40, padR = 14, padT = 14, padB = 26;
    const innerW = W - padL - padR, innerH = H - padT - padB;
    const allPct = series.flatMap(s => [s.pr, s.nr]);
    let lo = Math.min(0, ...allPct), hi = Math.max(0, ...allPct);
    const span = Math.max(hi - lo, 0.02); lo -= span * 0.08; hi += span * 0.08;
    const x = (i: number) => padL + (i / (series.length - 1)) * innerW;
    const y = (v: number) => padT + innerH * (1 - (v - lo) / (hi - lo));

    const pPts = series.map((s, i) => ({ ...s, x: x(i), y: y(s.pr) }));
    const nPts = series.map((s, i) => ({ x: x(i), y: y(s.nr) }));
    const toLine = (ps: { x: number; y: number }[]) => ps.map((p, i) => `${i ? 'L' : 'M'}${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(' ');

    const ticks = [0, 0.25, 0.5, 0.75, 1].map(f => lo + (hi - lo) * f);
    const grid = ticks.map(v => ({ y: y(v), label: (v * 100).toFixed(0) + '%' }));
    const zeroY = (lo < 0 && hi > 0) ? y(0) : null;

    const n = series.length;
    const xticks = [0, Math.floor(n / 2), n - 1].map(i => ({ x: x(i), label: this.fmtDate(series[i].date) }));

    return { W, H, padL, padR, innerW, pPts, nPts, pLine: toLine(pPts), nLine: toLine(nPts), grid, zeroY, xticks, n };
  });

  /** point shown in the tooltip (hovered, else latest) */
  tip = computed(() => {
    const c = this.chart(); if (!c) return null;
    const i = this.hover() ?? c.n - 1;
    const p = c.pPts[i]; if (!p) return null;
    return { date: this.fmtDate(p.date), iso: p.date, value: p.value, pr: p.pr, nr: p.nr, x: p.x, y: p.y };
  });

  openInspect(iso: string) {
    this.inspectDate.set(iso); this.inspectOpen.set(true);
    this.inspectData.set(null); this.inspectSearch.set(''); this.inspectLoading.set(true);
    this.api.performanceHoldings(iso, this.period(), this.accountIds()).subscribe({
      next: d => { this.inspectData.set(d); this.inspectLoading.set(false); },
      error: () => { this.inspectLoading.set(false); },
    });
  }
  inspectLabel = computed(() => this.inspectDate() ? this.fmtDate(this.inspectDate()) : '');
  inspectRows = computed(() => {
    const d = this.inspectData(); if (!d) return [];
    const q = this.inspectSearch().trim().toUpperCase();
    return q ? d.rows.filter((r: any) => (r.symbol || '').includes(q)) : d.rows;
  });

  portfolioReturn = computed(() => { const c = this.chart(); return c ? c.pPts[c.n - 1].pr : null; });
  niftyReturn = computed(() => { const c = this.chart(); return c ? c.pPts[c.n - 1].nr : null; });
}
