import { Component, OnInit, inject, signal, computed, effect, HostListener } from '@angular/core';
import { RouterOutlet, RouterLink, RouterLinkActive, Router } from '@angular/router';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpClient } from '@angular/common/http';
import { TickerService } from './services/ticker.service';
import { ApiService } from './services/api.service';
import { DemoService } from './services/demo.service';
import { AuthService } from './services/auth.service';

@Component({
  selector: 'app-root',
  imports: [RouterOutlet, RouterLink, RouterLinkActive, CommonModule, FormsModule],
  templateUrl: './app.html',
  styleUrl: './app.scss'
})
export class App implements OnInit {
  ticker = inject(TickerService);
  api = inject(ApiService);
  demoSvc = inject(DemoService);
  auth = inject(AuthService);
  demo = this.demoSvc.demo;        // reactive demo-mode flag
  private router = inject(Router);

  funds = signal<any>(null);
  kiteConnected = signal(false);
  // Sidebar is a drawer — closed by default, opens on demand (☰).
  sidebarOpen = signal(false);
  archiveOpen = signal(false);   // archive popover (hover-controlled)

  // ── Auth (email OTP → trusted device + PIN; see AuthService) ─────────────────
  // Unlocked = real session OR demo mode. The lock screen reads auth.phase().
  authed = computed(() => this.demo() || this.auth.phase() === 'unlocked');
  emailInput = signal('');
  codeInput = signal('');
  pinInput = signal('');
  pin2Input = signal('');
  private _dataLoaded = false;

  // ── Auto-lock timer pill (topbar) ────────────────────────────────────────────
  lockMenuOpen = signal(false);      // the "set duration" popover
  customMins = signal<number | null>(null);
  savingLock = signal(false);

