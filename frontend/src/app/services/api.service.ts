import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';

// API base — picks the right backend automatically:
//   • Dev (Angular `ng serve` on :4200)  → http://localhost:8000/api
//   • Production (built + served by FastAPI itself) → same-origin /api
//   • Override via window.__API_BASE__ if you want to point a static frontend
//     at a different backend host.
function _apiBase(): string {
  if (typeof window === 'undefined') return 'http://localhost:8000/api';
  const override = (window as any).__API_BASE__;
  if (override) return override;
  const { hostname, protocol, host } = window.location;
  if (hostname === 'localhost' || hostname === '127.0.0.1') {
    // local dev — backend runs on a different port
    return 'http://localhost:8000/api';
  }
  return `${protocol}//${host}/api`;
}
const BASE = _apiBase();

export interface Funds { cash: number; pnl: number; paper_mode: boolean; initial_capital: number; }
export interface Candle { date: string; open: number; high: number; low: number; close: number; volume: number; }

@Injectable({ providedIn: 'root' })
export class ApiService {
  private http = inject(HttpClient);

  // ── Funds / Account ────────────────────────────────────────────────────────
  getFunds(): Observable<Funds> { return this.http.get<Funds>(`${BASE}/account/funds`); }
  resetPaper(): Observable<any> { return this.http.post<any>(`${BASE}/paper/reset`, {}); }

  // ── Pinned sidebar tabs (durable, server-persisted) ────────────────────────
  getNavPins(): Observable<{ pins: string[] }> { return this.http.get<{ pins: string[] }>(`${BASE}/dashboard/nav-pins`); }
  saveNavPins(pins: string[]): Observable<{ pins: string[] }> { return this.http.put<{ pins: string[] }>(`${BASE}/dashboard/nav-pins`, { pins }); }

  // ── Option chain ───────────────────────────────────────────────────────────
  getOptionUnderlyings(): Observable<any[]> { return this.http.get<any[]>(`${BASE}/options/underlyings`); }
  getOptionExpiries(underlying: string): Observable<string[]> {
    return this.http.get<string[]>(`${BASE}/options/expiries`, { params: { underlying } });
  }
  getOptionChain(underlying: string, expiry: string): Observable<any> {
    return this.http.get<any>(`${BASE}/options/chain`, { params: { underlying, expiry } });
  }
  cheapPeScan(opts: {
    underlying?: string;
    min_dte?: number; max_dte?: number;
    moneyness_min?: number; moneyness_max?: number;
    weights?: string; top_n?: number;
  }): Observable<any> {
    const params: any = {};
    for (const [k, v] of Object.entries(opts)) {
      if (v !== undefined && v !== null && v !== '') params[k] = v;
    }
    return this.http.get<any>(`${BASE}/options/cheap-pe-scan`, { params });
  }
  richPeScan(opts: {
    underlying?: string;
    min_dte?: number; max_dte?: number;
    moneyness_min?: number; moneyness_max?: number;
    weights?: string; top_n?: number;
  }): Observable<any> {
    const params: any = {};
    for (const [k, v] of Object.entries(opts)) {
      if (v !== undefined && v !== null && v !== '') params[k] = v;
    }
    return this.http.get<any>(`${BASE}/options/rich-pe-scan`, { params });
  }
  listStrategies(): Observable<any> {
    return this.http.get<any>(`${BASE}/strategies`);
  }

