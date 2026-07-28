import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';

function _apiBase(): string {
  if (typeof window === 'undefined') return 'http://localhost:8000/api';
  const override = (window as any).__API_BASE__;
  if (override) return override;
  const { hostname, protocol, host } = window.location;
  if (hostname === 'localhost' || hostname === '127.0.0.1') return 'http://localhost:8000/api';
  return `${protocol}//${host}/api`;
}

export type Residency = 'resident' | 'nri' | 'rnor';

export interface TaxProfile {
  name: string; residency: Residency | null; relation: string | null;
  spouse: string | null; dob: string | null; pan: string | null;
  other_income: number | null; note: string | null;
  age: number | null; is_minor: boolean; is_senior: boolean;
  house_count?: number;
}

export interface TaxOption {
  method: string; rate_label: string; gain: number;
  base_tax: number; surcharge_rate: number; surcharge: number; cess: number; total: number;
}
export interface TaxBase {
  asset: string; owner: string; seller: string; residency: string;
  sale_price: number; cost: number; indexed_cost: number;
  holding: { months: number; years: number; long_term: boolean };
  gain: number; gain_indexed: number; pre_2001: boolean; house_count: number;
  term: 'long' | 'short'; tax: number | null; effective_pct?: number;
  options?: { flat: TaxOption; indexed: TaxOption | null; can_index: boolean; chosen: TaxOption };
  sec54F_eligible?: boolean; sec54EC_shield?: number;
  tds?: { section: string; rate: number; on: string; amount: number; note: string };
  note?: string;
}
export interface TaxScenario {
  key: string; type: 'cash' | 'reinvest' | 'blocked'; for_owner: boolean;
  title: string; tax: number; saved: number; detail: string; caveats: string[];
}
export interface ParcelAnalysis {
  base: TaxBase; scenarios: TaxScenario[];
  best_cash?: TaxScenario; best?: TaxScenario;
}
export interface LandTax {
  parcels: ParcelAnalysis[];
  portfolio: {
    count: number; total_gain: number; tax_now: number;
    tax_best_cash: number; tax_best: number; saving_cash: number; saving_max: number;
  };
  assumptions: { fy: string; cii_sale: number; notes: string[] };
}
export interface TaxProfilesResp { profiles: TaxProfile[]; house_counts: Record<string, number>; }

export interface PlanPrefs {
  transfer_ok: boolean; sell_fast: boolean; bonds_amount: number; house_amount: number;
}
export interface PlanParcel {
  asset: string; owner: string; sale_value: number; gain: number; seller: string; via_gift: boolean;
  tax_naive: number; tax_opt: number; saved: number; bonds: number; house: number;
  cash_in_hand: number; used_54F: boolean; rate_label: string; steps: string[]; effective_rate: number;
}
export interface PlanTotals { sale: number; gain: number; tax_naive: number; tax_opt: number; saved: number; cash: number; bonds: number; house: number; }
export interface SalePlan {
  parcels: PlanParcel[]; totals: PlanTotals; prefs: PlanPrefs;
}
export interface TaxConfig {
  key: string; label: string; badge: string; why: string;
  totals: PlanTotals; parcels: PlanParcel[]; prefs: PlanPrefs;
}
export interface LandConfigs {
  sell_now_tax: number; max_saving: number; can_transfer: boolean; configs: TaxConfig[];
}

// ── Apartments (flats): Sec-54 capital gains + rental-income tax ("Ashish") ──────
export interface RentalHP {
  gav: number; municipal_tax: number; nav: number;
  std_deduction: number; loan_interest: number; income: number;
}
export interface AptRental {
  owner: string; residency: string; monthly_rent: number; annual_rent: number;
  units: { name: string; monthly_rent: number; location: string | null }[];
  hp: RentalHP; baseline_tax: number; marginal_rate: number; other_income: number;
}
export interface AptFlat {
  base: TaxBase & { sec54_gain?: number; sec54_two_house?: boolean };
  scenarios: TaxScenario[]; best_cash?: TaxScenario; best?: TaxScenario;
}
export interface ApartmentTax {
  flats: AptFlat[];
  portfolio: {
    count: number; let_out: number; total_gain: number;
    tax_now: number; tax_best_cash: number; tax_best: number; saving_cash: number; saving_max: number;
    monthly_rent: number; annual_rent: number; rent_tax_now: number;
  };
  rentals: AptRental[];
  assumptions: { fy: string; cii_sale: number; notes: string[] };
}
export interface RentPlan {
  regime: string; annual_rent: number; owner: string; owner_share: number;
  owner_hp: RentalHP; owner_tax: number; other_income: number; marginal_rate: number;
  co_owner: string | null; co_tax: number; co_hp: RentalHP | null;
  co_share?: number; co_other_income?: number; total_tax: number;
  no_lever_tax: number;   // full rent to owner, same other-income/regime, no loan/split — the "before"
}
export interface RentPlanIn {
  owner?: string; annual_rent?: number; other_income?: number | null;
  municipal_tax?: number; loan_interest?: number; regime?: string;
  co_owner?: string | null; co_owner_share?: number; co_owner_other_income?: number | null;
}

