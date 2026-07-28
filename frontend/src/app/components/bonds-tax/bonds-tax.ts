import { Component, ElementRef, EventEmitter, Input, OnInit, Output, ViewChild, computed, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import {
  TaxService, TaxProfile, Residency,
  BondTax, BondOwnerTax, BondIncomePlan,
} from '../../services/tax.service';

interface Choice { key: string; label: string; }
interface Msg {
  role: 'bot' | 'user';
  text?: string;
  card?: 'portfolio' | 'owners' | 'owner-detail' | 'opt' | 'opt-levers'
       | 'levers' | 'cg' | 'tds' | 'compare' | 'profiles';
  data?: any;
}

@Component({
  selector: 'app-bonds-tax',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './bonds-tax.html',
  styleUrl: './bonds-tax.scss',
})
export class BondsTax implements OnInit {
  private api = inject(TaxService);
  @ViewChild('scroller') scroller?: ElementRef<HTMLDivElement>;
  @Input() embedded = false;
  @Output() closed = new EventEmitter<void>();
  @Output() toggleSize = new EventEmitter<void>();
  @Input() expanded = false;

  // Bandhan's portrait (drop the file at frontend/public/bandhan-bonds-tax.png).
  avatarImg = '/bandhan-bonds-tax.png';
  imgOk = signal(true);
  inr = TaxService.inr;

  boldify(text: string | undefined): string {
    if (!text) return '';
    const esc = text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    return esc.replace(/\*\*(.+?)\*\*/g, '<b>$1</b>');
  }

  bot = { name: 'Bandhan', role: 'Bonds & Fixed-Income Tax Guide', emoji: '🛡️' };

  data = signal<BondTax | null>(null);
  profiles = signal<TaxProfile[]>([]);
  loading = signal(true);
  error = signal<string | null>(null);
  msgs = signal<Msg[]>([]);
  options = signal<Choice[]>([]);
  typing = signal(false);

  ngOnInit() {
    this.api.bonds().subscribe({
      next: d => { this.data.set(d); this.loading.set(false); this.greet(); },
      error: e => { this.loading.set(false); this.error.set(e?.error?.detail || 'Could not load your bond tax data.'); },
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
      { key: 'income', label: '💰 Pay less tax on my bond income' },
      { key: 'name', label: '👨‍👩‍👦 Whose name should a bond be in?' },
      { key: 'tds', label: '🧾 TDS & Form 15G/15H' },
      { key: 'cg', label: '📊 Tax on selling a bond (capital gains)' },
    ]);
  }

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
    this.say(`Hello! I'm ${this.bot.name}, your bonds & fixed-income tax guide. 🛡️`);
    if (p && p.count > 0) {
      const bits: string[] = [`I've read all **${p.count} of your bonds** (${this.inr(p.invested)} invested)`];
      if (p.total_interest > 0) bits.push(`they pay **${this.inr(p.total_interest)}/yr** in coupons`);
      this.say(`${bits.join(', ')}. Everything below is FY 2025-26, on your real numbers.`);
      this.card('portfolio', p);
      if (p.tax_now > 0) {
        this.say(`Right now that coupon income costs the family about **${this.inr(p.tax_now)}/yr** in tax — and I can show you how to bring it close to **${this.inr(p.tax_best)}**.`);
      } else if (p.taxable_interest > 0) {
        this.say(`On the incomes I have on file, those coupons fall under the **₹12 L rebate**, so the tax is **₹0** — but **${this.inr(p.taxable_interest)}/yr** is still *taxable* interest. Tell me each holder's real salary/income and I'll show what's actually owed and how to shield it.`);
      } else {
        this.say('All of your bond coupons are already **tax-free** — nothing owed. 🎉 I can still show you how to keep it that way as you add more.');
      }
    } else {
      this.say('I don\'t see any bonds yet. Add a few on the Bonds page and I\'ll show the tax on their coupons and exactly how to lower it.');
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
      case 'income': return this.incomeOverview();
      case 'income_opt': return this.incomeOptimise();
      case 'name': return this.whoseName();
      case 'tds': return this.tdsExplainer();
      case 'cg': return this.capitalGains();
      case 'menu': this.say('What else can I help with?'); return this.setMenu();
      default: return this.setMenu();
    }
  }

  // ════════════════════════════════════════════════════════════════════════
  //  1️⃣  BOND INCOME — coupon interest tax + the live optimiser
  // ════════════════════════════════════════════════════════════════════════
  private incomeOverview() {
    const d = this.data()!; const p = d.portfolio;
    if (!p.taxable_interest) {
      this.say('None of your bonds pay **taxable** interest right now — either they\'re tax-free PSU bonds or the coupons are small. Nothing to shelter here. 🎉');
      return this.options.set([{ key: 'name', label: '👨‍👩‍👦 Whose name should a bond be in?' }, { key: 'menu', label: '↩ Back to menu' }]);
    }
    this.say('Here\'s the tax on your **bond coupon income**, FY 2025-26. Interest is **“Income from Other Sources”** — stacked on the holder\'s other income and taxed at their slab.');
    this.card('owners', d.owners.filter(o => o.taxable_interest > 0));
    this.say(`Of your **${this.inr(p.total_interest)}/yr** in coupons, **${this.inr(p.taxfree_interest)}** is already tax-free (${(p.taxfree_pct * 100).toFixed(0)}%). The taxable **${this.inr(p.taxable_interest)}** is what we work on — and **${this.inr(p.tds)}** of it is taken up front as 10% TDS.`);
    this.say('The two biggest levers: **hold a bond in a lower-slab family member\'s name** (under the new regime, someone with total income ≤ ₹12 L pays *zero* tax), and **move into tax-free PSU bonds**. Let\'s try them on your numbers, live.');
    this.options.set([
      { key: 'income_opt', label: `✨ Show me how to save up to ${this.inr(p.saving_max)}` },
      { key: 'menu', label: '↩ Back to menu' },
    ]);
  }

  // ── the live income optimiser ──────────────────────────────────────────────
  optOwner = signal<string>('');
  optRegime = signal<'new' | 'old'>('new');
  optOther = signal(0);
  optSplitTo = signal<string | null>(null);
  optSplitShare = signal(0);
  optTaxfree = signal(0);
  incomePlan = signal<BondIncomePlan | null>(null);
  optBusy = signal(false);

  taxableOwners = computed<BondOwnerTax[]>(() => (this.data()?.owners || []).filter(o => o.taxable_interest > 0));
  private curOwner = computed<BondOwnerTax | undefined>(() =>
    (this.data()?.owners || []).find(o => o.owner === this.optOwner()));
  /** the owner's taxable coupon — the "was" base */
  optTaxable = computed(() => this.curOwner()?.taxable_interest ?? 0);
  optWas = computed(() => this.curOwner()?.tax_now ?? 0);

  /** resident adults (not the owner, not their spouse/minor) a share can go to */
  splitTargets = computed<TaxProfile[]>(() => {
    const owner = (this.optOwner() || '').toLowerCase();
    const ownerP = this.profiles().find(p => (p.name || '').toLowerCase() === owner);
    const spouse = (ownerP?.spouse || '').toLowerCase();
    return this.profiles().filter(p =>
      p.residency === 'resident' && !p.is_minor
      && (p.name || '').toLowerCase() !== owner
      && (p.name || '').toLowerCase() !== spouse
      && (p.spouse || '').toLowerCase() !== owner);
  });

  private incomeOptimise() {
    const owners = this.taxableOwners();
    const first = owners[0];
    this.optOwner.set(first?.owner || '');
    this.optRegime.set('new');
    this.optOther.set(first?.other_income || 0);
    this.optSplitTo.set(null); this.optSplitShare.set(0); this.optTaxfree.set(0);
    this.incomePlan.set(null);
    this.say('Let\'s bring your bond-income tax down. Pick who could **co-hold** the bonds, switch the **regime**, or move a slice into **tax-free bonds** — I\'ll recompute the tax live.');
    this.card('opt');
    this.card('opt-levers');
    this.options.set([{ key: 'menu', label: '↩ Back to menu' }]);
    this._recompute();
  }

  pickOwner(name: string) {
    this.optOwner.set(name);
    this.optOther.set(this.curOwner()?.other_income || 0);
    this.optSplitTo.set(null); this.optSplitShare.set(0); this.optTaxfree.set(0);
    this._recompute();
  }
  setRegime(r: 'new' | 'old') { this.optRegime.set(r); this._recompute(); }
  pickSplit(name: string | null) {
    this.optSplitTo.set(name);
    if (name && this.optSplitShare() === 0) this.optSplitShare.set(1);
    if (!name) this.optSplitShare.set(0);
    this._recompute();
  }
  setSplitShare(v: any) { this.optSplitShare.set(Math.max(0, Math.min(1, (Number(v) || 0) / 100))); this._recompute(); }
  private _numeric(v: any) { return Math.max(0, Math.round(Number(String(v).replace(/[^\d]/g, '')) || 0)); }
  setOther(v: any) { this.optOther.set(this._numeric(v)); this._recompute(); }
  setTaxfree(v: any) { this.optTaxfree.set(Math.max(0, Math.min(this.optTaxable(), this._numeric(v)))); this._recompute(); }
  fmt(n: number): string { return n > 0 ? Math.round(n).toLocaleString('en-IN') : ''; }

  private _timer: any = null;
  private _recompute() {
    clearTimeout(this._timer);
    this._timer = setTimeout(() => {
      this.optBusy.set(true);
      this.api.bondIncomePlan({
        owner: this.optOwner(),
        taxable_interest: this.optTaxable(),
        other_income: this.optOther(),
        regime: this.optRegime(),
        taxfree_switch: this.optTaxfree(),
        co_owner: this.optSplitTo(),
        co_owner_share: this.optSplitShare(),
      }).subscribe({
        next: r => { this.incomePlan.set(r); this.optBusy.set(false); },
        error: () => this.optBusy.set(false),
      });
    }, 220);
  }

  incomeLevers(): { icon: string; title: string; body: string }[] {
    return [
      { icon: '🏛️', title: 'Tax-free PSU bonds (Sec 10(15))', body: 'NHAI / REC / PFC / IRFC / IREDA / HUDCO tax-free bonds pay a coupon that is fully exempt — no tax and no TDS, ever. The cleanest way to earn bond income tax-free.' },
      { icon: '👨‍👩‍👦', title: 'Hold in a low-slab family member\'s name', body: 'Interest is taxed in the HOLDER\'s slab. Under the new regime a resident whose total income ≤ ₹12 L pays zero tax (Sec 87A rebate) — so a bond in an adult child\'s / parent\'s name can be tax-free. A spouse or minor is clubbed back (Sec 64) and saves nothing.' },
      { icon: '⚖️', title: 'New vs old regime', body: 'The new regime\'s ₹12 L rebate usually wins for interest income. The old regime only helps if you have big 80C/80D/loan deductions. I compare both for you.' },
      { icon: '🧾', title: 'Form 15G / 15H — stop the 10% TDS', body: 'If the holder\'s income is below the taxable limit, submitting Form 15G (under 60) or 15H (senior) to the issuer stops the 10% TDS — so the cash isn\'t locked up waiting for a refund.' },
      { icon: '📅', title: 'Spread maturities across financial years', body: 'A cumulative bond dumps all its interest in one year. Staggering maturities keeps each year\'s income lower — inside a lower slab or the ₹12 L rebate.' },
      { icon: '🏦', title: 'Zero-coupon / cumulative for deferral', body: 'No annual coupon means no yearly tax — the gain lands once, at maturity, and a listed one held >12 months is taxed at just 12.5% instead of your slab.' },
    ];
  }

  // ════════════════════════════════════════════════════════════════════════
  //  2️⃣  WHOSE NAME
  // ════════════════════════════════════════════════════════════════════════
  private whoseName() {
    const d = this.data()!;
    this.say('For a bond, **whose name it\'s in decides the tax on every coupon** — interest is taxed in the *holder\'s* slab, not yours.');
    this.card('compare', d.portfolio);
    this.say('A **tax-free gift to a resident adult child or parent** whose income is under **₹12 L** means their coupons are taxed at **₹0** (new-regime rebate), and they can file **Form 15G** so no TDS is even deducted. A gift to a **spouse or minor is clubbed** (Sec 64) — the income comes straight back to you, so it saves nothing.');
    this.card('profiles', {});
    this.say('Correct anyone\'s residency or income here — every number updates instantly:');
    this.options.set([
      { key: 'income_opt', label: '💰 Try the split on my numbers' },
      { key: 'menu', label: '↩ Back to menu' },
    ]);
  }

  // ════════════════════════════════════════════════════════════════════════
  //  3️⃣  TDS & 15G/15H
  // ════════════════════════════════════════════════════════════════════════
  private tdsExplainer() {
    const p = this.data()!.portfolio;
    this.say('**TDS on bond interest** — since April 2023, the issuer deducts **10% (Sec 193)** on the coupon of a *taxable* bond before it reaches you. Tax-free bonds have **no TDS**.');
    this.card('tds', p);
    this.say('TDS isn\'t an extra tax — it\'s a *prepayment*. You adjust it against your final bill and get any excess back as a refund. But the money is locked up till you file.');
    this.say('**To stop the TDS at source:** if the holder\'s total income is below the taxable limit, give the issuer **Form 15G** (under 60) or **Form 15H** (senior citizen) at the start of the year. No deduction, full cash in hand.');
    this.options.set([
      { key: 'income', label: '💰 See my income tax & how to cut it' },
      { key: 'menu', label: '↩ Back to menu' },
    ]);
  }

  // ════════════════════════════════════════════════════════════════════════
  //  4️⃣  CAPITAL GAINS ON SALE
  // ════════════════════════════════════════════════════════════════════════
  private capitalGains() {
    this.say('If you **hold a bond to maturity** it\'s simply redeemed at par — no capital gain (you were already taxed on the coupons). Capital-gains tax only appears if you **sell early on the exchange**.');
    this.card('cg', this.data()!.capital_gains);
    this.say('So the tax-smart moves are: hold **listed** bonds **over 12 months** so any exit gain is long-term at just **12.5%**; and if you must book a loss, use it to **set off a capital gain elsewhere** (shares, property).');
    this.options.set([
      { key: 'income', label: '💰 Back to bond-income tax' },
      { key: 'menu', label: '↩ Back to menu' },
    ]);
  }

  // ── residency / income editor ────────────────────────────────────────────
  setResidency(name: string, residency: Residency) {
    this.api.setProfile(name, { residency }).subscribe({
      next: () => {
        this.profiles.update(list => list.map(p => p.name === name ? { ...p, residency } : p));
        this.api.bonds().subscribe({ next: d => this.data.set(d) });
        this.push({ role: 'user', text: `Set ${name} → ${residency.toUpperCase()}` });
        this._readFrom();
        this.typing.set(true);
        setTimeout(() => {
          this.typing.set(false);
          this.say(`Done — **${name}** is now **${residency === 'nri' ? 'a Non-Resident (NRI)' : residency === 'rnor' ? 'RNOR' : 'a Resident'}**. I've updated every calculation.`);
          this.card('profiles', {});
        }, 380);
      },
      error: () => this.say('Hmm, I couldn\'t save that just now.'),
    });
  }

  setIncome(name: string, value: any) {
    const income = Math.max(0, Math.round(Number(String(value).replace(/[^\d]/g, '')) || 0));
    this.api.setProfile(name, { other_income: income }).subscribe({
      next: () => {
        this.profiles.update(list => list.map(p => p.name === name ? { ...p, other_income: income } : p));
        this.api.bonds().subscribe({ next: d => this.data.set(d) });
      },
      error: () => {},
    });
  }

  assumptions(): string[] { return this.data()?.assumptions?.notes || []; }
}
