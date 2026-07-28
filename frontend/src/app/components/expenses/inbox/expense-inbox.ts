import { Component, EventEmitter, OnInit, Output, computed, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpErrorResponse } from '@angular/common/http';
import {
  ExpensesService, InboxBatch, InboxBatchSummary, InboxRow, RowPatch, InboxApproveResult,
} from '../../../services/expenses.service';
import {
  BarItem, SankeyNode, SankeyLink,
  personColor, categoryColor, OUT_COLOR, IN_COLOR,
} from '../charts/expense-charts';

type Flow = 'all' | 'debit' | 'credit';
type SortCol = 'date' | 'name' | 'category' | 'owner' | 'amount' | 'confidence';
type Flag = 'review' | 'duplicate' | 'uncategorised' | 'lowconf' | 'edited' | 'whose';

const LOW_CONFIDENCE = 0.7;

/**
 * The review desk for transactions Claude pushed in over MCP.
 *
 * Nothing here is in the ledger yet — every row is staged. The job of this screen
 * is to make verifying a few hundred rows fast: filter down to what's suspect,
 * fix it inline, tick what's right, approve. Edits are saved to the staging area
 * as you go (debounced), and always flushed before an approve, so what you see is
 * exactly what gets written.
 */
@Component({
  selector: 'app-expense-inbox',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './expense-inbox.html',
  styleUrl: './expense-inbox.scss',
})
export class ExpenseInbox implements OnInit {
  private api = inject(ExpensesService);

  @Output() closed = new EventEmitter<void>();
  /** fired after rows land in the ledger, so the tab reloads its month */
  @Output() imported = new EventEmitter<InboxApproveResult>();

  batches = signal<InboxBatchSummary[]>([]);
  batch = signal<InboxBatch | null>(null);
  categories = signal<string[]>([]);
  incomeCategories = signal<string[]>([]);
  owners = signal<string[]>(['Ranjeev', 'Sanjeev', 'Ramprasad', 'Maha']);

  loading = signal(true);
  busy = signal(false);
  error = signal<string | null>(null);
  result = signal<InboxApproveResult | null>(null);
  durable = signal(true);

  // ── filters ────────────────────────────────────────────────────────────────
  flow = signal<Flow>('all');
  flags = signal<Set<Flag>>(new Set<Flag>());
  cat = signal<string>('');            // '' = every category
  owner = signal<string>('');
  search = signal('');
  from = signal('');
  to = signal('');
  minAmt = signal<number | null>(null);
  showDone = signal(false);            // include approved/rejected rows
  group = signal(false);               // group the table by category
  sort = signal<{ col: SortCol; dir: 1 | -1 }>({ col: 'date', dir: -1 });

  selected = signal<Set<string>>(new Set<string>());
  openGroups = signal<Set<string>>(new Set<string>());

  /** the manage-pushes rail — open by itself when there is more than one push,
   *  because that is exactly when you need to see them as separate things */
  railOpen = signal(false);
  private railAuto = true;           // stop overriding it once you've touched it

  /** which side of the ledger the category donut is showing */
  donutDir = signal<'debit' | 'credit'>('debit');

  inr = ExpensesService.inr;
  inrFull = ExpensesService.inrFull;

  ngOnInit() { this.load(); }

  load(keepId?: string) {
    this.loading.set(true); this.error.set(null);
    this.api.inbox().subscribe({
      next: list => {
        this.batches.set(list.batches);
        this.durable.set(list.durable);
        // pushes stay tucked behind the "Pushes" button by default — you open it
        // when you want to pick which statement to work through
        if (this.railAuto) this.railOpen.set(false);
        const pick = keepId || list.batches.find(b => b.status === 'pending')?.id || list.batches[0]?.id;
        if (pick) this.openBatch(pick);
        else { this.batch.set(null); this.loading.set(false); }
      },
      error: () => { this.loading.set(false); this.error.set('Could not reach the API.'); },
    });
  }

  /** `after` = refreshing straight after an approve: keep the success message and
   *  DON'T re-tick anything (re-seeding the selection after an approve would arm
   *  the button with rows the user never chose). */
  openBatch(id: string, after = false) {
    this.loading.set(true);
    if (!after) this.result.set(null);
    this.api.inboxBatch(id).subscribe({
      next: d => {
        this.batch.set(d.batch);
        this.categories.set(d.categories);
        this.incomeCategories.set(d.income_categories);
        if (d.owners?.length) this.owners.set(d.owners);
        // first open: pre-tick everything clean — the fast path is "scan, then approve"
        this.selected.set(after ? new Set<string>()
          : new Set(d.batch.rows.filter(r => r.status === 'pending' && this.isClean(r)).map(r => r.id)));
        this.loading.set(false);
      },
      error: (e: HttpErrorResponse) => {
        this.loading.set(false);
        this.error.set(e.status === 404 ? 'That batch is gone — ask Claude to send it again.' : 'Could not load the batch.');
      },
    });
  }

