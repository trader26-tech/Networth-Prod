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

export interface EquityAccount {
  id: string; person: string; broker: string; account_label: string; kind: string;
  status: string; connected: boolean; last_synced: string | null; api_key_hint: string | null;
  sellable_on: string | null;
  value: number; invested: number; pnl: number; pnl_pct: number | null;
  day_change: number; day_change_pct: number; holdings: number;
}
export interface StockRow {
  symbol: string; name?: string | null; isin?: string | null; exchange: string; last_price: number; avg_price: number; quantity: number;
  invested: number; value: number; pnl: number; pnl_pct: number | null;
  day_change: number; day_change_pct: number; accounts: number; priced: boolean; price_manual?: boolean; currency?: string;
  screener_url?: string | null;
}
export interface PersonRow {
  person: string; value: number; invested: number; pnl: number; pnl_pct: number | null;
  day_change: number; day_change_pct: number; holdings: number;
}
export interface HoldingDetail {
  account_id: string; person: string; account_label: string;
  symbol: string; name: string | null; isin?: string | null; exchange: string;
  quantity: number; avg_price: number; last_price: number;
  invested: number; value: number; pnl: number; pnl_pct: number | null;
  day_change: number; day_change_pct: number; priced: boolean; price_manual?: boolean; currency?: string;
  screener_url?: string | null;
}
export interface IndexQuote { id: string; label: string; last: number; change: number; change_pct: number; }
export interface PerfPoint { date: string; value: number; nifty: number; }
export interface Performance {
  period: string; points: PerfPoint[]; symbols: number; coverage: number;
  total_value: number; covered_value: number; missing_value: number; coverage_value_pct: number;
  missing: { symbol: string; value: number }[]; missing_count: number;
  start: PerfPoint | null; end: PerfPoint | null; note: string;
}
export type DividendStatus = 'pending' | 'received' | 'not_received';
export interface Dividend {
  id: string; date: string; symbol: string; name: string | null;
  per_share: number; shares: number; amount: number;   // amount is always ₹ (USD converted)
  received: boolean; received_at?: string | null;
  status: DividendStatus;
  currency: 'INR' | 'USD';                              // per_share is in this currency
  person: string | null; account_id: string | null; note: string | null;
}
export interface DividendSyncItem {
  symbol: string; name?: string; date: string; per_share: number; shares: number; amount: number; subject?: string;
  synced_at?: string | null;              // when this row was first pulled from the exchange page
  prev_shares?: number;                    // (updated rows) share count before the refresh
  source?: 'auto' | 'manual';              // (existing rows) auto-synced vs your hand-entered
}
export interface DividendSyncResult {
  ok: boolean; added_count: number; updated_count: number; existing_count: number; skipped_existing: number;
  pruned_count: number; declared_count: number; held_symbols: number; source_reachable: boolean;
  added: DividendSyncItem[];
  updated: DividendSyncItem[];
  existing: DividendSyncItem[];            // already on your calendar (unchanged) — auto or manual
  pruned: { symbol: string; date: string; amount: number }[];
}
export interface TaxStatement {
  client_id: string; client_name: string; pan: string; fy: string; person: string;
  short_term: number; long_term: number; intraday: number; non_equity: number;
  fno_options: number; fno_futures: number; dividends: number;
  equity_gain: number; fno_gain: number; total_booked: number;
  ltcg_free_left: number; filename: string;
}
export interface CorpActionMatched {
  symbol: string; name?: string; date: string; per_share: number; shares: number; amount: number; subject?: string;
}
export interface CorpActionImportResult {
  ok: boolean; matched_count: number; added_count: number; updated_count: number;
  unheld_count: number; dividend_rows: number; rows_total: number;
  date_from: string | null; date_to: string | null;
  kinds: Record<string, number>;
  matched: CorpActionMatched[];
  upload?: CorpActionUpload;
}
export interface CorpActionUpload {
  id: string; filename: string; uploaded_at: string;
  rows_total: number; dividend_rows: number; kinds: Record<string, number>;
  date_from: string | null; date_to: string | null;
  matched_count: number; added_count: number; updated_count: number; unheld_count: number;
  matched: CorpActionMatched[];
}
export interface DivReconcileMatch {
  credit_date: string; credit_amount: number; narration: string;
  div_id: string; name: string; symbol: string | null; person: string | null;
  div_date: string; amount: number; date_diff: number; amount_diff: number;
  name_match: boolean; confidence: 'high' | 'review' | 'already';
}
export interface DivReconcileCredit { date: string; amount: number; narration: string; }
export interface DivReconcileResult {
  matched: DivReconcileMatch[]; review: DivReconcileMatch[];
  already: DivReconcileMatch[]; unmatched: DivReconcileCredit[];
  ignored: DivReconcileCredit[];   // non-dividend deposits — shown so nothing in the file is hidden
  counts: { credits: number; all_credits: number; matched: number; review: number; already: number; unmatched: number; ignored: number };
}
export interface DivReconcileMeta { filename?: string; date_from?: string | null; date_to?: string | null; person?: string; credits?: number; amount?: number; }
export interface DivStatementUpload {
  id: string; filename: string; uploaded_by: string | null; uploaded_at: string;
  date_from: string | null; date_to: string | null; person: string | null;
  credits: number; marked: number; amount: number;
}
// ── LTCG harvesting: booked long-term gain vs the ₹1.25 L tax-free allowance ──
export interface TaxPnlParsed {
  period_from: string | null; period_to: string | null; client_id: string | null;
  long_term: number; short_term: number; intraday: number; non_equity: number;
  realized_total: number; source_sheet: string;
}
export interface HarvestView {
  fy_label: string; fy_from: string; fy_to: string; allowance: number;
  lt_booked: number; st_booked: number; lt_used: number; lt_remaining: number;
  fully_used: boolean; period_from: string | null; period_to: string | null;
  fy_mismatch: boolean; mismatch_note: string | null;
  // tax owed on gains already booked (listed equity, FY2024-25+ rates)
  st_rate: number; lt_rate: number;
  st_taxable: number; st_tax: number;
  lt_taxable: number; lt_tax: number; total_tax: number;
}
export interface HarvestRecord extends HarvestView {
  id: string; account_id: string; person: string | null; account_label: string | null;
  file_name: string; updated_at: string;
  long_term?: number; short_term?: number; realized_total?: number;
}
export interface HarvestPreview { file_name: string; parsed: TaxPnlParsed; harvest: HarvestView; }

