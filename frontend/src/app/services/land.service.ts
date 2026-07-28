import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import { AuthService } from './auth.service';

// API base — mirrors api.service.ts / networth.service.ts.
function _apiBase(): string {
  if (typeof window === 'undefined') return 'http://localhost:8000/api';
  const override = (window as any).__API_BASE__;
  if (override) return override;
  const { hostname, protocol, host } = window.location;
  if (hostname === 'localhost' || hostname === '127.0.0.1') return 'http://localhost:8000/api';
  return `${protocol}//${host}/api`;
}

export interface LandDocument {
  id: string;
  land_id: string;
  filename: string;
  mime_type: string | null;
  size: number | null;
  created_at: string;
}

export interface LandParcel {
  id: string;
  name: string;
  owner: string | null;
  location: string | null;
  area_sqft: number | null;
  bought_date: string | null;
  bought_price: number | null;
  current_estimated_price: number | null;
  after_brokerage_price: number | null;
  notes: string | null;
  sellable_on: string | null;
  created_at: string;
  updated_at: string;
  // Derived server-side (never stored):
  documents: LandDocument[];
  holding_years: number | null;
  gain: number | null;
  gain_pct: number | null;
  net_gain: number | null;
  cagr: number | null;       // gross CAGR on current estimated price
  net_cagr: number | null;   // CAGR on after-brokerage realisable price
  rate_per_sqft: number | null;        // present value ÷ area
  bought_rate_per_sqft: number | null; // cost ÷ area
}

export interface LandSummary {
  parcel_count: number;
  invested: number;
  current_value: number;
  realisable_value: number;
  total_gain: number;
  total_gain_pct: number | null;
  blended_cagr: number | null;
  document_count: number;
}

export type ParcelInput = {
  name: string;
  owner?: string | null;
  location?: string | null;
  area_sqft?: number | null;
  bought_date?: string | null;
  bought_price?: number | null;
  current_estimated_price?: number | null;
  after_brokerage_price?: number | null;
  notes?: string | null;
  sellable_on?: string | null;
};

@Injectable({ providedIn: 'root' })
export class LandService {
  private readonly base = _apiBase() + '/land';
  private http = inject(HttpClient);
  private auth = inject(AuthService);

  summary(): Observable<LandSummary> { return this.http.get<LandSummary>(`${this.base}/summary`); }
  parcels(): Observable<LandParcel[]> { return this.http.get<LandParcel[]>(`${this.base}/parcels`); }
  addParcel(p: ParcelInput): Observable<LandParcel> { return this.http.post<LandParcel>(`${this.base}/parcels`, p); }
  updateParcel(id: string, patch: Partial<ParcelInput>): Observable<LandParcel> {
    return this.http.put<LandParcel>(`${this.base}/parcels/${id}`, patch);
  }
  deleteParcel(id: string): Observable<any> { return this.http.delete(`${this.base}/parcels/${id}`); }

  uploadDocument(id: string, file: File): Observable<LandDocument> {
    const fd = new FormData();
    fd.append('file', file);
    return this.http.post<LandDocument>(`${this.base}/parcels/${id}/documents`, fd);
  }
  deleteDocument(docId: string): Observable<any> { return this.http.delete(`${this.base}/documents/${docId}`); }
  renameDocument(docId: string, name: string): Observable<LandDocument> {
    return this.http.patch<LandDocument>(`${this.base}/documents/${docId}`, { name });
  }

  /** Direct link to view/download a document. Loaded by the browser directly
   *  (<img>/<iframe>/"open in new tab"), so it can't send the Authorization
   *  header — we pass the access token as a query param the auth guard accepts. */
  documentUrl(docId: string): string {
    const t = this.auth.token();
    return t ? `${this.base}/documents/${docId}?access_token=${encodeURIComponent(t)}` : `${this.base}/documents/${docId}`;
  }

  /** Format as Indian currency — crore/lakh aware, with correct lakh/crore
   *  comma grouping (e.g. ₹1.23 Cr, ₹75.50 L, ₹45,000).
   *  Pass compact=false for the exact rupee figure: ₹1,23,45,678. */
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

  /** Exact rupees with Indian (lakh/crore) comma grouping: ₹1,23,45,678. */
  static inrFull(v: number | null | undefined): string {
    return LandService.inr(v, false);
  }

  static pct(v: number | null | undefined): string {
    if (v === null || v === undefined || isNaN(v as number)) return '—';
    const sign = v > 0 ? '+' : '';
    return sign + (v * 100).toFixed(1) + '%';
  }
}
