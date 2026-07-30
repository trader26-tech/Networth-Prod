import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';

function _apiBase(): string {
  if (typeof window === 'undefined') return 'http://localhost:8000/api';
  const o = (window as any).__API_BASE__;
  if (o) return o;
  const { hostname, protocol, host } = window.location;
  if (hostname === 'localhost' || hostname === '127.0.0.1') return 'http://localhost:8000/api';
  return `${protocol}//${host}/api`;
}

export interface AionionHolding {
  id: string; symbol?: string | null; name?: string | null; isin?: string | null;
  quantity?: number | null; avg_price?: number | null; import_price?: number | null;
  _value?: number | null; _kind?: string | null;
}
export interface AionionBond {
  id: string; issuer: string; isin?: string; quantity: number; face_value: number;
  coupon_rate: number; coupon_freq: string; maturity_date?: string | null;
  ytm_input?: number | null; _value?: number | null; _needs?: string[];
}
export interface AionionPreview {
  investor: { name?: string; pan?: string; client_id?: string };
  owner: string;
  account: { account_label: string; broker: string };
  holdings: AionionHolding[];
  bonds: AionionBond[];
  totals: { equity_market?: number; mf_value?: number; bond_value?: number; net_worth?: number };
  warnings: string[];
  counts: { holdings: number; equities: number; mutual_funds: number; bonds: number };
}
export interface AionionCommitReport {
  holdings: number; bonds: number; accounts?: number; errors: string[];
}

@Injectable({ providedIn: 'root' })
export class AionionService {
  private http = inject(HttpClient);
  private base = _apiBase();

  preview(file: File, owner?: string): Observable<AionionPreview> {
    const fd = new FormData();
    fd.append('file', file);
    if (owner) fd.append('owner', owner);
    return this.http.post<AionionPreview>(`${this.base}/aionion/preview`, fd);
  }
  commit(preview: AionionPreview, sections?: string[]): Observable<AionionCommitReport> {
    return this.http.post<AionionCommitReport>(`${this.base}/aionion/commit`, { preview, sections });
  }
  static inr(v: number | null | undefined): string {
    if (v === null || v === undefined || isNaN(v)) return '—';
    return '₹' + Number(v).toLocaleString('en-IN', { maximumFractionDigits: 2 });
  }
}
