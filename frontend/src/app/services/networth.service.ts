import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';

// API base — mirrors api.service.ts: dev → http://localhost:8000/api,
// production (served by FastAPI) → same-origin /api.
function _apiBase(): string {
  if (typeof window === 'undefined') return 'http://localhost:8000/api';
  const override = (window as any).__API_BASE__;
  if (override) return override;
  const { hostname, protocol, host } = window.location;
  if (hostname === 'localhost' || hostname === '127.0.0.1') return 'http://localhost:8000/api';
  return `${protocol}//${host}/api`;
}

export interface OwnerSplit { person: string; pct: number; }

export interface Asset {
  id: string;
  name: string;
  asset_class: string;
  current_value: number;
  invested_value: number | null;
  monthly_income: number | null;
  cagr: number | null;
  purchase_date: string | null;
  risk: 'low' | 'medium' | 'high';
  owners: OwnerSplit[];
  source: any;
  documents: any[];
  notes: string | null;
  created_at: string;
  updated_at: string;
}

export interface Person { name: string; risk_profile: string; color: string; }

export interface ClassSummary {
  asset_class: string; label: string; liability: boolean;
  value: number; monthly_income: number; asset_count: number;
  cagr: number | null; pct: number;
}
export interface PersonSummary {
  person: string; value: number; monthly_income: number; asset_count: number; pct: number;
}
export interface Summary {
  net_worth: number; total_assets: number; total_liabilities: number;
  monthly_income: number; annual_income: number; weighted_cagr: number | null;
  asset_count: number;
  by_class: ClassSummary[];
  by_person: PersonSummary[];
  people: Person[];
}

export interface SheetMeta {
  name: string; rows: number; cols: number; non_empty_rows: number;
  header_guess: number | null; likely_asset: boolean;
  preview: { row: number; cells: any[] }[];
}
export interface UploadResult {
  upload_id: string; filename: string; uploaded_at: string;
  sheet_count: number; sheets: SheetMeta[];
}
export interface SheetGrid {
  sheet: string; rows: any[][]; col_letters: string[];
  total_rows: number; total_cols: number; col_offset: number;
  truncated_rows: boolean; truncated_cols: boolean;
}
export interface Meta {
  asset_classes: { key: string; label: string; default_risk: string; liability: boolean }[];
  risk_levels: string[];
  import_fields: { key: string; label: string }[];
}

export interface ImportRequest {
  upload_id: string; sheet: string;
  columns: { index: number; field: string }[];
  header_row?: number | null;
  data_start_row: number; data_end_row?: number | null;
  col_offset: number; max_cols: number;
  value_scale: number;
  defaults: { asset_class: string; risk?: string | null; owners: OwnerSplit[] };
}

@Injectable({ providedIn: 'root' })
export class NetworthService {
  private readonly base = _apiBase() + '/networth';
  private http = inject(HttpClient);

  meta(): Observable<Meta> { return this.http.get<Meta>(`${this.base}/meta`); }
  summary(): Observable<Summary> { return this.http.get<Summary>(`${this.base}/summary`); }

  assets(): Observable<Asset[]> { return this.http.get<Asset[]>(`${this.base}/assets`); }
  addAsset(a: Partial<Asset>): Observable<Asset> { return this.http.post<Asset>(`${this.base}/assets`, a); }
  updateAsset(id: string, patch: Partial<Asset>): Observable<Asset> {
    return this.http.put<Asset>(`${this.base}/assets/${id}`, patch);
  }
  deleteAsset(id: string): Observable<any> { return this.http.delete(`${this.base}/assets/${id}`); }
  bulkDelete(body: { ids?: string[]; asset_class?: string; all?: boolean }): Observable<{ deleted: number }> {
    return this.http.post<{ deleted: number }>(`${this.base}/assets/bulk-delete`, body);
  }

  people(): Observable<Person[]> { return this.http.get<Person[]>(`${this.base}/people`); }
  savePeople(p: Person[]): Observable<Person[]> { return this.http.put<Person[]>(`${this.base}/people`, p); }

  upload(file: File): Observable<UploadResult> {
    const fd = new FormData();
    fd.append('file', file);
    return this.http.post<UploadResult>(`${this.base}/upload`, fd);
  }
  uploads(): Observable<any[]> { return this.http.get<any[]>(`${this.base}/uploads`); }
  sheet(uploadId: string, name: string, maxRows = 300, maxCols = 60, colOffset = 0): Observable<SheetGrid> {
    const p = `name=${encodeURIComponent(name)}&max_rows=${maxRows}&max_cols=${maxCols}&col_offset=${colOffset}`;
    return this.http.get<SheetGrid>(`${this.base}/upload/${uploadId}/sheet?${p}`);
  }
  deleteUpload(uploadId: string): Observable<any> { return this.http.delete(`${this.base}/upload/${uploadId}`); }
  import(req: ImportRequest): Observable<{ imported: number; assets: Asset[] }> {
    return this.http.post<{ imported: number; assets: Asset[] }>(`${this.base}/import`, req);
  }

  /** Format a number as Indian currency (₹, lakh/crore aware). */
  static inr(v: number | null | undefined, compact = true): string {
    if (v === null || v === undefined || isNaN(v as number)) return '—';
    const neg = v < 0; const a = Math.abs(v);
    let s: string;
    if (!compact) {
      s = '₹' + Math.round(a).toLocaleString('en-IN');
    } else if (a >= 1e7) {
      s = '₹' + (a / 1e7).toFixed(2) + ' Cr';
    } else if (a >= 1e5) {
      s = '₹' + (a / 1e5).toFixed(2) + ' L';
    } else if (a >= 1e3) {
      s = '₹' + (a / 1e3).toFixed(1) + 'K';
    } else {
      s = '₹' + a.toFixed(0);
    }
    return neg ? '-' + s : s;
  }
}