  scanBwbAll(opts: {
    maxLoss: number; minProfit: number;
    minOi?: number; minVolume?: number;
    maxSpreadPct?: number; maxAtmPct?: number;
    minLots?: number; minPop?: number; minEv?: number;
    expiriesPerUnderlying?: number; sortBy?: string; autoRelax?: boolean;
    page?: number; perPage?: number;
  }): Observable<any> {
    return this.http.get<any>(`${BASE}/options/scan-bwb-all`, {
      params: {
        max_loss:        opts.maxLoss,
        min_profit:      opts.minProfit,
        min_oi:          opts.minOi          ?? 10000,
        min_volume:      opts.minVolume      ?? 100,
        max_spread_pct:  opts.maxSpreadPct   ?? 5.0,
        max_atm_pct:     opts.maxAtmPct      ?? 5.0,
        min_lots:        opts.minLots        ?? 5,
        min_pop:         opts.minPop         ?? 0,
        min_ev:          opts.minEv          ?? -999999,
        expiries_per_underlying: opts.expiriesPerUnderlying ?? 10,
        sort_by:         opts.sortBy         ?? 'pop',
        auto_relax:      opts.autoRelax      ?? true,
        page:            opts.page           ?? 1,
        per_page:        opts.perPage        ?? 30,
      }
    });
  }

  scanBwb(underlying: string, expiry: string, opts: {
    maxLoss: number; minProfit: number;
    minOi?: number; minVolume?: number;
    maxSpreadPct?: number; maxAtmPct?: number;
    minLiquidity?: number; minLots?: number;
    minPop?: number; minEv?: number;
    sortBy?: string;
    page?: number; perPage?: number;
  }): Observable<any> {
    return this.http.get<any>(`${BASE}/options/scan-bwb`, {
      params: {
        underlying,
        expiry,
        max_loss:        opts.maxLoss,
        min_profit:      opts.minProfit,
        min_oi:          opts.minOi          ?? 10000,
        min_volume:      opts.minVolume      ?? 100,
        max_spread_pct:  opts.maxSpreadPct   ?? 5.0,
        max_atm_pct:     opts.maxAtmPct      ?? 5.0,
        min_liquidity:   opts.minLiquidity   ?? 0,
        min_lots:        opts.minLots        ?? 5,
        min_pop:         opts.minPop         ?? 0,
        min_ev:          opts.minEv          ?? -999999,
        sort_by:         opts.sortBy         ?? 'profit',
        page:            opts.page           ?? 1,
        per_page:        opts.perPage        ?? 30,
      }
    });
  }

  scanBatman(underlying: string, expiry: string, opts: {
    maxLoss: number; minProfit: number;
    minOi?: number; minVolume?: number;
    maxSpreadPct?: number; maxAtmPct?: number;
    minLots?: number; minPop?: number; minEv?: number;
    sortBy?: string; page?: number; perPage?: number;
  }): Observable<any> {
    return this.http.get<any>(`${BASE}/options/scan-batman`, {
      params: {
        underlying,
        expiry,
        max_loss:       opts.maxLoss,
        min_profit:     opts.minProfit,
        min_oi:         opts.minOi        ?? 10000,
        min_volume:     opts.minVolume    ?? 100,
        max_spread_pct: opts.maxSpreadPct ?? 5.0,
        max_atm_pct:    opts.maxAtmPct    ?? 8.0,
        min_lots:       opts.minLots      ?? 5,
        min_pop:        opts.minPop       ?? 0,
        min_ev:         opts.minEv        ?? -999999,
        sort_by:        opts.sortBy       ?? 'ev,profit',
        page:           opts.page         ?? 1,
        per_page:       opts.perPage      ?? 30,
      }
    });
  }

  scanBatmanAll(opts: {
    maxLoss: number; minProfit: number;
    minOi?: number; minVolume?: number;
    maxSpreadPct?: number; maxAtmPct?: number;
    minLots?: number; minPop?: number; minEv?: number;
    expiriesPerUnderlying?: number; sortBy?: string; autoRelax?: boolean;
    page?: number; perPage?: number;
  }): Observable<any> {
    return this.http.get<any>(`${BASE}/options/scan-batman-all`, {
      params: {
        max_loss:        opts.maxLoss,
        min_profit:      opts.minProfit,
        min_oi:          opts.minOi          ?? 10000,
        min_volume:      opts.minVolume      ?? 100,
        max_spread_pct:  opts.maxSpreadPct   ?? 5.0,
        max_atm_pct:     opts.maxAtmPct      ?? 8.0,
        min_lots:        opts.minLots        ?? 5,
        min_pop:         opts.minPop         ?? 0,
        min_ev:          opts.minEv          ?? -999999,
        expiries_per_underlying: opts.expiriesPerUnderlying ?? 10,
        sort_by:         opts.sortBy         ?? 'ev,profit',
        auto_relax:      opts.autoRelax      ?? true,
        page:            opts.page           ?? 1,
        per_page:        opts.perPage        ?? 30,
      }
    });
  }

