import { Component, EventEmitter, Input, Output, signal } from '@angular/core';
import { CommonModule } from '@angular/common';

/**
 * The two chart forms the expenses screens need, as small standalone components.
 *
 * Forms follow the job of the data:
 *   • magnitude across categories / people  → horizontal bars, ONE hue
 *     (length carries the magnitude; colour is identity only for people)
 *   • money in vs out over the statement    → diverging columns on one shared
 *     scale, above/below a single zero line (never two axes)
 *
 * Both are click-to-filter: the chart is the filter UI, not a picture beside it.
 */

// ── palette ────────────────────────────────────────────────────────────────────
// Validated with the dataviz validator (light, surface #fff): all four people pass
// the lightness band, chroma floor, CVD separation (worst adjacent ΔE 13.3 deutan)
// and the normal-vision floor (worst 22.9). The app's original Sanjeev violet
// (#9b6dd6) sat ΔE 11 from Ranjeev's blue — indistinguishable in a chart — so it
// was re-stepped to #7c3fbf (same violet, one step deeper: normal ΔE 18.9). Orange/aqua fall under 3:1 vs white, which is why
// every bar here is directly labelled (the required relief), never colour-alone.
export const PERSON_COLORS: Record<string, string> = {
  Ranjeev: '#5b8def',
  Sanjeev: '#7c3fbf',
  Ramprasad: '#f0883e',
  Maha: '#20c4a8',
};
export const UNASSIGNED_COLOR = '#9aa0b5';
/** money out / money in — passes every check as a pair (ΔE 26.1 normal, 9.9 protan) */
export const OUT_COLOR = '#e0654f';
export const IN_COLOR = '#16a085';

export function personColor(name: string | null | undefined): string {
  return PERSON_COLORS[(name || '').trim()] || UNASSIGNED_COLOR;
}

/**
 * Categorical hues for the category donut, in fixed order.
 * Validated (light, #fff): lightness band PASS, chroma floor PASS, worst adjacent
 * CVD ΔE 9.9 (protan) PASS, normal-vision floor 20.0 PASS. The amber sits at 2.89:1
 * against white (WARN) — relieved by the legend, which labels every slice with its
 * name and amount, so identity is never colour-alone.
 */
export const CAT_COLORS = [
  '#387ed1', '#e0654f', '#16a085', '#c9891a', '#7c3fbf', '#d9558f', '#6b8f2f',
];
/** designated neutral — "Other"/"Uncategorised", deliberately outside the rotation */
export const NEUTRAL_COLOR = '#8a90a8';

/**
 * A category's colour, taken from its position in your *whole* category list —
 * not from its rank in the current chart. Filtering the table therefore never
 * repaints the slices that survive.
 */
export function categoryColor(name: string, vocabulary: string[]): string {
  const k = (name || '').trim().toLowerCase();
  if (!k || k === 'uncategorised' || k === 'other' || k.startsWith('other (')) return NEUTRAL_COLOR;
  const i = vocabulary.findIndex(c => c.trim().toLowerCase() === k);
  if (i >= 0) return CAT_COLORS[i % CAT_COLORS.length];
  // not in the list yet (Claude invented it) — stable hash so it keeps its hue
  let h = 0;
  for (let j = 0; j < k.length; j++) h = (h * 31 + k.charCodeAt(j)) % 100003;
  return CAT_COLORS[h % CAT_COLORS.length];
}

export interface BarItem {
  key: string;
  label: string;
  value: number;
  count?: number;
  color?: string;        // identity charts only; omit for a single-hue magnitude chart
}

export interface DayFlow { date: string; out: number; in: number; }

function inr(v: number): string {
  const a = Math.abs(v);
  if (a >= 1e7) return '₹' + (a / 1e7).toFixed(2) + ' Cr';
  if (a >= 1e5) return '₹' + (a / 1e5).toFixed(2) + ' L';
  if (a >= 1000) return '₹' + Math.round(a).toLocaleString('en-IN');
  return '₹' + Math.round(a).toLocaleString('en-IN');
}

// ── horizontal bars ────────────────────────────────────────────────────────────
@Component({
  selector: 'app-bar-breakdown',
  standalone: true,
  imports: [CommonModule],
  template: `
    <div class="bb">
      @for (b of shownList(); track b.key) {
        <button class="bb-row" type="button"
                [class.on]="selected === b.key" [class.off]="selected && selected !== b.key"
                (click)="pick.emit(selected === b.key ? null : b.key)"
                [attr.aria-pressed]="selected === b.key"
                [title]="b.label + ' · ' + fmt(b.value) + (b.count ? ' · ' + b.count + ' rows' : '')">
          <span class="bb-label">{{ b.label }}</span>
          <span class="bb-track">
            <i class="bb-fill" [style.width.%]="pct(b.value)" [style.background]="b.color || accent"></i>
          </span>
          <span class="bb-val">{{ fmt(b.value) }}</span>
          <span class="bb-count">{{ b.count ?? '' }}</span>
        </button>
      } @empty {
        <div class="bb-none">Nothing to show.</div>
      }
    </div>
  `,
  styles: [`
    :host { display: block; }
    .bb { display: flex; flex-direction: column; gap: 2px; }          /* 2px surface gap */
    .bb-row { display: grid; grid-template-columns: 108px 1fr auto 26px; align-items: center; gap: 9px;
      background: none; border: 0; padding: 3px 4px; border-radius: 7px; cursor: pointer; text-align: left;
      font: inherit; transition: background .12s, opacity .12s; }
    .bb-row:hover { background: var(--nw-card2, #f5f7fb); }
    .bb-row.on { background: var(--nw-card2, #f5f7fb); }
    .bb-row.off { opacity: .42; }
    .bb-label { font-size: 11.5px; font-weight: 600; color: var(--nw-text, #1a1c2e);
      white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .bb-track { height: 10px; border-radius: 999px; background: #eef0f6; overflow: hidden; }
    .bb-fill { display: block; height: 100%; border-radius: 0 4px 4px 0; min-width: 3px; transition: width .25s ease; }
    .bb-val { font-size: 11.5px; font-weight: 800; color: var(--nw-text, #1a1c2e); font-variant-numeric: tabular-nums; }
    .bb-count { font-size: 10.5px; color: var(--nw-muted, #6b7190); text-align: right; font-variant-numeric: tabular-nums; }
    .bb-none { font-size: 12px; color: var(--nw-muted, #6b7190); padding: 8px 4px; }
  `],
})
export class BarBreakdown {
  @Input() items: BarItem[] = [];
  @Input() accent = OUT_COLOR;
  @Input() topN = 8;
  @Input() selected: string | null = null;
  @Output() pick = new EventEmitter<string | null>();

