import { Component, OnInit, inject, signal, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpErrorResponse } from '@angular/common/http';
import { ActivatedRoute } from '@angular/router';
import { BondsService, BondSummary, BondRow, BondInput, ScheduleRow, PaymentMonth, PaymentStatus, BondSip, BondSipInput, BondSipSplit, ReconcileResult, ReconcileMatch, StatementUpload } from '../../services/bonds.service';
import { BondsTax } from '../bonds-tax/bonds-tax';

type Draft = BondInput & { id?: string };
type SipDraft = { id?: string; owner: string; total: number | null; expected_date: string; note: string; splits: BondSipSplit[] };

const EMPTY_SIP: SipDraft = { owner: '', total: null, expected_date: '', note: '', splits: [{ name: '', amount: 0 }] };

const EMPTY: Draft = {
  owner: '', broker: '', issuer: '', bond_type: 'Corporate NCD', isin: '', rating: '',
  tax_free: false, face_value: 1000, quantity: 0, buy_price: 1000,
  coupon_rate: 0, coupon_freq: 'quarterly', repayment_type: 'amortizing',
  purchase_date: '', first_payment_date: '', maturity_date: '',
  redemption_value: null, ytm_input: null, schedule: null, note: '', sellable_on: '',
};

@Component({
  selector: 'app-bonds',
  standalone: true,
  imports: [CommonModule, FormsModule, BondsTax],
  templateUrl: './bonds.html',
  styleUrl: './bonds.scss',
})
export class Bonds implements OnInit {
  private api = inject(BondsService);
  private route = inject(ActivatedRoute);

  // Bandhan — the on-page bonds-tax assistant
  assistantOpen = signal(false);
  assistantExpanded = signal(false);   // compact popup ⇄ big drawer
  assistantImg = '/bandhan-bonds-tax.png';
  assistantImgOk = signal(true);
  closeAssistant() { this.assistantOpen.set(false); this.assistantExpanded.set(false); }

  summary = signal<BondSummary | null>(null);
  loading = signal(true);
  error = signal<string | null>(null);
  needsMigration = signal(false);

  // global view state
  tds = signal(true);                                    // default to net-of-TDS — most bonds pay after 10% TDS
  view = signal<'repayments' | 'investments'>('repayments');

  // repayment-chart controls
  series = signal<'both' | 'interest' | 'principal'>('interest');  // which bars + scale (default: interest)
  pickedDate = signal<string>('');                            // YYYY-MM-DD the user inspects

  // filters (account attribution)
  search = signal('');
  ownerFilter = signal('');
  brokerFilter = signal('');

  showForm = signal(false);
  editingId = signal<string | null>(null);
  draft = signal<Draft>({ ...EMPTY });
  scheduleDraft = signal<ScheduleRow[]>([]);
  generating = signal(false);

  // expand a table row to reveal a bond's full details
  expandedBond = signal<string | null>(null);
  toggleBond(id: string) { this.expandedBond.update(c => (c === id ? null : id)); }

  // ── per-payment received / pending / not-received marks (calendar) ───────────
  // keyed by `${bond_id}|${YYYY-MM-DD}`; anything absent defaults to 'pending'.
  paymentStatus = signal<Record<string, PaymentStatus>>({});
  private static statusKey(bondId: string, date: string): string { return `${bondId}|${(date || '').slice(0, 10)}`; }
  statusOf(bondId: string, date: string): PaymentStatus {
    return this.paymentStatus()[Bonds.statusKey(bondId, date)] || 'pending';
  }
  private _todayIso = (() => { const t = new Date(); return `${t.getFullYear()}-${String(t.getMonth() + 1).padStart(2, '0')}-${String(t.getDate()).padStart(2, '0')}`; })();

  private loadStatuses() {
    this.api.paymentStatuses().subscribe({
      next: rows => {
        const m: Record<string, PaymentStatus> = {};
        for (const r of rows || []) if (r.bond_id && r.date) m[Bonds.statusKey(r.bond_id, r.date)] = r.status;
        this.paymentStatus.set(m);
      },
      error: () => {},                 // table not migrated yet → everything stays pending
    });
  }

  /** Mark a single payment received / pending / not-received — optimistic. */
  setPayStatus(bondId: string, date: string, status: PaymentStatus) {
    const k = Bonds.statusKey(bondId, date);
    const prev = this.paymentStatus();
    this.paymentStatus.update(m => { const n = { ...m }; if (status === 'pending') delete n[k]; else n[k] = status; return n; });
    this.api.setPaymentStatus(bondId, (date || '').slice(0, 10), status).subscribe({ error: () => this.paymentStatus.set(prev) });
  }

  // ── Bank-statement reconciliation (upload → match credits → mark received) ────
  reconcileOpen = signal(false);
  reconcileBusy = signal(false);
  reconcileError = signal<string | null>(null);
  reconcileResult = signal<ReconcileResult | null>(null);
  reconcileFileName = signal('');
  reconcileAccepted = signal<Set<string>>(new Set());
  reconcileDone = signal<number | null>(null);
  uploads = signal<StatementUpload[]>([]);
  private rcKey(m: { bond_id: string; scheduled_date: string }): string { return `${m.bond_id}|${m.scheduled_date}`; }

  private loadUploads() { this.api.reconcileUploads().subscribe({ next: u => this.uploads.set(u || []), error: () => {} }); }

  /** The latest statement date_to that touched an owner → "reconciled till …". */
  reconciledTill(owner: string): string | null {
    let best: string | null = null;
    for (const u of this.uploads()) {
      if (!u.date_to) continue;
      if ((u.owners || []).length && !u.owners.includes(owner)) continue;
      if (!best || u.date_to > best) best = u.date_to;
    }
    return best;
  }
  lastUpload = computed<StatementUpload | null>(() => this.uploads()[0] || null);

  openReconcile() {
    this.reconcileOpen.set(true);
    this.reconcileResult.set(null); this.reconcileError.set(null);
    this.reconcileFileName.set(''); this.reconcileDone.set(null);
  }
  closeReconcile() { this.reconcileOpen.set(false); }

  onReconcileFile(ev: Event) {
    const input = ev.target as HTMLInputElement;
    const f = input.files?.[0]; input.value = '';
    if (!f) return;
    this.reconcileFileName.set(f.name);
    this.reconcileBusy.set(true); this.reconcileError.set(null);
    this.reconcileResult.set(null); this.reconcileDone.set(null);
    this.api.reconcilePreview(f).subscribe({
      next: r => {
        this.reconcileBusy.set(false);
        this.reconcileResult.set(r);
        this.reconcileAccepted.set(new Set(r.matched.map(m => this.rcKey(m))));  // pre-tick confident ones
      },
      error: (e: HttpErrorResponse) => {
        this.reconcileBusy.set(false);
        this.reconcileError.set(e.error?.detail || 'Could not read the statement. Export it as Excel/CSV and retry.');
      },
    });
  }

  isAccepted(m: ReconcileMatch): boolean { return this.reconcileAccepted().has(this.rcKey(m)); }
  toggleAccept(m: ReconcileMatch) {
    const k = this.rcKey(m);
    this.reconcileAccepted.update(s => { const n = new Set(s); n.has(k) ? n.delete(k) : n.add(k); return n; });
  }
  acceptedCount = computed(() => this.reconcileAccepted().size);