  isClean(r: InboxRow): boolean { return !r.issues.length && !r.duplicate && !!r.category; }

  // ── presentation helpers ───────────────────────────────────────────────────
  /** Split "HDFC Bank Savings XXXX2285" into a bank name and the last digits, so
   *  the KPI bar can say which account this statement is, not just its raw label. */
  private acct = computed(() => {
    const raw = (this.batch()?.account || '').trim();
    if (!raw) return { bank: 'Account statement', tail: '' };
    const m = raw.match(/([0-9]{3,})\s*$/);                    // trailing digits
    const tail = m ? m[1].slice(-4) : '';
    let bank = (m ? raw.slice(0, m.index) : raw)
      .replace(/[X•*\u2022\-_,\s]+$/i, '')                    // strip the masking run
      .replace(/\b(a\/c|acct|account|no\.?)\b/gi, '')
      .trim();
    if (!bank) bank = 'Account';
    return { bank, tail };
  });
  bankName = computed(() => this.acct().bank);
  /** "···· 2285" — never the full number */
  acctMask = computed(() => this.acct().tail ? '···· ' + this.acct().tail : '');
  acctOwner = computed(() => (this.batch()?.owner || '').trim());

  /** the same split, for a push card in the rail */
  private splitAccount(raw: string): { bank: string; tail: string } {
    const s = (raw || '').trim();
    if (!s) return { bank: 'Statement', tail: '' };
    const m = s.match(/([0-9]{3,})\s*$/);
    const tail = m ? m[1].slice(-4) : '';
    const bank = (m ? s.slice(0, m.index) : s)
      .replace(/[X•*•\-_,\s]+$/i, '')
      .replace(/\b(a\/c|acct|account|no\.?)\b/gi, '').trim();
    return { bank: bank || 'Statement', tail };
  }
  pushBank(b: InboxBatchSummary): string { return this.splitAccount(b.account || b.source).bank; }
  pushMask(b: InboxBatchSummary): string {
    const t = this.splitAccount(b.account || b.source).tail;
    return t ? '···· ' + t : '';
  }
  /** share of a push that has been dealt with, for its little progress bar */
  pushDone(b: InboxBatchSummary): number {
    return b.count ? Math.round(((b.count - b.pending) / b.count) * 100) : 100;
  }

  /** the two halves of "1 / 127" plus the percentage that goes under it */
  readyCount = computed(() => this.counts().all - this.counts().review);
  readyLabel = computed(() => {
    const pct = Math.round(this.readyPct() * 100);
    const c = this.counts();
    return pct === 0 && this.readyCount() > 0 ? '<1% ready' : `${pct}% ready`;
  });

  /**
   * The one line under a transaction's name. Exactly one thing at a time, in order
   * of what you need to act on: what's missing → what it is in the statement.
   * (No badges for how the category or owner was worked out — the system recomputes
   * that itself and it isn't a decision you have to make.)
   */
  rowNote(r: InboxRow): string {
    if (r.issues.length) return this.issueText(r);
    const n = (r.narration || '').trim();
    if (!n) return '';
    const name = (r.name || '').trim();
    const echo = n === name
      || (n.toLowerCase().startsWith(name.toLowerCase()) && n.length - name.length < 8);
    return echo ? '' : n;
  }

  /** Rows that can't be approved until something is filled in — as opposed to
   *  rows that merely need a yes/no on a suspected duplicate. */
  blockers = computed(() => this.pendingRows().filter(r => r.issues.length || !r.category).length);
  needsReview(r: InboxRow): boolean { return !this.isClean(r); }

  // ── whose is it? ───────────────────────────────────────────────────────────
  /** Nobody set — the one owner case that is still your decision. */
  ownerUnsure(r: InboxRow): boolean { return !r.owner; }

  /** how many other rows an edit you just made also fixed (kept for the API contract) */
  applied = signal<number>(0);

  // ── derived ────────────────────────────────────────────────────────────────
  rows = computed(() => this.batch()?.rows || []);
  pendingRows = computed(() => this.rows().filter(r => r.status === 'pending'));

  counts = computed(() => {
    const p = this.pendingRows();
    return {
      all: p.length,
      debit: p.filter(r => r.direction === 'debit').length,
      credit: p.filter(r => r.direction === 'credit').length,
      review: p.filter(r => this.needsReview(r)).length,
      duplicate: p.filter(r => r.duplicate).length,
      uncategorised: p.filter(r => !r.category).length,
      lowconf: p.filter(r => r.confidence != null && r.confidence < LOW_CONFIDENCE).length,
      edited: p.filter(r => r.edited).length,
      whose: p.filter(r => this.ownerUnsure(r)).length,
      done: this.rows().length - p.length,
    };
  });