export interface DividendMeta { symbol: string; prev_years: number | null; }
export interface DividendTds { person: string; rate: number | null; }
export interface DividendCollected { symbol: string; person: string; collected: number | null; }
export interface DividendPatch { date?: string; symbol?: string; name?: string; per_share?: number; shares?: number; received?: boolean; status?: DividendStatus; currency?: 'INR' | 'USD'; person?: string; }
export interface EquitySummary {
  accounts: EquityAccount[]; by_person: PersonRow[]; stocks: StockRow[];
  holdings_detail: HoldingDetail[];
  account_count: number; connected_count: number; holding_count: number;
  total_value: number; total_invested: number; total_pnl: number; total_pnl_pct: number | null;
  day_change: number; day_change_pct: number;
  best_today: { symbol: string; day_change_pct: number; value: number } | null;
  worst_today: { symbol: string; day_change_pct: number; value: number } | null;
  top_holding: { symbol: string; value: number } | null;
  price_source: string;
  usd_inr?: number;                                    // live USD→INR (for USD dividends + hover)
}

@Injectable({ providedIn: 'root' })
export class EquityService {
  private http = inject(HttpClient);
  private base = `${_apiBase()}/equity`;

  summary(): Observable<EquitySummary> { return this.http.get<EquitySummary>(`${this.base}/summary`); }
  indices(): Observable<{ indices: IndexQuote[] }> { return this.http.get<{ indices: IndexQuote[] }>(`${this.base}/indices`); }
  manualPrices(): Observable<Record<string, number>> { return this.http.get<Record<string, number>>(`${this.base}/manual-prices`); }
  setManualPrice(symbol: string, price: number | null): Observable<any> {
    return this.http.put(`${this.base}/manual-prices`, { symbol, price });
  }
  setScreenerLink(symbol: string, url: string | null): Observable<any> {
    return this.http.put(`${this.base}/screener-links`, { symbol, url });
  }
  seedScreenerLinks(): Observable<{ written: number }> {
    return this.http.post<{ written: number }>(`${this.base}/screener-links/seed`, {});
  }
  performance(period: string, accounts: string[] = [], refresh = false): Observable<Performance> {
    const a = accounts.length ? `&accounts=${accounts.join(',')}` : '';
    const r = refresh ? '&refresh=true' : '';
    return this.http.get<Performance>(`${this.base}/performance?period=${period}${a}${r}`);
  }
  performanceHoldings(date: string, period: string, accounts: string[] = []): Observable<any> {
    const a = accounts.length ? `&accounts=${accounts.join(',')}` : '';
    return this.http.get<any>(`${this.base}/performance/holdings?date=${date}&period=${period}${a}`);
  }
  accounts(): Observable<EquityAccount[]> { return this.http.get<EquityAccount[]>(`${this.base}/accounts`); }
  addAccount(body: { person: string; account_label: string; api_key: string; api_secret: string }): Observable<any> {
    return this.http.post<any>(`${this.base}/accounts`, body);
  }
  connectMotilal(body: { person: string; account_label: string; api_key: string; api_secret: string; client_code: string; password: string; dob: string; totp?: string }): Observable<any> {
    return this.http.post<any>(`${this.base}/accounts/connect-motilal`, body);
  }
  reloginMotilal(id: string, body: { password: string; dob: string; totp?: string }): Observable<any> {
    return this.http.post<any>(`${this.base}/accounts/${id}/relogin-motilal`, body);
  }
  // ── Excel/CSV import (ICICI / IBKR / Motilal) ───────────────────────────────
  createImported(body: { person: string; account_label: string; broker: string }): Observable<any> {
    return this.http.post<any>(`${this.base}/accounts/imported`, body);
  }
  importPreview(id: string, file: File): Observable<{ count: number; matched: number; holdings: any[] }> {
    const fd = new FormData(); fd.append('file', file);
    return this.http.post<any>(`${this.base}/accounts/${id}/import-preview`, fd);
  }
  importFile(id: string, file: File): Observable<{ ok: boolean; count: number }> {
    const fd = new FormData(); fd.append('file', file);
    return this.http.post<any>(`${this.base}/accounts/${id}/import`, fd);
  }
  // ── dividends (calendar + monthly totals) ───────────────────────────────────
  dividends(): Observable<Dividend[]> { return this.http.get<Dividend[]>(`${this.base}/dividends`); }
  /** Per-account reconciliation working-state (rate + received tick before "Add as received"). */
  dividendRecon(): Observable<Record<string, { ps: number | null; received: boolean }>> {
    return this.http.get<Record<string, { ps: number | null; received: boolean }>>(`${this.base}/dividends/recon`);
  }
  saveDividendRecon(blob: Record<string, { ps: number | null; received: boolean }>): Observable<{ ok: boolean }> {
    return this.http.put<{ ok: boolean }>(`${this.base}/dividends/recon`, blob);
  }
  /** Log NSE-declared dividends for held stocks (dedup + prune on sell). */
  syncDeclaredDividends(): Observable<DividendSyncResult> { return this.http.post<DividendSyncResult>(`${this.base}/dividends/sync-declared`, {}); }
  /** Import a Corporate Actions CSV → dividends for held stocks (dedup, no delete). */
  importCorpActions(file: File): Observable<CorpActionImportResult> {
    const fd = new FormData(); fd.append('file', file);
    return this.http.post<CorpActionImportResult>(`${this.base}/dividends/corporate-actions/import`, fd);
  }
  corpActionUploads(): Observable<CorpActionUpload[]> {
    return this.http.get<CorpActionUpload[]>(`${this.base}/dividends/corporate-actions`);
  }
  deleteCorpActionUpload(id: string): Observable<{ ok: boolean }> {
    return this.http.delete<{ ok: boolean }>(`${this.base}/dividends/corporate-actions/${id}`);
  }

