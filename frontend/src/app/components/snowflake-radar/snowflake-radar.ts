import { Component, Input, computed } from '@angular/core';
import { CommonModule } from '@angular/common';

/**
 * Snowflake — 6-axis radar in the Simply Wall St visual style.
 *
 *   value · future · past · health · dividend · governance
 *
 * Renders concentric circular rings, a smooth Catmull-Rom curve through the
 * data points (so the shape looks like an organic blob rather than a hexagon),
 * and color-coded axis labels positioned outside the rings.
 *
 * Axes whose underlying data isn't trackable (fewer than 2 sub-checks with
 * real data) are drawn as a small inward-dip at the origin and labelled
 * "n/a" so the user can never mistake "no data" for "score zero".
 */
@Component({
  selector: 'app-snowflake-radar',
  imports: [CommonModule],
  template: `
    <svg [attr.viewBox]="viewBox()" [attr.width]="size" [attr.height]="size" class="radar">
      <defs>
        <radialGradient [attr.id]="gradId()" cx="50%" cy="50%" r="65%" fx="50%" fy="50%">
          <stop offset="0%"   [attr.stop-color]="fillColor()" stop-opacity="0.95"/>
          <stop offset="60%"  [attr.stop-color]="fillColor()" stop-opacity="0.80"/>
          <stop offset="100%" [attr.stop-color]="fillColor()" stop-opacity="0.50"/>
        </radialGradient>
        <filter [attr.id]="glowId()" x="-30%" y="-30%" width="160%" height="160%">
          <feGaussianBlur stdDeviation="2.5" result="b"/>
          <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
        </filter>
      </defs>

      <!-- Concentric ring backgrounds (subtle zebra) -->
      <circle *ngFor="let ring of rings(); let i = index"
              cx="0" cy="0" [attr.r]="ring"
              class="ring-fill"
              [attr.fill-opacity]="i % 2 === 0 ? 0.04 : 0.02" />

      <!-- Concentric ring outlines -->
      <circle *ngFor="let ring of rings(); let i = index"
              cx="0" cy="0" [attr.r]="ring"
              class="ring-stroke"
              [class.outer]="i === rings().length - 1" />

      <!-- Axis spokes -->
      <line *ngFor="let a of axes()"
            x1="0" y1="0"
            [attr.x2]="a.outer.x" [attr.y2]="a.outer.y"
            class="spoke" />

      <!-- Filled blob: smooth Catmull-Rom through the data points -->
      <path *ngIf="hasShape()"
            [attr.d]="bezierPath()"
            [attr.fill]="'url(#' + gradId() + ')'"
            [attr.stroke]="strokeColor()"
            [attr.filter]="'url(#' + glowId() + ')'"
            stroke-width="1.4"
            stroke-linejoin="round"
            class="data" />

      <!-- Data dots for trackable axes -->
      <g *ngIf="hasData()">
        <circle *ngFor="let p of dataPoints()"
                [hidden]="!p.trackable"
                [attr.cx]="p.x" [attr.cy]="p.y"
                r="3"
                [attr.fill]="strokeColor()"
                stroke="var(--snowflake-bg, #ffffff)" stroke-width="1.4" />
      </g>

      <!-- Axis labels -->
      <text *ngFor="let a of axes()"
            [attr.x]="a.label.x" [attr.y]="a.label.y"
            [attr.text-anchor]="a.label.anchor"
            [attr.fill]="a.color"
            class="lbl">{{ a.long }}</text>

      <!-- Composite badge in the center -->
      <g *ngIf="hasData() && showComposite">
        <circle cx="0" cy="0" [attr.r]="centerR()"
                [attr.fill]="strokeColor()" fill-opacity="0.16"
                [attr.stroke]="strokeColor()" stroke-width="1.2"/>
        <text x="0" y="0" text-anchor="middle" dominant-baseline="central"
              [attr.fill]="strokeColor()"
              class="center-num">{{ composite != null ? (composite | number:'1.1-1') : 'n/a' }}</text>
        <text x="0" [attr.y]="centerR() - 5" text-anchor="middle" class="center-sub">/ 6</text>
      </g>
    </svg>
  `,
  styles: [`
    /* Default palette: light theme. Override the --snowflake-* tokens on
       the host (or any ancestor) if you need a dark-mode variant. */
    :host { display: inline-block; line-height: 0; }
    svg.radar { display: block; }
    .ring-fill   { fill: var(--snowflake-label, rgba(15,23,42,0.20)); stroke: none; }
    .ring-stroke { fill: none; stroke: var(--snowflake-grid, rgba(15,23,42,0.10)); stroke-width: 1; }
    .ring-stroke.outer {
      stroke: var(--snowflake-grid-outer, rgba(15,23,42,0.24));
      stroke-width: 1.3;
    }
    .spoke { stroke: var(--snowflake-spoke, rgba(15,23,42,0.10)); stroke-width: 1; }
    .lbl {
      font-size: 9.5px; font-weight: 700;
      font-family: -apple-system, BlinkMacSystemFont, system-ui, sans-serif;
      letter-spacing: 1px; text-transform: uppercase;
    }
    .center-num {
      font-size: 14px; font-weight: 800; font-variant-numeric: tabular-nums;
      font-family: -apple-system, BlinkMacSystemFont, system-ui, sans-serif;
    }
    .center-sub {
      font-size: 8px; font-weight: 700;
      fill: var(--snowflake-label, rgba(15,23,42,0.55));
    }
  `],
})
export class SnowflakeRadarComponent {
  /** Axis scores, 0–6 each. A null/undefined score means the axis is not
   *  trackable (no underlying data) — UI renders that axis as "n/a". */
  @Input() axes_data: {
    value:      { score: number | null; trackable: boolean };
    future:     { score: number | null; trackable: boolean };
    past:       { score: number | null; trackable: boolean };
    health:     { score: number | null; trackable: boolean };
    dividend:   { score: number | null; trackable: boolean };
    governance: { score: number | null; trackable: boolean };
  } | null = null;