  catOptions = computed(() => {
    const seen = new Map<string, number>();
    for (const r of this.rows()) {
      const k = r.category || 'Uncategorised';
      seen.set(k, (seen.get(k) || 0) + 1);
    }
    return [...seen.entries()].sort((a, b) => b[1] - a[1]).map(([category, count]) => ({ category, count }));
  });

  catsFor(dir: 'debit' | 'credit'): string[] {
    return dir === 'credit' ? this.incomeCategories() : this.categories();
  }

  /** One filter predicate for the table AND the charts. `skip` lets a chart
   *  ignore the dimension it controls — otherwise clicking a category bar would
   *  collapse the category chart to that single bar. */
  private passes(r: InboxRow, skip?: 'cat' | 'owner' | 'date'): boolean {
    const fl = this.flags();
    if (!this.showDone() && r.status !== 'pending') return false;
    if (this.flow() !== 'all' && r.direction !== this.flow()) return false;
    if (fl.has('review') && !this.needsReview(r)) return false;
    if (fl.has('duplicate') && !r.duplicate) return false;
    if (fl.has('uncategorised') && r.category) return false;
    if (fl.has('lowconf') && !(r.confidence != null && r.confidence < LOW_CONFIDENCE)) return false;
    if (fl.has('edited') && !r.edited) return false;
    if (fl.has('whose') && !this.ownerUnsure(r)) return false;
    if (skip !== 'cat' && this.cat() && (r.category || 'Uncategorised') !== this.cat()) return false;
    if (skip !== 'owner' && this.owner() && r.owner !== this.owner()) return false;
    if (skip !== 'date') {
      if (this.from() && (r.date || '') < this.from()) return false;
      if (this.to() && (r.date || '') > this.to()) return false;
    }
    if (this.minAmt() != null && r.amount < this.minAmt()!) return false;
    const q = this.search().trim().toLowerCase();
    if (q && !(r.name + ' ' + r.narration + ' ' + r.category + ' ' + r.owner).toLowerCase().includes(q)) return false;
    return true;
  }

  view = computed<InboxRow[]>(() => {
    const { col, dir } = this.sort();
    const rows = this.rows().filter(r => this.passes(r));
    return [...rows].sort((a, b) => {
      if (col === 'amount') return (a.amount - b.amount) * dir;
      if (col === 'confidence') return ((a.confidence ?? -1) - (b.confidence ?? -1)) * dir;
      return String(a[col] || '').localeCompare(String(b[col] || '')) * dir;
    });
  });

  /** the same rows, bucketed by category — the "see it by category" view */
  groups = computed(() => {
    const map = new Map<string, InboxRow[]>();
    for (const r of this.view()) {
      const k = r.category || 'Uncategorised';
      (map.get(k) || map.set(k, []).get(k)!).push(r);
    }
    return [...map.entries()]
      .map(([category, rows]) => ({
        category,
        rows,
        count: rows.length,
        spend: rows.filter(r => r.direction === 'debit').reduce((s, r) => s + r.amount, 0),
        income: rows.filter(r => r.direction === 'credit').reduce((s, r) => s + r.amount, 0),
        unknown: rows.some(r => !r.category_known && !!r.category),
      }))
      .sort((a, b) => (b.spend + b.income) - (a.spend + a.income));
  });

  // ── the chart row: money flow + the category donut ─────────────────────────
  showCharts = signal(true);         // the whole chart row
  moreOpen = signal(false);          // the extra-filters popover
  flowBig = signal(false);           // the full-size flow overlay
  /** viewBox units; `wide` sizes the frame so this is ~the rendered height too */
  readonly CHART_H = 234;
  private readonly HUB = '#387ed1';  // the pool — app accent; validated vs in/out

  /** Fold a category list to the biggest N, with the tail as one "Other". */
  private foldTop(items: BarItem[], n: number): BarItem[] {
    const s = [...items].filter(i => i.value > 0).sort((a, b) => b.value - a.value);
    if (s.length <= n) return s;
    const tail = s.slice(n - 1);
    return [...s.slice(0, n - 1), {
      key: '__other__', label: `Other (${tail.length})`,
      value: tail.reduce((t, x) => t + x.value, 0),
      count: tail.reduce((t, x) => t + (x.count || 0), 0),
    }];
  }

  private byCategory(dir: 'debit' | 'credit'): BarItem[] {
    const agg = new Map<string, BarItem>();
    for (const r of this.rows().filter(x => this.passes(x, 'cat'))) {
      if (r.direction !== dir) continue;
      const key = r.category || 'Uncategorised';
      const cur = agg.get(key) || { key, label: key, value: 0, count: 0 };
      cur.value += r.amount; cur.count = (cur.count || 0) + 1;
      agg.set(key, cur);
    }
    return [...agg.values()];
  }

