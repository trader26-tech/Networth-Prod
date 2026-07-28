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

export type PayoutType = 'payout' | 'cumulative';
export type Frequency = 'monthly' | 'quarterly' | 'half_yearly' | 'yearly';

export interface FdDeposit {
  id: string;
  owner: string | null;
  bank: string;
  principal: number | null;
  interest_rate: number | null;
  start_date: string | null;
  tenure_months: number | null;
  compounding_frequency: Frequency;
  payout_type: PayoutType;
  payout_frequency: Frequency;
  note: string | null;
  sellable_on: string | null;
  created_at?: string;
  updated_at?: string;
  // derived (server-computed)
  maturity_date: string | null;
  matured: boolean;
  years_to_maturity: number | null;
  progress: number;
  annual_interest: number;
  effective_yield: number;
  tenure_years: number;
  maturity_amount: number;
  current_value: number;
  interest_earned: number;
  monthly_income: number;
  payout_per_period: number;
  payouts_per_year: number;
  dashboard_cagr: number;
}

export interface FdSummary {
  count: number;
  value: number;
  principal: number;
  annual_interest: number;
  monthly_income: number;
  avg_rate: number | null;
  next_maturity: string | null;
  fds: FdDeposit[];
}

export interface FdInput {
  owner?: string | null;
  bank: string;
  principal?: number | null;
  interest_rate?: number | null;
  start_date?: string | null;
  tenure_months?: number | null;
  compounding_frequency?: Frequency;
  payout_type?: PayoutType;
  payout_frequency?: Frequency;
  note?: string | null;
  sellable_on?: string | null;
}

@Injectable({ providedIn: 'root' })
export class FdService {
  private readonly base = _apiBase() + '/fd';
  private http = inject(HttpClient);

  summary(): Observable<FdSummary> { return this.http.get<FdSummary>(`${this.base}/summary`); }
  items(): Observable<FdDeposit[]> { return this.http.get<FdDeposit[]>(`${this.base}/items`); }
  addItem(f: FdInput): Observable<FdDeposit> { return this.http.post<FdDeposit>(`${this.base}/items`, f); }
  updateItem(id: string, patch: Partial<FdInput>): Observable<FdDeposit> {
    return this.http.put<FdDeposit>(`${this.base}/items/${id}`, patch);
  }
  deleteItem(id: string): Observable<any> { return this.http.delete(`${this.base}/items/${id}`); }
  banks(): Observable<{ banks: string[] }> { return this.http.get<{ banks: string[] }>(`${this.base}/banks`); }

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
  static inrFull(v: number | null | undefined): string { return FdService.inr(v, false); }
  static pct(v: number | null | undefined): string {
    if (v === null || v === undefined || isNaN(v as number)) return '—';
    return (v > 0 ? '+' : '') + (v * 100).toFixed(1) + '%';
  }
}
