import { Component, OnInit, inject, signal, computed, output, input } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { RemindersService, ReminderFeed, ReminderItem } from '../../services/reminders.service';

interface DayCell {
  day: number; iso: string; inTotal: number; outTotal: number; net: number;
  count: number; pending: number; isToday: boolean; padding: boolean; tone: string;
}
interface DayGroup {
  date: string; label: string; weekday: string; rel: string;
  overdue: boolean; isToday: boolean; items: ReminderItem[]; inTotal: number; outTotal: number;
}

/**
 * The Reminders page, condensed for the dashboard: a colour-coded month calendar
 * on the left and a live task list (what's due, most-urgent first) on the right,
 * sharing one set of filters + show/hide prefs. Ticking a payout received/paid,
 * moving a date, skipping or hiding an item all work here and stay in sync with
 * the source stores (bonds calendar, dashboard receipts, digest email).
 */
@Component({
  selector: 'app-reminder-calendar',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink],
  templateUrl: './reminder-calendar.html',
  styleUrl: './reminder-calendar.scss',
})
export class ReminderCalendar implements OnInit {
  private api = inject(RemindersService);
  /** re-emits the recomputed feed after a change, so a parent's badge stays fresh. */
  feedChange = output<ReminderFeed>();
  /** categories to drop entirely (the dashboard hides income noise: Rent/Salary/…). */
  hideCategories = input<string[]>([]);

  feed = signal<ReminderFeed | null>(null);
  loading = signal(true);
  private now = new Date();
  viewYear = signal(this.now.getFullYear());
  viewMonth = signal(this.now.getMonth());
  selectedDay = signal<string | null>(null);

  // filters (drive both the calendar and the task list)
  dir = signal<'all' | 'in' | 'out'>('all');
  owner = signal('');
  showDone = signal(true);   // completed items visible by default (collected + editable)
  // country filter: All → everything in ₹ · Kuwait → only KWD entries, in KD.
  country = signal<'all' | 'india' | 'kuwait'>('all');
  hasKuwait = computed(() => (this.feed()?.items || []).some(i => i.region === 'kuwait'));

  // show/hide prefs — categories + individually-hidden items
  allCategories = signal<string[]>([]);
  enabledCats = signal<Set<string>>(new Set());
  mutedItems = signal<Set<string>>(new Set());
  prefsOpen = signal(false);
  savingPrefs = signal(false);

  editKey = signal<string | null>(null);
  editDate = signal<string>('');
  busyKey = signal<string | null>(null);
  digestMsg = signal<string | null>(null);
  sendingDigest = signal(false);

  inr = RemindersService.inr;
  abs(v: number): number { return Math.abs(v); }
  /** amount to show/sum in the SELECTED currency: KWD-native when viewing Kuwait,
   *  else the ₹ value (India entries have native == ₹, so 'all'/'india' use ₹). */
  amt(it: ReminderItem): number { return this.country() === 'kuwait' ? (it.native ?? it.amount) : it.amount; }
  /** format a value in the selected currency (KD for Kuwait, ₹ otherwise) */
  money(v: number): string {
    return this.country() === 'kuwait'
      ? 'KD ' + (v || 0).toLocaleString('en-IN', { maximumFractionDigits: 3 })
      : this.inr(v);
  }
  readonly WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  private readonly MONTHS = ['January', 'February', 'March', 'April', 'May', 'June',
    'July', 'August', 'September', 'October', 'November', 'December'];

  ngOnInit() {
    this.api.feed().subscribe({
      next: f => { this.feed.set(f); this.loading.set(false); this.feedChange.emit(f); },
      error: () => this.loading.set(false),
    });
    this.api.prefs().subscribe({
      next: p => {
        this.allCategories.set(p.categories);
        this.enabledCats.set(new Set(p.enabled === null ? p.categories : p.enabled));
        this.mutedItems.set(new Set(p.muted || []));
      },
      error: () => {},
    });
  }

  private today = computed(() => this.feed()?.today || '');
  monthKey = computed(() => `${this.viewYear()}-${String(this.viewMonth() + 1).padStart(2, '0')}`);
  monthLabel = computed(() => `${this.MONTHS[this.viewMonth()]} ${this.viewYear()}`);
  isCurrentMonth = computed(() => this.monthKey() === (this.today() || '').slice(0, 7));