  /** in-categories → the pool → out (→ out-categories) and whatever is left over.
   *  When more went out than came in, the difference came off the balance you
   *  already had — shown as its own source so the two sides actually balance. */
  flowGraph = computed<{ nodes: SankeyNode[]; links: SankeyLink[]; height: number }>(() => {
    // five a side, tail folded into "Other" — the donut beside it carries the long
    // list, so the flow stays readable instead of a stack of hairlines
    const ins = this.foldTop(this.byCategory('credit'), 5);
    const outs = this.foldTop(this.byCategory('debit'), 5);
    const inTotal = ins.reduce((t, x) => t + x.value, 0);
    const outTotal = outs.reduce((t, x) => t + x.value, 0);
    if (inTotal <= 0 && outTotal <= 0) return { nodes: [], links: [], height: 200 };

    const nodes: SankeyNode[] = [];
    const links: SankeyLink[] = [];
    const fromBalance = Math.max(0, outTotal - inTotal);
    const kept = Math.max(0, inTotal - outTotal);
    const pool = inTotal + fromBalance;

    for (const i of ins) {
      nodes.push({ id: 'in:' + i.key, label: i.label, value: i.value, col: 0, color: IN_COLOR, kind: 'in' });
      links.push({ from: 'in:' + i.key, to: 'pool', value: i.value });
    }
    if (fromBalance > 0) {
      nodes.push({ id: 'in:__balance__', label: 'From your balance', value: fromBalance, col: 0,
                   color: '#9aa0b5', kind: 'in' });
      links.push({ from: 'in:__balance__', to: 'pool', value: fromBalance });
    }
    nodes.push({ id: 'pool', label: 'Money in', value: pool, col: 1, color: this.HUB, kind: 'hub' });

    if (outTotal > 0) {
      nodes.push({ id: 'out', label: 'Money out', value: outTotal, col: 2, color: OUT_COLOR, kind: 'out' });
      links.push({ from: 'pool', to: 'out', value: outTotal });
    }
    if (kept > 0) {
      nodes.push({ id: 'kept', label: 'Left over', value: kept, col: 2, color: IN_COLOR, kind: 'kept' });
      links.push({ from: 'pool', to: 'kept', value: kept });
    }
    for (const o of outs) {
      nodes.push({ id: 'cat:' + o.key, label: o.label, value: o.value, col: 3, color: OUT_COLOR,
                   kind: 'out', clickable: o.key !== '__other__' });
      links.push({ from: 'out', to: 'cat:' + o.key, value: o.value });
    }
    const rowsPerSide = Math.max(ins.length + (fromBalance > 0 ? 1 : 0), outs.length, 2);
    return { nodes, links, height: Math.min(620, Math.max(300, 34 + rowsPerSide * 56)) };
  });
  hasFlow = computed(() => this.flowGraph().nodes.length > 0);

  /** clicking a spend node on the right filters the table to that category */
  pickFlowNode(id: string) {
    if (!id.startsWith('cat:')) return;
    const key = id.slice(4);
    this.cat.set(this.cat() === key ? '' : key);
  }

  pickCategory(key: string | null) { this.cat.set(key && key !== '__other__' ? key : ''); }
  /** the category donut, for whichever side of the ledger is showing.
   *  Colours come from the position in your whole category list, so filtering the
   *  table never repaints the slices that are still on screen. */
  donutItems = computed<BarItem[]>(() => {
    const dir = this.donutDir();
    const vocab = dir === 'credit' ? this.incomeCategories() : this.categories();
    return this.byCategory(dir).map(i => ({ ...i, color: categoryColor(i.label, vocab) }));
  });

  viewSpend = computed(() => this.view().filter(r => r.direction === 'debit').reduce((s, r) => s + r.amount, 0));
  viewIncome = computed(() => this.view().filter(r => r.direction === 'credit').reduce((s, r) => s + r.amount, 0));

  selectedRows = computed(() => this.rows().filter(r => this.selected().has(r.id) && r.status === 'pending'));
  selSpend = computed(() => this.selectedRows().filter(r => r.direction === 'debit').reduce((s, r) => s + r.amount, 0));
  selIncome = computed(() => this.selectedRows().filter(r => r.direction === 'credit').reduce((s, r) => s + r.amount, 0));
  selBlocked = computed(() => this.selectedRows().filter(r => r.issues.length || !r.category).length);
  canApprove = computed(() => this.selectedRows().length > 0 && !this.busy());

  // ── the live summary of what you've ticked ──────────────────────────────────
  /** Everything here reads off the rows as edited, so the numbers move with you
   *  and what you see is exactly what the approve writes. */
  selCounts = computed(() => {
    const s = this.selectedRows();
    return { debit: s.filter(r => r.direction === 'debit').length,
             credit: s.filter(r => r.direction === 'credit').length };
  });