  // ── Reconcile dividends from a bank statement ───────────────────────────────
  dividendReconcilePreview(file: File, person = ''): Observable<DivReconcileResult> {
    const fd = new FormData(); fd.append('file', file, file.name);
    const q = person ? `?person=${encodeURIComponent(person)}` : '';
    return this.http.post<DivReconcileResult>(`${this.base}/dividends/reconcile/preview${q}`, fd);
  }
  dividendReconcileConfirm(div_ids: string[], meta?: DivReconcileMeta): Observable<{ ok: boolean; marked: number; upload: DivStatementUpload | null }> {
    return this.http.post<{ ok: boolean; marked: number; upload: DivStatementUpload | null }>(`${this.base}/dividends/reconcile/confirm`, { div_ids, ...(meta || {}) });
  }
  dividendReconcileUploads(): Observable<DivStatementUpload[]> {
    return this.http.get<DivStatementUpload[]>(`${this.base}/dividends/reconcile/uploads`);
  }

  // ── LTCG harvesting (per account + FY, from a Zerodha Tax P&L) ───────────────
  harvestAll(): Observable<{ allowance: number; records: HarvestRecord[] }> {
    return this.http.get<{ allowance: number; records: HarvestRecord[] }>(`${this.base}/harvest`);
  }
  harvestForAccount(accId: string): Observable<{ allowance: number; records: HarvestRecord[] }> {
    return this.http.get<{ allowance: number; records: HarvestRecord[] }>(`${this.base}/accounts/${accId}/harvest`);
  }
  harvestPreview(accId: string, file: File): Observable<HarvestPreview> {
    const fd = new FormData(); fd.append('file', file, file.name);
    return this.http.post<HarvestPreview>(`${this.base}/accounts/${accId}/harvest/preview`, fd);
  }
  harvestSave(accId: string, fy: string, parsed: TaxPnlParsed, fileName: string): Observable<{ ok: boolean; record: HarvestRecord }> {
    return this.http.post<{ ok: boolean; record: HarvestRecord }>(`${this.base}/accounts/${accId}/harvest`, { fy, parsed, file_name: fileName });
  }
  harvestDelete(accId: string, fyLabel: string): Observable<{ ok: boolean }> {
    return this.http.delete<{ ok: boolean }>(`${this.base}/accounts/${accId}/harvest/${encodeURIComponent(fyLabel)}`);
  }
  // ── Tax P&L (Zerodha Tax P&L statement → per-person, per-FY realized gains) ──
  importTaxPnl(file: File): Observable<{ ok: boolean; statement: TaxStatement }> {
    const fd = new FormData(); fd.append('file', file);
    return this.http.post<{ ok: boolean; statement: TaxStatement }>(`${this.base}/tax-pnl/import`, fd);
  }
  taxStatements(): Observable<{ statements: TaxStatement[]; ltcg_exempt: number; durable?: boolean }> {
    return this.http.get<{ statements: TaxStatement[]; ltcg_exempt: number; durable?: boolean }>(`${this.base}/tax-pnl`);
  }
  deleteTaxPnl(clientId: string, fy: string): Observable<{ ok: boolean }> {
    return this.http.delete<{ ok: boolean }>(`${this.base}/tax-pnl/${clientId}/${fy}`);
  }
  addDividend(body: { date: string; symbol: string; name?: string; per_share: number; shares: number; received?: boolean; status?: DividendStatus; currency?: 'INR' | 'USD'; person?: string; account_id?: string; note?: string }): Observable<Dividend> {
    return this.http.post<Dividend>(`${this.base}/dividends`, body);
  }
  updateDividend(id: string, patch: DividendPatch): Observable<Dividend> {
    return this.http.patch<Dividend>(`${this.base}/dividends/${id}`, patch);
  }
  deleteDividend(id: string): Observable<any> { return this.http.delete(`${this.base}/dividends/${id}`); }
  dividendMeta(): Observable<DividendMeta[]> { return this.http.get<DividendMeta[]>(`${this.base}/dividend-meta`); }
  setDividendMeta(symbol: string, prev_years: number | null): Observable<DividendMeta> {
    return this.http.put<DividendMeta>(`${this.base}/dividend-meta`, { symbol, prev_years });
  }
  dividendTds(): Observable<DividendTds[]> { return this.http.get<DividendTds[]>(`${this.base}/dividend-tds`); }
  setDividendTds(person: string, rate: number | null): Observable<DividendTds> {
    return this.http.put<DividendTds>(`${this.base}/dividend-tds`, { person, rate });
  }
  dividendCollected(): Observable<DividendCollected[]> { return this.http.get<DividendCollected[]>(`${this.base}/dividend-collected`); }
  setDividendCollected(symbol: string, person: string, collected: number | null): Observable<DividendCollected> {
    return this.http.put<DividendCollected>(`${this.base}/dividend-collected`, { symbol, person, collected });
  }

