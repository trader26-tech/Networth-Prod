import { Component, ElementRef, EventEmitter, Input, OnInit, Output, ViewChild, computed, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { TaxService, LandTax as LandTaxData, TaxProfile, ParcelAnalysis, TaxScenario, Residency, PlanPrefs, SalePlan } from '../../services/tax.service';

interface Choice { key: string; label: string; }
interface Msg {
  role: 'bot' | 'user';
  text?: string;
  card?: 'portfolio' | 'parcels' | 'compare' | 'scenarios' | 'levers' | 'transfer' | 'profiles' | 'select' | 'step' | 'plan' | 'reshuffle';
  data?: any;
}

@Component({
  selector: 'app-land-tax',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './land-tax.html',
  styleUrl: './land-tax.scss',
})
export class LandTax implements OnInit {
  private api = inject(TaxService);
  @ViewChild('scroller') scroller?: ElementRef<HTMLDivElement>;
  @Input() embedded = false;          // true → renders as a panel inside another page
  @Input() expanded = false;          // true → the parent has grown it into the big drawer
  @Output() closed = new EventEmitter<void>();
  @Output() toggleSize = new EventEmitter<void>();   // expand ⇄ collapse

  // Bhoomi's portrait (drop the file at frontend/public/bhoomi-land-tax.jpg).
  // Falls back to the emoji until the image is present.
  avatarImg = '/bhoomi-land-tax.png';
  imgOk = signal(true);

  inr = TaxService.inr;

  /** our scripted bot copy is trusted — render **bold** markers as <b>. */
  boldify(text: string | undefined): string {
    if (!text) return '';
    const esc = text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    return esc.replace(/\*\*(.+?)\*\*/g, '<b>$1</b>');
  }

  // the character for THIS tab (land)
  bot = { name: 'Bhoomi', role: 'Land & Capital-Gains Guide', emoji: '🏞️' };

  data = signal<LandTaxData | null>(null);
  profiles = signal<TaxProfile[]>([]);
  loading = signal(true);
  error = signal<string | null>(null);
  msgs = signal<Msg[]>([]);
  options = signal<Choice[]>([]);
  typing = signal(false);

  ngOnInit() {
    this.api.land().subscribe({
      next: d => { this.data.set(d); this.loading.set(false); this.greet(); },
      error: e => { this.loading.set(false); this.error.set(e?.error?.detail || 'Could not load your land tax data.'); },
    });
    this.api.profiles().subscribe({ next: r => { this.profiles.set(r.profiles); this.houseCounts.set(r.house_counts || {}); }, error: () => {} });
    // restore any saved plan so the user doesn't have to redo it
    this.api.getPrefs().subscribe({
      next: (sp) => {
        if (sp?.prefs) this.prefs.set({ ...this.prefs(), ...sp.prefs });
        if (Array.isArray(sp?.selections)) {
          this.selected.set(new Set(sp.selections.map((s: any) => s.name)));
          const rv: Record<string, number> = {};
          for (const s of sp.selections) if (s.registered_value != null) rv[s.name] = s.registered_value;
          this.regValues.set(rv);
          this.savedPlan.set(true);
        }
      }, error: () => {},
    });
  }
  savedPlan = signal(false);

  // ── message helpers ──────────────────────────────────────────────────────
  private push(m: Msg) { this.msgs.update(a => [...a, m]); }
  private say(text: string) { this.push({ role: 'bot', text }); }
  private card(card: Msg['card'], data?: any) { this.push({ role: 'bot', card, data }); }
  private setMenu() {
    this.options.set([
      { key: 'own_tax', label: '🧾 Tax on all the land we already own' },
      { key: 'sell_plan', label: '💰 Sell a property — the tax-smart way' },
      { key: 'nri', label: '🌏 Save tax on land as an NRI' },
    ]);
  }
  /** Put the newest question at the TOP so the answer flows in below it — the
   *  user reads top-to-bottom and scrolls down at their own pace (no jump-to-end). */
  private _readFrom() {
    setTimeout(() => {
      const el = this.scroller?.nativeElement; if (!el) return;
      const users = el.querySelectorAll('.row.user');
      const last = users[users.length - 1] as HTMLElement | undefined;
      if (!last) return;
      const top = last.getBoundingClientRect().top - el.getBoundingClientRect().top + el.scrollTop - 12;
      el.scrollTo({ top: Math.max(0, top), behavior: 'smooth' });
    }, 90);
  }

  // ── flow ─────────────────────────────────────────────────────────────────
  greet() {
    const d = this.data(); const p = d?.portfolio;
    const value = d ? d.parcels.reduce((s, x) => s + (x.base.sale_price || 0), 0) : 0;
    this.say(`Namaste! I’m ${this.bot.name}, your land & capital-gains guide. 🌿`);
    this.say(`I’ve read all **${p?.count ?? 0} of your plots** — about **${this.inr(value)}** in value, with **${this.inr(p?.total_gain)}** of gains sitting inside them. Everything below is FY 2025-26, on your real numbers.`);
    this.say('What would you like to do?');
    this.setMenu();
  }

  choose(c: Choice) {
    this.push({ role: 'user', text: c.label });
    this.options.set([]);
    this.typing.set(true);
    this._readFrom();                 // bring the question to the top; the answer flows in below
    setTimeout(() => { this.typing.set(false); this._route(c.key); }, 420);
  }

  private _route(key: string) {
    switch (key) {
      case 'own_tax': return this.ownTax();
      case 'own_optimise': return this.ownOptimise();
      case 'sell_plan': return this.startPlan();
      case 'nri': return this.nri();
      case 'see_plan': return this.showPlan();
      case 'menu': this.say('What else can I help with?'); return this.setMenu();
      default: return this.setMenu();
    }
  }

  // 1️⃣ Tax on all the land we already own (good UI) + one follow-up to optimise
  private ownTax() {
    const d = this.data()!; const p = d.portfolio;
    this.say('Here’s the full picture on **every plot you own today** — long-term capital gains, FY 2025-26, on your real numbers:');
    this.card('parcels', d.parcels);
    this.say(`Sold as they stand today, together that’s a gain of **${this.inr(p.total_gain)}** and a tax of about **${this.inr(p.tax_now)}**.`);
    this.say('There’s one clean, legal way to bring this down — **registering some plots in a resident family member’s name** before you sell.');
    this.options.set([
      { key: 'own_optimise', label: `✨ Optimise — split into another name · save up to ${this.inr(p.saving_max)}` },
      { key: 'menu', label: '↩ Back to menu' },
    ]);
  }

  // follow-up to (1): the exact plot-by-plot reshuffle between family members
  private ownOptimise() {
    const p = this.data()!.portfolio;
    this.say('Here’s the tax-smartest name to register **each plot** in before you sell — it all comes down to whose name it’s in when it sells:');
    this.card('reshuffle');
    this.say(`Do this across every plot and your tax falls from **${this.inr(p.tax_now)}** to about **${this.inr(p.tax_best)}** — a saving of **${this.inr(p.saving_max)}**. 🚫 Never gift to your **spouse (Maha)**: Sec 64 clubs the gain straight back to you, so it saves nothing.`);
    this.options.set([{ key: 'menu', label: '↩ Back to menu' }]);
  }

  // 3️⃣ Save tax on land as an NRI
  private nri() {
    const p = this.data()!.portfolio;
    this.say('As an **NRI (Ramprasad)** the rules are stricter — flat **12.5%**, *no indexation*, and the buyer deducts heavy TDS up front. These are the levers that legally cut it, most powerful first:');
    this.card('levers');
    this.say(`Put together — a tax-free gift to a **resident adult son** + **Sec 54F** or **54EC bonds** + **spreading sales across years** — takes your tax from **${this.inr(p.tax_now)}** down toward **${this.inr(p.tax_best)}**.`);
    this.options.set([{ key: 'menu', label: '↩ Back to menu' }]);
  }

  // called from the profiles card
  setResidency(name: string, residency: Residency) {
    this.api.setProfile(name, { residency }).subscribe({
      next: () => {
        this.profiles.update(list => list.map(p => p.name === name ? { ...p, residency } : p));
        // reload the analysis so numbers reflect the change
        this.api.land().subscribe({ next: d => this.data.set(d) });
        this.push({ role: 'user', text: `Set ${name} → ${residency.toUpperCase()}` });
        this._readFrom();
        this.typing.set(true);
        setTimeout(() => {
          this.typing.set(false);
          this.say(`Done — **${name}** is now **${residency === 'nri' ? 'a Non-Resident (NRI)' : residency === 'rnor' ? 'RNOR' : 'a Resident'}**. I’ve updated every calculation.`);
          this.card('profiles', {});
        }, 380);
      },
      error: () => this.say('Hmm, I couldn’t save that just now.'),
    });
  }

  // ════════════════════════════════════════════════════════════════════════
  //  "Plan a sale" — select parcels → live hero → listen → deep-calc best plan
  // ════════════════════════════════════════════════════════════════════════
  selected = signal<Set<string>>(new Set());
  regValues = signal<Record<string, number>>({});
  prefs = signal<PlanPrefs>({ transfer_ok: false, sell_fast: false, bonds_amount: 0, house_amount: 0 });
  prefsTouched = signal(false);
  plan = signal<SalePlan | null>(null);
  planning = signal(false);
  houseCounts = signal<Record<string, number>>({});

  /** every parcel with the base numbers the picker needs */
  planRows = computed(() => (this.data()?.parcels || [])
    .filter(p => p.base.term === 'long' && p.base.tax != null)
    .map(p => ({ name: p.base.asset!, owner: p.base.owner!, sale: p.base.sale_price,
                 cost: p.base.cost, gain: p.base.gain, tax: p.base.tax! })));

  private profByName = computed<Record<string, TaxProfile>>(() => {
    const m: Record<string, TaxProfile> = {};
    for (const p of this.profiles()) m[(p.name || '').toLowerCase()] = p;
    return m;
  });

  /** which levers can actually improve the tax for the CURRENT selection — so we
   *  never ask a question whose answer can't change anything. */
  saleLevers = computed(() => {
    const rows = this.selectedRows(); const pb = this.profByName(); const hc = this.houseCounts();
    let ownerNri = false, ownerCan54F = false;
    for (const o of new Set(rows.map(r => r.owner))) {
      const p = pb[(o || '').toLowerCase()];
      if ((p?.residency) === 'nri') ownerNri = true;
      if ((hc[o] ?? 9) <= 1) ownerCan54F = true;
    }
    const residentAdultChild = this.profiles().some(p =>
      p.residency === 'resident' && (p.relation === 'son' || p.relation === 'daughter') && !p.is_minor);
    const sonCan54F = this.profiles().some(p =>
      p.residency === 'resident' && (p.relation === 'son' || p.relation === 'daughter') && !p.is_minor
      && (hc[p.name] ?? 9) <= 1);
    return {
      bonds: this.liveGain() > 0,                    // 54EC only matters if there's a gain
      transfer: ownerNri && residentAdultChild,      // gifting only helps if owner is NRI & a resident child exists
      reinvest: ownerCan54F || sonCan54F,            // 54F needs a ≤1-house seller (owner, or a son via gift)
    };
  });

  isSel(name: string): boolean { return this.selected().has(name); }
  toggleParcel(name: string) {
    this.selected.update(s => { const n = new Set(s); n.has(name) ? n.delete(name) : n.add(name); return n; });
  }
  selectAllParcels() { this.selected.set(new Set(this.planRows().map(r => r.name))); }
  regValue(name: string): number {
    const rv = this.regValues()[name];
    return rv != null ? rv : (this.planRows().find(r => r.name === name)?.sale ?? 0);
  }
  setReg(name: string, v: any) {
    const n = Number(v) || 0;
    this.regValues.update(m => ({ ...m, [name]: n }));
  }

  /** live hero — updates the moment you tick a parcel (tax = sell-now, no planning) */
  private selectedRows = computed(() => this.planRows().filter(r => this.selected().has(r.name)));
  liveCash = computed(() => this.selectedRows().reduce((s, r) => s + this.regValue(r.name), 0));
  liveGain = computed(() => this.selectedRows().reduce((s, r) => s + Math.max(0, this.regValue(r.name) - r.cost), 0));
  liveTax = computed(() => {
    // sell-now tax scales with the registered value vs the recorded gain (rough live figure)
    return this.selectedRows().reduce((s, r) => {
      const g = Math.max(0, this.regValue(r.name) - r.cost);
      const eff = r.gain > 0 ? r.tax / r.gain : 0;      // effective rate from the full analysis
      return s + g * eff;
    }, 0);
  });
  selCount = computed(() => this.selected().size);

  /** most you could put into 54EC bonds across the selection (₹50L per parcel) */
  maxBonds = computed(() =>
    this.selectedRows().reduce((s, r) => s + Math.min(5_000_000, Math.max(0, this.regValue(r.name) - r.cost)), 0));
  /** can Sec 54F (house) actually be used given the CURRENT transfer choice?
   *  needs a seller who owns ≤1 house — the owner, or a resident son if transferring. */
  reinvestPossible = computed(() => {
    const hc = this.houseCounts();
    const ownerOk = [...new Set(this.selectedRows().map(r => r.owner))].some(o => (hc[o] ?? 9) <= 1);
    const sonOk = this.profiles().some(p =>
      p.residency === 'resident' && (p.relation === 'son' || p.relation === 'daughter') && !p.is_minor && (hc[p.name] ?? 9) <= 1);
    return ownerOk || (this.prefs().transfer_ok && sonOk);
  });
  /** most you could roll into a house (Sec 54F) — the whole proceeds of eligible plots */
  maxHouse = computed(() => this.reinvestPossible()
    ? this.selectedRows().reduce((s, r) => s + this.regValue(r.name), 0) : 0);

  private _sels() { return [...this.selected()].map(name => ({ name, registered_value: this.regValues()[name] })); }

  /** Indian comma-grouped display; empty for 0 so the placeholder shows */
  fmt(n: number): string { return n > 0 ? Math.round(n).toLocaleString('en-IN') : ''; }
  regDisplay(name: string): string { return Math.round(this.regValue(name)).toLocaleString('en-IN'); }
  setRegText(name: string, text: string) {
    const n = Number(String(text).replace(/[^\d]/g, '')) || 0;
    this.regValues.update(m => ({ ...m, [name]: n }));
  }

  // ══════════════════════════════════════════════════════════════════════════
  //  Sale planner — a chat flow: pick plots → one live plan card (sliders + tax)
  // ══════════════════════════════════════════════════════════════════════════
  wizBusy = signal(false);
  houseAmt = signal(0);          // ₹ into a new house (Sec 54F)
  bondsAmt = signal(0);          // ₹ into 54EC bonds (kept minimal — it locks 5 yrs)

  /** cash you keep to spend = whatever isn't put into a house or bonds (before tax) */
  cashUse = computed(() => Math.max(0, this.liveCash() - this.houseAmt() - this.bondsAmt()));
  /** most you can put into bonds right now (₹50L/plot cap, and can't exceed what's left after the house) */
  wBondsMax = computed(() => Math.min(this.maxBonds(), Math.max(0, this.liveCash() - this.houseAmt())));

  /** THE headline answer: whose name to register in for the least tax (from the live plan) */
  transferTo = computed(() => {
    const pl = this.plan(); if (!pl) return [] as string[];
    return [...new Set(pl.parcels.filter(p => p.via_gift).map(p => p.seller))];
  });

  private _resetFlow() { this.plan.set(null); this.houseAmt.set(0); this.bondsAmt.set(0); this.wizBusy.set(false); }

  startPlan() {
    this._resetFlow();
    this.say('Let’s sell the tax-smart way. **Which lands are you selling?** Tick the plots below; I’ll total the cash as you go.');
    this.card('select');
    this.options.set([{ key: 'see_plan', label: '✓ Show the best plan' }, { key: 'menu', label: '↩ Menu' }]);
  }

  showPlan() {
    if (!this.selected().size) {
      this.say('Pick at least one plot first 🙂'); return this.options.set([{ key: 'see_plan', label: '✓ Show the best plan' }, { key: 'menu', label: '↩ Menu' }]);
    }
    // default = save the MOST tax: everything into a house (Sec 54F), bonds at zero
    this.houseAmt.set(this.liveCash()); this.bondsAmt.set(0);
    this.say('Here’s the tax-smartest way to sell. **Drag the three sliders** to trade cash against a house or bonds — the tax updates live.');
    this.card('plan');
    this.options.set([{ key: 'sell_plan', label: '↩ Change plots' }, { key: 'menu', label: '↩ Menu' }]);
    this._recompute();
  }

  /** a slice's share of the total proceeds, for the allocation bar. */
  pct(v: number): number { const t = this.liveCash(); return t > 0 ? Math.round((v / t) * 100) : 0; }

  private _num(v: any) { return Math.round(Number(String(v).replace(/[^\d]/g, '')) || 0); }
  setHouse(v: any) { this.houseAmt.set(Math.max(0, Math.min(this.liveCash() - this.bondsAmt(), this._num(v)))); this._recompute(); }
  setBonds(v: any) { this.bondsAmt.set(Math.max(0, Math.min(this.wBondsMax(), this._num(v)))); this._recompute(); }
  setCash(v: any) {                       // cash is the balancer → dragging it moves the house money
    const c = Math.max(0, Math.min(this.liveCash() - this.bondsAmt(), this._num(v)));
    this.houseAmt.set(this.liveCash() - this.bondsAmt() - c); this._recompute();
  }

  private _wtimer: any = null;
  private _recompute() {
    const prefs: PlanPrefs = { transfer_ok: this.saleLevers().transfer, sell_fast: false,
                               bonds_amount: this.bondsAmt(), house_amount: this.houseAmt() };
    this.prefs.set(prefs);
    clearTimeout(this._wtimer);
    this._wtimer = setTimeout(() => {
      this.wizBusy.set(true);
      this.api.plan(this._sels(), prefs).subscribe({
        next: pl => { this.plan.set(pl); this.wizBusy.set(false); },
        error: () => { this.wizBusy.set(false); },
      });
    }, 220);
  }

  // ── per-land reshuffle: which family member to register each plot in ──────
  resLabel(r: string | null | undefined): string { return r === 'nri' ? 'NRI' : r === 'rnor' ? 'RNOR' : 'Resident'; }

  /** the person a scenario would put the sale in (owner for own-name levers, the giftee for a gift). */
  private _who(scen: TaxScenario, owner: string): string {
    if (scen.for_owner) return owner;
    const k = scen.key || '';
    if (k.startsWith('gift54F_')) return k.slice('gift54F_'.length);
    if (k.startsWith('gift_')) return k.slice('gift_'.length);
    return owner;
  }
  /** plain-English "why this is the tax-smartest move" for a scenario. */
  private _why(scen: TaxScenario, who: string, owner: string, residency: string): string {
    const k = scen.key || '';
    const nriWhy = `${owner} is an NRI — a flat 12.5% with no indexation, and the buyer must deduct heavy TDS up front.`;
    if (k === 'sec54EC') return `The gain here is small, so parking ₹50 L of it in 54EC bonds clears the whole tax — no name change needed.`;
    if (k === 'sec54F_owner') return `${owner} owns ≤ 1 house, so putting the whole sale value into one house (Sec 54F) exempts the entire gain.`;
    if (k.startsWith('gift54F_')) return `${nriWhy} A tax-free gift to ${who} (a resident son who owns ≤ 1 house) lets him use Sec 54F, so the whole gain becomes exempt. The money ends up as a house.`;
    if (k.startsWith('gift_')) return `${nriWhy} Gift it to ${who} (resident son): he can use 20%-with-indexation and pays only 1% TDS instead of the NRI’s heavy TDS. Stays as cash.`;
    return scen.detail || scen.title;
  }

  /** saving on a single parcel = tax now − best-case tax. */
  saveOf(p: any): number {
    const now = p?.base?.tax ?? 0;
    const best = (p?.best ?? p?.base)?.tax ?? now;
    return Math.max(0, now - best);
  }

  /** for each long-term plot: the best-tax move (who to register it in) + a cash fallback. */
  reshuffleRows = computed(() => {
    const parcels = (this.data()?.parcels || []).filter(p => p.base.term === 'long' && p.base.tax != null);
    return parcels.map(p => {
      const owner = p.base.owner!;
      const taxNow = p.base.tax!;
      const residency = p.base.residency;
      const best = p.best;                                   // overall lowest tax
      const cash = p.best_cash;                              // lowest tax that keeps the money as cash
      const who = best ? this._who(best, owner) : owner;
      const needsHouse = !!best && best.type === 'reinvest';
      const row: any = {
        name: p.base.asset, owner, residency, gain: p.base.gain, taxNow,
        who, toOwner: !best || !!best.for_owner, toTag: (best && !best.for_owner) ? 'resident son' : this.resLabel(residency),
        tax: best ? best.tax : taxNow, saved: best ? best.saved : 0,
        why: best ? this._why(best, who, owner, residency) : 'Already in the best name — no reshuffle needed.',
        needsHouse,
      };
      if (needsHouse && cash && best && cash.key !== best.key) {
        const altWho = this._who(cash, owner);
        row.altWho = altWho; row.altToOwner = !!cash.for_owner; row.altTax = cash.tax; row.altSaved = cash.saved;
      }
      return row;
    });
  });

  // ── card data helpers (used by the template) ─────────────────────────────
  parcelRows(): ParcelAnalysis[] { return this.data()?.parcels || []; }
  levers(): { icon: string; title: string; body: string }[] {
    const cap = this.inr(this.data()?.assumptions?.cii_sale ? 5000000 : 5000000);
    return [
      { icon: '🏦', title: '54EC capital-gains bonds', body: `Put up to ${cap} of the gain into NHAI/REC bonds within 6 months — that slice becomes tax-free. 5-year lock-in, ~5% interest.` },
      { icon: '🏠', title: 'Sec 54F — reinvest in one house', body: 'Put the whole sale value into a single residential house → the entire gain is exempt. Catch: you must own ≤ 1 house — so this works via a resident son, not for Ramprasad directly.' },
      { icon: '🏡', title: 'Sec 54 — house → another house', body: 'This is the one people ask about — but Sec 54 only applies when you sell a residential HOUSE (like your apartments), rolling that gain into another house. It does NOT cover land; for land the equivalent is Sec 54F above.' },
      { icon: '🌾', title: 'Sec 54B — farm land → farm land', body: 'If a plot is agricultural and was actually farmed, reinvesting the gain into other agricultural land within 2 years makes that gain exempt. Urban, non-agricultural plots don’t qualify.' },
      { icon: '👨‍👩‍👦', title: 'Gift to a resident adult child', body: 'Tax-free gift, no clubbing. They get the 20%-with-indexation option and only 1% TDS instead of the NRI’s heavy TDS.' },
      { icon: '📅', title: 'Spread sales across financial years', body: 'The ₹50 L bond limit resets every year, and smaller yearly gains keep the surcharge lower.' },
      { icon: '⏳', title: 'Hold for more than 24 months', body: 'Then it’s long-term (12.5%), not short-term (your slab, up to 30%). All your parcels already qualify.' },
      { icon: '📜', title: 'Use the 1-Apr-2001 fair value for old land', body: 'Land bought before 2001 can use its 2001 market value as cost — usually far higher than the old price, so the taxable gain shrinks. Get a registered valuer’s certificate.' },
    ];
  }
  assumptions(): string[] { return this.data()?.assumptions?.notes || []; }
}
