import { Injectable, inject, signal } from '@angular/core';
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

export interface FnoTradebookMeta { count: number; name: string | null; at: string | null; }
export interface FnoTradebookRec {
  id: string; name: string; date_from: string | null; date_to: string | null;
  count: number; added?: number; at: string | null; legacy?: boolean;
}
export interface FnoPnlStatement {
  id: string; name: string; date_from: string | null; date_to: string | null; symbols: number;
  realized: number; unrealized: number; charges: number; other?: number; at: string | null;
}
export interface FnoStatementCoverage {
  count: number; date_from: string | null; date_to: string | null;
  charges: number; other: number; realized: number; unrealized: number;
}
export interface FnoTradebooks {
  tradebooks: FnoTradebookRec[];
  coverage: { date_from: string | null; date_to: string | null; fills: number; books: number };
  statement?: FnoPnlStatement | null;            // aggregate summary (back-compat)
  statements?: FnoPnlStatement[];                // the list, for the timeline
  statement_coverage?: FnoStatementCoverage;
}
export interface FnoAccount {
  id: string; person: string | null; account_label: string;
  kite_user_id: string | null; user_name: string | null;
  status: string; connected: boolean; uses_env_app: boolean; is_paid_app?: boolean;
  api_key_hint: string | null; token_updated_at: string | null;
  last_synced: string | null; note: string | null; created_at: string;
  tradebook?: FnoTradebookMeta;
  price_feed?: boolean;
  pnl_charges?: number | null;   // brokerage + taxes from the imported P&L statement
  pnl_other?: number | null;     // other credits/debits from the statement
  strategy?: string | null;      // pins this account's non-crude trades to one strategy
  login_url?: string;
}
export interface FnoStrategyLive {
  day_pnl: number; realized: number; unrealized: number;
  positions: number; open_positions: number;
}
export interface FnoPosition {
  tradingsymbol: string; exchange: string; quantity: number; average_price: number;
  last_price: number; m2m: number; strategy: string; product: string;
}
export interface FnoLiveAccount {
  id: string; account_label: string; person: string | null; kite_user_id: string | null;
  day_pnl: number; by_strategy: Record<string, FnoStrategyLive>; positions: FnoPosition[];
}
export interface FnoLive {
  ts: string | null; market_open: boolean; accounts: FnoLiveAccount[];
  total_day_pnl: number; by_strategy: Record<string, number>;
}
export interface FnoPledged { value: number | null; source: 'kite' | 'manual' | null; updated_at: string | null; }
export interface FnoCagr { cagr: number | null; total_return: number; days: number; pledged: number; }
export interface FnoDrawdown { mdd: number; peak_date: string | null; trough_date: string | null; }
export interface FnoMaxDrawdown { combined: FnoDrawdown; by_strategy: Record<string, FnoDrawdown>; }
export interface FnoSummary {
  accounts: FnoAccount[]; market_open: boolean; live: FnoLive;
  overall_pnl: number; today_pnl: number; by_strategy: Record<string, number>;
  since: string | null; trading_days: number;
  pledged: FnoPledged; cagr: FnoCagr | null; max_drawdown: FnoMaxDrawdown;
}
export interface FnoCalendarDay {
  date: string; total: number; by_strategy: Record<string, number>; trades_count: number;
}
export interface FnoCalendar { year: number; month: number; days: FnoCalendarDay[]; month_total: number; }
export interface FnoSeriesAcct { account_id: string; label: string; person: string | null; total: number; }
export interface FnoSeriesPoint { t: string; pnl: number; day?: number; by_account?: FnoSeriesAcct[]; by_strategy?: Record<string, number>; }
export interface FnoSeries { range: string; date?: string; points: FnoSeriesPoint[]; }
export interface FnoStrategyStats {
  strategy: string; total: number; today: number; days: number;
  win_days: number; loss_days: number; win_rate: number | null; avg_day: number;
  best: { date: string; total: number }; worst: { date: string; total: number };
  recent: { date: string; total: number }[];
}
export interface FnoTrade {
  id: string; account_id: string; trade_id: string; order_id: string;
  strategy: string; tradingsymbol: string; exchange: string; instrument_type: string;
  transaction_type: string; quantity: number; price: number; product: string | null;
  trade_date: string; fill_ts: string | null; source: string;
}
export interface FnoLoginLog {
  id: string; account_id: string | null; account_label: string | null;
  event: string; detail: string; created_at: string;
}
export interface FnoOpenLeg {
  account_id: string; account_label: string; person: string | null;
  tradingsymbol: string; exchange: string; instrument_type: string; strategy: string;
  side: 'LONG' | 'SHORT' | 'CLOSED'; qty: number; avg: number; ltp: number | null;
  invested: number; unrealized: number | null;
  day_pnl?: number | null;      // live per-leg P&L (matches Kite terminal); null when carried
  realized?: number;            // intraday booked component (round-trips today)
  closed?: boolean; live?: boolean;
  // parsed instrument geometry for the payoff diagram (backend-provided)
  root?: string; strike?: number | null; opt_type?: 'CE' | 'PE' | 'FUT' | null;
  expiry?: string | null; multiplier?: number;
}
export interface FnoOptionQuote { price: number; iv?: number; delta?: number; oi?: number; volume?: number; symbol?: string; }
export interface FnoOptionRow { strike: number; atm?: boolean; ce: FnoOptionQuote; pe: FnoOptionQuote; }
export interface FnoOptionChain {
  underlying: string; spot: number; expiry: string; dte: number; lot_size: number;
  atm_strike: number; chain: FnoOptionRow[];
}
export interface FnoOpenPositions {
  positions: FnoOpenLeg[]; count: number; priced_count: number; unpriced_count: number;
  total_unrealized: number; total_invested: number;
  total_day_pnl?: number | null;   // live positions P&L (matches Kite) or null when carried
  live_mode?: boolean;             // true → card mirrors Kite's live Positions screen
  by_strategy: { strategy: string; unrealized: number; day_pnl?: number; count: number }[];
  feed_ok: boolean; as_of: string; expired_count?: number;
  spots?: Record<string, number>;   // best-effort underlying price per root (payoff x-axis)
}

