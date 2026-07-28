import { Component, OnInit, Input, inject, signal, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpErrorResponse } from '@angular/common/http';
import { DashboardService, Position } from '../../services/dashboard.service';
import { PlannerService, WishItem } from '../../services/planner.service';

const CLASS_COLOR: Record<string, string> = {
  apartments: '#4e8cff', land: '#2bb673', built: '#9b6dd6', stocks: '#f0883e',
  gold: '#e8b730', bonds: '#16b8a6', fd: '#00a3c4', ulip: '#e0598b', cash: '#94a3b8',
};

interface LinkedAsset {
  key: string; name: string; asset_class: string | null; owner: string;
  realisable: number; sellable_on: string | null; sold: boolean;
}

interface Plan {
  item: WishItem;
  price: number;
  saved: number;            // manual funds + sold assets (capped at price)
  manualSaved: number;
  assetSaved: number;
  pct: number;              // 0..100
  remaining: number;
  perMonth: number | null;  // ₹/mo still needed to hit the buy-by date
  assets: LinkedAsset[];
  feasible: boolean;        // perMonth fits within your surplus
  status: 'ready' | 'saving' | 'noprice' | 'bought';
  verdict: string;          // the plain-English "how well can I buy it" line
}

@Component({
  selector: 'app-buy-planner',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './buy-planner.html',
  styleUrl: './buy-planner.scss',
})
export class BuyPlanner implements OnInit {
  private api = inject(PlannerService);

  @Input() set monthlySurplus(v: number) { this._surplus.set(v || 0); }
  @Input() set positions(v: Position[]) { this._positions.set(v || []); }
  private _surplus = signal(0);
  private _positions = signal<Position[]>([]);

  wishlist = signal<WishItem[]>([]);
  loading = signal(true);
  needsMigration = signal(false);
  error = signal<string | null>(null);
  showModal = signal(false);

  inr = DashboardService.inr;
  inrFull = DashboardService.inrFull;
  color = (c: string | null) => (c && CLASS_COLOR[c]) || '#6b7190';

  surplus = computed(() => Math.max(0, this._surplus()));
  committed = computed(() => this.plans().filter(p => p.status === 'saving').reduce((s, p) => s + (p.perMonth || 0), 0));
  overCommitted = computed(() => this.committed() > this.surplus() + 1);

  ngOnInit() { this.reload(); }

  reload() {
    this.loading.set(true); this.error.set(null); this.needsMigration.set(false);
    this.api.summary().subscribe({
      next: s => { this.wishlist.set(s.wishlist || []); this.loading.set(false); },
      error: (e: HttpErrorResponse) => {
        this.loading.set(false);
        if (e.status === 503) this.needsMigration.set(true);
        else this.error.set('Could not load the planner.');
      },
    });
  }

  // ── date helpers ──────────────────────────────────────────────────────────────
  private now() { return new Date(); }
  monthsUntil(d: string | null): number {
    if (!d) return 0;
    const [y, m] = d.split('-').map(Number);
    if (!y || !m) return 0;
    const n = this.now();
    return Math.max(0, (y - n.getFullYear()) * 12 + (m - 1 - n.getMonth()));
  }
  monthName(d: string | null): string {
    if (!d) return '';
    const dt = new Date(d + (d.length <= 7 ? '-01' : '') + 'T00:00:00');
    return isNaN(dt.getTime()) ? '' : dt.toLocaleDateString('en-IN', { month: 'short', year: 'numeric' });
  }
  fmtDate(d: string | null): string { return this.monthName(d) || 'anytime'; }
  private monthDate(t: number): Date { const n = this.now(); return new Date(n.getFullYear(), n.getMonth() + t, 1); }
  fmtMonthAhead(t: number): string { return this.monthDate(t).toLocaleDateString('en-IN', { month: 'short', year: 'numeric' }); }
  /** whole months since `aIso` (when the item was added) — drives SIP accrual. */
  private monthsElapsed(aIso: string | null | undefined): number {
    if (!aIso) return 0;
    const a = new Date(aIso); if (isNaN(a.getTime())) return 0;
    const n = this.now();
    return Math.max(0, (n.getFullYear() - a.getFullYear()) * 12 + (n.getMonth() - a.getMonth()));
  }
  /** whole months from `aIso` to the `bIso` date (min 1) — the SIP horizon. */
  private monthsFromTo(aIso: string | null | undefined, bIso: string | null): number {
    const a = aIso ? new Date(aIso) : this.now();
    const sa = isNaN(a.getTime()) ? this.now() : a;
    if (!bIso) return 1;
    const [y, m] = bIso.split('-').map(Number); if (!y || !m) return 1;
    return Math.max(1, (y - sa.getFullYear()) * 12 + (m - 1 - sa.getMonth()));
  }

