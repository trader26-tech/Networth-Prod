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

export interface AccountInput {
  member: string;
  broker: string;
  start_date: string;
  start_amount: number;
  end_date: string;
  end_amount: number;
  note?: string;
  sellable_on?: string | null;
}

export interface AccountRow extends AccountInput {
  id: string;
  years: number | null;
  gain: number;
  gain_pct: number | null;
  cagr: number | null;
}

export interface MemberRow {
  member: string;
  start_amount: number;
  end_amount: number;
  gain: number;
  gain_pct: number | null;
  cagr: number | null;
  accounts: number;
  brokers: string[];
}

export interface BrokerageSummary {
  accounts: AccountRow[];
  members: MemberRow[];
  count: number;
  member_count: number;
  total_invested: number;
  total_current: number;
  total_gain: number;
  total_gain_pct: number | null;
  combined_cagr: number | null;
  best: { member: string; broker: string; cagr: number } | null;
  worst: { member: string; broker: string; cagr: number } | null;
  earliest_start: string | null;
  latest_end: string | null;
}

@Injectable({ providedIn: 'root' })
export class BrokerageService {
  private readonly base = _apiBase() + '/brokerage';
  private http = inject(HttpClient);

  summary(): Observable<BrokerageSummary> { return this.http.get<BrokerageSummary>(`${this.base}/summary`); }
  create(a: AccountInput): Observable<any> { return this.http.post(`${this.base}/accounts`, a); }
  update(id: string, a: AccountInput): Observable<any> { return this.http.put(`${this.base}/accounts/${id}`, a); }
  remove(id: string): Observable<any> { return this.http.delete(`${this.base}/accounts/${id}`); }

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
  static inrFull(v: number | null | undefined): string { return BrokerageService.inr(v, false); }
  static pct(v: number | null | undefined): string {
    if (v === null || v === undefined || isNaN(v as number)) return '—';
    const sign = v > 0 ? '+' : '';
    return sign + (v * 100).toFixed(1) + '%';
  }
}
