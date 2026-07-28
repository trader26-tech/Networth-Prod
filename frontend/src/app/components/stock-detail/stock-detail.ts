import { Component, EventEmitter, HostListener, Input, Output, computed, signal } from '@angular/core';
import { CommonModule, DecimalPipe } from '@angular/common';
import { SnowflakeRadarComponent } from '../snowflake-radar/snowflake-radar';
import { METRIC_GUIDE, MetricSpec, metricBand } from './metric-guide';

/**
 * Detail-view modal — opened when a user clicks a portfolio row.
 *
 *  1. Decision card  — what the system thinks + WHY + recommended action.
 *  2. Snowflake      — big version, with composite + axis breakdown.
 *  3. Signals        — strengths / warnings / blockers in plain English.
 *  4. Metric guide   — every key ratio: current value, acceptable bands,
 *                       plain-English interpretation, and what it tells you.
 *  5. Raw data       — full 45-column Tickertape row at the bottom.
 */
@Component({
  selector: 'app-stock-detail',
  imports: [CommonModule, DecimalPipe, SnowflakeRadarComponent],
  templateUrl: './stock-detail.html',
  styleUrl: './stock-detail.scss',
})
export class StockDetailComponent {
  /** The selected row (must include `snow` with axes + summary + raw). */
  @Input() row: any | null = null;
  @Output() close = new EventEmitter<void>();

  /** Expose Math so the template can clamp marker positions etc. */
  Math = Math;

  @HostListener('document:keydown.escape')
  onEsc() { this.close.emit(); }

  // ─── Action playbook per bucket ──────────────────────────────────────────
  // What the user should actually DO. Plain English, no jargon.
  private readonly PLAYBOOK: Record<string, { title: string; body: string; what_to_do: string[] }> = {
    KEEP_AND_ADD: {
      title: "Core holding — add on weakness",
      body: "Above-average fundamentals across most axes. The system's highest-conviction tier. " +
            "Don't chase the price; wait for corrections or earnings dips to add. Hold for years.",
      what_to_do: [
        "Maintain or increase target weight (max 8% per position).",
        "Add only on >15% price corrections from 1Y high.",
        "Re-evaluate composite each quarter — exit if it drops below 3.5.",
      ],
    },
    HOLD: {
      title: "Maintain — don't add, don't trim",
      body: "Solid business, but not in 'must-add' territory at current price. Either valuation is full or one or two axes are softening. Watch for confirmation either way.",
      what_to_do: [
        "Keep current position size.",
        "Don't average down or up.",
        "If composite improves to ≥4.5 → upgrade to Keep+Add. If it drops below 2.5 → trim.",
      ],
    },
    TRIM: {
      title: "Reduce — capital better deployed elsewhere",
      body: "Below-average composite for the cap tier. Either the thesis is breaking down or valuation has run ahead of fundamentals. Don't blow it up overnight; scale out.",
      what_to_do: [
        "Cut position by ~50% over the next 1–2 months (tax-aware).",
        "If held > 1 year, sell LTCG losses first to offset gains.",
        "Don't add even on dips — the dip is the market pricing in the deterioration.",
      ],
    },
    EXIT: {
      title: "Sell — capital is being destroyed or trapped",
      body: "Either a hard rule fired (governance / fraud / over-leverage) or fundamentals are far below acceptable. Holding longer compounds the loss.",
      what_to_do: [
        "Exit the entire position. Don't hope it recovers — hope is not a strategy.",
        "Prioritize tax-loss harvesting: sell losses that have been held >1 year first (LTCG offset).",
        "Move capital to a Keep+Add stock or a Nifty index fund while you decide.",
      ],
    },
    INSUFFICIENT_DATA: {
      title: "Not scoreable — manual review required",
      body: "Too few axes have reliable underlying data for the rubric to make a call. " +
            "Usually means this isn't a traditional equity (ETF, REIT, recently-listed stock) or it's so illiquid that Tickertape has gaps.",
      what_to_do: [
        "Don't size based on Snowflake — the score is meaningless here.",
        "If it's an ETF, evaluate it on tracking error and expense ratio instead.",
        "If it's a recent IPO / small cap with gaps, wait 2–4 quarters for the data to fill in, then re-score.",
      ],
    },
  };

