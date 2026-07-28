import { Component, OnInit, inject, signal, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ApiService } from '../../services/api.service';

interface Formula { label: string; latex: string; value: number; }
interface StrategyAnalysis {
  name: string; description: string;
  intermediates: { [k: string]: number };
  per_cycle:     { [k: string]: number };
  annual_gross_pct: number; annual_net_pct: number;
  monthly_gross_pct: number; monthly_net_pct: number;
  prob_negative_month: number;
  max_loss_per_cycle: number; max_gain_per_cycle: number;
  sharpe_estimate: number;
  formulae: Formula[];
  notes: string[];
}

@Component({
  selector: 'app-strategy-math',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './strategy-math.html',
  styleUrl: './strategy-math.scss',
})
export class StrategyMathComponent implements OnInit {
  private api = inject(ApiService);

  // ── Inputs (bound to sliders / number boxes) ──────────────────────────────
  spot          = signal(24000);
  capital       = signal(1800000);
  sigma         = signal(0.15);   // 15%
  mu            = signal(0.12);   // 12%
  riskFree      = signal(0.065);
  cashYield     = signal(0.065);
  Tmonths       = signal(1);
  alphaPut      = signal(0.03);
  alphaCall     = signal(0.03);
  alphaHedge    = signal(0.07);
  hedgeMonths   = signal(3);
  slabRate      = signal(0.30);
  cessRate      = signal(0.04);
  frictionPct   = signal(0.005);

  // ── View state ────────────────────────────────────────────────────────────
  view          = signal<'compare' | 'detail'>('compare');
  selectedKey   = signal<'covered_call' | 'wheel' | 'protected_wheel' | 'iron_condor'>('wheel');
  loading       = signal(false);
  error         = signal('');

  comparison    = signal<{ [k: string]: StrategyAnalysis } | null>(null);
  detail        = signal<StrategyAnalysis | null>(null);

  showFormulae      = signal(true);
  showIntermediates = signal(false);

  // ── Lifecycle ─────────────────────────────────────────────────────────────
  ngOnInit() {
    this.runComparison();
  }

  paramsBody() {
    return {
      spot: this.spot(), capital: this.capital(),
      sigma: this.sigma(), mu: this.mu(),
      risk_free: this.riskFree(), cash_yield: this.cashYield(),
      T_months: this.Tmonths(),
      alpha_put: this.alphaPut(), alpha_call: this.alphaCall(),
      alpha_hedge: this.alphaHedge(), hedge_T_months: this.hedgeMonths(),
      slab_rate: this.slabRate(), cess_rate: this.cessRate(),
      friction_pct: this.frictionPct(),
    };
  }

  runComparison() {
    this.loading.set(true);
    this.error.set('');
    this.api.ccMathCompare(this.paramsBody()).subscribe({
      next: (res) => {
        this.comparison.set(res);
        this.loading.set(false);
      },
      error: (e) => {
        this.error.set(e?.error?.detail || 'Failed to compute');
        this.loading.set(false);
      },
    });
  }

  runDetail(key: string) {
    const k = key as 'covered_call' | 'wheel' | 'protected_wheel' | 'iron_condor';
    this.selectedKey.set(k);
    this.view.set('detail');
    this.loading.set(true);
    this.api.ccMathAnalyze({ ...this.paramsBody(), strategy: k }).subscribe({
      next: (res) => {
        this.detail.set(res);
        this.loading.set(false);
      },
      error: (e) => {
        this.error.set(e?.error?.detail || 'Failed to compute');
        this.loading.set(false);
      },
    });
  }

  backToCompare() {
    this.view.set('compare');
    this.detail.set(null);
    this.runComparison();
  }

  // Auto-recompute when sliders change (debounced via setTimeout)
  private debounceTimer: any;
  onParamChange() {
    clearTimeout(this.debounceTimer);
    this.debounceTimer = setTimeout(() => {
      if (this.view() === 'compare') this.runComparison();
      else this.runDetail(this.selectedKey());
    }, 350);
  }

  resetDefaults() {
    this.spot.set(24000); this.capital.set(1800000);
    this.sigma.set(0.15); this.mu.set(0.12);
    this.riskFree.set(0.065); this.cashYield.set(0.065);
    this.Tmonths.set(1);
    this.alphaPut.set(0.03); this.alphaCall.set(0.03);
    this.alphaHedge.set(0.07); this.hedgeMonths.set(3);
    this.slabRate.set(0.30); this.cessRate.set(0.04);
    this.frictionPct.set(0.005);
    this.onParamChange();
  }

