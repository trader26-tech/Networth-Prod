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

export type Frequency = 'monthly' | 'quarterly' | 'half_yearly' | 'yearly' | 'single';
export type FundType = 'equity' | 'balanced' | 'debt' | 'other';

export interface UlipPolicy {
  id: string;
  owner: string | null;
  insurer: string | null;
  plan_name: string;
  policy_number: string | null;
  life_assured: string | null;
  start_date: string | null;
  policy_term_years: number | null;
  premium_paying_term_years: number | null;
  premium_amount: number | null;
  premium_frequency: Frequency;
  sum_assured: number | null;
  fund_value: number | null;
  fund_type: FundType | null;
  note: string | null;
  sellable_on: string | null;
  created_at?: string;
  updated_at?: string;
  // derived (server-computed)
  lock_in_end: string | null;
  maturity_date: string | null;
  locked: boolean;
  years_to_maturity: number | null;
  years_to_lock_in_end: number | null;
  premiums_paid_count: number;
  premiums_total_count: number;
  invested: number;
  total_premiums: number;
  remaining_premiums: number;
  remaining_premiums_count: number;
  fully_paid: boolean;
  annual_outflow: number;
  premiums_per_year: number;
  gain: number | null;
  gain_pct: number | null;
  xirr: number | null;
}

export interface UlipSummary {
  count: number;
  fund_value: number;
  invested: number;
  gain: number | null;
  gain_pct: number | null;
  remaining_premiums: number;
  annual_outflow: number;
  sum_assured: number;
  xirr: number | null;
  policies: UlipPolicy[];
}

export interface UlipInput {
  owner?: string | null;
  insurer?: string | null;
  plan_name: string;
  policy_number?: string | null;
  life_assured?: string | null;
  start_date?: string | null;
  policy_term_years?: number | null;
  premium_paying_term_years?: number | null;
  premium_amount?: number | null;
  premium_frequency?: Frequency;
  sum_assured?: number | null;
  fund_value?: number | null;
  fund_type?: FundType | null;
  note?: string | null;
  sellable_on?: string | null;
}

@Injectable({ providedIn: 'root' })
export class UlipService {
  private readonly base = _apiBase() + '/ulip';
  private http = inject(HttpClient);

  summary(): Observable<UlipSummary> { return this.http.get<UlipSummary>(`${this.base}/summary`); }
  items(): Observable<UlipPolicy[]> { return this.http.get<UlipPolicy[]>(`${this.base}/items`); }
  addItem(p: UlipInput): Observable<UlipPolicy> { return this.http.post<UlipPolicy>(`${this.base}/items`, p); }
  updateItem(id: string, patch: Partial<UlipInput>): Observable<UlipPolicy> {
    return this.http.put<UlipPolicy>(`${this.base}/items/${id}`, patch);
  }
  deleteItem(id: string): Observable<any> { return this.http.delete(`${this.base}/items/${id}`); }
  insurers(): Observable<{ insurers: string[] }> { return this.http.get<{ insurers: string[] }>(`${this.base}/insurers`); }

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
  static inrFull(v: number | null | undefined): string { return UlipService.inr(v, false); }
  static pct(v: number | null | undefined): string {
    if (v === null || v === undefined || isNaN(v as number)) return '—';
    return (v > 0 ? '+' : '') + (v * 100).toFixed(1) + '%';
  }
}