  playbook = computed(() => {
    const b = this.row?.snow?.bucket;
    return this.PLAYBOOK[b] ?? null;
  });

  /**
   * Detailed multi-paragraph rationale — explains exactly why the system
   * landed on this bucket, which axes counted, which axes were excluded
   * because data wasn't trackable, and how the composite was computed.
   *
   * The user explicitly asked for verbose / educational output; that's
   * what this builds. It composes from data already on the row — no extra
   * backend hop.
   */
  rationale = computed(() => {
    const s = this.row?.snow;
    if (!s) return null;
    const bucket    = s.bucket as string;
    const composite = s.composite as number | null;
    const axes      = (s.axes || {}) as Record<string, { score: number | null; available: number; passed: number; trackable: boolean }>;
    const blockers  = (s.blockers ?? []) as string[];
    const warnings  = (s.warnings ?? []) as string[];
    const strengths = (s.strengths ?? []) as string[];
    const tier      = s.cap_tier as string | undefined;

    // ─ Bucket-trigger explanation ─────────────────────────────────────────
    const triggers: string[] = [];
    if (blockers.length) {
      triggers.push(
        `A hard rule fired: ${blockers[0].toLowerCase()}. In Indian markets this kind of signal historically precedes large drawdowns, so the system overrides composite scoring and forces an EXIT.`
      );
    }
    if (bucket === 'INSUFFICIENT_DATA') {
      const untrackable = Object.entries(axes).filter(([_, a]) => !a.trackable).map(([k]) => k);
      triggers.push(
        `Fewer than 3 of the 6 axes have enough underlying data to score reliably. The untrackable axes are: ${untrackable.join(', ')}. Rather than fabricate a number, the system marks this as not scoreable and asks you to evaluate it manually (or by another framework — e.g. tracking error for an ETF).`
      );
    } else if (composite != null) {
      if (composite >= 4.5) {
        triggers.push(
          `The weighted composite came in at ${composite.toFixed(2)}/6 — comfortably above 4.5, the system's "Keep & Add" threshold. That means above-average sub-check pass rates across most measurable axes.`
        );
      } else if (composite >= 3.5) {
        triggers.push(
          `The weighted composite is ${composite.toFixed(2)}/6 — between 3.5 (Hold) and 4.5 (Keep & Add). The business is sound but either valuation, growth, or one of the quality axes is preventing a stronger rating.`
        );
      } else if (composite >= 2.5) {
        triggers.push(
          `The weighted composite is ${composite.toFixed(2)}/6 — below the 3.5 "Hold" line. Multiple axes are dragging — typically a combination of weak growth, deteriorating margins, or stretched balance sheet. The thesis is breaking down on the numbers.`
        );
      } else {
        triggers.push(
          `The weighted composite is ${composite.toFixed(2)}/6 — well below the 2.5 line. Sub-check pass rate is low across most axes. Holding longer compounds the opportunity cost while better setups exist in your universe.`
        );
      }
    }

    // ─ Axis-by-axis breakdown ─────────────────────────────────────────────
    const trackable = Object.entries(axes)
      .filter(([_, a]) => a.trackable)
      .map(([k, a]) => ({
        name: k,
        score: a.score as number,
        passed: a.passed,
        available: a.available,
        verdict:
          (a.score as number) >= 4.5 ? "strong" :
          (a.score as number) >= 3.0 ? "average" :
          (a.score as number) >= 1.5 ? "weak"   : "very weak",
      }));
    const untrackable = Object.entries(axes)
      .filter(([_, a]) => !a.trackable)
      .map(([k, a]) => ({ name: k, available: a.available }));

    // ─ How composite was computed ─────────────────────────────────────────
    let how_composite = "";
    if (composite != null && trackable.length) {
      const w_text =
        tier === "large" ? "Large-cap weighting (Value 20%, Future 15%, Past 15%, Health 20%, Dividend 15%, Governance 15%)"
      : tier === "mid"   ? "Mid-cap weighting (Value 15%, Future 25%, Past 20%, Health 15%, Dividend 5%, Governance 20%)"
      : tier === "small" ? "Small-cap weighting (Value 10%, Future 35%, Past 15%, Health 15%, Dividend 0%, Governance 25%)"
                         : "Unknown-cap fallback weighting";
      how_composite =
        `The composite is the weighted average of the ${trackable.length} trackable axis scores. ` +
        `${w_text} reflects what matters most at this cap tier — small caps live or die by growth and governance, large caps by health and value. ` +
        (untrackable.length ?
          `The remaining ${untrackable.length} axis/axes (${untrackable.map(a => a.name).join(', ')}) had too few data points and were excluded; the active weights renormalize to 1.0 so untrackable axes never drag the score down.` :
          "All 6 axes had enough data, so no weights were dropped.");
    } else if (composite == null) {
      how_composite = `The composite cannot be computed because the trackable axis count is below the minimum of 3. The score field is left as "n/a" rather than zero so you're not misled by an artificial low number.`;
    }

    // ─ What this tells you about the company ──────────────────────────────
    let interpretation = "";
    if (bucket === 'KEEP_AND_ADD') {
      interpretation = "Businesses that score this well across the rubric usually share three traits: durable competitive position (visible in high consistent ROCE), real cash generation (OCF tracking PAT), and disciplined capital allocation (low/managed debt). These are the kind of holdings you want to size up over time and ride through volatility.";
    } else if (bucket === 'HOLD') {
      interpretation = "Solid businesses in this band often have one specific weakness — typically valuation that's outrun fundamentals, or a single axis (growth, margins) that has plateaued. The right move is to hold what you own and watch for the next quarter's data: if the weak axis recovers, upgrade; if it deteriorates, downgrade.";
    } else if (bucket === 'TRIM') {
      interpretation = "The fundamental story is weakening. This is often the dangerous in-between zone where retail holds on hoping for recovery while the data quietly tells you the business is losing momentum. The historical evidence is clear: when composites drop into this band, returns over the next 12-24 months are usually below benchmark.";
    } else if (bucket === 'EXIT') {
      interpretation = "Either a hard rule fired (governance, fraud-signal, over-leverage) or the broader fundamentals are far below acceptable. In Indian markets, EXIT-bucket stocks often have asymmetric downside — a single bad headline can take them down 30–50% with no warning. Tax-loss harvesting (if you hold > 1 year at a loss) is a real opportunity here too.";
    } else if (bucket === 'INSUFFICIENT_DATA') {
      interpretation = "There's no scoring conclusion to draw — the underlying ratios needed for a fundamental call simply aren't available for this asset (typical of ETFs, REITs, recently-listed companies). This isn't a negative signal; it's the system being honest about its limits.";
    }

    return { triggers, trackable, untrackable, how_composite, interpretation };
  });

