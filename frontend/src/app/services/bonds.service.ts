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

export interface ScheduleRow { date: string; interest: number; principal: number; }

export interface BondInput {
  owner: string;
  broker: string;
  issuer: string;
  bond_type: string;
  isin?: string;
  rating?: string;
  tax_free: boolean;
  face_value: number;
  quantity: number;
  buy_price: number;
  coupon_rate: number;
  coupon_freq: string;
  repayment_type: string;
  purchase_date: string;
  first_payment_date?: string | null;
  maturity_date: string;
  redemption_value?: number | null;
  ytm_input?: number | null;
  schedule?: ScheduleRow[] | null;
  note?: string;
  sellable_on?: string | null;
}

export interface SchedulePayment extends ScheduleRow { total: number; }

export interface BondRow extends BondInput {
  id: string;
  invested: number;
  face_total: number;
  annual_income: number;
  annual_income_net: number;
  monthly_income: number;
  monthly_income_net: number;
  ytm: number | null;
  current_yield: number | null;
  capital_recovered: number;
  capital_recovered_pct: number;
  future_principal: number;
  principal_outstanding: number;
  years_to_maturity: number;
  next_payment: { date: string; amount: number } | null;
  schedule: SchedulePayment[];
}

export interface BondMember { member: string; invested: number; monthly_income: number; monthly_income_net: number; bonds: number; ytm: number | null; }
export interface BondAccount {
  owner: string; broker: string; invested: number; monthly_income: number; monthly_income_net: number;
  interest_12m: number; principal_12m: number; net_12m: number; bonds: number;
}
export interface CombinedRating { score: number; label: string; weighted: number; max: number; rated_pct: number; }
export interface PaymentItem { date: string; issuer: string; owner: string; broker: string; tax_free: boolean; interest: number; principal: number; total: number; tds: number; net: number; }
export interface PaymentMonth { month: string; total: number; interest: number; principal: number; tds: number; net: number; capital_recovered: number; capital_recovered_pct: number; count: number; payments: PaymentItem[]; }
export interface YearCashflow { year: string; interest: number; principal: number; total: number; net: number; matures: string[]; }

export interface BondSummary {
  bonds: BondRow[];
  members: BondMember[];
  payments_by_account: BondAccount[];
  count: number;
  member_count: number;
  account_count: number;
  total_invested: number;
  total_monthly_income: number;
  total_monthly_income_net: number;
  total_annual_income: number;
  total_annual_income_net: number;
  total_maturity_value: number;
  total_capital_recovered: number;
  portfolio_ytm: number | null;
  combined_rating: CombinedRating | null;
  avg_coupon: number | null;
  avg_years_to_maturity: number | null;
  taxfree_invested: number;
  payment_schedule: PaymentMonth[];
  yearly_cashflow: YearCashflow[];
}

export interface GenerateReq {
  invested: number; face_total: number; ytm: number;
  first_payment_date: string; maturity_date: string;
  coupon_freq: string; repayment_type: string;
}
export interface GenerateResp { schedule: ScheduleRow[]; coupon_rate: number; }

/** Lifecycle of a single bond payout — mirrors the Dividends calendar. */
export type PaymentStatus = 'pending' | 'received' | 'not_received';
export interface BondPaymentStatus { bond_id: string; date: string; status: PaymentStatus; }

export interface BondSipSplit { name: string; amount: number; }
export interface BondSipInput {
  owner?: string; total: number; expected_date: string; note?: string; splits: BondSipSplit[];
}
export interface BondSip extends BondSipInput {
  id: string; status: 'pending' | 'logged'; created_at: string; logged_at: string | null;
}