  /** biggest first, tail folded into one "Other" row — never a new hue */
  get sorted(): BarItem[] {
    return [...(this.items || [])].filter(i => i.value > 0).sort((a, b) => b.value - a.value);
  }
  shownList(): BarItem[] {
    const s = this.sorted;
    if (s.length <= this.topN) return s;
    const head = s.slice(0, this.topN - 1);
    const tail = s.slice(this.topN - 1);
    head.push({
      key: '__other__', label: `Other (${tail.length})`,
      value: tail.reduce((t, x) => t + x.value, 0),
      count: tail.reduce((t, x) => t + (x.count || 0), 0),
    });
    return head;
  }
  pct(v: number): number {
    const max = Math.max(...this.shownList().map(x => x.value), 1);
    return Math.max(2, (v / max) * 100);
  }
  fmt(v: number): string { return inr(v); }
}

// ── vertical column breakdown ────────────────────────────────────────────────
/**
 * The same magnitude-by-identity job as the horizontal bars, but as upright
 * columns — the shape the user asked for on "who spends what". Each column is one
 * person, height = ₹ spend, coloured in that person's identity hue and directly
 * labelled with name + amount (colour is never the only cue). Click a column to
 * filter; the selected one lifts, the rest dim.
 */
interface Col { key: string; label: string; value: number; count: number; color: string;
  x: number; y: number; w: number; h: number; }

@Component({
  selector: 'app-column-breakdown',
  standalone: true,
  imports: [CommonModule],
  template: `
    <div class="cb2">
      <svg [attr.viewBox]="'0 0 ' + W + ' ' + H" class="cb2-svg" role="img"
           [attr.aria-label]="'Spend by person'" (mouseleave)="hoverKey.set(null)">
        <!-- gridlines + axis ticks, so bar heights read against a scale -->
        @for (g of grid(); track g.v) {
          <line [attr.x1]="PAD_L" [attr.x2]="W - PAD_R" [attr.y1]="g.y" [attr.y2]="g.y" class="cb2-grid" />
          <text [attr.x]="PAD_L - 8" [attr.y]="g.y + 3.5" class="cb2-tick">{{ fmt(g.v) }}</text>
        }
        <line [attr.x1]="PAD_L" [attr.x2]="W - PAD_R" [attr.y1]="baseY()" [attr.y2]="baseY()" class="cb2-base" />

        @for (c of cols(); track c.key) {
          <g class="cb2-col" [class.on]="selected === c.key" [class.off]="selected && selected !== c.key"
             [class.hov]="hoverKey() === c.key"
             (mouseenter)="hoverKey.set(c.key)"
             (click)="pick.emit(selected === c.key ? null : c.key)">
            <!-- soft track behind each bar -->
            <rect [attr.x]="c.x" [attr.y]="PAD_T" [attr.width]="c.w" [attr.height]="baseY() - PAD_T"
                  rx="7" class="cb2-track" />
            <rect [attr.x]="c.x" [attr.y]="c.y" [attr.width]="c.w" [attr.height]="c.h"
                  [attr.fill]="c.color" rx="7" class="cb2-bar" />
            <!-- value above the bar -->
            <text [attr.x]="c.x + c.w / 2" [attr.y]="c.y - 9" class="cb2-val">{{ fmt(c.value) }}</text>
            <!-- name + count under the baseline -->
            <text [attr.x]="c.x + c.w / 2" [attr.y]="baseY() + 22" class="cb2-name">{{ c.label }}</text>
            <text [attr.x]="c.x + c.w / 2" [attr.y]="baseY() + 39" class="cb2-count">{{ c.count }} item{{ c.count === 1 ? '' : 's' }}</text>
            <!-- full-height hit target -->
            <rect [attr.x]="c.x - gap / 2" [attr.y]="PAD_T" [attr.width]="c.w + gap" [attr.height]="H - PAD_T"
                  fill="transparent" class="cb2-hit" />
          </g>
        }
      </svg>
      @if (!cols().length) { <div class="cb2-none">No spend to show.</div> }
    </div>
  `,
  styles: [`
    :host { display: block; }
    .cb2 { width: 100%; }
    .cb2-svg { width: 100%; height: auto; display: block; overflow: visible;
      font-family: -apple-system, BlinkMacSystemFont, 'Inter', 'Segoe UI', Roboto, sans-serif;
      font-variant-numeric: tabular-nums; }
    .cb2-grid { stroke: #eef0f6; stroke-width: 1; }
    .cb2-base { stroke: #dfe3ee; stroke-width: 1.5; }
    .cb2-tick { fill: #a2a7bb; font-size: 9.5px; font-weight: 600; text-anchor: end; }
    .cb2-col { cursor: pointer; }
    .cb2-track { fill: #f4f6fb; opacity: 0; transition: opacity .12s; }
    .cb2-col.hov .cb2-track, .cb2-col.on .cb2-track { opacity: 1; }
    .cb2-bar { transition: opacity .16s, y .3s ease, height .3s ease, filter .14s; }
    .cb2-col.hov .cb2-bar { filter: brightness(1.05); }
    .cb2-col.on .cb2-bar { filter: brightness(1.04); }
    .cb2-col.off .cb2-bar { opacity: .32; }
    .cb2-col.off .cb2-val, .cb2-col.off .cb2-name, .cb2-col.off .cb2-count { opacity: .4; }
    .cb2-val { fill: #14162a; font-size: 13.5px; font-weight: 800; text-anchor: middle; letter-spacing: -.3px;
      paint-order: stroke; stroke: #fff; stroke-width: 3px; stroke-linejoin: round; }
    .cb2-name { fill: #262a3d; font-size: 13px; font-weight: 700; text-anchor: middle; }
    .cb2-col.on .cb2-name { fill: #14162a; font-weight: 800; }
    .cb2-count { fill: #8b90a6; font-size: 10.5px; font-weight: 600; text-anchor: middle; }
    .cb2-hit { cursor: pointer; }
    .cb2-none { padding: 26px 4px; text-align: center; font-size: 12.5px; color: var(--nw-muted, #6b7190); }
  `],
})
export class ColumnBreakdown {
  @Input() items: BarItem[] = [];
  @Input() accent = OUT_COLOR;
  @Input() topN = 6;
  @Input() selected: string | null = null;
  @Output() pick = new EventEmitter<string | null>();

