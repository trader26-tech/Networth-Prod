import { Component, computed, inject, signal } from '@angular/core';
import { CommonModule, DecimalPipe } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ApiService } from '../../services/api.service';

type Tier = 'exit' | 'review' | 'watch' | 'keep' | 'unknown';

/**
 * Exit Strategy — uploads broker holdings, matches each to the Scorecard
 * master, and ranks them by an "exit conviction" 0-100 computed from:
 *   • Hard flags: pledge > 25%, D/E > 3 (ex-financials), Current Ratio < 1,
 *     ICR < 1.5, mcap < 100Cr, 3M avg volume < 5000
 *   • Soft flags: scorecard composite percentile, FII reducing, low promoter
 *     holding, large 1Y drawdown
 *   • Position flags: weight < 0.25% (noise position), > 50% loss
 *
 * Two-signal rule: a stock is shown as "EXIT" only if ≥ 2 hard flags fire.
 * Single-hard stocks demote to "REVIEW".
 */
@Component({
  selector: 'app-exit-strategy',
  imports: [CommonModule, DecimalPipe, FormsModule],
  templateUrl: './exit-strategy.html',
  styleUrl: './exit-strategy.scss',
})
export class ExitStrategyComponent {
  private api = inject(ApiService);

  // State
  result    = signal<any | null>(null);
  loading   = signal(false);
  uploading = signal(false);
  error     = signal('');

  // Filters
  tierFilter = signal<'all' | Tier>('all');
  search     = signal('');
  hideKeep   = signal(true);  // by default, hide stuff already classed KEEP

  // Detail modal
  selected = signal<any | null>(null);

  // Tier metadata for UI
  readonly TIERS: { key: Tier; label: string; sub: string; color: string; bg: string }[] = [
    { key: 'exit',   label: 'EXIT',    sub: 'Multiple hard flags — review urgently',  color: '#b73a4f', bg: 'rgba(224,85,107,0.10)' },
    { key: 'review', label: 'REVIEW',  sub: '1 hard flag + soft signals',              color: '#d9a441', bg: 'rgba(217,164,65,0.12)' },
    { key: 'watch',  label: 'WATCH',   sub: 'Concerning but not urgent',               color: '#2476d8', bg: 'rgba(77,154,255,0.10)' },
    { key: 'keep',   label: 'KEEP',    sub: 'No exit signals',                         color: '#15924b', bg: 'rgba(45,187,125,0.08)' },
    { key: 'unknown',label: 'UNKNOWN', sub: 'Not in scorecard',                        color: '#6b7689', bg: 'rgba(15,23,42,0.06)' },
  ];

  // ── Upload ─────────────────────────────────────────────────────────────
  onUploadChange(ev: Event) {
    const input = ev.target as HTMLInputElement;
    const file = input?.files?.[0];
    if (!file) return;
    input.value = '';
    this.error.set('');
    this.uploading.set(true);
    this.loading.set(true);
    this.api.exitStrategyUpload(file).subscribe({
      next: (r) => {
        this.result.set(r);
        this.uploading.set(false);
        this.loading.set(false);
        if (!r?.available) {
          this.error.set(r?.error || 'No scorecard data uploaded yet. Go to Scorecard first.');
        }
      },
      error: (e) => {
        this.error.set(e?.error?.detail || e?.message || 'Upload failed');
        this.uploading.set(false);
        this.loading.set(false);
      },
    });
  }

  // ── Derived ────────────────────────────────────────────────────────────
  tierCounts = computed(() => this.result()?.tier_counts || {});

  filteredRows = computed(() => {
    const rows = (this.result()?.rows || []) as any[];
    const t = this.tierFilter();
    const q = this.search().trim().toLowerCase();
    return rows.filter(r => {
      if (this.hideKeep() && r.tier === 'keep' && t === 'all') return false;
      if (t !== 'all' && r.tier !== t) return false;
      if (q) {
        const name   = (r.name || r.share || '').toLowerCase();
        const ticker = (r.ticker || '').toLowerCase();
        const sub    = (r.sub_sector || '').toLowerCase();
        if (!name.includes(q) && !ticker.includes(q) && !sub.includes(q)) return false;
      }
      return true;
    });
  });

  totalValue = computed(() => {
    const rows = (this.result()?.rows || []) as any[];
    return rows.reduce((s, r) => s + (r.current_value || 0), 0);
  });

  flaggedValue = computed(() => {
    const rows = (this.result()?.rows || []) as any[];
    return rows
      .filter(r => r.tier === 'exit' || r.tier === 'review')
      .reduce((s, r) => s + (r.current_value || 0), 0);
  });

  flaggedPct = computed(() => {
    const tot = this.totalValue();
    return tot > 0 ? (this.flaggedValue() / tot) * 100 : 0;
  });

  // ── Detail ─────────────────────────────────────────────────────────────
  openDetail(row: any) { this.selected.set(row); }
  closeDetail()        { this.selected.set(null); }

  // ── Helpers ────────────────────────────────────────────────────────────
  tierMeta(t: Tier) {
    return this.TIERS.find(x => x.key === t) || this.TIERS[4];
  }

  setTier(t: 'all' | Tier) {
    this.tierFilter.set(t);
    if (t !== 'all' && t !== 'keep') this.hideKeep.set(false);
  }

  /** ₹ compact: 25.4 L Cr / 50K Cr / 12.3 Cr / 4.5 L */
  fmtRs(v: number | null | undefined): string {
    if (v == null || isNaN(v as any)) return '—';
    const n = Number(v);
    const a = Math.abs(n);
    const sign = n < 0 ? '−' : '';
    if (a >= 1_00_00_000) return `${sign}₹${(a / 1_00_00_000).toFixed(2)} Cr`;
    if (a >= 1_00_000)    return `${sign}₹${(a / 1_00_000).toFixed(2)} L`;
    if (a >= 1_000)       return `${sign}₹${(a / 1_000).toFixed(1)}K`;
    return `${sign}₹${Math.round(a).toLocaleString('en-IN')}`;
  }

  fmtMcap(v: number | null | undefined): string {
    if (v == null) return '—';
    const n = Number(v);
    if (n >= 1_00_000) return `₹${(n / 1_00_000).toFixed(2)}L Cr`;
    if (n >= 1_000)    return `₹${(n / 1_000).toFixed(1)}K Cr`;
    return `₹${n.toFixed(0)} Cr`;
  }

  fmtPct(v: number | null | undefined, digits = 1): string {
    if (v == null || isNaN(v as any)) return '—';
    return `${v >= 0 ? '+' : ''}${Number(v).toFixed(digits)}%`;
  }

  fmtNum(v: number | null | undefined, digits = 1): string {
    if (v == null || isNaN(v as any)) return '—';
    return Number(v).toFixed(digits);
  }

  fmtScore(v: number | null | undefined): string {
    if (v == null) return '—';
    return Math.round(v).toString();
  }

  scoreClass(v: number | null | undefined): string {
    if (v == null) return 'na';
    if (v >= 70) return 'good';
    if (v >= 40) return 'mid';
    return 'bad';
  }

  /** Color of the conviction bar — high = bad. */
  convictionClass(c: number | null | undefined): string {
    if (c == null) return 'na';
    if (c >= 70) return 'bad';
    if (c >= 50) return 'warn';
    if (c >= 30) return 'mid';
    return 'good';
  }

  /** Width % of the conviction bar (capped). */
  convictionPct(c: number | null | undefined): number {
    if (c == null) return 0;
    return Math.max(0, Math.min(100, c));
  }

  severityClass(s: string): string {
    return `sev-${s || 'low'}`;
  }
}