  confirmReconcile() {
    const res = this.reconcileResult(); if (!res) return;
    const accepted = [...res.matched, ...res.review].filter(m => this.isAccepted(m));
    const items = accepted.map(m => ({ bond_id: m.bond_id, date: m.scheduled_date }));
    if (!items.length) { this.closeReconcile(); return; }
    // statement metadata for the upload history (dates cover ALL parsed credits)
    const allDates = [...res.matched, ...res.review, ...res.already].map(m => m.credit_date)
      .concat(res.unmatched.map(u => u.date)).filter(Boolean).sort();
    const meta = {
      filename: this.reconcileFileName() || undefined,
      date_from: allDates[0] || null,
      date_to: allDates[allDates.length - 1] || null,
      owners: [...new Set(accepted.map(m => m.owner).filter((o): o is string => !!o))],
      credits: res.counts.credits,
      amount: Math.round(accepted.reduce((s, m) => s + (m.net || 0), 0) * 100) / 100,
    };
    this.reconcileBusy.set(true); this.reconcileError.set(null);
    this.api.reconcileConfirm(items, meta).subscribe({
      next: r => {
        this.reconcileBusy.set(false);
        // optimistic: paint the calendar cells green now, then reload from server
        this.paymentStatus.update(m => { const n = { ...m }; for (const it of items) n[Bonds.statusKey(it.bond_id, it.date)] = 'received'; return n; });
        this.reconcileDone.set(r.marked);
        this.loadStatuses();
        this.loadUploads();
        setTimeout(() => this.reconcileOpen.set(false), 1400);
      },
      error: (e: HttpErrorResponse) => { this.reconcileBusy.set(false); this.reconcileError.set('Could not save — ' + (e.error?.detail || 'try again.')); },
    });
  }

  inr = BondsService.inr;
  inrFull = BondsService.inrFull;
  pct = BondsService.pct;

  hasData = computed(() => (this.summary()?.count || 0) > 0);

  readonly BOND_TYPES = ['G-Sec', 'SDL', 'Corporate NCD', 'Tax-free', 'RBI Floating-Rate',
    '54EC Capital-Gain', 'SGB', 'T-Bill', 'Perpetual (AT1)', 'Other'];
  readonly FREQS = [
    { v: 'monthly', l: 'Monthly' }, { v: 'quarterly', l: 'Quarterly' },
    { v: 'half_yearly', l: 'Half-yearly' }, { v: 'annual', l: 'Annual' },
    { v: 'cumulative', l: 'Cumulative (at maturity)' }, { v: 'zero', l: 'Zero-coupon' },
  ];
  freqLabel(v: string): string { return this.FREQS.find(f => f.v === v)?.l || v; }

  ngOnInit() {
    const t = new Date();
    this.pickedDate.set(`${t.getFullYear()}-${String(t.getMonth() + 1).padStart(2, '0')}-${String(t.getDate()).padStart(2, '0')}`);
    this.load();
    // arriving from a dashboard "Log SIP details" reminder → jump to Investments
    // and highlight the SIP that's waiting to be logged.
    const q = this.route.snapshot.queryParamMap;
    if (q.get('view') === 'investments') this.view.set('investments');
    const sip = q.get('sip');
    if (sip) {
      this.view.set('investments');
      this.highlightSip.set(sip);
      setTimeout(() => this.highlightSip.set(null), 4000);
    }
  }

  load() {
    this.loading.set(true);
    this.error.set(null);
    this.needsMigration.set(false);
    this.loadStatuses();
    this.loadSips();
    this.loadUploads();
    this.api.summary().subscribe({
      next: s => { this.summary.set(s); this.loading.set(false); },
      error: (e: HttpErrorResponse) => {
        this.loading.set(false);
        if (e.status === 503) this.needsMigration.set(true);
        else this.error.set('Could not reach the API. Is the backend running?');
      },
    });
  }

  // ── add / edit ────────────────────────────────────────────────────────────
  openAdd() { this.editingId.set(null); this.draft.set({ ...EMPTY }); this.scheduleDraft.set([]); this.showForm.set(true); }
  openEdit(b: BondRow) {
    this.editingId.set(b.id);
    this.draft.set({
      id: b.id, owner: b.owner, broker: b.broker, issuer: b.issuer, bond_type: b.bond_type,
      isin: b.isin || '', rating: b.rating || '', tax_free: b.tax_free,
      face_value: b.face_value, quantity: b.quantity, buy_price: b.buy_price,
      coupon_rate: b.coupon_rate, coupon_freq: b.coupon_freq, repayment_type: b.repayment_type,
      purchase_date: (b.purchase_date || '').slice(0, 10),
      first_payment_date: ((b as any).first_payment_date || '').slice(0, 10),
      maturity_date: (b.maturity_date || '').slice(0, 10),
      redemption_value: (b as any).redemption_value ?? null,
      ytm_input: (b as any).ytm_input ?? null,
      schedule: null, note: b.note || '', sellable_on: (b as any).sellable_on ?? '',
    });
    this.scheduleDraft.set((b.schedule || []).map(p => ({ date: (p.date || '').slice(0, 10), interest: p.interest, principal: p.principal })));
    this.showForm.set(true);
  }
  closeForm() { this.showForm.set(false); }
  setField<K extends keyof Draft>(key: K, val: Draft[K]) { this.draft.update(d => ({ ...d, [key]: val })); }

  // editable schedule rows
  setSchedRow(i: number, key: 'date' | 'interest' | 'principal', val: any) {
    this.scheduleDraft.update(rows => rows.map((r, idx) => idx === i ? { ...r, [key]: key === 'date' ? val : +val } : r));
  }
  addSchedRow() {
    const rows = this.scheduleDraft();
    const last = rows[rows.length - 1];
    this.scheduleDraft.set([...rows, { date: last?.date || this.draft().first_payment_date || '', interest: 0, principal: 0 }]);
  }
  removeSchedRow(i: number) { this.scheduleDraft.update(rows => rows.filter((_, idx) => idx !== i)); }

  canGenerate = computed(() => {
    const d = this.draft();
    return +d.buy_price > 0 && +d.quantity > 0 && !!d.first_payment_date && !!d.maturity_date
      && d.ytm_input != null && +d.ytm_input > 0
      && d.coupon_freq !== 'cumulative' && d.coupon_freq !== 'zero';
  });

  generateSchedule() {
    if (!this.canGenerate()) return;
    const d = this.draft();
    this.generating.set(true);
    this.api.generate({
      invested: +d.buy_price * +d.quantity, face_total: +d.face_value * +d.quantity,
      ytm: +(d.ytm_input as number), first_payment_date: d.first_payment_date as string,
      maturity_date: d.maturity_date, coupon_freq: d.coupon_freq, repayment_type: d.repayment_type,
    }).subscribe({
      next: r => { this.scheduleDraft.set(r.schedule || []); this.generating.set(false); },
      error: () => { this.generating.set(false); this.error.set('Could not generate the schedule.'); },
    });
  }

