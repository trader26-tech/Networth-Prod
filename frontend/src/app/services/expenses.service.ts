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

export type Frequency = 'weekly' | 'monthly' | 'quarterly' | 'half_yearly' | 'yearly' | 'one_time';
export type Region = 'india' | 'kuwait';

/** One expense as it applies to a viewed month (recurring carried-forward, or a
 *  one-off dated in it). `month_inr` is what it contributes THIS month. */
export interface MonthEntry {
  id: string;
  name: string;
  category: string | null;
  owner: string | null;
  currency: string;
  region: Region;
  region_label: string;
  recurring: boolean;
  frequency: Frequency;
  amount: number | null;          // native amount
  month_native: number;           // native contribution this month
  month_inr: number;              // ₹ contribution this month
  amount_inr: number;
  on_date: string | null;         // one-off: date · recurring: start month / day it recurs
  end_date?: string | null;       // recurring: stopped-from month
  created_at?: string | null;     // when this expense was logged
  note: string | null;
  essential: boolean;
  is_subscription: boolean;
  payment_method: string | null;
  is_foreign: boolean;
}

export interface CatRow { category: string; monthly_inr: number; pct: number; }
export interface RegionCat { category: string; amount_inr: number; pct: number; }
export interface RegionBlock {
  region: Region; label: string; currency: string;
  total_inr: number; pct: number; categories: RegionCat[];
}

export interface PersonCard {
  person: string; spent_inr: number; recurring_inr: number; onetime_inr: number;
  committed_inr: number; total_inr: number; count: number;
}
export interface CommitmentItem {
  id: string; label: string; owner: string; monthly_inr: number; until: string | null;
  sub: string; remaining?: number | null;
}
export interface Commitments {
  total_monthly_inr: number; by_person: Record<string, number>;
  loans: CommitmentItem[]; sips: CommitmentItem[]; count: number;
}

export interface MonthSummary {
  period: string;
  total_inr: number;
  recurring_inr: number;
  onetime_inr: number;
  india_inr: number;
  kuwait_inr: number;
  count: number;
  recurring_count: number;
  regions: RegionBlock[];
  by_category: CatRow[];
  person_cards: PersonCard[];
  commitments: Commitments;
  entries: MonthEntry[];
  end_date_ready?: boolean;
  fx: { ok: boolean; stale?: boolean; source?: string; updated_at?: string; inr_per: Record<string, number> };
}

export interface EntryInput {
  region: Region;
  name: string;
  category?: string | null;
  amount?: number | null;
  recurring: boolean;
  frequency?: Frequency;
  owner?: string | null;
  essential?: boolean;
  is_subscription?: boolean;
  payment_method?: string | null;
  on_date?: string | null;
  end_date?: string | null;
  note?: string | null;
}

export interface RegionMeta { region: Region; label: string; currency: string; }
export interface ExpenseMeta {
  frequencies: string[]; categories: string[]; income_categories: string[];
  owners: string[]; currencies: string[];
  regions: RegionMeta[]; end_date_ready: boolean; end_date_migration: string;
}

// ── Inbox: transactions Claude pushed in over MCP, waiting to be verified ─────
export type InboxDirection = 'debit' | 'credit';
export type InboxRowStatus = 'pending' | 'approved' | 'rejected';
/** how a row's owner was decided — 'default'/'none' mean it's only a guess */
export type OwnerSource = 'claude' | 'user' | 'narration' | 'history' | 'account' | 'category' | 'default' | 'none';

export interface InboxRow {
  id: string;
  date: string | null;
  amount: number;
  direction: InboxDirection;
  name: string;
  narration: string;
  category: string;
  category_known: boolean;        // false → Claude suggested a category you don't have yet
  category_source: 'rule' | 'memory' | 'claude' | 'user' | 'none';
  category_suggested: string;     // what Claude said, when your memory overrode it
  owner: string;
  owner_source: OwnerSource;      // how the owner was decided
  owner_guess: boolean;           // true → nothing solid matched; confirm it
  confidence: number | null;      // 0–1, how sure Claude was
  note: string;
  balance: number | null;
  ref: string;
  status: InboxRowStatus;
  duplicate: boolean;
  duplicate_of: string | null;
  issues: string[];               // 'date' | 'amount' | 'category' | 'name' | 'direction'
  linked_id: string | null;
  edited: boolean;
  approved_at?: string;
}