  selTopCats = computed<BarItem[]>(() => {
    const vocab = this.categories();
    const agg = new Map<string, BarItem>();
    for (const r of this.selectedRows()) {
      if (!r.category) continue;
      const cur = agg.get(r.category)
        || { key: r.category, label: r.category, value: 0, count: 0, color: categoryColor(r.category, vocab) };
      cur.value += r.amount; cur.count = (cur.count || 0) + 1;
      agg.set(r.category, cur);
    }
    return [...agg.values()].sort((a, b) => b.value - a.value).slice(0, 4);
  });

  selTopOwners = computed<BarItem[]>(() => {
    const agg = new Map<string, BarItem>();
    for (const r of this.selectedRows()) {
      if (!r.owner) continue;
      const cur = agg.get(r.owner)
        || { key: r.owner, label: r.owner, value: 0, count: 0, color: personColor(r.owner) };
      cur.value += r.amount; cur.count = (cur.count || 0) + 1;
      agg.set(r.owner, cur);
    }
    return [...agg.values()].sort((a, b) => (b.count || 0) - (a.count || 0)).slice(0, 4);
  });

  // ── the WHOLE book Claude sent — the always-on summary above the search ──────
  bookSpend = computed(() => this.pendingRows().filter(r => r.direction === 'debit').reduce((s, r) => s + r.amount, 0));
  bookIncome = computed(() => this.pendingRows().filter(r => r.direction === 'credit').reduce((s, r) => s + r.amount, 0));
  bookTopCats = computed<BarItem[]>(() => {
    const vocab = this.categories();
    const agg = new Map<string, BarItem>();
    for (const r of this.pendingRows()) {
      if (r.direction !== 'debit' || !r.category) continue;
      const cur = agg.get(r.category)
        || { key: r.category, label: r.category, value: 0, count: 0, color: categoryColor(r.category, vocab) };
      cur.value += r.amount; cur.count = (cur.count || 0) + 1;
      agg.set(r.category, cur);
    }
    return [...agg.values()].sort((a, b) => b.value - a.value).slice(0, 4);
  });
  bookTopOwners = computed<BarItem[]>(() => {
    const agg = new Map<string, BarItem>();
    for (const r of this.pendingRows()) {
      const owner = r.owner || 'Unassigned';
      const cur = agg.get(owner) || { key: owner, label: owner, value: 0, count: 0, color: personColor(r.owner || '') };
      cur.value += r.amount; cur.count = (cur.count || 0) + 1;
      agg.set(owner, cur);
    }
    return [...agg.values()].sort((a, b) => (b.count || 0) - (a.count || 0)).slice(0, 4);
  });

  // ── the filtered subset (below the search) ──────────────────────────────────
  viewCatCount = computed(() => new Set(this.view().map(r => r.category || 'Uncategorised')).size);

  /** how many filter dimensions are on, for the badge on the Filters button */
  filterBadge = computed(() => [
    this.flow() !== 'all', !!this.cat(), !!this.owner(), !!this.from() || !!this.to(),
    this.minAmt() != null, this.flags().size > 0, this.group(), this.showDone(),
  ].filter(Boolean).length);

  allShownTicked = computed(() => {
    const v = this.view().filter(r => r.status === 'pending');
    return v.length > 0 && v.every(r => this.selected().has(r.id));
  });

  // ── selection ──────────────────────────────────────────────────────────────
  isSel(r: InboxRow) { return this.selected().has(r.id); }
  toggleSel(r: InboxRow) {
    this.selected.update(s => { const n = new Set(s); n.has(r.id) ? n.delete(r.id) : n.add(r.id); return n; });
  }
  tickShown(on: boolean) {
    const ids = this.view().filter(r => r.status === 'pending').map(r => r.id);
    this.selected.update(s => {
      const n = new Set(s);
      ids.forEach(id => on ? n.add(id) : n.delete(id));
      return n;
    });
  }
  tickGroup(rows: InboxRow[], on: boolean) {
    this.selected.update(s => {
      const n = new Set(s);
      rows.filter(r => r.status === 'pending').forEach(r => on ? n.add(r.id) : n.delete(r.id));
      return n;
    });
  }
  groupTicked(rows: InboxRow[]): boolean {
    const p = rows.filter(r => r.status === 'pending');
    return p.length > 0 && p.every(r => this.selected().has(r.id));
  }
  selectClean() {
    this.selected.set(new Set(this.pendingRows().filter(r => this.isClean(r)).map(r => r.id)));
  }

  // ── manage pushes ──────────────────────────────────────────────────────────
  toggleRail() { this.railAuto = false; this.railOpen.set(!this.railOpen()); }

  // ── filters ────────────────────────────────────────────────────────────────
  toggleFlag(f: Flag) {
    this.flags.update(s => { const n = new Set(s); n.has(f) ? n.delete(f) : n.add(f); return n; });
  }
  hasFlag(f: Flag) { return this.flags().has(f); }

  /** Spend / Income / All only — everything narrower than that lives behind
   *  "Filters", so the control row stays one line you can read at a glance. */
  setFlow(f: Flow) { this.flow.set(f); }