  // derived figures from the editable schedule (live, source of truth)
  schedInterest = computed(() => this.scheduleDraft().reduce((a, r) => a + (+r.interest || 0), 0));
  schedPrincipal = computed(() => this.scheduleDraft().reduce((a, r) => a + (+r.principal || 0), 0));
  /** Effective-average coupon: total interest ÷ years ÷ face × 100. */
  draftCoupon = computed<number | null>(() => {
    const d = this.draft(); const face = +d.face_value * +d.quantity;
    if (!d.purchase_date || !d.maturity_date || face <= 0) return null;
    const yrs = (new Date(d.maturity_date).getTime() - new Date(d.purchase_date).getTime()) / (365 * 864e5);
    if (yrs <= 0) return null;
    return this.schedInterest() / yrs / face * 100;
  });
  /** YTM (XIRR) over the editable schedule, computed client-side for live feedback. */
  draftYtm = computed<number | null>(() => {
    const d = this.draft(); const rows = this.scheduleDraft();
    const invested = +d.buy_price * +d.quantity;
    if (invested <= 0 || !d.purchase_date || !rows.length) return null;
    const flows: { t: number; a: number }[] = [{ t: new Date(d.purchase_date).getTime(), a: -invested }];
    for (const r of rows) {
      if (!r.date) continue;
      flows.push({ t: new Date(r.date).getTime(), a: (+r.interest || 0) + (+r.principal || 0) });
    }
    return xirr(flows);
  });

  canSave = computed(() => {
    const d = this.draft();
    return !!d.owner.trim() && !!d.issuer.trim() && !!d.purchase_date && !!d.maturity_date
      && +d.quantity > 0 && +d.buy_price > 0;
  });

  save() {
    if (!this.canSave()) return;
    const d = this.draft();
    const rows = this.scheduleDraft().filter(r => !!r.date)
      .map(r => ({ date: r.date, interest: +r.interest || 0, principal: +r.principal || 0 }));
    const payload: BondInput = {
      owner: d.owner.trim(), broker: (d.broker || '').trim(), issuer: d.issuer.trim(),
      bond_type: d.bond_type, isin: (d.isin || '').trim(), rating: (d.rating || '').trim(),
      tax_free: !!d.tax_free, face_value: +d.face_value, quantity: +d.quantity, buy_price: +d.buy_price,
      coupon_rate: +(this.draftCoupon() ?? d.coupon_rate ?? 0), coupon_freq: d.coupon_freq, repayment_type: d.repayment_type,
      purchase_date: d.purchase_date, first_payment_date: d.first_payment_date || null,
      maturity_date: d.maturity_date,
      redemption_value: d.redemption_value ? +d.redemption_value : null,
      ytm_input: d.ytm_input != null ? +d.ytm_input : null,
      schedule: rows.length ? rows : null,
      note: (d.note || '').trim(), sellable_on: d.sellable_on || null,
    };
    const id = this.editingId();
    const req = id ? this.api.update(id, payload) : this.api.create(payload);
    req.subscribe({
      next: () => { this.showForm.set(false); this.load(); },
      error: (e: HttpErrorResponse) => {
        if (e.status === 503) this.needsMigration.set(true);
        else this.error.set('Could not save the bond — ' + this.errText(e));
      },
    });
  }

  /** Pull a human-readable reason out of an HTTP error (FastAPI sends {detail}). */
  private errText(e: HttpErrorResponse): string {
    if (e.status === 0) return 'no response from the API (is the backend running?)';
    const d = e.error?.detail ?? e.error?.message ?? e.error;
    if (typeof d === 'string') return d;
    if (Array.isArray(d)) return d.map((x: any) => x.loc ? `${x.loc?.join('.')}: ${x.msg}` : x.msg).join('; ');
    return `HTTP ${e.status}${e.statusText ? ' ' + e.statusText : ''}`;
  }
  remove(b: BondRow) {
    if (!confirm(`Delete ${b.issuer}?`)) return;
    this.api.remove(b.id).subscribe({ next: () => this.load(), error: () => this.error.set('Could not delete.') });
  }

  // ── filtering (account attribution) ─────────────────────────────────────────
  owners = computed(() => Array.from(new Set((this.summary()?.bonds || []).map(b => b.owner))).filter(Boolean).sort());
  brokers = computed(() => Array.from(new Set((this.summary()?.bonds || []).map(b => b.broker))).filter(Boolean).sort());
  hasFilters = computed(() => !!this.search().trim() || !!this.ownerFilter() || !!this.brokerFilter());
  clearFilters() { this.search.set(''); this.ownerFilter.set(''); this.brokerFilter.set(''); }

  private matchBond(b: { owner: string; broker: string; issuer: string }): boolean {
    if (this.ownerFilter() && b.owner !== this.ownerFilter()) return false;
    if (this.brokerFilter() && b.broker !== this.brokerFilter()) return false;
    const q = this.search().trim().toLowerCase();
    if (q && !(`${b.issuer} ${b.owner} ${b.broker}`.toLowerCase().includes(q))) return false;
    return true;
  }

  filteredBonds = computed(() => (this.summary()?.bonds || []).filter(b => this.matchBond(b)));
  filteredAccounts = computed(() => (this.summary()?.payments_by_account || [])
    .filter(a => this.matchBond({ owner: a.owner, broker: a.broker, issuer: '' })));

  /** Investments view: accounts grouped by person → one card per member (broker
   *  accounts clubbed), each expandable to show how much sits in each account. */
  filteredPersonAccounts = computed(() => {
    const map = new Map<string, {
      owner: string; invested: number; monthly_income: number; monthly_income_net: number;
      interest_12m: number; principal_12m: number; net_12m: number; bonds: number; accounts: any[];
    }>();
    for (const a of this.filteredAccounts()) {
      let p = map.get(a.owner);
      if (!p) {
        p = { owner: a.owner, invested: 0, monthly_income: 0, monthly_income_net: 0,
              interest_12m: 0, principal_12m: 0, net_12m: 0, bonds: 0, accounts: [] };
        map.set(a.owner, p);
      }
      p.invested += a.invested; p.monthly_income += a.monthly_income; p.monthly_income_net += a.monthly_income_net;
      p.interest_12m += a.interest_12m; p.principal_12m += a.principal_12m; p.net_12m += a.net_12m; p.bonds += a.bonds;
      p.accounts.push(a);
    }
    const people = Array.from(map.values()).sort((x, y) => y.invested - x.invested);
    people.forEach(p => p.accounts.sort((x: any, y: any) => y.invested - x.invested));
    return people;
  });

  /** Payment timeline re-aggregated for the active filter + running capital recovered. */
  filteredSchedule = computed<PaymentMonth[]>(() => {
    const sch = this.summary()?.payment_schedule || [];
    if (!this.hasFilters()) return sch;
    const baseRecovered = this.filteredBonds().reduce((a, b) => a + (b.capital_recovered || 0), 0);
    const invested = this.filteredBonds().reduce((a, b) => a + (b.invested || 0), 0) || 1;
    let run = baseRecovered;
    const out: PaymentMonth[] = [];
    for (const m of sch) {
      const pays = m.payments.filter(p => this.matchBond(p));
      if (!pays.length) continue;
      const agg = pays.reduce((o, p) => ({
        total: o.total + p.total, interest: o.interest + p.interest, principal: o.principal + p.principal,
        tds: o.tds + p.tds, net: o.net + p.net,
      }), { total: 0, interest: 0, principal: 0, tds: 0, net: 0 });
      run += agg.principal;
      out.push({ month: m.month, ...agg, capital_recovered: round2(run), capital_recovered_pct: run / invested, count: pays.length, payments: pays });
    }
    return out;
  });

