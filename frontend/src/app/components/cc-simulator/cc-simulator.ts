import { Component, OnInit, OnDestroy, inject, signal, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ApiService } from '../../services/api.service';

interface SimState {
  id: string; name: string; created_at: string; days_open: number;
  starting_capital: number; slab_rate_pct: number;
  nifty_spot: number; nb_live_price: number;
  nb_shares: number; nb_avg_cost: number; nb_invested: number; nb_mtm_value: number; nb_unrealised_pnl: number;
  cash_balance: number;
  active_call: any | null;
  call_history: any[];
  total_premium_received: number; total_premium_paid_back: number;
  charges_breakdown: { brokerage: number; stt: number; exchange: number; other: number; gst: number; total: number };
  realised_options_pnl: number; realised_etf_pnl: number;
  realised_pnl_pretax: number; realised_tax_paid: number; realised_pnl_posttax: number;
  unrealised_pnl: number; total_portfolio_value: number;
  net_pnl_pretax: number; net_pnl_posttax: number; net_pnl_pct: number; annualised_pct: number;
  nb_history: any[]; notes: string;
}

@Component({
  selector: 'app-cc-simulator',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './cc-simulator.html',
  styleUrl: './cc-simulator.scss',
})
export class CcSimulatorComponent implements OnInit, OnDestroy {
  private api = inject(ApiService);

  view = signal<'hub' | 'detail'>('hub');
  sims = signal<SimState[]>([]);
  selected = signal<SimState | null>(null);
  loading = signal(false);
  error = signal('');
  lastUpdated = signal('');
  private timer: any;

  // Create dialog
  createOpen = signal(false);
  createName = signal('My CC Sandbox');
  createCapital = signal('1000000');
  createSlab = signal('30');
  createDeploy = signal('98');
  creating = signal(false);

  // Sell-call dialog (paper)
  sellOpen = signal(false);
  sellStrike = signal('');
  sellExpiry = signal('');
  sellLots = signal('1');
  sellLotSize = signal('75');
  sellPremium = signal('');
  sellChain = signal<any | null>(null);
  sellChainLoading = signal(false);

  // Close call dialog
  closeOpen = signal(false);
  closePrice = signal('');
  closing = signal(false);

  // Sell shares dialog
  sellSharesOpen = signal(false);
  sellSharesQty = signal('');
  sellSharesProcessing = signal(false);

  // Buy shares dialog
  buySharesOpen = signal(false);
  buySharesQty = signal('');
  buySharesProcessing = signal(false);

  // Slab edit
  editingSlab = signal(false);
  newSlab = signal('');

  // Computed
  pnlPositive = computed(() => (this.selected()?.net_pnl_posttax ?? 0) >= 0);

  ngOnInit() {
    this.loadList();
    this.timer = setInterval(() => this.refresh(), 10000);
  }
  ngOnDestroy() { clearInterval(this.timer); }

