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

export type FinanceMode = 'income' | 'savings';

export interface WishItem {
  id: string;
  name: string;
  price: number | null;
  priority: number;
  finance_mode: FinanceMode;
  finance_assets: string | null;   // JSON array of asset position-keys
  sold_assets: string | null;      // JSON array of keys already sold (collected)
  target_date: string | null;      // when you want to buy it
  monthly_contribution: number | null;  // (legacy) ₹/mo set aside
  saved: number | null;            // ₹ actually set aside so far
  bought: boolean;
  note: string | null;
  created_at?: string;
  updated_at?: string;
}

export interface WishInput {
  name: string;
  price?: number | null;
  priority?: number | null;
  finance_mode?: FinanceMode;
  finance_assets?: string | null;
  sold_assets?: string | null;
  target_date?: string | null;
  monthly_contribution?: number | null;
  saved?: number | null;
  bought?: boolean;
  note?: string | null;
}

@Injectable({ providedIn: 'root' })
export class PlannerService {
  private readonly base = _apiBase() + '/planner';
  private http = inject(HttpClient);

  summary(): Observable<{ wishlist: WishItem[] }> {
    return this.http.get<{ wishlist: WishItem[] }>(`${this.base}/summary`);
  }
  addWish(e: WishInput): Observable<WishItem> { return this.http.post<WishItem>(`${this.base}/wishlist`, e); }
  updateWish(id: string, patch: Partial<WishInput>): Observable<WishItem> { return this.http.put<WishItem>(`${this.base}/wishlist/${id}`, patch); }
  deleteWish(id: string): Observable<any> { return this.http.delete(`${this.base}/wishlist/${id}`); }
  reorderWish(order: string[]): Observable<{ items: WishItem[] }> { return this.http.post<{ items: WishItem[] }>(`${this.base}/wishlist/reorder`, { order }); }
}
