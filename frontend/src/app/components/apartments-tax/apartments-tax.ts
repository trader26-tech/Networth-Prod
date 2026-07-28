import { Component, ElementRef, EventEmitter, Input, OnInit, Output, ViewChild, computed, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import {
  TaxService, TaxProfile, TaxScenario, Residency, PlanPrefs, SalePlan,
  ApartmentTax, AptRental, AptFlat, RentPlan,
} from '../../services/tax.service';

interface Choice { key: string; label: string; }
interface Msg {
  role: 'bot' | 'user';
  text?: string;
  card?: 'flats' | 'rent-summary' | 'rent-owner' | 'rent-opt' | 'rent-levers'
       | 'compare' | 'reshuffle' | 'levers' | 'transfer' | 'profiles' | 'select' | 'plan';
  data?: any;
}

@Component({
  selector: 'app-apartments-tax',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './apartments-tax.html',
  styleUrl: './apartments-tax.scss',
})
export class ApartmentsTax implements OnInit {
  private api = inject(TaxService);
  @ViewChild('scroller') scroller?: ElementRef<HTMLDivElement>;
  @Input() embedded = false;
  @Output() closed = new EventEmitter<void>();
  @Output() toggleSize = new EventEmitter<void>();
  @Input() expanded = false;

  // Ashish's portrait (drop the file at frontend/public/ashish-apartment-tax.png).
  avatarImg = '/ashish-apartment-tax.png';
  imgOk = signal(true);
  inr = TaxService.inr;

  boldify(text: string | undefined): string {
    if (!text) return '';
    const esc = text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    return esc.replace(/\*\*(.+?)\*\*/g, '<b>$1</b>');
  }

  bot = { name: 'Ashish', role: 'Apartments, Rent & Capital-Gains Guide', emoji: '🏢' };

  data = signal<ApartmentTax | null>(null);
  profiles = signal<TaxProfile[]>([]);
  loading = signal(true);
  error = signal<string | null>(null);
  msgs = signal<Msg[]>([]);
  options = signal<Choice[]>([]);
  typing = signal(false);

  ngOnInit() {
    this.api.apartments().subscribe({
      next: d => { this.data.set(d); this.loading.set(false); this.greet(); },
      error: e => { this.loading.set(false); this.error.set(e?.error?.detail || 'Could not load your apartment tax data.'); },
    });
    this.api.profiles().subscribe({ next: r => this.profiles.set(r.profiles), error: () => {} });
  }

  // ── message helpers ──────────────────────────────────────────────────────
  private push(m: Msg) { this.msgs.update(a => [...a, m]); }
  private say(text: string) { this.push({ role: 'bot', text }); }
  private card(card: Msg['card'], data?: any) { this.push({ role: 'bot', card, data }); }
  private setMenu() {
    const p = this.data()?.portfolio;
    this.options.set([
      { key: 'rent', label: '🏠 Save tax on my rental income' },
      { key: 'sell', label: '💰 Sell a flat — the tax-smart way' },
      { key: 'name', label: '👨‍👩‍👦 Whose name should a flat be in?' },
      ...(this.hasNri() ? [{ key: 'nri', label: '🌏 NRI: tax on flats & rent' }] : []),
    ]);
  }
  private hasNri() { return this.profiles().some(p => p.residency === 'nri'); }

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
    const p = this.data()?.portfolio;
    this.say(`Hello! I'm ${this.bot.name}, your flats, rent & capital-gains guide. 🏢`);
    if (p && p.count > 0) {
      const bits: string[] = [`I've read all **${p.count} of your flats**`];
      if (p.let_out > 0) bits.push(`**${p.let_out}** are rented out (**${this.inr(p.monthly_rent)}/month**)`);
      this.say(`${bits.join(', ')}. Everything below is FY 2025-26, on your real numbers.`);
    }
    this.say('What would you like to do?');
    this.setMenu();
  }

  choose(c: Choice) {
    this.push({ role: 'user', text: c.label });
    this.options.set([]);
    this.typing.set(true);
    this._readFrom();
    setTimeout(() => { this.typing.set(false); this._route(c.key); }, 420);
  }

  private _route(key: string) {
    switch (key) {
      case 'rent': return this.rentOverview();
      case 'rent_opt': return this.rentOptimise();
      case 'sell': return this.saleOverview();
      case 'sell_plan': return this.startPlan();
      case 'name': return this.whoseName();
      case 'nri': return this.nri();
      case 'menu': this.say('What else can I help with?'); return this.setMenu();
      default: return this.setMenu();
    }
  }

  // ════════════════════════════════════════════════════════════════════════
  //  1️⃣  RENTAL INCOME — the everyday saver
  // ════════════════════════════════════════════════════════════════════════
  private rentOverview() {
    const d = this.data()!; const rentals = d.rentals || [];
    if (!rentals.length) {
      this.say('None of your flats are marked as rented right now. Add a **monthly rent** on the Apartments page and I can show the tax on it and how to bring it down.');
      return this.options.set([{ key: 'menu', label: '↩ Back to menu' }]);
    }
    this.say('Here\'s your **rental income**, FY 2025-26. Rent is taxed under **“Income from House Property”** — and it comes with a generous automatic deduction.');
    this.card('rent-summary', d.portfolio);
    this.say('Every rupee of rent first gets a **flat 30% standard deduction (Sec 24a)** — no bills needed — so only 70% is ever taxable. On top of that, **home-loan interest** and **municipal tax** are fully deductible on a let-out flat. Here\'s each owner\'s breakdown:');
    for (const r of rentals) this.card('rent-owner', r);
    this.say('💡 The tax above assumes the rent is that person\'s **only** income — with the 30% deduction and the **₹12 lakh rebate**, small rents often come to **₹0 on their own**. Stacked on a salary, it\'s taxed at that slab. Set **your other income** in the next step for the real figure.');
    this.say('Want to see how much a home loan, or splitting a flat into a family member\'s name, would save?');
    this.options.set([
      { key: 'rent_opt', label: '✨ Show me how to pay less rent-tax' },
      { key: 'menu', label: '↩ Back to menu' },
    ]);
  }

  // ── the live rent optimiser ──────────────────────────────────────────────
  rentOwner = signal<string>('');
  roLoan = signal(0);
  roMunicipal = signal(0);
  roOther = signal(0);
  roRegime = signal<'new' | 'old'>('new');
  roSplitTo = signal<string | null>(null);
  roSplitShare = signal(0);
  rentPlan = signal<RentPlan | null>(null);
  rentBusy = signal(false);

  rentOwners = computed(() => (this.data()?.rentals || []).map(r => r.owner));
  private curRental = computed<AptRental | undefined>(() =>
    (this.data()?.rentals || []).find(r => r.owner === this.rentOwner()));
  /** the "before" figure — full rent to the owner at the SAME other-income & regime, no
   *  levers. Comes from the plan so the before→after delta isolates the loan/split saving. */
  rentWas = computed(() => this.rentPlan()?.no_lever_tax ?? this.curRental()?.baseline_tax ?? 0);
  /** resident adults (not the owner, not the owner's spouse/minor) a share can go to */
  splitTargets = computed<TaxProfile[]>(() => {
    const owner = (this.rentOwner() || '').toLowerCase();
    const ownerP = this.profiles().find(p => (p.name || '').toLowerCase() === owner);
    const spouse = (ownerP?.spouse || '').toLowerCase();
    return this.profiles().filter(p =>
      p.residency === 'resident' && !p.is_minor
      && (p.name || '').toLowerCase() !== owner
      && (p.name || '').toLowerCase() !== spouse
      && (p.spouse || '').toLowerCase() !== owner);
  });

  private rentOptimise() {
    const rentals = this.data()!.rentals;
    this.rentOwner.set(rentals[0]?.owner || '');
    this.roLoan.set(0); this.roMunicipal.set(0);
    this.roOther.set(this.curRental()?.other_income || 0);
    this.roRegime.set('new'); this.roSplitTo.set(null); this.roSplitShare.set(0);
    this.rentPlan.set(null);
    this.say('Let\'s bring your rent-tax down. Add a **home-loan interest** figure if you have a loan on the flat, pick who could **co-own** it, and I\'ll recompute the tax live.');
    this.card('rent-opt');
    this.card('rent-levers');
    this.options.set([{ key: 'menu', label: '↩ Back to menu' }]);
    this._recomputeRent();
  }

  pickRentOwner(name: string) {
    this.rentOwner.set(name);
    this.roOther.set(this.curRental()?.other_income || 0);
    this.roSplitTo.set(null); this.roSplitShare.set(0);
    this._recomputeRent();
  }
  setRegime(r: 'new' | 'old') { this.roRegime.set(r); this._recomputeRent(); }
  pickSplit(name: string | null) {
    this.roSplitTo.set(name);
    if (name && this.roSplitShare() === 0) this.roSplitShare.set(0.5);
    if (!name) this.roSplitShare.set(0);
    this._recomputeRent();
  }
  setSplitShare(v: any) { this.roSplitShare.set(Math.max(0, Math.min(1, (Number(v) || 0) / 100))); this._recomputeRent(); }
  private _numeric(v: any) { return Math.max(0, Math.round(Number(String(v).replace(/[^\d]/g, '')) || 0)); }
  setLoan(v: any) { this.roLoan.set(this._numeric(v)); this._recomputeRent(); }
  setMunicipal(v: any) { this.roMunicipal.set(this._numeric(v)); this._recomputeRent(); }
  setOther(v: any) { this.roOther.set(this._numeric(v)); this._recomputeRent(); }
  fmt(n: number): string { return n > 0 ? Math.round(n).toLocaleString('en-IN') : ''; }

  private _rtimer: any = null;
  private _recomputeRent() {
    clearTimeout(this._rtimer);
    this._rtimer = setTimeout(() => {
      this.rentBusy.set(true);
      this.api.rentPlan({
        owner: this.rentOwner(),
        other_income: this.roOther(),
        loan_interest: this.roLoan(),
        municipal_tax: this.roMunicipal(),
        regime: this.roRegime(),
        co_owner: this.roSplitTo(),
        co_owner_share: this.roSplitShare(),
      }).subscribe({
        next: r => { this.rentPlan.set(r); this.rentBusy.set(false); },
        error: () => this.rentBusy.set(false),
      });
    }, 220);
  }

  rentLevers(): { icon: string; title: string; body: string }[] {
    return [
      { icon: '📉', title: '30% standard deduction (Sec 24a)', body: 'Automatic on every let-out flat — 30% of the rent (after municipal tax) is deducted with no proof or bills. Only 70% is ever taxable.' },
      { icon: '🏦', title: 'Home-loan interest (Sec 24b)', body: 'On a let-out flat the FULL interest is deductible (no ₹2 lakh cap that applies to a self-occupied home). A loan can wipe out the rent-tax entirely.' },
      { icon: '🧾', title: 'Municipal / property tax', body: 'Property tax you actually pay in the year is deducted before the 30% even applies.' },
      { icon: '👨‍👩‍👦', title: 'Split into a resident family member\'s name', body: 'Co-own with a low-income resident adult (an adult child) so their share of the rent is taxed in a lower slab or their basic exemption. A spouse or minor doesn\'t work — the income is clubbed back (Sec 64).' },
      { icon: '⚖️', title: 'Old vs new regime', body: 'The new regime has lower slabs but blocks setting a house-property loss against other income; the old regime allows it (up to ₹2 lakh). I compare both for you.' },
      { icon: '🌏', title: 'NRI: lower TDS (Form 13)', body: 'Rent to an NRI has TDS at ~30% deducted by the tenant. A lower-deduction certificate (Form 13) cuts that to the real tax, and the 30% deduction & interest still apply.' },
    ];
  }

  // ════════════════════════════════════════════════════════════════════════
  //  2️⃣  SELLING A FLAT — capital gains (Sec 54)
  // ════════════════════════════════════════════════════════════════════════
  private saleOverview() {
    const d = this.data()!; const p = d.portfolio;
    const longFlats = d.flats.filter(f => f.base.term === 'long' && f.base.tax != null);
    if (!longFlats.length) {
      this.say('None of your flats show a long-term capital gain to plan yet (they need to be held over 24 months with a recorded value). Add a **current value** on the Apartments page and I\'ll work it out.');
      return this.options.set([{ key: 'menu', label: '↩ Back to menu' }]);
    }
    this.say('Here\'s the tax on **selling each flat today** — long-term capital gains, FY 2025-26:');
    this.card('flats', longFlats);
    this.say(`Sold as they stand, that\'s a gain of **${this.inr(p.total_gain)}** and a tax of about **${this.inr(p.tax_now)}**.`);
    this.say('The good news: a **flat is far easier to shelter than land**. Under **Sec 54** you only need to reinvest the **gain** (not the whole sale value) into another house — and there\'s **no limit** on how many houses you already own.');
    this.options.set([
      { key: 'sell_plan', label: `💰 Plan the sale — save up to ${this.inr(p.saving_max)}` },
      { key: 'menu', label: '↩ Back to menu' },
    ]);
  }

  // ── pick flats → live hero → 2 sliders (Sec 54 house + 54EC bonds) ──────────
  selected = signal<Set<string>>(new Set());
  regValues = signal<Record<string, number>>({});
  plan = signal<SalePlan | null>(null);
  houseAmt = signal(0);          // ₹ of GAIN reinvested into a new house (Sec 54)
  bondsAmt = signal(0);          // ₹ of gain into 54EC bonds
  wizBusy = signal(false);

  planRows = computed(() => (this.data()?.flats || [])
    .filter(f => f.base.term === 'long' && f.base.tax != null)
    .map(f => ({ name: f.base.asset!, owner: f.base.owner!, sale: f.base.sale_price,
                 cost: f.base.cost, gain: f.base.gain, tax: f.base.tax! })));

  isSel(name: string): boolean { return this.selected().has(name); }
  toggleParcel(name: string) {
    this.selected.update(s => { const n = new Set(s); n.has(name) ? n.delete(name) : n.add(name); return n; });
    // ticking a flat rebuilds the plan live in the SAME screen (max-shelter default)
    if (this.selCount()) { this.houseAmt.set(this.liveGain()); this.bondsAmt.set(0); this._recompute(); }
    else this.plan.set(null);
  }
  regValue(name: string): number {
    const rv = this.regValues()[name];
    return rv != null ? rv : (this.planRows().find(r => r.name === name)?.sale ?? 0);
  }
  regDisplay(name: string): string { return Math.round(this.regValue(name)).toLocaleString('en-IN'); }
  setRegText(name: string, text: string) {
    const n = Number(String(text).replace(/[^\d]/g, '')) || 0;
    this.regValues.update(m => ({ ...m, [name]: n }));
    // gain changed → keep sliders valid and refresh the plan live
    this.houseAmt.set(Math.min(this.houseAmt(), Math.max(0, this.liveGain() - this.bondsAmt())));
    if (this.selCount()) this._recompute();
  }

  private selectedRows = computed(() => this.planRows().filter(r => this.selected().has(r.name)));
  liveCash = computed(() => this.selectedRows().reduce((s, r) => s + this.regValue(r.name), 0));
  liveGain = computed(() => this.selectedRows().reduce((s, r) => s + Math.max(0, this.regValue(r.name) - r.cost), 0));
  liveTax = computed(() => this.selectedRows().reduce((s, r) => {
    const g = Math.max(0, this.regValue(r.name) - r.cost);
    const eff = r.gain > 0 ? r.tax / r.gain : 0;
    return s + g * eff;
  }, 0));
  selCount = computed(() => this.selected().size);
  /** most gain you can park in 54EC bonds — ₹50L per flat */
  maxBonds = computed(() =>
    this.selectedRows().reduce((s, r) => s + Math.min(5_000_000, Math.max(0, this.regValue(r.name) - r.cost)), 0));

  cashInHand = computed(() => this.plan()?.totals?.cash ?? 0);
  transferTo = computed(() => {
    const pl = this.plan(); if (!pl) return [] as string[];
    return [...new Set(pl.parcels.filter(p => p.via_gift).map(p => p.seller))];
  });

  private _sels() { return [...this.selected()].map(name => ({ name, registered_value: this.regValues()[name] })); }

  // One self-contained screen (level 2): pick flats AND watch the tax-smart plan
  // build itself below — no "show plan" step, no loop back. Exit is the menu only.
  startPlan() {
    this.plan.set(null); this.houseAmt.set(0); this.bondsAmt.set(0); this.wizBusy.set(false);
    this.selected.set(new Set());
    this.say('Let\'s sell the tax-smart way. **Tick the flats you\'re selling** — your lowest-tax plan (Sec 54 + 54EC bonds) builds itself below, and you can fine-tune it with the sliders.');
    this.card('select');
    this.card('plan');
    this.options.set([{ key: 'menu', label: '↩ Back to menu' }]);
  }

  private _num(v: any) { return Math.round(Number(String(v).replace(/[^\d]/g, '')) || 0); }
  setHouse(v: any) { this.houseAmt.set(Math.max(0, Math.min(this.liveGain() - this.bondsAmt(), this._num(v)))); this._recompute(); }
  setBonds(v: any) { this.bondsAmt.set(Math.max(0, Math.min(Math.min(this.maxBonds(), this.liveGain() - this.houseAmt()), this._num(v)))); this._recompute(); }

  private _wtimer: any = null;
  private _recompute() {
    const prefs: PlanPrefs = { transfer_ok: false, sell_fast: false,
                               bonds_amount: this.bondsAmt(), house_amount: this.houseAmt() };
    clearTimeout(this._wtimer);
    this._wtimer = setTimeout(() => {
      this.wizBusy.set(true);
      this.api.apartmentSalePlan(this._sels(), prefs).subscribe({
        next: pl => { this.plan.set(pl); this.wizBusy.set(false); },
        error: () => this.wizBusy.set(false),
      });
    }, 220);
  }

  // ════════════════════════════════════════════════════════════════════════
  //  3️⃣ / 4️⃣  Whose name / NRI
  // ════════════════════════════════════════════════════════════════════════
  private whoseName() {
    const rows = this.reshuffleRows();
    if (!rows.length) {
      this.say('None of your flats show a long-term capital gain to plan yet (they need a recorded value and 24+ months held). Add a **current value** on the Apartments page and I\'ll tell you exactly whose name each should be in.');
      return this.options.set([{ key: 'menu', label: '↩ Back to menu' }]);
    }
    this.say('Straight answer first: **for a flat you rarely need to rename it.** Unlike land, **Sec 54** lets you — *even as an NRI* — reinvest just the gain into another house **in your own name** and pay **₹0**. A rename only pays off when you want the **cash** on an NRI-owned flat (a resident son gets 20%-indexation + 1% TDS instead of your heavy TDS u/s 195).');
    this.say('So here\'s **each flat, whose name to keep or move it to, and the tax** — on your real numbers. The grey card = keep your name & reinvest; the “Prefer cash?” line = the exact son to register it in if you\'d rather not buy another house:');
    this.card('reshuffle');
    this.say('One rule to remember: gifting to a **resident adult son** is tax-free and carries the cost & holding period over (nothing resets). Gifting to a **spouse or a minor is clubbed** (Sec 64) → it saves nothing, so I never suggest it.');
    this.say('Correct anyone\'s residency here and every recommendation above updates instantly:');
    this.card('profiles', {});
    this.options.set([{ key: 'menu', label: '↩ Back to menu' }]);
  }

  // ── per-flat "whose name" recommendation (mirrors Bhoomi's land reshuffle) ──
  resLabel(r: string | null | undefined): string { return r === 'nri' ? 'NRI' : r === 'rnor' ? 'RNOR' : 'Resident'; }

  /** the person a scenario would register the flat in (owner for own-name levers, the giftee for a gift). */
  private _who(scen: TaxScenario, owner: string): string {
    if (scen.for_owner) return owner;
    const k = scen.key || '';
    if (k.startsWith('gift54_')) return k.slice('gift54_'.length);
    if (k.startsWith('gift_')) return k.slice('gift_'.length);
    return owner;
  }
  /** plain-English "why this is the tax-smartest move" for a flat scenario. */
  private _why(scen: TaxScenario, who: string, owner: string, residency: string): string {
    const k = scen.key || '';
    const isNri = residency === 'nri';
    if (k === 'sec54EC') return `The gain here is small — parking up to ₹50 L of it in 54EC bonds clears the whole tax in ${owner}'s own name. No name change needed.`;
    if (k === 'sec54') {
      return isNri
        ? `No name change needed to reach ₹0: for a flat, Sec 54 lets even an NRI reinvest just the gain into another house and pay ₹0 tax. Gifting to a resident son only helps if you'd rather take the cash — see below.`
        : `${owner} is a resident and already gets the best treatment. Keep it in ${owner}'s name and reinvest the gain into one other house (Sec 54) → ₹0. A name change wouldn't lower it.`;
    }
    if (k.startsWith('gift54_')) return `${owner} is an NRI. Gift the flat to ${who} (resident son) first, then ${who} reinvests the gain under Sec 54 → ₹0 tax. The gift itself is tax-free and nothing resets.`;
    if (k.startsWith('gift_')) return `${owner} is an NRI paying a flat 12.5% with heavy TDS u/s 195. Gift it to ${who} (resident son): he gets the 20%-with-indexation option and only 1% TDS — the smart move when you want the cash rather than another house.`;
    return scen.detail || scen.title;
  }

  /** for each long-term flat: the best-tax move (whose name) + a keep-the-cash fallback. */
  reshuffleRows = computed(() => {
    const flats = (this.data()?.flats || []).filter(f => f.base.term === 'long' && f.base.tax != null);
    return flats.map(f => {
      const owner = f.base.owner!;
      const taxNow = f.base.tax!;
      const residency = f.base.residency;
      const best = f.best;                        // overall lowest tax
      const cash = f.best_cash;                   // lowest tax that keeps the money as cash
      const who = best ? this._who(best, owner) : owner;
      const needsHouse = !!best && best.type === 'reinvest';
      const row: any = {
        name: f.base.asset, owner, residency, gain: f.base.gain, taxNow,
        who, toOwner: !best || !!best.for_owner,
        toTag: (best && !best.for_owner) ? 'resident son' : this.resLabel(residency),
        tax: best ? best.tax : taxNow, saved: best ? best.saved : 0,
        why: best ? this._why(best, who, owner, residency) : 'Already in the best name — no change needed.',
        needsHouse,
      };
      if (needsHouse && cash && best && cash.key !== best.key) {
        row.altWho = this._who(cash, owner); row.altToOwner = !!cash.for_owner;
        row.altTax = cash.tax; row.altSaved = cash.saved;
      }
      return row;
    });
  });

  private nri() {
    const p = this.data()!.portfolio;
    this.say('As an **NRI**, both selling and renting are taxed harder — but there are solid, legal ways to cut it right down.');
    this.say('**On a sale:** flat 12.5% (no indexation) and the buyer must deduct TDS u/s 195 up front. Levers:');
    this.card('levers');
    this.say(`Put together — a tax-free gift to a **resident adult son** + **Sec 54** (reinvest the gain) or **54EC bonds** — can take the sale tax from **${this.inr(p.tax_now)}** down toward **${this.inr(p.tax_best)}**.`);
    this.say('**On rent:** the tenant deducts ~30% TDS, but you still get the 30% standard deduction and full loan interest — file a return (or a Form 13 lower-deduction certificate) to claim the balance back.');
    this.options.set([{ key: 'menu', label: '↩ Back to menu' }]);
  }

  setResidency(name: string, residency: Residency) {
    this.api.setProfile(name, { residency }).subscribe({
      next: () => {
        this.profiles.update(list => list.map(p => p.name === name ? { ...p, residency } : p));
        this.api.apartments().subscribe({ next: d => this.data.set(d) });
        this.push({ role: 'user', text: `Set ${name} → ${residency.toUpperCase()}` });
        this._readFrom();
        this.typing.set(true);
        setTimeout(() => {
          this.typing.set(false);
          this.say(`Done — **${name}** is now **${residency === 'nri' ? 'a Non-Resident (NRI)' : residency === 'rnor' ? 'RNOR' : 'a Resident'}**. I\'ve updated every calculation.`);
          this.card('profiles', {});
        }, 380);
      },
      error: () => this.say('Hmm, I couldn\'t save that just now.'),
    });
  }

  // ── card helpers used by the template ────────────────────────────────────
  levers(): { icon: string; title: string; body: string }[] {
    return [
      { icon: '🏠', title: 'Sec 54 — reinvest the gain in another flat', body: 'Put just the capital GAIN (not the whole sale value) into one residential house → the entire gain is exempt, ₹0 tax. No limit on other houses you own. Up to two houses once, if the gain ≤ ₹2 crore.' },
      { icon: '🏦', title: '54EC capital-gains bonds', body: 'Park up to ₹50 lakh of the gain in NHAI/REC bonds within 6 months — that slice becomes tax-free. 5-year lock-in, ~5% interest.' },
      { icon: '👨‍👩‍👦', title: 'Gift to a resident adult child', body: 'Tax-free gift, no clubbing. They get the 20%-with-indexation option and only 1% TDS instead of the NRI\'s heavy TDS u/s 195.' },
      { icon: '📅', title: 'Spread sales across financial years', body: 'The ₹50 L bond limit resets every year, and smaller yearly gains keep the surcharge lower.' },
      { icon: '⏳', title: 'Hold for more than 24 months', body: 'Then it\'s long-term (12.5%), not short-term at your slab (up to 30%).' },
    ];
  }
  assumptions(): string[] { return this.data()?.assumptions?.notes || []; }
}
