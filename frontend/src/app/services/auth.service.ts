import { Injectable, inject, signal } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { firstValueFrom } from 'rxjs';

/**
 * Owns the entire client-side auth lifecycle:
 *   • the lock-screen state machine (email → OTP → PIN / set-PIN → unlocked)
 *   • the short-lived access token (localStorage) attached to every API call
 *   • the idle watcher: slides the token forward while active, warns ~60s before
 *     expiry, and auto-locks on idle — server-enforced, this is just the UX.
 *
 * The long-lived trusted-device token lives in an httpOnly cookie we never see.
 */
function apiBase(): string {
  if (typeof window === 'undefined') return 'http://localhost:8000/api';
  const override = (window as any).__API_BASE__;
  if (override) return override;
  const { hostname, protocol, host } = window.location;
  if (hostname === 'localhost' || hostname === '127.0.0.1') return 'http://localhost:8000/api';
  return `${protocol}//${host}/api`;
}
const BASE = apiBase();

const AT = 'nw_at';            // access token
const AT_EXP = 'nw_at_exp';    // its expiry (epoch seconds)

export type AuthPhase = 'loading' | 'email' | 'otp' | 'setpin' | 'pin' | 'unlocked';

@Injectable({ providedIn: 'root' })
export class AuthService {
  private http = inject(HttpClient);

  phase = signal<AuthPhase>('loading');
  email = signal('');               // raw email entered (used to verify)
  maskedEmail = signal('');         // masked, for display
  signinEmail = signal('');         // masked allowlisted address codes go to
  hasPin = signal(false);
  lockMinutes = signal(10);
  lockPresets = signal<number[]>([1, 5, 10, 30, 60]);
  busy = signal(false);
  error = signal('');
  notice = signal('');              // e.g. "code printed to server log" (dev)

  // Idle warning shown ~60s before auto-lock
  warnSeconds = signal<number | null>(null);
  // Live seconds until auto-lock (null while locked) — drives the topbar timer.
  secondsLeft = signal<number | null>(null);

  private exp = 0;                  // access-token expiry epoch (seconds)
  private lastActivity = 0;
  private lastRefresh = 0;
  private tick: any = null;
  private activityBound = false;

  // ── token accessors (used by the HTTP interceptor) ──────────────────────────
  token(): string | null {
    const t = typeof localStorage !== 'undefined' ? localStorage.getItem(AT) : null;
    return t || null;
  }
  isUnlocked(): boolean { return this.phase() === 'unlocked'; }

  private store(token: string, expEpoch: number) {
    this.exp = expEpoch;
    if (typeof localStorage !== 'undefined') {
      localStorage.setItem(AT, token);
      localStorage.setItem(AT_EXP, String(expEpoch));
    }
  }
  private clearToken() {
    this.exp = 0;
    if (typeof localStorage !== 'undefined') {
      localStorage.removeItem(AT);
      localStorage.removeItem(AT_EXP);
    }
  }

  // ── bootstrap on app load ───────────────────────────────────────────────────
  async init(): Promise<void> {
    const now = Math.floor(Date.now() / 1000);
    const storedExp = Number(typeof localStorage !== 'undefined' ? localStorage.getItem(AT_EXP) : 0) || 0;

    // Pull device state (cookie-based) so we know which screen to show.
    let sess: any = null;
    try { sess = await firstValueFrom(this.http.get<any>(`${BASE}/auth/session`, { withCredentials: true })); }
    catch { /* offline → treat as unknown device */ }

    if (sess) {
      this.hasPin.set(!!sess.has_pin);
      this.lockMinutes.set(sess.lock_minutes || 10);
      this.lockPresets.set(sess.lock_presets || [1, 5, 10, 30, 60]);
      if (sess.email) this.maskedEmail.set(sess.email);
      if (sess.signin_email) this.signinEmail.set(sess.signin_email);
    }

    // Valid stored access token + still a trusted device → straight in.
    if (this.token() && storedExp > now + 5 && sess?.device_known) {
      this.exp = storedExp;
      this.enterUnlocked();
      return;
    }
    this.clearToken();

    if (sess?.device_known && sess?.has_pin) this.phase.set('pin');
    else this.phase.set('email');
  }

