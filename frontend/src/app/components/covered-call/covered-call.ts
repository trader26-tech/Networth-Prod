import {
  Component, OnInit, OnDestroy, inject, signal, computed,
  ViewChild, ElementRef, effect,
} from '@angular/core';
import { CommonModule, DecimalPipe } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { ApiService } from '../../services/api.service';

// ─────────────────────────────────────────────────────────────────────────────
// Types
// ─────────────────────────────────────────────────────────────────────────────
interface OtmCall {
  strike: number; premium: number; bid: number; ask: number;
  delta: number | null; iv: number | null; symbol: string;
  oi: number; distance: number; distance_pct: number; is_atm: boolean;
}
interface SetupData {
  underlying: string; spot: number; expiry: string; expiries: string[];
  lot_size: number; dte: number; niftybees_price: number;
  atm_strike: number; otm_calls: OtmCall[];
}
interface Scenario {
  label: string; pct: number; nifty: number;
  niftybees_pnl: number; call_pnl: number; total_pnl: number;
  roi: number; is_loss: boolean; is_capped: boolean; is_at_entry: boolean;
}
interface Analysis {
  underlying: string; spot: number; expiry: string; dte: number;
  strike: number; lots: number; lot_size: number;
  premium: number; current_call_price: number; call_price_source: 'live' | 'bs';
  bid: number; ltp: number;
  delta: number | null; iv: number | null; symbol: string;
  entry_nifty: number; niftybees_entry: number;
  niftybees_price: number; shares: number; shares_full: number; coverage_ratio: number;
  niftybees_cost: number; premium_total: number; net_capital: number;
  breakeven: number; downside_pct: number;
  upside_to_cap: number; upside_cap_pct: number;
  max_profit: number; max_profit_pct: number; atm_strike: number;
  payoff_curve: { nifty: number; pnl: number }[];
  scenarios: Scenario[];
  pnl_at_70pct_spot: number; pnl_at_50pct_spot: number;
  opportunity_cost_at_20pct_up: number;
}
interface ChartGeom {
  PAD: { top: number; right: number; bottom: number; left: number };
  cw: number; ch: number; W: number; H: number;
  xMin: number; xRange: number; yMin: number; yRange: number; dpr: number;
}

// ── Position tracking types ──────────────────────────────────────────────────
interface CCCall {
  id?: string; strike: number; expiry: string;
  lots: number; lot_size: number;
  premium_received: number; premium_total: number;
  entry_date?: string; exit_date?: string; exit_price?: number;
  pnl?: number; status: 'open' | 'closed' | 'expired' | 'rolled';
  capture_pct?: number;
}
interface CCPosition {
  id: string; name: string; created_at: string; status: 'active' | 'closed';
  underlying: string;
  shares: number; niftybees_entry_price: number; niftybees_cost: number; entry_nifty?: number;
  lots: number; lot_size: number;
  active_call: CCCall | null;
  call_history: CCCall[];
  total_premium_collected: number; notes: string;
  tags?: string[];
  live?: {
    nifty_spot: number; niftybees_price: number;
    etf_pnl: number; options_pnl: number; total_pnl: number; capture_pct: number;
    current_call_price?: number | null;
    call_ltp?: number; call_bid?: number; call_ask?: number; call_mid?: number;
    call_price_source?: 'direct' | 'chain' | 'bs';
    call_symbol?: string;
    call_last_trade?: string;
    call_delta?: number | null;
    call_iv?:    number | null;
  };
}
interface RankedStrike {
  rank: number; strike: number; premium: number; otm_pct: number;
  delta: number; iv: number; oi: number;
  days_to_50pct: number; premium_per_day: number;
  early_yield_pct: number; hold_yield_pct: number;
  prob_safe_pct: number; prob_keep_pct: number; score: number;
  // BTS scoring fields (NEW — primary ranking key)
  bts?: number;
  net_yield_per_cycle_pct?: number;
  cycle_quality?: number;
  iv_quality?: number;
  delta_quality?: number;
  in_entry_band?: boolean;
  entry_band?: 'in_band' | 'below_band' | 'above_band';
  assignment_risk: 'low' | 'medium' | 'high';
  liquidity: 'low' | 'medium' | 'high';
  max_gain_pct: number;
  why_good: string[]; why_caution: string[]; summary: string;
}
interface BestTradeCandidate extends RankedStrike {
  expiry: string; dte: number;
  expiry_rank: number; global_rank: number;
}
interface BestTradeData {
  underlying: string; spot: number; vix: number;
  expiries_scanned: { expiry: string; dte: number; candidates: number }[];
  total_scanned: number; top_n: number;
  candidates: BestTradeCandidate[];
  same_expiry_alternatives: BestTradeCandidate[];
  scoring: {
    formula: string;
    components: { [key: string]: string };
    why_short_dated_dominates_globally: string;
    tie_breaker: string;
  };
}
interface PremarketData {
  timestamp: string; spot: number; expiry: string; expiries: string[]; dte: number;
  vix: number; vix_ok: boolean; vix_label: string;
  atm_iv: number | null; iv_richness_pct: number; iv_label: string; iv_ok: boolean;
  pcr: number; pcr_label: string; pcr_ok: boolean;
  checks_passed: number; checks_total: number;
  overall_signal: 'go' | 'caution' | 'skip';
  ranked_strikes: RankedStrike[];
}

// ─────────────────────────────────────────────────────────────────────────────
// Component
// ─────────────────────────────────────────────────────────────────────────────
@Component({
  selector: 'app-covered-call',
  imports: [CommonModule, FormsModule, DecimalPipe, RouterLink],
  templateUrl: './covered-call.html',
  styleUrl: './covered-call.scss',
})
export class CoveredCallComponent implements OnInit, OnDestroy {
  private api = inject(ApiService);

  @ViewChild('payoffCanvas')  canvasRef!:  ElementRef<HTMLCanvasElement>;
  @ViewChild('overlayCanvas') overlayRef!: ElementRef<HTMLCanvasElement>;

  // ── View state ────────────────────────────────────────────────────────────
  currentView = signal<'hub' | 'new' | 'detail'>('hub');

  // ── Hub state ─────────────────────────────────────────────────────────────
  positions       = signal<CCPosition[]>([]);
  premarket       = signal<PremarketData | null>(null);
  bestTradeChecks = signal<PremarketData | null>(null);   // per-expiry checks for current best-trade pick
  hubLoading      = signal(true);
  premktLoading   = signal(false);   // first-load only — replaces UI with spinner
  premktRefreshing = signal(false);  // silent background refresh — keeps data visible
  selectedPos     = signal<CCPosition | null>(null);

  // Premarket scan filters
  premktExpiryFilter = signal('');
  expandedRankIdx    = signal<number | null>(null);
  premktExpanded     = signal(false);
  premktMode         = signal<'best' | 'expiry'>('best');
  // Strategy cadence — monthly = sell-and-re-sell every 22d (default),
  // quarterly = sell longer-dated calls with 30% TP + vega/MTM exits.
  cadence            = signal<'monthly' | 'quarterly'>('monthly');
  strikePickerOpen   = signal(false);  // "Select call to sell" — collapsed by default
  bestStrikeDetailsOpen = signal(false);
  bestStrikeOpenSections = signal<Set<string>>(new Set());
  showAllStrikes     = signal(false);

  // Best trade (cross-expiry) modal
  bestTradeOpen     = signal(false);
  bestTradeLoading  = signal(false);
  bestTradeError    = signal('');
  bestTrade         = signal<BestTradeData | null>(null);
  bestTradeAltIdx   = signal<number | null>(null);   // which alt card is expanded
  bestTradeRationaleOpen = signal(false);            // info panel toggle

  // Close call dialog (rich)
  closeCallOpen     = signal(false);
  closeCallPid      = signal('');
  closeCallPrice    = signal('');
  closeCallKind     = signal<'expired_worthless' | 'closed_at_profit' | 'closed_at_loss' | 'rolled' | 'assigned'>('closed_at_profit');
  closeNbAction     = signal<'held_all' | 'sold_partial' | 'sold_all'>('held_all');
  closeNbShares     = signal('');
  closeNbSellPrice  = signal('');
  closeNotes        = signal('');
  closeCallLoading  = signal(false);

  // Position context for the close dialog (snapshot at open)
  closeCallContext  = signal<any | null>(null);

  // Save position dialog
  saveDialogOpen   = signal(false);
  saveName         = signal('');
  savingPosition   = signal(false);

  // ── Entry price correction ────────────────────────────────────────────────
  editingEntryPrice  = signal(false);
  entryNiftyInput    = signal('');
  savingEntryPrice   = signal(false);
  entryPriceSavedMsg = signal('');

  // ── Analysis state ────────────────────────────────────────────────────────
  setup      = signal<SetupData | null>(null);
  analysis   = signal<Analysis | null>(null);
  loading    = signal(false);
  analyzing  = signal(false);
  refreshing = signal(false);
  error      = signal('');
  lastUpdatedStr = signal('');

  selectedExpiry  = signal('');
  selectedStrike  = signal(0);
  lots            = signal(1);
  showRiskDetail  = signal(false);
  chartMode          = signal<'combined' | 'breakdown'>('combined');
  chartZoom          = signal<'in' | 'out'>('out');

  // Phase 2C — Hedge overlay on payoff diagram
  // 'off'    : show CC payoff only (default)
  // 'overlay': add a teal "CC + Hedge" line on top of CC line
  hedgeOverlayMode   = signal<'off' | 'overlay'>('off');
  hedgeOverlayId     = signal<string>('');     // which hedge from openHedges to overlay
  hedgeOverlayHedge  = computed(() => {
    const id = this.hedgeOverlayId();
    if (!id) return null;
    return this.openHedges().find(h => h.id === id) || null;
  });
  targetDaysFromNow  = signal(0);
  targetNifty        = signal(0);

  tooltipVisible = signal(false);
  tooltipX       = signal(0);
  tooltipY       = signal(0);
  tooltipData    = signal<{
    nifty: number; pnl: number; zone: string;
    nbPnl?: number; callPnl?: number;
    targetPnl: number; targetNbPnl: number; targetCallPnl: number;
    targetDFN: number; targetDaysLeft: number;
    // Phase 2C — hedge tooltip extras
    hedgePnl?: number;          // hedge alone P&L at this Nifty
    cchedgePnl?: number;        // CC + Hedge combined at expiry
    hedgeStrike?: number;       // for label
    hedgeDte?: number;          // hedge time-to-expiry
    hedgeIsActive?: boolean;    // whether overlay is on AND a hedge is selected
  } | null>(null);

  // AI chat
  chatOpen     = signal(false);
  chatLoading  = signal(false);
  chatInput    = signal('');
  chatMessages = signal<{ role: 'user' | 'assistant'; content: string }[]>([]);

  // ── Exit-strategy engine (roll-up-on-momentum) ───────────────────────────
  exitStatus       = signal<any | null>(null);
  exitLoading      = signal(false);
  rollingUp        = signal(false);
  exitLastUpdated  = signal('');
  expandedStopInfo = signal<'stop1' | 'stop2' | 'stop3' | 'stop4' | null>(null);
  toggleStopInfo(s: 'stop1' | 'stop2' | 'stop3' | 'stop4') {
    this.expandedStopInfo.update(v => v === s ? null : s);
  }

  // Static encyclopedia of each stop — what / why / risks / progress-bar max.
  // Lives on the component so the popover can reference it without a backend call.
  stopInfo: Record<'stop1' | 'stop2' | 'stop3' | 'stop4', {
    title: string; what: string; why_good: string; risks: string[]; bar_max: string; fires_at: string;
  }> = {
    stop1: {
      title:    'Take-Profit (60% rule)',
      what:     "Watches the live LTP of the short call. The moment it falls to ≤ 40% of the premium you originally received (= 60% of max profit captured), the stop fires.",
      why_good: "Plateau analysis (50–65% TP) shows Sharpe ratio is essentially identical, but 60% delivers ~10% more annual yield. Combined with the DTE ≤ 14 force-exit (Stop 5), gamma-tail risk is bounded.",
      risks: [
        "If you exit too early in a strong-rally setup you forfeit further decay AND have to re-write at a strike that may be too close to spot.",
        "On a thinly-traded contract the LTP can be stale — verify the bid before buying back.",
        "After the buyback you still have the ETF leg, which is uncovered until you sell a fresh call.",
      ],
      bar_max:  "100% = the call has decayed to ₹0 (full premium captured). The stop fires at 60%, so the bar is meant to be acted on around 60%, not pushed to 100%.",
      fires_at: "Call LTP ≤ premium_received × 0.4  ·  AND  ·  DTE > 0",
    },
    stop2: {
      title:    'Roll-Up trigger (Δ ≥ 0.40)',
      what:     "Fires when the short call's delta crosses 0.40 (≈ 40% chance of finishing ITM) AND we still have time for theta to work in a fresh cycle.",
      why_good: "Acting at delta 0.40 (still OTM) is far cheaper than waiting for spot to cross strike — the buyback cost is much lower, and you keep more of NiftyBees' upside by uncapping early. The 0.40 threshold gives a Δ 0.25-0.30 entry the right amount of breathing room (~2.5% Nifty rally) before firing.",
      risks: [
        "Rolling chases the move — if Nifty reverses right after the roll, you lock in extra cost without the upside benefit.",
        "Extending DTE adds gamma exposure for longer.",
        "Roll-up only works when the new strike still offers usable premium; in low-IV regimes the roll may be net-debit.",
      ],
      bar_max:  "100% = call delta has reached 0.40 (the fire threshold). At 100% the trigger is met (subject to DTE > 5).",
      fires_at: "Δ ≥ 0.40  ·  AND  ·  DTE > 5",
    },
    stop3: {
      title:    'Assignment Defence',
      what:     "Triggers in the final 0–1 days before expiry IF the call is in-the-money. Without action you risk physical assignment / cash-settlement loss equal to the intrinsic value.",
      why_good: "Expiry-day intrinsic value crystallises as a real loss on the options leg (your ETF gain offsets it but you lose flexibility). Buying back a 0-DTE ITM call avoids assignment and frees the ETF leg to keep running.",
      risks: [
        "Buying back an ITM call on expiry day is expensive — intrinsic value is unavoidable; only time-value is recovered.",
        "Strong gap-down opens may flip the call back OTM after you've paid to close.",
        "Liquidity tightens late in the session; spreads can widen 5×.",
      ],
      bar_max:  "100% = at expiry day (DTE = 0). The bar tracks how close you are to the assignment window; the fill colour deepens as the trade approaches expiry.",
      fires_at: "DTE ≤ 1  ·  AND  ·  Spot > Strike (ITM)",
    },
    stop4: {
      title:    'Hold (no-action zone)',
      what:     "Default state when stops 1–3 are quiet. Theta is ticking in your favour and the trade is doing exactly what it was designed to do.",
      why_good: "Doing nothing is a strategy. Every day spent in this zone is a day of premium decay landing on your P&L without any execution cost. The best covered-call cycles spend ~80% of their life here.",
      risks: [
        "Complacency — even a quiet trade can flip overnight on news or RBI/Fed surprises. Re-check daily, not just when alerts fire.",
        "If IV expands (VIX spike), the call's mark-to-market value can rise even with no spot move, temporarily eroding the captured P&L.",
      ],
      bar_max:  "100% = the entire premium has been captured (call worth ₹0 with the trade still open). In practice you'd want to roll out of this stop into Stop 1 long before reaching 100%.",
      fires_at: "Stops 1, 2, 3 all silent  ·  AND  ·  trade still open",
    },
  };

