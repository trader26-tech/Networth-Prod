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

export type Frequency = 'weekly' | 'monthly' | 'quarterly' | 'half_yearly' | 'yearly' | 'one_time';

export interface Income {
  id: string;
  owner: string | null;
  source: string;
  category: string | null;
  amount: number | null;
  currency: string;
  frequency: Frequency;
  account: string | null;
  active: boolean;
  is_template?: boolean;
  template_id?: string | null;
  added?: boolean;              // reminder already logged this month
  on_date: string | null;
  note: string | null;
  created_at?: string;
  updated_at?: string;
  // derived
  monthly_native: number;
  monthly_inr: number;
  annual_inr: number;
  amount_inr: number;
  inr_per_unit: number;
  is_foreign: boolean;
  one_time: boolean;
}

export interface CatRow { category: string; monthly_inr: number; pct: number; }
export interface PersonRow { person: string; monthly_inr: number; }
export interface CurRow { currency: string; monthly_inr: number; }

export interface IncomeSummary {
  count: number;
  monthly_total: number;
  annual_total: number;
  one_time_total?: number;
  by_category: CatRow[];
  by_person: PersonRow[];
  by_currency: CurRow[];
  currencies: string[];
  has_foreign: boolean;
  entries: Income[];
  fx: { ok: boolean; stale?: boolean; source?: string; updated_at?: string; inr_per: Record<string, number> };
}

export interface IncomeInput {
  owner?: string | null;
  source: string;
  category?: string | null;
  amount?: number | null;
  currency?: string;
  frequency?: Frequency;
  account?: string | null;
  active?: boolean;
  on_date?: string | null;
  note?: string | null;
}

export interface LogInput {
  owner?: string | null;
  source: string;
  category?: string | null;
  amount?: number | null;
  currency?: string;
  on_date?: string | null;
  account?: string | null;
  template_id?: string | null;
  note?: string | null;
}
export interface LogSummary extends IncomeSummary { period: string; }

@Injectable({ providedIn: 'root' })
export class OtherIncomeService {
  private readonly base = _apiBase() + '/other-income';
  private http = inject(HttpClient);

  meta(): Observable<{ frequencies: string[]; categories: string[]; currencies: string[] }> {
    return this.http.get<{ frequencies: string[]; categories: string[]; currencies: string[] }>(`${this.base}/meta`);
  }
  accounts(): Observable<{ accounts: string[] }> { return this.http.get<{ accounts: string[] }>(`${this.base}/accounts`); }

  // actual income log (the month table)
  log(period: string): Observable<LogSummary> { return this.http.get<LogSummary>(`${this.base}/log?period=${period}`); }
  addLog(e: LogInput): Observable<Income> { return this.http.post<Income>(`${this.base}/log`, e); }
  updateLog(id: string, patch: Partial<LogInput>): Observable<Income> { return this.http.put<Income>(`${this.base}/log/${id}`, patch); }
  deleteLog(id: string): Observable<any> { return this.http.delete(`${this.base}/log/${id}`); }

  // recurring templates (reminders)
  templates(period: string): Observable<{ period: string; templates: Income[] }> {
    return this.http.get<{ period: string; templates: Income[] }>(`${this.base}/templates?period=${period}`);
  }
  addTemplate(e: IncomeInput): Observable<Income> { return this.http.post<Income>(`${this.base}/templates`, e); }
  deleteTemplate(id: string): Observable<any> { return this.http.delete(`${this.base}/templates/${id}`); }
  toggleReminder(template_id: string, period: string): Observable<any> {
    return this.http.post<any>(`${this.base}/reminder`, { template_id, period });
  }

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
  static inrFull(v: number | null | undefined): string { return OtherIncomeService.inr(v, false); }
  static money(v: number | null | undefined, currency: string): string {
    if (v === null || v === undefined || isNaN(v as number)) return '—';
    if ((currency || 'INR').toUpperCase() === 'INR') return OtherIncomeService.inrFull(v);
    return `${currency} ${Math.round(v).toLocaleString('en-IN')}`;
  }
}