  scanIronCondor(underlying: string, expiry: string, opts: {
    maxLoss: number; minProfit: number;
    minOi?: number; minVolume?: number;
    maxSpreadPct?: number; maxAtmPct?: number;
    minLots?: number; minPop?: number; minEv?: number;
    sortBy?: string; page?: number; perPage?: number;
  }): Observable<any> {
    return this.http.get<any>(`${BASE}/options/scan-iron-condor`, {
      params: {
        underlying,
        expiry,
        max_loss:       opts.maxLoss,
        min_profit:     opts.minProfit,
        min_oi:         opts.minOi        ?? 10000,
        min_volume:     opts.minVolume    ?? 100,
        max_spread_pct: opts.maxSpreadPct ?? 5.0,
        max_atm_pct:    opts.maxAtmPct    ?? 5.0,
        min_lots:       opts.minLots      ?? 5,
        min_pop:        opts.minPop       ?? 0,
        min_ev:         opts.minEv        ?? -999999,
        sort_by:        opts.sortBy       ?? 'ev,pop',
        page:           opts.page         ?? 1,
        per_page:       opts.perPage      ?? 30,
      }
    });
  }

  scanIronCondorAll(opts: {
    maxLoss: number; minProfit: number;
    minOi?: number; minVolume?: number;
    maxSpreadPct?: number; maxAtmPct?: number;
    minLots?: number; minPop?: number; minEv?: number;
    expiriesPerUnderlying?: number; sortBy?: string; autoRelax?: boolean;
    page?: number; perPage?: number;
  }): Observable<any> {
    return this.http.get<any>(`${BASE}/options/scan-iron-condor-all`, {
      params: {
        max_loss:        opts.maxLoss,
        min_profit:      opts.minProfit,
        min_oi:          opts.minOi          ?? 10000,
        min_volume:      opts.minVolume      ?? 100,
        max_spread_pct:  opts.maxSpreadPct   ?? 5.0,
        max_atm_pct:     opts.maxAtmPct      ?? 5.0,
        min_lots:        opts.minLots        ?? 5,
        min_pop:         opts.minPop         ?? 0,
        min_ev:          opts.minEv          ?? -999999,
        expiries_per_underlying: opts.expiriesPerUnderlying ?? 10,
        sort_by:         opts.sortBy         ?? 'ev,pop',
        auto_relax:      opts.autoRelax      ?? true,
        page:            opts.page           ?? 1,
        per_page:        opts.perPage        ?? 30,
      }
    });
  }

  // ── Strategy ───────────────────────────────────────────────────────────────
  executeStrategy(body: any): Observable<any> { return this.http.post<any>(`${BASE}/strategy/execute`, body); }
  squareOff(sid: string): Observable<any> { return this.http.post<any>(`${BASE}/strategy/square-off/${sid}`, {}); }
  updateSlTarget(sid: string, sl: number | null, target: number | null): Observable<any> {
    return this.http.put<any>(`${BASE}/strategy/${sid}/sl-target`, { sl_amount: sl, target_amount: target });
  }
  getOpenStrategies(): Observable<any[]> { return this.http.get<any[]>(`${BASE}/strategy/open`); }
  getStrategyHistory(): Observable<any[]> { return this.http.get<any[]>(`${BASE}/strategy/history`); }
  getStrategyStats(): Observable<any> { return this.http.get<any>(`${BASE}/strategy/stats`); }
  getPnlHistory(): Observable<any[]> { return this.http.get<any[]>(`${BASE}/strategy/pnl-history`); }