  // Stop3 has no native progress field — derive an expiry-urgency bar.
  // Assumes a typical monthly cycle starts ~30 DTE; clamp 0–100.
  stop3Progress(stop3: any): number {
    const dte = stop3?.dte ?? 30;
    return Math.max(0, Math.min(100, ((30 - dte) / 30) * 100));
  }
  // Stop4: % of premium captured = captured / (captured + remaining)
  stop4Progress(stop4: any): number {
    const cap = Number(stop4?.captured_so_far ?? 0);
    const rem = Number(stop4?.remaining_to_capture ?? 0);
    const total = cap + rem;
    if (total <= 0) return 0;
    return Math.max(0, Math.min(100, (cap / total) * 100));
  }

  // ── Hub summary (home screen): KPIs + categorized queue + charts ─────────
  hubSummary       = signal<any | null>(null);
  hubSummaryLoading = signal(false);

  // Collapse state — everything closed by default; user opens what they need.
  hubChartsExpanded = signal(false);
  hubHistoryExpanded = signal(false);
  // Per-category content expansion (header always visible, content opens on click)
  hubCatExpanded = signal<{ stop1: boolean; stop2: boolean; stop3: boolean; stop4: boolean }>({
    stop1: false, stop2: false, stop3: false, stop4: false,
  });

  @ViewChild('cumulativeCanvas') cumulativeCanvasRef?: ElementRef<HTMLCanvasElement>;
  @ViewChild('monthlyCanvas')    monthlyCanvasRef?:    ElementRef<HTMLCanvasElement>;

  private chartGeom: ChartGeom | null = null;
  private refreshTimer: any;

  // ── Computed ─────────────────────────────────────────────────────────────
  expiries = computed(() => this.setup()?.expiries ?? []);
  calls    = computed(() => this.setup()?.otm_calls ?? []);
  selectedCall = computed(() =>
    this.calls().find(c => Math.abs(c.strike - this.selectedStrike()) < 0.5) ?? null
  );
  spotDisplay = computed(() => this.analysis()?.spot ?? this.setup()?.spot ?? 0);

  activePositions = computed(() => this.positions().filter(p => p.status === 'active'));
  closedPositions = computed(() => this.positions().filter(p => p.status === 'closed'));

  // ── Tag filter (active positions) ─────────────────────────────────────────
  // Selected filter tags. If empty → show everything. Otherwise show
  // positions that include at least one of the selected tags ("OR" semantics).
  selectedTagFilters = signal<Set<string>>(new Set());
  // Tag-edit state: which position currently has its inline tag editor open
  editingTagsFor    = signal<string | null>(null);
  newTagInput       = signal('');
  tagSavingFor      = signal<string | null>(null);

  // Every unique tag across active positions, sorted, with count.
  allActiveTags = computed<{ tag: string; count: number }[]>(() => {
    const counts = new Map<string, number>();
    for (const p of this.activePositions()) {
      for (const t of (p.tags || [])) {
        const k = (t || '').toLowerCase().trim();
        if (!k) continue;
        counts.set(k, (counts.get(k) ?? 0) + 1);
      }
    }
    return [...counts.entries()]
      .map(([tag, count]) => ({ tag, count }))
      .sort((a, b) => b.count - a.count || a.tag.localeCompare(b.tag));
  });

  // Active positions after applying the tag filter — feeds the sort/group pipeline.
  filteredActivePositions = computed<CCPosition[]>(() => {
    const sel = this.selectedTagFilters();
    const list = this.activePositions();
    if (sel.size === 0) return list;
    return list.filter(p => (p.tags || []).some(t => sel.has((t || '').toLowerCase())));
  });

  // All positions (active + closed) filtered by the same tag selection.
  // Used by hubStats so KPIs reflect only the selected tag(s).
  filteredAllPositions = computed<CCPosition[]>(() => {
    const sel = this.selectedTagFilters();
    const list = this.positions();
    if (sel.size === 0) return list;
    return list.filter(p => (p.tags || []).some(t => sel.has((t || '').toLowerCase())));
  });

  toggleTagFilter(tag: string) {
    const k = tag.toLowerCase();
    this.selectedTagFilters.update(prev => {
      const next = new Set(prev);
      if (next.has(k)) next.delete(k); else next.add(k);
      return next;
    });
  }
  clearTagFilters() { this.selectedTagFilters.set(new Set()); }
  isTagFilterActive(tag: string): boolean {
    return this.selectedTagFilters().has(tag.toLowerCase());
  }

  // Stable tone (1-6) per tag — keeps the same colour every render.
  tagTone(tag: string): number {
    const s = (tag || '').toLowerCase();
    let h = 0;
    for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
    return Math.abs(h) % 6;
  }

  // ── Per-card tag editing ──────────────────────────────────────────────────
  openTagEditor(pid: string, e: Event) {
    e.stopPropagation();
    this.newTagInput.set('');
    this.editingTagsFor.set(pid);
  }
  closeTagEditor() {
    this.editingTagsFor.set(null);
    this.newTagInput.set('');
  }
  private _saveTags(p: CCPosition, nextTags: string[]) {
    this.tagSavingFor.set(p.id);
    this.api.updateCoveredCallTags(p.id, nextTags).subscribe({
      next: (r) => {
        const tags = r?.tags ?? nextTags;
        this.positions.update(list => list.map(x => x.id === p.id ? { ...x, tags } : x));
        this.tagSavingFor.set(null);
      },
      error: () => { this.tagSavingFor.set(null); },
    });
  }
  addTag(p: CCPosition, raw: string) {
    const t = (raw || '').toLowerCase().trim();
    if (!t || t.length > 24) return;
    const current = (p.tags || []).map(x => x.toLowerCase());
    if (current.includes(t)) { this.newTagInput.set(''); return; }
    if (current.length >= 10) return;
    this._saveTags(p, [...current, t]);
    this.newTagInput.set('');
  }
  removeTag(p: CCPosition, tag: string, e: Event) {
    e.stopPropagation();
    const next = (p.tags || []).filter(x => x.toLowerCase() !== tag.toLowerCase());
    this._saveTags(p, next);
    // If this was the only position with this tag and the user had it
    // selected as a filter, drop it so the empty-state doesn't appear.
    if (this.isTagFilterActive(tag)) {
      const stillExists = this.activePositions().some(pp => pp.id !== p.id && (pp.tags || []).some(t => t.toLowerCase() === tag.toLowerCase()));
      if (!stillExists) {
        this.selectedTagFilters.update(prev => {
          const n = new Set(prev); n.delete(tag.toLowerCase()); return n;
        });
      }
    }
  }
  onTagInputKey(e: KeyboardEvent, p: CCPosition) {
    if (e.key === 'Enter' || e.key === ',') {
      e.preventDefault();
      this.addTag(p, this.newTagInput());
    } else if (e.key === 'Escape') {
      this.closeTagEditor();
    }
  }

  // ── Hub-level KPIs (top of positions tab) ─────────────────────────────────
  // 1) Total premium collected — lifetime headline number for an income strategy
  // 2) Live P&L across active positions — current state of the book
  // 3) Win-rate on closed cycles — long-run skill / track-record signal
  hubStats = computed(() => {
    const ps = this.filteredAllPositions();
    const active = ps.filter(p => p.status === 'active');
    const isFiltered = this.selectedTagFilters().size > 0;

    let totalPremium = 0;
    let cyclesDone   = 0;
    let cyclesProfit = 0;
    for (const p of ps) {
      totalPremium += Number(p.total_premium_collected || 0);
      for (const c of (p.call_history || [])) {
        if (c.status === 'closed' || c.status === 'expired' || c.status === 'rolled') {
          cyclesDone += 1;
          if ((c.pnl ?? 0) > 0) cyclesProfit += 1;
        }
      }
    }

    let livePnl = 0;
    let priced  = 0;
    for (const p of active) {
      if (p.live) { livePnl += Number(p.live.total_pnl || 0); priced += 1; }
    }

    const winRate = cyclesDone > 0 ? (cyclesProfit / cyclesDone) * 100 : null;

    // Soonest-expiry callout — useful little hint under the live-P&L card
    let nearestExpiryDays: number | null = null;
    let nearestExpiryName = '';
    for (const p of active) {
      if (!p.active_call) continue;
      const dte = this.daysToCalendar(p.active_call.expiry);
      if (nearestExpiryDays === null || dte < nearestExpiryDays) {
        nearestExpiryDays = dte;
        nearestExpiryName = p.name;
      }
    }

    return {
      totalPremium,
      livePnl,
      cyclesDone,
      cyclesProfit,
      winRate,
      activeCount:  active.length,
      totalCount:   ps.length,
      pricedCount:  priced,
      nearestExpiryDays,
      nearestExpiryName,
      isFiltered,
    };
  });

  // ── Sort controls (hub) ───────────────────────────────────────────────────
  sortBy    = signal<'bought' | 'criticality' | 'expiry' | 'opt_pnl' | 'nb_pnl' | 'total_pnl'>('bought');
  sortOrder = signal<'asc' | 'desc'>('desc');
  // When sorting by criticality, this picks which bucket sits on top.
  criticalityPriority = signal<'defend' | 'roll' | 'take' | 'hold'>('defend');
  showSortMenu = signal(false);

  toggleSortMenu()  { this.showSortMenu.update(v => !v); }
  closeSortMenu()   { this.showSortMenu.set(false); }
  toggleSortOrder() { this.sortOrder.update(o => o === 'desc' ? 'asc' : 'desc'); }

  setSortBy(v: 'bought' | 'criticality' | 'expiry' | 'opt_pnl' | 'nb_pnl' | 'total_pnl') {
    this.sortBy.set(v);
  }
  setCriticalityPriority(v: 'defend' | 'roll' | 'take' | 'hold') {
    this.criticalityPriority.set(v);
  }

  // Discrete urgency bucket — drives both sort + on-card badge.
  criticalityBucket(p: any): 'defend' | 'roll' | 'take' | 'hold' {
    if (!p?.active_call) return 'hold';
    const ac   = p.active_call;
    const live = p.live;
    const dte  = Math.max(0, this.daysToCalendar(ac.expiry));
    if (dte <= 1) return 'defend';
    if (live?.options_pnl < 0) {
      const collected = ac.premium_received * ac.lots * ac.lot_size;
      if (collected > 0 && Math.abs(live.options_pnl) >= collected) return 'roll';
    }
    if (live?.current_call_price != null && ac.premium_received > 0) {
      const pct = live.current_call_price / ac.premium_received;
      if (pct <= 0.5 && pct > 0) return 'take';
    }
    return 'hold';
  }

  bucketLabel(b: string): string {
    return ({ defend: 'Defend', roll: 'Roll-up', take: 'Take profit', hold: 'Hold' } as any)[b] ?? '';
  }

  /**
   * What the trader should do when a position is in this bucket. Surfaced as
   * the title-tooltip on the slim row's bucket pill so HOLD positions show
   * what's actually expected ("let theta work, watch 60% TP / Δ 0.40 / DTE 14").
   */
  bucketHint(b: string): string {
    switch (b) {
      case 'defend': return 'Defend NOW — DTE ≤ 1 with the call ITM. Close immediately or let it auto-assign and lose your NB position.';
      case 'roll':   return 'Roll up — Δ ≥ 0.40 means the call is too close to ITM. Buy back, sell a higher strike next month.';
      case 'take':   return 'Take profit — premium decayed to 60%+ captured. Close + redeploy fresh next cycle.';
      case 'hold':   return 'Hold — let theta work. Watching: 60% TP, Δ ≥ 0.40 roll-up, DTE ≤ 14 force-exit. No action until one fires.';
      default:       return '';
    }
  }

  private bucketRank(b: string, priority: string): number {
    // Higher rank = sorted to top when desc.
    const baseOrder = ['defend', 'roll', 'take', 'hold'];
    const reordered = [priority, ...baseOrder.filter(x => x !== priority)];
    return reordered.length - reordered.indexOf(b);
  }

  private _sortPositions(list: any[]): any[] {
    const dir = this.sortOrder() === 'desc' ? -1 : 1;
    const by  = this.sortBy();
    const keyFn: (p: any) => number = (p) => {
      switch (by) {
        case 'bought':      return this.parseLocalDate(p.created_at).getTime();
        case 'criticality': return this.bucketRank(this.criticalityBucket(p), this.criticalityPriority());
        case 'expiry':      return p.active_call ? this.parseLocalDate(p.active_call.expiry).getTime() : Infinity;
        case 'opt_pnl':     return p.live?.options_pnl ?? 0;
        case 'nb_pnl':      return p.live?.etf_pnl ?? 0;
        case 'total_pnl':   return p.live?.total_pnl ?? 0;
      }
      return 0;
    };
    return [...list].sort((a, b) => (keyFn(a) - keyFn(b)) * dir);
  }

  sortedActivePositions = computed(() => this._sortPositions(this.filteredActivePositions()));
  sortedClosedPositions = computed(() => this._sortPositions(this.closedPositions()));

  // Group sorted positions under date / bucket headers.
  // - sortBy=bought  → group by bought date
  // - sortBy=expiry  → group by call-expiry date
  // - sortBy=criticality → group by bucket
  // - everything else → single group (no header)
  private _groupPositions(list: any[]): { key: string; label: string; sub: string; tone: string; positions: any[] }[] {
    const by = this.sortBy();
    if (by !== 'bought' && by !== 'expiry' && by !== 'criticality') {
      return list.length ? [{ key: 'all', label: '', sub: '', tone: '', positions: list }] : [];
    }
    const groups = new Map<string, any[]>();
    const meta:   Map<string, { label: string; sub: string; tone: string }> = new Map();

    for (const p of list) {
      let key = '', label = '', sub = '', tone = '';
      if (by === 'bought' || by === 'expiry') {
        const raw = by === 'bought' ? p.created_at : p.active_call?.expiry;
        if (!raw) continue;
        key   = this.dateKey(raw);
        if (!key) continue;
        label = this.fmtDateWithDay(raw);
        sub   = this.daysFromToday(raw);
        tone  = by === 'expiry' ? 'expiry' : 'bought';
      } else { // criticality
        const b = this.criticalityBucket(p);
        key   = b;
        label = this.bucketLabel(b);
        sub   = '';
        tone  = `bucket-${b}`;
      }
      if (!groups.has(key)) {
        groups.set(key, []);
        meta.set(key, { label, sub, tone });
      }
      groups.get(key)!.push(p);
    }

    return Array.from(groups.entries()).map(([k, arr]) => ({
      key: k, ...meta.get(k)!, positions: arr,
    }));
  }