  /** mm:ss (or h:mm:ss) until auto-lock; '' while locked. */
  lockCountdown = computed(() => {
    const s = this.auth.secondsLeft();
    if (s == null) return '';
    const t = Math.max(0, s);
    const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), sec = t % 60;
    const pad = (n: number) => String(n).padStart(2, '0');
    return h > 0 ? `${h}:${pad(m)}:${pad(sec)}` : `${m}:${pad(sec)}`;
  });
  /** Ring is a 15-radius circle (circumference ≈ 94.25); offset shows time left. */
  private readonly RING_C = 2 * Math.PI * 15;
  lockRingOffset = computed(() => {
    const s = this.auth.secondsLeft();
    const total = this.auth.lockMinutes() * 60;
    if (s == null || total <= 0) return this.RING_C;
    const frac = Math.min(1, Math.max(0, s / total));
    return this.RING_C * (1 - frac);
  });
  lockUrgent = computed(() => {
    const s = this.auth.secondsLeft();
    return s != null && s <= 60;
  });

  toggleLockMenu() {
    const open = !this.lockMenuOpen();
    this.lockMenuOpen.set(open);
    if (open) this.customMins.set(this.auth.lockMinutes());
  }
  closeLockMenu() { this.lockMenuOpen.set(false); }
  presetLabel(m: number) { return m >= 60 && m % 60 === 0 ? `${m / 60}h` : `${m}m`; }

  pickLockMinutes(m: number) { this._applyLock(m); }
  applyCustomLock() {
    const m = Math.round(Number(this.customMins()) || 0);
    if (m < 1 || m > 720) { this.auth.error.set('Choose 1–720 minutes.'); return; }
    this._applyLock(m);
  }
  private _applyLock(m: number) {
    this.savingLock.set(true);
    this.auth.setLockMinutes(m)
      .then(() => this.closeLockMenu())
      .catch(() => {})
      .finally(() => this.savingLock.set(false));
  }

  // Primary nav (always visible) — Net Worth is the home/focus; the options-
  // trading tools are archived (hover-revealed) to keep this clean.
  navItems = [
    { path: '/',                 label: 'Dashboard',        icon: 'dashboard', hint: 'Net worth, returns, income & advisors' },
    { path: '/stocks',           label: 'Stocks',           icon: 'trending-up', hint: 'Per-account CAGR + combined return · Ask Kuber for capital-gains tax' },
    { path: '/stock-tax',        label: 'Stock Tax',        icon: 'scale',     hint: 'Kuber — stock capital-gains chatbot: STCG/LTCG owed + how to save (loss harvest, LT-timing, ₹1.25L free)' },
    { path: '/fno',              label: 'F&O',              icon: 'candles',   hint: 'Live Zerodha F&O — strategy P&L, daily calendar & per-second chart' },
    { path: '/bonds',            label: 'Bonds',            icon: 'shield',    hint: 'Monthly income, YTM, maturity ladder · Ask Bandhan for coupon-income tax & how to lower it' },
    { path: '/bonds-tax',        label: 'Bond Tax',         icon: 'scale',     hint: 'Bandhan — bonds chatbot: coupon-income tax, TDS/15G, whose-name split & how to pay less' },
    // Reminders page retired from the nav — money in/out now lives on the
    // dashboard money-calendar; the /reminders route stays reachable by URL.
    { path: '/land',             label: 'Land',             icon: 'map-pin',   hint: 'Real-estate parcels & CAGR' },
    { path: '/land-tax',         label: 'Land Tax',         icon: 'scale',     hint: 'Bhoomi — land capital-gains chatbot: sell-now tax, how to pay less, gift-to-family' },
    { path: '/apartments',       label: 'Apartments',       icon: 'building',  hint: 'Rented flats: rent + appreciation · Ask Ashish for rent & sale tax' },
    { path: '/apartments-tax',   label: 'Apartment Tax',    icon: 'scale',     hint: 'Ashish — flats chatbot: rental-income tax (how to pay less) + Sec-54 capital gains on selling' },
    { path: '/house',            label: 'Bay Villa',        icon: 'home',      hint: 'TVH Bay Villa A-64 — payment schedule & funding plan' },
    { path: '/gold',             label: 'Gold & Silver',    icon: 'coin',      hint: 'Live-priced jewellery & bullion' },
    { path: '/salary',           label: 'Salary',           icon: 'briefcase', hint: 'Earned income (multi-currency) → monthly ₹' },
    { path: '/ulip',             label: 'ULIP',             icon: 'book',      hint: 'Unit-linked insurance: fund value, premiums & XIRR' },
    { path: '/lic',              label: 'LIC Insurance',    icon: 'umbrella',  hint: 'LIC life insurance: cover, premiums, maturity & bonus — explained simply' },
    { path: '/fd',               label: 'Fixed Deposits',   icon: 'bank',      hint: 'FD value + payout interest income & maturity' },
    { path: '/expenses',         label: 'Expenses',         icon: 'receipt',   hint: 'India & Kuwait spend · recurring carried forward · treemap' },
    { path: '/loans',            label: 'Loans',            icon: 'credit-card', hint: 'Liabilities: outstanding, EMI & repayment progress' },
    { path: '/cash',             label: 'Cash & Funds',     icon: 'wallet',    hint: 'Cash + bank balances (multi-currency), most liquid' },
    { path: '/documents',        label: 'Documents',        icon: 'folder',    hint: 'Secure vault: folders + files, with land/apartment/gold docs linked' },
    { path: '/other-income',     label: 'Other Income',     icon: 'coin',      hint: 'Dividends, interest, rent, bonuses & gifts → monthly ₹' },
  ];

  // Footer item — API Settings sits at the bottom-left, right above Archive
  settingsItem = { path: '/settings', label: 'API Settings', icon: 'gear' };

  // Archive (hover-revealed bottom-left) — options-trading + less-used pages.
  archiveItems = [
    { path: '/portfolio',        label: 'Portfolio',        icon: 'briefcase' },
    { path: '/smart-money',      label: 'Smart Money',      icon: 'target' },
    { path: '/scorecard',        label: 'Scorecard',        icon: 'target' },
    { path: '/hedges',           label: 'Hedges',           icon: 'shield' },
    { path: '/pe-scanner',       label: 'PE Spread',        icon: 'sliders' },
    { path: '/saved-strategies', label: 'Saved',            icon: 'folder' },
    { path: '/exit-strategy',    label: 'Exit Strategy',    icon: 'shield' },
    { path: '/covered-call',     label: 'Covered Call',     icon: 'home' },
    { path: '/protected-wheel',  label: 'Protected Wheel',  icon: 'shield' },
    { path: '/short-strangle',   label: 'Short Strangle',   icon: 'sliders' },
    { path: '/dashboard',        label: 'Dashboard',        icon: 'dashboard' },
    { path: '/strategies',       label: 'Strategies Hub',   icon: 'target' },
    { path: '/scanner',          label: 'Strategy Builder', icon: 'sliders' },
    { path: '/cc-simulator',     label: 'CC Simulator',     icon: 'flask' },
    { path: '/strategy-math',    label: 'Strategy Math',    icon: 'function' },
    { path: '/math-handbook',    label: 'Math Handbook',    icon: 'book' },
    { path: '/strategy',         label: 'Strategy Lab',     icon: 'atom' },
  ];

  showArchive()   { this.archiveOpen.set(true); }
  hideArchive()   { this.archiveOpen.set(false); }
  toggleArchive() { this.archiveOpen.set(!this.archiveOpen()); }

  // ── nav search ──────────────────────────────────────────────────────────────
  // The sidebar shows ONLY your pinned tabs by default. "All tabs" expands the
  // full curated list; the search box filters every tab. No frequency ranking.
  navQuery = signal('');
  showAll = signal(false);
  onNavClick(_path?: string) { this.toggleSidebar(); }

  // ── pinned tabs — durable (server + localStorage) & reorderable ─────────────
  // Pins persist in the DB (app_settings → key 'nav_pins') so they survive a
  // cache clear or a new device; localStorage is just a fast offline cache. The
  // order in this array IS the display order — drag to change it.
  navPins = signal<string[]>(this.loadPins());
  private loadPins(): string[] {
    if (typeof localStorage === 'undefined') return [];
    try { const v = JSON.parse(localStorage.getItem('nav_pins') || '[]'); return Array.isArray(v) ? v : []; } catch { return []; }
  }
  /** Write pins to the offline cache immediately + the server (fire-and-forget). */
  private _persistPins(pins: string[]) {
    if (typeof localStorage !== 'undefined') localStorage.setItem('nav_pins', JSON.stringify(pins));
    if (!this.demo()) this.api.saveNavPins(pins).subscribe({ next: () => {}, error: () => {} });
  }
  /** On boot, pull the durable order from the server. Server wins; but if the
   *  server has none yet and this device had local pins, migrate them up. */
  private _hydratePins() {
    if (this.demo()) return;
    this.api.getNavPins().subscribe({
      next: r => {
        const server = Array.isArray(r?.pins) ? r.pins : [];
        const local = this.navPins();
        if (server.length) {
          this.navPins.set(server);
          if (typeof localStorage !== 'undefined') localStorage.setItem('nav_pins', JSON.stringify(server));
        } else if (local.length) {
          this._persistPins(local);
        }
      },
      error: () => {},
    });
  }
  isPinned(path: string): boolean { return this.navPins().includes(path); }
  togglePin(path: string, ev?: Event) {
    ev?.stopPropagation(); ev?.preventDefault();
    this.navPins.update(p => {
      const n = p.includes(path) ? p.filter(x => x !== path) : [...p, path];
      this._persistPins(n);
      return n;
    });
  }

  // ── drag pinned tabs to any position (1st, 2nd, …) ──────────────────────────
  dragFrom = signal<number | null>(null);
  dragOver = signal<number | null>(null);
  onPinDragStart(i: number, ev: DragEvent) {
    this.dragFrom.set(i);
    try { ev.dataTransfer?.setData('text/plain', String(i)); if (ev.dataTransfer) ev.dataTransfer.effectAllowed = 'move'; } catch {}
  }
  onPinDragOver(i: number, ev: DragEvent) {
    ev.preventDefault();
    if (ev.dataTransfer) ev.dataTransfer.dropEffect = 'move';
    if (this.dragOver() !== i) this.dragOver.set(i);
  }
  onPinDrop(i: number, ev: DragEvent) { ev.preventDefault(); this.movePin(this.dragFrom(), i); this.onPinDragEnd(); }
  onPinDragEnd() { this.dragFrom.set(null); this.dragOver.set(null); }
  movePin(from: number | null, to: number | null) {
    if (from == null || to == null || from === to) return;
    this.navPins.update(p => {
      if (from < 0 || from >= p.length || to < 0 || to >= p.length) return p;
      const n = [...p]; const [m] = n.splice(from, 1); n.splice(to, 0, m);
      this._persistPins(n);
      return n;
    });
  }
  private _tag = (it: any, i: number) => ({ ...it, _i: i });

  /** Your pinned tabs, in your chosen order — always shown at the top. */
  pinnedTabs = computed(() => {
    const byPath = new Map([...this.navItems, ...this.archiveItems, this.settingsItem].map(this._tag).map(it => [it.path, it]));
    return this.navPins().map(p => byPath.get(p)).filter(Boolean) as any[];
  });

  /** Everything not pinned, in curated order — the main tabs, then the archived
   *  options tools + API Settings so nothing is stranded. No frequency ranking. */
  mainTabs = computed(() => {
    const pinnedSet = new Set(this.navPins());
    const main = this.navItems.map(this._tag);
    const extra = [...this.archiveItems, this.settingsItem].map((it, i) => this._tag(it, 900 + i));
    return [...main, ...extra].filter(it => !pinnedSet.has(it.path));
  });

  /** Search across every page (main + archive + settings). */
  navSearch = computed(() => {
    const q = this.navQuery().trim().toLowerCase();
    if (!q) return [];
    return [...this.navItems, ...this.archiveItems, this.settingsItem].map(this._tag)
      .filter(it => it.label.toLowerCase().includes(q) || (it.hint || '').toLowerCase().includes(q));
  });

  /** Enter in the search box jumps to the top result. */
  navEnter() {
    const first = this.navSearch()[0];
    if (!first) return;
    this.router.navigate([first.path]);
    this.navQuery.set('');
    this.sidebarOpen.set(false);
  }

  constructor() {
    // When the session becomes unlocked (any path: stored token, OTP, or PIN),
    // boot the live data + ticker exactly once.
    effect(() => {
      if (this.authed() && !this.demo()) this._bootData();
    });
  }

  ngOnInit() {
    // Demo mode (sample data) — skip auth and the live ticker entirely.
    if (this.demo()) {
      this.loadStatus();
      this.loadFunds();
      return;
    }
    // Decide the lock screen (email / PIN) or go straight in on a fresh token.
    this.auth.init();
    // Intercept Kite OAuth redirect: any URL with ?request_token= gets forwarded
    // to the settings page which then auto-exchanges the token.
    this._handleKiteOAuthRedirect();
  }

  /** Boot live data once we're authenticated (idempotent). */
  private _bootData() {
    if (this._dataLoaded) return;
    this._dataLoaded = true;
    this.ticker.connect();
    this.loadStatus();
    this.loadFunds();
    this._hydratePins();          // pull durable pin order from the server
    setInterval(() => { this.loadStatus(); this.loadFunds(); }, 10000);
  }

  // ── Lock-screen actions ──────────────────────────────────────────────────────
  // Single-user: no email box, code goes to the configured address (empty arg).
  // Multi-user: pass the email the user typed so they sign in / register with it.
  sendCode()     { this.auth.requestOtp(this.auth.multiUser() ? this.emailInput().trim() : ''); }
  submitOtp()    { this.auth.verifyOtp(this.codeInput()).then(() => this.codeInput.set('')); }
  submitNewPin() {
    if (this.pinInput() !== this.pin2Input()) { this.auth.error.set("PINs don't match."); return; }
    this.auth.setPin(this.pinInput()).then(() => { this.pinInput.set(''); this.pin2Input.set(''); });
  }
  submitUnlock() { this.auth.unlock(this.pinInput()).then(() => this.pinInput.set('')); }
  backToEmail()  { this.auth.error.set(''); this.codeInput.set(''); this.auth.phase.set('email'); }
  resendCode()   { this.auth.requestOtp(this.auth.multiUser() ? this.emailInput().trim() : ''); }
  staySignedIn() { this.auth.refresh(); }

  /** Manual lock button in the topbar. */
  lock() { this.auth.lock(); this.pinInput.set(''); }

  /** Enter the investor demo: sample data only, no backend, no real data. */
  viewDemo() {
    this.demoSvc.enter();
    this.router.navigate(['/']);
  }

  /** Leave demo mode → re-evaluate auth and drop to the proper lock screen. */
  exitDemo() {
    this.demoSvc.exit();
    this._dataLoaded = false;
    this.auth.init();
    this.router.navigate(['/']);
  }

  private _handleKiteOAuthRedirect() {
    const params = new URLSearchParams(window.location.search);
    const requestToken = params.get('request_token');
    if (!requestToken) return;
    // Strip the OAuth params from the browser URL so they don't pollute history
    window.history.replaceState({}, '', window.location.pathname);
    // If the F&O page started this login (it stamps the account id before
    // redirecting to Kite), finish the exchange there; else → settings.
    const fnoAcc = localStorage.getItem('fno_connect_acc');
    this.router.navigate([fnoAcc ? '/fno' : '/settings'], {
      queryParams: { kite_token: requestToken },
    });
  }

  loadFunds() {
    this.api.getFunds().subscribe(f => this.funds.set(f));
  }

  loadStatus() {
    this.api.getAuthStatus().subscribe(s => this.kiteConnected.set(s.connected));
  }

  resetPaper() {
    if (confirm('Reset paper trading account to ₹10,00,000? All positions and strategies will be cleared.')) {
      this.api.resetPaper().subscribe(() => this.loadFunds());
    }
  }

  toggleSidebar() { this.sidebarOpen.update(v => !v); }

  // ── Mobile long-press tooltips ──────────────────────────────────────────────
  // Phones have no hover, so the [data-tip] hints (hero stats, gauges, etc.) are
  // invisible. A ~350ms long-press on any such element pops a floating tip with
  // its value; it auto-hides, and scrolling/short taps cancel it.
  touchTip = signal<{ x: number; y: number; text: string } | null>(null);
  private _tipTimer: any = null;
  private _tipHide: any = null;

  @HostListener('touchstart', ['$event'])
  onTouchStart(e: TouchEvent) {
    this.touchTip.set(null);
    clearTimeout(this._tipTimer);
    const el = (e.target as HTMLElement)?.closest?.('[data-tip]') as HTMLElement | null;
    const text = el?.getAttribute('data-tip');
    if (!el || !text) return;
    this._tipTimer = setTimeout(() => {
      const r = el.getBoundingClientRect();
      const x = Math.min(Math.max(r.left + r.width / 2, 14), window.innerWidth - 14);
      this.touchTip.set({ x, y: r.top, text });
      clearTimeout(this._tipHide);
      this._tipHide = setTimeout(() => this.touchTip.set(null), 3200);
    }, 350);
  }

  @HostListener('touchmove')
  @HostListener('touchend')
  @HostListener('touchcancel')
  onTouchEnd() { clearTimeout(this._tipTimer); }

  // Close the auto-lock popover when clicking anywhere outside it.
  @HostListener('document:click', ['$event'])
  onDocClick(e: MouseEvent) {
    if (!this.lockMenuOpen()) return;
    if (!(e.target as HTMLElement)?.closest?.('.autolock')) this.closeLockMenu();
  }
}