  next12Total = computed(() => this.filteredSchedule().slice(0, 12).reduce((a, m) => a + this.amt(m), 0));

  // ── repayment timeline chart (monthly bars + inspect-day marker) ─────────────
  /** Whole-life monthly chart aggregated from each (filtered) bond's real
   *  schedule. Bars + scale follow the Interest/Principal/Both toggle (so small
   *  interest isn't dwarfed by principal); a marker sits on the inspected day. */
  repayChart = computed(() => {
    const bonds = this.filteredBonds();
    if (!bonds.length) return null;
    const ser = this.series();
    type Acc = { owner: string; broker: string; interest: number; principal: number; total: number; net: number };
    type M = { month: string; interest: number; principal: number; total: number; tds: number; net: number; accts: Map<string, Acc> };
    const map = new Map<string, M>();
    for (const b of bonds) {
      for (const p of (b.schedule || [])) {
        const key = (p.date || '').slice(0, 7);
        if (!key) continue;
        let m = map.get(key);
        if (!m) { m = { month: key, interest: 0, principal: 0, total: 0, tds: 0, net: 0, accts: new Map() }; map.set(key, m); }
        const tds = b.tax_free ? 0 : (p.interest || 0) * 0.1;
        const net = (p.total || 0) - tds;
        m.interest += p.interest || 0; m.principal += p.principal || 0; m.total += p.total || 0; m.tds += tds; m.net += net;
        const ak = b.owner + '|' + (b.broker || '');
        let a = m.accts.get(ak);
        if (!a) { a = { owner: b.owner, broker: b.broker, interest: 0, principal: 0, total: 0, net: 0 }; m.accts.set(ak, a); }
        a.interest += p.interest || 0; a.principal += p.principal || 0; a.total += p.total || 0; a.net += net;
      }
    }
    const keys = Array.from(map.keys()).sort();
    if (!keys.length) return null;
    // scale to the active series so interest bars aren't crushed by principal
    const maxFor = (k: string) => ser === 'interest' ? map.get(k)!.interest
      : ser === 'principal' ? map.get(k)!.principal : map.get(k)!.total;
    const scaleMax = Math.max(...keys.map(maxFor), 1);
    const showInt = ser !== 'principal', showPri = ser !== 'interest';
    const curKey = this.pickedDate().slice(0, 7);
    let cumTotal = 0, cumNet = 0, cumPrincipal = 0;
    const months = keys.map(k => {
      const m = map.get(k)!;
      cumTotal += m.total; cumNet += m.net; cumPrincipal += m.principal;
      return {
        month: k, label: this.monthLabel(k), year: k.slice(0, 4),
        interest: m.interest, principal: m.principal, total: m.total, tds: m.tds, net: m.net,
        iH: showInt ? m.interest / scaleMax * 100 : 0,
        pH: showPri ? m.principal / scaleMax * 100 : 0,
        cumTotal, cumNet, cumPrincipal,
        accounts: Array.from(m.accts.values()).sort((x, y) => y.total - x.total),
        isSel: k === curKey,
      };
    });
    const n = months.length;
    const idx = months.findIndex(mm => mm.month === curKey);
    let markerLeftPct: number;
    if (idx >= 0) {
      const [yy, mm] = curKey.split('-').map(Number);
      const dim = new Date(yy, mm, 0).getDate();
      const day = +this.pickedDate().slice(8, 10) || 1;
      markerLeftPct = (idx + (day - 1) / dim) / n * 100;
    } else { markerLeftPct = curKey < keys[0] ? 0 : 100; }
    const years: { year: string; count: number }[] = [];
    for (const m of months) {
      const last = years[years.length - 1];
      if (last && last.year === m.year) last.count++; else years.push({ year: m.year, count: 1 });
    }
    return { months, years, n, markerLeftPct, showInt, showPri };
  });

  /** Date-precise detail for the inspected day, over the filtered bonds:
   *  what this month has paid *so far* (up to & including the day) vs the full
   *  month, and the running totals of principal/interest/everything collected
   *  across all months up to that date. */
  dayDetail = computed(() => {
    const bonds = this.filteredBonds();
    const day = this.pickedDate();
    if (!bonds.length || !day) return null;
    const monthKey = day.slice(0, 7);
    type Acc = { owner: string; broker: string; interest: number; principal: number; total: number; net: number };
    const accts = new Map<string, Acc>();
    let cumInt = 0, cumIntNet = 0, cumPri = 0, cumTot = 0, cumNet = 0;
    let mIntSoFar = 0, mIntSoFarNet = 0, mPriSoFar = 0, mTotSoFar = 0, mNetSoFar = 0;
    let mIntFull = 0, mIntFullNet = 0, mPriFull = 0, mTotFull = 0, mNetFull = 0;
    for (const b of bonds) {
      for (const p of (b.schedule || [])) {
        const d = (p.date || '').slice(0, 10);
        if (!d) continue;
        const tds = b.tax_free ? 0 : (p.interest || 0) * 0.1;
        const net = (p.total || 0) - tds;
        const intNet = (p.interest || 0) - tds;
        const onOrBefore = d <= day;
        if (d.slice(0, 7) === monthKey) {
          mIntFull += p.interest || 0; mIntFullNet += intNet; mPriFull += p.principal || 0; mTotFull += p.total || 0; mNetFull += net;
          if (onOrBefore) {
            mIntSoFar += p.interest || 0; mIntSoFarNet += intNet; mPriSoFar += p.principal || 0; mTotSoFar += p.total || 0; mNetSoFar += net;
            const ak = b.owner + '|' + (b.broker || '');
            let a = accts.get(ak);
            if (!a) { a = { owner: b.owner, broker: b.broker, interest: 0, principal: 0, total: 0, net: 0 }; accts.set(ak, a); }
            a.interest += p.interest || 0; a.principal += p.principal || 0; a.total += p.total || 0; a.net += net;
          }
        }
        if (onOrBefore) { cumInt += p.interest || 0; cumIntNet += intNet; cumPri += p.principal || 0; cumTot += p.total || 0; cumNet += net; }
      }
    }
    return {
      monthLabel: this.monthLabel(monthKey), hasMonth: mTotFull > 0,
      mIntSoFar, mIntSoFarNet, mPriSoFar, mTotSoFar, mNetSoFar,
      mIntFull, mIntFullNet, mPriFull, mTotFull, mNetFull,
      cumInt, cumIntNet, cumPri, cumTot, cumNet,
      accounts: Array.from(accts.values()).sort((x, y) => y.total - x.total),
    };
  });

