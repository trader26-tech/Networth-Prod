import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpErrorResponse } from '@angular/common/http';
import { LicService, LicSummary, LicPolicy, LicMeta, LicInput, PlanType, Frequency, PolicyStatus } from '../../services/lic.service';

type StatusFilter = 'all' | 'active' | 'matured' | 'surrendered';

@Component({
  selector: 'app-lic',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './lic.html',
  styleUrl: './lic.scss',
})
export class Lic implements OnInit {
  private api = inject(LicService);

  inr = LicService.inr;
  inrFull = LicService.inrFull;

  summary = signal<LicSummary | null>(null);
  meta = signal<LicMeta | null>(null);
  loading = signal(true);
  error = signal<string | null>(null);
  notice = signal<string | null>(null);

  // filters
  statusFilter = signal<StatusFilter>('active');
  holderFilter = signal<string>('all');
  query = signal('');
  expanded = signal<string | null>(null);

  // add / edit
  showAdd = signal(false);
  saving = signal(false);
  draft = signal<LicInput>(this._blankDraft());
  editingId = signal<string | null>(null);
  editDraft = signal<Partial<LicInput>>({});

  ngOnInit() { this.reload(); }

  reload() {
    this.loading.set(true);
    this.api.meta().subscribe({ next: m => this.meta.set(m), error: () => {} });
    this.api.summary().subscribe({
      next: s => { this.summary.set(s); this.loading.set(false); this.error.set(null); },
      error: (e: HttpErrorResponse) => { this.loading.set(false); this.error.set(e.error?.detail || 'Could not load LIC policies.'); },
    });
  }

  // ── derived ────────────────────────────────────────────────────────────────
  holders = computed(() => (this.summary()?.by_holder || []).map(h => h.holder));
  holderCount = computed(() => (this.summary()?.by_holder || []).length);

  // ── hero hover breakdowns — "what happens when, and from which policy" ───────
  private activePolicies = computed(() => (this.summary()?.policies || []).filter(p => p.is_active));

  /** Every future maturity, soonest first: the payout timeline behind "will receive". */
  maturityTimeline = computed(() =>
    this.activePolicies()
      .filter(p => (p.expected_maturity || 0) > 0)
      .map(p => ({
        plan: p.plan, holder: p.holder || '—', amount: p.expected_maturity || 0,
        date: p.maturity_date, year: p.maturity_year, yrs: p.years_to_maturity,
        when: this.fmtMonthYear(p.maturity_date),
      }))
      .sort((a, b) => (a.date || '').localeCompare(b.date || '')));

  /** Life cover by policy (what the family gets if something happens). */
  coverContribs = computed(() =>
    this.activePolicies()
      .filter(p => (p.life_cover || 0) > 0)
      .map(p => ({ plan: p.plan, holder: p.holder || '—', amount: p.life_cover || 0, when: 'while running' }))
      .sort((a, b) => b.amount - a.amount));

  /** Yearly premium by policy (what you pay to keep them alive). */
  premiumContribs = computed(() =>
    this.activePolicies()
      .filter(p => (p.annual_premium || 0) > 0)
      .map(p => ({ plan: p.plan, holder: p.holder || '—', amount: p.annual_premium || 0,
                   when: this.freqLabel(p.premium_frequency) }))
      .sort((a, b) => b.amount - a.amount));

  /** Money already in — matured & surrendered policies. */
  receivedContribs = computed(() =>
    (this.summary()?.policies || [])
      .filter(p => p.is_matured || p.is_surrendered)
      .map(p => ({ plan: p.plan, holder: p.holder || '—',
                   amount: (p.is_surrendered ? p.fund_value : p.total_maturity) || 0,
                   when: p.is_surrendered ? 'surrendered' : ('matured ' + (p.maturity_year || '')) }))
      .sort((a, b) => b.amount - a.amount));

  /** Years until the next maturity (for the hero side callout). */
  nextMaturityYears = computed(() => {
    const n = this.summary()?.next_maturity; if (!n?.date) return null;
    const days = (new Date(n.date + 'T00:00:00').getTime() - Date.now()) / 86400000;
    return days > 0 ? Math.max(0, Math.round(days / 365 * 10) / 10) : 0;
  });

  filtered = computed<LicPolicy[]>(() => {
    const all = this.summary()?.policies || [];
    const sf = this.statusFilter();
    const hf = this.holderFilter();
    const q = this.query().trim().toLowerCase();
    return all.filter(p => {
      if (sf === 'active' && !p.is_active) return false;
      if (sf === 'matured' && !p.is_matured) return false;
      if (sf === 'surrendered' && !p.is_surrendered) return false;
      if (hf !== 'all' && p.holder !== hf) return false;
      if (q && !(`${p.plan} ${p.policy_number} ${p.holder} ${p.type_label}`.toLowerCase().includes(q))) return false;
      return true;
    });
  });

  statusCounts = computed(() => {
    const s = this.summary();
    return { all: s?.count ?? 0, active: s?.active_count ?? 0, matured: s?.matured_count ?? 0, surrendered: s?.surrendered_count ?? 0 };
  });