export interface FnoHealthIssue {
  key: string; kind: 'relogin' | 'master' | 'stale' | 'nodata'; severity: 'high' | 'medium';
  account_id: string; person: string; title: string; detail: string;
  state_token: string; fix_label: string; dismissed_as?: string;
}
export interface FnoHealth {
  today: string; issues: FnoHealthIssue[]; count: number; high_count: number;
  ignored: FnoHealthIssue[]; ignored_count: number; ok: boolean;
}

@Injectable({ providedIn: 'root' })
export class FnoService {
  private http = inject(HttpClient);
  private base = `${_apiBase()}/fno`;

  // ── data-health (dashboard bell) ────────────────────────────────────────────
  health() { return this.http.get<FnoHealth>(`${this.base}/health`); }
  dismissHealth(key: string, token: string, action: 'done' | 'ignore') {
    return this.http.post<FnoHealth>(`${this.base}/health/dismiss`, { key, token, action });
  }
  restoreHealth(key: string) {
    return this.http.post<FnoHealth>(`${this.base}/health/restore`, { key, token: '', action: 'done' });
  }

  // ── Live WebSocket (per-second; storage stays 1-minute server-side) ─────────
  live = signal<FnoLive | null>(null);
  wsConnected = signal(false);
  private ws: WebSocket | null = null;
  private wsWanted = false;

  private _wsUrl(): string {
    if (typeof window === 'undefined') return 'ws://localhost:8000/ws/fno';
    const override = (window as any).__WS_URL__;
    if (override) return String(override).replace(/\/ws\/ticker$/, '/ws/fno');
    const { hostname, host, protocol } = window.location;
    if (hostname === 'localhost' || hostname === '127.0.0.1') return 'ws://localhost:8000/ws/fno';
    return `${protocol === 'https:' ? 'wss:' : 'ws:'}//${host}/ws/fno`;
  }