  /** Click / double-click a bar → inspect that month. Jump to the LAST day of
   *  the month so every payout that month is counted as "collected so far". */
  pickMonth(month: string) {
    const [y, m] = month.split('-').map(Number);
    const last = new Date(y, m, 0).getDate();
    this.pickedDate.set(`${month}-${String(last).padStart(2, '0')}`);
  }

  // briefly highlight a month card in the schedule after scrolling to it
  flashMonth = signal<string | null>(null);

  // ── Inline month selection (no popup) ────────────────────────────────────────
  // Clicking a bar selects that month; the breakdown panel + the (single-month)
  // payment schedule below both follow it. Highlight follows activeMonth().
  selectedMonth = signal<string>('');
  dow = ['S', 'M', 'T', 'W', 'T', 'F', 'S'];

  /** Distinct months (sorted) that have any payout, over the filtered bonds. */
  monthsList = computed(() => {
    const set = new Set<string>();
    for (const b of this.filteredBonds())
      for (const p of (b.schedule || [])) { const k = (p.date || '').slice(0, 7); if (k) set.add(k); }
    return Array.from(set).sort();
  });

  private _todayKey(): string { const t = new Date(); return `${t.getFullYear()}-${String(t.getMonth() + 1).padStart(2, '0')}`; }

  /** The month currently shown: the user's pick if still valid, else this month
   *  (or the next upcoming one). Derived from monthsList — NOT repayChart — so
   *  there's no circular dependency with the chart's highlight. */
  activeMonth = computed(() => {
    const months = this.monthsList();
    if (!months.length) return '';
    const sel = this.selectedMonth();
    if (sel && months.includes(sel)) return sel;
    const now = this._todayKey();
    return months.find(m => m >= now) || months[months.length - 1];
  });

  selectBar(month: string) { this.selectedMonth.set(month); }
  canStepActive(delta: number): boolean {
    const m = this.monthsList(); const i = m.indexOf(this.activeMonth());
    return i >= 0 && i + delta >= 0 && i + delta < m.length;
  }
  stepActiveMonth(delta: number) {
    const m = this.monthsList(); const i = m.indexOf(this.activeMonth());
    if (i >= 0 && i + delta >= 0 && i + delta < m.length) this.selectedMonth.set(m[i + delta]);
  }

  // stable colour per bond for the share bar + legend (mirrors the stocks allocation)
  private _palette = ['#16a34a', '#2563eb', '#7c3aed', '#f59e0b', '#0891b2', '#db2777',
                      '#65a30d', '#9333ea', '#0d9488', '#e11d48', '#4f46e5', '#ca8a04', '#15803d', '#ea580c'];
  segColor(i: number): string { return this._palette[i % this._palette.length]; }

  // which person card is expanded (to reveal their per-broker breakdown)
  expandedPerson = signal<string | null>(null);
  togglePerson(owner: string) { this.expandedPerson.update(p => p === owner ? null : owner); }

  // ── Pending SIPs — a planned buy you log the real holdings of once it executes ──
  sips = signal<BondSip[]>([]);
  pendingSips = computed(() => this.sips().filter(s => s.status === 'pending'));
  highlightSip = signal<string | null>(null);   // set when arriving from a reminder link
  showSipForm = signal(false);
  editingSipId = signal<string | null>(null);
  sipDraft = signal<SipDraft>({ ...EMPTY_SIP, splits: [{ name: '', amount: 0 }] });
  savingSip = signal(false);

  private loadSips() {
    this.api.listSips().subscribe({ next: s => this.sips.set(s || []), error: () => {} });
  }

  /** Sum of a SIP's splits — shown against the total so the split is easy to balance. */
  sipSplitSum(s: { splits: BondSipSplit[] }): number { return (s.splits || []).reduce((a, x) => a + (+x.amount || 0), 0); }
  sipDraftSum = computed(() => this.sipDraft().splits.reduce((a, x) => a + (+x.amount || 0), 0));

  openAddSip() {
    this.editingSipId.set(null);
    this.sipDraft.set({ ...EMPTY_SIP, splits: [{ name: '', amount: 0 }] });
    this.showSipForm.set(true);
  }
  openEditSip(s: BondSip) {
    this.editingSipId.set(s.id);
    this.sipDraft.set({
      id: s.id, owner: s.owner || '', total: s.total, expected_date: (s.expected_date || '').slice(0, 10),
      note: s.note || '', splits: (s.splits && s.splits.length ? s.splits.map(x => ({ ...x })) : [{ name: '', amount: 0 }]),
    });
    this.showSipForm.set(true);
  }
  closeSipForm() { this.showSipForm.set(false); }
  setSipField<K extends keyof SipDraft>(key: K, val: SipDraft[K]) { this.sipDraft.update(d => ({ ...d, [key]: val })); }
  setSipSplit(i: number, key: 'name' | 'amount', val: any) {
    this.sipDraft.update(d => ({ ...d, splits: d.splits.map((s, idx) => idx === i ? { ...s, [key]: key === 'amount' ? (+val || 0) : val } : s) }));
  }
  addSipSplit() { this.sipDraft.update(d => ({ ...d, splits: [...d.splits, { name: '', amount: 0 }] })); }
  removeSipSplit(i: number) { this.sipDraft.update(d => ({ ...d, splits: d.splits.filter((_, idx) => idx !== i) })); }

  canSaveSip = computed(() => {
    const d = this.sipDraft();
    return +(d.total || 0) > 0 && !!d.expected_date;
  });
  saveSip() {
    if (!this.canSaveSip()) return;
    const d = this.sipDraft();
    const payload: BondSipInput = {
      owner: (d.owner || '').trim(), total: +(d.total || 0), expected_date: d.expected_date,
      note: (d.note || '').trim(),
      splits: d.splits.filter(s => (s.name || '').trim()).map(s => ({ name: s.name.trim(), amount: +s.amount || 0 })),
    };
    this.savingSip.set(true);
    const id = this.editingSipId();
    const req = id ? this.api.updateSip(id, payload) : this.api.addSip(payload);
    req.subscribe({
      next: () => { this.savingSip.set(false); this.showSipForm.set(false); this.loadSips(); },
      error: (e: HttpErrorResponse) => { this.savingSip.set(false); this.error.set('Could not save the SIP — ' + this.errText(e)); },
    });
  }
  removeSip(s: BondSip) {
    if (!confirm(`Delete this pending SIP of ${this.inrFull(s.total)}?`)) return;
    this.api.removeSip(s.id).subscribe({ next: () => this.loadSips(), error: () => this.error.set('Could not delete the SIP.') });
  }
  /** Mark a SIP logged — it stops nudging you on the dashboard. */
  markSipLogged(s: BondSip) {
    this.api.updateSip(s.id, { status: 'logged' }).subscribe({ next: () => this.loadSips(), error: () => this.error.set('Could not update the SIP.') });
  }
  reopenSip(s: BondSip) {
    this.api.updateSip(s.id, { status: 'pending' }).subscribe({ next: () => this.loadSips(), error: () => {} });
  }
  /** "Log as a bond": open the Add-Bond form pre-filled from one SIP split so the
   *  real purchase becomes an actual holding; the SIP is then marked logged. */
  logSplitAsBond(s: BondSip, split: BondSipSplit) {
    this.openAdd();
    this.draft.update(d => ({
      ...d, owner: s.owner || d.owner, issuer: split.name || '',
      note: `From SIP of ${this.inrFull(s.total)} (${this.fmtDate(s.expected_date)})`,
      purchase_date: (s.expected_date || '').slice(0, 10),
    }));
    this.view.set('investments');
  }