  // ── helpers ──────────────────────────────────────────────────────────────
  toggleExpand(id: string) { this.expanded.update(v => v === id ? null : id); }

  private ladderMax = computed(() => Math.max(1, ...(this.summary()?.ladder || []).map(l => l.amount || 0)));
  /** bar height as a % of the biggest payout (min 18% so tiny bars stay visible) */
  ladderH(amount: number | null): number {
    return Math.round(18 + 82 * ((amount || 0) / this.ladderMax()));
  }

  fmtDate(d: string | null): string {
    if (!d) return '—';
    const dt = new Date(d + (d.length <= 10 ? 'T00:00:00' : ''));
    return isNaN(dt.getTime()) ? d : dt.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' });
  }
  fmtMonthYear(d: string | null): string {
    if (!d) return '—';
    const dt = new Date(d + 'T00:00:00');
    return isNaN(dt.getTime()) ? d : dt.toLocaleDateString('en-IN', { month: 'short', year: 'numeric' });
  }
  statusLabel(p: LicPolicy): string {
    return p.is_active ? 'Running' : p.is_matured ? 'Matured — paid out' : 'Surrendered';
  }
  freqLabel(f: Frequency | null): string {
    return ({ monthly: 'monthly', quarterly: 'every 3 months', half_yearly: 'twice a year', yearly: 'yearly', single: 'one-time' } as any)[f || 'yearly'] || 'yearly';
  }

  // ── add / edit / delete ──────────────────────────────────────────────────
  private _blankDraft(): LicInput {
    return { plan: '', plan_type: 'endowment', premium_frequency: 'yearly', status: 'active', holder: '' };
  }
  openAdd() { this.draft.set(this._blankDraft()); this.showAdd.set(true); }
  setDraft<K extends keyof LicInput>(k: K, v: LicInput[K]) { this.draft.update(d => ({ ...d, [k]: v })); }

  add() {
    const d = this.draft();
    if (!d.plan?.trim()) { this.error.set('Plan name is required.'); return; }
    this.saving.set(true);
    this.api.addItem(d).subscribe({
      next: () => { this.saving.set(false); this.showAdd.set(false); this._flash('Policy added'); this.reload(); },
      error: (e: HttpErrorResponse) => { this.saving.set(false); this.error.set(e.error?.detail || 'Could not add policy.'); },
    });
  }

  startEdit(p: LicPolicy) {
    this.editingId.set(p.id);
    this.editDraft.set({
      holder: p.holder, plan: p.plan, plan_type: p.plan_type ?? 'endowment', policy_number: p.policy_number,
      status: p.status, term_years: p.term_years, premium_paying_years: p.premium_paying_years,
      premium: p.premium, premium_frequency: p.premium_frequency,
      premium_annual: p.premium_annual, paid_by: p.paid_by, start_date: p.start_date, maturity_date: p.maturity_date,
      sum_assured: p.sum_assured, maturity_amount: p.maturity_amount, bonus: p.bonus, total_maturity: p.total_maturity,
      fund_value: p.fund_value, invested: p.invested, remarks: p.remarks,
      whole_life: p.whole_life, accident_benefit: p.accident_benefit,
      nominee: p.nominee, nominee_relation: p.nominee_relation, nominee_phone: p.nominee_phone,
      agent_name: p.agent_name, agent_phone: p.agent_phone, branch: p.branch,
    });
  }
  /** open the edit modal straight onto a holder's first active policy — used by the
   *  claim card's "Add nominee / contact" prompt so the user lands on the right form. */
  editHolderContact(holder: string) {
    const p = (this.summary()?.policies || []).find(x => x.holder === holder && x.is_active)
      || (this.summary()?.policies || []).find(x => x.holder === holder);
    if (p) { this.startEdit(p); }
  }
  setEdit<K extends keyof LicInput>(k: K, v: LicInput[K]) { this.editDraft.update(d => ({ ...d, [k]: v })); }
  saveEdit() {
    const id = this.editingId(); if (!id) return;
    this.saving.set(true);
    this.api.updateItem(id, this.editDraft()).subscribe({
      next: () => { this.saving.set(false); this.editingId.set(null); this._flash('Policy updated'); this.reload(); },
      error: (e: HttpErrorResponse) => { this.saving.set(false); this.error.set(e.error?.detail || 'Could not update.'); },
    });
  }
  cancelEdit() { this.editingId.set(null); }

  remove(p: LicPolicy) {
    if (!confirm(`Remove ${p.plan} (${p.policy_number})? This deletes it from the register.`)) return;
    this.api.deleteItem(p.id).subscribe({
      next: () => { this._flash('Policy removed'); this.reload(); },
      error: (e: HttpErrorResponse) => this.error.set(e.error?.detail || 'Could not delete.'),
    });
  }

  private _flash(msg: string) { this.notice.set(msg); setTimeout(() => this.notice.set(null), 3500); }
}
