import { Component, OnInit, inject, signal, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpErrorResponse } from '@angular/common/http';
import { RemindersService, ReminderFeed, ReminderItem } from '../../services/reminders.service';

interface DayCell {
  day: number; iso: string; inTotal: number; outTotal: number; net: number;
  count: number; pending: number; isToday: boolean; padding: boolean; tone: string;
}
interface DayGroup {
  date: string; label: string; weekday: string; rel: string;
  overdue: boolean; isToday: boolean; items: ReminderItem[];
  inTotal: number; outTotal: number;
}
interface MemberFlow { owner: string; inTotal: number; outTotal: number; net: number; }

@Component({
  selector: 'app-reminders',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './reminders.html',
  styleUrl: './reminders.scss',
})
export class Reminders implements OnInit {
  private api = inject(RemindersService);

  feed = signal<ReminderFeed | null>(null);
  loading = signal(true);
  error = signal<string | null>(null);

  // view + navigation
  view = signal<'calendar' | 'timeline'>('calendar');
  viewYear = signal<number>(new Date().getFullYear());
  viewMonth = signal<number>(new Date().getMonth());   // 0..11
  selectedDay = signal<string | null>(null);           // iso; null = whole month

  // filters
  dir = signal<'all' | 'in' | 'out'>('all');
  owner = signal<string>('');
  showDone = signal(false);

  // what to show / hide — categories + individual items (persisted, live)
  allCategories = signal<string[]>([]);
  enabledCats = signal<Set<string>>(new Set());        // categories currently shown
  mutedItems = signal<Set<string>>(new Set());         // hidden items ("source:source_id")
  prefsOpen = signal(false);
  savingPrefs = signal(false);

  // inline date-edit + digest
  editKey = signal<string | null>(null);
  editDate = signal<string>('');
  busyKey = signal<string | null>(null);
  digestMsg = signal<string | null>(null);
  sendingDigest = signal(false);

  // add-your-own reminder (a one-off task or payment on a chosen day)
  addOpen = signal(false);
  addBusy = signal(false);
  naLabel = signal('');
  naDate = signal('');
  naCategory = signal('Bonds');
  naDir = signal<'action' | 'in' | 'out'>('action');
  naAmount = signal<number | null>(null);
  naNote = signal('');
  readonly addCategories = ['Bonds', 'Stocks', 'F&O', 'FDs', 'Loans', 'SIPs', 'Dividends', 'Expenses', 'Other'];

  inr = RemindersService.inr;
  abs(v: number): number { return Math.abs(v); }
  readonly WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  private readonly MONTHS = ['January', 'February', 'March', 'April', 'May', 'June',
    'July', 'August', 'September', 'October', 'November', 'December'];

  ngOnInit() { this.load(); this.loadPrefs(); }

  load() {
    this.loading.set(true); this.error.set(null);
    this.api.feed().subscribe({
      next: f => { this.feed.set(f); this.loading.set(false); },
      error: (_e: HttpErrorResponse) => { this.loading.set(false); this.error.set('Could not load reminders. Is the backend running?'); },
    });
  }

  loadPrefs() {
    this.api.prefs().subscribe({
      next: p => {
        this.allCategories.set(p.categories);
        this.enabledCats.set(new Set(p.enabled === null ? p.categories : p.enabled));
        this.mutedItems.set(new Set(p.muted || []));
      },
      error: () => {},
    });
  }
  catOn(c: string): boolean { return this.enabledCats().has(c); }
  toggleCat(c: string) {
    const s = new Set(this.enabledCats());
    s.has(c) ? s.delete(c) : s.add(c);
    this.enabledCats.set(s);
    this.savePrefs();
  }
  showAllCats() { this.enabledCats.set(new Set(this.allCategories())); this.savePrefs(); }
  private savePrefs() {
    this.savingPrefs.set(true);
    this.api.setPrefs([...this.enabledCats()]).subscribe({
      next: () => this.savingPrefs.set(false),
      error: () => this.savingPrefs.set(false),
    });
  }
  /** count of hidden categories — for the "N hidden" pill. */
  mutedCatCount = computed(() => Math.max(0, this.allCategories().length - this.enabledCats().size));

