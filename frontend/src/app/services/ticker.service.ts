import { Injectable, signal } from '@angular/core';

export interface Tick {
  last_price: number;
  change: number;
  change_pct: number;
}

export interface StrategyAlert {
  id: number;
  type: 'sl_hit' | 'target_hit';
  strategy_id: string;
  strategy_name: string;
  pnl: number;
  time: string;
}

@Injectable({ providedIn: 'root' })
export class TickerService {
  ticks = signal<Record<string, Tick>>({});
  strategyMtm = signal<Record<string, number>>({}); // live P&L per strategy id
  alerts = signal<StrategyAlert[]>([]);
  connected = signal(false);
  private ws: WebSocket | null = null;

  private _wsUrl(): string {
    if (typeof window === 'undefined') return 'ws://localhost:8000/ws/ticker';
    const override = (window as any).__WS_URL__;
    if (override) return override;
    const { hostname, host, protocol } = window.location;
    if (hostname === 'localhost' || hostname === '127.0.0.1') {
      return 'ws://localhost:8000/ws/ticker';
    }
    const wsProto = protocol === 'https:' ? 'wss:' : 'ws:';
    return `${wsProto}//${host}/ws/ticker`;
  }

  connect() {
    if (this.ws) return;
    this.ws = new WebSocket(this._wsUrl());
    this.ws.onopen = () => this.connected.set(true);
    this.ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.type === 'tick') {
        this.ticks.set(msg.data);
      } else if (msg.type === 'strategy_mtm') {
        this.strategyMtm.set(msg.data);
      } else if (msg.type === 'sl_hit' || msg.type === 'target_hit') {
        const alert: StrategyAlert = {
          id: Date.now(),
          type: msg.type,
          strategy_id: msg.strategy_id,
          strategy_name: msg.strategy_name,
          pnl: msg.pnl,
          time: new Date().toLocaleTimeString(),
        };
        this.alerts.update(a => [alert, ...a].slice(0, 10));
      }
    };
    this.ws.onclose = () => {
      this.connected.set(false);
      this.ws = null;
      setTimeout(() => this.connect(), 3000);
    };
    this.ws.onerror = () => this.ws?.close();
  }

  dismissAlert(id: number) {
    this.alerts.update(a => a.filter(x => x.id !== id));
  }
}