  owners = computed(() => {
    const s = new Set<string>();
    for (const it of this.feed()?.items || []) if (it.owner && it.owner !== '—') s.add(it.owner);
    return [...s].sort();
  });

  mutedId(it: ReminderItem): string { return `${it.source}:${it.source_id}`; }
  private inScope(it: ReminderItem): boolean {
    if (this.hideCategories().includes(it.category)) return false;
    // action items (log-SIP / relogin-Kite) aren't money in/out — a direction
    // filter of in|out shouldn't drop them; only an explicit hide should.
    if (this.dir() !== 'all' && it.direction !== 'action' && it.direction !== this.dir()) return false;
    if (this.country() !== 'all' && (it.region || 'india') !== this.country()) return false;
    if (this.owner() && it.owner !== this.owner()) return false;
    if (this.allCategories().length && !this.enabledCats().has(it.category)) return false;
    if (this.mutedItems().has(this.mutedId(it))) return false;
    return true;
  }
  isAction(it: ReminderItem): boolean { return it.direction === 'action'; }
  private monthItems = computed<ReminderItem[]>(() =>
    (this.feed()?.items || []).filter(it => it.date.slice(0, 7) === this.monthKey() && this.inScope(it)));

  monthIn = computed(() => this.monthItems().filter(i => i.direction === 'in').reduce((s, i) => s + this.amt(i), 0));
  monthOut = computed(() => this.monthItems().filter(i => i.direction === 'out').reduce((s, i) => s + this.amt(i), 0));
  monthNet = computed(() => this.monthIn() - this.monthOut());
  // collected (money-IN received) and paid (expenses settled) so far this month —
  // shown as "collected of total" so you see progress at a glance.
  monthInGot = computed(() => this.monthItems().filter(i => i.direction === 'in' && i.status === 'done').reduce((s, i) => s + this.amt(i), 0));
  monthOutPaid = computed(() => this.monthItems().filter(i => i.direction === 'out' && i.status === 'done').reduce((s, i) => s + this.amt(i), 0));
  overdueCount = computed(() => (this.feed()?.overdue_count) || 0);
  // "In hand" = what's actually landed minus what's actually been paid this month
  // (revenue − expense, realised so far) — the headline the user acts on.
  inHand = computed(() => this.monthInGot() - this.monthOutPaid());
  inGotPct = computed(() => { const t = this.monthIn(); return t > 0 ? Math.min(1, this.monthInGot() / t) : 0; });
  outPaidPct = computed(() => { const t = this.monthOut(); return t > 0 ? Math.min(1, this.monthOutPaid() / t) : 0; });

  // ── colour-coded 42-cell grid ────────────────────────────────────────────────
  cells = computed<DayCell[]>(() => {
    const y = this.viewYear(), m = this.viewMonth();
    const first = new Date(y, m, 1).getDay();
    const dim = new Date(y, m + 1, 0).getDate();
    const byDay = new Map<number, ReminderItem[]>();
    for (const it of this.monthItems()) {
      const d = +it.date.slice(8, 10);
      (byDay.get(d) || byDay.set(d, []).get(d)!).push(it);
    }
    let maxFlow = 1;
    for (const items of byDay.values()) {
      const flow = items.reduce((s, i) => s + this.amt(i), 0);
      if (flow > maxFlow) maxFlow = flow;
    }
    const lvl = (v: number) => { const r = v / maxFlow; return r <= 0 ? 0 : r <= 0.25 ? 1 : r <= 0.5 ? 2 : r <= 0.75 ? 3 : 4; };
    const out: DayCell[] = [];
    const pad = (): DayCell => ({ day: 0, iso: '', inTotal: 0, outTotal: 0, net: 0, count: 0, pending: 0, isToday: false, padding: true, tone: '' });
    for (let i = 0; i < first; i++) out.push(pad());
    for (let d = 1; d <= dim; d++) {
      const iso = `${this.monthKey()}-${String(d).padStart(2, '0')}`;
      const items = byDay.get(d) || [];
      const inT = items.filter(i => i.direction === 'in').reduce((s, i) => s + this.amt(i), 0);
      const outT = items.filter(i => i.direction === 'out').reduce((s, i) => s + this.amt(i), 0);
      const net = inT - outT;
      // colour by STATUS (like the dividends/bonds calendars): green = all got,
      // orange = pending, red = didn't get (skipped), blend = partly done.
      // action items (log-SIP / relogin) aren't money — keep them out of the
      // ₹-graded colour so a due-date cell isn't tinted by a phantom amount.
      const money = items.filter(i => i.direction !== 'action');
      const gotAmt = money.filter(i => i.status === 'done').reduce((s, i) => s + this.amt(i), 0);
      const pendAmt = money.filter(i => i.status === 'pending').reduce((s, i) => s + this.amt(i), 0);
      const missAmt = money.filter(i => i.status === 'skipped').reduce((s, i) => s + this.amt(i), 0);
      const awaiting = pendAmt + missAmt;
      let tone = '';
      if (items.length) {
        const L = lvl(inT + outT) || 1;
        if (gotAmt > 0 && awaiting > 0) tone = `mx${L}`;
        else if (missAmt > 0 && pendAmt === 0 && gotAmt === 0) tone = `rd${L}`;
        else if (awaiting > 0 && gotAmt === 0) tone = `yl${L}`;
        else if (gotAmt > 0) tone = `gn${L}`;
      }
      out.push({
        day: d, iso, padding: false, count: items.length, inTotal: inT, outTotal: outT, net, tone,
        pending: items.filter(i => i.status === 'pending').length, isToday: iso === this.today(),
      });
    }
    while (out.length % 7 !== 0) out.push(pad());
    return out;
  });