  bucketLabel = computed(() => {
    const b = this.row?.snow?.bucket as string | undefined;
    if (!b) return '';
    return b
      .replace('KEEP_AND_ADD',      'Keep & Add')
      .replace('INSUFFICIENT_DATA', 'Not Trackable')
      .replace(/_/g, ' ')
      .toLowerCase()
      .replace(/\b\w/g, c => c.toUpperCase());
  });

  // ─── Metric resolution ──────────────────────────────────────────────────
  // Walks every key in METRIC_GUIDE and emits a row for the UI.
  metricRows = computed(() => {
    const raw = this.row?.snow?.raw;
    if (!raw) return [];
    const peerContext = (this.row?.snow?.peer_context ?? {}) as Record<string, any>;
    const rows: Array<{
      key:    string;
      label:  string;
      axis:   string;
      value:  number | null;
      spec:   MetricSpec;
      band:   ReturnType<typeof metricBand>;
      bandIdx: number;
      pctOnBar: number;
      interpret: string | null;
      peer: any | null;        // peer_context entry — {median, q1, q3, percentile, peer_count, ...}
      exempt: boolean;         // sector-exempt? (e.g. D/E for banks)
    }> = [];
    for (const [key, spec] of Object.entries(METRIC_GUIDE)) {
      const rawVal = raw[key];
      const v = rawVal === '' || rawVal == null ? null : Number(rawVal);
      const validV = v != null && !isNaN(v) ? v : null;
      const band   = validV != null ? metricBand(spec, validV) : null;
      const bandIdx = band ? spec.bands.indexOf(band) : -1;
      const pctOnBar = bandIdx >= 0
        ? ((bandIdx + 0.5) / spec.bands.length) * 100
        : 0;
      const peer = peerContext[key] ?? null;
      const exempt = !!peer?.exempt;
      rows.push({
        key,
        label:    spec.label ?? key,
        axis:     spec.axis,
        value:    validV,
        spec,
        band,
        bandIdx,
        pctOnBar,
        interpret: validV != null && spec.interpret ? spec.interpret(validV) : null,
        peer,
        exempt,
      });
    }
    return rows;
  });

