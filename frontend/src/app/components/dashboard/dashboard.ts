import {
  Component, OnInit, OnDestroy, AfterViewInit,
  inject, signal, ElementRef, ViewChild
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { ApiService } from '../../services/api.service';
import { TickerService } from '../../services/ticker.service';

declare const LightweightCharts: any;

@Component({
  selector: 'app-dashboard',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink],
  templateUrl: './dashboard.html',
  styleUrl: './dashboard.scss',
})
export class DashboardComponent implements OnInit, AfterViewInit, OnDestroy {
  @ViewChild('pnlChart') pnlChartRef!: ElementRef;

  api = inject(ApiService);
  ticker = inject(TickerService);

  stats = signal<any>(null);
  openStrategies = signal<any[]>([]);
  history = signal<any[]>([]);
  confirmSquareOff = signal<string | null>(null);
  hasPnlData = signal(false);

  editSlModal = signal<{ id: string; name: string; sl: string; target: string } | null>(null);
  savingSlTarget = signal(false);

  private chart: any = null;
  private refreshInterval: any;

  ngOnInit() {
    this.load();
    this.refreshInterval = setInterval(() => this.load(), 10000);
  }

  ngAfterViewInit() {
    this.drawChart();
  }

  ngOnDestroy() {
    clearInterval(this.refreshInterval);
    this.chart?.remove();
  }

  load() {
    this.api.getStrategyStats().subscribe(s => {
      this.stats.set(s);
    });
    this.api.getOpenStrategies().subscribe(s => this.openStrategies.set(s));
    this.api.getStrategyHistory().subscribe(h => this.history.set(h.slice(0, 20)));
    this.api.getPnlHistory().subscribe(pts => this.drawChart(pts));
  }

  livePnl(sid: string): number {
    return this.ticker.strategyMtm()[sid] ?? (this.openStrategies().find(s => s.id === sid)?.current_pnl ?? 0);
  }

  requestSquareOff(sid: string) {
    this.confirmSquareOff.set(sid);
  }

  confirmSquareOffAction() {
    const sid = this.confirmSquareOff();
    if (!sid) return;
    this.confirmSquareOff.set(null);
    this.api.squareOff(sid).subscribe(() => this.load());
  }

  cancelSquareOff() {
    this.confirmSquareOff.set(null);
  }

  dismissAlert(id: number) {
    this.ticker.dismissAlert(id);
  }

  drawChart(pts?: any[]) {
    if (!this.pnlChartRef?.nativeElement) return;
    const lc = (window as any).LightweightCharts;
    if (!lc) return;

    if (!pts) {
      this.api.getPnlHistory().subscribe(p => this.drawChart(p));
      return;
    }
    this.hasPnlData.set(pts.length > 0);
    if (pts.length === 0) return;

    if (this.chart) { this.chart.remove(); this.chart = null; }

    this.chart = lc.createChart(this.pnlChartRef.nativeElement, {
      autoSize: true,
      layout: { background: { color: '#1a1d24' }, textColor: '#8b8fa8' },
      grid: { vertLines: { color: '#2a2d3a' }, horzLines: { color: '#2a2d3a' } },
      rightPriceScale: { borderColor: '#2a2d3a' },
      timeScale: { borderColor: '#2a2d3a', timeVisible: true },
    });

    const AreaSeries = lc.AreaSeries;
    const series = AreaSeries
      ? this.chart.addSeries(AreaSeries, {
          lineColor: '#387ed1', topColor: 'rgba(56,126,209,0.3)',
          bottomColor: 'rgba(56,126,209,0)', lineWidth: 2,
        })
      : this.chart.addAreaSeries({ lineColor: '#387ed1', topColor: 'rgba(56,126,209,0.3)', bottomColor: 'rgba(56,126,209,0)', lineWidth: 2 });

    let cumulative = 0;
    const data = pts.map(p => {
      cumulative += p.pnl;
      return { time: p.date, value: Math.round(cumulative) };
    });
    series.setData(data);
    this.chart.timeScale().fitContent();
  }

  strategyName(s: any): string {
    const nLegs = s.legs?.length ?? 0;
    return `${s.name} (${nLegs} legs)`;
  }

  openEditSl(s: any) {
    this.editSlModal.set({
      id: s.id,
      name: s.name,
      sl: s.sl_amount != null ? String(Math.abs(s.sl_amount)) : '',
      target: s.target_amount != null ? String(s.target_amount) : '',
    });
  }

  closeEditSl() { this.editSlModal.set(null); }

  saveSlTarget() {
    const m = this.editSlModal();
    if (!m) return;
    const sl = m.sl ? -Math.abs(parseFloat(m.sl)) : null;
    const target = m.target ? Math.abs(parseFloat(m.target)) : null;
    this.savingSlTarget.set(true);
    this.api.updateSlTarget(m.id, sl, target).subscribe({
      next: () => {
        this.savingSlTarget.set(false);
        this.editSlModal.set(null);
        this.load();
      },
      error: () => this.savingSlTarget.set(false),
    });
  }

  updateEditField(field: 'sl' | 'target', v: string) {
    const m = this.editSlModal();
    if (!m) return;
    this.editSlModal.set({ ...m, [field]: v });
  }
}
