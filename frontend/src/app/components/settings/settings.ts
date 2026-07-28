import { Component, OnInit, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpClient } from '@angular/common/http';
import { ActivatedRoute } from '@angular/router';
import { AuthService } from '../../services/auth.service';

// Auto-detect API base — localhost in dev, same-origin in production.
function _apiBase(): string {
  if (typeof window === 'undefined') return 'http://localhost:8000/api';
  const { hostname, protocol, host } = window.location;
  if (hostname === 'localhost' || hostname === '127.0.0.1') {
    return 'http://localhost:8000/api';
  }
  return `${protocol}//${host}/api`;
}
const API = _apiBase();

@Component({
  selector: 'app-settings',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './settings.html',
  styleUrl: './settings.scss'
})
export class SettingsComponent implements OnInit {
  http = inject(HttpClient);
  auth = inject(AuthService);
  private route = inject(ActivatedRoute);

  // ── Security (this device) ──────────────────────────────────────────────────
  secEmail = signal('');
  secDelivery = signal('');
  secPin = signal('');
  secPin2 = signal('');
  secBusy = signal(false);
  secMsg = signal('');
  secError = signal('');

  loadSecurity() {
    this.auth.getSettings().then((s: any) => {
      this.secEmail.set(s.email || '');
      this.secDelivery.set(s.email_delivery || '');
    }).catch(() => {});
  }

  saveLockMinutes(mins: number) {
    this.secError.set(''); this.secMsg.set('');
    this.auth.setLockMinutes(mins)
      .then(() => { this.secMsg.set(`Auto-lock set to ${mins} min on this device.`); setTimeout(() => this.secMsg.set(''), 4000); })
      .catch((e: any) => this.secError.set(e?.error?.detail || 'Could not update auto-lock.'));
  }

  saveNewPin() {
    this.secError.set(''); this.secMsg.set('');
    if (!/^\d{4,8}$/.test(this.secPin())) { this.secError.set('PIN must be 4–8 digits.'); return; }
    if (this.secPin() !== this.secPin2()) { this.secError.set("PINs don't match."); return; }
    this.secBusy.set(true);
    this.auth.changePin(this.secPin())
      .then(() => { this.secPin.set(''); this.secPin2.set(''); this.secMsg.set('PIN updated.'); setTimeout(() => this.secMsg.set(''), 4000); })
      .catch((e: any) => this.secError.set(e?.error?.detail || 'Could not change PIN.'))
      .finally(() => this.secBusy.set(false));
  }

  logoutDevice() {
    if (!confirm('Log out this device? You\'ll need an email code to sign in again here. Other devices stay signed in.')) return;
    this.auth.logout();
  }

  status = signal<any>(null);
  supabaseStatus = signal<any>(null);
  supabaseLoading = signal(false);

  // Editable Supabase connection (URL + service key) — always editable.
  sbUrl = signal('');
  sbKey = signal('');
  sbKeySet = signal(false);
  sbKeyHint = signal('');
  sbSaving = signal(false);
  sbMsg = signal('');
  sbError = signal('');
  sbEditOpen = signal(false);
  toggleSbEdit() { this.sbEditOpen.update(v => !v); }

  // Step 1: save keys
  apiKey = signal('');
  apiSecret = signal('');
  savingKeys = signal(false);
  loginUrl = signal('');
  step1Error = signal('');

  // Step 2: paste request_token
  requestToken = signal('');
  connecting = signal(false);
  step2Error = signal('');

  // Direct mode: API key + access token only
  directApiKey = signal('');
  directAccessToken = signal('');
  directConnecting = signal(false);
  directError = signal('');

  successMsg = signal('');
  activeTab = signal<'browser' | 'direct'>('browser');
  autoExchanging = signal(false);
  autoError = signal('');

  // UI state — collapsible details panels
  infoOpen = signal(false);      // status detail expander
  howtoOpen = signal(true);      // "How this works" card open by default

  toggleInfo()  { this.infoOpen.update(v => !v); }
  toggleHowto() { this.howtoOpen.update(v => !v); }

  // The URL Kite should redirect to after login — same origin as this page.
  get callbackUrl(): string {
    if (typeof window === 'undefined') return '';
    return window.location.origin + '/';
  }

  ngOnInit() {
    this.loadStatus();
    this.loadSupabaseStatus();
    this.loadSupabaseConfig();
    this.loadSecurity();
    this._checkAutoToken();
  }

  loadSupabaseConfig() {
    this.http.get<any>(`${API}/auth/supabase-config`).subscribe({
      next: (c) => {
        this.sbUrl.set(c.url || '');
        this.sbKeySet.set(!!c.key_set);
        this.sbKeyHint.set(c.key_hint || '');
        this.sbKey.set('');            // never prefill the secret; blank = keep current
      },
      error: () => {},
    });
  }

  saveSupabaseConfig() {
    if (!this.sbUrl().trim()) { this.sbError.set('Project URL is required'); return; }
    this.sbSaving.set(true);
    this.sbError.set('');
    this.sbMsg.set('');
    this.http.post<any>(`${API}/auth/supabase-config`, {
      url: this.sbUrl().trim(),
      service_key: this.sbKey().trim(),   // blank → backend keeps the existing key
    }).subscribe({
      next: (s) => {
        this.sbSaving.set(false);
        this.supabaseStatus.set(s);
        this.sbMsg.set(s?.reachable
          ? `✓ Connected — ${s.message || 'Supabase is reachable.'}`
          : `Saved. ${s?.error || 'Not reachable yet — you may still need to run the table migrations.'}`);
        this.sbKey.set('');
        this.loadSupabaseConfig();
        setTimeout(() => this.sbMsg.set(''), 9000);
      },
      error: (e) => {
        this.sbSaving.set(false);
        this.sbError.set(e.error?.detail || 'Could not save Supabase settings.');
      },
    });
  }

