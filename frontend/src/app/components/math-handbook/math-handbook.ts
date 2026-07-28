import { Component, signal, computed, AfterViewInit, OnDestroy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';

interface Chapter {
  id: string; number: string; title: string; subtitle: string;
}
interface ChapterGroup {
  label: string;
  icon: string;
  day?: number;        // 1 or 2 — for the day-banner tagging
  chapters: Chapter[];
}

@Component({
  selector: 'app-math-handbook',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './math-handbook.html',
  styleUrl: './math-handbook.scss',
})
export class MathHandbookComponent implements AfterViewInit, OnDestroy {

  // Expose Math for template expressions like Math.log, Math.sqrt
  readonly Math = Math;

  // ─────────────────────────────────────────────────────────────────────────
  //  RUNNING EXAMPLE — every chapter uses these values
  // ─────────────────────────────────────────────────────────────────────────
  S    = signal(24000);   // spot
  sig  = signal(0.15);    // annual volatility
  mu   = signal(0.12);    // real-world drift
  r    = signal(0.065);   // risk-free
  Tdays = signal(30);     // days to expiry
  Kc   = signal(24720);   // call strike (3% OTM)
  Kp   = signal(23280);   // put strike  (3% OTM)
  Kh   = signal(22320);   // hedge put strike (7% OTM)
  L    = signal(75);      // lot size
  slab = signal(0.30);    // tax slab

  // Convenience computed
  T  = computed(() => this.Tdays() / 365);
  sigT = computed(() => this.sig() * Math.sqrt(this.T()));

  // ─────────────────────────────────────────────────────────────────────────
  //  STANDARD NORMAL — Φ(x) and φ(x)
  // ─────────────────────────────────────────────────────────────────────────
  static erf(x: number): number {
    // Abramowitz-Stegun approximation
    const a1 = 0.254829592, a2 = -0.284496736, a3 = 1.421413741,
          a4 = -1.453152027, a5 = 1.061405429, p = 0.3275911;
    const sg = x < 0 ? -1 : 1;
    const ax = Math.abs(x) / Math.sqrt(2);
    const t = 1 / (1 + p * ax);
    const y = 1 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * Math.exp(-ax * ax);
    return sg * y;
  }
  static N(x: number): number { return 0.5 * (1 + MathHandbookComponent.erf(x / Math.sqrt(2))); }
  static n(x: number): number { return Math.exp(-0.5 * x * x) / Math.sqrt(2 * Math.PI); }
  N(x: number) { return MathHandbookComponent.N(x); }
  n(x: number) { return MathHandbookComponent.n(x); }

  // ─────────────────────────────────────────────────────────────────────────
  //  BLACK-SCHOLES (live) — used in every chapter's widgets
  // ─────────────────────────────────────────────────────────────────────────
  d1 = computed(() => {
    const S = this.S(), K = this.Kc(), T = this.T(), r = this.r(), sig = this.sig();
    return (Math.log(S / K) + (r + 0.5 * sig * sig) * T) / (sig * Math.sqrt(T));
  });
  d2 = computed(() => this.d1() - this.sigT());

  callPrice = computed(() => {
    const S = this.S(), K = this.Kc(), T = this.T(), r = this.r();
    return S * this.N(this.d1()) - K * Math.exp(-r * T) * this.N(this.d2());
  });
  putPrice = computed(() => {
    // for the put strike Kp
    const S = this.S(), K = this.Kp(), T = this.T(), r = this.r(), sig = this.sig();
    const d1p = (Math.log(S / K) + (r + 0.5 * sig * sig) * T) / (sig * Math.sqrt(T));
    const d2p = d1p - sig * Math.sqrt(T);
    return K * Math.exp(-r * T) * this.N(-d2p) - S * this.N(-d1p);
  });
  hedgePutPrice = computed(() => {
    const S = this.S(), K = this.Kh(), T = 90 / 365, r = this.r(), sig = this.sig();
    const d1h = (Math.log(S / K) + (r + 0.5 * sig * sig) * T) / (sig * Math.sqrt(T));
    const d2h = d1h - sig * Math.sqrt(T);
    return K * Math.exp(-r * T) * this.N(-d2h) - S * this.N(-d1h);
  });

  // Greeks (for the call at strike Kc)
  delta = computed(() => this.N(this.d1()));
  gamma = computed(() => this.n(this.d1()) / (this.S() * this.sigT()));
  vega  = computed(() => this.S() * this.n(this.d1()) * Math.sqrt(this.T()) / 100);
  theta = computed(() => {
    const S = this.S(), K = this.Kc(), r = this.r();
    const T = this.T();
    return ((-S * this.n(this.d1()) * this.sig() / (2 * Math.sqrt(T)))
             - r * K * Math.exp(-r * T) * this.N(this.d2())) / 365;
  });

  // Probability of put assignment (under real-world drift μ)
  probPutAssigns = computed(() => {
    const S = this.S(), K = this.Kp(), T = this.T(), mu = this.mu(), sig = this.sig();
    const d2_mu = (Math.log(S / K) + (mu - 0.5 * sig * sig) * T) / (sig * Math.sqrt(T));
    return 1 - this.N(d2_mu);
  });
  probCallAssigns = computed(() => {
    const S = this.S(), K = this.Kc(), T = this.T(), mu = this.mu(), sig = this.sig();
    const d2_mu = (Math.log(S / K) + (mu - 0.5 * sig * sig) * T) / (sig * Math.sqrt(T));
    return this.N(d2_mu);
  });

  // ─────────────────────────────────────────────────────────────────────────
  //  CHAPTER WIDGET HELPERS
  // ─────────────────────────────────────────────────────────────────────────

  // ── Protected Wheel combined payoff (long NB + short CC + long hedge put) ─
  protectedWheelPayoff = computed(() => {
    const S0 = this.S(), Kc = this.Kc(), Kh = this.Kh();
    const pc = this.callPrice(), ph = this.hedgePutPrice();
    const W = 540, H = 200, offX = 40, offY = 20;
    const xMin = S0 * 0.80, xMax = S0 * 1.20;
    const N = 80;
    const pts: number[] = [];
    for (let i = 0; i <= N; i++) {
      const ST = xMin + (xMax - xMin) * i / N;
      // Components per unit
      const nbPnL    = ST - S0;
      const ccPnL    = pc - Math.max(ST - Kc, 0);
      const hedgePnL = Math.max(Kh - ST, 0) - ph;
      pts.push(nbPnL + ccPnL + hedgePnL);
    }
    const maxAbs = Math.max(...pts.map(Math.abs)) || 1;
    const out: string[] = [];
    for (let i = 0; i <= N; i++) {
      const x = offX + (i / N) * W;
      const y = offY + H/2 - (pts[i] / maxAbs) * (H/2);
      out.push(`${x.toFixed(1)},${y.toFixed(1)}`);
    }
    return {
      path: 'M ' + out.join(' L '),
      xMin, xMax, S0, Kc, Kh,
      maxGain: Math.max(...pts).toFixed(2),
      maxLoss: Math.min(...pts).toFixed(2),
    };
  });

  // ── Uncertainty cone for Eq 1.2 (StDev grows with √t) ──────────────────────
  uncertaintyCone = computed(() => {
    const S0 = this.S(), sig = this.sig(), mu = this.mu();
    const W = 540, H = 200, offX = 40, offY = 20;
    const Tmax = 1;          // 1 year horizon
    const N = 60;
    const yMin = S0 * 0.5, yMax = S0 * 1.7;
    const yScale = (y: number) => offY + H - ((y - yMin) / (yMax - yMin)) * H;
    const xScale = (t: number) => offX + (t / Tmax) * W;
    const pts = { centre: [] as string[], oneU: [] as string[], oneL: [] as string[],
                  twoU: [] as string[], twoL: [] as string[] };
    for (let i = 0; i <= N; i++) {
      const t = (i / N) * Tmax;
      const expected = S0 * Math.exp(mu * t);
      const sd = sig * Math.sqrt(t) * S0;   // approximately for small T
      const x = xScale(t).toFixed(1);
      pts.centre.push(`${x},${yScale(expected).toFixed(1)}`);
      pts.oneU.push(`${x},${yScale(expected + sd).toFixed(1)}`);
      pts.oneL.push(`${x},${yScale(expected - sd).toFixed(1)}`);
      pts.twoU.push(`${x},${yScale(expected + 2*sd).toFixed(1)}`);
      pts.twoL.push(`${x},${yScale(expected - 2*sd).toFixed(1)}`);
    }
    return {
      centre: 'M ' + pts.centre.join(' L '),
      one:    'M ' + pts.oneU.join(' L ') + ' L ' + pts.oneL.reverse().join(' L ') + ' Z',
      two:    'M ' + pts.twoU.join(' L ') + ' L ' + pts.twoL.reverse().join(' L ') + ' Z',
      yMin, yMax,
      labelTop: yScale(yMax).toFixed(1),
      labelBot: yScale(yMin).toFixed(1),
      labelStart: yScale(S0).toFixed(1),
      width: W, offX, offY,
    };
  });

  // Random walks (Chapter 1)
  walkSeed = signal(1);
  walkSigma = signal(0.15);
  walkPaths = computed(() => {
    const seed = this.walkSeed();
    const sig = this.walkSigma();
    const paths: string[] = [];
    let rngState = seed;
    const rand = () => {
      // xorshift32
      rngState ^= rngState << 13; rngState ^= rngState >>> 17; rngState ^= rngState << 5;
      return ((rngState >>> 0) / 0xffffffff) - 0.5;
    };
    for (let p = 0; p < 5; p++) {
      const pts: string[] = [];
      let val = 100;
      for (let i = 0; i <= 30; i++) {
        const x = 40 + (i / 30) * 540;
        const yval = 200 - ((val - 100) / 100) * 80;
        pts.push(`${x.toFixed(1)},${Math.max(20, Math.min(220, yval)).toFixed(1)}`);
        const dt = 1 / 30;
        val = val * Math.exp(-0.5 * sig * sig * dt + sig * Math.sqrt(dt) * rand() * 4);
      }
      paths.push(pts.join(' '));
    }
    return paths;
  });
  pathColors = ['#34d399', '#fca5a5', '#93c5fd', '#fbbf24', '#c7d2fe'];
  regeneratePaths() {
    this.walkSeed.set(Math.floor(Math.random() * 1_000_000) + 1);
  }

  // Log-normal density (Chapter 2) — returns SVG path
  logNormalPath = computed(() => {
    const S0 = this.S(), mu = this.mu(), sig = this.sig(), T = this.T();
    const m = (mu - 0.5 * sig * sig) * T;
    const s = sig * Math.sqrt(T);
    // Plot density of S_T over a range
    const xMin = S0 * 0.7, xMax = S0 * 1.3;
    const points: string[] = [];
    const W = 540, H = 160; const offX = 40, offY = 40;
    let maxY = 0;
    const ys: number[] = [];
    for (let i = 0; i <= 80; i++) {
      const x = xMin + (xMax - xMin) * i / 80;
      const lnX = Math.log(x / S0);
      const z = (lnX - m) / s;
      const dens = Math.exp(-0.5 * z * z) / (x * s * Math.sqrt(2 * Math.PI));
      ys.push(dens);
      if (dens > maxY) maxY = dens;
    }
    for (let i = 0; i <= 80; i++) {
      const x = offX + (i / 80) * W;
      const y = offY + H - (ys[i] / maxY) * H;
      points.push(`${x.toFixed(1)},${y.toFixed(1)}`);
    }
    return `M ${offX},${offY+H} L ` + points.join(' L ') + ` L ${offX+W},${offY+H} Z`;
  });

  // Payoff diagrams — generate SVG points for various positions
  payoffPath(kind: 'long_call' | 'short_call' | 'long_put' | 'short_put' |
             'covered_call' | 'csp' | 'iron_condor', K?: number): string {
    const S0 = this.S();
    const W = 540, H = 160; const offX = 40, offY = 40;
    const xMin = S0 * 0.85, xMax = S0 * 1.15;
    const Kn = K ?? this.Kc();
    const lot = this.L();

    let prem = 0;
    if (kind === 'long_call' || kind === 'short_call' || kind === 'covered_call') {
      prem = this.callPrice();
    } else if (kind === 'long_put' || kind === 'short_put' || kind === 'csp') {
      prem = this.putPrice();
    }

    const pts: number[] = [];
    const N = 80;
    for (let i = 0; i <= N; i++) {
      const ST = xMin + (xMax - xMin) * i / N;
      let pl = 0;
      switch (kind) {
        case 'long_call':    pl = lot * (Math.max(ST - Kn, 0) - prem); break;
        case 'short_call':   pl = lot * (prem - Math.max(ST - Kn, 0)); break;
        case 'long_put':     pl = lot * (Math.max(Kn - ST, 0) - prem); break;
        case 'short_put':    pl = lot * (prem - Math.max(Kn - ST, 0)); break;
        case 'covered_call': pl = lot * ((Math.min(ST, Kn) - S0) + prem); break;
        case 'csp':          pl = lot * (prem - Math.max(Kn - ST, 0)); break;
        case 'iron_condor':  {
          const Kps = this.Kp(), KpL = Kps - 480;
          const Kcs = this.Kc(), KcL = Kcs + 480;
          const netP = (this.putPrice() - this.putPrice() * 0.45) +
                       (this.callPrice() - this.callPrice() * 0.45);
          pl = lot * (netP -
                      (Math.max(Kps - ST, 0) - Math.max(KpL - ST, 0)) -
                      (Math.max(ST - Kcs, 0) - Math.max(ST - KcL, 0)));
          break;
        }
      }
      pts.push(pl);
    }
    // Scale
    const maxAbs = Math.max(...pts.map(Math.abs)) || 1;
    const out: string[] = [];
    for (let i = 0; i <= N; i++) {
      const x = offX + (i / N) * W;
      const y = offY + H/2 - (pts[i] / maxAbs) * (H/2);
      out.push(`${x.toFixed(1)},${y.toFixed(1)}`);
    }
    return 'M ' + out.join(' L ');
  }

  // Greeks profiles — Δ, Γ, Θ vs spot
  greekPath(which: 'delta' | 'gamma' | 'theta'): string {
    const K = this.Kc(), T = this.T(), r = this.r(), sig = this.sig();
    const W = 540, H = 160, offX = 40, offY = 40;
    const xMin = K * 0.92, xMax = K * 1.08;
    const N = 80;
    const ys: number[] = [];
    for (let i = 0; i <= N; i++) {
      const S = xMin + (xMax - xMin) * i / N;
      const d1 = (Math.log(S / K) + (r + 0.5 * sig * sig) * T) / (sig * Math.sqrt(T));
      const d2 = d1 - sig * Math.sqrt(T);
      let v = 0;
      if (which === 'delta') v = this.N(d1);
      else if (which === 'gamma') v = this.n(d1) / (S * sig * Math.sqrt(T));
      else v = ((-S * this.n(d1) * sig / (2 * Math.sqrt(T))) - r * K * Math.exp(-r * T) * this.N(d2)) / 365;
      ys.push(v);
    }
    const minY = Math.min(...ys), maxY = Math.max(...ys);
    const range = maxY - minY || 1;
    const out: string[] = [];
    for (let i = 0; i <= N; i++) {
      const x = offX + (i / N) * W;
      const y = offY + H - ((ys[i] - minY) / range) * H;
      out.push(`${x.toFixed(1)},${y.toFixed(1)}`);
    }
    return 'M ' + out.join(' L ');
  }

  // Wheel state machine simulation
  wheelN_A = computed(() => 1 / Math.max(this.probPutAssigns(), 0.01));
  wheelN_B = computed(() => 1 / Math.max(this.probCallAssigns(), 0.01));
  wheelCycleMonths = computed(() => this.wheelN_A() + this.wheelN_B());
  wheelBuyLow = computed(() => (this.S() - this.Kp()) * this.L());
  wheelSellHigh = computed(() => (this.Kc() - this.S()) * this.L());

  // Friction calculator
  netReturn = computed(() => {
    const grossPct = 14; // example gross %
    const tax = grossPct * this.slab() * 1.04;
    const friction = 0.5;
    return grossPct - tax - friction;
  });

  // ─────────────────────────────────────────────────────────────────────────
  //  TOC — grouped by topic / strategy / practical
  // ─────────────────────────────────────────────────────────────────────────
  readonly chapters: Chapter[] = [
    { id: 'intro',           number: 'Preface',     title: 'How to read this book',           subtitle: 'Setting up your example' },
    { id: 'ch1-random',      number: 'Chapter 1',   title: 'Random Walks',                    subtitle: 'Why prices are stochastic' },
    { id: 'ch2-lognormal',   number: 'Chapter 2',   title: 'Log-Normal Distribution',         subtitle: 'The shape of returns' },
    { id: 'ch-moneyness',    number: 'Basics',      title: 'ITM / ATM / OTM',                 subtitle: 'The language of options' },
    { id: 'ch3-blackscholes',number: 'Chapter 3',   title: 'Black-Scholes Pricing',           subtitle: 'The cornerstone' },
    { id: 'ch4-greeks',      number: 'Chapter 4',   title: 'The Greeks',                      subtitle: 'How options change' },
    { id: 'ch5-vrp',         number: 'Chapter 5',   title: 'Volatility Risk Premium',         subtitle: 'The seller\'s edge' },
    { id: 'ch6-cc',          number: 'Chapter 6',   title: 'Covered Call Math',               subtitle: 'Decomposing the position' },
    { id: 'ch7-csp',         number: 'Chapter 7',   title: 'Cash-Secured Put',                subtitle: 'The mirror image' },
    { id: 'ch8-wheel',       number: 'Chapter 8',   title: 'The Wheel',                       subtitle: 'Three sources of edge' },
    { id: 'ch9-protected',   number: 'Chapter 9',   title: 'Protected Wheel',                 subtitle: 'Insurance economics' },
    { id: 'ch10-iron',       number: 'Chapter 10',  title: 'Iron Condor',                     subtitle: 'Defined-risk income' },
    { id: 'ch11-friction',   number: 'Chapter 11',  title: 'Frictions',                       subtitle: 'Tax + slippage + charges' },
    { id: 'ch12-decision',   number: 'Chapter 12',  title: 'Decision Framework',              subtitle: 'Choosing your weapon' },
    // ─── Day 2 — Protected Wheel Master Class ──────────────────────────────
    { id: 'pw-overview',     number: 'D2 · 1',      title: 'Why Protected Wheel?',            subtitle: 'The case for crash insurance' },
    { id: 'pw-anatomy',      number: 'D2 · 2',      title: 'Position Anatomy',                subtitle: 'The 4 legs you hold' },
    { id: 'pw-entry',        number: 'D2 · 3',      title: 'Entry Checklist',                 subtitle: 'When to start a cycle' },
    { id: 'pw-strikes',      number: 'D2 · 4',      title: 'Strike Selection',                subtitle: 'How to pick K_p, K_c, K_h' },
    { id: 'pw-greeks',       number: 'D2 · 5',      title: 'Combined Greeks',                 subtitle: 'Net Δ, Γ, θ, ν of the position' },
    { id: 'pw-returns',      number: 'D2 · 6',      title: 'Expected Return',                 subtitle: 'Where every rupee comes from' },
    { id: 'pw-risk',         number: 'D2 · 7',      title: 'Risk & Failure Modes',            subtitle: 'When the strategy bleeds' },
    { id: 'pw-maximize',     number: 'D2 · 8',      title: 'Maximizing Profits',              subtitle: 'Tuning every dial' },
    { id: 'pw-exit',         number: 'D2 · 9',      title: 'Exit & Rolling',                  subtitle: 'Knowing when to act' },
    { id: 'pw-pitfalls',     number: 'D2 · 10',     title: 'Common Pitfalls',                 subtitle: 'What kills the strategy' },
    { id: 'pw-improvements', number: 'D2 · 11',     title: '10 Improvements',                 subtitle: 'Boosting return 10% → 17%' },
  ];

  readonly groups: ChapterGroup[] = [
    // ── Day 1: all foundational content collapsed into one tab ─────────────
    { label: 'Foundations & Strategies (Day 1)', icon: '📘', day: 1,
      chapters: [
        this.chapters[0],   // Preface
        this.chapters[3],   // ITM/OTM
        this.chapters[1],   // Random Walks
        this.chapters[2],   // Log-Normal
        this.chapters[4],   // Black-Scholes
        this.chapters[5],   // Greeks
        this.chapters[6],   // VRP
        this.chapters[7],   // Covered Call
        this.chapters[8],   // CSP
        this.chapters[9],   // Wheel
        this.chapters[10],  // Protected Wheel
        this.chapters[11],  // Iron Condor
        this.chapters[12],  // Frictions
        this.chapters[13],  // Decision
      ] },
    // ── Day 2: deep dive on Protected Wheel ───────────────────────────────
    { label: 'Protected Wheel Master Class (Day 2)', icon: '🎯', day: 2,
      chapters: [
        this.chapters[14], this.chapters[15], this.chapters[16], this.chapters[17],
        this.chapters[18], this.chapters[19], this.chapters[20], this.chapters[21],
        this.chapters[22], this.chapters[23], this.chapters[24],
      ] },
  ];

  expandedGroups = signal<Set<string>>(new Set(['Getting Started', 'Foundations', 'Income Strategies', 'Risk-Managed', 'Practical']));
  toggleGroup(label: string) {
    const s = new Set(this.expandedGroups());
    if (s.has(label)) s.delete(label); else s.add(label);
    this.expandedGroups.set(s);
  }
  isGroupOpen(label: string): boolean { return this.expandedGroups().has(label); }

  // Variables ⓘ-button: which equation's panel is expanded
  expandedVar = signal<string>('');
  toggleVar(id: string) {
    this.expandedVar.set(this.expandedVar() === id ? '' : id);
  }

  activeId = signal<string>('intro');
  exampleBarOpen = signal(true);
  tocOpen = signal(true);
  toggleExampleBar() { this.exampleBarOpen.update(v => !v); }
  toggleToc() { this.tocOpen.update(v => !v); }

  // ── Scroll-spy ────────────────────────────────────────────────────────────
  private observer?: IntersectionObserver;
  ngAfterViewInit() { setTimeout(() => this.attachObserver(), 100); }
  ngOnDestroy() { this.observer?.disconnect(); }
  private attachObserver() {
    if (typeof window === 'undefined') return;
    this.observer = new IntersectionObserver((entries) => {
      const visible = entries.filter(e => e.isIntersecting)
                              .sort((a, b) => b.intersectionRatio - a.intersectionRatio);
      if (visible[0]) this.activeId.set(visible[0].target.id);
    }, { rootMargin: '-15% 0px -65% 0px', threshold: [0, 0.25, 0.5, 0.75, 1] });
    this.chapters.forEach(c => {
      const el = document.getElementById(c.id);
      if (el) this.observer!.observe(el);
    });
  }
  scrollTo(id: string) {
    const el = document.getElementById(id);
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'start' });
      this.activeId.set(id);
    }
  }

  // ── Formatters ─────────────────────────────────────────────────────────────
  fmt(v: number, dp = 2): string {
    if (v == null || isNaN(v)) return '—';
    return v.toFixed(dp);
  }
  fmtRs(v: number): string {
    if (v == null || isNaN(v)) return '—';
    return `₹${Math.round(v).toLocaleString('en-IN')}`;
  }
  fmtPct(v: number, dp = 2): string {
    if (v == null || isNaN(v)) return '—';
    return `${v.toFixed(dp)}%`;
  }

  resetExample() {
    this.S.set(24000); this.sig.set(0.15); this.mu.set(0.12);
    this.r.set(0.065); this.Tdays.set(30);
    this.Kc.set(24720); this.Kp.set(23280); this.Kh.set(22320);
  }
}