  hoverKey = signal<string | null>(null);

  readonly W = 560;
  readonly H = 250;
  readonly PAD_T = 26;      // room for the value label above the tallest bar
  readonly PAD_B = 46;      // room for name + count under the baseline
  readonly PAD_L = 46;      // axis tick gutter
  readonly PAD_R = 12;
  readonly gap = 26;        // surface gap between bars
  baseY(): number { return this.H - this.PAD_B; }

  get sorted(): BarItem[] {
    return [...(this.items || [])].filter(i => i.value > 0).sort((a, b) => b.value - a.value);
  }
  shownList(): BarItem[] {
    const s = this.sorted;
    if (s.length <= this.topN) return s;
    const head = s.slice(0, this.topN - 1);
    const tail = s.slice(this.topN - 1);
    head.push({ key: '__other__', label: `Other (${tail.length})`,
      value: tail.reduce((t, x) => t + x.value, 0), count: tail.reduce((t, x) => t + (x.count || 0), 0) });
    return head;
  }
  /** a "nice" round ceiling for the axis, so ticks land on clean numbers */
  private niceMax(): number {
    const m = Math.max(...this.shownList().map(x => x.value), 1);
    const pow = Math.pow(10, Math.floor(Math.log10(m)));
    const n = m / pow;
    const step = n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10;
    return step * pow;
  }
  cols(): Col[] {
    const list = this.shownList();
    if (!list.length) return [];
    const max = this.niceMax();
    const plotH = this.baseY() - this.PAD_T;
    const n = list.length;
    const avail = this.W - this.PAD_L - this.PAD_R;
    const w = Math.min(72, (avail - this.gap * (n - 1)) / n);
    const totalW = w * n + this.gap * (n - 1);
    const startX = this.PAD_L + (avail - totalW) / 2;
    return list.map((b, i) => {
      const h = Math.max(3, (b.value / max) * plotH);
      return { key: b.key, label: b.label, value: b.value, count: b.count || 0,
        color: b.color || this.accent, x: startX + i * (w + this.gap), y: this.baseY() - h, w, h };
    });
  }
  grid(): { v: number; y: number }[] {
    const max = this.niceMax();
    const plotH = this.baseY() - this.PAD_T;
    const steps = 4;
    const out: { v: number; y: number }[] = [];
    for (let i = 1; i <= steps; i++) {
      const v = (max / steps) * i;
      out.push({ v, y: this.baseY() - (v / max) * plotH });
    }
    return out;
  }
  fmt(v: number): string { return inr(v); }
}

// ── category donut ─────────────────────────────────────────────────────────────
interface Arc extends BarItem { d: string; pct: number; color: string; }

/**
 * Part-to-whole for one side of the ledger: which categories make up the spend
 * (or the income). A donut rather than a pie because the hole carries the total,
 * which is the number you actually want beside the slices.
 *
 * Every slice is named and valued in the legend — colour is a lookup aid, never
 * the only way to tell two slices apart.
 */