  // If the app root detected a ?request_token= in the Kite OAuth redirect and
  // navigated here with ?kite_token=..., auto-exchange it immediately.
  private _checkAutoToken() {
    this.route.queryParams.subscribe(params => {
      const token = params['kite_token'];
      if (!token) return;
      this.activeTab.set('browser');
      this.autoExchanging.set(true);
      this.step2Error.set('');
      this.http.post<any>(`${API}/auth/connect`, { request_token: token }).subscribe({
        next: (r) => {
          this.autoExchanging.set(false);
          this.successMsg.set(`✓ Auto-connected as ${r.user_name} (${r.user_id}) — Kite login successful!`);
          this.loadStatus();
          setTimeout(() => this.successMsg.set(''), 8000);
        },
        error: (e) => {
          this.autoExchanging.set(false);
          this.autoError.set(e.error?.detail || 'Token exchange failed — try the login flow again.');
          this.activeTab.set('browser');
        },
      });
    });
  }

  loadStatus() {
    this.http.get<any>(`${API}/auth/status`).subscribe(s => this.status.set(s));
  }

  loadSupabaseStatus(retest = false) {
    this.supabaseLoading.set(true);
    const url = retest ? `${API}/auth/supabase-status?retest=1` : `${API}/auth/supabase-status`;
    this.http.get<any>(url).subscribe({
      next: (s) => { this.supabaseStatus.set(s); this.supabaseLoading.set(false); },
      error: () => { this.supabaseStatus.set(null);  this.supabaseLoading.set(false); },
    });
  }

  // ── Quick login: reuse api_key/secret already in .env (one click) ─────────
  quickLoggingIn = signal(false);

  quickLogin() {
    this.quickLoggingIn.set(true);
    this.step1Error.set('');
    this.autoError.set('');
    this.http.get<any>(`${API}/auth/login-url`).subscribe({
      next: (r) => {
        this.quickLoggingIn.set(false);
        // Same-tab: Kite logs you in, redirects to app root, which forwards the
        // request_token here and _checkAutoToken() exchanges it automatically.
        window.location.href = r.login_url;
      },
      error: (e) => {
        this.quickLoggingIn.set(false);
        this.step1Error.set(e.error?.detail || 'Could not start login — check your keys in .env.');
      },
    });
  }

  // ── Tab 1: Browser flow ──────────────────────────────────────────────────

  saveKeys() {
    if (!this.apiKey() || !this.apiSecret()) { this.step1Error.set('Both API key and secret are required'); return; }
    this.savingKeys.set(true);
    this.step1Error.set('');
    this.http.post<any>(`${API}/auth/save-keys`, {
      api_key: this.apiKey(),
      api_secret: this.apiSecret(),
    }).subscribe({
      next: (r) => {
        this.loginUrl.set(r.login_url);
        this.savingKeys.set(false);
      },
      error: (e) => { this.step1Error.set(e.error?.detail || 'Failed'); this.savingKeys.set(false); }
    });
  }

  openLoginUrl() {
    window.open(this.loginUrl(), '_blank');
  }

  connectToken() {
    if (!this.requestToken()) { this.step2Error.set('Paste the request_token from the redirect URL'); return; }
    this.connecting.set(true);
    this.step2Error.set('');
    this.http.post<any>(`${API}/auth/connect`, { request_token: this.requestToken() }).subscribe({
      next: (r) => {
        this.successMsg.set(`✓ Connected as ${r.user_name} (${r.user_id})`);
        this.connecting.set(false);
        this.loginUrl.set('');
        this.requestToken.set('');
        this.loadStatus();
        setTimeout(() => this.successMsg.set(''), 5000);
      },
      error: (e) => { this.step2Error.set(e.error?.detail || 'Token exchange failed'); this.connecting.set(false); }
    });
  }

  // ── Tab 2: Direct token ─────────────────────────────────────────────────

  connectDirect() {
    if (!this.directApiKey() || !this.directAccessToken()) {
      this.directError.set('API key and access token are required');
      return;
    }
    this.directConnecting.set(true);
    this.directError.set('');
    this.http.post<any>(`${API}/auth/connect-direct`, {
      api_key: this.directApiKey(),
      access_token: this.directAccessToken(),
    }).subscribe({
      next: (r) => {
        this.successMsg.set(`✓ Connected as ${r.user_name} (${r.user_id}). Live market data is now active!`);
        this.directConnecting.set(false);
        this.directApiKey.set('');
        this.directAccessToken.set('');
        this.loadStatus();
        setTimeout(() => this.successMsg.set(''), 6000);
      },
      error: (e) => { this.directError.set(e.error?.detail || 'Connection failed — check your credentials'); this.directConnecting.set(false); }
    });
  }

  disconnect() {
    if (!confirm('Disconnect from Kite? The app will fall back to simulated prices.')) return;
    this.http.post<any>(`${API}/auth/disconnect`, {}).subscribe(() => {
      this.loadStatus();
      this.successMsg.set('Disconnected — using mock market data');
      setTimeout(() => this.successMsg.set(''), 4000);
    });
  }
}