  connectLive() {
    this.wsWanted = true;
    if (this.ws) return;
    this.ws = new WebSocket(this._wsUrl());
    this.ws.onopen = () => this.wsConnected.set(true);
    this.ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        if (msg.type === 'fno_pnl') this.live.set(msg.data);
      } catch { /* ignore malformed frame */ }
    };
    this.ws.onclose = () => {
      this.wsConnected.set(false);
      this.ws = null;
      if (this.wsWanted) setTimeout(() => this.connectLive(), 3000);
    };
    this.ws.onerror = () => this.ws?.close();
  }

  disconnectLive() {
    this.wsWanted = false;
    this.ws?.close();
    this.ws = null;
  }

  // ── REST ─────────────────────────────────────────────────────────────────────
  private _acc(accounts?: string[]): string {
    return accounts && accounts.length ? `accounts=${accounts.join(',')}` : '';
  }
  summary(accounts?: string[]): Observable<FnoSummary> {
    const a = this._acc(accounts);
    return this.http.get<FnoSummary>(`${this.base}/summary${a ? '?' + a : ''}`);
  }
  accounts(): Observable<FnoAccount[]> { return this.http.get<FnoAccount[]>(`${this.base}/accounts`); }
  addAccount(body: { account_label: string; person?: string; api_key?: string; api_secret?: string }): Observable<FnoAccount> {
    return this.http.post<FnoAccount>(`${this.base}/accounts`, body);
  }
  editAccount(id: string, body: { account_label?: string; person?: string; note?: string; api_key?: string; api_secret?: string }): Observable<FnoAccount> {
    return this.http.put<FnoAccount>(`${this.base}/accounts/${id}`, body);
  }
  removeAccount(id: string): Observable<any> { return this.http.delete(`${this.base}/accounts/${id}`); }
  loginUrl(id: string): Observable<{ login_url: string }> {
    return this.http.get<{ login_url: string }>(`${this.base}/accounts/${id}/login-url`);
  }
  connect(id: string, body: { request_token?: string; access_token?: string }): Observable<any> {
    return this.http.post<any>(`${this.base}/accounts/${id}/connect`, body);
  }
  disconnect(id: string): Observable<{ ok: boolean; cleared: { source: string; id: string; label: string }[] }> {
    return this.http.post<any>(`${this.base}/accounts/${id}/disconnect`, {});
  }
  credentials(id: string): Observable<{ api_key: string; api_secret: string }> {
    return this.http.get<any>(`${this.base}/accounts/${id}/credentials`);
  }
  syncTrades(id: string): Observable<{ ok: boolean; added: number }> {
    return this.http.post<{ ok: boolean; added: number }>(`${this.base}/accounts/${id}/sync-trades`, {});
  }
  importTradebook(id: string, file: File): Observable<any> {
    const fd = new FormData(); fd.append('file', file);
    return this.http.post<any>(`${this.base}/accounts/${id}/import-tradebook`, fd);
  }
  importPnlStatement(id: string, file: File): Observable<any> {
    const fd = new FormData(); fd.append('file', file);
    return this.http.post<any>(`${this.base}/accounts/${id}/import-pnl-statement`, fd);
  }
  deleteOnePnlStatement(id: string, stmtId: string): Observable<FnoTradebooks & { ok: boolean }> {
    return this.http.delete<FnoTradebooks & { ok: boolean }>(`${this.base}/accounts/${id}/pnl-statements/${stmtId}`);
  }
  deletePnlStatement(id: string): Observable<any> {
    return this.http.delete<any>(`${this.base}/accounts/${id}/pnl-statement`);
  }
  deleteTradebook(id: string): Observable<{ ok: boolean; removed: number }> {
    return this.http.delete<{ ok: boolean; removed: number }>(`${this.base}/accounts/${id}/tradebook`);
  }
  listTradebooks(id: string): Observable<FnoTradebooks> {
    return this.http.get<FnoTradebooks>(`${this.base}/accounts/${id}/tradebooks`);
  }
  deleteOneTradebook(id: string, tbId: string): Observable<FnoTradebooks & { ok: boolean; removed: number }> {
    return this.http.delete<FnoTradebooks & { ok: boolean; removed: number }>(`${this.base}/accounts/${id}/tradebooks/${tbId}`);
  }
  setPriceFeed(accountId: string | null): Observable<{ ok: boolean; account_id: string | null }> {
    return this.http.put<{ ok: boolean; account_id: string | null }>(`${this.base}/price-feed`, { account_id: accountId });
  }
  setAccountStrategy(id: string, strategy: string | null): Observable<{ ok: boolean; strategy: string | null }> {
    return this.http.put<{ ok: boolean; strategy: string | null }>(`${this.base}/accounts/${id}/strategy`, { strategy });
  }
  setLegStrategy(accountId: string, tradingsymbol: string, strategy: string | null): Observable<{ ok: boolean; strategy: string | null }> {
    return this.http.put<{ ok: boolean; strategy: string | null }>(`${this.base}/open-positions/strategy`, { account_id: accountId, tradingsymbol, strategy });
  }
  loginLog(limit = 100): Observable<FnoLoginLog[]> {
    return this.http.get<FnoLoginLog[]>(`${this.base}/login-log?limit=${limit}`);
  }
  calendar(year: number, month: number, accounts?: string[]): Observable<FnoCalendar> {
    const a = this._acc(accounts);
    return this.http.get<FnoCalendar>(`${this.base}/calendar?year=${year}&month=${month}${a ? '&' + a : ''}`);
  }
  series(range: string, date?: string, accounts?: string[]): Observable<FnoSeries> {
    const d = date ? `&date=${date}` : '';
    const a = this._acc(accounts);
    return this.http.get<FnoSeries>(`${this.base}/series?range=${range}${d}${a ? '&' + a : ''}`);
  }
  // intraday graph of the open-positions (carry-forward) unrealized P&L for today
  openSeries(accounts?: string[]): Observable<{ date: string; points: { t: string; pnl: number }[] }> {
    const a = this._acc(accounts);
    return this.http.get<{ date: string; points: { t: string; pnl: number }[] }>(`${this.base}/open-series${a ? '?' + a : ''}`);
  }
  strategies(accounts?: string[]): Observable<FnoStrategyStats[]> {
    const a = this._acc(accounts);
    return this.http.get<FnoStrategyStats[]>(`${this.base}/strategies${a ? '?' + a : ''}`);
  }
  setPledged(value: number | null): Observable<FnoPledged> {
    return this.http.put<FnoPledged>(`${this.base}/pledged`, { value });
  }
  refreshPledged(): Observable<FnoPledged> {
    return this.http.post<FnoPledged>(`${this.base}/pledged/refresh`, {});
  }
  trades(date?: string, strategy?: string, accounts?: string[]): Observable<FnoTrade[]> {
    const q: string[] = [];
    if (date) q.push(`date=${date}`);
    if (strategy) q.push(`strategy=${strategy}`);
    if (accounts && accounts.length) q.push(`accounts=${accounts.join(',')}`);
    return this.http.get<FnoTrade[]>(`${this.base}/trades${q.length ? '?' + q.join('&') : ''}`);
  }
  setTradeStrategy(tradePk: string, strategy: string): Observable<FnoTrade> {
    return this.http.patch<FnoTrade>(`${this.base}/trades/${tradePk}`, { strategy });
  }
  /** Bulk-set the strategy on many trades at once (Trade-History bulk assign). */
  // ── strategy catalog (the list of strategies you can tag trades with) ──────
  getStrategyCatalog(): Observable<{ key: string; label: string; color: string }[]> {
    return this.http.get<{ key: string; label: string; color: string }[]>(`${this.base}/strategy-catalog`);
  }
  addStrategy(key: string, label: string, color: string): Observable<{ key: string; label: string; color: string }[]> {
    return this.http.post<{ key: string; label: string; color: string }[]>(`${this.base}/strategy-catalog`, { key, label, color });
  }
  removeStrategy(key: string): Observable<{ key: string; label: string; color: string }[]> {
    return this.http.delete<{ key: string; label: string; color: string }[]>(`${this.base}/strategy-catalog/${encodeURIComponent(key)}`);
  }
  bulkSetTradeStrategy(ids: string[], strategy: string): Observable<{ ok: boolean; updated: number; accounts: number }> {
    return this.http.patch<{ ok: boolean; updated: number; accounts: number }>(`${this.base}/trades/bulk/strategy`, { ids, strategy });
  }
  /** Bulk-set the strategy on many open legs at once (Open-Positions bulk assign). */
  bulkSetLegStrategy(legs: { account_id: string; tradingsymbol: string }[], strategy: string | null): Observable<{ ok: boolean; updated: number }> {
    return this.http.put<{ ok: boolean; updated: number }>(`${this.base}/open-positions/bulk-strategy`, { legs, strategy });
  }
  openPositions(accounts?: string[]): Observable<FnoOpenPositions> {
    const a = this._acc(accounts);
    return this.http.get<FnoOpenPositions>(`${this.base}/open-positions${a ? '?' + a : ''}`);
  }

  // ── options chain (for the what-if picker) ───────────────────────────────────
  private optBase = `${_apiBase()}/options`;
  optionExpiries(underlying: string): Observable<string[]> {
    return this.http.get<string[]>(`${this.optBase}/expiries?underlying=${encodeURIComponent(underlying)}`);
  }
  optionChain(underlying: string, expiry: string, spot: number): Observable<FnoOptionChain> {
    const q = `underlying=${encodeURIComponent(underlying)}&expiry=${encodeURIComponent(expiry)}&spot=${spot || 0}`;
    return this.http.get<FnoOptionChain>(`${this.optBase}/chain?${q}`);
  }

  // ── formatting ───────────────────────────────────────────────────────────────
  static inr(v: number | null | undefined): string {
    if (v == null || isNaN(v)) return '—';
    const a = Math.abs(v); const sign = v < 0 ? '−' : '';
    if (a >= 1e7) return `${sign}₹${(a / 1e7).toFixed(2)} Cr`;
    if (a >= 1e5) return `${sign}₹${(a / 1e5).toFixed(2)} L`;
    return `${sign}₹${Math.round(a).toLocaleString('en-IN')}`;
  }
  static inrSigned(v: number | null | undefined): string {
    if (v == null || isNaN(v)) return '—';
    return (v > 0 ? '+' : '') + FnoService.inr(v);
  }
  /** Exact rupee value, signed, full Indian-comma grouping — no L/Cr shorthand.
   *  Used where precision matters (e.g. the chart hover tooltip). */
  static inrFull(v: number | null | undefined): string {
    if (v == null || isNaN(v)) return '—';
    const sign = v > 0 ? '+' : v < 0 ? '−' : '';
    return `${sign}₹${Math.round(Math.abs(v)).toLocaleString('en-IN')}`;
  }
}
