import { Component, signal, computed, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { Router, RouterLink } from '@angular/router';
import { STRATEGIES, StrategyMeta } from '../../shared/strategy-registry';

@Component({
  selector: 'app-strategies-hub',
  standalone: true,
  imports: [CommonModule, RouterLink],
  templateUrl: './strategies-hub.html',
  styleUrl: './strategies-hub.scss',
})
export class StrategiesHubComponent {
  private router = inject(Router);

  // Filter UI state
  filter = signal<'all' | 'active' | 'coming-soon'>('all');

  strategies = computed(() => {
    const f = this.filter();
    if (f === 'all') return STRATEGIES;
    return STRATEGIES.filter(s => s.status === f);
  });

  countActive   = computed(() => STRATEGIES.filter(s => s.status === 'active').length);
  countComing   = computed(() => STRATEGIES.filter(s => s.status === 'coming-soon').length);
  countTotal    = computed(() => STRATEGIES.length);

  open(s: StrategyMeta) {
    if (s.status !== 'active') return;
    this.router.navigateByUrl('/' + s.route);
  }

  setFilter(f: 'all' | 'active' | 'coming-soon') {
    this.filter.set(f);
  }

  riskLabel(level: string): string {
    return level.charAt(0).toUpperCase() + level.slice(1);
  }
  statusLabel(s: string): string {
    return s.split('-').map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');
  }
}