  // Group metric rows by axis for the section layout.
  metricsByAxis = computed(() => {
    const groups: Record<string, ReturnType<typeof this.metricRows>> = {};
    for (const r of this.metricRows()) {
      (groups[r.axis] ||= []).push(r);
    }
    return groups;
  });

  readonly AXIS_ORDER: { key: string; title: string; desc: string }[] = [
    { key: 'value',      title: 'Value',
      desc: "Is the price reasonable today — vs peers, vs history, vs growth rate?" },
    { key: 'future',     title: 'Future',
      desc: "What does next year look like? Forward consensus + recent momentum." },
    { key: 'past',       title: 'Past',
      desc: "Track record — growth, margins, and capital efficiency over 5+ years." },
    { key: 'health',     title: 'Financial Health',
      desc: "Balance-sheet strength + cash quality. Can the company survive a downturn?" },
    { key: 'dividend',   title: 'Dividend',
      desc: "Is it paying you to wait, and is the payout sustainable?" },
    { key: 'governance', title: 'Governance & Risk',
      desc: "India-specific: promoter conduct, pledge, institutional confidence." },
    { key: 'meta',       title: 'Profile',
      desc: "Size and classification — affects which playbook applies." },
  ];

  // Tab state — the metric guide is paginated by axis for readability.
  activeAxis = signal<string>('value');

  rawEntries = computed(() => {
    const raw = this.row?.snow?.raw;
    if (!raw) return [];
    return Object.entries(raw).filter(([_, v]) => v !== '' && v != null);
  });

  // ─── Helpers exposed to the template ────────────────────────────────────
  fmtValue(value: number | null, unit?: string): string {
    if (value == null || isNaN(value)) return '—';
    // For very large numbers (revenue, market cap), display compactly.
    const abs = Math.abs(value);
    let s: string;
    if (abs >= 1_00_00_000) s = (value / 1_00_00_000).toFixed(2) + ' Cr';
    else if (abs >= 1_00_000) s = (value / 1_00_000).toFixed(2) + ' L';
    else if (abs >= 1) s = value.toFixed(2);
    else s = value.toFixed(4);
    return unit && unit !== 'Cr' ? `${s}${unit}` : s;
  }

  toneClass(tone: string | undefined): string {
    return `tone-${tone ?? 'grey'}`;
  }

  axisTrackable(axisKey: string): boolean {
    const ax = this.row?.snow?.axes?.[axisKey];
    return !!ax?.trackable;
  }

  /** Set when a chip was clicked — used to highlight the matched metric. */
  highlightedMetric = signal<string>('');

  /**
   * Click handler for a strength/warning/red-flag chip. Matches the chip
   * text against the metric guide entries and, on hit:
   *   • switches the metric-guide tab to that axis,
   *   • scrolls to the metric,
   *   • briefly highlights it so the user can see where it landed.
   */
  jumpToMetric(text: string) {
    if (!text) return;
    const target = this._findMetricForText(text);
    if (!target) return;
    // Switch to the right axis tab
    this.activeAxis.set(target.spec.axis);
    this.highlightedMetric.set(target.key);
    // Wait a frame so the *content of that tab is rendered, then scroll.
    setTimeout(() => {
      const el = document.querySelector(`[data-metric-key="${target.key}"]`);
      if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
      // Clear highlight after a moment
      setTimeout(() => this.highlightedMetric.set(''), 2200);
    }, 60);
  }