  /** the narrow filters, inside the popover; a 0-count one is noise so it's hidden */
  flagFilters = computed(() => {
    const c = this.counts();
    return ([
      { flag: 'review' as Flag, label: 'Needs review', count: c.review },
      { flag: 'uncategorised' as Flag, label: 'No category', count: c.uncategorised },
      { flag: 'whose' as Flag, label: 'Nobody set', count: c.whose },
      { flag: 'duplicate' as Flag, label: 'Possible duplicates', count: c.duplicate },
      { flag: 'edited' as Flag, label: 'Edited by me', count: c.edited },
      { flag: 'lowconf' as Flag, label: 'Claude was unsure', count: c.lowconf },
    ]).filter(x => x.count > 0 || this.hasFlag(x.flag));
  });

  /** the filters that live behind "Filters" — for the dot on the button */
  extraFilters = computed(() => !!this.cat() || !!this.owner() || !!this.from() || !!this.to()
    || this.minAmt() != null || this.group() || this.showDone() || this.flags().size > 0);

  /** how much of the batch is ready to go, for the progress meter */
  readyPct = computed(() => {
    const c = this.counts();
    return c.all ? (c.all - c.review) / c.all : 1;
  });

  /** jump straight to the rows that need attention */
  onlyReview() {
    this.flags.update(f => { const n = new Set(f); n.add('review'); return n; });
    this.flow.set('all');
  }
  clearFilters() {
    this.flow.set('all'); this.flags.set(new Set()); this.cat.set(''); this.owner.set('');
    this.search.set(''); this.from.set(''); this.to.set(''); this.minAmt.set(null);
    this.showDone.set(false);
  }
  filtersOn = computed(() =>
    this.flow() !== 'all' || this.flags().size > 0 || !!this.cat() || !!this.owner() ||
    !!this.search() || !!this.from() || !!this.to() || this.minAmt() != null || this.showDone());

  sortBy(col: SortCol) {
    this.sort.update(s => s.col === col ? { col, dir: (s.dir === 1 ? -1 : 1) as 1 | -1 } : { col, dir: col === 'date' ? -1 : 1 });
  }
  arrow(col: SortCol): string { const s = this.sort(); return s.col === col ? (s.dir === 1 ? '▲' : '▼') : ''; }

  toggleGroup(cat: string) {
    this.openGroups.update(s => { const n = new Set(s); n.has(cat) ? n.delete(cat) : n.add(cat); return n; });
  }
  isGroupOpen(cat: string) { return this.openGroups().has(cat); }

  /** Date + amount read as plain text until you click them — the table stays calm
   *  and still edits in place. */
  editCell = signal<{ id: string; col: 'date' | 'amount' } | null>(null);
  isEditing(r: InboxRow, col: 'date' | 'amount') {
    const e = this.editCell();
    return !!e && e.id === r.id && e.col === col;
  }
  editCellOpen(r: InboxRow, col: 'date' | 'amount') {
    if (r.status !== 'pending') return;
    this.editCell.set({ id: r.id, col });
  }
  editCellClose() { this.editCell.set(null); this.flush(); }

  fmtAmount(r: InboxRow): string {
    return (r.direction === 'credit' ? '+' : '−') + ExpensesService.inrFull(r.amount).replace('₹', '₹');
  }

  // ── inline edits (optimistic locally, debounced to the staging store) ───────
  private queue = new Map<string, RowPatch>();
  private timer: any = null;
  saving = signal(false);

  private applyLocal(id: string, patch: Partial<InboxRow>) {
    this.batch.update(b => b ? {
      ...b,
      rows: b.rows.map(r => r.id === id ? { ...r, ...patch, edited: true } as InboxRow : r),
    } : b);
  }

  private queuePatch(id: string, patch: Omit<RowPatch, 'id'>) {
    const cur = this.queue.get(id) || { id };
    this.queue.set(id, { ...cur, ...patch, id });
    clearTimeout(this.timer);
    this.timer = setTimeout(() => this.flush(), 600);
  }

  /** Push queued edits to the server. Returns a promise so approve can await it. */
  flush(): Promise<void> {
    clearTimeout(this.timer); this.timer = null;
    const b = this.batch();
    const patches = [...this.queue.values()];
    if (!b || !patches.length) return Promise.resolve();
    this.queue.clear();
    this.saving.set(true);
    return new Promise(resolve => {
      this.api.inboxPatch(b.id, patches).subscribe({
        next: res => {
          this.saving.set(false);
          // trust the server's re-validated rows (amounts/dates normalised there)
          this.batch.set(res.batch);
          if (res.also_updated) {
            this.applied.set(res.also_updated);
            setTimeout(() => this.applied.set(0), 6000);
          }
          resolve();
        },
        error: () => {
          this.saving.set(false);
          this.error.set('Could not save that edit — check your connection.');
          resolve();
        },
      });
    });
  }

