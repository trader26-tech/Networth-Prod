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

export type CashType = 'cash' | 'bank';

export interface CashItem {
  id: string;
  owner: string | null;
  type: CashType;
  where: string | null;
  account_label: string | null;
  balance: number | null;
  currency: string;
  as_of_date: string | null;
  note: string | null;
  created_at?: string;
  updated_at?: string;
  // derived
  balance_inr: number;
  inr_per_unit: number;
  is_foreign: boolean;
}

export interface CashSummary {
  count: number;
  total: number;
  cash: number;
  bank: number;
  by_person: { person: string; balance_inr: number }[];
  currencies: string[];
  has_foreign: boolean;
  entries: CashItem[];
  fx: { ok: boolean; stale?: boolean; source?: string; updated_at?: string; inr_per: Record<string, number> };
}

export interface CashInput {
  owner?: string | null;
  type?: CashType;
  where?: string | null;
  account_label?: string | null;
  balance?: number | null;
  currency?: string;
  as_of_date?: string | null;
  note?: string | null;
}

@Injectable({ providedIn: 'root' })
export class CashService {
  private readonly base = _apiBase() + '/cash';
  private http = inject(HttpClient);

  summary(): Observable<CashSummary> { return this.http.get<CashSummary>(`${this.base}/summary`); }
  addItem(c: CashInput): Observable<CashItem> { return this.http.post<CashItem>(`${this.base}/items`, c); }
  updateItem(id: string, patch: Partial<CashInput>): Observable<CashItem> { return this.http.put<CashItem>(`${this.base}/items/${id}`, patch); }
  deleteItem(id: string): Observable<any> { return this.http.delete(`${this.base}/items/${id}`); }
  wheres(): Observable<{ wheres: string[] }> { return this.http.get<{ wheres: string[] }>(`${this.base}/wheres`); }

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
  static inrFull(v: number | null | undefined): string { return CashService.inr(v, false); }
  static money(v: number | null | undefined, currency: string): string {
    if (v === null || v === undefined || isNaN(v as number)) return '—';
    if ((currency || 'INR').toUpperCase() === 'INR') return CashService.inrFull(v);
    return `${currency} ${Math.round(v).toLocaleString('en-IN')}`;
  }
}
