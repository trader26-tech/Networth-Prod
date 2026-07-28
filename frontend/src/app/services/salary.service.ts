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

export type Frequency = 'monthly' | 'annual';

export interface SalaryEntry {
  id: string;
  person: string;
  amount: number | null;
  currency: string;
  frequency: Frequency;
  bank_account: string | null;
  note: string | null;
  created_at?: string;
  updated_at?: string;
  // derived (server-computed)
  region?: 'india' | 'kuwait';    // from currency: INR → India, else → Kuwait
  region_label?: string;
  monthly_native: number;
  monthly_inr: number;
  annual_inr: number;
  inr_per_unit: number;
  is_foreign: boolean;
}

export interface SalaryInput {
  person: string;
  amount?: number | null;
  currency?: string;
  frequency?: Frequency;
  bank_account?: string | null;
  note?: string | null;
}

export interface FxRates {
  ok: boolean;
  stale?: boolean;
  inr_per: Record<string, number>;
  source?: string;
  updated_at?: string;
  error?: string;
}

@Injectable({ providedIn: 'root' })
export class SalaryService {
  private readonly base = _apiBase() + '/salary';
  private http = inject(HttpClient);

  items(): Observable<SalaryEntry[]> { return this.http.get<SalaryEntry[]>(`${this.base}/items`); }
  addItem(s: SalaryInput): Observable<SalaryEntry> { return this.http.post<SalaryEntry>(`${this.base}/items`, s); }
  updateItem(id: string, patch: Partial<SalaryInput>): Observable<SalaryEntry> {
    return this.http.put<SalaryEntry>(`${this.base}/items/${id}`, patch);
  }
  deleteItem(id: string): Observable<any> { return this.http.delete(`${this.base}/items/${id}`); }

  fx(refresh = false): Observable<FxRates> {
    return this.http.get<FxRates>(`${this.base}/fx${refresh ? '?refresh=1' : ''}`);
  }
  currencies(): Observable<{ currencies: string[] }> { return this.http.get<{ currencies: string[] }>(`${this.base}/currencies`); }
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
  static inrFull(v: number | null | undefined): string { return SalaryService.inr(v, false); }

  /** Amount in its own currency, e.g. "KWD 450" or "₹1,00,000". */
  static money(v: number | null | undefined, currency: string): string {
    if (v === null || v === undefined || isNaN(v as number)) return '—';
    if ((currency || 'INR').toUpperCase() === 'INR') return SalaryService.inrFull(v);
    return `${currency} ${Math.round(v).toLocaleString('en-IN')}`;
  }
}
