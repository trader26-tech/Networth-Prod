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

// The plan blob is owned by the House component (see its PlanData interface);
// the API stores it opaquely in the app_cache KV.
export interface PlanEnvelope { plan: any | null; durable: boolean; updated_at?: string | null; }

@Injectable({ providedIn: 'root' })
export class HouseService {
  private http = inject(HttpClient);
  private base = _apiBase();

  getPlan(): Observable<PlanEnvelope> {
    return this.http.get<PlanEnvelope>(`${this.base}/house/plan`);
  }
  savePlan(plan: any): Observable<{ ok: boolean; durable: boolean }> {
    return this.http.put<{ ok: boolean; durable: boolean }>(`${this.base}/house/plan`, { plan });
  }
  resetPlan(): Observable<{ ok: boolean }> {
    return this.http.delete<{ ok: boolean }>(`${this.base}/house/plan`);
  }
}