  // ── per-item hide (e.g. one recurring expense) ──────────────────────────────
  mutedId(it: ReminderItem): string { return `${it.source}:${it.source_id}`; }
  itemMuted(it: ReminderItem): boolean { return this.mutedItems().has(this.mutedId(it)); }
  toggleMute(it: ReminderItem) {
    const s = new Set(this.mutedItems()); const id = this.mutedId(it);
    s.has(id) ? s.delete(id) : s.add(id);
    this.mutedItems.set(s);
    this.saveMuted();
  }
  unmute(id: string) { const s = new Set(this.mutedItems()); s.delete(id); this.mutedItems.set(s); this.saveMuted(); }
  unmuteAll() { this.mutedItems.set(new Set()); this.saveMuted(); }
  private saveMuted() { this.savingPrefs.set(true); this.api.setMuted([...this.mutedItems()]).subscribe({ next: () => this.savingPrefs.set(false), error: () => this.savingPrefs.set(false) }); }
  hiddenCount = computed(() => this.mutedItems().size);
  /** every hidden thing (categories + items), for the manager in the panel. */
  hiddenItems = computed(() => {
    const ids = this.mutedItems(); if (!ids.size) return [] as { id: string; label: string; sub: string }[];
    const seen = new Map<string, { id: string; label: string; sub: string }>();
    for (const it of this.feed()?.items || []) {
      const id = this.mutedId(it);
      if (ids.has(id) && !seen.has(id)) seen.set(id, { id, label: it.label, sub: it.category });
    }
    for (const id of ids) if (!seen.has(id)) seen.set(id, { id, label: id.split(':').slice(1).join(':') || id, sub: id.split(':')[0] });
    return [...seen.values()].sort((a, b) => a.label.localeCompare(b.label));
  });
  /** total hidden (categories + items) — drives the header badge. */
  hiddenTotal = computed(() => this.mutedCatCount() + this.hiddenCount());

  private today = computed(() => this.feed()?.today || '');
  monthKey = computed(() => `${this.viewYear()}-${String(this.viewMonth() + 1).padStart(2, '0')}`);
  monthLabel = computed(() => `${this.MONTHS[this.viewMonth()]} ${this.viewYear()}`);
  isCurrentMonth = computed(() => this.monthKey() === (this.today() || '').slice(0, 7));

  owners = computed(() => {
    const s = new Set<string>();
    for (const it of this.feed()?.items || []) if (it.owner && it.owner !== '—') s.add(it.owner);
    return [...s].sort();
  });

  private inScope(it: ReminderItem): boolean {
    if (this.dir() !== 'all' && it.direction !== this.dir()) return false;
    if (this.owner() && it.owner !== this.owner()) return false;
    // hidden categories + individually-hidden items drop out of the page entirely
    if (this.allCategories().length && !this.enabledCats().has(it.category)) return false;
    if (this.mutedItems().has(this.mutedId(it))) return false;
    return true;
  }

  private monthItems = computed<ReminderItem[]>(() =>
    (this.feed()?.items || []).filter(it => it.date.slice(0, 7) === this.monthKey() && this.inScope(it)));

  // ── hero rollup ──────────────────────────────────────────────────────────────
  hero = computed(() => {
    const f = this.feed();
    if (!f) return null;
    return {
      overdueCount: f.overdue_count, overdueIn: f.overdue_amount_in, overdueOut: f.overdue_amount_out,
      next30In: f.next_30_in, next30Out: f.next_30_out, upcomingCount: f.upcoming_count,
      pendingCount: f.pending_count,
      monthNet: this.monthIn() - this.monthOut(),
    };
  });

  // ── per-member income vs expense for the month ───────────────────────────────
  members = computed<MemberFlow[]>(() => {
    const map = new Map<string, MemberFlow>();
    for (const it of this.monthItems()) {
      const m = map.get(it.owner) || { owner: it.owner, inTotal: 0, outTotal: 0, net: 0 };
      if (it.direction === 'in') m.inTotal += it.amount; else m.outTotal += it.amount;
      m.net = m.inTotal - m.outTotal;
      map.set(it.owner, m);
    }
    return [...map.values()].sort((a, b) => b.inTotal + b.outTotal - (a.inTotal + a.outTotal));
  });

  monthIn = computed(() => this.monthItems().filter(i => i.direction === 'in').reduce((s, i) => s + i.amount, 0));
  monthOut = computed(() => this.monthItems().filter(i => i.direction === 'out').reduce((s, i) => s + i.amount, 0));
  monthPendingIn = computed(() => this.monthItems().filter(i => i.direction === 'in' && i.status === 'pending').reduce((s, i) => s + i.amount, 0));
  monthPendingOut = computed(() => this.monthItems().filter(i => i.direction === 'out' && i.status === 'pending').reduce((s, i) => s + i.amount, 0));