  // ── flow: request code → OTP ────────────────────────────────────────────────
  // Single-user app: no email box. The server sends the code to the allowlisted
  // address; the client passes no email and the backend fills it in.
  async requestOtp(email?: string): Promise<void> {
    const e = (email || '').trim();   // usually empty → backend uses the allowlist
    this.busy.set(true); this.error.set(''); this.notice.set('');
    try {
      const r: any = await firstValueFrom(this.http.post(`${BASE}/auth/request-otp`, { email: e }, { withCredentials: true }));
      this.email.set(e);
      this.maskedEmail.set(r.masked || this.signinEmail() || '');
      this.notice.set(r.emailed ? '' : 'Dev mode: code printed to the server log.');
      this.phase.set('otp');
    } catch (err: any) {
      this.error.set(err?.error?.detail || 'Could not send the code. Try again.');
    } finally { this.busy.set(false); }
  }

  async verifyOtp(code: string): Promise<void> {
    const c = (code || '').trim();
    if (c.length < 6) { this.error.set('Enter the 6-digit code.'); return; }
    this.busy.set(true); this.error.set('');
    try {
      const r: any = await firstValueFrom(this.http.post(`${BASE}/auth/verify-otp`,
        { email: this.email(), code: c }, { withCredentials: true }));
      this.store(r.access_token, r.expires_at);
      this.lockMinutes.set(r.lock_minutes || 10);
      this.lockPresets.set(r.lock_presets || this.lockPresets());
      this.hasPin.set(!!r.has_pin);
      if (r.email) this.maskedEmail.set(r.email);
      this.phase.set(r.has_pin ? 'unlocked' : 'setpin');
      if (this.phase() === 'unlocked') this.enterUnlocked();
    } catch (err: any) {
      this.error.set(err?.error?.detail || 'Incorrect or expired code.');
    } finally { this.busy.set(false); }
  }

  // ── PIN: create / unlock ────────────────────────────────────────────────────
  async setPin(pin: string): Promise<void> {
    const p = (pin || '').trim();
    if (!/^\d{4,8}$/.test(p)) { this.error.set('PIN must be 4–8 digits.'); return; }
    this.busy.set(true); this.error.set('');
    try {
      await firstValueFrom(this.http.post(`${BASE}/auth/set-pin`, { pin: p }, { withCredentials: true }));
      this.hasPin.set(true);
      this.enterUnlocked();
    } catch (err: any) {
      this.error.set(err?.error?.detail || 'Could not set the PIN.');
    } finally { this.busy.set(false); }
  }

  async unlock(pin: string): Promise<void> {
    const p = (pin || '').trim();
    if (!p) { this.error.set('Enter your PIN.'); return; }
    this.busy.set(true); this.error.set('');
    try {
      const r: any = await firstValueFrom(this.http.post(`${BASE}/auth/unlock`, { pin: p }, { withCredentials: true }));
      this.store(r.access_token, r.expires_at);
      this.lockMinutes.set(r.lock_minutes || this.lockMinutes());
      this.enterUnlocked();
    } catch (err: any) {
      const detail = err?.error?.detail || 'Incorrect PIN.';
      this.error.set(detail);
      // Device got revoked (too many wrong PINs) → fall back to email sign-in.
      if (/email/i.test(detail)) { this.hasPin.set(false); this.phase.set('email'); }
    } finally { this.busy.set(false); }
  }