  loginUrl(id: string): Observable<{ login_url: string }> { return this.http.get<{ login_url: string }>(`${this.base}/accounts/${id}/login-url`); }
  connect(id: string, request_token: string): Observable<any> { return this.http.post<any>(`${this.base}/accounts/${id}/connect`, { request_token }); }
  refresh(id: string): Observable<any> { return this.http.post<any>(`${this.base}/accounts/${id}/refresh`, {}); }
  disconnect(id: string): Observable<{ ok: boolean; cleared: { source: string; id: string; label: string }[] }> { return this.http.post<any>(`${this.base}/accounts/${id}/disconnect`, {}); }
  edit(id: string, body: any): Observable<any> { return this.http.put<any>(`${this.base}/accounts/${id}`, body); }
  remove(id: string): Observable<any> { return this.http.delete(`${this.base}/accounts/${id}`); }

  // ── formatting ──────────────────────────────────────────────────────────────
  static inr(v: number | null | undefined): string {
    if (v == null || isNaN(v)) return '—';
    const a = Math.abs(v); const sign = v < 0 ? '-' : '';
    if (a >= 1e7) return `${sign}₹${(a / 1e7).toFixed(2)} Cr`;
    if (a >= 1e5) return `${sign}₹${(a / 1e5).toFixed(2)} L`;
    return `${sign}₹${Math.round(a).toLocaleString('en-IN')}`;
  }
  static pct(v: number | null | undefined): string {
    if (v == null || isNaN(v)) return '—';
    return `${v >= 0 ? '+' : ''}${(v * 100).toFixed(2)}%`;
  }
}
