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

export interface CasInvestor {
  name?: string | null;
  pan?: string | null;          // masked by the backend
  cas_id?: string | null;
  period_from?: string | null;
  period_to?: string | null;
}

export interface CasAccount {
  id: string;
  person?: string | null;
  broker?: string | null;
  account_label?: string | null;
}

export interface CasHolding {
  id: string;
  account_id: string | null;
  isin?: string | null;
  name?: string | null;
  quantity?: number | null;
  avg_price?: number | null;
  account_label?: string | null;
  _value?: number | null;
  _kind?: string | null;
}

export interface CasBond {
  id: string;
  owner: string;
  broker?: string;
  issuer: string;
  bond_type: string;
  isin?: string;
  face_value: number;
  quantity: number;
  buy_price: number;
  coupon_rate: number;
  coupon_freq: string;
  repayment_type: string;
  purchase_date: string;
  maturity_date?: string | null;
  note?: string;
  _value?: number | null;
  _needs?: string[];
}

export interface CasMfFolio {
  amc?: string | null;
  scheme?: string | null;
  isin?: string | null;
  folio?: string | null;
  units?: number | null;
  value?: number | null;
}

export interface CasTotals {
  total_portfolio?: number | null;
  equity_value?: number | null;
  bond_value?: number | null;
  demat_fund_value?: number | null;
  reconciled?: boolean;
  reconciliation_drift?: number;
  stated_class_values?: Record<string, number>;
  parsed_class_values?: Record<string, number>;
}

export interface CasPreview {
  investor: CasInvestor;
  as_of: string;
  owner: string;
  accounts: CasAccount[];
  holdings: CasHolding[];
  bonds: CasBond[];
  mf_folios: CasMfFolio[];
  totals: CasTotals;
  warnings: string[];
  counts: { accounts: number; holdings: number; bonds: number; mf_folios: number };
}

export interface CasCommitReport {
  holdings: number;
  bonds: number;
  accounts?: number;
  errors: string[];
}

@Injectable({ providedIn: 'root' })
export class CasService {
  private http = inject(HttpClient);
  private base = _apiBase();

  /** Upload the CAS PDF + PAN (the PDF password). Nothing is written yet. */
  preview(file: File, pan: string, owner?: string): Observable<CasPreview> {
    const fd = new FormData();
    fd.append('file', file);
    fd.append('pan', pan);
    if (owner) fd.append('owner', owner);
    return this.http.post<CasPreview>(`${this.base}/cas/preview`, fd);
  }

  /** Persist the reviewed preview into stocks + bonds. */
  commit(preview: CasPreview, sections?: string[]): Observable<CasCommitReport> {
    return this.http.post<CasCommitReport>(`${this.base}/cas/commit`, { preview, sections });
  }

  static inr(v: number | null | undefined): string {
    if (v === null || v === undefined || isNaN(v)) return '—';
    return '₹' + Number(v).toLocaleString('en-IN', { maximumFractionDigits: 2 });
  }
}