  // ── task list (right column) — the selected month only, in date order ────────
  taskGroups = computed<DayGroup[]>(() => {
    const items = this.monthItems().filter(it => this.showDone() || it.status === 'pending');
    const byDate = new Map<string, ReminderItem[]>();
    for (const it of items) (byDate.get(it.date) || byDate.set(it.date, []).get(it.date)!).push(it);
    const today = this.today();
    const out: DayGroup[] = [];
    for (const [date, its] of [...byDate.entries()].sort((a, b) => a[0].localeCompare(b[0]))) {
      out.push({
        date, label: this.fmtDate(date), weekday: this.weekday(date), rel: this.rel(date, today),
        overdue: date < today && its.some(i => i.status === 'pending'), isToday: date === today,
        items: its.sort((a, b) => (a.direction === b.direction ? b.amount - a.amount : a.direction === 'in' ? -1 : 1)),
        inTotal: its.filter(i => i.direction === 'in').reduce((s, i) => s + this.amt(i), 0),
        outTotal: its.filter(i => i.direction === 'out').reduce((s, i) => s + this.amt(i), 0),
      });
    }
    return out;
  });
  taskCount = computed(() => this.taskGroups().reduce((s, g) => s + g.items.length, 0));

  // ── selected-day dialog ──────────────────────────────────────────────────────
  dayItems = computed<ReminderItem[]>(() => {
    const sel = this.selectedDay();
    if (!sel) return [];
    return this.monthItems().filter(it => it.date === sel)
      .sort((a, b) => (a.direction === b.direction ? b.amount - a.amount : a.direction === 'in' ? -1 : 1));
  });
  dayIn = computed(() => this.dayItems().filter(i => i.direction === 'in').reduce((s, i) => s + this.amt(i), 0));
  dayOut = computed(() => this.dayItems().filter(i => i.direction === 'out').reduce((s, i) => s + this.amt(i), 0));
  selectedLabel = computed(() => { const s = this.selectedDay(); return s ? this.fmtDate(s) : ''; });
  selectedWeekday = computed(() => { const s = this.selectedDay(); return s ? this.weekday(s) : ''; });

  // ── navigation ───────────────────────────────────────────────────────────────
  prevMonth() { let m = this.viewMonth() - 1, y = this.viewYear(); if (m < 0) { m = 11; y--; } this.viewMonth.set(m); this.viewYear.set(y); this.selectedDay.set(null); }
  nextMonth() { let m = this.viewMonth() + 1, y = this.viewYear(); if (m > 11) { m = 0; y++; } this.viewMonth.set(m); this.viewYear.set(y); this.selectedDay.set(null); }
  goToday() { const t = new Date(); this.viewYear.set(t.getFullYear()); this.viewMonth.set(t.getMonth()); this.selectedDay.set(null); }
  // clicking a calendar cell (or a day header in the list) narrows the task list
  // to that date; a "back" button restores the whole month. No popup on the home.
  selectDay(c: DayCell) { if (c.padding || !c.count) return; this.selectedDay.set(this.selectedDay() === c.iso ? null : c.iso); }
  openDay(iso: string) { this.selectedDay.set(iso); }
  clearDay() { this.selectedDay.set(null); this.editKey.set(null); }

