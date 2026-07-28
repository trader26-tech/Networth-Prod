import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';

function _apiBase(): string {
  if (typeof window === 'undefined') return 'http://localhost:8000/api';
  const override = (window as any).__API_BASE__;
  if (override) return override;
  const { hostname, protocol, host } = window.location;
  if (hostname === 'localhost' || hostname === '127.0.0.1') return 'http://localhost:8000/api';
  return `${protocol}//${host}/api`;
}

export interface CorpLeg { symbol: string; mult: number | null; }

export interface Position {
  symbol: string;
  isin: string | null;
  exchange: string | null;
  qty: number;
  transferred?: number;
  avg_cost: number;
  invested: number;
  price: number | null;
  value: number | null;
  pnl: number | null;
  pnl_pct: number | null;
  cagr: number | null;
  first_date: string | null;
  holding_years: number | null;
}

export interface NiftyInfo {
  ok: boolean;
  level?: number;
  start_level?: number;
  start_date?: string;
  return_pct?: number | null;
  cagr?: number | null;
}

export interface TimelinePoint { month: string; buys: number; sells: number; net: number; invested: number; }

export interface StockSummary {
  empty?: boolean;
  owner: string | null;
  positions: Position[];
  holdings_count: number;
  invested: number;             // priced basis (comparable to current_value)
  invested_all?: number;        // cost of every open holding
  invested_unpriced?: number;   // cost sitting in holdings we couldn't price
  current_value: number;
  unrealized_pnl: number;
  unrealized_pct: number | null;
  realized_pnl: number;
  xirr: number | null;
  priced: number;
  missing_price: number;
  trade_count: number;
  period_from: string | null;
  period_to: string | null;
  price_source: string;
  nifty: NiftyInfo;
  auto_splits?: { symbol: string; factor: number }[];
  timeline?: TimelinePoint[];
}

export interface OwnerInfo {
  owner: string;
  accounts: string[];
  trades: number;
  from: string | null;
  to: string | null;
}

@Injectable({ providedIn: 'root' })
export class StocksService {
  private readonly base = _apiBase() + '/stocks';
  private http = inject(HttpClient);

  upload(file: File, owner: string): Observable<any> {
    const fd = new FormData();
    fd.append('file', file);
    fd.append('owner', owner || 'default');
    return this.http.post<any>(`${this.base}/upload`, fd);
  }
  owners(): Observable<OwnerInfo[]> { return this.http.get<OwnerInfo[]>(`${this.base}/owners`); }
  summary(owner?: string, overrides?: Record<string, number>,
          corpActions?: Record<string, CorpLeg[]>,
          transfers?: Record<string, number>): Observable<StockSummary> {
    const p = new URLSearchParams();
    if (owner) p.set('owner', owner);
    if (overrides && Object.keys(overrides).length) p.set('overrides', JSON.stringify(overrides));
    if (corpActions && Object.keys(corpActions).length) p.set('corp_actions', JSON.stringify(corpActions));
    if (transfers && Object.keys(transfers).length) p.set('transfers', JSON.stringify(transfers));
    const q = p.toString();
    return this.http.get<StockSummary>(`${this.base}/summary${q ? '?' + q : ''}`);
  }
  deleteOwner(owner: string): Observable<any> { return this.http.delete(`${this.base}/owner/${encodeURIComponent(owner)}`); }

  static inr(v: number | null | undefined, compact = true): string {
    if (v === null || v === undefined || isNaN(v as number)) return '—';
    const neg = v < 0; const a = Math.abs(v);
    let s: string;
    if (!compact) { s = '₹' + Math.round(a).toLocaleString('en-IN'); }
    else if (a >= 1e7) { s = '₹' + (a / 1e7).toLocaleString('en-IN', { maximumFractionDigits: 2 }) + ' Cr'; }
    else if (a >= 1e5) { s = '₹' + (a / 1e5).toLocaleString('en-IN', { maximumFractionDigits: 2 }) + ' L'; }
    else { s = '₹' + Math.round(a).toLocaleString('en-IN'); }
    return neg ? '-' + s : s;
  }
  static inrFull(v: number | null | undefined): string { return StocksService.inr(v, false); }
  static pct(v: number | null | undefined): string {
    if (v === null || v === undefined || isNaN(v as number)) return '—';
    const sign = v > 0 ? '+' : '';
    return sign + (v * 100).toFixed(1) + '%';
  }
}