@Component({
  selector: 'app-donut-breakdown',
  standalone: true,
  imports: [CommonModule],
  template: `
    <div class="dn">
      <div class="dn-ring">
        <svg viewBox="0 0 200 200" class="dn-svg" role="img"
             [attr.aria-label]="'Breakdown by category'" (mouseleave)="hover.set(null)">
          @if (arcs().length === 1) {
            <circle cx="100" cy="100" [attr.r]="R" fill="none"
                    [attr.stroke]="arcs()[0].color" [attr.stroke-width]="SW" />
          } @else {
            @for (a of arcs(); track a.key) {
              <path [attr.d]="a.d" fill="none" [attr.stroke]="a.color" [attr.stroke-width]="SW"
                    stroke-linecap="butt" class="dn-arc"
                    [class.dim]="hover() && hover() !== a.key"
                    [class.sel]="selected === a.key"
                    (mouseenter)="hover.set(a.key)"
                    (click)="pick.emit(selected === a.key ? null : a.key)" />
            }
          }
          <text x="100" y="94" class="dn-c-val">{{ fmt(focus().value) }}</text>
          <text x="100" y="112" class="dn-c-key">{{ focus().label }}</text>
          @if (focus().count) {
            <text x="100" y="127" class="dn-c-sub">{{ focus().count }} transactions</text>
          }
        </svg>
      </div>

      <ul class="dn-leg">
        @for (a of arcs(); track a.key) {
          <li>
            <button type="button" class="dn-li"
                    [class.on]="selected === a.key" [class.dim]="hover() && hover() !== a.key"
                    (mouseenter)="hover.set(a.key)" (mouseleave)="hover.set(null)"
                    (click)="pick.emit(selected === a.key ? null : a.key)"
                    [title]="a.label + ' · ' + fmt(a.value) + ' · ' + a.pct.toFixed(0) + '%'">
              <i class="dn-chip" [style.background]="a.color"></i>
              <span class="dn-name">{{ a.label }}</span>
              <span class="dn-val">{{ fmt(a.value) }}</span>
              <span class="dn-pct">{{ pctLabel(a.pct) }}</span>
            </button>
          </li>
        } @empty {
          <li class="dn-none">Nothing to show.</li>
        }
      </ul>
    </div>
  `,
  styles: [`
    :host { display: block; }
    .dn { display: flex; align-items: center; gap: 16px; }
    .dn-ring { flex: 0 0 auto; width: 166px; }
    .dn-svg { width: 100%; height: auto; display: block; overflow: visible;
      font-family: -apple-system, BlinkMacSystemFont, 'Inter', 'Segoe UI', Roboto, sans-serif;
      font-variant-numeric: tabular-nums; }
    .dn-arc { transition: opacity .15s, stroke-width .15s; cursor: pointer; }
    .dn-arc.dim { opacity: .28; }
    .dn-arc.sel { stroke-width: 30; }
    .dn-c-val { text-anchor: middle; font-size: 21px; font-weight: 800; fill: #14162a; letter-spacing: -.6px; }
    .dn-c-key { text-anchor: middle; font-size: 10.5px; font-weight: 700; fill: #4b5170;
      letter-spacing: .01em; }
    .dn-c-sub { text-anchor: middle; font-size: 9.5px; font-weight: 600; fill: #8b90a6; }

    .dn-leg { flex: 1 1 auto; min-width: 0; list-style: none; margin: 0; padding: 0;
      display: flex; flex-direction: column; gap: 1px; }
    .dn-li { width: 100%; display: grid; grid-template-columns: 9px 1fr auto 34px; align-items: center;
      gap: 8px; background: none; border: 0; padding: 3px 5px; border-radius: 6px; cursor: pointer;
      text-align: left; font: inherit; transition: background .12s, opacity .12s; }
    .dn-li:hover, .dn-li.on { background: #f3f5fa; }
    .dn-li.dim { opacity: .45; }
    .dn-chip { width: 9px; height: 9px; border-radius: 3px; display: block; }
    .dn-name { font-size: 11.5px; font-weight: 600; color: #262a3d; white-space: nowrap;
      overflow: hidden; text-overflow: ellipsis; }
    .dn-val { font-size: 11.5px; font-weight: 700; color: #14162a; font-variant-numeric: tabular-nums; }
    .dn-pct { font-size: 10.5px; font-weight: 600; color: #8b90a6; text-align: right;
      font-variant-numeric: tabular-nums; }
    .dn-none { font-size: 12px; color: #8b90a6; padding: 6px 4px; }
    @media (max-width: 1180px) { .dn { flex-direction: column; align-items: stretch; gap: 10px; }
      .dn-ring { width: 150px; margin: 0 auto; } }
  `],
})
export class DonutBreakdown {
  @Input() items: BarItem[] = [];
  /** what the hole says when nothing is hovered */
  @Input() totalLabel = 'Total';
  @Input() topN = 7;
  @Input() selected: string | null = null;
  @Output() pick = new EventEmitter<string | null>();

  readonly R = 76;
  readonly SW = 26;
  private readonly GAP_RAD = 0.035;        // ≈2px of surface between slices
  hover = signal<string | null>(null);

  private shown(): BarItem[] {
    const s = [...(this.items || [])].filter(i => i.value > 0).sort((a, b) => b.value - a.value);
    if (s.length <= this.topN) return s;
    const tail = s.slice(this.topN - 1);
    return [...s.slice(0, this.topN - 1), {
      key: '__other__', label: `Other (${tail.length})`,
      value: tail.reduce((t, x) => t + x.value, 0),
      count: tail.reduce((t, x) => t + (x.count || 0), 0),
      color: NEUTRAL_COLOR,
    }];
  }

  total(): number { return this.shown().reduce((t, x) => t + x.value, 0); }

