import { Component, ElementRef, EventEmitter, Input, OnInit, Output, ViewChild, computed, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { TaxService, EquityTax, EqPerson, EqWhatIf, TaxProfile, Residency } from '../../services/tax.service';

interface Choice { key: string; label: string; }
interface Msg {
  role: 'bot' | 'user';
  text?: string;
  card?: 'family' | 'liability' | 'save' | 'harvest' | 'crossover' | 'headroom'
        | 'positions' | 'profiles' | 'whatif' | 'whatif-result';
  data?: any;
}

/**
 * "Kuber" — the stock capital-gains TaxBot. Same rule-based, no-LLM chat pattern
 * as Bhoomi (land): every number is computed server-side by api/tax/equity.py, so
 * the conversation can be trusted. Realized CG from the Zerodha Console Tax-P&L +
 * live tax-saving levers (harvest / crossover / ₹1.25L headroom) from the tradebook.
 */
@Component({
  selector: 'app-stock-tax',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './stock-tax.html',
  styleUrl: './stock-tax.scss',
})
export class StockTax implements OnInit {
  private api = inject(TaxService);
  @ViewChild('scroller') scroller?: ElementRef<HTMLDivElement>;
  @Input() embedded = false;
  @Input() expanded = false;
  @Output() closed = new EventEmitter<void>();
  @Output() toggleSize = new EventEmitter<void>();

  avatarImg = '/kuber-stock-tax.png';
  imgOk = signal(true);
  inr = TaxService.inr;
  bot = { name: 'Kuber', role: 'Stocks & Capital-Gains Guide', emoji: '📈' };

  data = signal<EquityTax | null>(null);
  profiles = signal<TaxProfile[]>([]);
  loading = signal(true);
  error = signal<string | null>(null);
  msgs = signal<Msg[]>([]);
  options = signal<Choice[]>([]);
  typing = signal(false);

  /** trusted scripted copy — render **bold** as <b>. */
  boldify(text: string | undefined): string {
    if (!text) return '';
    const esc = text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    return esc.replace(/\*\*(.+?)\*\*/g, '<b>$1</b>');
  }

  ngOnInit() {
    this.api.equity().subscribe({
      next: d => { this.data.set(d); this.loading.set(false); this.greet(); },
      error: e => { this.loading.set(false); this.error.set(e?.error?.detail || 'Could not load your stock tax data.'); },
    });
    this.api.profiles().subscribe({ next: r => this.profiles.set(r.profiles), error: () => {} });
  }

  // ── message helpers ──────────────────────────────────────────────────────
  private push(m: Msg) { this.msgs.update(a => [...a, m]); }
  private say(text: string) { this.push({ role: 'bot', text }); }
  private card(card: Msg['card'], data?: any) { this.push({ role: 'bot', card, data }); }
  private setMenu() {
    this.options.set([
      { key: 'owe', label: '🧾 What tax do we owe this year?' },
      { key: 'save', label: '💸 Save tax — the openings I spotted' },
      { key: 'whatif', label: '🔮 What if I sell a stock today?' },
      { key: 'people', label: '👨‍👩‍👦 Per-person breakdown & residency' },
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
    const d = this.data()!; const f = d.family;
    const saveable = f.total_saveable + f.ltcg_headroom * this.data()!.assumptions.ltcg_rate;
    this.say(`Namaste! I’m ${this.bot.name}, your stocks & capital-gains guide. 📈`);
    this.say(`I’ve read the whole family’s equity — **${f.people} ${f.people === 1 ? 'person' : 'people'}**, realized gains of **${this.inr(f.realized_stcg + f.realized_ltcg)}** this year (${d.fy}), and I’m watching **${this.inr(saveable)}** of tax you could still legally save.`);
    this.say('Everything below is on your **real Zerodha numbers**, FY 2025-26. What shall we look at?');
    this.setMenu();
  }

  choose(c: Choice) {
    this.push({ role: 'user', text: c.label });
    this.options.set([]);
    this.typing.set(true);
    this._readFrom();
    setTimeout(() => { this.typing.set(false); this._route(c.key); }, 380);
  }

  private _route(key: string) {
    switch (key) {
      case 'owe': return this.owe();
      case 'save': return this.save();
      case 'whatif': return this.startWhatIf();
      case 'people': return this.people();
      case 'holdings': return this.holdings();
      case 'menu': this.say('What else can I help with?'); return this.setMenu();
      default: return this.setMenu();
    }
  }

  private _back() { this.options.set([{ key: 'menu', label: '↩ Back to menu' }]); }

  // 1️⃣ What tax do we owe — family roll-up then per-person liability
  private owe() {
    const d = this.data()!; const f = d.family;
    if (!f.realized_stcg && !f.realized_ltcg && !f.intraday && !f.dividends) {
      this.say('Good news — I don’t see any **realized** equity gains booked this year yet, so there’s **no capital-gains tax** owed so far. Upload a fresh Zerodha *Tax P&L* statement when you have one and I’ll compute it to the rupee.');
      this.say('Want me to show where you could **save tax on what you still hold** instead?');
      this.options.set([{ key: 'save', label: '💸 Show my tax-saving openings' }, { key: 'menu', label: '↩ Menu' }]);
      return;
    }
    this.say(`Here’s the family’s **capital-gains tax for ${d.fy}** — short-term at 20% (Sec 111A), long-term at 12.5% over the ₹1.25 L free limit (Sec 112A):`);
    this.card('family', f);
    this.say('And the split **person by person** — sorted by who owes the most:');
    this.card('liability', d.people.filter(p => p.has_statement || p.liability.total_tax > 0));
    if (f.intraday || f.dividends) {
      this.say(`Separately, there’s **${this.inr(f.intraday)}** of intraday and **${this.inr(f.dividends)}** of dividends — both taxed at your slab rate, not shown in the CG figure above.`);
    }
    this.say('Want the legal ways to bring next year’s bill down?');
    this.options.set([{ key: 'save', label: '💸 Yes — show me how to save' }, { key: 'menu', label: '↩ Menu' }]);
  }

  // 2️⃣ Save tax — harvest + crossover + headroom, most valuable first
  private save() {
    const d = this.data()!; const f = d.family;
    const withUn = d.people.filter(p => p.unrealized);
    const anyHarvest = withUn.some(p => (p.unrealized!.harvest.length));
    const anyCross = withUn.some(p => (p.unrealized!.crossover.length));
    const anyHeadroom = withUn.some(p => p.unrealized!.ltcg_headroom > 0 && p.unrealized!.lt_gain > 0);

    this.say('Three legal levers, ranked by how much they save on **your live holdings** right now:');

    // A — harvest losses
    if (anyHarvest) {
      this.say(`**1 · Harvest losses** — book stocks sitting in the red to cancel out your gains. I found **${this.inr(f.harvest_saved)}** of tax you can wipe out:`);
      this.card('harvest', withUn.filter(p => p.unrealized!.harvest.length));
    }
    // B — crossover timing
    if (anyCross) {
      this.say(`**2 · Wait for long-term** — these lots are days away from crossing 12 months. Hold a little longer and the rate drops **20% → 12.5%**, saving **${this.inr(f.crossover_saved)}**:`);
      this.card('crossover', withUn.filter(p => p.unrealized!.crossover.length));
    }
    // C — headroom
    if (anyHeadroom) {
      this.say('**3 · Use the ₹1.25 L free limit** — everyone gets ₹1.25 L of long-term gains tax-free every year. You can realize this much of your winners at **zero tax** (then buy back if you still like them):');
      this.card('headroom', withUn.filter(p => p.unrealized!.ltcg_headroom > 0));
    }
    if (!anyHarvest && !anyCross && !anyHeadroom) {
      this.say('Your holdings are all in good shape — nothing sitting at a harvestable loss, nothing about to cross long-term, and the ₹1.25 L free limit is already used. I’ll keep watching as prices move. 👍');
    }
    this.options.set([
      { key: 'whatif', label: '🔮 Try “what if I sell…”' },
      { key: 'holdings', label: '📊 See all my holdings' },
      { key: 'menu', label: '↩ Menu' },
    ]);
  }

  // holdings overview
  private holdings() {
    const d = this.data()!;
    const withPos = d.people.filter(p => p.unrealized && p.unrealized.positions.length);
    if (!withPos.length) { this.say('I don’t have any priced holdings to show yet — upload a tradebook to populate this.'); return this._back(); }
    this.say('Every stock you hold, with its live value and whether the gain would be **short** or **long-term** if you sold today:');
    this.card('positions', withPos);
    this._back();
  }

  // 4️⃣ Per-person + residency
  private people() {
    this.say('Here’s where each person stands — their residency drives the rules (an **NRI** has TDS deducted on gains and can’t set the basic-exemption limit against them). Tap to change if I’ve got someone wrong:');
    this.card('profiles', {});
    this.say('And the full per-person picture:');
    this.card('liability', this.data()!.people);
    this._back();
  }

  setResidency(name: string, residency: Residency) {
    this.api.setProfile(name, { residency }).subscribe({
      next: () => {
        this.profiles.update(list => list.map(p => p.name === name ? { ...p, residency } : p));
        this.push({ role: 'user', text: `Set ${name} → ${residency.toUpperCase()}` });
        this._readFrom();
        this.typing.set(true);
        this.api.equity(this.data()?.fy).subscribe({ next: d => this.data.set(d) });
        setTimeout(() => {
          this.typing.set(false);
          this.say(`Done — **${name}** is now **${residency === 'nri' ? 'a Non-Resident (NRI)' : residency === 'rnor' ? 'RNOR' : 'a Resident'}**. I’ve recomputed every figure.`);
          this.card('profiles', {});
        }, 360);
      },
      error: () => this.say('Hmm, I couldn’t save that just now.'),
    });
  }

  // ════════════════════════════════════════════════════════════════════════
  //  "What if I sell" — pick person → pick stock → set qty → live tax
  // ════════════════════════════════════════════════════════════════════════
  wiPerson = signal<string>('');
  wiSymbol = signal<string>('');
  wiQty = signal<number>(0);
  wiPrice = signal<number | null>(null);
  wiResult = signal<EqWhatIf | null>(null);
  wiBusy = signal(false);
  wiError = signal<string | null>(null);

  peopleWithHoldings = computed(() => (this.data()?.people || []).filter(p => p.unrealized && p.unrealized.positions.length));
  wiPositions = computed(() => {
    const p = this.data()?.people.find(x => x.person === this.wiPerson());
    return p?.unrealized?.positions || [];
  });
  wiPos = computed(() => this.wiPositions().find(p => p.symbol === this.wiSymbol()));

  startWhatIf() {
    this.wiResult.set(null); this.wiSymbol.set(''); this.wiQty.set(0); this.wiPrice.set(null); this.wiError.set(null);
    const ppl = this.peopleWithHoldings();
    if (!ppl.length) { this.say('I need some tradebook holdings first — upload a Zerodha tradebook and I can run “what if I sell” on the real lots.'); return this._back(); }
    this.wiPerson.set(ppl[0].person);
    this.say('Let’s test a sale. **Whose holdings, which stock, and how many shares?** I’ll FIFO-match the exact lots and split the gain into short vs long-term:');
    this.card('whatif', {});
    this._back();
  }

  pickWiPerson(name: string) { this.wiPerson.set(name); this.wiSymbol.set(''); this.wiQty.set(0); this.wiPrice.set(null); this.wiResult.set(null); }
  pickWiSymbol(sym: string) {
    const pos = this.wiPositions().find(p => p.symbol === sym);
    this.wiSymbol.set(sym);
    this.wiQty.set(pos ? pos.qty : 0);
    this.wiPrice.set(pos ? pos.price : null);
    this.wiResult.set(null);
    this.runWhatIf();
  }
  setWiQty(v: any) { this.wiQty.set(Math.max(0, Number(String(v).replace(/[^\d.]/g, '')) || 0)); this.runWhatIf(); }
  setWiPrice(v: any) { const n = Number(String(v).replace(/[^\d.]/g, '')) || 0; this.wiPrice.set(n || null); this.runWhatIf(); }

  private _wiTimer: any = null;
  runWhatIf() {
    this.wiError.set(null);
    if (!this.wiPerson() || !this.wiSymbol() || this.wiQty() <= 0) { this.wiResult.set(null); return; }
    clearTimeout(this._wiTimer);
    this._wiTimer = setTimeout(() => {
      this.wiBusy.set(true);
      this.api.equityWhatIf(this.wiPerson(), this.wiSymbol(), this.wiQty(), this.wiPrice() || undefined).subscribe({
        next: r => { this.wiResult.set(r); this.wiBusy.set(false); },
        error: e => { this.wiBusy.set(false); this.wiResult.set(null); this.wiError.set(e?.error?.detail || 'Could not compute that sale.'); },
      });
    }, 260);
  }

  // ── small helpers for the template ───────────────────────────────────────
  resLabel(r: Residency | string | null | undefined): string { return r === 'nri' ? 'NRI' : r === 'rnor' ? 'RNOR' : 'Resident'; }
  initial(name: string): string { return (name || '?').trim().charAt(0).toUpperCase(); }
  assumptions(): string[] { return this.data()?.assumptions?.notes || []; }
}