  // ── date helpers ─────────────────────────────────────────────────────────────
  private d(iso: string): Date { const [y, m, dd] = iso.split('-').map(Number); return new Date(y, m - 1, dd); }
  fmtDate(iso: string): string { return this.d(iso).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' }); }
  weekday(iso: string): string { return this.d(iso).toLocaleDateString('en-IN', { weekday: 'long' }); }
  rel(iso: string, today: string): string {
    if (!today) return '';
    const days = Math.round((this.d(iso).getTime() - this.d(today).getTime()) / 86400000);
    if (days === 0) return 'Today';
    if (days === 1) return 'Tomorrow';
    if (days === -1) return 'Yesterday';
    if (days < 0) return `${-days}d overdue`;
    if (days < 30) return `in ${days}d`;
    return `in ${Math.round(days / 30)}mo`;
  }

  // ── show / hide prefs ────────────────────────────────────────────────────────
  catOn(c: string): boolean { return this.enabledCats().has(c); }
  toggleCat(c: string) { const s = new Set(this.enabledCats()); s.has(c) ? s.delete(c) : s.add(c); this.enabledCats.set(s); this.savePrefs(); }
  showAllCats() { this.enabledCats.set(new Set(this.allCategories())); this.savePrefs(); }
  mutedCatCount = computed(() => Math.max(0, this.allCategories().length - this.enabledCats().size));
  private savePrefs() {
    this.savingPrefs.set(true);
    this.api.setPrefs([...this.enabledCats()]).subscribe({ next: () => this.savingPrefs.set(false), error: () => this.savingPrefs.set(false) });
  }
  itemMuted(it: ReminderItem): boolean { return this.mutedItems().has(this.mutedId(it)); }
  toggleMute(it: ReminderItem) { const s = new Set(this.mutedItems()); const id = this.mutedId(it); s.has(id) ? s.delete(id) : s.add(id); this.mutedItems.set(s); this.saveMuted(); }
  unmute(id: string) { const s = new Set(this.mutedItems()); s.delete(id); this.mutedItems.set(s); this.saveMuted(); }
  unmuteAll() { this.mutedItems.set(new Set()); this.saveMuted(); }
  private saveMuted() { this.savingPrefs.set(true); this.api.setMuted([...this.mutedItems()]).subscribe({ next: () => this.savingPrefs.set(false), error: () => this.savingPrefs.set(false) }); }
  hiddenCount = computed(() => this.mutedItems().size);
  hiddenTotal = computed(() => this.mutedCatCount() + this.hiddenCount());
  /** how many filters differ from the defaults — drives the badge on the Filters button. */
  activeFilters = computed(() =>
    (this.dir() !== 'all' ? 1 : 0) +
    (this.country() !== 'all' ? 1 : 0) +
    (this.owner() ? 1 : 0) +
    (!this.showDone() ? 1 : 0) +
    this.hiddenTotal());
  resetFilters() {
    this.dir.set('all'); this.country.set('all'); this.owner.set(''); this.showDone.set(true);
    this.showAllCats(); this.unmuteAll();
  }

  // ── two-column filter popover (same UX as the stocks page) ─────────────────────
  filterCat = signal<'dir' | 'region' | 'who' | 'status' | 'cats'>('dir');
  /** the left-rail categories — region/who only appear when relevant. `active` marks
   *  the ones that carry a non-default choice (shows the little dot). */
  filterCats = computed(() => {
    type Key = 'dir' | 'region' | 'who' | 'status' | 'cats';
    const cats: { key: Key; label: string; active: () => boolean }[] = [
      { key: 'dir', label: 'Direction', active: () => this.dir() !== 'all' },
    ];
    if (this.hasKuwait()) cats.push({ key: 'region', label: 'Region', active: () => this.country() !== 'all' });
    if (this.owners().length > 1) cats.push({ key: 'who', label: 'Who', active: () => !!this.owner() });
    cats.push({ key: 'status', label: 'Completed', active: () => !this.showDone() });
    cats.push({ key: 'cats', label: 'Categories', active: () => this.hiddenTotal() > 0 });
    return cats;
  });
  hiddenItems = computed(() => {
    const ids = this.mutedItems(); if (!ids.size) return [] as { id: string; label: string; sub: string }[];
    const seen = new Map<string, { id: string; label: string; sub: string }>();
    for (const it of this.feed()?.items || []) { const id = this.mutedId(it); if (ids.has(id) && !seen.has(id)) seen.set(id, { id, label: it.label, sub: it.category }); }
    for (const id of ids) if (!seen.has(id)) seen.set(id, { id, label: id.split(':').slice(1).join(':') || id, sub: id.split(':')[0] });
    return [...seen.values()].sort((a, b) => a.label.localeCompare(b.label));
  });

  // ── marking (connected to the source stores) ──────────────────────────────────
  private apply(key: string, patch: { due_date?: string | null; status?: any }) {
    this.busyKey.set(key);
    this.api.setOverride(key, patch).subscribe({
      next: f => { this.feed.set(f); this.busyKey.set(null); this.feedChange.emit(f); },
      error: () => this.busyKey.set(null),
    });
  }
  markDone(it: ReminderItem) { this.apply(it.key, { status: it.status === 'done' ? 'pending' : 'done' }); }
  skip(it: ReminderItem) { this.apply(it.key, { status: it.status === 'skipped' ? 'pending' : 'skipped' }); }
  openEdit(it: ReminderItem) { this.editKey.set(it.key); this.editDate.set(it.date); }
  cancelEdit() { this.editKey.set(null); }
  saveEdit(it: ReminderItem) { const dv = this.editDate(); this.editKey.set(null); this.apply(it.key, { due_date: dv === it.orig_date ? null : dv }); }

  sendDigest() {
    this.sendingDigest.set(true); this.digestMsg.set(null);
    this.api.sendDigest().subscribe({
      next: r => {
        this.sendingDigest.set(false);
        this.digestMsg.set(r.sent ? `Emailed ${r.count} reminder(s)` :
          r.reason === 'nothing_due' ? 'Nothing due right now' :
          r.reason === 'no_api_key' ? 'Email not configured' : `Could not send (${r.reason})`);
        setTimeout(() => this.digestMsg.set(null), 6000);
      },
      error: () => { this.sendingDigest.set(false); this.digestMsg.set('Could not send the digest'); },
    });
  }

  actionWord(it: ReminderItem): string { return it.direction === 'in' ? 'Got it' : it.direction === 'out' ? 'Paid' : 'Done'; }
  kindTag(it: ReminderItem): string {
    if (it.source === 'custom') {
      const m: Record<string, string> = { Bonds: 'BOND', Stocks: 'STOCK', 'F&O': 'F&O', FDs: 'FD', Loans: 'LOAN', SIPs: 'SIP', Dividends: 'DIV', Expenses: 'BILL' };
      return m[it.category] || 'NOTE';
    }
    switch (it.source) {
      case 'bond': return 'BOND';
      case 'loan': return 'EMI';
      case 'fd': return 'FD';
      case 'income': return 'IN';
      case 'expense': return 'BILL';
      case 'dividend': return 'DIV';
      case 'sip': return 'SIP';
      case 'kite': return 'KITE';
      default: return '•';
    }
  }

  // ── action items (log-SIP / relogin-Kite / your own to-do) — a CTA, not money ─
  actionCta(it: ReminderItem): string {
    if (it.source === 'custom') return 'Open';
    return it.source === 'sip' ? 'Log details' : 'Re-login';
  }
  /** where the CTA takes you: the tab a custom item is tagged for, else SIP/F&O. */
  actionLink(it: ReminderItem): any[] {
    if (it.source === 'custom') {
      const r: Record<string, string> = { Bonds: '/bonds', FDs: '/bonds', Loans: '/bonds', SIPs: '/bonds', Stocks: '/stocks', Dividends: '/stocks', 'F&O': '/fno', Expenses: '/expenses' };
      return [r[it.category] || '/reminders'];
    }
    return it.source === 'sip' ? ['/bonds'] : ['/fno'];
  }
  actionQuery(it: ReminderItem): Record<string, string> {
    return it.source === 'sip' ? { view: 'investments', sip: it.source_id } : {};
  }

  deleteCustom(it: ReminderItem) {
    if (!it.custom) return;
    const id = it.source_id || it.key.replace('custom:', '');
    this.busyKey.set(it.key);
    this.api.deleteCustom(id).subscribe({
      next: f => { this.feed.set(f); this.busyKey.set(null); },
      error: () => { this.busyKey.set(null); },
    });
  }
}