  arcs(): Arc[] {
    const list = this.shown();
    const total = this.total() || 1;
    let a0 = -Math.PI / 2;
    return list.map(s => {
      const sweep = (s.value / total) * Math.PI * 2;
      const gap = Math.min(sweep * 0.3, this.GAP_RAD);
      const a1 = a0 + sweep;
      const arc: Arc = {
        ...s, color: s.color || NEUTRAL_COLOR,
        pct: (s.value / total) * 100,
        d: this.arcPath(a0 + gap / 2, a1 - gap / 2),
      };
      a0 = a1;
      return arc;
    });
  }

  private arcPath(a0: number, a1: number): string {
    const cx = 100, cy = 100, r = this.R;
    const large = a1 - a0 > Math.PI ? 1 : 0;
    return `M${(cx + r * Math.cos(a0)).toFixed(2)},${(cy + r * Math.sin(a0)).toFixed(2)} `
      + `A${r},${r} 0 ${large} 1 ${(cx + r * Math.cos(a1)).toFixed(2)},${(cy + r * Math.sin(a1)).toFixed(2)}`;
  }

  /** hovered slice, else the whole */
  focus(): { label: string; value: number; count: number } {
    const h = this.hover() || this.selected;
    const hit = h ? this.arcs().find(a => a.key === h) : null;
    if (hit) return { label: hit.label, value: hit.value, count: hit.count || 0 };
    return {
      label: this.totalLabel, value: this.total(),
      count: this.shown().reduce((t, x) => t + (x.count || 0), 0),
    };
  }
  /** built here, not in the template: a bare "<" inside an Angular binding ends
   *  the element early and silently drops the rest of the template */
  pctLabel(pct: number): string {
    return (pct > 0 && pct < 1 ? '<1' : pct.toFixed(0)) + '%';
  }
  fmt(v: number): string { return inr(v); }
}

// ── diverging day columns ──────────────────────────────────────────────────────
@Component({
  selector: 'app-flow-columns',
  standalone: true,
  imports: [CommonModule],
  template: `
    <div class="fc-wrap">
      <div class="fc-legend">
        <span><i [style.background]="OUT"></i>Out</span>
        <span><i [style.background]="IN"></i>In</span>
        @if (hover() !== null && day(hover()!); as d) {
          <em class="fc-read">{{ dayLabel(d.date) }} · <b class="out">{{ fmt(d.out) }}</b> out@if (d.in) { · <b class="in">{{ fmt(d.in) }}</b> in }</em>
        } @else {
          <em class="fc-hint">hover a day · click to filter</em>
        }
      </div>
      <svg [attr.viewBox]="'0 0 ' + W + ' ' + H" class="fc-svg" role="img"
           [attr.aria-label]="'Money in and out by day'" (mouseleave)="hover.set(null)">
        <!-- zero baseline -->
        <line [attr.x1]="PAD" [attr.x2]="W - PAD" [attr.y1]="zeroY()" [attr.y2]="zeroY()"
              stroke="#dfe3ee" stroke-width="1" />
        @for (c of cols(); track c.date) {
          <g [class.dim]="hover() !== null && hover() !== c.i" [class.sel]="selected === c.date">
            @if (c.outH > 0) {
              <path [attr.d]="barPath(c.x, zeroY(), c.outH, false)" [attr.fill]="OUT" />
            }
            @if (c.inH > 0) {
              <path [attr.d]="barPath(c.x, zeroY(), c.inH, true)" [attr.fill]="IN" />
            }
            <!-- hit target: the whole column slot, not just the painted pixels -->
            <rect [attr.x]="c.x - gap() / 2" y="0" [attr.width]="slot()" [attr.height]="H"
                  fill="transparent" (mouseenter)="hover.set(c.i)"
                  (click)="pick.emit(selected === c.date ? null : c.date)" class="fc-hit" />
          </g>
        }
        <!-- direct labels: only the biggest day each way -->
        @if (peakOut(); as p) {
          <text [attr.x]="p.x + bw() / 2" [attr.y]="outLabelY(p.outH)" class="fc-lab out">{{ fmt(p.out) }}</text>
        }
        @if (peakIn(); as p) {
          <text [attr.x]="p.x + bw() / 2" [attr.y]="zeroY() - p.inH - 4" class="fc-lab in">{{ fmt(p.in) }}</text>
        }
        @for (t of ticks(); track t.date) {
          <text [attr.x]="t.x" [attr.y]="H - 2" class="fc-tick">{{ dayLabel(t.date) }}</text>
        }
      </svg>
    </div>
  `,
  styles: [`
    :host { display: block; }
    .fc-wrap { display: flex; flex-direction: column; gap: 4px; }
    .fc-legend { display: flex; align-items: center; gap: 12px; font-size: 11px; color: var(--nw-muted, #6b7190);
      span { display: inline-flex; align-items: center; gap: 5px; font-weight: 700; }
      i { width: 9px; height: 9px; border-radius: 2px; display: inline-block; }
      .fc-read { margin-left: auto; font-style: normal; font-weight: 600;
        b { font-weight: 800; } b.out { color: #e0654f; } b.in { color: #16a085; } }
      .fc-hint { margin-left: auto; font-style: normal; } }
    .fc-svg { width: 100%; height: auto; display: block; overflow: visible; }
    .fc-svg g { transition: opacity .12s; }
    .fc-svg g.dim { opacity: .35; }
    .fc-svg g.sel path { stroke: #1a1c2e; stroke-width: 1.2; }
    .fc-hit { cursor: pointer; }
    .fc-svg text { font-family: -apple-system, BlinkMacSystemFont, 'Inter', 'Segoe UI', Roboto, sans-serif;
      font-variant-numeric: tabular-nums; }
    .fc-lab { font-size: 9px; font-weight: 700; text-anchor: middle; }
    .fc-lab.out { fill: #b8452f; } .fc-lab.in { fill: #0f7d68; }
    .fc-tick { font-size: 8.5px; fill: #8b90a6; text-anchor: middle; }
  `],
})
export class FlowColumns {
  @Input() days: DayFlow[] = [];
  @Input() selected: string | null = null;
  /** panel-sized: a smaller viewBox so the same type reads bigger once scaled */
  @Input() compact = false;
  @Output() pick = new EventEmitter<string | null>();

