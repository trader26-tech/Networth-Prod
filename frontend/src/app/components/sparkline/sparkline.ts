import { Component, Input, computed } from '@angular/core';
import { CommonModule } from '@angular/common';

/**
 * Sparkline — minimal weekly-close 1Y price chart.
 *
 *  • Pure SVG, no chart library.
 *  • Color: green if the period ended above its start, red otherwise.
 *  • Renders a smooth polyline through ~52 weekly close points; the
 *    series gets normalized to fit the box (so the shape, not the
 *    absolute level, is what reads at a glance).
 */
@Component({
  selector: 'app-sparkline',
  imports: [CommonModule],
  template: `
    @if (hasData()) {
      <svg [attr.viewBox]="viewBox()" [attr.width]="width" [attr.height]="height"
           class="spark" preserveAspectRatio="none">
        <defs>
          <linearGradient [attr.id]="gradId()" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%"  [attr.stop-color]="lineColor()" stop-opacity="0.35"/>
            <stop offset="100%" [attr.stop-color]="lineColor()" stop-opacity="0.02"/>
          </linearGradient>
        </defs>
        <!-- Filled area underneath -->
        <path [attr.d]="areaPath()" [attr.fill]="'url(#' + gradId() + ')'" stroke="none" />
        <!-- The line -->
        <path [attr.d]="linePath()" fill="none" [attr.stroke]="lineColor()" stroke-width="1.6"
              stroke-linejoin="round" stroke-linecap="round" />
        <!-- Last point dot -->
        <circle [attr.cx]="lastDot().x" [attr.cy]="lastDot().y" r="2"
                [attr.fill]="lineColor()" />
      </svg>
    } @else {
      <div class="spark-empty" [style.width.px]="width" [style.height.px]="height">—</div>
    }
  `,
  styles: [`
    :host { display: inline-block; line-height: 0; }
    svg.spark { display: block; }
    .spark-empty {
      display: inline-flex; align-items: center; justify-content: center;
      font-size: 11px; color: rgba(15,23,42,0.35);
      font-family: -apple-system, BlinkMacSystemFont, system-ui, sans-serif;
    }
  `],
})
export class SparklineComponent {
  /** Weekly-close points (numeric). 2+ values required to draw a line. */
  @Input() points: number[] | null | undefined = null;

  /** Period change in percent — if provided, used to pick line color
   *  (green ≥ 0 / red < 0). Falls back to comparing first vs last point. */
  @Input() changePct: number | null | undefined = null;

  @Input() width  = 96;
  @Input() height = 32;

  /** Unique id so multiple sparklines on a page don't share gradient defs. */
  private readonly _uid = Math.random().toString(36).slice(2, 9);
  gradId = () => `spark-grad-${this._uid}`;

  hasData = computed(() => Array.isArray(this.points) && this.points.length >= 2);

  viewBox = computed(() => `0 0 ${this.width} ${this.height}`);

  /** Normalize points to the viewport, returning {x, y} pairs. */
  private xy() {
    const pts = this.points ?? [];
    if (pts.length < 2) return [];
    const min = Math.min(...pts);
    const max = Math.max(...pts);
    const range = max - min || 1;
    const stepX = this.width / (pts.length - 1);
    // 2px top/bottom padding so the line doesn't clip
    const padY = 2;
    const usableH = this.height - padY * 2;
    return pts.map((v, i) => ({
      x: i * stepX,
      y: padY + (1 - (v - min) / range) * usableH,
    }));
  }

  linePath = computed(() => {
    const pts = this.xy();
    if (pts.length < 2) return '';
    return pts.reduce((acc, p, i) =>
      acc + (i === 0 ? `M ${p.x.toFixed(2)} ${p.y.toFixed(2)} `
                     : `L ${p.x.toFixed(2)} ${p.y.toFixed(2)} `), '');
  });

  areaPath = computed(() => {
    const pts = this.xy();
    if (pts.length < 2) return '';
    const first = pts[0], last = pts[pts.length - 1];
    let d = `M ${first.x.toFixed(2)} ${this.height} `;
    d += `L ${first.x.toFixed(2)} ${first.y.toFixed(2)} `;
    for (let i = 1; i < pts.length; i++) {
      d += `L ${pts[i].x.toFixed(2)} ${pts[i].y.toFixed(2)} `;
    }
    d += `L ${last.x.toFixed(2)} ${this.height} Z`;
    return d;
  });

  lastDot = computed(() => {
    const pts = this.xy();
    return pts.length ? pts[pts.length - 1] : { x: 0, y: 0 };
  });

  lineColor = computed(() => {
    if (this.changePct != null) {
      return this.changePct >= 0 ? '#15924b' : '#b73a4f';
    }
    const pts = this.points ?? [];
    if (pts.length < 2) return '#94a3b8';
    return pts[pts.length - 1] >= pts[0] ? '#15924b' : '#b73a4f';
  });
}