  @Input() bucket: string | null = null;
  @Input() composite: number | null = null;
  @Input() size = 180;
  @Input() pad = 28;
  @Input() showComposite = true;

  private readonly AXIS_LABELS_LONG = ['VALUE', 'FUTURE', 'PAST', 'HEALTH', 'DIVIDEND', 'GOVERNANCE'];
  private readonly AXIS_KEYS:
    ('value' | 'future' | 'past' | 'health' | 'dividend' | 'governance')[] =
    ['value', 'future', 'past', 'health', 'dividend', 'governance'];

  // Unique per-instance IDs so multiple charts can share a page.
  private readonly _uid = Math.random().toString(36).slice(2, 9);
  gradId = () => `snow-grad-${this._uid}`;
  glowId = () => `snow-glow-${this._uid}`;

  private radius(): number { return (this.size / 2) - this.pad; }

  viewBox = computed(() => {
    const half = this.size / 2;
    return `${-half} ${-half} ${this.size} ${this.size}`;
  });

  /** 6 unit vectors starting at 12 o'clock, going clockwise. */
  private unitVectors() {
    return Array.from({ length: 6 }, (_, i) => {
      const a = (-Math.PI / 2) + (i * (Math.PI / 3));
      return { x: Math.cos(a), y: Math.sin(a) };
    });
  }

  /** Color by raw score band (red → amber → blue → green). */
  private scoreColor(v: number): string {
    if (v <= 1.5) return '#e0556b';
    if (v <= 3.0) return '#d9a441';
    if (v <= 4.5) return '#4d9aff';
    return '#2dbb7d';
  }