  get W(): number { return this.compact ? 430 : 720; }
  get H(): number { return this.compact ? 132 : 148; }
  readonly PAD = 6;
  private get PLOT(): number { return this.compact ? 92 : 104; }   // leaves a band for the value labels + date ticks          // drawing height, leaves room for the date ticks
  readonly OUT = OUT_COLOR;
  readonly IN = IN_COLOR;
  hover = signal<number | null>(null);

  slot(): number { return (this.W - this.PAD * 2) / Math.max(1, this.days.length); }
  gap(): number { return Math.min(4, this.slot() * 0.25); }                 // ≥2px surface gap
  bw(): number { return Math.max(2, Math.min(24, this.slot() - this.gap())); }

  /** ONE scale for both directions — the zero line just sits where it must. */
  private scale(): number {
    const mo = Math.max(...this.days.map(d => d.out), 0);
    const mi = Math.max(...this.days.map(d => d.in), 0);
    const span = mo + mi;
    return span > 0 ? this.PLOT / span : 0;
  }
  zeroY(): number {
    const mi = Math.max(...this.days.map(d => d.in), 0);
    return 14 + mi * this.scale();      // 14 = room for the 'in' peak label above
  }
  cols() {
    const s = this.scale();
    return this.days.map((d, i) => ({
      i, date: d.date, out: d.out, in: d.in,
      x: this.PAD + i * this.slot() + this.gap() / 2,
      outH: d.out * s, inH: d.in * s,
    }));
  }
  /** square at the baseline, 4px rounded at the data end */
  barPath(x: number, zero: number, h: number, up: boolean): string {
    const w = this.bw();
    const r = Math.min(4, w / 2, h);
    return up
      ? `M${x},${zero} v${-(h - r)} q0,${-r} ${r},${-r} h${w - 2 * r} q${r},0 ${r},${r} v${h - r} z`
      : `M${x},${zero} v${h - r} q0,${r} ${r},${r} h${w - 2 * r} q${r},0 ${r},${-r} v${-(h - r)} z`;
  }
  /** below the column end, but never on top of the date ticks */
  outLabelY(h: number): number { return Math.min(this.zeroY() + h + 11, this.H - 15); }
  peakOut() { const c = this.cols().filter(x => x.outH > 8); return c.sort((a, b) => b.out - a.out)[0] || null; }
  peakIn() { const c = this.cols().filter(x => x.inH > 8); return c.sort((a, b) => b.in - a.in)[0] || null; }
  ticks() {
    const c = this.cols();
    if (!c.length) return [];
    const step = Math.max(1, Math.ceil(c.length / 8));
    return c.filter((_, i) => i % step === 0 || i === c.length - 1)
      .map(x => ({ date: x.date, x: x.x + this.bw() / 2 }));
  }
  day(i: number): DayFlow | null { return this.days[i] || null; }
  dayLabel(iso: string): string {
    const d = new Date(iso + 'T00:00:00');
    return isNaN(d.getTime()) ? iso : d.toLocaleDateString('en-IN', { day: 'numeric', month: 'short' });
  }
  fmt(v: number): string { return inr(v); }
}

// ── Sankey: where the money came from and where it went ────────────────────────
export interface SankeyNode {
  id: string; label: string; value: number;
  col: number; color: string;
  kind?: 'in' | 'hub' | 'out' | 'kept';
  clickable?: boolean;
}
export interface SankeyLink { from: string; to: string; value: number; }

interface LaidNode extends SankeyNode { x: number; y: number; h: number; sy: number; ty: number; ly: number; }
interface LaidLink extends SankeyLink { d: string; color: string; key: string; hlD?: string; hlColor?: string; }
/** one person's share of an out-category link, to paint as an inset highlight band */
export interface SankeyHighlight { key: string; frac: number; color: string; }

/**
 * A money-flow diagram: sources on the left, the pool in the middle, where it
 * went on the right. Laid out by hand (no chart library) so it stays inline SVG
 * and matches the rest of the app.
 *
 * Colour is semantic, not decorative — money in is green, the pool is the app
 * accent, money out is warm. Every node is directly labelled with its name and
 * amount, so identity never rests on colour alone.
 */