  groupedActivePositions = computed(() => this._groupPositions(this.sortedActivePositions()));
  groupedClosedPositions = computed(() => this._groupPositions(this.sortedClosedPositions()));

  targetPnl = computed(() => {
    const a = this.analysis();
    if (!a) return null;
    const spot     = this.targetNifty() > 0 ? this.targetNifty() : a.spot;
    const daysLeft = Math.max(a.dte - this.targetDaysFromNow(), 0);
    return this.pnlAtDate(spot, a, daysLeft);
  });

  positionAlerts = computed(() => {
    const a = this.analysis();
    if (!a) return null;
    const refSpot        = this.targetNifty() > 0 ? this.targetNifty() : a.spot;
    const daysLeft       = Math.max(a.dte - this.targetDaysFromNow(), 0);
    const iv             = (a.iv ?? 16) / 100;
    const currentCallVal = this.bsCall(refSpot, a.strike, daysLeft / 365, iv);
    const callGross      = a.lots * a.lot_size * (currentCallVal - a.premium);
    const callMtmLoss    = Math.max(0, callGross);
    const callPnl        = a.lots * a.lot_size * (a.premium - currentCallVal);
    const extraMargin    = callMtmLoss * 1.1;
    const callIsITM      = refSpot > a.strike;
    const currentNbPrice = a.niftybees_price * (refSpot / a.spot);
    const nbMtmLoss      = a.shares * Math.max(0, a.niftybees_price - currentNbPrice);
    const nbPctChange    = (refSpot - a.spot) / a.spot * 100;
    const nbIsDown       = refSpot < a.spot;
    const avgDownCost    = a.shares * currentNbPrice;
    const newAvgPrice    = (a.niftybees_price + currentNbPrice) / 2;
    return {
      refSpot, daysLeft,
      isScenario: this.targetNifty() > 0 || this.targetDaysFromNow() > 0,
      callIsITM, callMtmLoss, currentCallVal, extraMargin, callPnl,
      currentNbPrice, nbMtmLoss, nbPctChange, nbIsDown,
      avgDownShares: a.shares, avgDownCost, newAvgPrice,
    };
  });

  // ── Detail view: live P&L from exitStatus (most fresh data) ──────────────
  // Used for chart overlays and the live panel. Falls back to selectedPos.live
  // if exitStatus isn't loaded yet.
  detailLivePnl = computed<number | null>(() => {
    if (this.currentView() !== 'detail') return null;
    const pos = this.selectedPos();
    if (!pos) return null;
    const es = this.exitStatus();
    if (es?.spot && pos.active_call) {
      const spot   = Number(es.spot);
      const nbLive = spot / 100;
      const etfPnl = pos.shares * (nbLive - pos.niftybees_entry_price);
      const ac     = pos.active_call;
      const curPr  = es.active_call?.current_price ?? null;
      const callPnl = curPr != null
        ? (ac.premium_received - curPr) * ac.lots * ac.lot_size
        : 0;
      return Math.round(etfPnl + callPnl);
    }
    return pos.live?.total_pnl ?? null;
  });

  // ── Lifecycle ─────────────────────────────────────────────────────────────
  constructor() {
    effect(() => {
      const a = this.analysis();
      this.chartMode();
      this.chartZoom();
      this.targetDaysFromNow();
      this.targetNifty();
      this.hedgeOverlayMode();
      this.hedgeOverlayId();
      // Also track exitStatus so the chart re-draws when live data refreshes
      this.exitStatus();
      this.selectedPos();
      if (a) setTimeout(() => this.drawChart(a), 50);
    });
  }

  // ── Strategy Playbook modal ────────────────────────────────────────────
  playbookOpen = signal(false);
  openPlaybook()  { this.playbookOpen.set(true); }
  closePlaybook() { this.playbookOpen.set(false); }

  // ── KPI info popovers (which one is open, if any) ──────────────────────
  kpiInfoOpen = signal<'premium' | 'pnl' | 'winrate' | null>(null);
  toggleKpiInfo(which: 'premium' | 'pnl' | 'winrate') {
    this.kpiInfoOpen.set(this.kpiInfoOpen() === which ? null : which);
  }

  // ── Position card slim/expanded state ──────────────────────────────────
  expandedPositions = signal<Set<string>>(new Set());
  togglePositionDetails(pid: string, ev: Event) {
    ev.stopPropagation();
    const next = new Set(this.expandedPositions());
    if (next.has(pid)) next.delete(pid); else next.add(pid);
    this.expandedPositions.set(next);
  }
  isPositionExpanded(pid: string): boolean {
    return this.expandedPositions().has(pid);
  }

  // ── Hedges + NB drift state (Phase 2B + 3A) ───────────────────────────
  openHedges       = signal<any[]>([]);
  openHedgesCount  = computed(() => this.openHedges().length);
  hedgesAnnualDrag = signal<string | null>(null);

  loadOpenHedges() {
    this.api.listHedges('open').subscribe({
      next: (r: any) => {
        const hs = r.hedges || [];
        this.openHedges.set(hs);
        // Compute aggregate annual drag % of spot
        const spot = r.spot || this.niftySpot();
        if (!hs.length || !spot) {
          this.hedgesAnnualDrag.set(null);
          return;
        }
        const totalCost = hs.reduce((s: number, h: any) =>
          s + (h.premium_paid || 0) * (h.lots || 1) * (h.lot_size || 75), 0);
        const avgDte = hs.reduce((s: number, h: any) => s + (h.dte || 0), 0) / hs.length;
        if (!avgDte) { this.hedgesAnnualDrag.set(null); return; }
        const annualCost = totalCost * (365 / avgDte);
        const dragPct = (annualCost / spot) * 100;
        this.hedgesAnnualDrag.set(dragPct.toFixed(2));
      },
      error: () => {
        this.openHedges.set([]);
        this.hedgesAnnualDrag.set(null);
      },
    });
  }

  // ── Counter-cyclical alerts (hedge-only) ───────────────────────────────
  counterCyclicalAlerts = computed(() => {
    const alerts: Array<{
      id: string; tone: 'good' | 'warn' | 'bad' | 'info';
      icon: string; title: string; action: string;
      cta?: { route: string; label: string };
    }> = [];

    const hedges = this.openHedges();

    // ① Hedge fires (deeply ITM put)
    for (const h of hedges) {
      if (h.unrealized_pnl != null && h.unrealized_pnl > 0 && h.current_price != null && h.premium_paid > 0) {
        const gain_pct = (h.current_price / h.premium_paid - 1) * 100;
        if (gain_pct >= 100) {   // hedge has 2x'd
          alerts.push({
            id: `hedge-fired-${h.id}`,
            tone: 'good',
            icon: '🛡️',
            title: `Hedge ${h.strike} PE has 2x'd (+${gain_pct.toFixed(0)}%)`,
            action: 'NB has likely crashed. Wait for VIX < 18 OR Nifty up 5% from local low, then DCA hedge profit into NB over 4-8 weeks.',
            cta: { route: '/hedges', label: 'Manage hedge' },
          });
          break; // one alert max
        }
      }
    }

    // ② No hedge open — prompt to start one
    if (hedges.length === 0 && this.positions().length > 0) {
      alerts.push({
        id: 'no-hedge',
        tone: 'warn',
        icon: '🛡️',
        title: 'You have CC positions open but NO HEDGE',
        action: 'A protective put limits crash damage to ~13% vs 30% raw. Open a hedge before the next bad month.',
        cta: { route: '/hedges', label: 'Start a hedge' },
      });
    }

    return alerts;
  });

  ngOnInit() {
    this.loadHub();
    this.loadOpenHedges();
    // Auto-refresh:
    //  - Hub: positions only (premarket lives in the 'new' view now)
    //  - Analysis views: chain data only
    //  - Premarket is NEVER auto-refreshed — user triggers it via Refresh button
    // Hub polls positions every 15s for live MTM. Detail view polls chain
    // (via _fetchSetup) and exit-status every 30s. Manual refresh button on
    // the hub force-runs loadPositions immediately.
    this.refreshTimer = setInterval(() => {
      if (this.currentView() === 'hub') {
        this.loadPositions();
      } else if (!this.loading() && !this.analyzing()) {
        this._fetchSetup(false);
        if (this.currentView() === 'detail' && this.selectedPos()) {
          this.loadExitStatus();
        }
      }
    }, 10000);
  }

  ngOnDestroy() { clearInterval(this.refreshTimer); }

  // ── Hub ────────────────────────────────────────────────────────────────────
  loadHub() {
    this.loadPositions();
    this.loadHubSummary();
  }

  hubLastUpdated = signal('');

  // ── Live Nifty ticker ─────────────────────────────────────────────────────
  niftySpot    = signal(0);
  niftySpotRef = signal(0);   // first value of session — used for change %

  private _setNiftySpot(spot: number) {
    if (!spot || spot <= 0) return;
    if (!this.niftySpotRef()) this.niftySpotRef.set(spot);
    this.niftySpot.set(spot);
  }

  niftyChangeAbs = computed(() => this.niftySpot() - this.niftySpotRef());
  niftyChangePct = computed(() => {
    const ref = this.niftySpotRef();
    return ref > 0 ? ((this.niftySpot() - ref) / ref) * 100 : 0;
  });
  niftyUp = computed(() => this.niftySpot() >= this.niftySpotRef());

  loadPositions() {
    // Show skeleton only on first load — subsequent refreshes update silently
    if (!this.positions().length) this.hubLoading.set(true);

    this.api.getCoveredCallPositions().subscribe({
      next: (res) => {
        // Spot comes from the top-level field — always present regardless of position count
        if (res.spot) this._setNiftySpot(res.spot);
        const ps = res.positions ?? [];
        this.positions.set(ps);
        this.hubLoading.set(false);
        this.hubLastUpdated.set(new Date().toLocaleTimeString('en-IN', {
          hour: '2-digit', minute: '2-digit', second: '2-digit',
        }));
        // Keep detail view's selectedPos live data in sync.
        const sel = this.selectedPos();
        if (sel) {
          const refreshed = ps.find((p: CCPosition) => p.id === sel.id);
          if (refreshed) this.selectedPos.set(refreshed);
        }
      },
      error: () => {
        this.hubLoading.set(false);
        // Keep existing positions visible on transient errors — don't wipe the view
      },
    });
  }

  loadHubSummary() {
    // Show loading state only on first load
    if (!this.hubSummary()) this.hubSummaryLoading.set(true);

    this.api.getHubSummary().subscribe({
      next: (s) => {
        if (s?.spot) this._setNiftySpot(s.spot);
        this.hubSummary.set(s);
        this.hubSummaryLoading.set(false);
        if (this.hubChartsExpanded()) {
          setTimeout(() => { this.drawCumulativeChart(); this.drawMonthlyChart(); }, 50);
        }
      },
      error: () => { this.hubSummaryLoading.set(false); },
    });
  }

  toggleHubCharts() {
    const willOpen = !this.hubChartsExpanded();
    this.hubChartsExpanded.set(willOpen);
    if (willOpen) {
      setTimeout(() => { this.drawCumulativeChart(); this.drawMonthlyChart(); }, 80);
    }
  }

  toggleHubHistory() { this.hubHistoryExpanded.update(v => !v); }

  toggleHubCat(key: 'stop1' | 'stop2' | 'stop3' | 'stop4') {
    this.hubCatExpanded.update(s => ({ ...s, [key]: !s[key] }));
  }

  // ── Hub charts ─────────────────────────────────────────────────────────────
  // Chart 1: P&L decomposition over time — premium kept (above zero, green) +
  // option losses (below zero, red), plus current NB-unrealised band on the
  // far right so user sees where every rupee actually came from.
  drawCumulativeChart() {
    const decomp = this.hubSummary()?.pnl_decomposition;
    const series: any[] = decomp?.series || [];
    const totals = decomp?.totals || { premium_kept: 0, option_losses: 0, nb_unrealised: 0 };
    const canvas = this.cumulativeCanvasRef?.nativeElement;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const dpr = window.devicePixelRatio || 1;
    const W = canvas.parentElement?.clientWidth ?? 600;
    const H = 240;
    canvas.width = W * dpr; canvas.height = H * dpr;
    canvas.style.width = W + 'px'; canvas.style.height = H + 'px';
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);

    const PAD = { top: 22, right: 110, bottom: 32, left: 64 };
    const cw = W - PAD.left - PAD.right, ch = H - PAD.top - PAD.bottom;

    if (!series.length) {
      ctx.fillStyle = 'rgba(120,140,170,0.7)';
      ctx.font = '13px system-ui'; ctx.textAlign = 'center';
      ctx.fillText('No closed cycles yet — your P&L decomposition will appear here', W / 2, H / 2);
      return;
    }

    // Y-range: top = premium_kept (max), bottom = -option_losses (max)
    // Plus extend for NB unrealised slab on the right.
    const maxPrem = Math.max(0, ...series.map(d => d.premium_kept));
    const maxLoss = Math.max(0, ...series.map(d => d.option_losses));
    const nbBand  = Math.abs(totals.nb_unrealised);
    const yHiData = Math.max(maxPrem, totals.nb_unrealised > 0 ? totals.nb_unrealised + maxPrem : 0);
    const yLoData = -Math.max(maxLoss, totals.nb_unrealised < 0 ? -totals.nb_unrealised + maxLoss : 0);
    const yPad = Math.max(yHiData - yLoData, 100) * 0.08;
    const yLo = yLoData - yPad, yHi = yHiData + yPad;
    const yRange = yHi - yLo;

    const toX = (i: number) => PAD.left + (i / Math.max(1, series.length - 1)) * cw;
    const toY = (v: number) => PAD.top + (1 - (v - yLo) / yRange) * ch;
    const y0 = toY(0);

    // Y grid + labels
    ctx.strokeStyle = 'rgba(0,0,0,0.05)'; ctx.lineWidth = 1;
    ctx.font = '10px system-ui'; ctx.textAlign = 'right'; ctx.fillStyle = 'rgba(60,75,100,0.75)';
    for (let i = 0; i <= 5; i++) {
      const v = yLo + (i / 5) * yRange, y = toY(v);
      ctx.beginPath(); ctx.moveTo(PAD.left, y); ctx.lineTo(PAD.left + cw, y); ctx.stroke();
      ctx.fillText(this.fmtK(v), PAD.left - 8, y + 3);
    }

    // Zero line
    ctx.strokeStyle = 'rgba(0,0,0,0.20)'; ctx.lineWidth = 1.2; ctx.setLineDash([4, 3]);
    ctx.beginPath(); ctx.moveTo(PAD.left, y0); ctx.lineTo(PAD.left + cw, y0); ctx.stroke();
    ctx.setLineDash([]);

    // ── Premium kept (green area, above 0) ────────────────────────────────
    ctx.beginPath(); ctx.moveTo(toX(0), y0);
    series.forEach((d, i) => ctx.lineTo(toX(i), toY(d.premium_kept)));
    ctx.lineTo(toX(series.length - 1), y0); ctx.closePath();
    const gPrem = ctx.createLinearGradient(0, PAD.top, 0, y0);
    gPrem.addColorStop(0, 'rgba(16,185,129,0.42)'); gPrem.addColorStop(1, 'rgba(16,185,129,0.04)');
    ctx.fillStyle = gPrem; ctx.fill();