export interface ReconcileMatch {
  credit_date: string; credit_amount: number; narration: string;
  bond_id: string; issuer: string; owner: string | null;
  scheduled_date: string; gross: number; net: number; tax_free: boolean;
  date_diff: number; amount_diff: number; amount_ok: boolean;
  confidence: 'high' | 'review'; status: string;
}
export interface ReconcileCredit { date: string; amount: number; narration: string; }
export interface ReconcileMeta {
  filename?: string; date_from?: string | null; date_to?: string | null;
  owners?: string[]; credits?: number; amount?: number;
}
export interface StatementUpload {
  id: string; filename: string; uploaded_by: string | null; uploaded_at: string;
  date_from: string | null; date_to: string | null; owners: string[];
  credits: number; marked: number; amount: number;
}
export interface ReconcileResult {
  matched: ReconcileMatch[]; review: ReconcileMatch[];
  already: ReconcileMatch[]; unmatched: ReconcileCredit[];
  counts: { credits: number; matched: number; review: number; already: number; unmatched: number };
}

@Injectable({ providedIn: 'root' })
export class BondsService {
  private readonly base = _apiBase() + '/bonds';
  private http = inject(HttpClient);

  summary(): Observable<BondSummary> { return this.http.get<BondSummary>(`${this.base}/summary`); }

  // ── Pending SIPs — a planned bond purchase you log the real details of later ──
  listSips(): Observable<BondSip[]> { return this.http.get<BondSip[]>(`${this.base}/sips`); }
  addSip(s: BondSipInput): Observable<BondSip> { return this.http.post<BondSip>(`${this.base}/sips`, s); }
  updateSip(id: string, patch: Partial<BondSipInput> & { status?: 'pending' | 'logged' }): Observable<BondSip> {
    return this.http.put<BondSip>(`${this.base}/sips/${id}`, patch);
  }
  removeSip(id: string): Observable<any> { return this.http.delete(`${this.base}/sips/${id}`); }
  generate(req: GenerateReq): Observable<GenerateResp> { return this.http.post<GenerateResp>(`${this.base}/generate`, req); }
  create(b: BondInput): Observable<any> { return this.http.post(`${this.base}/bonds`, b); }
  update(id: string, b: BondInput): Observable<any> { return this.http.put(`${this.base}/bonds/${id}`, b); }
  remove(id: string): Observable<any> { return this.http.delete(`${this.base}/bonds/${id}`); }

  /** Per-payment received/pending/not-received marks (calendar). */
  paymentStatuses(): Observable<BondPaymentStatus[]> { return this.http.get<BondPaymentStatus[]>(`${this.base}/payment-status`); }
  setPaymentStatus(bond_id: string, date: string, status: PaymentStatus): Observable<BondPaymentStatus> {
    return this.http.put<BondPaymentStatus>(`${this.base}/payment-status`, { bond_id, date, status });
  }

  /** Bank-statement reconciliation: upload → matched/review/already/unmatched. */
  reconcilePreview(file: File): Observable<ReconcileResult> {
    const fd = new FormData();
    fd.append('file', file, file.name);
    return this.http.post<ReconcileResult>(`${this.base}/reconcile/preview`, fd);
  }
  reconcileConfirm(items: { bond_id: string; date: string }[], meta?: ReconcileMeta): Observable<{ ok: boolean; marked: number; upload: StatementUpload | null }> {
    return this.http.post<{ ok: boolean; marked: number; upload: StatementUpload | null }>(`${this.base}/reconcile/confirm`, { items, ...(meta || {}) });
  }
  reconcileUploads(): Observable<StatementUpload[]> { return this.http.get<StatementUpload[]>(`${this.base}/reconcile/uploads`); }
  deleteUpload(id: string): Observable<{ deleted: boolean }> { return this.http.delete<{ deleted: boolean }>(`${this.base}/reconcile/uploads/${id}`); }

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
  static inrFull(v: number | null | undefined): string { return BondsService.inr(v, false); }
  static pct(v: number | null | undefined): string {
    if (v === null || v === undefined || isNaN(v as number)) return '—';
    const sign = v > 0 ? '+' : '';
    return sign + (v * 100).toFixed(2) + '%';
  }
}