  /**
   * Match a chip's free-form text ("ROCE 28% — exceptional capital efficiency",
   * "Promoter pledge 35% — distress signal") to the corresponding metric in
   * METRIC_GUIDE. Uses simple keyword matching — robust to wording tweaks in
   * the backend's _explain() output.
   */
  private _findMetricForText(text: string): { key: string; spec: MetricSpec } | null {
    const lower = text.toLowerCase();
    // Hand-curated mapping of keywords → metric key. Order matters — most
    // specific phrases first so e.g. "5Y EPS CAGR" wins over plain "EPS".
    const map: Array<[RegExp, string]> = [
      [/operating cash flow.*net income|ocf\s*[/÷]\s*(?:ni|net income)/i, 'Operating Cash Flow'],
      [/operating cash flow/i,        'Operating Cash Flow'],
      [/free cash flow/i,             'Free Cash Flow'],
      [/interest coverage/i,          'Interest Coverage Ratio'],
      [/debt[ /]to[ /]equity|debt[ /]equity/i, 'Debt to Equity'],
      [/quick ratio/i,                'Quick Ratio'],
      [/current ratio/i,              'Current Ratio'],
      [/5y ?eps cagr|5y historical eps growth/i,       '5Y Historical EPS Growth'],
      [/5y ?revenue cagr|5y historical revenue growth/i, '5Y Historical Revenue Growth'],
      [/forward revenue growth/i,     '1Y Forward Revenue Growth'],
      [/forward eps growth/i,         '1Y Forward EPS Growth'],
      [/ebitda margin/i,              'EBITDA Margin'],
      [/5y avg net profit margin/i,   '5Y Avg Net Profit Margin'],
      [/net (?:profit )?margin/i,     'Net Profit Margin'],
      [/5y avg roe|5y avg return on equity/i, '5Y Avg Return on Equity'],
      [/roce/i,                       'ROCE'],
      [/return on equity|^roe/i,      'Return on Equity'],
      [/promoter pledge|pledged promoter|pledge/i, 'Pledged Promoter Holdings'],
      [/promoter holding/i,           'Promoter Holding'],
      [/foreign institutional|\bfii\b/i, 'Foreign Institutional Holding'],
      [/domestic institutional|\bdii\b/i, 'Domestic Institutional Holding'],
      [/dividend yield/i,             'Dividend Yield'],
      [/payout ratio/i,               'Payout Ratio'],
      [/forward pe/i,                 'Forward PE Ratio'],
      [/pe (?:ratio)?\s*\d/i,         'PE Ratio'],
      [/\bpe\b/i,                     'PE Ratio'],
      [/ev[ /]?ebitda/i,              'EV/EBITDA Ratio'],
      [/\bpb\b|price to book/i,       'PB Ratio'],
      [/\bps\b|price to sales/i,      'PS Ratio'],
      [/market cap/i,                 'Market Cap'],
    ];
    for (const [rx, key] of map) {
      if (rx.test(lower)) {
        const spec = METRIC_GUIDE[key];
        if (spec) return { key, spec };
      }
    }
    return null;
  }

  /**
   * Pre-compute the numeric range string for each band ("0–15", "15–30", "30–60", "≥60")
   * so the band bar can label its segments. Uses the metric's unit if any.
   */
  bandSegments(spec: MetricSpec): { label: string; range: string; tone: string }[] {
    const unit = spec.unit ?? '';
    const out: { label: string; range: string; tone: string }[] = [];
    let prev: number | null = null;
    for (let i = 0; i < spec.bands.length; i++) {
      const b = spec.bands[i];
      const lo = prev;
      const hi = b.to;
      let range: string;
      if (hi === Infinity)            range = lo == null ? `any` : `≥ ${lo}${unit}`;
      else if (lo == null)            range = `< ${hi}${unit}`;
      else if (hi - lo < 0.01)        range = `${lo}${unit}`;
      else                            range = `${lo}–${hi}${unit}`;
      out.push({ label: b.label, range, tone: b.tone });
      prev = hi;
    }
    return out;
  }
}