    // Top edge line
    ctx.beginPath();
    series.forEach((d, i) => i === 0 ? ctx.moveTo(toX(i), toY(d.premium_kept)) : ctx.lineTo(toX(i), toY(d.premium_kept)));
    ctx.strokeStyle = '#10b981'; ctx.lineWidth = 2.2; ctx.stroke();

    // ── Option losses (red area, below 0) ─────────────────────────────────
    if (maxLoss > 0) {
      ctx.beginPath(); ctx.moveTo(toX(0), y0);
      series.forEach((d, i) => ctx.lineTo(toX(i), toY(-d.option_losses)));
      ctx.lineTo(toX(series.length - 1), y0); ctx.closePath();
      const gLoss = ctx.createLinearGradient(0, y0, 0, toY(yLo));
      gLoss.addColorStop(0, 'rgba(239,68,68,0.04)'); gLoss.addColorStop(1, 'rgba(239,68,68,0.42)');
      ctx.fillStyle = gLoss; ctx.fill();
      ctx.beginPath();
      series.forEach((d, i) => i === 0 ? ctx.moveTo(toX(i), toY(-d.option_losses)) : ctx.lineTo(toX(i), toY(-d.option_losses)));
      ctx.strokeStyle = '#ef4444'; ctx.lineWidth = 2; ctx.stroke();
    }

    // ── Net realised line (white with shadow) ─────────────────────────────
    ctx.beginPath();
    series.forEach((d, i) => i === 0 ? ctx.moveTo(toX(i), toY(d.net_realised)) : ctx.lineTo(toX(i), toY(d.net_realised)));
    ctx.strokeStyle = 'rgba(15,23,42,0.85)'; ctx.lineWidth = 2.5; ctx.stroke();

    // ── NB unrealised band on the far right (visualises live MTM) ─────────
    const nbX = PAD.left + cw + 6;
    const nbW = 18;
    if (nbBand > 0) {
      const nbTop    = totals.nb_unrealised > 0 ? toY(totals.net_realised + totals.nb_unrealised) : toY(totals.net_realised);
      const nbBottom = totals.nb_unrealised > 0 ? toY(totals.net_realised) : toY(totals.net_realised + totals.nb_unrealised);
      const nbColor  = totals.nb_unrealised >= 0 ? '#60a5fa' : '#f97316';
      ctx.fillStyle = nbColor + '30';   // 30 = ~19% alpha
      ctx.fillRect(nbX, Math.min(nbTop, nbBottom), nbW, Math.abs(nbBottom - nbTop));
      ctx.strokeStyle = nbColor; ctx.lineWidth = 1.5;
      ctx.strokeRect(nbX, Math.min(nbTop, nbBottom), nbW, Math.abs(nbBottom - nbTop));

      ctx.fillStyle = nbColor; ctx.font = '700 10px system-ui'; ctx.textAlign = 'left';
      ctx.fillText('NB MTM', nbX + nbW + 4, toY(totals.net_realised + totals.nb_unrealised / 2) - 4);
      ctx.fillStyle = nbColor; ctx.font = '700 11px system-ui';
      ctx.fillText((totals.nb_unrealised >= 0 ? '+' : '') + this.fmtK(totals.nb_unrealised), nbX + nbW + 4, toY(totals.net_realised + totals.nb_unrealised / 2) + 9);
    }

    // ── Current totals labels (top-right area) ────────────────────────────
    ctx.font = '600 10px system-ui'; ctx.textAlign = 'right';
    ctx.fillStyle = '#10b981';
    ctx.fillText('Premium kept  +₹' + this.fmtK(totals.premium_kept), PAD.left + cw, PAD.top - 6);