// ── Listed-equity capital-gains ("TaxBot") ────────────────────────────────────
export interface EqTaxLine {
  base_tax: number; surcharge: number; surcharge_rate: number; cess: number; total: number;
  taxable: number; rate: number; rate_label: string;
  gain?: number; exempt_used?: number; exempt_free_left?: number;
}
export interface EqLiability {
  residency: Residency; stcg: number; ltcg: number;
  stcg_after_setoff: number; ltcg_after_setoff: number;
  carry_forward_stcl: number; carry_forward_ltcl: number;
  stcg_tax: EqTaxLine; ltcg_tax: EqTaxLine; total_tax: number;
  ltcg_exempt_used: number; ltcg_free_left: number; nri_tds_note: string | null;
}
export interface EqCrossover { symbol: string; qty: number; buy_date: string; days_to_lt: number; gain: number; tax_now: number; tax_if_wait: number; tax_saved: number; }
export interface EqHarvest { symbol: string; qty: number; loss: number; price: number; term: 'short' | 'long'; tax_saved: number; }
export interface EqPosition { symbol: string; qty: number; avg_cost: number; price: number; value: number; unrealized: number; lt_qty: number; term: 'short' | 'long' | 'mixed'; }
export interface EqUnrealized {
  unrealized_stcg: number; unrealized_ltcg: number;
  st_gain: number; st_loss: number; lt_gain: number; lt_loss: number;
  crossover: EqCrossover[]; harvest: EqHarvest[]; positions: EqPosition[];
  harvest_total_loss: number; harvest_total_saved: number; crossover_total_saved: number;
  ltcg_headroom: number; unpriced_lots: number;
}
export interface EqPerson {
  person: string; residency: Residency; other_income: number; accounts: string[];
  has_statement: boolean; holdings: number;
  realized: { stcg: number; ltcg: number; intraday: number; dividends: number };
  liability: EqLiability; unrealized: EqUnrealized | null;
}
export interface EqFamily {
  total_tax: number; realized_stcg: number; realized_ltcg: number;
  harvest_saved: number; crossover_saved: number; ltcg_headroom: number;
  intraday: number; dividends: number; total_saveable: number; people: number;
}
export interface EquityTax {
  fy: string; fys_available: string[]; people: EqPerson[]; family: EqFamily;
  price_source: string; assumptions: { stcg_rate: number; ltcg_rate: number; ltcg_exempt: number; lt_days: number; notes: string[] };
}
export interface EqWhatIf {
  person: string; symbol: string; qty_requested: number; qty_matched: number; shortfall: number;
  price: number; st_gain: number; lt_gain: number; st_tax: EqTaxLine; lt_tax: EqTaxLine;
  total_gain: number; total_tax: number;
}