  setCategory(r: InboxRow, v: string) {
    const known = this.catsFor(r.direction).some(c => c.toLowerCase() === v.toLowerCase());
    this.applyLocal(r.id, { category: v, category_known: known, issues: r.issues.filter(i => i !== 'category') });
    this.queuePatch(r.id, { category: v });
  }
  setOwner(r: InboxRow, v: string) {
    this.applyLocal(r.id, { owner: v, owner_source: 'user', owner_guess: false });
    this.queuePatch(r.id, { owner: v });
  }
  setName(r: InboxRow, v: string) { this.applyLocal(r.id, { name: v }); this.queuePatch(r.id, { name: v }); }
  setDate(r: InboxRow, v: string) {
    this.applyLocal(r.id, { date: v, issues: r.issues.filter(i => i !== 'date') });
    this.queuePatch(r.id, { date: v });
  }
  setAmount(r: InboxRow, v: string) {
    const n = parseFloat(String(v).replace(/,/g, ''));
    if (isNaN(n) || n <= 0) return;
    this.applyLocal(r.id, { amount: n, issues: r.issues.filter(i => i !== 'amount') });
    this.queuePatch(r.id, { amount: n });
  }
  flip(r: InboxRow) {
    const dir = r.direction === 'debit' ? 'credit' : 'debit';
    // the category vocabulary differs per direction — drop one that no longer applies
    const keep = this.catsFor(dir).some(c => c.toLowerCase() === (r.category || '').toLowerCase());
    this.applyLocal(r.id, { direction: dir, category: keep ? r.category : '', category_known: keep });
    this.queuePatch(r.id, { direction: dir, category: keep ? r.category : '' });
  }
  // ── add a category to the app's own list, from right here ──────────────────
  addingCat = signal<string | null>(null);
  newCatOpen = signal(false);
  newCatName = signal('');
  newCatApply = signal(true);
  newCatErr = signal<string | null>(null);

  openNewCat() {
    if (this.newCatOpen()) { this.newCatOpen.set(false); return; }
    // Pre-fill with a category Claude invented that your list doesn't have yet.
    // Spends only: this adds an *expense* category, and a credit row's category is
    // measured against the income list, so it can be "unknown" there and already
    // present here.
    const isNew = (r: InboxRow) => r.direction === 'debit' && !!r.category
      && !this.categories().some(c => c.toLowerCase() === r.category!.toLowerCase());
    const invented = this.selectedRows().find(isNew) || this.pendingRows().find(isNew);
    this.newCatName.set(invented?.category || '');
    this.newCatErr.set(null);
    this.newCatOpen.set(true);
  }

  /** Adds it app-wide (every screen sees it), and optionally files the ticked rows
   *  under it in the same click. */
  createCategory() {
    const name = this.newCatName().trim();
    if (!name || this.addingCat()) return;
    if (this.categories().some(c => c.toLowerCase() === name.toLowerCase())) {
      this.newCatErr.set('You already have that one.');
      if (this.newCatApply()) this.bulkCategory(this.matchCategory(name));
      return;
    }
    this.addingCat.set(name); this.newCatErr.set(null);
    this.api.addCategory(name).subscribe({
      next: res => {
        this.categories.set(res.categories);
        const canon = this.matchCategory(name);
        // rows already carrying this category (Claude's invention) are now known
        this.batch.update(b => b ? {
          ...b,
          rows: b.rows.map(x => (x.category || '').toLowerCase() === canon.toLowerCase()
            ? { ...x, category_known: true } : x),
        } : b);
        if (this.newCatApply() && this.selectedRows().length) this.bulkCategory(canon);
        this.addingCat.set(null);
        this.newCatOpen.set(false);
        this.newCatName.set('');
      },
      error: () => { this.addingCat.set(null); this.newCatErr.set('Could not add that category.'); },
    });
  }
  /** the app's own spelling of a category name */
  private matchCategory(name: string): string {
    return this.categories().find(c => c.toLowerCase() === name.toLowerCase()) || name;
  }

  // ── bulk actions ───────────────────────────────────────────────────────────
  bulkCategory(v: string) {
    if (!v) return;
    const rows = this.selectedRows();
    rows.forEach(r => this.setCategory(r, v));
  }
  bulkOwner(v: string) {
    if (!v) return;
    this.selectedRows().forEach(r => this.setOwner(r, v));
  }
  rejectSelected() {
    const b = this.batch(); const ids = this.selectedRows().map(r => r.id);
    if (!b || !ids.length) return;
    this.busy.set(true);
    this.flush().then(() => this.api.inboxReject(b.id, ids).subscribe({
      next: batch => { this.batch.set(batch as InboxBatch); this.selected.set(new Set()); this.busy.set(false); this.refreshIndex(); },
      error: () => { this.busy.set(false); this.error.set('Could not skip those rows.'); },
    }));
  }
  rejectRow(r: InboxRow) {
    const b = this.batch(); if (!b) return;
    this.flush().then(() => this.api.inboxReject(b.id, [r.id]).subscribe({
      next: batch => {
        this.batch.set(batch as InboxBatch);
        this.selected.update(s => { const n = new Set(s); n.delete(r.id); return n; });
        this.refreshIndex();
      },
      error: () => this.error.set('Could not skip that row.'),
    }));
  }
  restoreRow(r: InboxRow) {
    const b = this.batch(); if (!b) return;
    this.api.inboxRestore(b.id, [r.id]).subscribe({
      next: batch => this.batch.set(batch as InboxBatch),
      error: () => this.error.set('Could not restore that row.'),
    });
  }

