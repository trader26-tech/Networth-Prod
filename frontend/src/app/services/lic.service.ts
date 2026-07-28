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

export type PlanType = 'endowment' | 'money_back' | 'ulip' | 'health' | 'term' | 'whole_life';
export type Frequency = 'monthly' | 'quarterly' | 'half_yearly' | 'yearly' | 'single';
export type PolicyStatus = 'active' | 'matured' | 'surrendered' | 'lapsed';

export interface LicPolicy {
  id: string;
  holder: string | null;
  policy_number: string | null;
  plan: string;
  plan_type: PlanType | null;
  term_years: number | null;
  premium_paying_years: number | null;
  premium: number | null;
  premium_frequency: Frequency;
  premium_annual: number | null;
  paid_by: string | null;
  start_date: string | null;
  maturity_date: string | null;
  sum_assured: number | null;
  maturity_amount: number | null;
  bonus: number | null;
  total_maturity: number | null;
  status: PolicyStatus;
  fund_units: number | null;
  fund_nav: number | null;
  fund_value: number | null;
  invested: number | null;
  remarks: string | null;
  note: string | null;
  // life-insurance essentials (raw)
  whole_life: boolean | null;
  accident_benefit: number | null;
  nominee: string | null;
  nominee_relation: string | null;
  nominee_phone: string | null;
  agent_name: string | null;
  agent_phone: string | null;
  branch: string | null;
  // derived (server-computed)
  is_active: boolean;
  is_matured: boolean;
  is_surrendered: boolean;
  annual_premium: number;
  life_cover: number | null;
  expected_maturity: number | null;
  premiums_paid: number | null;
  premium_paying_years_eff: number | null;
  fully_paid: boolean;
  years_to_maturity: number | null;
  maturity_year: number | null;
  start_year: number | null;
  premium_years_left: number | null;
  premium_done: boolean;
  is_whole_life: boolean;
  cover_lifelong: boolean;
  cover_until_label: string;
  has_nominee: boolean;
  has_claim_contact: boolean;
  type_label: string;
  type_blurb: string;
}

export interface LicHolder {
  holder: string; count: number; active: number;
  cover: number; annual_premium: number; expected: number;
  accident_benefit: number; cover_lifelong: boolean; cover_until_year: number | null;
  premium_years_left: number | null; bonus: number;
  nominee: string | null; nominee_relation: string | null; nominee_phone: string | null;
  agent_name: string | null; agent_phone: string | null; branch: string | null;
  missing_nominee: number; missing_contact: number;
}
export interface LicType {
  type: string; label: string; count: number; active: number; expected: number; cover: number;
}
export interface LicLadderItem {
  date: string; year: number | null; holder: string | null;
  plan: string | null; policy_number: string | null; amount: number | null;
}
export interface LicNextMaturity {
  date: string; holder: string | null; plan: string | null;
  policy_number: string | null; amount: number | null;
}
export interface LicSummary {
  count: number; active_count: number; matured_count: number; surrendered_count: number;
  in_force_cover: number; annual_premium: number; monthly_premium: number;
  expected_maturity: number; received: number; invested: number;
  next_maturity: LicNextMaturity | null;
  missing_nominee: number;
  by_holder: LicHolder[]; by_type: LicType[]; ladder: LicLadderItem[];
  policies: LicPolicy[];
}
export interface LicMeta {
  plan_types: PlanType[]; type_labels: Record<string, string>;
  type_blurbs: Record<string, string>; frequencies: Frequency[]; statuses: PolicyStatus[];
}
export interface LicInput {
  holder?: string | null; policy_number?: string | null; plan: string;
  plan_type?: PlanType; term_years?: number | null; premium_paying_years?: number | null;
  premium?: number | null; premium_frequency?: Frequency; premium_annual?: number | null;
  paid_by?: string | null; start_date?: string | null; maturity_date?: string | null;
  sum_assured?: number | null; maturity_amount?: number | null; bonus?: number | null;
  total_maturity?: number | null; status?: PolicyStatus;
  fund_units?: number | null; fund_nav?: number | null; fund_value?: number | null;
  invested?: number | null; remarks?: string | null; note?: string | null;
  whole_life?: boolean | null; accident_benefit?: number | null;
  nominee?: string | null; nominee_relation?: string | null; nominee_phone?: string | null;
  agent_name?: string | null; agent_phone?: string | null; branch?: string | null;
}

@Injectable({ providedIn: 'root' })
export class LicService {
  private readonly base = _apiBase() + '/lic';
  private http = inject(HttpClient);

  summary(): Observable<LicSummary> { return this.http.get<LicSummary>(`${this.base}/summary`); }
  meta(): Observable<LicMeta> { return this.http.get<LicMeta>(`${this.base}/meta`); }
  addItem(p: LicInput): Observable<LicPolicy> { return this.http.post<LicPolicy>(`${this.base}/items`, p); }
  updateItem(id: string, patch: Partial<LicInput>): Observable<LicPolicy> {
    return this.http.put<LicPolicy>(`${this.base}/items/${id}`, patch);
  }
  deleteItem(id: string): Observable<any> { return this.http.delete(`${this.base}/items/${id}`); }

  /** Indian-format ₹ — compact (₹1.7 L / ₹1.2 Cr) by default. */
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
  static inrFull(v: number | null | undefined): string { return LicService.inr(v, false); }
}