  // ── colour-coded calendar grid (42 cells) ────────────────────────────────────
  cells = computed<DayCell[]>(() => {
    const y = this.viewYear(), m = this.viewMonth();
    const first = new Date(y, m, 1).getDay();
    const dim = new Date(y, m + 1, 0).getDate();
    const byDay = new Map<number, ReminderItem[]>();
    for (const it of this.monthItems()) {
      const d = +it.date.slice(8, 10);
      (byDay.get(d) || byDay.set(d, []).get(d)!).push(it);
    }
    // scale intensity to the busiest day this month (by gross flow)
    let maxFlow = 1;
    for (const items of byDay.values()) {
      const flow = items.reduce((s, i) => s + i.amount, 0);
      if (flow > maxFlow) maxFlow = flow;
    }
    const lvl = (v: number) => { const r = v / maxFlow; return r <= 0 ? 0 : r <= 0.25 ? 1 : r <= 0.5 ? 2 : r <= 0.75 ? 3 : 4; };
    const out: DayCell[] = [];
    const pad = (): DayCell => ({ day: 0, iso: '', inTotal: 0, outTotal: 0, net: 0, count: 0, pending: 0, isToday: false, padding: true, tone: '' });
    for (let i = 0; i < first; i++) out.push(pad());
    for (let d = 1; d <= dim; d++) {
      const iso = `${this.monthKey()}-${String(d).padStart(2, '0')}`;
      const items = byDay.get(d) || [];
      const inT = items.filter(i => i.direction === 'in').reduce((s, i) => s + i.amount, 0);
      const outT = items.filter(i => i.direction === 'out').reduce((s, i) => s + i.amount, 0);
      const net = inT - outT;
      let tone = '';
      if (items.length) tone = net >= 0 ? `in${lvl(inT + outT)}` : `out${lvl(inT + outT)}`;
      out.push({
        day: d, iso, padding: false, count: items.length, inTotal: inT, outTotal: outT, net, tone,
        pending: items.filter(i => i.status === 'pending').length, isToday: iso === this.today(),
      });
    }
    while (out.length % 7 !== 0) out.push(pad());
    return out;
  });

  // ── day-detail (items on the selected day, for inline marking) ───────────────
  dayItems = computed<ReminderItem[]>(() => {
    const sel = this.selectedDay();
    if (!sel) return [];
    return this.monthItems().filter(it => it.date === sel)
      .sort((a, b) => (a.direction === b.direction ? b.amount - a.amount : a.direction === 'in' ? -1 : 1));
  });
  dayIn = computed(() => this.dayItems().filter(i => i.direction === 'in').reduce((s, i) => s + i.amount, 0));
  dayOut = computed(() => this.dayItems().filter(i => i.direction === 'out').reduce((s, i) => s + i.amount, 0));

  // ── date-separated payments list (below the calendar) ────────────────────────
  monthGroups = computed<DayGroup[]>(() => this.groupsFrom(this.monthItems(), null));
  timelineGroups = computed<DayGroup[]>(() => this.groupsFrom((this.feed()?.items || []).filter(it => this.inScope(it)), null));

  private groupsFrom(items: ReminderItem[], _x: null): DayGroup[] {
    const byDate = new Map<string, ReminderItem[]>();
    for (const it of items) {
      if (!this.showDone() && it.status !== 'pending') continue;
      (byDate.get(it.date) || byDate.set(it.date, []).get(it.date)!).push(it);
    }
    const today = this.today();
    const out: DayGroup[] = [];
    for (const [date, its] of [...byDate.entries()].sort((a, b) => a[0].localeCompare(b[0]))) {
      out.push({
        date, label: this.fmtDate(date), weekday: this.weekday(date), rel: this.rel(date, today),
        overdue: date < today && its.some(i => i.status === 'pending'),
        isToday: date === today, items: its,
        inTotal: its.filter(i => i.direction === 'in').reduce((s, i) => s + i.amount, 0),
        outTotal: its.filter(i => i.direction === 'out').reduce((s, i) => s + i.amount, 0),
      });
    }
    return out;
  }
  hasMonthItems = computed(() => this.monthGroups().length > 0);