// ── Bonds / fixed income: coupon-interest tax + how to pay less ("Bandhan") ──────
export interface BondIssuerLine {
  issuer: string; invested: number; interest: number; tax_free: boolean;
  bond_type: string; years_to_maturity: number | null;
}
export interface BondOwnerTax {
  owner: string; residency: string; other_income: number; bonds: number; invested: number;
  taxable_invested: number; taxfree_invested: number;
  taxable_interest: number; taxfree_interest: number;
  tds: number; tax_now: number; best_regime: string; new_tax: number; old_tax: number;
  marginal_rate: number; under_rebate: boolean; tax_best: number; best_shift_to: string | null;
  issuers: BondIssuerLine[];
}
export interface BondPortfolio {
  count: number; member_count: number; invested: number;
  taxable_invested: number; taxfree_invested: number;
  taxable_interest: number; taxfree_interest: number; total_interest: number;
  tds: number; tax_now: number; tax_best: number; saving_max: number; taxfree_pct: number;
}
export interface BondTax {
  portfolio: BondPortfolio; owners: BondOwnerTax[];
  capital_gains: { listed_lt_rate: number; listed_lt_months: number; note: string };
  assumptions: { fy: string; tds_rate: number; new_rebate_limit: number; old_rebate_limit: number; notes: string[] };
}
export interface BondIncomePlan {
  regime: string; gross_taxable_interest: number; taxfree_switch: number; remaining_taxable: number;
  owner: string; owner_share: number; owner_portion: number; owner_tax: number;
  owner_other_income: number; owner_marginal_rate: number; owner_under_rebate: boolean;
  co_owner: string | null; co_share: number; co_portion: number; co_tax: number;
  co_other_income: number; co_under_rebate: boolean;
  was: number; total_tax: number; saved: number;
  tds_before: number; tds_after: number; tds_saved: number;
}
export interface BondIncomePlanIn {
  owner?: string; taxable_interest?: number | null; other_income?: number | null; regime?: string;
  taxfree_switch?: number; co_owner?: string | null; co_owner_share?: number; co_owner_other_income?: number | null;
}

@Injectable({ providedIn: 'root' })
export class TaxService {
  private readonly base = _apiBase() + '/tax';
  private http = inject(HttpClient);

  bonds(): Observable<BondTax> { return this.http.get<BondTax>(`${this.base}/bonds`); }
  bondIncomePlan(body: BondIncomePlanIn): Observable<BondIncomePlan> {
    return this.http.post<BondIncomePlan>(`${this.base}/bonds/income-plan`, body);
  }

  equity(fy?: string): Observable<EquityTax> {
    return this.http.get<EquityTax>(`${this.base}/equity${fy ? `?fy=${encodeURIComponent(fy)}` : ''}`);
  }
  equityWhatIf(person: string, symbol: string, qty: number, price?: number): Observable<EqWhatIf> {
    return this.http.post<EqWhatIf>(`${this.base}/equity/what-if`, { person, symbol, qty, price });
  }

  land(): Observable<LandTax> { return this.http.get<LandTax>(`${this.base}/land`); }
  apartments(): Observable<ApartmentTax> { return this.http.get<ApartmentTax>(`${this.base}/apartments`); }
  apartmentSalePlan(selections: { name: string; registered_value?: number }[], prefs: PlanPrefs): Observable<SalePlan> {
    return this.http.post<SalePlan>(`${this.base}/apartments/sale-plan`, { selections, prefs });
  }
  rentPlan(body: RentPlanIn): Observable<RentPlan> {
    return this.http.post<RentPlan>(`${this.base}/apartments/rent-plan`, body);
  }
  profiles(): Observable<TaxProfilesResp> { return this.http.get<TaxProfilesResp>(`${this.base}/profiles`); }
  setProfile(name: string, patch: Partial<TaxProfile>): Observable<TaxProfile> {
    return this.http.put<TaxProfile>(`${this.base}/profiles/${encodeURIComponent(name)}`, patch);
  }
  plan(selections: { name: string; registered_value?: number }[], prefs: PlanPrefs): Observable<SalePlan> {
    return this.http.post<SalePlan>(`${this.base}/land/plan`, { selections, prefs, save: true });
  }
  configs(selections: { name: string; registered_value?: number }[]): Observable<LandConfigs> {
    return this.http.post<LandConfigs>(`${this.base}/land/configs`, { selections });
  }
  getPrefs(): Observable<any> { return this.http.get<any>(`${this.base}/land/prefs`); }

  static inr(v: number | null | undefined): string {
    if (v === null || v === undefined || isNaN(v as number)) return '—';
    const neg = v < 0; const a = Math.abs(v);
    let s: string;
    if (a >= 1e7) s = '₹' + (a / 1e7).toFixed(2) + ' Cr';
    else if (a >= 1e5) s = '₹' + (a / 1e5).toFixed(2) + ' L';
    else s = '₹' + Math.round(a).toLocaleString('en-IN');
    return neg ? '-' + s : s;
  }
}