  /** Everything about the active month, over the filtered bonds: totals, a
   *  per-bond share breakdown (the "ratio"), a money-by-day calendar grid, and
   *  every individual payment — all shown inline (no popup). */
  monthDetail = computed(() => {
    const mk = this.activeMonth();
    if (!mk) return null;
    const net = this.tds();
    const rows = new Map<string, any>();
    const acc = new Map<string, any>();
    const dayMap = new Map<number, any>();
    const payments: any[] = [];
    let tI = 0, tP = 0, tT = 0, tN = 0;
    for (const b of this.filteredBonds()) {
      for (const p of (b.schedule || [])) {
        if ((p.date || '').slice(0, 7) !== mk) continue;
        const tds = b.tax_free ? 0 : (p.interest || 0) * 0.1;
        const pnet = (p.total || 0) - tds;
        tI += p.interest || 0; tP += p.principal || 0; tT += p.total || 0; tN += pnet;
        const key = (b as any).id || b.issuer;
        let r = rows.get(key);
        if (!r) { r = { issuer: b.issuer, owner: b.owner, broker: b.broker, rating: b.rating, interest: 0, principal: 0, total: 0, net: 0, count: 0 }; rows.set(key, r); }
        r.interest += p.interest || 0; r.principal += p.principal || 0; r.total += p.total || 0; r.net += pnet; r.count++;
        const ak = (b.owner || '—') + '|' + (b.broker || '');
        let a = acc.get(ak);
        if (!a) { a = { owner: b.owner || '—', broker: b.broker || '', interest: 0, principal: 0, total: 0, net: 0, intDue: 0, intRecv: 0 }; acc.set(ak, a); }
        a.interest += p.interest || 0; a.principal += p.principal || 0; a.total += p.total || 0; a.net += pnet;
        const day = +(p.date || '').slice(8, 10);
        const fullDate = (p.date || '').slice(0, 10);
        const bid = (b as any).id || b.issuer;
        const st = this.statusOf(bid, fullDate);      // received | pending | not_received
        // net interest (of TDS) due this month per account + how much is collected
        const niNet = (p.interest || 0) - tds;
        a.intDue += niNet;
        if (st === 'received') a.intRecv += niNet;
        let dd = dayMap.get(day);
        if (!dd) { dd = { interest: 0, principal: 0, total: 0, net: 0, count: 0, items: [], received: 0, pending: 0, notrecv: 0, netReceived: 0, netPending: 0, netNotrecv: 0 }; dayMap.set(day, dd); }
        dd.interest += p.interest || 0; dd.principal += p.principal || 0; dd.total += p.total || 0; dd.net += pnet; dd.count++;
        // status is graded on the gross payout (did the money arrive), not the series toggle
        if (st === 'received') { dd.received += p.total || 0; dd.netReceived += pnet; }
        else if (st === 'not_received') { dd.notrecv += p.total || 0; dd.netNotrecv += pnet; }
        else { dd.pending += p.total || 0; dd.netPending += pnet; }
        dd.items.push({ issuer: b.issuer, status: st });
        payments.push({ date: fullDate, day, bond_id: bid, status: st, issuer: b.issuer, owner: b.owner, broker: b.broker, interest: p.interest || 0, principal: p.principal || 0, total: p.total || 0, net: pnet, tax_free: b.tax_free });
      }
    }
    if (!payments.length) return null;

    // The Both/Interest/Principal toggle filters EVERYTHING below the timeline.
    // `pick` returns the value for the active series (TDS-aware for interest).
    const ser = this.series();                       // 'both' | 'interest' | 'principal'
    const pick = (o: { interest: number; principal: number; total: number; net: number }) =>
      ser === 'interest'  ? (net ? o.net - o.principal : o.interest)
      : ser === 'principal' ? o.principal
      : (net ? o.net : o.total);
    const denom = pick({ interest: tI, principal: tP, total: tT, net: tN }) || 1;

    const bonds = Array.from(rows.values())
      .map(r => ({ ...r, val: pick(r), pct: pick(r) / denom }))
      .filter(r => r.val > 0)
      .sort((a, b) => b.val - a.val);
    const accounts = Array.from(acc.values())
      .map(a => ({ ...a, val: pick(a), pct: pick(a) / denom }))
      .filter(a => a.val > 0)
      .sort((a, b) => b.val - a.val);
    // group accounts by person → cards (each person + their per-broker breakdown)
    const perMap = new Map<string, any>();
    for (const a of accounts) {
      let p = perMap.get(a.owner);
      if (!p) { p = { owner: a.owner, val: 0, interest: 0, principal: 0, total: 0, net: 0, intDue: 0, intRecv: 0, brokers: [] as any[] }; perMap.set(a.owner, p); }
      p.val += a.val; p.interest += a.interest; p.principal += a.principal; p.total += a.total; p.net += a.net;
      p.intDue += a.intDue || 0; p.intRecv += a.intRecv || 0;
      p.brokers.push({ broker: a.broker, val: a.val, interest: a.interest, principal: a.principal, net: a.net });
    }
    const people = Array.from(perMap.values()).sort((a, b) => b.val - a.val);
    people.forEach(p => { p.brokers.sort((x: any, y: any) => y.val - x.val); p.intPct = p.intDue > 0 ? Math.min(1, p.intRecv / p.intDue) : 0; });

    // per-payment value for the active series; schedule shows only relevant rows
    payments.forEach(p => { p.val = pick(p); });
    const seriesPayments = payments.filter(p => p.val > 0).sort((a, b) => a.date.localeCompare(b.date) || b.val - a.val);

    const [y, m] = mk.split('-').map(Number);
    const firstDow = new Date(y, m - 1, 1).getDay();
    const dim = new Date(y, m, 0).getDate();
    // ── status colouring (green received · orange pending · red not-received ·
    // blend partly), graded by the day's gross payout vs the busiest day this
    // month — one shared scale so darker = more money, exactly like Dividends.
    const dvals = Array.from(dayMap.values());
    const dayMax = Math.max(0, ...dvals.map(d => d.total));
    const level = (v: number): number => {
      if (v <= 0 || dayMax <= 0) return 0;
      const r = v / dayMax;
      return r <= 0.25 ? 1 : r <= 0.5 ? 2 : r <= 0.75 ? 3 : 4;
    };
    const cells: any[] = [];
    for (let i = 0; i < firstDow; i++) cells.push(null);
    for (let d = 1; d <= dim; d++) {
      const dd = dayMap.get(d);
      const total = dd ? dd.total : 0;
      const interest = dd ? dd.interest : 0;
      const principal = dd ? dd.principal : 0;
      const received = dd ? dd.received : 0, pending = dd ? dd.pending : 0, notrecv = dd ? dd.notrecv : 0;
      const awaiting = pending + notrecv;
      let tone = 'bs-l0';
      if (received > 0 && awaiting > 0) tone = 'mx' + (level(awaiting) || 1);            // partly collected
      else if (notrecv > 0 && pending === 0 && received === 0) tone = 'rd' + (level(notrecv) || 1); // not received → red
      else if (awaiting > 0 && received === 0) tone = 'yl' + (level(awaiting) || 1);      // awaiting → orange
      else if (received > 0) tone = 'bs-l' + (level(received) || 1);                      // received → green
      const netTotal = dd ? dd.net : 0;                              // total less 10% TDS on interest
      cells.push({
        day: d, amount: total, total, net: netTotal, interest, principal, received, pending, notrecv, tone,
        netReceived: dd ? dd.netReceived : 0, netPending: dd ? dd.netPending : 0, netNotrecv: dd ? dd.netNotrecv : 0,
        count: dd ? dd.count : 0, items: dd ? dd.items : [],
        isToday: dd ? `${mk}-${String(d).padStart(2, '0')}` === this._todayIso : false,
      });
    }
    return { month: mk, label: this.monthLabel(mk), series: ser,
             interest: tI, principal: tP, total: tT, net: tN, seriesTotal: pick({ interest: tI, principal: tP, total: tT, net: tN }),
             bonds, accounts, people, top: bonds[0] || null,
             payments: seriesPayments, cells, payCount: seriesPayments.length };
  });

