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

export type LoanFreq = 'monthly' | 'quarterly' | 'half_yearly' | 'yearly';
export type LoanPayStatus = 'pending' | 'paid' | 'unpaid';
export interface LoanPaymentStatus { loan_id: string; date: string; status: LoanPayStatus; }

export interface FxCostPoint { month: string; fx: number; nominal: number; effective: number; }
export interface FxCost {
  available: boolean;
  currency: string;
  nominal_rate: number | null;
  effective_rate?: number;
  currency_drag?: number;
  fx_start?: number;
  fx_now?: number;
  fx_change_pct?: number;
  start_month?: string;
  series?: FxCostPoint[];
  projection?: FxCostPoint[];
  peg?: number;
  source?: string;
}

export interface Loan {
  id: string;
  owner: string | null;
  lender: string;
  loan_type: string;
  account_no: string | null;
  currency: string;
  original_amount: number | null;
  outstanding_principal: number | null;
  interest_rate: number | null;
  emi_amount: number | null;
  emi_frequency: LoanFreq;
  start_date: string | null;
  maturity_date: string | null;
  next_installment_date: string | null;
  installments_paid: number | null;
  installments_remaining: number | null;
  status: 'active' | 'closed';
  closed_on: string | null;
  note: string | null;
  // derived (server-computed, all *_inr are INR)
  fx_rate: number;
  is_foreign: boolean;
  closed: boolean;
  outstanding_inr: number;
  original_inr: number;
  emi_inr: number;
  emi_monthly_inr: number;
  paid_principal: number;
  paid_principal_inr: number;
  progress: number;
}

export interface LoansSummary {
  loans: Loan[];
  count: number;
  active_count: number;
  total_outstanding_inr: number;
  total_emi_monthly_inr: number;
  total_original_inr: number;
  total_repaid_inr: number;
  repaid_pct: number | null;
  by_person: { person: string; outstanding_inr: number; emi_monthly_inr: number; count: number }[];
  has_foreign: boolean;
}

export interface LoanInput {
  owner?: string | null;
  lender: string;
  loan_type?: string;
  account_no?: string | null;
  currency?: string;
  original_amount?: number | null;
  outstanding_principal?: number | null;
  interest_rate?: number | null;
  emi_amount?: number | null;
  emi_frequency?: LoanFreq;
  start_date?: string | null;
  maturity_date?: string | null;
  next_installment_date?: string | null;
  installments_paid?: number | null;
  installments_remaining?: number | null;
  note?: string | null;
}

@Injectable({ providedIn: 'root' })
export class LoansService {
  private readonly base = _apiBase() + '/loans';
  private http = inject(HttpClient);

  summary(): Observable<LoansSummary> { return this.http.get<LoansSummary>(`${this.base}/summary`); }
  meta(): Observable<{ loan_types: string[]; frequencies: string[] }> { return this.http.get<any>(`${this.base}/meta`); }
  addItem(l: LoanInput): Observable<Loan> { return this.http.post<Loan>(`${this.base}/items`, l); }
  updateItem(id: string, patch: Partial<LoanInput>): Observable<Loan> {
    return this.http.put<Loan>(`${this.base}/items/${id}`, patch);
  }
  fxCost(id: string): Observable<FxCost> { return this.http.get<FxCost>(`${this.base}/items/${id}/fx-cost`); }
  preclose(id: string): Observable<Loan> { return this.http.post<Loan>(`${this.base}/items/${id}/preclose`, {}); }
  reopen(id: string): Observable<Loan> { return this.http.post<Loan>(`${this.base}/items/${id}/reopen`, {}); }
  deleteItem(id: string): Observable<any> { return this.http.delete(`${this.base}/items/${id}`); }

  /** Per-installment paid / pending / unpaid marks (repayment calendar). */
  paymentStatuses(): Observable<LoanPaymentStatus[]> { return this.http.get<LoanPaymentStatus[]>(`${this.base}/payment-status`); }
  setPaymentStatus(loan_id: string, date: string, status: LoanPayStatus): Observable<LoanPaymentStatus> {
    return this.http.put<LoanPaymentStatus>(`${this.base}/payment-status`, { loan_id, date, status });
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
  static inrFull(v: number | null | undefined): string { return LoansService.inr(v, false); }
  static cur(v: number | null | undefined, code: string): string {
    if (v === null || v === undefined || isNaN(v as number)) return '—';
    return `${code} ${Math.round(v).toLocaleString('en-IN')}`;
  }
  static pct(v: number | null | undefined): string {
    if (v === null || v === undefined || isNaN(v as number)) return '—';
    return (v * 100).toFixed(1) + '%';
  }
}