  loadList() {
    this.loading.set(true);
    this.api.ccSimList().subscribe({
      next: (s) => {
        this.sims.set(s);
        this.loading.set(false);
        this.lastUpdated.set(new Date().toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit' }));
      },
      error: (e) => {
        this.error.set(e?.error?.detail || 'Failed to load simulations');
        this.loading.set(false);
      },
    });
  }

  refresh() {
    if (this.view() === 'hub') {
      this.loadList();
    } else if (this.selected()) {
      this.api.ccSimGet(this.selected()!.id).subscribe({
        next: (s) => {
          this.selected.set(s);
          this.lastUpdated.set(new Date().toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit' }));
        },
      });
    }
  }

  openSim(s: SimState) {
    this.selected.set(s);
    this.view.set('detail');
    this.refresh();
  }
  backToHub() {
    this.selected.set(null);
    this.view.set('hub');
    this.loadList();
  }

  // ── Create simulation ─────────────────────────────────────────────────────
  openCreate() {
    this.createOpen.set(true);
    this.error.set('');
  }
  cancelCreate() { this.createOpen.set(false); }
  confirmCreate() {
    const cap = parseFloat(this.createCapital());
    const slab = parseFloat(this.createSlab());
    const dep = parseFloat(this.createDeploy());
    if (!cap || cap < 100000) { this.error.set('Capital must be at least ₹1,00,000'); return; }
    this.creating.set(true);
    this.api.ccSimCreate({
      name: this.createName().trim() || 'My CC Sandbox',
      capital: cap, slab_rate_pct: slab, deploy_pct: dep,
    }).subscribe({
      next: (s) => {
        this.creating.set(false);
        this.createOpen.set(false);
        this.loadList();
        this.openSim(s);
      },
      error: (e) => {
        this.creating.set(false);
        this.error.set(e?.error?.detail || 'Failed to create simulation');
      },
    });
  }

  deleteSim(s: SimState, ev: Event) {
    ev.stopPropagation();
    if (!confirm(`Delete simulation "${s.name}"? All cycle history will be lost.`)) return;
    this.api.ccSimDelete(s.id).subscribe({ next: () => this.loadList() });
  }

  // ── Slab edit ─────────────────────────────────────────────────────────────
  editSlab() {
    this.newSlab.set(String(this.selected()?.slab_rate_pct ?? 30));
    this.editingSlab.set(true);
  }
  saveSlab() {
    const v = parseFloat(this.newSlab());
    if (isNaN(v) || v < 0 || v > 50) { this.error.set('Enter a slab between 0 and 50'); return; }
    this.api.ccSimUpdateSlab(this.selected()!.id, v).subscribe({
      next: (s) => { this.selected.set(s); this.editingSlab.set(false); },
    });
  }

  // ── Sell call (paper) ─────────────────────────────────────────────────────
  openSellCall() {
    this.sellOpen.set(true);
    this.sellStrike.set('');
    this.sellExpiry.set('');
    this.sellPremium.set('');
    this.sellChain.set(null);
    this.error.set('');
    // Load chain data for strike picker
    this.sellChainLoading.set(true);
    this.api.getCoveredCallSetup('NIFTY', '').subscribe({
      next: (d) => {
        this.sellChain.set(d);
        this.sellChainLoading.set(false);
        this.sellExpiry.set(d.expiry);
      },
      error: () => { this.sellChainLoading.set(false); },
    });
  }
  cancelSellCall() { this.sellOpen.set(false); }
  pickStrike(strike: number, premium: number) {
    this.sellStrike.set(String(strike));
    this.sellPremium.set(String(premium));
  }
  confirmSellCall() {
    const strike = parseFloat(this.sellStrike());
    const lots = parseInt(this.sellLots(), 10);
    const lotSize = parseInt(this.sellLotSize(), 10);
    const premium = parseFloat(this.sellPremium());
    const expiry = this.sellExpiry();
    if (!strike || !premium || !expiry || !lots || !lotSize) { this.error.set('Fill all fields'); return; }
    this.api.ccSimSellCall(this.selected()!.id, { strike, expiry, lots, lot_size: lotSize, premium }).subscribe({
      next: (s) => { this.selected.set(s); this.sellOpen.set(false); },
      error: (e) => { this.error.set(e?.error?.detail || 'Failed to sell call'); },
    });
  }

  // ── Close call ────────────────────────────────────────────────────────────
  openCloseCall() {
    const ac = this.selected()?.active_call;
    if (!ac) return;
    this.closePrice.set(String(ac.current_price ?? ''));
    this.closeOpen.set(true);
  }
  cancelCloseCall() { this.closeOpen.set(false); }
  confirmCloseCall() {
    const p = parseFloat(this.closePrice());
    if (isNaN(p) || p < 0) { this.error.set('Enter a valid close price'); return; }
    this.closing.set(true);
    this.api.ccSimCloseCall(this.selected()!.id, p).subscribe({
      next: (s) => { this.selected.set(s); this.closeOpen.set(false); this.closing.set(false); },
      error: (e) => { this.error.set(e?.error?.detail || 'Failed to close'); this.closing.set(false); },
    });
  }

  // ── Sell shares (cash generation) ─────────────────────────────────────────
  openSellShares() {
    this.sellSharesQty.set('');
    this.sellSharesOpen.set(true);
  }
  cancelSellShares() { this.sellSharesOpen.set(false); }
  confirmSellShares() {
    const q = parseInt(this.sellSharesQty(), 10);
    if (!q || q <= 0) { this.error.set('Enter a valid quantity'); return; }
    this.sellSharesProcessing.set(true);
    this.api.ccSimSellShares(this.selected()!.id, q).subscribe({
      next: (s) => { this.selected.set(s); this.sellSharesOpen.set(false); this.sellSharesProcessing.set(false); },
      error: (e) => { this.error.set(e?.error?.detail || 'Failed to sell shares'); this.sellSharesProcessing.set(false); },
    });
  }

  // ── Buy shares (deploy cash) ──────────────────────────────────────────────
  openBuyShares() {
    this.buySharesQty.set('');
    this.buySharesOpen.set(true);
  }
  cancelBuyShares() { this.buySharesOpen.set(false); }
  confirmBuyShares() {
    const q = parseInt(this.buySharesQty(), 10);
    if (!q || q <= 0) { this.error.set('Enter a valid quantity'); return; }
    this.buySharesProcessing.set(true);
    this.api.ccSimBuyShares(this.selected()!.id, q).subscribe({
      next: (s) => { this.selected.set(s); this.buySharesOpen.set(false); this.buySharesProcessing.set(false); },
      error: (e) => { this.error.set(e?.error?.detail || 'Failed to buy shares'); this.buySharesProcessing.set(false); },
    });
  }

  // ── Helpers ───────────────────────────────────────────────────────────────
  fmtRs(v: number | null | undefined): string {
    if (v === null || v === undefined) return '—';
    const sign = v >= 0 ? '+' : '−';
    return `${sign}₹${Math.abs(v).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`;
  }
  fmtRsPlain(v: number | null | undefined): string {
    if (v === null || v === undefined) return '—';
    return `₹${v.toLocaleString('en-IN', { maximumFractionDigits: 0 })}`;
  }
  fmtPct(v: number): string { return `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`; }
}