  // ── Auth ───────────────────────────────────────────────────────────────────
  getAuthStatus(): Observable<any> { return this.http.get<any>(`${BASE}/auth/status`); }
  getSupabaseStatus(): Observable<any> { return this.http.get<any>(`${BASE}/auth/supabase-status`); }

  // ── Futures Price ─────────────────────────────────────────────────────────
  getFuturesPrice(underlying: string, riskFreeRate = 6.5, dividendYield = 1.3): Observable<any> {
    return this.http.get<any>(`${BASE}/options/futures-price`, {
      params: {
        underlying,
        risk_free_rate: (riskFreeRate / 100).toFixed(4),
        dividend_yield: (dividendYield / 100).toFixed(4),
      },
    });
  }

  // ── Covered Call ───────────────────────────────────────────────────────────
  getCoveredCallSetup(underlying: string, expiry: string): Observable<any> {
    const params: any = { underlying };
    if (expiry) params['expiry'] = expiry;
    return this.http.get<any>(`${BASE}/covered-call/setup`, { params });
  }


  analyzeCoveredCall(opts: {
    underlying: string; expiry: string; strike: number;
    lots: number; niftybees_price?: number; custom_shares?: number;
    entry_premium?: number; entry_niftybees_price?: number; entry_nifty?: number;
  }): Observable<any> {
    const params: any = {
      underlying:             opts.underlying,
      expiry:                 opts.expiry,
      strike:                 opts.strike,
      lots:                   opts.lots,
      niftybees_price:        opts.niftybees_price        ?? 0,
      custom_shares:          opts.custom_shares          ?? 0,
      entry_premium:          opts.entry_premium          ?? 0,
      entry_niftybees_price:  opts.entry_niftybees_price  ?? 0,
      entry_nifty:            opts.entry_nifty            ?? 0,
    };
    return this.http.get<any>(`${BASE}/covered-call/analyze`, { params });
  }

  chatWithAI(query: string, position: any, history: { role: string; content: string }[]): Observable<{ answer: string }> {
    return this.http.post<{ answer: string }>(`${BASE}/ai/chat`, { query, position, history });
  }

  // ── Covered call positions ──────────────────────────────────────────────────
  getCoveredCallPositions(): Observable<{ spot: number; niftybees_price: number; positions: any[] }> {
    return this.http.get<{ spot: number; niftybees_price: number; positions: any[] }>(`${BASE}/covered-call/positions`);
  }
  createCoveredCallPosition(body: any): Observable<any> {
    return this.http.post<any>(`${BASE}/covered-call/positions`, body);
  }
  deleteCoveredCallPosition(pid: string): Observable<any> {
    return this.http.delete<any>(`${BASE}/covered-call/positions/${pid}`);
  }
  updateCoveredCallPosition(pid: string, body: any): Observable<any> {
    return this.http.put<any>(`${BASE}/covered-call/positions/${pid}`, body);
  }
  updateCoveredCallTags(pid: string, tags: string[]): Observable<any> {
    return this.http.put<any>(`${BASE}/covered-call/positions/${pid}/tags`, { tags });
  }
  closeCoveredCallCycle(
    pid: string,
    exitPrice: number,
    extras?: {
      close_kind?:     string;
      nb_action?:      string;
      nb_shares_sold?: number;
      nb_sell_price?:  number;
      notes?:          string;
    },
  ): Observable<any> {
    return this.http.post<any>(`${BASE}/covered-call/positions/${pid}/close-call`, {
      exit_price: exitPrice,
      ...(extras || {}),
    });
  }
  addCoveredCallCycle(pid: string, call: any): Observable<any> {
    return this.http.post<any>(`${BASE}/covered-call/positions/${pid}/add-call`, call);
  }
  getCoveredCallPremarket(params?: any): Observable<any> {
    return this.http.get<any>(`${BASE}/covered-call/premarket`, { params });
  }
  getCoveredCallBestTrade(topN = 4, scanExpiries = 6, cadence: 'monthly' | 'quarterly' = 'monthly'): Observable<any> {
    return this.http.get<any>(`${BASE}/covered-call/best-trade`, {
      params: { top_n: topN, scan_expiries: scanExpiries, cadence },
    });
  }

