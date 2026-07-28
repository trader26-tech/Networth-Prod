import { Injectable, signal } from '@angular/core';

export interface PendingLeg {
  symbol: string;
  strike: number;
  type: 'CE' | 'PE';
  transaction_type: 'BUY' | 'SELL';
  qty: number;
  premium: number;
}

export interface PendingStrategy {
  name: string;
  underlying: string;
  expiry: string;
  legs: PendingLeg[];
}

@Injectable({ providedIn: 'root' })
export class StrategyBuilderService {
  pending = signal<PendingStrategy | null>(null);

  load(s: PendingStrategy) { this.pending.set(s); }
  consume(): PendingStrategy | null {
    const v = this.pending();
    this.pending.set(null);
    return v;
  }
}