  // ── position lookups ────────────────────────────────────────────────────────
  posKey(p: Position): string { return `${p.asset_class}|${p.name}|${p.owner}`; }
  private posByKey = computed(() => {
    const m = new Map<string, Position>();
    this._positions().forEach(p => m.set(this.posKey(p), p));
    return m;
  });
  private parse(json: string | null): string[] {
    if (!json) return [];
    try { const a = JSON.parse(json); return Array.isArray(a) ? a : []; } catch { return []; }
  }
  private claimedBy(exceptId: string): Set<string> {
    const s = new Set<string>();
    this.wishlist().forEach(w => { if (w.id !== exceptId) this.parse(w.finance_assets).forEach(k => s.add(k)); });
    return s;
  }
  availableFor(item: WishItem): Position[] {
    const mine = new Set(this.parse(item.finance_assets));
    const claimed = this.claimedBy(item.id);
    // only assets that have a sell date set (on their own page) can fund a buy
    return this._positions().filter(p => p.value > 0 && !!p.sellable_on
        && !mine.has(this.posKey(p)) && !claimed.has(this.posKey(p)))
      .slice().sort((a, b) => b.realisable - a.realisable);
  }
  linkedAssets(item: WishItem): LinkedAsset[] {
    const sold = new Set(this.parse(item.sold_assets));
    return this.parse(item.finance_assets).map(key => {
      const p = this.posByKey().get(key);
      const [cls, name, owner] = key.split('|');
      return {
        key, name: p?.name || name || 'Asset', asset_class: p?.asset_class || cls || null,
        owner: p?.owner || owner || '', realisable: p?.realisable || 0,
        sellable_on: p?.sellable_on || null, sold: sold.has(key),
      };
    });
  }

  // ── the per-item plan: an SIP that fills the bar each month ─────────────────────
  plans = computed<Plan[]>(() => {
    const surplus = this.surplus();
    return this.wishlist().slice().sort((a, b) => a.priority - b.priority).map(item => {
      const price = item.price || 0;
      const assets = this.linkedAssets(item);
      const assetSaved = assets.filter(a => a.sold).reduce((s, a) => s + a.realisable, 0);
      const unsoldVal = assets.filter(a => !a.sold).reduce((s, a) => s + a.realisable, 0);
      const manualSaved = item.saved || 0;

      // the SIP: an explicit ₹/mo, else derived from price ÷ months-to-target
      // (over the gap not already funded by lump sums + linked assets).
      const baseGap = Math.max(0, price - manualSaved - assetSaved - unsoldVal);
      const horizon = item.target_date ? this.monthsFromTo(item.created_at, item.target_date) : null;
      const sipDerived = horizon != null && baseGap > 0 ? Math.ceil(baseGap / horizon) : 0;
      const sip = (item.monthly_contribution && item.monthly_contribution > 0) ? item.monthly_contribution : sipDerived;

      // SIP money accrued so far = months since you added it × the SIP
      const sipAccrued = sip * this.monthsElapsed(item.created_at);
      const rawSaved = manualSaved + assetSaved + sipAccrued;
      const saved = price > 0 ? Math.min(price, rawSaved) : rawSaved;
      const pct = price > 0 ? Math.min(100, Math.round((rawSaved / price) * 100)) : 0;
      const remaining = Math.max(0, price - rawSaved);

      if (item.bought) return { item, price, saved, manualSaved, assetSaved, pct: 100, remaining: 0, perMonth: sip, assets, feasible: true, status: 'bought', verdict: 'bought' };
      if (price <= 0) return { item, price, saved, manualSaved, assetSaved, pct, remaining, perMonth: null, assets, feasible: true, status: 'noprice', verdict: 'add a price' };
      if (rawSaved >= price - 1) return { item, price, saved, manualSaved, assetSaved, pct: 100, remaining: 0, perMonth: sip, assets, feasible: true, status: 'ready', verdict: '🎉 You can buy it now' };

      const feasible = sip <= surplus + 1;
      // when will it be funded? project SIP + unsold-asset sales forward.
      let readyLabel = item.target_date ? this.monthName(item.target_date) : '';
      if (!item.target_date && (sip > 0 || unsoldVal > 0)) {
        const unsold = assets.filter(a => !a.sold).map(a => ({ amt: a.realisable, m: this.monthsUntil(a.sellable_on) }));
        let bal = 0; for (let t = 0; t <= 1200; t++) { if (t > 0) bal += sip; bal += unsold.filter(u => u.m === t).reduce((s, u) => s + u.amt, 0); if (bal >= remaining - 1) { readyLabel = this.fmtMonthAhead(t); break; } }
      }

      let verdict: string;
      if (sip > 0) {
        verdict = `SIP ${this.inr(sip)}/mo` + (readyLabel ? ` → ${readyLabel}` : '');
        if (!feasible) verdict += ` · over your ${this.inr(surplus)}/mo spare`;
      } else if (unsoldVal >= remaining - 1 && unsoldVal > 0) {
        const names = assets.filter(a => !a.sold).map(a => a.name).slice(0, 2).join(' + ');
        verdict = `sell ${names} to cover it`;
      } else {
        verdict = `set a buy-by date or a monthly amount`;
      }
      return { item, price, saved, manualSaved, assetSaved, pct, remaining, perMonth: sip, assets, feasible, status: 'saving', verdict };
    });
  });