  // ── Protected Wheel ────────────────────────────────────────────────────────
  scanProtectedWheel(params?: {
    capital?: number; csp_expiry?: string; cc_expiry?: string;
    has_nb_position?: boolean; nb_entry?: number;
  }): Observable<any> {
    // Strip undefined / null / empty-string params — Angular otherwise sends
    // them as the literal string "undefined" which FastAPI 422s on.
    const clean: Record<string, string> = {};
    for (const [k, v] of Object.entries(params || {})) {
      if (v === undefined || v === null || v === '') continue;
      clean[k] = String(v);
    }
    return this.http.get<any>(`${BASE}/protected-wheel/scan`, { params: clean });
  }
  bestCycleProtectedWheel(capital = 1800000, scanExpiries = 4): Observable<any> {
    return this.http.get<any>(`${BASE}/protected-wheel/best-cycle`, {
      params: { capital: String(capital), scan_expiries: String(scanExpiries) },
    });
  }

  // ── Hedges ────────────────────────────────────────────────────────────────
  listHedges(status?: string): Observable<any> {
    const params: Record<string, string> = {};
    if (status) params['status'] = status;
    return this.http.get<any>(`${BASE}/hedges`, { params });
  }
  getHedge(hid: string): Observable<any> {
    return this.http.get<any>(`${BASE}/hedges/${hid}`);
  }
  createHedge(body: {
    strike: number; expiry: string; lots: number; lot_size: number;
    premium_paid: number; symbol?: string; notes?: string;
    tagged_strategies?: string[];
  }): Observable<any> {
    return this.http.post<any>(`${BASE}/hedges`, body);
  }
  updateHedge(hid: string, updates: { notes?: string; tagged_strategies?: string[] }): Observable<any> {
    return this.http.put<any>(`${BASE}/hedges/${hid}`, updates);
  }
  deleteHedge(hid: string): Observable<any> {
    return this.http.delete<any>(`${BASE}/hedges/${hid}`);
  }
  closeHedge(hid: string, closePrice: number, kind = 'closed'): Observable<any> {
    return this.http.post<any>(`${BASE}/hedges/${hid}/close`, { close_price: closePrice, kind });
  }
  rollHedge(hid: string, body: {
    close_price: number; new_strike: number; new_expiry: string;
    new_lots: number; new_lot_size: number; new_premium: number;
    new_symbol?: string; transfer_tags?: boolean;
  }): Observable<any> {
    return this.http.post<any>(`${BASE}/hedges/${hid}/roll`, body);
  }
  tagStrategyToHedge(hid: string, strategyId: string): Observable<any> {
    return this.http.post<any>(`${BASE}/hedges/${hid}/tag`, { strategy_id: strategyId });
  }
  untagStrategyFromHedge(hid: string, strategyId: string): Observable<any> {
    return this.http.post<any>(`${BASE}/hedges/${hid}/untag`, { strategy_id: strategyId });
  }
  scanHedgeCandidates(): Observable<any> {
    return this.http.get<any>(`${BASE}/hedges/scan/candidates`);
  }
  hedgesForStrategy(strategyId: string): Observable<any> {
    return this.http.get<any>(`${BASE}/hedges/for-strategy/${strategyId}`);
  }

