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

export type MetalType = 'gold' | 'silver' | 'other';

export interface GoldDocument {
  id: string;
  gold_id: string;
  filename: string;
  mime_type: string | null;
  size: number | null;
  created_at: string;
}

export interface GoldItem {
  id: string;
  name: string;
  owner: string | null;
  metal_type: MetalType;
  weight_g: number | null;
  purity_pct: number | null;
  manual_value: number | null;
  purchase_date: string | null;
  purchase_price: number | null;
  location: string | null;             // where the piece is kept (free text)
  notes: string | null;
  sellable_on: string | null;
  created_at: string;
  updated_at: string;
  documents: GoldDocument[];
}

export interface MetalPrices {
  gold_24k_per_g: number | null;
  silver_per_g: number | null;
  usd_inr?: number;
  spot_gold_usd_oz?: number;
  spot_silver_usd_oz?: number;
  ok: boolean;
  stale?: boolean;
  source?: string;
  updated_at?: string;
  error?: string;
}

export interface MetalPerformance {
  ok: boolean;
  gold: { '1y'?: number; '3y'?: number; '5y'?: number };
  silver: { '1y'?: number; '3y'?: number; '5y'?: number };
  series?: { labels: string[]; gold: number[]; silver: number[] };
  updated_at?: string;
  error?: string;
}

export type GoldInput = {
  name: string;
  owner?: string | null;
  metal_type?: MetalType;
  weight_g?: number | null;
  purity_pct?: number | null;
  manual_value?: number | null;
  purchase_date?: string | null;
  purchase_price?: number | null;
  location?: string | null;
  notes?: string | null;
  sellable_on?: string | null;
};

@Injectable({ providedIn: 'root' })
export class GoldService {
  private readonly base = _apiBase() + '/gold';
  private http = inject(HttpClient);
  private auth = inject(AuthService);

  prices(refresh = false): Observable<MetalPrices> {
    return this.http.get<MetalPrices>(`${this.base}/prices${refresh ? '?refresh=1' : ''}`);
  }
  performance(): Observable<MetalPerformance> {
    return this.http.get<MetalPerformance>(`${this.base}/performance`);
  }
  items(): Observable<GoldItem[]> { return this.http.get<GoldItem[]>(`${this.base}/items`); }
  addItem(i: GoldInput): Observable<GoldItem> { return this.http.post<GoldItem>(`${this.base}/items`, i); }
  updateItem(id: string, patch: Partial<GoldInput>): Observable<GoldItem> {
    return this.http.put<GoldItem>(`${this.base}/items/${id}`, patch);
  }
  deleteItem(id: string): Observable<any> { return this.http.delete(`${this.base}/items/${id}`); }

  uploadDocument(id: string, file: File): Observable<GoldDocument> {
    const fd = new FormData();
    fd.append('file', file);
    return this.http.post<GoldDocument>(`${this.base}/items/${id}/documents`, fd);
  }
  deleteDocument(docId: string): Observable<any> { return this.http.delete(`${this.base}/documents/${docId}`); }
  renameDocument(docId: string, name: string): Observable<GoldDocument> {
    return this.http.patch<GoldDocument>(`${this.base}/documents/${docId}`, { name });
  }
  // Browser-loaded directly (<img>/<iframe>/"open in new tab"), so it can't
  // send the Authorization header — pass the access token as a query param.
  documentUrl(docId: string): string {
    const t = this.auth.token();
    return t ? `${this.base}/documents/${docId}?access_token=${encodeURIComponent(t)}` : `${this.base}/documents/${docId}`;
  }

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
  static inrFull(v: number | null | undefined): string { return GoldService.inr(v, false); }

  static pct(v: number | null | undefined): string {
    if (v === null || v === undefined || isNaN(v as number)) return '—';
    const sign = v > 0 ? '+' : '';
    return sign + (v * 100).toFixed(1) + '%';
  }
}