    // X axis labels
    ctx.fillStyle = 'rgba(60,75,100,0.7)'; ctx.font = '10px system-ui'; ctx.textAlign = 'left';
    ctx.fillText(series[0].date, PAD.left, H - 10);
    ctx.textAlign = 'right'; ctx.fillText(series[series.length - 1].date, PAD.left + cw, H - 10);
    ctx.textAlign = 'center'; ctx.fillStyle = 'rgba(60,75,100,0.6)';
    ctx.fillText(
      `${series.length} closed cycle${series.length === 1 ? '' : 's'} · net realised ${(totals.net_realised >= 0 ? '+' : '') + this.fmtK(totals.net_realised)}`,
      PAD.left + cw / 2, H - 10
    );
  }

  // Chart 2: per-cycle outcome bars (each closed cycle = 1 bar, colour-coded
  // by close-type: green=expired worthless, lime=closed at profit, red=loss,
  // amber=rolled). Tells the story of which cycles worked and which didn't.
  drawMonthlyChart() {
    const oc = this.hubSummary()?.cycle_outcomes;
    const bars: any[] = oc?.bars || [];
    const dist: any[] = oc?.distribution || [];
    const canvas = this.monthlyCanvasRef?.nativeElement;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const dpr = window.devicePixelRatio || 1;
    const W = canvas.parentElement?.clientWidth ?? 400;
    const H = 240;
    canvas.width = W * dpr; canvas.height = H * dpr;
    canvas.style.width = W + 'px'; canvas.style.height = H + 'px';
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);

    const PAD = { top: 18, right: 12, bottom: 56, left: 52 };
    const cw = W - PAD.left - PAD.right, ch = H - PAD.top - PAD.bottom;

    if (!bars.length) {
      ctx.fillStyle = 'rgba(120,140,170,0.7)';
      ctx.font = '12px system-ui'; ctx.textAlign = 'center';
      ctx.fillText('No closed cycles yet', W / 2, H / 2);
      return;
    }

    const colourFor = (kind: string) => ({
      expired_worthless: '#10b981',
      closed_at_profit:  '#84cc16',
      closed_at_loss:    '#ef4444',
      rolled:            '#f59e0b',
    }[kind] || '#94a3b8');

    const yMin = Math.min(0, ...bars.map(b => b.pnl));
    const yMax = Math.max(0, ...bars.map(b => b.pnl));
    const yPad = (yMax - yMin) * 0.12 || Math.abs(yMax) * 0.2 || 100;
    const yLo = yMin - yPad, yHi = yMax + yPad;
    const yRange = yHi - yLo;

    const slotW = cw / bars.length;
    const barW  = Math.max(4, Math.min(28, slotW - 3));
    const toY   = (v: number) => PAD.top + (1 - (v - yLo) / yRange) * ch;
    const y0    = toY(0);

    // Y grid
    ctx.strokeStyle = 'rgba(0,0,0,0.05)'; ctx.lineWidth = 1;
    ctx.font = '10px system-ui'; ctx.textAlign = 'right'; ctx.fillStyle = 'rgba(60,75,100,0.7)';
    for (let i = 0; i <= 3; i++) {
      const v = yLo + (i / 3) * yRange, y = toY(v);
      ctx.beginPath(); ctx.moveTo(PAD.left, y); ctx.lineTo(PAD.left + cw, y); ctx.stroke();
      ctx.fillText(this.fmtK(v), PAD.left - 6, y + 3);
    }
    ctx.strokeStyle = 'rgba(0,0,0,0.18)'; ctx.lineWidth = 1; ctx.setLineDash([3, 3]);
    ctx.beginPath(); ctx.moveTo(PAD.left, y0); ctx.lineTo(PAD.left + cw, y0); ctx.stroke();
    ctx.setLineDash([]);

    // Bars
    bars.forEach((b, i) => {
      const cx = PAD.left + i * slotW + slotW / 2;
      const bx = cx - barW / 2;
      const by = toY(b.pnl);
      const bh = Math.abs(by - y0);
      const col = colourFor(b.kind);
      const grad = ctx.createLinearGradient(0, by, 0, y0);
      if (b.pnl >= 0) {
        grad.addColorStop(0, col); grad.addColorStop(1, col + '88');
      } else {
        grad.addColorStop(0, col + '88'); grad.addColorStop(1, col);
      }
      ctx.fillStyle = grad;
      ctx.fillRect(bx, Math.min(by, y0), barW, bh);
    });

    // ── Distribution legend (bottom) ──────────────────────────────────────
    const legendY = H - 32;
    let lx = PAD.left;
    ctx.font = '10px system-ui'; ctx.textBaseline = 'middle';
    const labelMap: any = {
      expired_worthless: 'Expired worthless',
      closed_at_profit:  'Closed at profit',
      closed_at_loss:    'Closed at loss',
      rolled:            'Rolled',
    };
    dist.forEach((d) => {
      const col = colourFor(d.kind);
      ctx.fillStyle = col;
      ctx.fillRect(lx, legendY, 9, 9);
      ctx.fillStyle = 'rgba(60,75,100,0.85)'; ctx.textAlign = 'left';
      const txt = `${labelMap[d.kind] || d.kind}: ${d.count} (${d.pct}%)`;
      ctx.fillText(txt, lx + 13, legendY + 5);
      lx += ctx.measureText(txt).width + 26;
    });
    ctx.textBaseline = 'alphabetic';

    // Bottom caption
    ctx.fillStyle = 'rgba(60,75,100,0.6)'; ctx.font = '10px system-ui'; ctx.textAlign = 'center';
    ctx.fillText('Per-cycle P&L · colour shows how each cycle closed', W / 2, H - 6);
  }

  togglePremktExpanded() {
    const willOpen = !this.premktExpanded();
    this.premktExpanded.set(willOpen);
    if (willOpen && !this.premarket()) this.refreshPremarket();
  }

  toggleBestStrikeDetails() {
    this.bestStrikeDetailsOpen.update(v => !v);
  }

  toggleStrikePicker() { this.strikePickerOpen.update(v => !v); }

  // Rough SPAN+ELM margin for the short Nifty CE leg.
  // Indian brokers hold ~13% of strike notional per lot for a short OTM call;
  // this varies with VIX/Δ but 13% is a solid working estimate for Nifty.
  // (When NiftyBees is pledged as collateral the broker offsets this margin —
  // we surface that in the tooltip rather than netting it client-side.)
  shortCallMargin = computed(() => {
    const a: any = this.analysis();
    if (!a) return 0;
    return Math.round(0.13 * Number(a.strike) * Number(a.lot_size) * Number(a.lots));
  });

  // Granularity for the compact scenarios table — coarse by default; user
  // can switch to fine (1% steps) when they want a closer look.
  scenarioGranularity = signal<'coarse' | 'fine'>('coarse');
  setScenarioGranularity(g: 'coarse' | 'fine') { this.scenarioGranularity.set(g); }

  // Whether to break out the Total P&L into its NiftyBees + Call legs.
  scenarioBreakdown = signal(false);
  toggleScenarioBreakdown() { this.scenarioBreakdown.update(v => !v); }

  // Compact hedge-aware scenario list for the New Position view.
  // Interpolates total P&L from the dense `payoff_curve` so we can show fine
  // moves (±1, ±2) that the backend's coarse `scenarios[]` array doesn't have.
  compactScenarios = computed(() => {
    const a: any = this.analysis();
    if (!a?.scenarios?.length) return [];

    const finePcts   = [-10, -7, -5, -3, -2, -1, 0, 1, 2, 3, 5, 7, 10];
    const coarsePcts = [-10, -5, -3, 0, 3, 5, 10];
    const wanted = this.scenarioGranularity() === 'fine' ? finePcts : coarsePcts;

    const spot: number = a.spot;
    const curve: { nifty: number; pnl: number }[] = a.payoff_curve ?? [];
    const strike: number = a.strike;

    // Per-Nifty-target breakdown components (mirrors backend _pnl()).
    const shares          = Number(a.shares ?? 0);
    const niftybeesEntry  = Number(a.niftybees_entry ?? 0);
    const lotsN           = Number(a.lots ?? 0);
    const lotSize         = Number(a.lot_size ?? 0);
    const premium         = Number(a.premium ?? 0);
    const NB_RATIO        = 100;   // 1 NiftyBees share ≈ Nifty / 100
    const nbPnlAt   = (st: number) => shares * (st / NB_RATIO - niftybeesEntry);
    const callPnlAt = (st: number) => lotsN * lotSize * (premium - Math.max(st - strike, 0));

    // Interpolate P&L for any nifty target from the dense payoff_curve.
    const interp = (target: number): number => {
      if (!curve.length) return 0;
      if (target <= curve[0].nifty) return curve[0].pnl;
      if (target >= curve[curve.length - 1].nifty) return curve[curve.length - 1].pnl;
      let lo = 0, hi = curve.length - 1;
      while (hi - lo > 1) {
        const mid = (lo + hi) >> 1;
        if (curve[mid].nifty <= target) lo = mid; else hi = mid;
      }
      const t = (target - curve[lo].nifty) / (curve[hi].nifty - curve[lo].nifty || 1);
      return curve[lo].pnl + t * (curve[hi].pnl - curve[lo].pnl);
    };

    const hedgeOn = this.hedgeOverlayMode() === 'overlay';
    const h = hedgeOn ? this.hedgeOverlayHedge() : null;
    const qty       = h ? Number(h.lots ?? 1) * Number(h.lot_size ?? 75) : 0;
    const hStrike   = h ? Number(h.strike) : 0;
    const hPremium  = h ? Number(h.premium_paid ?? 0) : 0;

    return wanted.map(pct => {
      const niftyAt = Math.round(spot * (1 + pct / 100));
      // Prefer the exact scenario row if backend already has it (keeps the
      // niftybees / call breakdown numbers consistent). Otherwise compute.
      const exact = a.scenarios.find((s: any) => s.pct === pct);
      const nbPnl   = exact ? exact.niftybees_pnl : nbPnlAt(niftyAt);
      const callPnl = exact ? exact.call_pnl      : callPnlAt(niftyAt);
      const totalPnl = exact ? exact.total_pnl    : (nbPnl + callPnl);
      // Sanity-check against the dense payoff curve when we computed it.
      const totalSafe = exact ? totalPnl : interp(niftyAt);
      const isCapped = niftyAt > strike;
      const isFlat   = pct === 0;
      const hedgePnl = h && hStrike ? qty * (Math.max(hStrike - niftyAt, 0) - hPremium) : 0;

      return {
        pct,
        label:         (pct > 0 ? '+' : '') + pct + '%',
        nifty:         niftyAt,
        niftybees_pnl: nbPnl,
        call_pnl:      callPnl,
        total_pnl:     exact ? totalPnl : totalSafe,
        hedge_pnl:     hedgePnl,
        combined_pnl:  (exact ? totalPnl : totalSafe) + hedgePnl,
        is_loss:       (exact ? totalPnl : totalSafe) < 0,
        is_capped:     isCapped,
        is_at_entry:   isFlat,
        hedge_active:  !!(h && hStrike),
      };
    });
  });

  setPremktMode(m: 'best' | 'expiry') {
    if (this.premktMode() === m) return;
    this.premktMode.set(m);
    this.bestStrikeOpenSections.set(new Set());
    // Always ensure premarket data is loaded — it provides the 3 market
    // checks (VIX / ATM IV / PCR) that are shown in BOTH modes.
    if (!this.premarket() && !this.premktLoading()) this.refreshPremarket();
    if (m === 'best' && !this.bestTrade() && !this.bestTradeLoading()) {
      this.refreshBestTrade();
    }
  }

  toggleBestStrikeSection(section: 'details' | 'metrics' | 'why' | 'tradeoffs' | 'bts') {
    this.bestStrikeOpenSections.update(s => {
      const next = new Set(s);
      if (next.has(section)) next.delete(section); else next.add(section);
      return next;
    });
  }

  isBestStrikeSectionOpen(section: 'details' | 'metrics' | 'why' | 'tradeoffs' | 'bts'): boolean {
    return this.bestStrikeOpenSections().has(section);
  }

  openNewPosition() {
    this.selectedPos.set(null);
    this.analysis.set(null);
    this.setup.set(null);
    this.selectedStrike.set(0);
    this.lots.set(1);
    this.targetDaysFromNow.set(0);
    this.targetNifty.set(0);
    this.currentView.set('new');
    this.loadSetup();
    // Always load premarket (for market checks shown in both modes) +
    // also load best-trade scan if best mode is active (default).
    if (!this.premarket()) this.refreshPremarket();
    if (this.premktMode() === 'best' && !this.bestTrade() && !this.bestTradeLoading()) {
      this.refreshBestTrade();
    }
  }

  openPosition(pos: CCPosition) {
    this.selectedPos.set(pos);
    this.analysis.set(null);
    this.setup.set(null);
    this.exitStatus.set(null);
    this.error.set('');
    this.lots.set(pos.lots);
    this.targetDaysFromNow.set(0);
    this.targetNifty.set(0);
    if (pos.active_call) {
      this.selectedExpiry.set(pos.active_call.expiry);
      this.selectedStrike.set(pos.active_call.strike);
    } else {
      // No active call — let _fetchSetup pick the current ATM strike automatically
      this.selectedExpiry.set('');
      this.selectedStrike.set(0);
    }
    this.currentView.set('detail');
    this.loadSetup();
    this.loadExitStatus();
  }

  backToHub() {
    this.currentView.set('hub');
    this.loadHub();
  }

  useRankedStrike(strike: number, expiry: string) {
    // Collapse the premarket scanner so the builder is visible without scrolling.
    this.premktExpanded.set(false);

    if (this.currentView() !== 'new') {
      this.openNewPosition();
      // Wait for setup to load, then select chosen strike + expiry
      const tid = setInterval(() => {
        if (this.setup()) {
          clearInterval(tid);
          if (expiry && this.selectedExpiry() !== expiry) {
            this.selectedExpiry.set(expiry);
            this.selectedStrike.set(strike);
            this.loadSetup();
          } else {
            this.selectStrike(strike);
          }
        }
      }, 300);
      return;
    }

    // Already in 'new' view — sync expiry/strike in place, no reset.
    if (expiry && this.selectedExpiry() !== expiry) {
      this.selectedExpiry.set(expiry);
      this.selectedStrike.set(strike);
      this.loadSetup();
    } else {
      this.selectStrike(strike);
    }
  }

  toggleRankExpand(idx: number) {
    this.expandedRankIdx.update(v => v === idx ? null : idx);
  }

  // ── Exit-strategy engine ─────────────────────────────────────────────────
  loadExitStatus() {
    const pos = this.selectedPos();
    if (!pos || !pos.active_call) { this.exitStatus.set(null); return; }
    this.exitLoading.set(true);
    this.api.getExitStatus(pos.id).subscribe({
      next: (s) => {
        this.exitStatus.set(s);
        this.exitLoading.set(false);
        this.exitLastUpdated.set(new Date().toLocaleTimeString('en-IN', {
          hour: '2-digit', minute: '2-digit', second: '2-digit',
        }));
      },
      error: () => { this.exitStatus.set(null); this.exitLoading.set(false); },
    });
  }

  rollUpNow() {
    const pos = this.selectedPos();
    const es  = this.exitStatus();
    if (!pos || !es?.active_call || !es?.suggested_roll || this.rollingUp()) return;
    if (!confirm(
      `Roll up?\n\n` +
      `1. Buy back ${es.active_call.strike} CE at ₹${es.active_call.current_price.toFixed(2)} ` +
      `(cost ₹${this.fmtK(es.active_call.buyback_cost_total)})\n` +
      `2. Sell new ${es.suggested_roll.new_strike} CE at ₹${es.suggested_roll.estimated_premium.toFixed(2)} ` +
      `(income ₹${this.fmtK(es.suggested_roll.estimated_premium_total)})\n\n` +
      `Net cash flow: ${this.fmtRs(es.suggested_roll.net_cash_flow)}\n` +
      `Realised loss on old call: ${this.fmtRs(es.active_call.realized_if_close)}`
    )) return;
    this.rollingUp.set(true);
    this.api.rollUpCoveredCall(pos.id, {
      close_price:  es.active_call.current_price,
      new_strike:   es.suggested_roll.new_strike,
      new_expiry:   es.suggested_roll.expiry,
      new_premium:  es.suggested_roll.estimated_premium,
    }).subscribe({
      next: (updatedPos) => {
        this.selectedPos.set(updatedPos);
        this.rollingUp.set(false);
        this.loadExitStatus();
        this.loadPositions();
      },
      error: () => this.rollingUp.set(false),
    });
  }

  closeCallOnly() {
    const pos = this.selectedPos();
    const es  = this.exitStatus();
    if (!pos || !es?.active_call) return;
    if (!confirm(
      `Close call only (no roll-up)?\n\n` +
      `Buy back ${es.active_call.strike} CE at ₹${es.active_call.current_price.toFixed(2)}\n` +
      `Realised P&L: ${this.fmtRs(es.active_call.realized_if_close)}\n\n` +
      `NiftyBees will go uncapped — full upside from here, no premium income until you sell a new call.`
    )) return;
    this.api.closeCoveredCallCycle(pos.id, es.active_call.current_price).subscribe({
      next: (updatedPos) => {
        this.selectedPos.set(updatedPos);
        this.loadExitStatus();
        this.loadPositions();
      },
    });
  }

  // Stop-1 take-profit roll: close current call (capturing the gain) and
  // immediately sell a fresh ~1% OTM call to restart the income cycle.
  takeProfitNow() {
    const pos = this.selectedPos();
    const es  = this.exitStatus();
    if (!pos || !es?.active_call || !es?.suggested_takeprofit || this.rollingUp()) return;
    const tp = es.suggested_takeprofit;
    if (!confirm(
      `Take profit + redeploy?\n\n` +
      `1. Buy back ${es.active_call.strike} CE at ₹${es.active_call.current_price.toFixed(2)} ` +
      `(cost ₹${this.fmtK(es.active_call.buyback_cost_total)})\n` +
      `   Realised profit: ${this.fmtRs(es.active_call.realized_if_close)}\n\n` +
      `2. Sell new ${tp.new_strike} CE at ₹${tp.estimated_premium.toFixed(2)} ` +
      `(income ₹${this.fmtK(tp.estimated_premium_total)})\n\n` +
      `Net cash flow: ${this.fmtRs(tp.net_cash_flow)}`
    )) return;
    this.rollingUp.set(true);
    this.api.rollUpCoveredCall(pos.id, {
      close_price:  es.active_call.current_price,
      new_strike:   tp.new_strike,
      new_expiry:   tp.expiry,
      new_premium:  tp.estimated_premium,
    }).subscribe({
      next: (updatedPos) => {
        this.selectedPos.set(updatedPos);
        this.rollingUp.set(false);
        this.loadExitStatus();
        this.loadPositions();
      },
      error: () => this.rollingUp.set(false),
    });
  }

  toggleShowAllStrikes() { this.showAllStrikes.update(v => !v); }

  refreshPremarket() {
    // Silent background refresh if we already have data — keep it on screen.
    // Show full-screen "Scanning…" only on the very first load.
    const hasData = !!this.premarket();
    if (hasData) this.premktRefreshing.set(true);
    else         this.premktLoading.set(true);

    this.expandedRankIdx.set(null);
    this.showAllStrikes.set(false);
    const params: any = { cadence: this.cadence() };
    if (this.premktExpiryFilter()) params.expiry = this.premktExpiryFilter();
    this.api.getCoveredCallPremarket(params).subscribe({
      next: (d)  => {
        this.premarket.set(d);
        this.premktLoading.set(false);
        this.premktRefreshing.set(false);
      },
      error: ()  => {
        if (!hasData) this.premarket.set(null);
        this.premktLoading.set(false);
        this.premktRefreshing.set(false);
      },
    });
  }

  // Switch strategy cadence. Re-fetches whichever scan is currently active
  // so the user immediately sees the new top picks. Also clears the
  // expiry filter (the previous expiry may be outside the new DTE band).
  setCadence(c: 'monthly' | 'quarterly') {
    if (this.cadence() === c) return;
    this.cadence.set(c);
    this.premktExpiryFilter.set('');
    this.bestStrikeOpenSections.set(new Set());
    if (this.premktMode() === 'best') {
      this.bestTrade.set(null);
      this.refreshBestTrade();
    } else {
      this.premarket.set(null);
      this.refreshPremarket();
    }
  }

  // ── Best Trade (cross-expiry) ─────────────────────────────────────────────
  openBestTrade() {
    this.bestTradeOpen.set(true);
    this.bestTradeAltIdx.set(null);
    if (!this.bestTrade()) this.refreshBestTrade();
  }
  closeBestTrade() { this.bestTradeOpen.set(false); }
  toggleBestTradeAlt(i: number) {
    this.bestTradeAltIdx.update(v => v === i ? null : i);
  }
  toggleBestTradeRationale() {
    this.bestTradeRationaleOpen.update(v => !v);
  }
  loadBestTradeChecks(expiry: string) {
    this.api.getCoveredCallPremarket({ expiry }).subscribe({
      next: (d) => this.bestTradeChecks.set(d),
      error: ()  => this.bestTradeChecks.set(null),
    });
  }

  refreshBestTrade() {
    this.bestTradeLoading.set(true);
    this.bestTradeError.set('');
    this.bestTradeAltIdx.set(null);
    this.api.getCoveredCallBestTrade(4, 4, this.cadence()).subscribe({
      next: (d) => {
        this.bestTrade.set(d);
        this.bestTradeLoading.set(false);
        // Fetch ATM IV / PCR / VIX for the chosen pick's expiry so the
        // checks strip reflects THIS trade's expiry, not the nearest one.
        const top = d?.candidates?.[0];
        if (top?.expiry) this.loadBestTradeChecks(top.expiry);
      },
      error: (e) => {
        this.bestTradeError.set(e?.error?.detail || 'Failed to scan trades. Is Kite connected?');
        this.bestTradeLoading.set(false);
      },
    });
  }
  useBestCandidate(c: BestTradeCandidate) {
    this.closeBestTrade();
    this.useRankedStrike(c.strike, c.expiry);
  }

  // ── Save dialog ────────────────────────────────────────────────────────────
  openSaveDialog() { this.saveName.set(''); this.saveDialogOpen.set(true); }
  cancelSave()     { this.saveDialogOpen.set(false); }

  openEntryPriceEditor() {
    const pos = this.selectedPos();
    if (!pos) return;
    // Prefer the stored entry_nifty; fall back to niftybees_entry_price × 100 approximation
    const current = pos.entry_nifty
      ? pos.entry_nifty.toFixed(0)
      : pos.niftybees_entry_price ? (pos.niftybees_entry_price * 100).toFixed(0) : '';
    this.entryNiftyInput.set(current);
    this.entryPriceSavedMsg.set('');
    this.editingEntryPrice.set(true);
  }
  cancelEntryPriceEditor() { this.editingEntryPrice.set(false); }

  saveEntryPrice() {
    const pos = this.selectedPos();
    if (!pos) return;
    const niftyLevel = parseFloat(this.entryNiftyInput());
    if (!niftyLevel || niftyLevel <= 0) return;
    const nbPrice = parseFloat((niftyLevel / 100).toFixed(2));
    this.savingEntryPrice.set(true);
    this.api.updateCoveredCallPosition(pos.id, {
      entry_nifty: niftyLevel,
      niftybees_entry_price: nbPrice,
    }).subscribe({
      next: (_) => {
        this.savingEntryPrice.set(false);
        this.editingEntryPrice.set(false);
        this.entryPriceSavedMsg.set(`Entry updated to Nifty ${niftyLevel}`);
        this.selectedPos.set({ ...pos, entry_nifty: niftyLevel, niftybees_entry_price: nbPrice });
        this.analysis.set(null);
        this.loadSetup();
        setTimeout(() => this.entryPriceSavedMsg.set(''), 4000);
      },
      error: () => {
        this.savingEntryPrice.set(false);
        this.entryPriceSavedMsg.set('Save failed — check backend connection');
      },
    });
  }

  confirmSavePosition() {
    const a = this.analysis();
    if (!a) return;
    this.savingPosition.set(true);
    const name = this.saveName() || `Nifty CC — ${a.expiry}`;
    this.api.createCoveredCallPosition({
      name,
      underlying:             'NIFTY',
      shares:                 a.shares,
      niftybees_entry_price:  a.niftybees_price,
      entry_nifty:            a.spot,
      niftybees_cost:         a.niftybees_cost,
      lots:                   a.lots,
      lot_size:               a.lot_size,
      active_call: {
        strike:           a.strike,
        expiry:           a.expiry,
        lots:             a.lots,
        lot_size:         a.lot_size,
        premium_received: a.premium,
        premium_total:    a.premium_total,
        cadence:          this.cadence(),
        entry_iv:         a.iv,
      },
      notes: '',
    }).subscribe({
      next: () => {
        this.savingPosition.set(false);
        this.saveDialogOpen.set(false);
        this.backToHub();
      },
      error: () => this.savingPosition.set(false),
    });
  }

  // ── Close call dialog (rich) ───────────────────────────────────────────────
  openCloseCall(pid: string, e: Event) {
    e.stopPropagation();
    this._openCloseCallDialog(pid);
  }

  // Called from the hub action queue card — same dialog, position context auto-fetched.
  openCloseCallRich(pid: string, e: Event) {
    e.stopPropagation();
    this._openCloseCallDialog(pid);
  }

  private _openCloseCallDialog(pid: string) {
    const positions = this.hubSummary()?.by_stop;
    let pos: any = null;
    if (positions) {
      for (const k of ['stop1', 'stop2', 'stop3', 'stop4'] as const) {
        const found = positions[k]?.find((p: any) => p.id === pid);
        if (found) { pos = found; break; }
      }
    }
    if (!pos) {
      pos = this.positions().find(p => p.id === pid)
         || this.selectedPos();
    }
    this.closeCallPid.set(pid);
    this.closeCallContext.set(pos);
    // Smart defaults from live exit-status if available
    const ac = pos?.active_call_live || pos?.active_call;
    if (ac) {
      this.closeCallPrice.set(String(ac.current_price ?? ac.premium_received ?? ''));
      // Default close kind: take-profit if call < entry, defensive if call > entry
      const cur = Number(ac.current_price ?? 0);
      const ent = Number(ac.premium_received ?? 0);
      if (cur <= 0.05) this.closeCallKind.set('expired_worthless');
      else if (cur < ent) this.closeCallKind.set('closed_at_profit');
      else this.closeCallKind.set('closed_at_loss');
    } else {
      this.closeCallPrice.set('');
      this.closeCallKind.set('closed_at_profit');
    }
    this.closeNbAction.set('held_all');
    this.closeNbShares.set('');
    this.closeNbSellPrice.set('');
    this.closeNotes.set('');
    this.closeCallOpen.set(true);
  }

  cancelCloseCall() { this.closeCallOpen.set(false); }

  submitCloseCall() {
    const price = parseFloat(this.closeCallPrice());
    if (isNaN(price) || price < 0) return;
    const nbAct = this.closeNbAction();
    const nbShares = nbAct === 'held_all' ? 0 : (parseInt(this.closeNbShares(), 10) || 0);
    const nbSell   = nbAct === 'held_all' ? 0 : (parseFloat(this.closeNbSellPrice()) || 0);
    if (nbAct !== 'held_all' && (nbShares <= 0 || nbSell <= 0)) {
      alert('Enter the number of NB shares sold and the sell price.');
      return;
    }
    this.closeCallLoading.set(true);
    this.api.closeCoveredCallCycle(this.closeCallPid(), price, {
      close_kind:     this.closeCallKind(),
      nb_action:      nbAct,
      nb_shares_sold: nbShares,
      nb_sell_price:  nbSell,
      notes:          this.closeNotes(),
    }).subscribe({
      next: () => {
        this.closeCallLoading.set(false);
        this.closeCallOpen.set(false);
        this.loadHub();
      },
      error: () => this.closeCallLoading.set(false),
    });
  }

  // P&L preview values for the close dialog
  closePreviewOptionPnl(): number {
    const ctx   = this.closeCallContext();
    if (!ctx) return 0;
    const ac    = ctx.active_call_live || ctx.active_call;
    if (!ac)    return 0;
    const price = parseFloat(this.closeCallPrice());
    if (isNaN(price)) return 0;
    const qty   = (ac.lots || 1) * (ac.lot_size || 75);
    return (ac.premium_received - price) * qty;
  }

  closePreviewNbPnl(): number {
    const ctx   = this.closeCallContext();
    if (!ctx) return 0;
    const nbAct = this.closeNbAction();
    if (nbAct === 'held_all') return 0;
    const shares = parseInt(this.closeNbShares(), 10) || 0;
    const price  = parseFloat(this.closeNbSellPrice()) || 0;
    if (!shares || !price) return 0;
    return shares * (price - (ctx.niftybees_entry_price || 0));
  }

  closePreviewTotal(): number {
    return this.closePreviewOptionPnl() + this.closePreviewNbPnl();
  }

  deletePosition(pid: string, e: Event) {
    e.stopPropagation();
    if (!confirm('Delete this position? This cannot be undone.')) return;
    this.api.deleteCoveredCallPosition(pid).subscribe({ next: () => this.loadHub() });
  }

  // ── Analysis data loading ──────────────────────────────────────────────────
  loadSetup() {
    this.loading.set(true);
    this.error.set('');
    this._fetchSetup(true);
  }

  manualRefresh() {
    if (this.refreshing()) return;
    this.refreshing.set(true);
    this._fetchSetup(false);
  }

  private _fetchSetup(initial: boolean) {
    this.api.getCoveredCallSetup('NIFTY', this.selectedExpiry()).subscribe({
      next: (data: SetupData) => {
        this._setNiftySpot(data.spot);
        this.setup.set(data);
        if (initial) {
          if (!this.selectedExpiry()) this.selectedExpiry.set(data.expiry);
          if (!this.selectedStrike()) {
            const atm = data.otm_calls.find(c => c.is_atm) ?? data.otm_calls[0];
            if (atm) this.selectedStrike.set(atm.strike);
          }
        }
        this.lastUpdatedStr.set(new Date().toLocaleTimeString('en-IN', {
          hour: '2-digit', minute: '2-digit', second: '2-digit',
        }));
        this.loading.set(false);
        this.refreshing.set(false);
        this.analyze();
      },
      error: () => {
        this.error.set('Failed to load chain data. Is the backend running?');
        this.loading.set(false);
        this.refreshing.set(false);
      },
    });
  }

  onExpiryChange(exp: string) {
    this.selectedExpiry.set(exp);
    this.analysis.set(null);
    this.loadSetup();
  }

  selectStrike(strike: number) {
    this.selectedStrike.set(strike);
    this.analyze();
  }

  analyze() {
    const s = this.setup();
    if (!s || !this.selectedStrike()) return;
    this.analyzing.set(true);

    const pos = this.currentView() === 'detail' ? this.selectedPos() : null;
    const ac  = pos?.active_call;

    this.api.analyzeCoveredCall({
      underlying:            'NIFTY',
      expiry:                this.selectedExpiry() || s.expiry,
      strike:                this.selectedStrike(),
      lots:                  this.lots(),
      custom_shares:         pos?.shares ?? 0,
      entry_premium:         ac?.premium_received ?? 0,
      entry_niftybees_price: pos?.niftybees_entry_price ?? 0,
      entry_nifty:           pos?.entry_nifty ?? 0,
    }).subscribe({
      next: (data: Analysis) => { this.analysis.set(data); this.analyzing.set(false); },
      error: (e: any) => {
        this.analyzing.set(false);
        this.error.set('Could not load payoff chart: ' + (e?.error?.detail || e?.message || 'check backend connection'));
      },
    });
  }

  fmtExpiry(expiry: string): string {
    if (!expiry) return '';
    const d = new Date(expiry + 'T00:00:00');
    return d.toLocaleDateString('en-IN', { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' });
  }

  setChartMode(mode: 'combined' | 'breakdown') { this.chartMode.set(mode); }

  // Single-control hedge picker. Empty value = overlay off; any hedge id =
  // overlay on with that hedge selected.
  onHedgePick(id: string) {
    if (!id) {
      this.hedgeOverlayMode.set('off');
      this.hedgeOverlayId.set('');
    } else {
      this.hedgeOverlayId.set(id);
      this.hedgeOverlayMode.set('overlay');
    }
  }

  // ── AI Chat ────────────────────────────────────────────────────────────────
  toggleChat() { this.chatOpen.set(!this.chatOpen()); }

  sendChat() {
    const q = this.chatInput().trim();
    if (!q || this.chatLoading()) return;
    const history = this.chatMessages().map(m => ({ role: m.role, content: m.content }));
    this.chatMessages.update(msgs => [...msgs, { role: 'user', content: q }]);
    this.chatInput.set('');
    this.chatLoading.set(true);
    const a = this.analysis();
    const position = a ? {
      spot: a.spot, strike: a.strike, premium: a.premium,
      premium_total: a.premium_total, dte: a.dte, iv: a.iv,
      shares: a.shares, niftybees_price: a.niftybees_price,
      niftybees_cost: a.niftybees_cost, lots: a.lots, lot_size: a.lot_size,
      breakeven: a.breakeven, max_profit: a.max_profit,
      coverage_ratio: a.coverage_ratio,
    } : null;
    this.api.chatWithAI(q, position, history).subscribe({
      next: (res) => {
        this.chatMessages.update(msgs => [...msgs, { role: 'assistant', content: res.answer }]);
        this.chatLoading.set(false);
      },
      error: () => {
        this.chatMessages.update(msgs => [...msgs,
          { role: 'assistant', content: 'Could not reach the AI backend. Is the server running and Ollama active?' },
        ]);
        this.chatLoading.set(false);
      },
    });
  }

  onChatKey(e: KeyboardEvent) {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); this.sendChat(); }
  }

  // ── Black-Scholes helpers ──────────────────────────────────────────────────
  private normCDF(x: number): number {
    const a1=0.254829592, a2=-0.284496736, a3=1.421413741, a4=-1.453152027, a5=1.061405429, p=0.3275911;
    const sign = x < 0 ? -1 : 1;
    const ax = Math.abs(x) / Math.sqrt(2);
    const t  = 1 / (1 + p * ax);
    const y  = 1 - (((((a5*t + a4)*t) + a3)*t + a2)*t + a1) * t * Math.exp(-ax * ax);
    return 0.5 * (1 + sign * y);
  }

  private bsCall(S: number, K: number, T: number, iv: number): number {
    if (T <= 0 || iv <= 0) return Math.max(S - K, 0);
    const r = 0.065, sqrtT = Math.sqrt(T);
    const d1 = (Math.log(S / K) + (r + 0.5 * iv * iv) * T) / (iv * sqrtT);
    const d2 = d1 - iv * sqrtT;
    return S * this.normCDF(d1) - K * Math.exp(-r * T) * this.normCDF(d2);
  }

  private bsPut(S: number, K: number, T: number, iv: number): number {
    if (T <= 0 || iv <= 0) return Math.max(K - S, 0);
    const r = 0.065, sqrtT = Math.sqrt(T);
    const d1 = (Math.log(S / K) + (r + 0.5 * iv * iv) * T) / (iv * sqrtT);
    const d2 = d1 - iv * sqrtT;
    return K * Math.exp(-r * T) * this.normCDF(-d2) - S * this.normCDF(-d1);
  }

  private pnlAtDate(spotVal: number, a: Analysis, daysLeft: number): number {
    const iv      = (a.iv ?? 16) / 100;
    const callVal = this.bsCall(spotVal, a.strike, daysLeft / 365, iv);
    const nbPnl   = a.shares * (spotVal / 100 - a.spot / 100);
    return nbPnl + a.lots * a.lot_size * (a.premium - callVal);
  }

  private pnlComponentsAtDate(spotVal: number, a: Analysis, daysLeft: number): { nbPnl: number; callPnl: number; total: number } {
    const iv      = (a.iv ?? 16) / 100;
    const callVal = this.bsCall(spotVal, a.strike, daysLeft / 365, iv);
    const nbPnl   = a.shares * (spotVal / 100 - a.spot / 100);
    const callPnl = a.lots * a.lot_size * (a.premium - callVal);
    return { nbPnl, callPnl, total: nbPnl + callPnl };
  }

  // ── Canvas payoff chart ────────────────────────────────────────────────────
  drawChart(a: Analysis) {
    const canvas  = this.canvasRef?.nativeElement;
    const overlay = this.overlayRef?.nativeElement;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const isBreakdown = this.chartMode() === 'breakdown';
    const nbEntry   = a.spot / 100;
    const nbCurve   = a.payoff_curve.map(p => ({ nifty: p.nifty, pnl: a.shares * (p.nifty / 100 - nbEntry) }));
    const callCurve = a.payoff_curve.map(p => ({ nifty: p.nifty, pnl: a.lots * a.lot_size * (a.premium - Math.max(p.nifty - a.strike, 0)) }));
    const targetDFN   = this.targetDaysFromNow();
    const targetDLeft = Math.max(a.dte - targetDFN, 0);
    const targetCurve = a.payoff_curve.map(p => ({ nifty: p.nifty, pnl: this.pnlAtDate(p.nifty, a, targetDLeft) }));
    const tNifty = this.targetNifty();

    const dpr = window.devicePixelRatio || 1;
    const W   = canvas.parentElement?.clientWidth ?? 700;
    // Aspect ratio: ~16:9 of width, clamped so it never feels squat on wide
    // screens or cramped on narrow ones.
    const H   = Math.max(360, Math.min(520, Math.round(W * 0.56)));

    for (const c of [canvas, overlay].filter(Boolean) as HTMLCanvasElement[]) {
      c.width = W * dpr; c.height = H * dpr;
      c.style.width = W + 'px'; c.style.height = H + 'px';
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);

    const PAD = { top: 76, right: 64, bottom: 56, left: 88 };  // top extra: up to 4 marker-label lanes (14px each)
    const cw  = W - PAD.left - PAD.right;
    const ch  = H - PAD.top  - PAD.bottom;

    // Track Y positions already occupied by right-edge labels so we can nudge
    // overlapping ones up/down. Reset every draw.
    const takenRightY: number[] = [];
    const placeRight = (y: number, color: string, text: string, font?: string) => {
      if (y < PAD.top + 6 || y > PAD.top + ch - 6) return;
      // Use a generous gap so the 10–11px text + descender never touches the
      // next label. Smaller gaps looked tight at certain zoom levels.
      const MIN_GAP = 16;
      let attempt = y;
      let step = MIN_GAP;
      const minY = PAD.top + 6;
      const maxY = PAD.top + ch - 6;
      // Greedy outward search: try y, then y±gap, ±2gap, … until clear.
      // 20 iterations covers ±10 gaps (160px) which is enough for any chart
      // height we render at.
      for (let i = 0; i < 20; i++) {
        const collides = takenRightY.some(ty => Math.abs(ty - attempt) < MIN_GAP);
        const inBounds = attempt >= minY && attempt <= maxY;
        if (!collides && inBounds) break;
        attempt = y + (i % 2 === 0 ? step : -step);
        if (i % 2 === 1) step += MIN_GAP;
        if (attempt < minY) attempt = minY;
        if (attempt > maxY) attempt = maxY;
      }
      takenRightY.push(attempt);
      ctx.fillStyle = color;
      ctx.font = font || '600 10px system-ui, sans-serif';
      ctx.textAlign = 'left';
      ctx.fillText(text, PAD.left + cw + 5, attempt + 4);
    };
    const curve  = a.payoff_curve;
    // Zoom: 'in' narrows the x-window to ±7% around current spot so close-to-the-
    // money behaviour (cap, break-even) is easier to read; 'out' uses the full
    // payoff range from the API so the long-tail loss is visible.
    let xMin: number, xMax: number;
    if (this.chartZoom() === 'in') {
      const w = a.spot * 0.07;
      xMin = Math.max(curve[0].nifty,                a.spot - w);
      xMax = Math.min(curve[curve.length - 1].nifty, a.spot + w);
    } else {
      xMin = curve[0].nifty;
      xMax = curve[curve.length - 1].nifty;
    }
    const xRange = xMax - xMin;

    // Y-axis: 'in' tightens around max_profit (the actionable region);
    // 'out' uses the full data extremes so the deep-loss tail is visible
    // (the long picture).
    let yMin: number, yMax: number;
    if (this.chartZoom() === 'in') {
      const maxP = Math.max(a.max_profit, 1);
      yMax = maxP * 1.25;
      yMin = -maxP * 2.0;
    } else {
      let allPnls = isBreakdown
        ? [...curve.map(p => p.pnl), ...nbCurve.map(p => p.pnl), ...callCurve.map(p => p.pnl)]
        : curve.map(p => p.pnl);
      allPnls = [...allPnls, ...targetCurve.map(p => p.pnl)];
      const yMinRaw = Math.min(...allPnls);
      const yMaxRaw = Math.max(...allPnls);
      const yPad    = (yMaxRaw - yMinRaw) * 0.10 || Math.abs(yMaxRaw) * 0.15 || 1000;
      yMin = yMinRaw - yPad;
      yMax = yMaxRaw + yPad;
    }
    const yRange = yMax - yMin;

    this.chartGeom = { PAD, cw, ch, W, H, xMin, xRange, yMin, yRange, dpr };
    const toX = (v: number) => PAD.left + (v - xMin) / xRange * cw;
    const toY = (v: number) => PAD.top  + (1 - (v - yMin) / yRange) * ch;
    const y0  = toY(0);

    ctx.fillStyle = 'rgba(255,255,255,0.97)';
    ctx.beginPath(); ctx.roundRect(PAD.left, PAD.top, cw, ch, 6); ctx.fill();

    const xBE = toX(a.breakeven);
    const xK  = toX(a.strike);

    const lossR = Math.min(xBE, PAD.left + cw);
    if (lossR > PAD.left) {
      const lg = ctx.createLinearGradient(PAD.left, 0, lossR, 0);
      lg.addColorStop(0, 'rgba(239,68,68,0.18)'); lg.addColorStop(1, 'rgba(239,68,68,0.06)');
      ctx.fillStyle = lg; ctx.fillRect(PAD.left, PAD.top, lossR - PAD.left, ch);
    }
    const pL = Math.max(xBE, PAD.left), pR = Math.min(xK, PAD.left + cw);
    if (pR > pL) {
      const pg = ctx.createLinearGradient(pL, 0, pR, 0);
      pg.addColorStop(0, 'rgba(16,185,129,0.05)'); pg.addColorStop(1, 'rgba(16,185,129,0.13)');
      ctx.fillStyle = pg; ctx.fillRect(pL, PAD.top, pR - pL, ch);
    }
    const capL = Math.max(xK, PAD.left);
    if (capL < PAD.left + cw) {
      ctx.fillStyle = 'rgba(99,102,241,0.08)';
      ctx.fillRect(capL, PAD.top, PAD.left + cw - capL, ch);
    }

    ctx.strokeStyle = 'rgba(0,0,0,0.07)'; ctx.lineWidth = 1;
    ctx.font = '11px system-ui, sans-serif';
    for (let i = 0; i <= 6; i++) {
      const v = yMin + (i / 6) * yRange, y = toY(v);
      ctx.beginPath(); ctx.moveTo(PAD.left, y); ctx.lineTo(PAD.left + cw, y); ctx.stroke();
      ctx.fillStyle = 'rgba(60,75,100,0.8)'; ctx.textAlign = 'right';
      ctx.fillText(this.fmtK(v), PAD.left - 10, y + 4);
    }
    const rawStep = xRange / 7;
    const mag     = Math.pow(10, Math.floor(Math.log10(rawStep)));
    const xStep   = Math.round(rawStep / mag) * mag || 500;
    const xGStart = Math.ceil(xMin / xStep) * xStep;
    ctx.strokeStyle = 'rgba(0,0,0,0.05)';
    for (let v = xGStart; v <= xMax; v += xStep) {
      const x = toX(v);
      ctx.beginPath(); ctx.moveTo(x, PAD.top); ctx.lineTo(x, PAD.top + ch); ctx.stroke();
    }

    ctx.strokeStyle = 'rgba(0,0,0,0.2)'; ctx.lineWidth = 1.5; ctx.setLineDash([6, 4]);
    ctx.beginPath(); ctx.moveTo(PAD.left, y0); ctx.lineTo(PAD.left + cw, y0); ctx.stroke();
    ctx.setLineDash([]);

    ctx.save();
    ctx.beginPath(); ctx.rect(PAD.left, PAD.top, cw, ch); ctx.clip();

    const drawLine = (pts: { nifty: number; pnl: number }[], color: string, width: number, dash: number[] = []) => {
      ctx.beginPath(); ctx.lineWidth = width; ctx.strokeStyle = color; ctx.setLineDash(dash);
      for (let i = 0; i < pts.length; i++) {
        const x = toX(pts[i].nifty), y = toY(pts[i].pnl);
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      }
      ctx.stroke(); ctx.setLineDash([]);
    };

    const drawFill = (pts: { nifty: number; pnl: number }[], colorAbove: string, colorBelow: string) => {
      ctx.beginPath(); ctx.moveTo(toX(pts[0].nifty), y0);
      for (const p of pts) ctx.lineTo(toX(p.nifty), Math.max(toY(p.pnl), y0));
      ctx.lineTo(toX(pts[pts.length-1].nifty), y0); ctx.closePath();
      const gA = ctx.createLinearGradient(0, y0, 0, PAD.top);
      gA.addColorStop(0, colorAbove.replace('1)', '0.03)')); gA.addColorStop(1, colorAbove);
      ctx.fillStyle = gA; ctx.fill();
      ctx.beginPath(); ctx.moveTo(toX(pts[0].nifty), y0);
      for (const p of pts) ctx.lineTo(toX(p.nifty), Math.min(toY(p.pnl), y0));
      ctx.lineTo(toX(pts[pts.length-1].nifty), y0); ctx.closePath();
      const gB = ctx.createLinearGradient(0, PAD.top + ch, 0, y0);
      gB.addColorStop(0, colorBelow.replace('1)', '0.5)')); gB.addColorStop(1, colorBelow.replace('1)', '0.02)'));
      ctx.fillStyle = gB; ctx.fill();
    };

    if (isBreakdown) {
      drawFill(nbCurve,   'rgba(96,165,250,1)',  'rgba(96,165,250,1)');
      drawFill(callCurve, 'rgba(245,158,11,1)',  'rgba(245,158,11,1)');
      drawLine(curve, 'rgba(40,50,80,0.25)', 1.5, [5, 4]);
      drawLine(nbCurve,   '#60a5fa', 2.5);
      drawLine(callCurve, '#f59e0b', 2.5);
      const labelRight = (pnl: number, color: string, label: string) =>
        placeRight(toY(pnl), color, label);
      labelRight(nbCurve[nbCurve.length-1].pnl,    '#60a5fa', 'NiftyBees');
      labelRight(callCurve[callCurve.length-1].pnl, '#f59e0b', 'Short Call');
      labelRight(curve[curve.length-1].pnl,          'rgba(40,50,80,0.55)', 'Total');
    } else {
      drawFill(curve, 'rgba(16,185,129,1)', 'rgba(239,68,68,1)');
      const beR  = Math.max(0, Math.min(1, (a.breakeven - xMin) / xRange));
      const strR = Math.max(0, Math.min(1, (a.strike    - xMin) / xRange));
      const grad = ctx.createLinearGradient(PAD.left, 0, PAD.left + cw, 0);
      grad.addColorStop(0, '#ef4444'); grad.addColorStop(beR, '#f97316');
      grad.addColorStop(strR, '#10b981'); grad.addColorStop(1, '#818cf8');
      drawLine(curve, grad as any, 2.5);
      const maxY = toY(a.max_profit);
      if (maxY > PAD.top + 5 && maxY < PAD.top + ch - 5 && xK > PAD.left && xK < PAD.left + cw - 10) {
        ctx.fillStyle = 'rgba(16,185,129,0.9)'; ctx.font = '600 10px monospace'; ctx.textAlign = 'left';
        ctx.fillText(`MAX ₹${this.fmtK(a.max_profit)}`, Math.min(xK + 5, PAD.left + cw - 90), maxY - 5);
      }
      ctx.font = '700 11px system-ui, sans-serif'; ctx.textAlign = 'center';
      const lossW = Math.min(xBE, PAD.left + cw) - PAD.left;
      if (lossW > 60) { ctx.fillStyle = 'rgba(239,68,68,0.75)'; ctx.fillText('▼ LOSS', PAD.left + lossW / 2, PAD.top + 22); }
      if (pR - pL > 60) { ctx.fillStyle = 'rgba(16,185,129,0.8)'; ctx.fillText('▲ PROFIT', (pL + pR) / 2, PAD.top + 22); }
      if (PAD.left + cw - capL > 60) { ctx.fillStyle = 'rgba(129,140,248,0.8)'; ctx.fillText('◼ CAPPED', (capL + PAD.left + cw) / 2, PAD.top + 22); }
    }

    drawLine(targetCurve, '#0891b2', 2, [7, 4]);
    const tLabel = targetDFN === 0 ? 'Now (BS)' : targetDFN >= a.dte ? '' : `T+${targetDFN}d`;
    if (tLabel) {
      const tEnd = targetCurve[targetCurve.length - 1];
      placeRight(toY(tEnd.pnl), '#0891b2', tLabel);
    }

    // ── Phase 2C — Hedge overlay (mode-aware) ─────────────────────────────
    // Two render modes:
    //   • Combined view  : draw existing CC curve + CC+Hedge as DOTTED on top
    //   • Breakdown view : draw the HEDGE leg ALONE as a third component
    //                      (alongside the NB ETF and Short Call curves)
    if (this.hedgeOverlayMode() === 'overlay') {
      const h = this.hedgeOverlayHedge();
      if (h && h.strike) {
        const hLots    = Number(h.lots ?? 1);
        const hLotSize = Number(h.lot_size ?? 75);
        const hStrike  = Number(h.strike);
        const hPremium = Number(h.premium_paid ?? 0);
        const qty      = hLots * hLotSize;

        // Hedge-only payoff (used in breakdown + as an input to combined)
        const hedgeAlone = curve.map(p => ({
          nifty: p.nifty,
          pnl:   qty * (Math.max(hStrike - p.nifty, 0) - hPremium),
        }));

        if (isBreakdown) {
          // BREAKDOWN: draw the hedge as a separate component curve.
          drawLine(hedgeAlone, '#14b8a6', 2.0, [0, 0]);
          placeRight(toY(hedgeAlone[hedgeAlone.length - 1].pnl), '#14b8a6', 'HEDGE', '700 10px system-ui, sans-serif');
        } else {
          // COMBINED: existing CC curve already drawn solid; add CC+Hedge
          // as a DOTTED teal line on top so the user sees the *floor* the
          // hedge adds without losing the original CC line.
          const cchedge = curve.map((p, i) => ({
            nifty: p.nifty,
            pnl:   p.pnl + hedgeAlone[i].pnl,
          }));
          drawLine(cchedge, '#14b8a6', 2.2, [4, 4]);   // dotted/dashed
          placeRight(toY(cchedge[cchedge.length - 1].pnl), '#14b8a6', 'CC + HEDGE', '700 10px system-ui, sans-serif');
        }
      }
    }

    // ── Detail view live overlays ─────────────────────────────────────────
    // "Exit now" dashed horizontal + "TP" target + dot on expiry curve.
    if (this.currentView() === 'detail') {
      const livePnl = this.detailLivePnl();
      const pos      = this.selectedPos();
      const ac       = pos?.active_call;

      // ① Take-profit target line — what P&L you lock in at 50% premium capture.
      // At TP the call gain is fixed (0.5 × premium_total); ETF P&L is live.
      if (ac && pos?.live) {
        const tpCallGain = ac.premium_received * 0.5 * ac.lots * ac.lot_size;
        const tpTotal    = (pos.live.etf_pnl || 0) + tpCallGain;
        const yTP = toY(tpTotal);
        if (yTP >= PAD.top && yTP <= PAD.top + ch) {
          ctx.strokeStyle = 'rgba(16,185,129,0.55)'; ctx.lineWidth = 1.5; ctx.setLineDash([6, 4]);
          ctx.beginPath(); ctx.moveTo(PAD.left, yTP); ctx.lineTo(PAD.left + cw, yTP);
          ctx.stroke(); ctx.setLineDash([]);
        }
      }

      // ② "Exit now" line — current live total P&L (mark-to-market).
      if (livePnl !== null) {
        const yLive = toY(livePnl);
        if (yLive >= PAD.top && yLive <= PAD.top + ch) {
          ctx.strokeStyle = 'rgba(6,182,212,0.75)'; ctx.lineWidth = 1.5; ctx.setLineDash([4, 3]);
          ctx.beginPath(); ctx.moveTo(PAD.left, yLive); ctx.lineTo(PAD.left + cw, yLive);
          ctx.stroke(); ctx.setLineDash([]);
        }
      }

      // ③ Dot on the payoff curve at the current live spot (where you land at expiry).
      const xSpotNow = toX(a.spot);
      if (xSpotNow >= PAD.left && xSpotNow <= PAD.left + cw) {
        const nearPnl = a.payoff_curve.reduce((b, p) =>
          Math.abs(p.nifty - a.spot) < Math.abs(b.nifty - a.spot) ? p : b
        ).pnl;
        const yDot = toY(nearPnl);
        ctx.beginPath(); ctx.arc(xSpotNow, yDot, 7, 0, Math.PI * 2);
        ctx.fillStyle = nearPnl >= 0 ? 'rgba(16,185,129,0.9)' : 'rgba(239,68,68,0.9)'; ctx.fill();
        ctx.strokeStyle = '#fff'; ctx.lineWidth = 2.5; ctx.stroke();
      }
    }

    ctx.restore();

    // Detail view: right-edge labels for horizontal overlay lines
    if (this.currentView() === 'detail') {
      const pos = this.selectedPos();
      const ac  = pos?.active_call;

      // TP target label (green)
      if (ac && pos?.live) {
        const tpCallGain = ac.premium_received * 0.5 * ac.lots * ac.lot_size;
        const tpTotal    = (pos.live.etf_pnl || 0) + tpCallGain;
        placeRight(toY(tpTotal), 'rgba(16,185,129,0.85)', `TP ${this.fmtK(tpTotal)}`);
      }

      // Exit-now label (cyan)
      const livePnl = this.detailLivePnl();
      if (livePnl !== null) {
        placeRight(toY(livePnl), 'rgba(6,182,212,0.9)', `Now ${this.fmtK(livePnl)}`);
      }
    }

    // ── Markers above the chart (BE/K/NOW/ENTRY).
    // Each marker is rendered as a single combined chip ("BE 24029") so we
    // only need ONE line of vertical space per lane. Markers within
    // MIN_X_GAP of each other get assigned different lanes — but we also
    // measure the actual rendered width and treat that as the collision box,
    // so very wide labels don't graze adjacent ones either.
    type Marker = { x: number; color: string; label: string; value: string };
    const markers: Marker[] = [
      { x: xBE,         color: 'rgba(217,119,6,0.95)',  label: 'BE',    value: a.breakeven.toFixed(0) },
      { x: xK,          color: 'rgba(99,102,241,0.95)', label: 'K',     value: a.strike.toFixed(0) },
      { x: toX(a.spot), color: 'rgba(60,80,120,0.85)',  label: 'SPOT',  value: a.spot.toFixed(0) },
    ];
    if (a.entry_nifty && Math.abs(a.entry_nifty - a.spot) > 1) {
      markers.push({ x: toX(a.entry_nifty), color: 'rgba(139,92,246,0.95)', label: 'ENTRY', value: a.entry_nifty.toFixed(0) });
    }
    const visibleMarkers = markers
      .filter(m => m.x >= PAD.left && m.x <= PAD.left + cw)
      .sort((a, b) => a.x - b.x);

    const LANE_DY = 16;          // one combined-text line per lane
    const LABEL_PAD = 6;         // px gap between adjacent labels horizontally
    ctx.font = 'bold 10px system-ui, sans-serif';
    // Pre-measure widths so we can use real text bounds for collision.
    const measured = visibleMarkers.map(m => {
      const text = `${m.label} ${m.value}`;
      const w = ctx.measureText(text).width;
      return { ...m, text, halfW: w / 2 + LABEL_PAD };
    });

    type Lane = { rightX: number };
    const lanes: Lane[] = [];
    const placements: { m: typeof measured[0]; lane: number }[] = [];
    for (const m of measured) {
      const leftEdge = m.x - m.halfW;
      let chosen = -1;
      for (let i = 0; i < lanes.length; i++) {
        if (leftEdge >= lanes[i].rightX) { chosen = i; break; }
      }
      if (chosen < 0) { chosen = lanes.length; lanes.push({ rightX: 0 }); }
      lanes[chosen].rightX = m.x + m.halfW;
      placements.push({ m, lane: chosen });
    }

    for (const { m, lane } of placements) {
      ctx.strokeStyle = m.color; ctx.lineWidth = 1.5; ctx.setLineDash([5, 4]);
      ctx.beginPath(); ctx.moveTo(m.x, PAD.top); ctx.lineTo(m.x, PAD.top + ch); ctx.stroke();
      ctx.setLineDash([]);
      const y = PAD.top - 12 - lane * LANE_DY;
      ctx.font = 'bold 10px system-ui, sans-serif';
      ctx.textAlign = 'center';
      ctx.fillStyle = m.color;
      ctx.fillText(m.text, m.x, y);
    }

    if (tNifty > 0) {
      const txX = toX(tNifty);
      if (txX >= PAD.left && txX <= PAD.left + cw) {
        ctx.strokeStyle = '#0891b2'; ctx.lineWidth = 1.5; ctx.setLineDash([3, 3]);
        ctx.beginPath(); ctx.moveTo(txX, PAD.top); ctx.lineTo(txX, PAD.top + ch); ctx.stroke();
        ctx.setLineDash([]);
        const tPnl  = this.pnlAtDate(tNifty, a, targetDLeft);
        const tPnlY = toY(tPnl);
        ctx.beginPath(); ctx.arc(txX, tPnlY, 6, 0, Math.PI * 2);
        ctx.fillStyle = '#06b6d4'; ctx.fill();
        ctx.strokeStyle = 'rgba(255,255,255,0.95)'; ctx.lineWidth = 2; ctx.stroke();
        ctx.font = 'bold 10px monospace'; ctx.textAlign = 'center';
        ctx.fillStyle = '#0891b2'; ctx.fillText(this.fmtK(tPnl), txX, PAD.top - 12);
      }
    }

    ctx.fillStyle = 'rgba(60,75,100,0.8)'; ctx.font = '11px monospace'; ctx.textAlign = 'center';
    for (let v = xGStart; v <= xMax; v += xStep) {
      ctx.fillText(v.toFixed(0), toX(v), PAD.top + ch + 20);
    }
    ctx.fillStyle = 'rgba(60,75,100,0.55)'; ctx.font = '12px system-ui, sans-serif'; ctx.textAlign = 'center';
    ctx.fillText('Nifty spot at expiry →', PAD.left + cw / 2, H - 6);
    ctx.save();
    ctx.translate(16, PAD.top + ch / 2); ctx.rotate(-Math.PI / 2); ctx.textAlign = 'center';
    ctx.fillText('P&L (₹)', 0, 0);
    ctx.restore();
  }

  // ── Crosshair ─────────────────────────────────────────────────────────────
  onCanvasMouseMove(e: MouseEvent) {
    const a = this.analysis();
    if (!a || !this.chartGeom) { this.tooltipVisible.set(false); return; }
    const overlay = this.overlayRef?.nativeElement;
    if (!overlay) return;
    const rect = overlay.getBoundingClientRect();
    const mx = e.clientX - rect.left, my = e.clientY - rect.top;
    const g  = this.chartGeom;
    if (mx < g.PAD.left || mx > g.PAD.left + g.cw || my < g.PAD.top || my > g.PAD.top + g.ch) {
      this.tooltipVisible.set(false); this.clearCrosshair(); return;
    }
    const niftyVal = g.xMin + (mx - g.PAD.left) / g.cw * g.xRange;
    const nearest  = a.payoff_curve.reduce((best, p) =>
      Math.abs(p.nifty - niftyVal) < Math.abs(best.nifty - niftyVal) ? p : best
    );
    const zone = nearest.nifty < a.breakeven ? 'loss' : nearest.nifty < a.strike ? 'profit' : 'capped';
    const isBreakdown = this.chartMode() === 'breakdown';
    let nbPnl: number | undefined, callPnl: number | undefined;
    if (isBreakdown) {
      nbPnl   = a.shares * (nearest.nifty / 100 - a.spot / 100);
      callPnl = a.lots * a.lot_size * (a.premium - Math.max(nearest.nifty - a.strike, 0));
    }
    const targetDFN   = this.targetDaysFromNow();
    const targetDLeft = Math.max(a.dte - targetDFN, 0);
    const comps       = this.pnlComponentsAtDate(nearest.nifty, a, targetDLeft);

    // Phase 2C — compute hedge tooltip extras when overlay is on
    let hedgePnl: number | undefined;
    let cchedgePnl: number | undefined;
    let hedgeStrike: number | undefined;
    let hedgeDte: number | undefined;
    let hedgeIsActive = false;
    if (this.hedgeOverlayMode() === 'overlay') {
      const h = this.hedgeOverlayHedge();
      if (h && h.strike) {
        const hLots    = Number(h.lots ?? 1);
        const hLotSize = Number(h.lot_size ?? 75);
        const qty      = hLots * hLotSize;
        const hStrike  = Number(h.strike);
        const hPremium = Number(h.premium_paid ?? 0);
        const hDte     = Number(h.dte ?? 0);
        // Hedge mark-to-market at the same target date as the rest of the
        // tooltip (today by default). Uses BS with ~14% IV (close to typical
        // Nifty IV; over time this can be derived from current_price/strike).
        const hedgeDaysLeft = Math.max(hDte - targetDFN, 0);
        const hedgeIv       = 0.14;
        const hedgeValBS    = this.bsPut(nearest.nifty, hStrike, hedgeDaysLeft / 365, hedgeIv);
        hedgePnl       = qty * (hedgeValBS - hPremium);
        cchedgePnl     = comps.total + hedgePnl;
        hedgeStrike    = hStrike;
        hedgeDte       = hDte;
        hedgeIsActive  = true;
      }
    }

    this.tooltipData.set({
      nifty: nearest.nifty, pnl: nearest.pnl, zone, nbPnl, callPnl,
      targetPnl: comps.total, targetNbPnl: comps.nbPnl, targetCallPnl: comps.callPnl,
      targetDFN, targetDaysLeft: targetDLeft,
      hedgePnl, cchedgePnl, hedgeStrike, hedgeDte, hedgeIsActive,
    });
    this.tooltipVisible.set(true);
    const tipW = 210;
    const tipX = mx + 14 + tipW > g.W ? mx - tipW - 14 : mx + 14;
    this.tooltipX.set(tipX);
    this.tooltipY.set(Math.max(g.PAD.top, Math.min(my - 30, g.PAD.top + g.ch - 170)));
    this.drawCrosshair(nearest, a, g, comps.total);
  }

  onCanvasMouseLeave() { this.tooltipVisible.set(false); this.clearCrosshair(); }

  // Click on the chart → snap targetNifty to that x-coordinate's Nifty value.
  onCanvasClick(e: MouseEvent) {
    if (!this.analysis() || !this.chartGeom) return;
    const overlay = this.overlayRef?.nativeElement;
    if (!overlay) return;
    const rect = overlay.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const g = this.chartGeom;
    if (mx < g.PAD.left || mx > g.PAD.left + g.cw) return;
    const niftyVal = g.xMin + (mx - g.PAD.left) / g.cw * g.xRange;
    this.targetNifty.set(Math.round(niftyVal));
  }

  private drawCrosshair(point: { nifty: number; pnl: number }, a: Analysis, g: ChartGeom, targetPnl?: number) {
    const overlay = this.overlayRef?.nativeElement;
    if (!overlay) return;
    const octx = overlay.getContext('2d');
    if (!octx) return;
    const { dpr } = g;
    const toX = (v: number) => g.PAD.left + (v - g.xMin) / g.xRange * g.cw;
    const toY = (v: number) => g.PAD.top  + (1 - (v - g.yMin) / g.yRange) * g.ch;
    const cx = toX(point.nifty), cy = toY(point.pnl);
    octx.setTransform(1, 0, 0, 1, 0, 0); octx.clearRect(0, 0, overlay.width, overlay.height);
    octx.setTransform(dpr, 0, 0, dpr, 0, 0);
    octx.strokeStyle = 'rgba(30,50,90,0.22)'; octx.lineWidth = 1; octx.setLineDash([4, 3]);
    octx.beginPath(); octx.moveTo(cx, g.PAD.top); octx.lineTo(cx, g.PAD.top + g.ch); octx.stroke();
    octx.strokeStyle = 'rgba(30,50,90,0.14)';
    octx.beginPath(); octx.moveTo(g.PAD.left, cy); octx.lineTo(cx, cy); octx.stroke();
    octx.setLineDash([]);
    const drawDot = (nx: number, pnl: number, fill: string) => {
      const dx = toX(nx), dy = toY(pnl);
      octx.beginPath(); octx.arc(dx, dy, 5, 0, Math.PI * 2);
      octx.fillStyle = fill; octx.fill();
      octx.strokeStyle = 'rgba(255,255,255,0.85)'; octx.lineWidth = 1.5; octx.stroke();
    };
    if (this.chartMode() === 'breakdown') {
      drawDot(point.nifty, a.shares * (point.nifty / 100 - a.spot / 100), '#60a5fa');
      drawDot(point.nifty, a.lots * a.lot_size * (a.premium - Math.max(point.nifty - a.strike, 0)), '#f59e0b');
      drawDot(point.nifty, point.pnl, 'rgba(40,50,80,0.7)');
    } else {
      drawDot(point.nifty, point.pnl, point.pnl >= 0 ? '#10b981' : '#ef4444');
    }
    if (targetPnl !== undefined) drawDot(point.nifty, targetPnl, '#0891b2');
  }

  private clearCrosshair() {
    const overlay = this.overlayRef?.nativeElement;
    if (!overlay) return;
    const octx = overlay.getContext('2d');
    if (octx) { octx.setTransform(1, 0, 0, 1, 0, 0); octx.clearRect(0, 0, overlay.width, overlay.height); }
  }

  // ── Formatters & helpers ───────────────────────────────────────────────────
  fmtK(v: number): string {
    const a = Math.abs(v), s = v < 0 ? '-' : '';
    if (a >= 1e5) return `${s}${(a / 1e5).toFixed(1)}L`;
    if (a >= 1e3) return `${s}${(a / 1e3).toFixed(0)}K`;
    return `${s}${a.toFixed(0)}`;
  }
  fmt(v: number, d = 0)  { return v?.toLocaleString('en-IN', { minimumFractionDigits: d, maximumFractionDigits: d }); }
  fmtPct(v: number)      { return `${v >= 0 ? '+' : ''}${v?.toFixed(2)}%`; }
  fmtRs(v: number | null | undefined) {
    if (v === null || v === undefined) return '—';
    return `${v >= 0 ? '+' : '−'}₹${Math.abs(v).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`;
  }
  // Parse a date string as a *calendar* date in the user's local timezone.
  // Backend often returns date-only strings ("2026-05-08") which JS parses as
  // UTC midnight. In IST that's still May 8, but in any TZ west of UTC it
  // would shift by a day. By extracting the YYYY-MM-DD parts and constructing
  // a local-midnight Date, the result is timezone-stable across users.
  parseLocalDate(s: string): Date {
    if (!s) return new Date(NaN);
    const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(s);
    if (m) return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
    return new Date(s);
  }
  // YYYY-MM-DD key in *local* time (not UTC). Used for grouping by date.
  dateKey(s: string): string {
    const d = this.parseLocalDate(s);
    if (isNaN(d.getTime())) return '';
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, '0');
    const day = String(d.getDate()).padStart(2, '0');
    return `${y}-${m}-${day}`;
  }
  fmtDate(s: string): string {
    const d = this.parseLocalDate(s);
    if (isNaN(d.getTime())) return s;
    return d.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: '2-digit' });
  }
  fmtDateWithDay(s: string): string {
    const d = this.parseLocalDate(s);
    if (isNaN(d.getTime())) return s || '';
    const day = d.toLocaleDateString('en-IN', { weekday: 'short' });
    return `${day} · ${this.fmtDate(s)}`;
  }
  // Calendar-day-accurate "today" diff. Compares local midnights so the
  // result is independent of time-of-day or browser timezone.
  daysFromToday(s: string): string {
    const d = this.parseLocalDate(s);
    if (isNaN(d.getTime())) return '';
    const today = new Date();
    const a = new Date(today.getFullYear(), today.getMonth(), today.getDate()).getTime();
    const b = new Date(d.getFullYear(),     d.getMonth(),     d.getDate()).getTime();
    const diffDays = Math.round((b - a) / 86_400_000);
    if (diffDays === 0)  return 'today';
    if (diffDays === 1)  return 'tomorrow';
    if (diffDays === -1) return 'yesterday';
    if (diffDays > 0)    return `in ${diffDays}d`;
    return `${-diffDays}d ago`;
  }
  // Calendar DTE — same calendar-aware logic, returns days as a number.
  daysToCalendar(s: string): number {
    const d = this.parseLocalDate(s);
    if (isNaN(d.getTime())) return 0;
    const today = new Date();
    const a = new Date(today.getFullYear(), today.getMonth(), today.getDate()).getTime();
    const b = new Date(d.getFullYear(),     d.getMonth(),     d.getDate()).getTime();
    return Math.round((b - a) / 86_400_000);
  }

  deltaTier(d: number | null): string {
    if (d === null) return '';
    const a = Math.abs(d);
    return a >= 0.45 ? 'delta-high' : a >= 0.25 ? 'delta-mid' : 'delta-low';
  }
  distanceTier(pct: number): string {
    if (pct <= 0) return 'atm';
    if (pct <= 2) return 'near';
    if (pct <= 5) return 'mid';
    return 'far';
  }
  onLotsChange(v: string) {
    const n = Math.max(1, Math.min(100, parseInt(v) || 1));
    this.lots.set(n); this.analyze();
  }
  toggleRisk() { this.showRiskDetail.update(v => !v); }
  onTargetNiftyChange(v: string) {
    const n = parseFloat(v);
    this.targetNifty.set(isNaN(n) || n <= 0 ? 0 : Math.round(n));
  }
  onTargetDaysChange(v: any) {
    const dte = this.analysis()?.dte ?? 0;
    const n = parseInt(v, 10);
    this.targetDaysFromNow.set(isNaN(n) || n < 0 ? 0 : Math.min(n, dte));
  }
}