  axes = computed(() => {
    const r = this.radius();
    const labelR = r + 18;
    const data = this.axes_data;
    return this.unitVectors().map((v, i) => {
      const lx = v.x * labelR;
      const ly = v.y * labelR + 3;
      let anchor: 'start' | 'middle' | 'end' = 'middle';
      if (lx >  4) anchor = 'start';
      if (lx < -4) anchor = 'end';
      const key = this.AXIS_KEYS[i];
      const ax  = data?.[key];
      const trackable = !!ax?.trackable && ax.score != null;
      const color = trackable
        ? this.scoreColor(ax!.score as number)
        : 'rgba(15,23,42,0.35)';   // muted dark for n/a labels (light theme)
      return {
        long:  this.AXIS_LABELS_LONG[i],
        outer: { x: v.x * r, y: v.y * r },
        label: { x: lx, y: ly, anchor },
        color,
        trackable,
      };
    });
  });

  /** 4 evenly spaced concentric circle radii (matches the reference image). */
  rings = computed(() => {
    const r = this.radius();
    return [0.25, 0.50, 0.75, 1.0].map(f => r * f);
  });

  centerR = computed(() => Math.max(14, this.radius() * 0.28));

  hasData = computed(() => this.axes_data !== null);

  /** Data points scaled by score; trackable flag determines whether to draw the dot. */
  dataPoints = computed(() => {
    const r = this.radius();
    const data = this.axes_data;
    return this.unitVectors().map((v, i) => {
      const ax = data?.[this.AXIS_KEYS[i]];
      const trackable = !!ax?.trackable && ax.score != null;
      // Non-trackable → place at origin (curve dips to center, signaling "no data")
      const score = trackable ? Math.max(0, Math.min(6, (ax!.score as number))) : 0;
      const f = trackable ? score / 6 : 0.0;
      return { x: v.x * r * f, y: v.y * r * f, trackable };
    });
  });

  /** Need at least 3 trackable axes to draw a meaningful blob shape. */
  hasShape = computed(() =>
    this.hasData() && this.dataPoints().filter(p => p.trackable).length >= 3
  );

  /**
   * Smooth closed Bezier path through the data points using Catmull-Rom
   * with tension 0.5 — produces the organic blob look seen in the reference.
   */
  bezierPath = computed(() => {
    const pts = this.dataPoints();
    if (pts.length < 3) return '';
    const n = pts.length;
    const t = 0.5;  // tension — higher = looser curves
    let d = `M ${pts[0].x.toFixed(2)} ${pts[0].y.toFixed(2)} `;
    for (let i = 0; i < n; i++) {
      const p0 = pts[(i - 1 + n) % n];
      const p1 = pts[i];
      const p2 = pts[(i + 1) % n];
      const p3 = pts[(i + 2) % n];
      const c1x = p1.x + ((p2.x - p0.x) * t) / 3;
      const c1y = p1.y + ((p2.y - p0.y) * t) / 3;
      const c2x = p2.x - ((p3.x - p1.x) * t) / 3;
      const c2y = p2.y - ((p3.y - p1.y) * t) / 3;
      d += `C ${c1x.toFixed(2)} ${c1y.toFixed(2)}, ${c2x.toFixed(2)} ${c2y.toFixed(2)}, ${p2.x.toFixed(2)} ${p2.y.toFixed(2)} `;
    }
    return d + 'Z';
  });

  private colorFor(): { fill: string; stroke: string } {
    switch ((this.bucket || '').toUpperCase()) {
      case 'KEEP_AND_ADD':      return { fill: '#2dbb7d', stroke: '#1a9b62' };
      case 'HOLD':              return { fill: '#4d9aff', stroke: '#2476e0' };
      case 'TRIM':              return { fill: '#d9a441', stroke: '#b48226' };
      case 'EXIT':              return { fill: '#e0556b', stroke: '#b73a4f' };
      case 'INSUFFICIENT_DATA': return { fill: '#7a8597', stroke: '#5a6478' };
      default:                  return { fill: '#9ca3af', stroke: '#6b7280' };
    }
  }
  fillColor   = computed(() => this.colorFor().fill);
  strokeColor = computed(() => this.colorFor().stroke);
}
