import { Injectable } from '@angular/core';

/** A regular cash-session for one market. Add a market by appending a row. */
export interface MarketSession {
  id: string;
  label: string;
  tz: string;        // IANA timezone — DST handled automatically via Intl
  open: number;      // minutes-of-day (local)
  close: number;
}

const SESSIONS: MarketSession[] = [
  { id: 'IN', label: 'NSE', tz: 'Asia/Kolkata',    open: 9 * 60 + 15, close: 15 * 60 + 30 },
  { id: 'US', label: 'US',  tz: 'America/New_York', open: 9 * 60 + 30, close: 16 * 60 },
];

/**
 * Market-hours helper. Holdings span India + US, so live polling must run while
 * EITHER market is open — not just the NSE. Times are evaluated in each market's
 * own timezone (DST-correct via Intl), independent of the device timezone.
 */
@Injectable({ providedIn: 'root' })
export class MarketService {
  /** weekday (0=Sun…6=Sat) + minutes-of-day in the given IANA timezone. */
  private localNow(tz: string): { day: number; mins: number } {
    const parts = new Intl.DateTimeFormat('en-US', {
      timeZone: tz, weekday: 'short', hour: '2-digit', minute: '2-digit', hour12: false,
    }).formatToParts(new Date());
    const val = (t: string) => parts.find(p => p.type === t)?.value ?? '';
    const days: Record<string, number> = { Sun: 0, Mon: 1, Tue: 2, Wed: 3, Thu: 4, Fri: 5, Sat: 6 };
    const day = days[val('weekday')] ?? 1;
    let hh = parseInt(val('hour'), 10); if (hh === 24) hh = 0;     // some envs emit '24' at midnight
    const mm = parseInt(val('minute'), 10) || 0;
    return { day, mins: hh * 60 + mm };
  }

  /** Is a specific market (by id) open right now? */
  isMarketOpen(id: string): boolean {
    const s = SESSIONS.find(x => x.id === id);
    if (!s) return false;
    const { day, mins } = this.localNow(s.tz);
    if (day === 0 || day === 6) return false;
    return mins >= s.open && mins <= s.close;
  }

  /** Any tracked market open right now (drives live polling + the LIVE badge). */
  anyOpen(): boolean { return SESSIONS.some(s => this.isMarketOpen(s.id)); }

  /** Backward-compatible alias — now means "any market we hold is open". */
  isOpen(): boolean { return this.anyOpen(); }

  /** Labels of the markets open right now, e.g. ['US']. */
  openLabels(): string[] { return SESSIONS.filter(s => this.isMarketOpen(s.id)).map(s => s.label); }

  label(): string {
    const open = this.openLabels();
    return open.length ? `Live · ${open.join(' + ')}` : 'Market closed';
  }
}