  // ── CC Simulator ────────────────────────────────────────────────────────────
  ccSimList(): Observable<any[]> {
    return this.http.get<any[]>(`${BASE}/cc-simulator/list`);
  }
  ccSimGet(id: string): Observable<any> {
    return this.http.get<any>(`${BASE}/cc-simulator/${id}`);
  }
  ccSimCreate(body: { name: string; capital: number; slab_rate_pct: number; deploy_pct: number }): Observable<any> {
    return this.http.post<any>(`${BASE}/cc-simulator`, body);
  }
  ccSimDelete(id: string): Observable<any> {
    return this.http.delete<any>(`${BASE}/cc-simulator/${id}`);
  }
  ccSimUpdateSlab(id: string, slabRatePct: number): Observable<any> {
    return this.http.post<any>(`${BASE}/cc-simulator/${id}/slab`, { slab_rate_pct: slabRatePct });
  }
  ccSimSellCall(id: string, body: { strike: number; expiry: string; lots: number; lot_size: number; premium: number }): Observable<any> {
    return this.http.post<any>(`${BASE}/cc-simulator/${id}/sell-call`, body);
  }
  ccSimCloseCall(id: string, closePrice: number, kind = 'closed'): Observable<any> {
    return this.http.post<any>(`${BASE}/cc-simulator/${id}/close-call`, { close_price: closePrice, kind });
  }
  ccSimSellShares(id: string, qty: number, price?: number): Observable<any> {
    return this.http.post<any>(`${BASE}/cc-simulator/${id}/sell-shares`, { qty, price });
  }
  ccSimBuyShares(id: string, qty: number, price?: number): Observable<any> {
    return this.http.post<any>(`${BASE}/cc-simulator/${id}/buy-shares`, { qty, price });
  }

  // ── CC Math (strategy expected-return analyzer) ────────────────────────────
  ccMathListStrategies(): Observable<any> {
    return this.http.get<any>(`${BASE}/cc-math/strategies`);
  }
  ccMathAnalyze(body: any): Observable<any> {
    return this.http.post<any>(`${BASE}/cc-math/analyze`, body);
  }
  ccMathCompare(body: any): Observable<any> {
    return this.http.post<any>(`${BASE}/cc-math/compare`, body);
  }
  ccMathSensitivity(body: any): Observable<any> {
    return this.http.post<any>(`${BASE}/cc-math/sensitivity`, body);
  }

  // ── Hub summary: categorized action queue + lifetime stats + charts ────────
  getHubSummary(): Observable<any> {
    return this.http.get<any>(`${BASE}/covered-call/hub-summary`);
  }

  // ── Portfolio analyzer (excel upload → live prices + insights + charts) ────
  previewPortfolio(file: File): Observable<any> {
    const fd = new FormData();
    fd.append('file', file);
    return this.http.post<any>(`${BASE}/portfolio/preview`, fd);
  }
  analyzePortfolio(file: File, opts: { skipRows?: number; dropFirstCols?: number; dropLastCols?: number } = {}): Observable<any> {
    const fd = new FormData();
    fd.append('file', file);
    fd.append('skip_rows',       String(opts.skipRows       ?? 0));
    fd.append('drop_first_cols', String(opts.dropFirstCols  ?? 0));
    fd.append('drop_last_cols',  String(opts.dropLastCols   ?? 0));
    return this.http.post<any>(`${BASE}/portfolio/analyze`, fd);
  }
  portfolioHistory(holdings: any[], days: number): Observable<any> {
    return this.http.post<any>(`${BASE}/portfolio/history`, { holdings, days });
  }
  portfolioSignals(holdings: any[], totalValue: number): Observable<any> {
    return this.http.post<any>(`${BASE}/portfolio/signals`, { holdings, total_value: totalValue });
  }