  // ── navigation ───────────────────────────────────────────────────────────────
  prevMonth() { let m = this.viewMonth() - 1, y = this.viewYear(); if (m < 0) { m = 11; y--; } this.viewMonth.set(m); this.viewYear.set(y); this.selectedDay.set(null); }
  nextMonth() { let m = this.viewMonth() + 1, y = this.viewYear(); if (m > 11) { m = 0; y++; } this.viewMonth.set(m); this.viewYear.set(y); this.selectedDay.set(null); }
  goToday() { const t = new Date(); this.viewYear.set(t.getFullYear()); this.viewMonth.set(t.getMonth()); this.selectedDay.set(null); }
  selectDay(c: DayCell) { if (c.padding || !c.count) { this.selectedDay.set(null); return; } this.selectedDay.set(this.selectedDay() === c.iso ? null : c.iso); }
  clearDay() { this.selectedDay.set(null); }
  selectedLabel = computed(() => { const s = this.selectedDay(); return s ? this.fmtDate(s) : ''; });
  selectedWeekday = computed(() => { const s = this.selectedDay(); return s ? this.weekday(s) : ''; });

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

  // ── actions ───────────────────────────────────────────────────────────────────
  private apply(key: string, patch: { due_date?: string | null; status?: any }) {
    this.busyKey.set(key);
    this.api.setOverride(key, patch).subscribe({
      next: f => { this.feed.set(f); this.busyKey.set(null); },
      error: () => { this.busyKey.set(null); this.load(); },
    });
  }
  markDone(it: ReminderItem) { this.apply(it.key, { status: it.status === 'done' ? 'pending' : 'done' }); }
  skip(it: ReminderItem) { this.apply(it.key, { status: it.status === 'skipped' ? 'pending' : 'skipped' }); }

  openEdit(it: ReminderItem) { this.editKey.set(it.key); this.editDate.set(it.date); }
  cancelEdit() { this.editKey.set(null); }
  saveEdit(it: ReminderItem) { const d = this.editDate(); this.editKey.set(null); this.apply(it.key, { due_date: d === it.orig_date ? null : d }); }

  sendDigest() {
    this.sendingDigest.set(true); this.digestMsg.set(null);
    this.api.sendDigest().subscribe({
      next: r => {
        this.sendingDigest.set(false);
        this.digestMsg.set(
          r.sent ? `Emailed ${r.count} reminder(s)` :
          r.reason === 'nothing_due' ? 'Nothing due right now' :
          r.reason === 'no_api_key' ? 'Email not configured (printed to server log)' :
          `Could not send (${r.reason})`);
        setTimeout(() => this.digestMsg.set(null), 6000);
      },
      error: () => { this.sendingDigest.set(false); this.digestMsg.set('Could not send the digest'); },
    });
  }

  actionWord(it: ReminderItem): string { return it.direction === 'in' ? 'Got it' : it.direction === 'out' ? 'Paid' : 'Done'; }
  isAction(it: ReminderItem): boolean { return it.direction === 'action'; }
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
      default: return '•';
    }
  }

  // ── add / delete a user reminder ──────────────────────────────────────────────
  openAdd() {
    this.naDate.set(this.today() || new Date().toISOString().slice(0, 10));
    this.naLabel.set(''); this.naNote.set(''); this.naAmount.set(null);
    this.naCategory.set('Bonds'); this.naDir.set('action');
    this.addOpen.set(true);
  }
  closeAdd() { this.addOpen.set(false); }
  submitAdd() {
    const label = this.naLabel().trim(); const date = this.naDate();
    if (!label || !date) return;
    this.addBusy.set(true);
    this.api.addCustom({
      date, label, category: this.naCategory(), direction: this.naDir(),
      amount: this.naDir() === 'action' ? 0 : (this.naAmount() || 0),
      note: this.naNote().trim() || undefined,
    }).subscribe({
      next: f => { this.feed.set(f); this.addBusy.set(false); this.addOpen.set(false); },
      error: () => { this.addBusy.set(false); },
    });
  }
  deleteCustom(it: ReminderItem) {
    if (!it.custom) return;
    const id = it.source_id || it.key.replace('custom:', '');
    this.busyKey.set(it.key);
    this.api.deleteCustom(id).subscribe({
      next: f => { this.feed.set(f); this.busyKey.set(null); if (this.dayItems().length === 0) this.selectedDay.set(null); },
      error: () => { this.busyKey.set(null); this.load(); },
    });
  }
}