  dayTip(c: { items: { issuer: string }[] }): string {
    return (c.items || []).map(i => i.issuer).join(', ');
  }

  // ── day modal — click a calendar cell to mark each payout received/pending/not ──
  showDay = signal(false);
  dayKey = signal('');                    // YYYY-MM-DD being inspected
  openDay(month: string, day: number) {
    this.dayKey.set(`${month}-${String(day).padStart(2, '0')}`);
    this.showDay.set(true);
  }
  dayLabelFull = computed(() => {
    const dt = new Date(this.dayKey());
    return isNaN(dt.getTime()) ? this.dayKey() : dt.toLocaleDateString('en-IN', { weekday: 'short', day: '2-digit', month: 'short', year: 'numeric' });
  });
  /** Every bond payout on the inspected day (across the filtered bonds). */
  dayPayments = computed(() => {
    const dk = this.dayKey();
    if (!dk) return [];
    const out: { bond_id: string; issuer: string; owner: string; broker: string; rating: string | null;
                 interest: number; principal: number; total: number; net: number; tax_free: boolean; status: PaymentStatus }[] = [];
    for (const b of this.filteredBonds()) {
      for (const p of (b.schedule || [])) {
        if ((p.date || '').slice(0, 10) !== dk) continue;
        const tds = b.tax_free ? 0 : (p.interest || 0) * 0.1;
        const bid = (b as any).id || b.issuer;
        out.push({ bond_id: bid, issuer: b.issuer, owner: b.owner, broker: b.broker, rating: b.rating ?? null,
                   interest: p.interest || 0, principal: p.principal || 0, total: p.total || 0, net: (p.total || 0) - tds,
                   tax_free: b.tax_free, status: this.statusOf(bid, dk) });
      }
    }
    return out.sort((a, b) => b.total - a.total);
  });
  dayTotals = computed(() => {
    const net = this.tds();
    let total = 0, received = 0, pending = 0, notrecv = 0;
    for (const p of this.dayPayments()) {
      if (p.status === 'received') {
        received += p.net;                          // REALIZED: always the actual net you received,
        total += p.net;                             // independent of the gross/net toggle
      } else {
        const v = net ? p.net : p.total;            // still-expected: follows the toggle (a projection)
        total += v;
        if (p.status === 'not_received') notrecv += v; else pending += v;
      }
    }
    return { total, received, pending, notrecv, count: this.dayPayments().length };
  });

  /** "To receive": payouts that are due (on/before today) but not yet marked
   *  received — the actionable money you should chase / confirm. */
  pendingTotal = computed(() => {
    const today = this._todayIso;
    const net = this.tds();
    let sum = 0;
    for (const b of this.filteredBonds()) {
      const bid = (b as any).id || b.issuer;
      for (const p of (b.schedule || [])) {
        const dt = (p.date || '').slice(0, 10);
        if (!dt || dt > today) continue;
        if (this.statusOf(bid, dt) === 'pending') {
          const tds = (net && !b.tax_free) ? (p.interest || 0) * 0.1 : 0;
          sum += (p.total || 0) - tds;
        }
      }
    }
    return sum;
  });
  statusLabel(s: PaymentStatus): string {
    return s === 'received' ? 'Received' : s === 'not_received' ? 'Not received' : 'Expected';
  }
  /** Tooltip for a calendar cell — the day's money split by status (net-of-TDS
   *  when the TDS toggle is on, to match the amount shown on the day). */
  statusTip(c: { total: number; net: number; received: number; pending: number; notrecv: number;
                 netReceived: number; netPending: number; netNotrecv: number }): string {
    const net = this.tds();
    const rv = c.netReceived;                     // realized: always the actual net received
    const pv = net ? c.netPending : c.pending;    // still-expected: follows the toggle
    const nv = net ? c.netNotrecv : c.notrecv;
    const parts: string[] = [];
    if (rv > 0) parts.push('Received ' + this.inrShort(rv));
    if (pv > 0) parts.push('Pending ' + this.inrShort(pv));
    if (nv > 0) parts.push('Not received ' + this.inrShort(nv));
    return parts.join(' · ') || this.inrShort(net ? c.net : c.total);
  }
  /** Mark every payout on the inspected day with one status (bulk shortcut). */
  markAllDay(status: PaymentStatus) {
    const dk = this.dayKey();
    for (const p of this.dayPayments()) this.setPayStatus(p.bond_id, dk, status);
  }
  /** Compact ₹ for tight spots (calendar cells): ₹1.1L, ₹25k. */
  inrShort(v: number): string {
    const a = Math.abs(v || 0);
    // floor to one decimal so 1.67k reads as 1.6k (truncate, don't round up) —
    // gives a truer picture than snapping to whole 2k/3k.
    const floor1 = (x: number) => (Math.floor(x * 10) / 10).toFixed(1);
    if (a >= 1e7) return '₹' + (a >= 1e8 ? Math.floor(v / 1e7).toString() : floor1(v / 1e7)) + 'Cr';
    if (a >= 1e5) return '₹' + floor1(v / 1e5) + 'L';
    if (a >= 1e3) return '₹' + floor1(v / 1e3) + 'k';
    return '₹' + Math.round(v);
  }

  /** Triple-tap a bar → inspect that month AND scroll to it in the schedule. */
  jumpToMonth(month: string) {
    this.pickMonth(month);
    setTimeout(() => {
      const el = document.getElementById('sched-' + month);
      if (!el) return;
      el.scrollIntoView({ behavior: 'smooth', block: 'center' });
      this.flashMonth.set(month);
      setTimeout(() => this.flashMonth.set(null), 1600);
    });
  }