@Component({
  selector: 'app-sankey-flow',
  standalone: true,
  imports: [CommonModule],
  template: `
    <div class="sk-wrap" [class.compact]="compact" [class.wide]="wide">
      <svg [attr.viewBox]="'0 0 ' + W + ' ' + H" class="sk-svg" role="img"
           [attr.aria-label]="'Money flow: sources, pool, and where it went'"
           (mouseleave)="hover.set(null)">
        @for (l of links(); track l.key) {
          <path [attr.d]="l.d" [attr.fill]="l.color"
                [class.dim]="(hover() && hover() !== l.from && hover() !== l.to) || (hasHighlight() && !l.hlD)"
                [class.faded]="hasHighlight() && !!l.hlD"
                [class.lit]="hover() === l.from || hover() === l.to" class="sk-link" />
          @if (l.hlD) {
            <path [attr.d]="l.hlD" [attr.fill]="l.hlColor" class="sk-hl" />
          }
        }
        @for (n of nodes(); track n.id) {
          <g class="sk-node" [class.dim]="hover() && hover() !== n.id && !touches(n.id)"
             (mouseenter)="hover.set(n.id)"
             (click)="n.clickable ? pick.emit(n.id) : null"
             [class.click]="n.clickable">
            <rect [attr.x]="n.x" [attr.y]="n.y" [attr.width]="NODE_W" [attr.height]="n.h"
                  [attr.fill]="n.color" rx="2" />
            @if (isSide(n)) {
              <!-- outer columns: one line beside the bar, so tiny nodes never collide -->
              <text [attr.x]="labelX(n)" [attr.y]="n.ly" [attr.text-anchor]="anchor(n)" class="sk-name">
                {{ short(n.label) }} <tspan class="sk-val" dx="5">{{ fmt(n.value) }}</tspan>
              </text>
            } @else {
              <!-- middle columns: name over value, above the bar -->
              <text [attr.x]="n.x" [attr.y]="n.ly" class="sk-name hub">{{ n.label }}</text>
              <text [attr.x]="n.x" [attr.y]="n.ly + 15" class="sk-val hub">{{ fmt(n.value) }}</text>
            }
          </g>
        }
      </svg>
    </div>
  `,
  styles: [`
    :host { display: block; }
    .sk-wrap { width: 100%; overflow-x: auto; }
    .sk-svg { width: 100%; min-width: 520px; height: auto; display: block; overflow: visible; }
    /* SVG text does not inherit the page font unless told to — without this the
       labels render in the browser's default face while the rest of the app is
       -apple-system/Inter, which is most of why they looked wrong. */
    .sk-svg text { font-family: -apple-system, BlinkMacSystemFont, 'Inter', 'Segoe UI', Roboto, sans-serif;
      font-variant-numeric: tabular-nums; }
    /* a thin surface halo behind every label, so a label that has to sit over a
       ribbon still reads cleanly instead of looking like an overlap */
    .sk-svg text { paint-order: stroke fill; stroke: #fff; stroke-width: 3.2px;
      stroke-linejoin: round; }
    .sk-wrap.compact .sk-name { font-size: 11.5px; }
    .sk-wrap.compact .sk-val, .sk-wrap.compact tspan.sk-val { font-size: 10.5px; }
    .sk-link { opacity: .34; transition: opacity .15s; }
    .sk-link.lit { opacity: .7; }
    .sk-link.dim { opacity: .1; }
    /* when a person is highlighted: their ribbons fade to a soft base, the rest go faint */
    .sk-link.faded { opacity: .16; }
    .sk-hl { opacity: .92; transition: opacity .15s; }
    .sk-node { transition: opacity .15s; }
    .sk-node.dim { opacity: .4; }
    .sk-node.click { cursor: pointer; }
    .sk-name { font-size: 12px; font-weight: 600; fill: #262a3d; letter-spacing: -.1px; }
    .sk-val { font-size: 11.5px; font-weight: 600; fill: #6a7089; letter-spacing: -.1px; }
    tspan.sk-val { font-size: 11.5px; }
    /* the two totals in the middle are the headline of the diagram */
    .sk-name.hub { font-size: 12.5px; font-weight: 800; fill: #14162a; }
    .sk-val.hub { font-size: 12.5px; font-weight: 700; fill: #4b5170; }
  `],
})
export class SankeyFlow {
  @Input() nodeSpec: SankeyNode[] = [];
  @Input() linkSpec: SankeyLink[] = [];
  @Input() height = 460;
  /** panel-sized: smaller frame so the same type reads bigger once scaled down */
  @Input() compact = false;
  /** full-bleed: a wide viewBox so the SVG renders ~1:1 across a whole row and the
   *  height input is the height you actually get (it scales with width, not against it) */
  @Input() wide = false;
  /** highlight one person's share of each out-category link (dims the rest); the
   *  flow shape/totals never change — this only paints an inset band per ribbon */
  @Input() highlight: SankeyHighlight[] = [];
  @Output() pick = new EventEmitter<string>();

  private hlMap(): Map<string, SankeyHighlight> { return new Map(this.highlight.map(h => [h.key, h])); }
  hasHighlight(): boolean { return this.highlight.length > 0; }

  /** The viewBox width is chosen to land near 1:1 with the rendered width, so the
   *  label sizes above are the sizes you actually see. `wide` = a two-thirds-of-the
   *  -row panel (~900px), not the full row. */
  get W(): number { return this.compact ? 760 : this.wide ? 900 : 1000; }
  get NODE_W(): number { return this.compact ? 9 : 11; }
  /** room for the middle column's two-line label above the tallest bar */
  get PAD_Y(): number { return this.compact ? 26 : 40; }
  get GAP(): number { return this.compact ? 11 : 15; }        // surface gap between stacked nodes
  get PAD_L(): number { return this.compact ? 156 : this.wide ? 176 : 150; }   // side-label gutters
  get PAD_R(): number { return this.compact ? 150 : this.wide ? 172 : 150; }
  private get LABEL_STEP(): number { return this.compact ? 13 : 16; }
  hover = signal<string | null>(null);