  // ── unlocked session: idle watcher + sliding refresh ────────────────────────
  private enterUnlocked() {
    this.error.set(''); this.notice.set(''); this.warnSeconds.set(null);
    this.phase.set('unlocked');
    this.lastActivity = Date.now();
    this.bindActivity();
    if (this.tick) clearInterval(this.tick);
    // 1s cadence so the topbar countdown ticks smoothly; the refresh logic
    // inside heartbeat() is self-throttled, so this stays cheap.
    this.tick = setInterval(() => this.heartbeat(), 1000);
    this.heartbeat();   // paint the timer immediately, don't wait a second
  }

  private bindActivity() {
    if (this.activityBound || typeof document === 'undefined') return;
    this.activityBound = true;
    const mark = () => { this.lastActivity = Date.now(); this.maybeRefresh(); };
    ['mousemove', 'mousedown', 'keydown', 'touchstart', 'scroll', 'click'].forEach(ev =>
      document.addEventListener(ev, mark, { passive: true }));
  }

  private maybeRefresh() {
    if (this.phase() !== 'unlocked') return;
    const now = Math.floor(Date.now() / 1000);
    const windowSec = this.lockMinutes() * 60;
    const secsLeft = this.exp - now;
    // Only refresh once we're past the halfway mark, throttled to ~30s, so an
    // active user keeps a fresh token without hammering the server.
    if (secsLeft < windowSec / 2 && Date.now() - this.lastRefresh > 30_000) {
      this.refresh();
    }
  }

  /** "Stay signed in" button + activity-driven sliding. */
  refresh() {
    this.lastRefresh = Date.now();
    this.http.post<any>(`${BASE}/auth/refresh`, {}, { withCredentials: true }).subscribe({
      next: r => { this.store(r.access_token, r.expires_at); this.warnSeconds.set(null); },
      error: () => this.lock(),   // token already expired server-side → lock
    });
  }

  private heartbeat() {
    if (this.phase() !== 'unlocked') return;
    const now = Math.floor(Date.now() / 1000);
    const secsLeft = this.exp - now;
    const idleSec = (Date.now() - this.lastActivity) / 1000;

    if (secsLeft <= 0) { this.lock(); return; }
    this.secondsLeft.set(secsLeft);
    // Warn in the last 60s — appears only when idle (active use refreshes first).
    if (secsLeft <= 60 && idleSec > 25) this.warnSeconds.set(secsLeft);
    else this.warnSeconds.set(null);
  }

  /** Idle/expired auto-lock — keeps the trusted device, just drops the token. */
  lock() {
    this.clearToken();
    this.warnSeconds.set(null);
    this.secondsLeft.set(null);
    if (this.tick) { clearInterval(this.tick); this.tick = null; }
    this.error.set(''); this.notice.set('');
    this.phase.set(this.hasPin() ? 'pin' : 'email');
  }

  /** Called by the HTTP interceptor on any 401 from a data endpoint. */
  onUnauthorized() {
    if (this.phase() === 'unlocked') this.lock();
  }

  // ── settings (per device) ───────────────────────────────────────────────────
  async setLockMinutes(mins: number): Promise<void> {
    const r: any = await firstValueFrom(this.http.post(`${BASE}/auth/settings`,
      { lock_minutes: mins }, { withCredentials: true }));
    this.lockMinutes.set(r.lock_minutes);
    // Re-mint the token now so the new window takes effect (and the topbar
    // countdown jumps to the new duration) immediately, not on next refresh.
    if (this.phase() === 'unlocked') this.refresh();
  }
  async changePin(pin: string): Promise<void> { await this.setPin(pin); }
  async getSettings(): Promise<any> {
    return firstValueFrom(this.http.get<any>(`${BASE}/auth/settings`, { withCredentials: true }));
  }

  /** Log out THIS device only (revokes its trusted-device token). */
  async logout(): Promise<void> {
    try { await firstValueFrom(this.http.post(`${BASE}/auth/logout`, {}, { withCredentials: true })); }
    catch { /* ignore */ }
    this.clearToken();
    this.hasPin.set(false);
    this.maskedEmail.set('');
    if (this.tick) { clearInterval(this.tick); this.tick = null; }
    this.phase.set('email');
  }
}