  /** Step the inspected day by ±1 calendar month (panel arrows). */
  stepMonth(delta: number) {
    const d = this.pickedDate();
    if (!d) return;
    const [y, m, day] = d.split('-').map(Number);
    const nd = new Date(y, m - 1 + delta, day);
    this.pickedDate.set(`${nd.getFullYear()}-${String(nd.getMonth() + 1).padStart(2, '0')}-${String(nd.getDate()).padStart(2, '0')}`);
  }

  // ── credit-rating gauge (semicircle with zones + needle) ────────────────────
  readonly gaugeR = 80;
  readonly ratingGaugeMax = 8;
  ratingGauge = computed(() => {
    const cr = this.summary()?.combined_rating;
    if (!cr) return null;
    const R = this.gaugeR, cx = 100, cy = 100;
    const L = Math.PI * R;
    const GMIN = 1, GMAX = cr.max || 8, span = GMAX - GMIN;
    const ZONES = [
      { from: 1, to: 3.5, color: '#e5484d' },   // low / junk
      { from: 3.5, to: 5, color: '#e0892a' },   // BBB–A-
      { from: 5, to: 6.5, color: '#d0b21a' },   // A range
      { from: 6.5, to: 8, color: '#16a06a' },   // AA–AAA
    ];
    const zones = ZONES.map(z => ({
      dash: `${(z.to - z.from) / span * L} ${L}`,
      offset: -((z.from - GMIN) / span * L),
      color: z.color,
    }));
    const frac = Math.max(0, Math.min(1, (cr.score - GMIN) / span));
    const theta = Math.PI * (1 - frac);                  // 180° (low) → 0° (high)
    const nLen = R - 14;
    const needle = { x: cx + nLen * Math.cos(theta), y: cy - nLen * Math.sin(theta) };
    let color = '#16a06a';
    if (cr.score < 3.5) color = '#e5484d';
    else if (cr.score < 5) color = '#e0892a';
    else if (cr.score < 6.5) color = '#d0b21a';
    return { L, zones, needle, cx, cy, label: cr.label, score: cr.score, max: GMAX, pct: cr.rated_pct, color };
  });

  /** Per-account breakdown of a single month's payments (who gets paid what). */
  monthAccounts(m: PaymentMonth) {
    const map = new Map<string, { owner: string; broker: string; interest: number; principal: number; total: number; net: number }>();
    for (const p of m.payments) {
      const key = p.owner + '|' + p.broker;
      const a = map.get(key) || { owner: p.owner, broker: p.broker, interest: 0, principal: 0, total: 0, net: 0 };
      a.interest += p.interest; a.principal += p.principal; a.total += p.total; a.net += p.net;
      map.set(key, a);
    }
    return Array.from(map.values()).sort((x, y) => y.total - x.total);
  }
  /** Net-of-TDS interest for a single payment (principal never taxed). */
  payInt(p: { interest: number; principal: number; net: number; tax_free: boolean }): number {
    return this.tds() && !p.tax_free ? p.net - p.principal : p.interest;
  }
  /** Month total interest respecting the TDS toggle. */
  mInt(m: PaymentMonth): number { return this.tds() ? m.interest - m.tds : m.interest; }

  /** Live mini repayment chart inside the form, from the editable schedule. */
  draftBars = computed(() => {
    const rows = this.scheduleDraft().filter(r => r.date);
    if (!rows.length) return null;
    const max = Math.max(...rows.map(r => (+r.interest || 0) + (+r.principal || 0)), 1);
    return rows.map(r => ({
      date: r.date,
      iH: (+r.interest || 0) / max * 100,
      pH: (+r.principal || 0) / max * 100,
      total: (+r.interest || 0) + (+r.principal || 0),
    }));
  });

  // ── TDS-aware money helpers ─────────────────────────────────────────────────
  /** Amount for a month/payment respecting the TDS toggle. */
  amt(x: { total: number; net: number }): number { return this.tds() ? x.net : x.total; }
  incomeMonthly = computed(() => { const s = this.summary(); return s ? (this.tds() ? s.total_monthly_income_net : s.total_monthly_income) : 0; });
  incomeAnnual = computed(() => { const s = this.summary(); return s ? (this.tds() ? s.total_annual_income_net : s.total_annual_income) : 0; });

  /** Bond rating → severity class for the badge colour. */
  ratingClass(r: string | null | undefined): string {
    if (!r) return '';
    const u = r.toUpperCase().replace(/\s/g, '');
    if (u.startsWith('AAA')) return 'r-aaa';
    if (u.startsWith('AA')) return 'r-aa';
    if (u.startsWith('A')) return 'r-a';
    if (u.startsWith('BBB')) return 'r-bbb';
    return 'r-low';
  }

  monthLabel(m: string): string {
    const [y, mo] = (m || '').split('-');
    const names = ['', 'January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
    return mo ? `${names[+mo]} ${y}` : m;
  }
  monthShort(m: string): string {
    const [y, mo] = (m || '').split('-');
    const names = ['', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    return mo ? `${names[+mo]} '${y.slice(2)}` : m;
  }
  dayLabel(d: string): string {
    const dt = new Date((d || '').slice(0, 10));
    return isNaN(dt.getTime()) ? d : dt.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' });
  }
  fmtDate(d: string | null): string {
    if (!d) return '—';
    const dt = new Date(d.slice(0, 10));
    return isNaN(dt.getTime()) ? d : dt.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' });
  }
  fmtDateTime(d: string | null): string {
    if (!d) return '—';
    const dt = new Date(d);
    return isNaN(dt.getTime()) ? d : dt.toLocaleDateString('en-IN', { day: '2-digit', month: 'short' }) + ', ' + dt.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' });
  }
  spanText(yrs: number | null): string {
    if (yrs == null || yrs <= 0) return '—';
    if (yrs < 1) return Math.max(1, Math.round(yrs * 12)) + ' mo';
    return yrs.toFixed(1) + ' yrs';
  }
}

// ── helpers ───────────────────────────────────────────────────────────────────
function round2(v: number): number { return Math.round(v * 100) / 100; }

/** XIRR via bisection over {t(ms), a(amount)} flows. Mirrors the backend. */
function xirr(flows: { t: number; a: number }[]): number | null {
  const valid = flows.filter(f => isFinite(f.t));
  if (valid.length < 2) return null;
  const t0 = Math.min(...valid.map(f => f.t));
  const yrs = valid.map(f => (f.t - t0) / (365 * 864e5));
  const amts = valid.map(f => f.a);
  const npv = (r: number) => {
    let s = 0;
    for (let i = 0; i < amts.length; i++) {
      const d = Math.pow(1 + r, yrs[i]);
      if (!isFinite(d) || d === 0) return Infinity;
      s += amts[i] / d;
    }
    return s;
  };
  let lo = -0.9999, hi = 10;
  let flo = npv(lo), fhi = npv(hi);
  if (flo * fhi > 0) { hi = 1000; fhi = npv(hi); if (flo * fhi > 0) return null; }
  for (let i = 0; i < 200; i++) {
    const mid = (lo + hi) / 2, fm = npv(mid);
    if (Math.abs(fm) < 1e-2) return Math.round(mid * 1e6) / 1e6;
    if (flo * fm < 0) hi = mid; else { lo = mid; flo = fm; }
  }
  return Math.round(((lo + hi) / 2) * 1e6) / 1e6;
}