  get H(): number { return this.height; }

  private cols(): number[] {
    return [...new Set(this.nodeSpec.map(n => n.col))].sort((a, b) => a - b);
  }
  private lastCol(): number { const cs = this.cols(); return cs[cs.length - 1] ?? 0; }
  private colX(col: number): number {
    const usable = this.W - this.PAD_L - this.PAD_R - this.NODE_W;
    return this.PAD_L + (col / (this.lastCol() || 1)) * usable;
  }
  /** outer columns get their label beside the bar (there are many, often tiny) */
  isSide(n: SankeyNode): boolean { return n.col === 0 || n.col === this.lastCol(); }

  /** one scale for every column: the tallest column fills the plot */
  private scale(): number {
    const plot = this.H - this.PAD_Y - 12;
    let worst = 1;
    for (const c of this.cols()) {
      const inCol = this.nodeSpec.filter(n => n.col === c);
      const total = inCol.reduce((t, n) => t + n.value, 0);
      const usable = plot - this.GAP * Math.max(0, inCol.length - 1);
      if (total > 0 && usable > 0) worst = Math.max(worst, total / usable);
    }
    return 1 / worst;
  }

  nodes(): LaidNode[] {
    const s = this.scale();
    const out: LaidNode[] = [];
    for (const c of this.cols()) {
      let y = this.PAD_Y;
      const col: LaidNode[] = [];
      for (const n of this.nodeSpec.filter(x => x.col === c)) {
        const h = Math.max(2, n.value * s);
        col.push({ ...n, x: this.colX(c), y, h, sy: y, ty: y, ly: y + h / 2 + 4 });
        y += h + this.GAP;
      }
      if (!col.length) { continue; }
      if (this.isSide(col[0])) {
        // Side labels are centred on their node, then relaxed apart so two of them
        // can never overlap (tiny nodes sit ~2px apart). A single downward pass can
        // run off the bottom of the frame, so it is followed by an upward pass that
        // pins the last label inside the frame and pushes the pile back up.
        const step = this.LABEL_STEP;
        let prev = -Infinity;
        for (const n of col) { n.ly = Math.max(n.ly, prev + step); prev = n.ly; }
        let limit = this.H - 5;
        for (let i = col.length - 1; i >= 0; i--) {
          col[i].ly = Math.min(col[i].ly, limit);
          limit = col[i].ly - step;
        }
        let top = 12;
        for (const n of col) { n.ly = Math.max(n.ly, top); top = n.ly + step; }
      } else {
        // Middle columns: name over value. The first node labels above its bar; any
        // node below it labels *under* its own bar instead — putting a second block
        // above would land it on top of the ribbons leaving the node above.
        col.forEach((n, i) => { n.ly = i === 0 ? Math.max(n.y - 19, 14) : n.y + n.h + 15; });
        // and if that ran past the frame, fall back to above-the-bar
        for (const n of col) if (n.ly + 16 > this.H) n.ly = Math.max(n.y - 19, 14);
      }
      out.push(...col);
    }
    return out;
  }

  links(): LaidLink[] {
    const laid = this.nodes();
    const by = new Map(laid.map(n => [n.id, n]));
    const s = this.scale();
    const hl = this.hlMap();
    const out: LaidLink[] = [];
    const ribbon = (x0: number, y0: number, x1: number, y1: number, xm: number, t: number) =>
      `M${x0},${y0} C${xm},${y0} ${xm},${y1} ${x1},${y1} L${x1},${y1 + t} `
      + `C${xm},${y1 + t} ${xm},${y0 + t} ${x0},${y0 + t} Z`;
    for (const l of this.linkSpec) {
      const a = by.get(l.from), b = by.get(l.to);
      if (!a || !b || l.value <= 0) continue;
      const th = Math.max(1, l.value * s);
      const x0 = a.x + this.NODE_W, x1 = b.x;
      const y0 = a.sy, y1 = b.ty;
      a.sy += th; b.ty += th;
      const xm = (x0 + x1) / 2;
      const key = l.from + '→' + l.to;
      const laidLink: LaidLink = { ...l, key, color: a.color, d: ribbon(x0, y0, x1, y1, xm, th) };
      // person-share highlight: an inset band along the top of this ribbon
      const h = hl.get(key);
      if (h && h.frac > 0) {
        const t = Math.max(1.5, th * Math.min(1, h.frac));
        laidLink.hlD = ribbon(x0, y0, x1, y1, xm, t);
        laidLink.hlColor = h.color;
      }
      out.push(laidLink);
    }
    return out;
  }

  /** is this node on either end of a link touching the hovered node? */
  touches(id: string): boolean {
    const h = this.hover();
    if (!h) return false;
    return this.linkSpec.some(l => (l.from === h && l.to === id) || (l.to === h && l.from === id));
  }
  /** keep a side label inside its gutter */
  short(label: string): string {
    const max = this.compact ? 15 : this.wide ? 16 : 22;
    return label.length > max ? label.slice(0, max - 1).trimEnd() + '…' : label;
  }
  labelX(n: LaidNode): number {
    return n.col === this.lastCol() ? n.x + this.NODE_W + 9 : n.x - 9;
  }
  anchor(n: LaidNode): string { return n.col === this.lastCol() ? 'start' : 'end'; }
  fmt(v: number): string { return inr(v); }
}
