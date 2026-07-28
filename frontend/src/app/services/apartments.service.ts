import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import { AuthService } from './auth.service';

function _apiBase(): string {
  if (typeof window === 'undefined') return 'http://localhost:8000/api';
  const override = (window as any).__API_BASE__;
  if (override) return override;
  const { hostname, protocol, host } = window.location;
  if (hostname === 'localhost' || hostname === '127.0.0.1') return 'http://localhost:8000/api';
  return `${protocol}//${host}/api`;
}

export interface AptDocument {
  id: string;
  apartment_id: string;
  filename: string;
  mime_type: string | null;
  size: number | null;
  created_at: string;
}

export interface AptTenant {
  id: string;
  apartment_id: string;
  name: string;
  phone: string | null;
  advance_paid: number | null;
  move_in_date: string | null;
  move_out_date: string | null;       // null while still living there
  notes: string | null;
  created_at: string;
  // derived server-side:
  tenancy_years: number | null;       // move-in → move-out (or today)
  active: boolean;                     // no move-out date = current tenant
}

export interface AptUnit {
  id: string;
  name: string;
  owner: string | null;
  location: string | null;
  area_sqft: number | null;
  bought_date: string | null;
  bought_price: number | null;
  current_estimated_price: number | null;
  after_brokerage_price: number | null;
  monthly_rent: number | null;
  notes: string | null;
  sellable_on: string | null;          // date you expect to be able to sell it (buy planner)
  created_at: string;
  updated_at: string;
  // Derived server-side (never stored):
  documents: AptDocument[];
  tenants: AptTenant[];
  current_tenant: AptTenant | null;
  holding_years: number | null;
  gain: number | null;
  gain_pct: number | null;
  cagr: number | null;                 // price appreciation CAGR
  net_cagr: number | null;
  annual_rent: number | null;
  rent_yield: number | null;           // annual rent ÷ present value
  rent_yield_on_cost: number | null;   // annual rent ÷ cost
  total_cagr: number | null;           // appreciation + rent — headline
  rate_per_sqft: number | null;
  bought_rate_per_sqft: number | null;
}

export interface AptSummary {
  unit_count: number;
  invested: number;
  current_value: number;
  realisable_value: number;
  total_gain: number;
  total_gain_pct: number | null;
  monthly_rent: number;
  annual_rent: number;
  gross_yield: number | null;
  blended_appreciation_cagr: number | null;
  blended_total_cagr: number | null;
  document_count: number;
}

export type UnitInput = {
  name: string;
  owner?: string | null;
  location?: string | null;
  area_sqft?: number | null;
  bought_date?: string | null;
  bought_price?: number | null;
  current_estimated_price?: number | null;
  after_brokerage_price?: number | null;
  monthly_rent?: number | null;
  notes?: string | null;
  sellable_on?: string | null;
};

export type TenantInput = {
  name: string;
  phone?: string | null;
  advance_paid?: number | null;
  move_in_date?: string | null;
  move_out_date?: string | null;
  notes?: string | null;
};

@Injectable({ providedIn: 'root' })
export class ApartmentsService {
  private readonly base = _apiBase() + '/apartments';
  private http = inject(HttpClient);
  private auth = inject(AuthService);

  summary(): Observable<AptSummary> { return this.http.get<AptSummary>(`${this.base}/summary`); }
  units(): Observable<AptUnit[]> { return this.http.get<AptUnit[]>(`${this.base}/units`); }
  addUnit(u: UnitInput): Observable<AptUnit> { return this.http.post<AptUnit>(`${this.base}/units`, u); }
  updateUnit(id: string, patch: Partial<UnitInput>): Observable<AptUnit> {
    return this.http.put<AptUnit>(`${this.base}/units/${id}`, patch);
  }
  deleteUnit(id: string): Observable<any> { return this.http.delete(`${this.base}/units/${id}`); }

  addTenant(unitId: string, t: TenantInput): Observable<AptTenant> {
    return this.http.post<AptTenant>(`${this.base}/units/${unitId}/tenants`, t);
  }
  updateTenant(tenantId: string, patch: Partial<TenantInput>): Observable<AptTenant> {
    return this.http.put<AptTenant>(`${this.base}/tenants/${tenantId}`, patch);
  }
  deleteTenant(tenantId: string): Observable<any> { return this.http.delete(`${this.base}/tenants/${tenantId}`); }

  uploadDocument(id: string, file: File): Observable<AptDocument> {
    const fd = new FormData();
    fd.append('file', file);
    return this.http.post<AptDocument>(`${this.base}/units/${id}/documents`, fd);
  }
  deleteDocument(docId: string): Observable<any> { return this.http.delete(`${this.base}/documents/${docId}`); }
  renameDocument(docId: string, name: string): Observable<AptDocument> {
    return this.http.patch<AptDocument>(`${this.base}/documents/${docId}`, { name });
  }
  // Browser-loaded directly (<img>/<iframe>/"open in new tab"), so it can't
  // send the Authorization header — pass the access token as a query param.
  documentUrl(docId: string): string {
    const t = this.auth.token();
    return t ? `${this.base}/documents/${docId}?access_token=${encodeURIComponent(t)}` : `${this.base}/documents/${docId}`;
  }

  /** Indian currency (₹, lakh/crore aware) with correct lakh/crore commas. */
  static inr(v: number | null | undefined, compact = true): string {
    if (v === null || v === undefined || isNaN(v as number)) return '—';
    const neg = v < 0; const a = Math.abs(v);
    let s: string;
    if (!compact) {
      s = '₹' + Math.round(a).toLocaleString('en-IN');
    } else if (a >= 1e7) {
      s = '₹' + (a / 1e7).toLocaleString('en-IN', { maximumFractionDigits: 2 }) + ' Cr';
    } else if (a >= 1e5) {
      s = '₹' + (a / 1e5).toLocaleString('en-IN', { maximumFractionDigits: 2 }) + ' L';
    } else {
      s = '₹' + Math.round(a).toLocaleString('en-IN');
    }
    return neg ? '-' + s : s;
  }
  static inrFull(v: number | null | undefined): string { return ApartmentsService.inr(v, false); }

  static pct(v: number | null | undefined): string {
    if (v === null || v === undefined || isNaN(v as number)) return '—';
    const sign = v > 0 ? '+' : '';
    return sign + (v * 100).toFixed(1) + '%';
  }
}