export interface InboxBatchSummary {
  id: string; source: string; account: string; owner: string; note: string;
  created_at: string; updated_at: string;
  count: number; pending: number; approved: number; rejected: number;
  needs_review: number; duplicates: number; owner_guesses: number;
  spend_inr: number; income_inr: number; net_inr: number;   // pending rows only
  statement_spend_inr?: number; statement_income_inr?: number;  // the whole statement
  date_from: string | null; date_to: string | null;
  status: 'pending' | 'done';
}

export interface InboxBatch extends InboxBatchSummary { currency: string; rows: InboxRow[]; }

export interface InboxList {
  batches: InboxBatchSummary[]; batch_count: number;
  pending_rows: number; needs_review: number; durable: boolean;
}

export interface InboxDetail {
  batch: InboxBatch; categories: string[]; income_categories: string[]; owners: string[];
}

export interface InboxApproveResult extends InboxBatchSummary {
  expenses_created: number; income_created: number;
  expenses_total_inr: number; income_total_inr: number;
  skipped: { id: string; name: string; why: string }[];
}

export interface RowPatch {
  id: string;
  date?: string; amount?: number | string; direction?: InboxDirection;
  name?: string; category?: string; owner?: string; note?: string;
  status?: 'pending' | 'rejected'; duplicate?: boolean;
}

// ── Money view: income logged this month + what's actually in hand ────────────
export interface IncomeEntry {
  id: string; source: string; category: string | null; owner: string | null;
  amount: number | null; amount_inr: number; currency: string;
  on_date: string | null; account: string | null; note: string | null;
  created_at?: string | null;
}
export interface CashAccount {
  owner: string; where: string | null; type: string; label: string | null;
  balance_inr: number; currency: string; balance: number | null; as_of_date: string | null;
}
export interface Overview {
  period: string;
  income: { total_inr: number; count: number; by_category: CatRow[];
            by_person: { person: string; monthly_inr: number }[]; entries: IncomeEntry[] };
  in_hand: { total_inr: number; cash_inr: number; bank_inr: number; accounts: number;
             by_person: { person: string; balance_inr: number }[]; entries: CashAccount[] };
  spend_inr: number; net_inr: number;
  inbox: { batches: number; pending_rows: number; needs_review: number; durable: boolean };
}

export interface StatementTxn {
  date: string; amount: number; direction: 'debit' | 'credit'; narration: string;
  name: string; category: string; ref: string; owner: string | null; already: boolean;
}
export interface StatementPreview {
  transactions: StatementTxn[];
  total_spent: number; total_earned: number;
  date_from: string | null; date_to: string | null;
  count: number; debit_count: number; credit_count: number;
  categories: string[]; income_categories: string[]; owner: string | null;
}
export interface StatementImportItem {
  date: string; amount: number; name: string; category?: string; owner?: string | null; note?: string; ref?: string; direction?: 'debit' | 'credit';
}

@Injectable({ providedIn: 'root' })
export class ExpensesService {
  private readonly base = _apiBase() + '/expenses';
  private http = inject(HttpClient);

  meta(): Observable<ExpenseMeta> { return this.http.get<ExpenseMeta>(`${this.base}/meta`); }
  methods(): Observable<{ methods: string[] }> { return this.http.get<{ methods: string[] }>(`${this.base}/methods`); }

  month(period: string): Observable<MonthSummary> {
    return this.http.get<MonthSummary>(`${this.base}/month?period=${period}`);
  }
  addCategory(name: string): Observable<{ categories: string[] }> { return this.http.post<{ categories: string[] }>(`${this.base}/categories`, { name }); }
  deleteCategory(name: string): Observable<{ categories: string[] }> { return this.http.delete<{ categories: string[] }>(`${this.base}/categories/${encodeURIComponent(name)}`); }