  private planFor(id: string) { return this.plans().find(p => p.item.id === id); }

  // ── summaries for the compact card ──────────────────────────────────────────────
  active = computed(() => this.plans().filter(p => p.status !== 'bought'));
  topItems = computed(() => this.active().slice(0, 4));
  boughtItems = computed(() => this.wishlist().filter(w => w.bought));
  summary = computed(() => {
    const a = this.active();
    const ready = a.filter(p => p.status === 'ready');
    const next = a.filter(p => p.status === 'saving').sort((x, y) => y.pct - x.pct)[0] || null;
    return {
      count: a.length,
      readyCount: ready.length,
      readyTotal: ready.reduce((s, p) => s + p.price, 0),
      next,
      totalCost: a.reduce((s, p) => s + p.price, 0),
      totalSaved: a.reduce((s, p) => s + p.saved, 0),
    };
  });

  // ── add a new item (asks the buy-by date up front) ──────────────────────────────
  newName = signal(''); newPrice = signal<number | null>(null); newTarget = signal('');
  addWish() {
    const name = this.newName().trim(); if (!name) return;
    this.api.addWish({ name, price: this.newPrice(), target_date: this.newTarget() || null }).subscribe({
      next: it => { this.wishlist.update(l => [...l, it]); this.newName.set(''); this.newPrice.set(null); this.newTarget.set(''); },
      error: () => this.error.set('Could not add item.'),
    });
  }

  patchWish(it: WishItem, patch: Partial<WishItem>) {
    this.wishlist.update(l => l.map(x => x.id === it.id ? { ...x, ...patch } : x));
    this.api.updateWish(it.id, patch as any).subscribe({ error: () => this.reload() });
  }
  setPrice(it: WishItem, v: number | null) { this.patchWish(it, { price: v == null || isNaN(v as any) ? null : +v }); }
  setName(it: WishItem, v: string) { const name = (v || '').trim(); if (name) this.patchWish(it, { name }); }
  /** set a buy-by date → SIP derives from price ÷ months (clears explicit SIP). */
  setTargetDate(it: WishItem, v: string) { this.patchWish(it, { target_date: v || '', monthly_contribution: null } as any); }
  /** set an explicit SIP amount → clears the target date. */
  setContribution(it: WishItem, v: number | null) { this.patchWish(it, { monthly_contribution: v == null || isNaN(v as any) || +v <= 0 ? null : +v, target_date: '' } as any); }
  markBought(it: WishItem) { this.patchWish(it, { bought: !it.bought }); }
  removeWish(it: WishItem) {
    this.wishlist.update(l => l.filter(x => x.id !== it.id));
    this.api.deleteWish(it.id).subscribe({ error: () => this.reload() });
  }
  move(it: WishItem, dir: -1 | 1) {
    const order = this.wishlist().filter(x => !x.bought).slice().sort((a, b) => a.priority - b.priority);
    const i = order.findIndex(x => x.id === it.id); const j = i + dir;
    if (i < 0 || j < 0 || j >= order.length) return;
    [order[i], order[j]] = [order[j], order[i]];
    const ids = order.map(x => x.id);
    this.wishlist.update(l => l.map(x => { const k = ids.indexOf(x.id); return k >= 0 ? { ...x, priority: k } : x; }));
    this.api.reorderWish(ids).subscribe({ next: r => this.wishlist.set(r.items), error: () => this.reload() });
  }

  // ── add funds (the money you've actually set aside) ─────────────────────────────
  fundDraft = signal<Record<string, number | null>>({});
  setFundDraft(id: string, v: number | null) { this.fundDraft.update(m => ({ ...m, [id]: v })); }
  addFunds(it: WishItem) {
    const amt = this.fundDraft()[it.id];
    if (!amt || isNaN(amt as any) || +amt === 0) return;
    const saved = Math.max(0, (it.saved || 0) + +amt);
    this.patchWish(it, { saved });
    this.setFundDraft(it.id, null);
  }
  resetSaved(it: WishItem) { if (confirm(`Reset funds saved for "${it.name}" to ₹0?`)) this.patchWish(it, { saved: 0 }); }

  // ── savings via selling assets ──────────────────────────────────────────────────
  addAsset(it: WishItem, key: string) {
    if (!key) return;
    const keys = this.parse(it.finance_assets); if (keys.includes(key)) return;
    keys.push(key);
    this.patchWish(it, { finance_assets: JSON.stringify(keys) });
  }
  removeAsset(it: WishItem, key: string) {
    const keys = this.parse(it.finance_assets).filter(k => k !== key);
    const sold = this.parse(it.sold_assets).filter(k => k !== key);
    this.patchWish(it, { finance_assets: JSON.stringify(keys), sold_assets: JSON.stringify(sold) });
  }
  toggleSold(it: WishItem, key: string) {
    const sold = new Set(this.parse(it.sold_assets));
    sold.has(key) ? sold.delete(key) : sold.add(key);
    this.patchWish(it, { sold_assets: JSON.stringify([...sold]) });
  }

  openModal() { this.showModal.set(true); }
}