  /** Cross-device saved portfolio — fetch the latest persisted snapshot. */
  fetchSavedPortfolio(): Observable<any> {
    return this.http.get<any>(`${BASE}/portfolio/saved`);
  }
  /** Save the analyzed result server-side so other devices can pick it up. */
  saveSavedPortfolio(result: any, fileName: string = ''): Observable<any> {
    return this.http.post<any>(`${BASE}/portfolio/saved`,
      { result, file_name: fileName });
  }
  clearSavedPortfolio(): Observable<any> {
    return this.http.delete<any>(`${BASE}/portfolio/saved`);
  }

  // ── Fundamentals / Snowflake ───────────────────────────────────────────────
  snowflakeList(opts: { bucket?: string; cap?: string; limit?: number } = {}): Observable<any> {
    const params: any = {};
    if (opts.bucket) params.bucket = opts.bucket;
    if (opts.cap)    params.cap    = opts.cap;
    if (opts.limit)  params.limit  = String(opts.limit);
    return this.http.get<any>(`${BASE}/fundamentals/snowflake`, { params });
  }
  snowflakeByTickers(tickers: string[]): Observable<any> {
    // POST keeps the URL out of the access log even for 1500-stock requests.
    return this.http.post<any>(`${BASE}/fundamentals/snowflake/by-tickers`,
      { identifiers: tickers });
  }
  snowflakeOne(ticker: string): Observable<any> {
    return this.http.get<any>(`${BASE}/fundamentals/snowflake/${encodeURIComponent(ticker)}`);
  }
  snowflakeLiveQuotes(tickers: string[]): Observable<any> {
    return this.http.post<any>(`${BASE}/fundamentals/snowflake/quotes`,
      { identifiers: tickers });
  }
  /** Bulk 1Y price sparklines (Yahoo Finance .NS). Returns weekly close
   *  points + change_pct per identifier. Server caches 6h. */
  sparklines(tickers: string[]): Observable<any> {
    return this.http.post<any>(`${BASE}/fundamentals/sparklines`,
      { identifiers: tickers });
  }

  // ── Smart-money tracker (bulk / block / SAST disclosures) ─────────────────
  /** Parse the file without saving; returns column mapping + sample + warnings.
   *  Call this first, show the user what we'd save, then call upload to commit.
   *  `mappingOverride` lets the user manually pick which file column maps to
   *  each canonical field (e.g. {"stock": "Symbol", "value": "Total"}).        */
  smartMoneyPreview(file: File, mappingOverride: Record<string, string> = {}): Observable<any> {
    const fd = new FormData();
    fd.append('file', file);
    fd.append('mapping_override', JSON.stringify(mappingOverride));
    return this.http.post<any>(`${BASE}/smart-money/preview`, fd);
  }
  smartMoneyUpload(file: File, mappingOverride: Record<string, string> = {}): Observable<any> {
    const fd = new FormData();
    fd.append('file', file);
    fd.append('mapping_override', JSON.stringify(mappingOverride));
    return this.http.post<any>(`${BASE}/smart-money/upload`, fd);
  }
  smartMoneyRebuild(): Observable<any> {
    return this.http.post<any>(`${BASE}/smart-money/rebuild`, {});
  }
  smartMoneySummary(): Observable<any> {
    return this.http.get<any>(`${BASE}/smart-money/summary`);
  }
  smartMoneyHottest(opts: { days?: number; limit?: number; sortBy?: string } = {}): Observable<any> {
    const params: any = {};
    if (opts.days   != null) params.days    = String(opts.days);
    if (opts.limit  != null) params.limit   = String(opts.limit);
    if (opts.sortBy)         params.sort_by = opts.sortBy;
    return this.http.get<any>(`${BASE}/smart-money/hottest`, { params });
  }
  smartMoneyCategories(days?: number): Observable<any> {
    const params: any = {};
    if (days != null) params.days = String(days);
    return this.http.get<any>(`${BASE}/smart-money/categories`, { params });
  }
  smartMoneyParties(opts: { days?: number; limit?: number } = {}): Observable<any> {
    const params: any = {};
    if (opts.days  != null) params.days  = String(opts.days);
    if (opts.limit != null) params.limit = String(opts.limit);
    return this.http.get<any>(`${BASE}/smart-money/parties`, { params });
  }
  smartMoneyStock(ticker: string, days?: number): Observable<any> {
    const params: any = {};
    if (days != null) params.days = String(days);
    return this.http.get<any>(`${BASE}/smart-money/stock/${encodeURIComponent(ticker)}`, { params });
  }