  addEntry(e: EntryInput): Observable<MonthEntry> { return this.http.post<MonthEntry>(`${this.base}/entry`, e); }
  updateEntry(id: string, e: EntryInput): Observable<MonthEntry> { return this.http.put<MonthEntry>(`${this.base}/entry/${id}`, e); }
  stopEntry(id: string, period: string): Observable<any> { return this.http.post<any>(`${this.base}/entry/${id}/stop`, { period }); }
  deleteEntry(id: string): Observable<any> { return this.http.delete(`${this.base}/entry/${id}`); }

  // ── Import from a bank statement ────────────────────────────────────────────
  statementPreview(file: File, owner = ''): Observable<StatementPreview> {
    const fd = new FormData(); fd.append('file', file, file.name);
    const q = owner ? `?owner=${encodeURIComponent(owner)}` : '';
    return this.http.post<StatementPreview>(`${this.base}/statement/preview${q}`, fd);
  }
  statementImport(items: StatementImportItem[]): Observable<{ ok: boolean; imported: number }> {
    return this.http.post<{ ok: boolean; imported: number }>(`${this.base}/statement/import`, { items });
  }

  // ── Inbox (from Claude, over MCP) ───────────────────────────────────────────
  inbox(): Observable<InboxList> { return this.http.get<InboxList>(`${this.base}/inbox`); }
  inboxBatch(id: string): Observable<InboxDetail> { return this.http.get<InboxDetail>(`${this.base}/inbox/${id}`); }
  inboxPatch(id: string, patches: RowPatch[]): Observable<{ batch: InboxBatch; also_updated?: number }> {
    return this.http.patch<{ batch: InboxBatch; also_updated?: number }>(`${this.base}/inbox/${id}/rows`, { patches });
  }
  inboxApprove(id: string, rowIds: string[] | null): Observable<InboxApproveResult> {
    return this.http.post<InboxApproveResult>(`${this.base}/inbox/${id}/approve`, { row_ids: rowIds });
  }
  inboxReject(id: string, rowIds: string[]): Observable<InboxBatch> {
    return this.http.post<InboxBatch>(`${this.base}/inbox/${id}/reject`, { row_ids: rowIds });
  }
  inboxRestore(id: string, rowIds: string[]): Observable<InboxBatch> {
    return this.http.post<InboxBatch>(`${this.base}/inbox/${id}/restore`, { row_ids: rowIds });
  }
  inboxDelete(id: string): Observable<any> { return this.http.delete(`${this.base}/inbox/${id}`); }
  /** remove several pushes, or every one still awaiting review */
  inboxDiscard(opts: { ids?: string[]; all_pending?: boolean }): Observable<{ count: number; remaining: InboxBatchSummary[] }> {
    return this.http.post<{ count: number; remaining: InboxBatchSummary[] }>(`${this.base}/inbox/discard`, opts);
  }

  // ── Money view (income + cash in hand) ──────────────────────────────────────
  overview(period: string): Observable<Overview> {
    return this.http.get<Overview>(`${this.base}/overview?period=${period}`);
  }
  addIncome(row: Partial<IncomeEntry>): Observable<IncomeEntry> {
    return this.http.post<IncomeEntry>(`${this.base}/income`, row);
  }
  updateIncome(id: string, patch: Partial<IncomeEntry>): Observable<IncomeEntry> {
    return this.http.put<IncomeEntry>(`${this.base}/income/${id}`, patch);
  }
  deleteIncome(id: string): Observable<any> { return this.http.delete(`${this.base}/income/${id}`); }

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
  static inrFull(v: number | null | undefined): string { return ExpensesService.inr(v, false); }
  static money(v: number | null | undefined, currency: string): string {
    if (v === null || v === undefined || isNaN(v as number)) return '—';
    if ((currency || 'INR').toUpperCase() === 'INR') return ExpensesService.inrFull(v);
    return `${currency} ${Math.round(v).toLocaleString('en-IN')}`;
  }
}
