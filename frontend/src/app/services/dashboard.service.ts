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

export interface Position {
  asset_class: string; class_label: string; name: string; owner: string;
  value: number; realisable: number; invested: number | null;
  cagr: number | null; monthly_income: number;
  liquidity_tier: number; liquidity_days: number; liquidity_label: string;
  divisible: boolean; sub: string | null; sellable_on?: string | null;
}
export interface ClassAlloc {
  asset_class: string; label: string; value: number; monthly_income: number;
  count: number; cagr: number | null; pct: number;
  liquidity_tier: number; liquidity_label: string;
}
export interface Person { person: string; value: number; gross_value?: number; loan_outstanding?: number; loan_emi?: number; advance_repay?: number; monthly_income: number; count: number; class_count: number; cagr: number | null; pct: number; photo?: string | null; color?: string | null; }
export interface LiabPerson { person: string; outstanding_inr: number; emi_monthly_inr: number; count: number; }
export interface Liabilities { outstanding: number; monthly_emi: number; count: number; active_count: number; by_person: LiabPerson[]; }
export interface AdvanceItem { tenant: string; unit: string; owner: string; amount: number; }
export interface Advances { total: number; count: number; items: AdvanceItem[]; }
export interface LiquidityBucket { tier: number; label: string; value: number; realisable: number; days: number; }
export interface IncomeItem { key: string; kind: string; label: string; owner: string; amount: number; received: boolean; }
export interface IncomeDue { period: string; items: IncomeItem[]; expected: number; received: number; pending: number; pending_count: number; }
export interface ExpenseDueItem { key: string; kind: string; label: string; owner: string; amount: number; paid: boolean; }
export interface ExpenseDue { period: string; items: ExpenseDueItem[]; expected: number; paid: number; pending: number; pending_count: number; }
export interface ReceiptResult { income_due: IncomeDue; expenses_due: ExpenseDue; }

export interface SalaryPerson { person: string; monthly_inr: number; annual_inr: number; count: number; currencies: string[]; }
export interface SalaryBlock {
  monthly_total: number; annual_total: number; count: number; currencies: string[]; has_foreign: boolean;
  by_person: SalaryPerson[]; entries: any[];
  fx: { ok: boolean; stale?: boolean; source?: string; updated_at?: string; inr_per: Record<string, number> };
}

export interface ExpenseCat { category: string; monthly_inr: number; pct: number; }
export interface ExpenseBlock {
  monthly_total: number; annual_total: number; essential: number; discretionary: number;
  subscriptions_total: number; subscriptions_count: number; count: number;
  by_category: ExpenseCat[]; by_person: { person: string; monthly_inr: number }[];
}

export interface Dashboard {
  net_worth: number; gross_assets?: number; liabilities?: Liabilities; advances?: Advances;
  realisable_value: number; invested: number; total_gain: number | null;
  portfolio_cagr: number | null; monthly_income: number; annual_income: number;
  monthly_expenses?: number; annual_expenses?: number; monthly_surplus?: number; savings_rate?: number | null;
  surplus_expenses?: number; planner_committed?: number;
  spent_this_month?: number;
  position_count: number; positions: Position[]; by_class: ClassAlloc[];
  by_person: Person[]; liquidity: LiquidityBucket[]; income_due: IncomeDue;
  salary?: SalaryBlock; expenses?: ExpenseBlock; spent?: ExpenseBlock;
  other_income?: { monthly_total: number; annual_total: number; by_person: { person: string; monthly_inr: number }[] };
  dividends?: { lifetime: number; this_year: number; this_month: number; ttm: number; monthly: number; count: number; year: string; top: { symbol: string; amount: number }[]; by_person: { person: string; this_year: number; ttm: number; monthly: number }[] };
  fno?: { total: number; booked: number; open: number; today: number; accounts: number; by_person: { person: string; booked: number; open: number; today: number; total: number }[] };
}

@Injectable({ providedIn: 'root' })
export class DashboardService {
  private readonly base = _apiBase() + '/dashboard';
  private readonly peopleBase = _apiBase() + '/people';
  private http = inject(HttpClient);

  /** Upload/replace a household member's avatar (multipart). */
  uploadPersonPhoto(name: string, file: File): Observable<any> {
    const fd = new FormData();
    fd.append('file', file);
    return this.http.post<any>(`${this.peopleBase}/${encodeURIComponent(name)}/photo`, fd);
  }
  /** Remove a member's avatar. */
  removePersonPhoto(name: string): Observable<any> {
    return this.http.delete<any>(`${this.peopleBase}/${encodeURIComponent(name)}/photo`);
  }

  summary(gold24k?: number | null, silver?: number | null): Observable<Dashboard> {
    const p: string[] = [];
    if (gold24k && gold24k > 0) p.push('gold24k=' + gold24k);
    if (silver && silver > 0) p.push('silver=' + silver);
    return this.http.get<Dashboard>(`${this.base}/summary${p.length ? '?' + p.join('&') : ''}`);
  }
  setReceipt(key: string, received: boolean, amount?: number): Observable<any> {
    return this.http.post<any>(`${this.base}/receipt`, { key, received, amount });
  }
  getSettings(): Observable<any> { return this.http.get<any>(`${this.base}/settings`); }
  saveSettings(s: any): Observable<any> { return this.http.put<any>(`${this.base}/settings`, s); }

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
  static inrFull(v: number | null | undefined): string { return DashboardService.inr(v, false); }
  static pct(v: number | null | undefined): string {
    if (v === null || v === undefined || isNaN(v as number)) return '—';
    return (v > 0 ? '+' : '') + (v * 100).toFixed(1) + '%';
  }
}