  // ── approve ────────────────────────────────────────────────────────────────
  approve() {
    const b = this.batch(); const ids = this.selectedRows().map(r => r.id);
    if (!b || !ids.length) return;
    this.busy.set(true); this.error.set(null);
    this.flush().then(() => this.api.inboxApprove(b.id, ids).subscribe({
      next: res => {
        this.busy.set(false);
        this.result.set(res);
        this.selected.set(new Set());
        this.imported.emit(res);
        // show what just landed (green ✓ rows) instead of them vanishing from the view
        this.showDone.set(true);
        this.openBatch(b.id, true);
        this.refreshIndex();
      },
      error: (e: HttpErrorResponse) => {
        this.busy.set(false);
        this.error.set(e.error?.detail || 'Could not add those to your expenses.');
      },
    }));
  }

  discard() { const b = this.batch(); if (b) this.removePush(b); }

  /** Remove one push. Anything already approved stays in the ledger. */
  removePush(b: InboxBatchSummary, ev?: Event) {
    ev?.stopPropagation();
    const what = `${b.count} row${b.count === 1 ? '' : 's'} from ${b.account || b.source}`;
    const kept = b.approved
      ? `\n\nThe ${b.approved} row${b.approved === 1 ? '' : 's'} you already approved stay in your expenses.`
      : '';
    if (!confirm(`Remove this push (${what})?${kept}`)) return;
    this.busy.set(true);
    this.api.inboxDelete(b.id).subscribe({
      next: () => { this.busy.set(false); this.load(); },
      error: () => { this.busy.set(false); this.error.set('Could not remove that push.'); },
    });
  }

  /** Clear everything still awaiting review — for when Claude sent duplicates. */
  removeAllPending() {
    const pending = this.batches().filter(b => b.status === 'pending');
    if (!pending.length) return;
    const rows = pending.reduce((t, b) => t + b.pending, 0);
    if (!confirm(`Remove all ${pending.length} pushes still awaiting review (${rows} rows)?`
      + `\n\nNothing you have already approved is affected.`)) return;
    this.busy.set(true);
    this.api.inboxDiscard({ all_pending: true }).subscribe({
      next: () => { this.busy.set(false); this.load(); },
      error: () => { this.busy.set(false); this.error.set('Could not clear the inbox.'); },
    });
  }

  pendingPushes = computed(() => this.batches().filter(b => b.status === 'pending').length);

  private refreshIndex() {
    this.api.inbox().subscribe({ next: l => { this.batches.set(l.batches); this.durable.set(l.durable); }, error: () => {} });
  }

  // ── formatting ─────────────────────────────────────────────────────────────
  fmtDay(iso: string | null): string {
    if (!iso) return '—';
    const d = new Date(iso + 'T00:00:00');
    return isNaN(d.getTime()) ? '—' : d.toLocaleDateString('en-IN', { day: 'numeric', month: 'short' });
  }
  fmtWhen(ts: string | null | undefined): string {
    if (!ts) return '';
    const d = new Date(ts);
    if (isNaN(d.getTime())) return '';
    const mins = Math.round((Date.now() - d.getTime()) / 60000);
    if (mins < 1) return 'just now';
    if (mins < 60) return `${mins} min ago`;
    if (mins < 1440) return `${Math.round(mins / 60)} h ago`;
    return d.toLocaleDateString('en-IN', { day: 'numeric', month: 'short' });
  }
  /** "18 Jul 2026, 3:42 PM" — the exact moment the push landed */
  fmtWhenFull(ts: string | null | undefined): string {
    if (!ts) return 'unknown time';
    const d = new Date(ts);
    if (isNaN(d.getTime())) return 'unknown time';
    return d.toLocaleString('en-IN', { day: 'numeric', month: 'short', year: 'numeric', hour: 'numeric', minute: '2-digit', hour12: true });
  }
  issueText(r: InboxRow): string {
    const map: Record<string, string> = {
      date: 'needs a date', amount: 'needs an amount', category: 'pick a category',
      name: 'needs a name', direction: 'spend or income?',
    };
    return r.issues.map(i => map[i] || i).join(' · ');
  }
}