  // ── Scorecard (6-category peer-ranked leaderboard from Tickertape CSV) ─────
  scorecardPreview(file: File): Observable<any> {
    const fd = new FormData();
    fd.append('file', file);
    return this.http.post<any>(`${BASE}/scorecard/preview`, fd);
  }
  scorecardUpload(file: File): Observable<any> {
    const fd = new FormData();
    fd.append('file', file);
    return this.http.post<any>(`${BASE}/scorecard/upload`, fd);
  }
  scorecardRebuild(): Observable<any> {
    return this.http.post<any>(`${BASE}/scorecard/rebuild`, {});
  }
  scorecardClear(): Observable<any> {
    return this.http.delete<any>(`${BASE}/scorecard/clear`);
  }
  scorecardSummary(): Observable<any> {
    return this.http.get<any>(`${BASE}/scorecard/summary`);
  }
  scorecardPresets(): Observable<any> {
    return this.http.get<any>(`${BASE}/scorecard/presets`);
  }
  scorecardLeaderboard(opts: {
    weights?:   Record<string, number>;
    subSector?: string;
    capTier?:   string;
    search?:    string;
    limit?:     number;
  } = {}): Observable<any> {
    const params: any = {};
    if (opts.weights)    params.weights    = JSON.stringify(opts.weights);
    if (opts.subSector)  params.sub_sector = opts.subSector;
    if (opts.capTier)    params.cap_tier   = opts.capTier;
    if (opts.search)     params.search     = opts.search;
    if (opts.limit)      params.limit      = String(opts.limit);
    return this.http.get<any>(`${BASE}/scorecard/leaderboard`, { params });
  }
  scorecardStock(ticker: string, weights?: Record<string, number>): Observable<any> {
    const params: any = {};
    if (weights) params.weights = JSON.stringify(weights);
    return this.http.get<any>(`${BASE}/scorecard/stock/${encodeURIComponent(ticker)}`, { params });
  }
  scorecardFiles(): Observable<any> {
    return this.http.get<any>(`${BASE}/scorecard/files`);
  }
  scorecardDeleteFile(name: string): Observable<any> {
    return this.http.delete<any>(`${BASE}/scorecard/files/${encodeURIComponent(name)}`);
  }

  // ── Exit Strategy ──────────────────────────────────────────────────────
  /** Upload a broker portfolio export (xlsx/xls/csv) and get back ranked
   *  exit candidates with per-holding conviction + specific reasons. */
  exitStrategyUpload(file: File): Observable<any> {
    const fd = new FormData();
    fd.append('file', file);
    return this.http.post<any>(`${BASE}/exit-strategy/upload`, fd);
  }
  /** Already-parsed holdings array → conviction analysis. */
  exitStrategyAnalyze(holdings: any[]): Observable<any> {
    return this.http.post<any>(`${BASE}/exit-strategy/analyze`, { holdings });
  }

  // ── Exit-strategy engine (roll-up-on-momentum) ─────────────────────────────
  getExitStatus(pid: string): Observable<any> {
    return this.http.get<any>(`${BASE}/covered-call/positions/${pid}/exit-status`);
  }
  rollUpCoveredCall(pid: string, body: {
    close_price:  number;
    new_strike:   number;
    new_expiry:   string;
    new_premium:  number;
    new_lots?:    number;
    new_lot_size?: number;
  }): Observable<any> {
    return this.http.post<any>(`${BASE}/covered-call/positions/${pid}/roll-up`, body);
  }
}
