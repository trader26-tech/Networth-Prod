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

export type ReminderDir = 'in' | 'out' | 'action';
export type ReminderStatus = 'pending' | 'done' | 'skipped';

export interface ReminderItem {
  key: string; date: string; orig_date: string; kind: string; category: string;
  direction: ReminderDir; label: string; sub: string; owner: string;
  amount: number; status: ReminderStatus; source: string; source_id: string;
  moved: boolean; overdue: boolean;
  region?: string; currency?: string; native?: number;   // region + native amount (KWD for Kuwait)
  custom?: boolean;                                       // user-added → deletable
}

export interface CustomReminderIn {
  date: string; label: string; category?: string;
  direction?: ReminderDir; amount?: number; owner?: string; note?: string;
}
export interface NotifyPrefs { categories: string[]; enabled: string[] | null; muted: string[]; }

export interface ReminderFeed {
  today: string; items: ReminderItem[]; count: number;
  pending_count: number; overdue_count: number;
  overdue_amount_in: number; overdue_amount_out: number;
  upcoming_count: number; upcoming_in: number; upcoming_out: number;
  next_30_in: number; next_30_out: number;
}

export interface DigestResult { sent: boolean; reason: string; count: number; }

@Injectable({ providedIn: 'root' })
export class RemindersService {
  private http = inject(HttpClient);
  private readonly base = _apiBase() + '/reminders';

  feed(): Observable<ReminderFeed> { return this.http.get<ReminderFeed>(this.base); }

  /** Move a reminder's date and/or set its status. Returns the recomputed feed. */
  setOverride(key: string, patch: { due_date?: string | null; status?: ReminderStatus | null }): Observable<ReminderFeed> {
    return this.http.put<ReminderFeed>(`${this.base}/override`, { key, ...patch });
  }

  /** Add a user-defined reminder (tagged with what it's for). Returns the recomputed feed. */
  addCustom(body: CustomReminderIn): Observable<ReminderFeed> {
    return this.http.post<ReminderFeed>(`${this.base}/custom`, body);
  }
  /** Remove a user-added reminder. Returns the recomputed feed. */
  deleteCustom(id: string): Observable<ReminderFeed> {
    return this.http.delete<ReminderFeed>(`${this.base}/custom/${id}`);
  }

  sendDigest(): Observable<DigestResult> { return this.http.post<DigestResult>(`${this.base}/send-digest`, {}); }

  prefs(): Observable<NotifyPrefs> { return this.http.get<NotifyPrefs>(`${this.base}/prefs`); }
  setPrefs(enabled: string[]): Observable<NotifyPrefs> { return this.http.put<NotifyPrefs>(`${this.base}/prefs`, { enabled }); }
  setMuted(muted: string[]): Observable<NotifyPrefs> { return this.http.put<NotifyPrefs>(`${this.base}/muted`, { muted }); }

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
}
