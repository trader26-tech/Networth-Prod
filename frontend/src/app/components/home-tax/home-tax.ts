import { Component, ElementRef, EventEmitter, Input, Output, ViewChild, computed, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { DashboardService, Dashboard, Position } from '../../services/dashboard.service';

interface Choice { key: string; label: string; }
interface Msg { role: 'bot' | 'user'; text?: string; card?: 'sectors' | 'plan' | 'need' | 'restructure' | 'regime' | 'zerotax'; data?: any; }

/**
 * "Chanakya" — the whole-picture tax strategist on the home dashboard. Non-LLM,
 * rule-based, FY2025-26, and fully SELF-CONTAINED: every answer is granular and
 * references the user's actual assets (from `positions`) and family (owners) —
 * how much tax each sector costs, exactly how to cut it, and how to raise a
 * given amount of cash tax-efficiently. It does NOT delegate anywhere.
 */
@Component({
  selector: 'app-home-tax',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './home-tax.html',
  styleUrl: './home-tax.scss',
})
export class HomeTax {
  @Input() embedded = false;
  @Input() expanded = false;
  @Input() set dash(d: Dashboard | null) { this._d.set(d); if (d && !this._greeted) { this._greeted = true; this.greet(); } }
  @Output() closed = new EventEmitter<void>();
  @Output() toggleSize = new EventEmitter<void>();

  bot = { name: 'Chanakya', role: 'Whole-Picture Tax Strategist', emoji: '🧮' };
  avatarImg = '/chanakya-tax.png';
  imgOk = signal(true);

  // ── "About Chanakya" — tap his portrait to see what he knows & can do ───────
  aboutOpen = signal(false);
  toggleAbout() { this.aboutOpen.update(v => !v); }
  /** the data he reads before answering (grounds every number in your real book) */
  knows: { icon: string; text: string }[] = [
    { icon: '📊', text: 'Every asset & holding you own — value, cost and unrealised gain' },
    { icon: '💼', text: 'Your salary and its currency — KWD means NRI, so it isn’t taxed in India' },
    { icon: '🏠', text: 'Rent from your flats and land (after the 30% deduction)' },
    { icon: '💵', text: 'Dividends credited to each family member this year' },
    { icon: '🏦', text: 'Interest from your bonds & fixed deposits' },
    { icon: '📈', text: 'F&O and any other income' },
    { icon: '👥', text: 'Who owns what — the whole family and their tax slabs' },
  ];
  /** what he can do — each opens that tool directly */
  skills: { key: string; icon: string; title: string; desc: string }[] = [
    { key: 'regime', icon: '⚖️', title: 'Old vs New regime', desc: 'For each person: sums up salary, rent, dividends & bond interest, then shows the tax under each regime and which wins — NRI-aware.' },
  ];
  openSkill(key: string) {
    const s = this.skills.find(x => x.key === key);
    this.aboutOpen.set(false);
    if (s) this.choose({ key, label: s.title });
  }

  private _d = signal<Dashboard | null>(null);
  private _greeted = false;
  msgs = signal<Msg[]>([]);
  options = signal<Choice[]>([]);
  typing = signal(false);
  inr = DashboardService.inr;

  boldify(t: string | undefined): string {
    if (!t) return '';
    const e = t.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    return e.replace(/\*\*(.+?)\*\*/g, '<b>$1</b>');
  }

  // ── FY2025-26 income-tax engines (slab income; capital gains are separate) ──
  // NRI-aware: an NRI gets NEITHER the 87A rebate (nil-tax up to ₹12L new / ₹5L
  // old) NOR — unless they draw an Indian salary — the standard deduction. That
  // single difference often flips which regime wins, so both engines take it.
  private CESS = 0.04; private LTCG = 0.125;
  taxNew(gross: number, isNRI = false, hasSalary = true): number {
    const ti = Math.max(0, gross - (hasSalary ? 75000 : 0));
    if (!isNRI && ti <= 1200000) return 0;                    // 87A rebate — residents only
    const b: [number, number][] = [[400000, 0], [800000, .05], [1200000, .10], [1600000, .15], [2000000, .20], [2400000, .25], [Infinity, .30]];
    let tax = 0, prev = 0;
    for (const [cap, r] of b) { if (ti > prev) { tax += (Math.min(ti, cap) - prev) * r; prev = cap; } else break; }
    return Math.round(tax * (1 + this.CESS));
  }
  taxOld(taxable: number, isNRI = false): number {
    const ti = Math.max(0, taxable);
    if (!isNRI && ti <= 500000) return 0;                     // 87A rebate — residents only
    const b: [number, number][] = [[250000, 0], [500000, .05], [1000000, .20], [Infinity, .30]];
    let tax = 0, prev = 0;
    for (const [cap, r] of b) { if (ti > prev) { tax += (Math.min(ti, cap) - prev) * r; prev = cap; } else break; }
    return Math.round(tax * (1 + this.CESS));
  }

  // ── group the real positions into taxable sectors ──────────────────────────
  private CLASS_GROUP: Record<string, string> = {
    land: 'property', apartments: 'property', built: 'property',
    stocks: 'equity', gold: 'gold', bonds: 'debt', fd: 'debt', cash: 'cash', ulip: 'ulip',
  };
  private pos(): Position[] { return this._d()?.positions || []; }
  private groupOf(c: string): string { return this.CLASS_GROUP[(c || '').toLowerCase()] || 'other'; }

  /** biggest-gain single asset in a sector (for concrete "your X" references) */
  private topAsset(group: string): Position | null {
    return this.pos().filter(p => this.groupOf(p.asset_class) === group)
      .map(p => ({ p, g: p.value - (p.invested ?? p.value) }))
      .sort((a, b) => b.g - a.g)[0]?.p || null;
  }

  picture = computed(() => {
    const d = this._d();
    const salary = d?.salary?.annual_total || 0;
    const dividends = d?.dividends?.this_year || 0;
    const fno = Math.max(0, d?.fno?.booked || 0);
    const other = d?.other_income?.annual_total || 0;
    // rent = annual income from property; interest = annual income from debt (from positions)
    let rent = 0, interest = 0;
    for (const p of this.pos()) {
      const g = this.groupOf(p.asset_class);
      if (g === 'property') rent += (p.monthly_income || 0) * 12;
      else if (g === 'debt') interest += (p.monthly_income || 0) * 12;
    }
    const rentTaxable = Math.round(rent * 0.70);                     // 30% standard deduction
    const slabIncome = Math.round(salary + rentTaxable + dividends + interest + fno + other);
    const marginal = slabIncome > 2400000 ? .30 : slabIncome > 2000000 ? .25 : slabIncome > 1600000 ? .20
      : slabIncome > 1200000 ? .15 : slabIncome > 800000 ? .10 : slabIncome > 400000 ? .05 : 0;
    // per-sector asset value + unrealised gain
    const sectorVals: Record<string, { value: number; gain: number }> = {};
    for (const p of this.pos()) {
      const g = this.groupOf(p.asset_class);
      const s = sectorVals[g] || (sectorVals[g] = { value: 0, gain: 0 });
      s.value += p.value; s.gain += p.value - (p.invested ?? p.value);
    }
    return {
      netWorth: d?.net_worth || 0, unrealisedGain: d?.total_gain || 0,
      salary, rent, rentTaxable, dividends, interest, fno, other, slabIncome, marginal,
      incomeTax: this.taxNew(slabIncome), sectorVals,
    };
  });

  // ── conversation ───────────────────────────────────────────────────────────
  @ViewChild('scrollEl') scrollEl?: ElementRef<HTMLDivElement>;
  /** keep the newest message in view — the reply should never land off-screen */
  private scrollDown() {
    const el = this.scrollEl?.nativeElement;
    if (!el || typeof requestAnimationFrame === 'undefined') return;
    requestAnimationFrame(() => requestAnimationFrame(() => { el.scrollTop = el.scrollHeight; }));
  }
  private push(m: Msg) { this.msgs.update(a => [...a, m]); this.scrollDown(); }
  private say(t: string) { this.push({ role: 'bot', text: t }); }
  private card(card: Msg['card'], data?: any) { this.push({ role: 'bot', card, data }); }
  private menu() {
    this.options.set([
      { key: 'regime', label: '⚖️ Old vs New regime — which wins?' },
    ]);
  }

  greet() {
    const p = this.picture();
    this.say(`Namaste — I'm **${this.bot.name}**, your whole-picture tax strategist. 🧮`);
    this.say(`I've read **every asset you own and every rupee you earn**: net worth **${this.inr(p.netWorth)}**, income about **${this.inr(p.salary + p.rent + p.dividends + p.interest + p.fno + p.other)}/yr**, and **${this.inr(p.unrealisedGain)}** of unrealised gains. On this you're paying roughly **${this.inr(p.incomeTax)}** of income tax a year — let's cut it. Everything is FY 2025-26, on your real numbers.`);
    this.menu();
  }

  choose(c: Choice) {
    this.push({ role: 'user', text: c.label });
    this.options.set([]); this.typing.set(true);
    setTimeout(() => { this.typing.set(false); this.route(c.key); }, 360);
  }
  private route(k: string) {
    switch (k) {
      case 'regime': return this.regimeView();
      case 'zerotax': return this.zeroTaxView();
      case 'now': return this.taxNowView();
      case 'plan': return this.planView();
      case 'need': return this.needView();
      case 'restructure': return this.restructureView();
      case 'menu': this.say('What else shall we work on?'); return this.menu();
      default: return this.menu();
    }
  }
  private back(): Choice { return { key: 'menu', label: '↩ Menu' }; }

  // 0 ── Old vs New regime — per person, NRI-aware ────────────────────────────
  // Salary drawn in KWD (a foreign currency) ⇒ the earner is an NRI, so that
  // salary is NOT taxed in India — only their India-sourced rent, dividends and
  // bond interest are. We compare both regimes on each person's India-taxable
  // slab income (old = after assumed 80C+NPS+standard deductions; new = its own
  // ₹75k + wider slabs) and flag the winner. A land/flat SALE is capital gains at
  // a flat 12.5% under either regime, so it's shown separately, not in the choice.
  private isInr(c: string): boolean { return !c || c.toUpperCase() === 'INR'; }
  regimeRows() {
    const d = this._d();
    const salByP = new Map((d?.salary?.by_person || []).map(s => [s.person, s]));
    const divByP = new Map((d?.dividends?.by_person || []).map(s => [s.person, s.this_year || 0]));
    const fnoByP = new Map((d?.fno?.by_person || []).map(s => [s.person, Math.max(0, s.booked || 0)]));
    const othByP = new Map((d?.other_income?.by_person || []).map(s => [s.person, (s.monthly_inr || 0) * 12]));
    const names = new Set<string>();
    for (const s of d?.salary?.by_person || []) names.add(s.person);
    for (const p of this.pos()) if (p.owner) names.add(p.owner);
    for (const k of divByP.keys()) names.add(k);
    for (const k of othByP.keys()) names.add(k);

    const rows = [...names].filter(Boolean).map(name => {
      const sal = salByP.get(name);
      const isNRI = !!sal && (sal.currencies || []).some(c => !this.isInr(c));
      const salaryIndia = isNRI ? 0 : (sal?.annual_inr || 0);         // KWD salary → 0 in India
      const salaryForeign = isNRI ? (sal?.annual_inr || 0) : 0;
      // rent + interest + capital gains from this person's own assets
      let rent = 0, interest = 0, landGain = 0, aptGain = 0;
      for (const p of this.pos()) {
        if (p.owner !== name) continue;
        const g = this.groupOf(p.asset_class);
        if (g === 'property') {
          rent += (p.monthly_income || 0) * 12;
          const gain = p.value - (p.invested ?? p.value);
          if ((p.asset_class || '').toLowerCase() === 'land') landGain += gain; else aptGain += gain;
        } else if (g === 'debt') interest += (p.monthly_income || 0) * 12;
      }
      const rentTaxable = Math.round(rent * 0.70);                    // 30% standard deduction
      const dividends = divByP.get(name) || 0;
      const interestInc = Math.round(interest);
      const fno = fnoByP.get(name) || 0;
      const other = othByP.get(name) || 0;
      const slab = Math.round(salaryIndia + rentTaxable + dividends + interestInc + fno + other);
      const hasSalary = salaryIndia > 0;

      // capital gains if the land / flat were sold (LTCG 12.5% — regime-neutral)
      const cgGain = Math.max(0, landGain) + Math.max(0, aptGain);
      const cgTax = Math.round(Math.max(0, landGain + aptGain) * this.LTCG);

      // NEW regime (own ₹75k std deduction + rebate baked into taxNew)
      const newTax = this.taxNew(slab, isNRI, hasSalary);
      // OLD regime — assume the usual deductions are claimed
      const stdDed = hasSalary ? 50000 : 0;
      const ded80c = Math.min(150000, Math.max(0, slab - stdDed));
      const nps = (slab - stdDed - ded80c) > 0 ? 50000 : 0;
      const oldDed = stdDed + ded80c + nps;
      const oldTax = this.taxOld(slab - oldDed, isNRI);

      const winner: 'old' | 'new' | 'tie' = oldTax < newTax ? 'old' : newTax < oldTax ? 'new' : 'tie';
      // itemised India-taxable income build-up (only the sources they actually have)
      const income: { icon: string; label: string; amount: number }[] = [];
      if (salaryIndia > 0) income.push({ icon: '💼', label: 'Salary', amount: salaryIndia });
      if (rentTaxable > 0) income.push({ icon: '🏠', label: 'Rent (after 30% deduction)', amount: rentTaxable });
      if (dividends > 0) income.push({ icon: '💵', label: 'Dividends · stocks', amount: dividends });
      if (interestInc > 0) income.push({ icon: '🏦', label: 'Interest · bonds & FDs', amount: interestInc });
      if (fno > 0) income.push({ icon: '📊', label: 'F&O', amount: fno });
      if (other > 0) income.push({ icon: '➕', label: 'Other income', amount: other });
      return {
        name, isNRI, salaryIndia, salaryForeign, rent, rentTaxable, dividends,
        interest: interestInc, fno, other, slab, hasSalary, income,
        newTax, oldTax, oldDed, winner, best: Math.min(newTax, oldTax), saving: Math.abs(newTax - oldTax),
        landGain: Math.max(0, landGain), aptGain: Math.max(0, aptGain), cgGain, cgTax,
      };
    }).filter(r => r.slab > 0 || r.cgTax > 0 || r.salaryForeign > 0)
      .sort((a, b) => b.slab - a.slab);

    return {
      rows,
      familyNew: rows.reduce((s, r) => s + r.newTax, 0),
      familyOld: rows.reduce((s, r) => s + r.oldTax, 0),
      familyBest: rows.reduce((s, r) => s + r.best, 0),
    };
  }
  regimeView() {
    const data = this.regimeRows();
    if (!data.rows.length) {
      this.say('I don’t see any India-taxable income on record yet — add salary, rent, dividends or bond interest and I’ll compare the regimes on real numbers.');
      this.options.set([this.back()]);
      return;
    }
    this.card('regime', data);
    const better = data.familyOld < data.familyNew ? 'OLD' : data.familyNew < data.familyOld ? 'NEW' : 'either';
    const save = Math.abs(data.familyNew - data.familyOld);
    const nri = data.rows.filter(r => r.isNRI).map(r => r.name);
    const nriNote = nri.length
      ? ` ${nri.join(' & ')} draw${nri.length === 1 ? 's' : ''} salary in **KWD → NRI**, so that pay isn’t taxed in India — only their Indian rent, dividends & interest count.`
      : '';
    const verb = better === 'either' ? 'they come out level' : `the **${better}** regime is cheaper`;
    this.say(`Right now, across the family, ${verb}${save > 0 ? ` — about **${this.inr(save)}/yr** less` : ''}.${nriNote} Tap any person to open their full income build-up. A land/flat **sale** is a separate **12.5% LTCG either way**, so it doesn’t swing the choice.`);
    this.options.set([{ key: 'zerotax', label: '💡 How do I pay ZERO tax?' }, this.back()]);
  }

  // ── accordion: one person open at a time (keeps the whole card on screen) ────
  regimeOpen = signal<string | null>(null);
  toggleRegime(name: string) { this.regimeOpen.set(this.regimeOpen() === name ? null : name); this.calcOpen.set(null); }
  isRegimeOpen(name: string): boolean { return this.regimeOpen() === name; }

  /** short ₹ in lakh/crore for slab labels — "₹12 L", "₹1.22 Cr" */
  lk(n: number): string {
    const a = Math.abs(n);
    if (a >= 1e7) return '₹' + (n / 1e7).toFixed(a % 1e7 ? 2 : 0) + ' Cr';
    if (a >= 1e5) return '₹' + (n / 1e5).toFixed(a % 1e5 ? 2 : 0) + ' L';
    return '₹' + Math.round(n).toLocaleString('en-IN');
  }
  // the working is collapsed by default — opened per person, on demand
  calcOpen = signal<string | null>(null);
  calcRegime = signal<'new' | 'old'>('new');
  isCalcOpen(name: string): boolean { return this.calcOpen() === name; }
  openCalc(r: any) { this.calcRegime.set(r.oldTax < r.newTax ? 'old' : 'new'); this.calcOpen.set(r.name); }
  closeCalc() { this.calcOpen.set(null); }
  setCalcRegime(reg: 'new' | 'old') { this.calcRegime.set(reg); }

  /** Slab-by-slab derivation of the tax under a chosen regime, so the card can
   *  SHOW how e.g. ₹84,056 is built up — not just state it. */
  taxBreakdown(r: any, regime: 'new' | 'old') {
    const total = regime === 'old' ? r.oldTax : r.newTax;
    const deductions: { label: string; amount: number }[] = [];
    let taxable: number, bands: [number, number][], rebateLimit: number;
    if (regime === 'new') {
      if (r.hasSalary) deductions.push({ label: 'Standard deduction', amount: 75000 });
      taxable = Math.max(0, r.slab - (r.hasSalary ? 75000 : 0));
      bands = [[400000, 0], [800000, .05], [1200000, .10], [1600000, .15], [2000000, .20], [2400000, .25], [Infinity, .30]];
      rebateLimit = 1200000;
    } else {
      const std = r.hasSalary ? 50000 : 0;
      const c80 = Math.min(150000, Math.max(0, r.slab - std));
      const nps = (r.slab - std - c80) > 0 ? 50000 : 0;
      if (std) deductions.push({ label: 'Standard deduction', amount: std });
      if (c80) deductions.push({ label: '80C — EPF / PPF / ELSS', amount: c80 });
      if (nps) deductions.push({ label: 'NPS — 80CCD(1B)', amount: nps });
      taxable = Math.max(0, r.slab - std - c80 - nps);
      bands = [[250000, 0], [500000, .05], [1000000, .20], [Infinity, .30]];
      rebateLimit = 500000;
    }
    const rebate = !r.isNRI && taxable <= rebateLimit;
    const slabs: { label: string; rate: number; amount: number }[] = [];
    if (!rebate) {
      let prev = 0;
      for (const [cap, rate] of bands) {
        if (taxable <= prev) break;
        const upper = Math.min(taxable, cap);
        if (rate > 0) slabs.push({ label: `${this.lk(prev)} – ${this.lk(upper)}`, rate, amount: Math.round((upper - prev) * rate) });
        prev = cap;
      }
    }
    const taxBeforeCess = Math.round(slabs.reduce((s, x) => s + x.amount, 0));
    const cess = Math.max(0, total - taxBeforeCess);      // forced to reconcile to the header figure
    return { regime, isNRI: r.isNRI, grossTaxable: r.slab, deductions, taxable, rebate, rebateLimit,
             exemptUpto: bands[0][0], slabs, taxBeforeCess, cess, total };
  }

  // ── Family tax optimiser ────────────────────────────────────────────────────
  // Reshuffle income-producing assets onto the members who still have ₹12 L
  // rebate room — WITHOUT tipping anyone over it (which would create fresh tax).
  // An NRI's interest goes to NRE (tax-free) rather than to a relative. Returns
  // the concrete moves AND the tax each person is left paying afterwards.
  zeroTaxPlan() {
    const rows = this.regimeRows().rows;
    const people = rows.map(r => ({
      name: r.name, isNRI: r.isNRI, hasSalary: r.salaryIndia > 0,
      fixed: Math.round(r.salaryIndia + r.fno + r.other),   // stays put (job / F&O / misc)
      beforeTax: r.best, cap: r.isNRI ? 400000 : 1200000,   // 0-tax ceiling (NRI exemption / 87A)
      ownKept: 0, incoming: [] as any[], nre: [] as any[], releases: 0,
    }));
    const byName = new Map(people.map(p => [p.name, p]));
    const held = (p: any) => p.fixed + p.ownKept + p.incoming.reduce((s: number, m: any) => s + m.income, 0);
    const room = (p: any) => Math.max(0, p.cap - held(p));

    // gather shiftable assets; an NRI's interest → NRE (tax-free), tracked on them
    const assets: any[] = [];
    for (const r of rows) {
      const owned = this.pos().filter(p => p.owner === r.name);
      for (const p of owned.filter(p => this.groupOf(p.asset_class) === 'property' && (p.monthly_income || 0) > 0)) {
        const rent = (p.monthly_income || 0) * 12;
        assets.push({ owner: r.name, icon: '🏠', name: p.name, meta: `rent ${this.inr(rent)}/yr`, income: Math.round(rent * 0.7) });
      }
      const debts = owned.filter(p => this.groupOf(p.asset_class) === 'debt' && (p.monthly_income || 0) > 0);
      if (debts.length) {
        const interest = Math.round(debts.reduce((s, p) => s + (p.monthly_income || 0) * 12, 0));
        const val = debts.reduce((s, p) => s + p.value, 0);
        const nm = debts.length === 1 ? debts[0].name : `${debts.length} FDs & bonds`;
        if (r.isNRI) byName.get(r.name)!.nre.push({ name: nm, meta: `${this.inr(val)} · interest ${this.inr(interest)}/yr` });
        else assets.push({ owner: r.name, icon: '🏦', name: nm, meta: `interest ${this.inr(interest)}/yr`, income: interest });
      }
      if (r.dividends > 0) assets.push({ owner: r.name, icon: '💵', name: 'Dividend shares', meta: `₹${Math.round(r.dividends).toLocaleString('en-IN')}/yr`, income: Math.round(r.dividends) });
    }

    // biggest income first → keep with a resident owner who has room, else drop on
    // the resident with the most remaining rebate room (never onto an NRI).
    assets.sort((a, b) => b.income - a.income);
    const residents = () => people.filter(p => !p.isNRI);
    for (const as of assets) {
      const owner = byName.get(as.owner)!;
      let host: any;
      if (!owner.isNRI && room(owner) >= as.income) host = owner;
      else host = residents().filter(p => room(p) > 0).sort((a, b) => room(b) - room(a))[0]
               || residents().sort((a, b) => held(a) - held(b))[0] || owner;
      if (host.name === as.owner) host.ownKept += as.income;
      else { host.incoming.push({ ...as, from: as.owner }); owner.releases += as.income; }
    }

    // one card per person: own income + what lands on them, vs their ₹12 L ceiling
    const hosts = people.map(p => {
      const ownIncome = p.fixed + p.ownKept;
      const incomingTotal = p.incoming.reduce((s: number, m: any) => s + m.income, 0);
      const finalIncome = ownIncome + incomingTotal;
      const finalTax = this.taxNew(finalIncome, p.isNRI, p.hasSalary);
      return {
        name: p.name, isNRI: p.isNRI, ownIncome, incoming: p.incoming, incomingTotal, finalIncome, finalTax,
        beforeTax: p.beforeTax, cap: p.cap, nre: p.nre, releases: p.releases,
        roomLeft: Math.max(0, p.cap - finalIncome), over: Math.max(0, finalIncome - p.cap),
        ownPct: Math.min(100, ownIncome / p.cap * 100),
        addPct: Math.max(0, Math.min(100, finalIncome / p.cap * 100) - Math.min(100, ownIncome / p.cap * 100)),
      };
    }).filter(h => h.finalIncome > 0 || h.beforeTax > 0)
      .sort((a, b) => b.incomingTotal - a.incomingTotal || b.releases - a.releases);

    return { hosts, familyBefore: rows.reduce((s, r) => s + r.best, 0), familyAfter: hosts.reduce((s, h) => s + h.finalTax, 0) };
  }
  zeroTaxView() {
    const plan = this.zeroTaxPlan();
    this.card('zerotax', plan);
    this.say(plan.familyBefore <= 0
      ? `You're **already at ₹0** across the family — nothing to optimise. 👏`
      : plan.familyAfter <= 0
        ? `Here's exactly **whose name should hold what**. I move each flat & holding onto a resident who still has rebate room — the bar shows their income filling toward ₹12 L, and I stop **before anyone tips over** (which would start taxing them). Net result: **₹0** for everyone.`
        : `Here's **whose name holds what**. Each bar fills a resident's ₹12 L rebate; once someone hits the ceiling, more income onto them *is* taxed, so the rest stays put. This gets the family from **${this.inr(plan.familyBefore)}** to **${this.inr(plan.familyAfter)}/yr**.`);
    this.options.set([{ key: 'regime', label: '⚖️ Back to the comparison' }, this.back()]);
  }

  // 1 ── per-sector "how much tax do I pay, and where from" ────────────────────
  taxNowView() {
    const p = this.picture(); const m = p.marginal || 0.3;
    const rows: { sector: string; income: number; tax: number; note: string }[] = [];
    if (p.salary) rows.push({ sector: '💼 Salary', income: p.salary, tax: Math.round(p.salary * m), note: 'Taxed at slab — your biggest lever is the regime + 80C/NPS.' });
    if (p.rent) rows.push({ sector: '🏠 Rent (flats)', income: p.rent, tax: Math.round(p.rentTaxable * m), note: 'Already after the 30% standard deduction; home-loan interest cuts it further.' });
    if (p.dividends) rows.push({ sector: '💵 Dividends', income: p.dividends, tax: Math.round(p.dividends * m), note: 'Taxed at your slab — cheaper if held by a lower-slab family member.' });
    if (p.interest) rows.push({ sector: '🏦 Interest (bonds/FD)', income: p.interest, tax: Math.round(p.interest * m), note: 'Fully at slab — the most tax-heavy income; shift to tax-free/54EC or lower-slab kin.' });
    if (p.fno) rows.push({ sector: '📊 F&O', income: p.fno, tax: Math.round(p.fno * m), note: 'Business income at slab — deduct every legitimate expense.' });
    const totalTax = p.incomeTax;
    // capital-gains-if-sold, per sector
    const cg: { sector: string; gain: number; tax: number; shelter: string }[] = [];
    for (const [g, v] of Object.entries(p.sectorVals)) {
      if (v.gain <= 10000 || g === 'cash' || g === 'debt') continue;
      const lbl = g === 'property' ? '🏡 Property' : g === 'equity' ? '📈 Equity' : g === 'gold' ? '🪙 Gold' : g;
      const shelter = g === 'property' ? '54EC bonds / 54F → can reach ₹0' : g === 'equity' ? 'first ₹1.25 L/yr is free; harvest losses' : 'hold >24 mo for 12.5%';
      cg.push({ sector: lbl, gain: v.gain, tax: Math.round(Math.max(0, v.gain - (g === 'equity' ? 125000 : 0)) * this.LTCG), shelter });
    }
    this.card('sectors', { rows, totalTax, marginal: m, slab: p.slabIncome, cg });
    this.say(`So about **${this.inr(totalTax)}/yr** in income tax today, taxed top-down at **${Math.round(m * 100)}%** on the last rupee. The capital-gains rows are only if you **sell** — and those are the most sheltered. Want the full plan to bring it all down?`);
    this.options.set([{ key: 'plan', label: '💡 Cut all of it — the plan' }, { key: 'need', label: '💰 Raise cash tax-smart' }, this.back()]);
  }

  // 2 ── the full, asset-referenced savings plan (no delegation) ───────────────
  planView() {
    const p = this.picture(); const m = p.marginal || 0.3;
    const rows: { title: string; save: number; steps: string[] }[] = [];
    // regime
    const oldT = this.taxOld(p.slabIncome - 50000 - 150000 - 50000);
    if (p.incomeTax - oldT > 2000)
      rows.push({ title: 'Switch to the OLD regime + claim deductions', save: p.incomeTax - oldT, steps: ['Elect old regime when filing.', 'Claim 80C ₹1.5 L (EPF/PPF/ELSS/insurance) + NPS ₹50 k + 80D health.', `Your deductions beat the new regime by ~${this.inr(p.incomeTax - oldT)}.`] });
    else
      rows.push({ title: 'Stay on the NEW regime', save: Math.max(0, oldT - p.incomeTax), steps: ['Its lower slabs + ₹75 k standard deduction already win.', 'No 80C/NPS lock-ins needed — keep the cash liquid.'] });
    // salary deductions (old regime worth it)
    if (p.incomeTax - oldT > 0)
      rows.push({ title: 'Max out 80C + NPS + 80D', save: Math.round(200000 * m), steps: ['80C ₹1.5 L, NPS 80CCD(1B) ₹50 k, 80D up to ₹75 k (self + senior parents).', `At your ${Math.round(m * 100)}% slab that's ~${this.inr(Math.round(200000 * m))} back.`] });
    // rent
    if (p.rent) rows.push({ title: 'Trim tax on your rental income', save: Math.round(p.rentTaxable * m * 0.35), steps: ['The 30% standard deduction is automatic.', 'Set off home-loan interest (up to ₹2 L on a let-out flat).', 'Hold the flat in a lower-slab family member’s name so the rent is taxed less.'] });
    // interest — the heaviest
    if (p.interest) rows.push({ title: 'Fix your fully-taxed interest income', save: Math.round(p.interest * m * 0.6), steps: [`~${this.inr(p.interest)}/yr of bond/FD interest is taxed at ${Math.round(m * 100)}% — the worst rate you pay.`, 'Move maturities into 54EC / tax-free PSU bonds, or to family in the 0–5% slab.'] });
    // capital gains — reference the actual biggest-gain asset
    const land = this.topAsset('property');
    if (land && (land.value - (land.invested ?? land.value)) > 500000) {
      const g = land.value - (land.invested ?? land.value);
      rows.push({ title: `Zero the tax on your property (${land.name})`, save: Math.round(g * this.LTCG), steps: [`**${land.name}** (owned by ${land.owner}) carries ~${this.inr(g)} of gain — a plain sale costs ${this.inr(Math.round(g * this.LTCG))}.`, `Gift it to a resident adult child first (tax-free, no clubbing) → they sell and reinvest in a house under Sec 54F → **tax ₹0**.`, 'Or park ₹50 L/plot in 54EC bonds (5-yr lock) to shield that slice.'] });
    }
    const stk = this.topAsset('equity');
    if (stk && this.picture().sectorVals['equity']?.gain > 200000)
      rows.push({ title: 'Harvest your equity gains tax-free', save: Math.round(125000 * this.LTCG), steps: [`Book & rebuy up to **₹1.25 L of long-term gains every year** — completely tax-free (it resets each year).`, 'Sell any losers before 31 March to offset realised gains; losses carry 8 years.'] });
    rows.sort((a, b) => b.save - a.save);
    this.card('plan', { rows, total: rows.reduce((s, r) => s + r.save, 0) });
    this.say(`Stack these and you're looking at roughly **${this.inr(rows.reduce((s, r) => s + r.save, 0))}** off — biggest first, each tied to your actual money.`);
    this.options.set([{ key: 'now', label: '🧾 Tax by sector' }, { key: 'restructure', label: '👥 Ownership moves' }, this.back()]);
  }

  // 3 ── "I need ₹X — raise it tax-efficiently" — a real calculator ────────────
  needAmt = signal<number | null>(null);
  fmt(n: number | null): string { return n ? Math.round(n).toLocaleString('en-IN') : ''; }   // Indian grouping
  setNeed(v: any) { const n = Math.round(Number(String(v).replace(/[^\d]/g, '')) || 0); this.needAmt.set(n || null); }
  /** greedily raise the target from the least-taxed assets first */
  needPlan = computed(() => {
    const target = this.needAmt() || 0; if (target <= 0) return null;
    // order: cash → debt(FD/bonds) → equity(₹1.25L free) → gold → property(shelterable)
    const order = ['cash', 'debt', 'equity', 'gold', 'property', 'ulip', 'other'];
    const bucket: Record<string, { value: number; gain: number; items: Position[] }> = {};
    for (const p of this.pos()) { const g = this.groupOf(p.asset_class); (bucket[g] || (bucket[g] = { value: 0, gain: 0, items: [] })).value += p.value; bucket[g].gain += p.value - (p.invested ?? p.value); bucket[g].items.push(p); }
    let left = target, tax = 0; let equityFree = 125000;
    const legs: { group: string; label: string; raise: number; tax: number; note: string }[] = [];
    for (const g of order) {
      const b = bucket[g]; if (!b || b.value < 1000 || left <= 0) continue;
      const take = Math.min(b.value, left);
      const gainFrac = b.value ? b.gain / b.value : 0;
      let legTax = 0, note = '';
      if (g === 'cash') { note = 'no tax — spend this first.'; }
      else if (g === 'debt') { note = 'principal is tax-free; only accrued interest is taxed.'; }
      else if (g === 'equity') { const rg = take * gainFrac; const taxable = Math.max(0, rg - equityFree); equityFree = Math.max(0, equityFree - rg); legTax = Math.round(taxable * this.LTCG); note = `long-term gain; first ₹1.25 L is free → ${this.inr(legTax)} tax.`; }
      else if (g === 'gold') { legTax = Math.round(take * gainFrac * this.LTCG); note = `12.5% LTCG on the gain → ${this.inr(legTax)}.`; }
      else if (g === 'property') { legTax = Math.round(take * gainFrac * this.LTCG); note = `12.5% LTCG (${this.inr(legTax)}) — but 54EC/54F can take it to ₹0.`; }
      else { legTax = Math.round(take * gainFrac * this.LTCG); note = `~12.5% on the gain.`; }
      const lbl = g === 'debt' ? 'FDs / bonds' : g === 'equity' ? 'Stocks' : g === 'property' ? 'Property' : g[0].toUpperCase() + g.slice(1);
      legs.push({ group: g, label: lbl, raise: Math.round(take), tax: legTax, note });
      tax += legTax; left -= take;
    }
    const shelterable = legs.filter(l => l.group === 'property').reduce((s, l) => s + l.tax, 0);
    return { target, raised: target - Math.max(0, left), short: Math.max(0, left), tax, legs, netIfPlanned: tax - shelterable };
  });
  needView() {
    this.needAmt.set(null);
    this.say('Tell me how much cash you need — I’ll show you the **cheapest-taxed way** to raise it from your own assets.');
    this.card('need');
    this.options.set([this.back()]);
  }

  // 4 ── ownership restructuring (rename to a lower-tax holder) ────────────────
  restructureView() {
    const d = this._d();
    const people = (d?.by_person || []).slice().sort((a, b) => b.monthly_income - a.monthly_income);
    const high = people[0]; const low = people[people.length - 1];
    const moves: { asset: string; from: string; to: string; why: string }[] = [];
    // income-producing assets held by the highest earner → move to lowest
    for (const p of this.pos()) {
      if (!high || !low || high.person === low.person) break;
      if ((p.monthly_income || 0) > 0 && p.owner === high.person && moves.length < 4)
        moves.push({ asset: p.name, from: p.owner, to: low.person, why: `${p.owner} is in the top slab; ${low.person} is lower — the income on this gets taxed less.` });
    }
    this.card('restructure', { moves, high: high?.person, low: low?.person });
    this.say(high && low && high.person !== low.person
      ? `**${high.person}** earns the most (top slab), **${low.person}** the least. Gifting income-producing assets to an **adult** child/parent is tax-free and not clubbed — the future income is then taxed in their lower bracket. For NRIs, a resident son also unlocks Sec 54F and cuts TDS to 1%.`
      : 'Gifting income-producing assets to an adult family member in a lower slab (tax-free, no clubbing for adult children) shifts future income to a lower bracket — and for NRIs unlocks Sec 54F + 1% TDS.');
    this.options.set([{ key: 'plan', label: '💡 The full plan' }, this.back()]);
  }
}