  // ── Computed UI helpers ───────────────────────────────────────────────────
  comparisonRows = computed(() => {
    const c = this.comparison();
    if (!c) return [];
    return Object.entries(c).map(([key, val]) => ({ key, ...val }));
  });

  bestStrategyKey = computed(() => {
    const rows = this.comparisonRows();
    if (!rows.length) return null;
    return rows.reduce((best, cur) => cur.annual_net_pct > best.annual_net_pct ? cur : best).key;
  });

  // Formatting helpers
  fmtPct(v: number, dp = 2): string {
    if (v == null || isNaN(v)) return '—';
    return `${v >= 0 ? '+' : ''}${v.toFixed(dp)}%`;
  }
  fmtRs(v: number): string {
    if (v == null || isNaN(v)) return '—';
    const sign = v >= 0 ? '+' : '−';
    return `${sign}₹${Math.abs(v).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`;
  }
  fmtRsPlain(v: number): string {
    if (v == null || isNaN(v)) return '—';
    return `₹${v.toLocaleString('en-IN', { maximumFractionDigits: 0 })}`;
  }
  fmtNum(v: number, dp = 2): string {
    if (v == null || isNaN(v)) return '—';
    return v.toFixed(dp);
  }
  /** Auto-pick decimal precision: small numbers get 4dp, large get 2dp. */
  fmtAuto(v: number): string {
    if (v == null || isNaN(v)) return '—';
    return v.toFixed(Math.abs(v) < 1 ? 4 : 2);
  }
  fmtKey(k: string): string {
    return k.split('_').map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');
  }
  // Render a "LaTeX-lite" formula. We don't bring in KaTeX — instead replace
  // a small set of common mathematical primitives with HTML/CSS spans.
  renderLatex(latex: string): string {
    let s = latex;
    // Subscripts: K_p → K<sub>p</sub>, K_p^{short} → K<sub>p</sub><sup>short</sup>
    s = s.replace(/\\Phi/g, 'Φ');
    s = s.replace(/\\mathbb\{P\}/g, 'ℙ');
    s = s.replace(/\\mathbb\{E\}/g, '𝔼');
    s = s.replace(/\\Pi/g, 'Π');
    s = s.replace(/\\alpha/g, 'α');
    s = s.replace(/\\sigma/g, 'σ');
    s = s.replace(/\\mu/g, 'μ');
    s = s.replace(/\\tau/g, 'τ');
    s = s.replace(/\\Delta/g, 'Δ');
    s = s.replace(/\\phi/g, 'φ');
    s = s.replace(/\\max/g, 'max');
    s = s.replace(/\\min/g, 'min');
    s = s.replace(/\\sqrt/g, '√');
    s = s.replace(/\\cdot/g, '·');
    s = s.replace(/\\frac\{([^}]*)\}\{([^}]*)\}/g, '<span class="frac"><span class="num">$1</span><span class="den">$2</span></span>');
    s = s.replace(/\\text\{([^}]*)\}/g, '<span class="op">$1</span>');
    // Subscripts: _p, _{cycle}
    s = s.replace(/\^\{([^}]*)\}/g, '<sup>$1</sup>');
    s = s.replace(/\^([a-zA-Z0-9])/g, '<sup>$1</sup>');
    s = s.replace(/_\{([^}]*)\}/g, '<sub>$1</sub>');
    s = s.replace(/_([a-zA-Z0-9])/g, '<sub>$1</sub>');
    return s;
  }

  // Color-grade percentage cells in the comparison table
  pctClass(v: number): string {
    if (v >= 1.0) return 'cell-strong-pos';
    if (v >= 0.5) return 'cell-pos';
    if (v >= 0)   return 'cell-neutral';
    return 'cell-neg';
  }
  sharpeClass(v: number): string {
    if (v >= 1.0) return 'cell-strong-pos';
    if (v >= 0.5) return 'cell-pos';
    if (v >= 0)   return 'cell-neutral';
    return 'cell-neg';
  }

  // Sort intermediates / per-cycle dicts to a stable order for display
  sortKeys(obj: { [k: string]: number }): { key: string; value: number }[] {
    return Object.entries(obj).map(([key, value]) => ({ key, value }));
  }
}
