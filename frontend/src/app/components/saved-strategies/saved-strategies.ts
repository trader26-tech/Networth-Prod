import { Component, computed, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterLink } from '@angular/router';
import { ApiService } from '../../services/api.service';

@Component({
  selector: 'app-saved-strategies',
  standalone: true,
  imports: [CommonModule, RouterLink],
  templateUrl: './saved-strategies.html',
  styleUrl: './saved-strategies.scss',
})
export class SavedStrategiesComponent {
  private api = inject(ApiService);

  positions = signal<any[]>([]);
  loading   = signal(false);
  error     = signal('');

  totalDeployed = computed(() =>
    this.positions().reduce((s, p) => s + (p?.niftybees_cost || 0), 0)
  );
  totalLivePnl = computed(() =>
    this.positions().reduce((s, p) => s + (p?.live?.total_pnl || 0), 0)
  );
  totalPremium = computed(() =>
    this.positions().reduce((s, p) => s + (p?.total_premium_collected || 0), 0)
  );

  constructor() {
    this.load();
  }

  load() {
    this.loading.set(true);
    this.error.set('');
    this.api.getCoveredCallPositions().subscribe({
      next: (r: any) => {
        this.positions.set(r?.positions || []);
        this.loading.set(false);
      },
      error: (e) => {
        this.error.set(e?.error?.detail || e?.message || 'Failed to load saved strategies');
        this.loading.set(false);
      },
    });
  }

  fmtRs(n: number | null | undefined): string {
    if (n == null || isNaN(n)) return '—';
    const sign = n < 0 ? '-' : '';
    const abs = Math.abs(n);
    if (abs >= 1e7) return `${sign}₹${(abs/1e7).toFixed(2)} Cr`;
    if (abs >= 1e5) return `${sign}₹${(abs/1e5).toFixed(2)} L`;
    if (abs >= 1e3) return `${sign}₹${(abs/1e3).toFixed(1)} k`;
    return `${sign}₹${abs.toFixed(0)}`;
  }
}
