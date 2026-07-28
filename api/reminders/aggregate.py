"""
Reminder feed — one unified, date-wise timeline of money moving in and out.

Nothing here is stored: every reminder is *computed* on read from the source
of truth for each cashflow, then the user's per-reminder overrides (a moved
date, a "done" tick) are layered on top (see store.py).

Sources
  • Bonds      → each holding's coupon/principal payout schedule      (money IN)
  • FDs        → non-cumulative interest payout dates                 (money IN)
  • Loans      → projected EMI installments from next-due forward     (money OUT)
  • Income     → this month's recurring rent / salary / other income  (money IN)
  • Expenses   → this month's recurring spend                         (money OUT)

Bond coupons and FD interest are pulled from their own dated schedules, so the
month-view income items for those kinds are dropped to avoid double-counting.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional

from . import store as ov_store


def _parse(d) -> Optional[date]:
    if not d:
        return None
    s = str(d)[:10]
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def _add_months(d: date, n: int) -> date:
    import calendar
    m = d.month - 1 + n
    y = d.year + m // 12
    m = m % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


_FREQ_STEP = {"monthly": 1, "quarterly": 3, "half_yearly": 6, "yearly": 12}


def _bond_reminders(win_lo: date, win_hi: date) -> list[dict]:
    out: list[dict] = []
    try:
        from ..bonds import store as bs
        from ..bonds import engine as be
        # bond payout status (received / pending / not_received) so the reminders
        # feed agrees with the bonds calendar out of the box.
        status_map: dict[str, str] = {}
        try:
            for r in bs.list_payment_status():
                if r.get("bond_id") and r.get("date"):
                    status_map[f"{r['bond_id']}|{str(r['date'])[:10]}"] = r.get("status")
        except Exception:
            pass
        for b in bs.list_bonds():
            bid = b.get("id") or b.get("issuer")
            for p in be.schedule(b):
                d = _parse(p.get("date"))
                if not d or d < win_lo or d > win_hi or not p.get("total"):
                    continue
                st = status_map.get(f"{bid}|{d.isoformat()}")
                base = "done" if st == "received" else "skipped" if st == "not_received" else "pending"
                is_principal = (p.get("principal") or 0) > 0
                out.append({
                    "key": f"bond:{bid}:{d.isoformat()}",
                    "orig_date": d.isoformat(), "source": "bond", "source_id": bid,
                    "kind": "Bond principal" if is_principal and not p.get("interest") else "Bond payout",
                    "direction": "in", "label": b.get("issuer") or "Bond",
                    "sub": f"{b.get('owner') or '—'} · {b.get('broker') or ''}".strip(" ·"),
                    "owner": b.get("owner") or "—", "amount": round(float(p["total"]), 2),
                    "base_status": base,
                })
    except Exception:
        pass
    return out


def _fd_reminders(win_lo: date, win_hi: date) -> list[dict]:
    out: list[dict] = []
    try:
        from ..fd import store as fs
        from ..fd import engine as fe
        for f in fs.list_fds():
            fid = f.get("id") or f.get("bank")
            for p in fe.payout_schedule(f):
                d = _parse(p.get("date"))
                if not d or d < win_lo or d > win_hi or not p.get("amount"):
                    continue
                out.append({
                    "key": f"fd:{fid}:{d.isoformat()}",
                    "orig_date": d.isoformat(), "source": "fd", "source_id": fid,
                    "kind": "FD interest", "direction": "in",
                    "label": f.get("bank") or "Fixed Deposit", "sub": f.get("owner") or "—",
                    "owner": f.get("owner") or "—", "amount": round(float(p["amount"]), 2),
                    "base_status": "pending",
                })
    except Exception:
        pass
    return out


def _loan_reminders(win_lo: date, win_hi: date) -> list[dict]:
    out: list[dict] = []
    try:
        from ..loans import store as ls
        from ..loans import engine as le
        for l in le.build_summary(ls.list_loans())["loans"]:
            if l.get("closed"):
                continue
            emi_inr = float(l.get("emi_inr") or 0)
            if emi_inr <= 0:
                continue
            start = _parse(l.get("next_installment_date")) or _parse(l.get("start_date"))
            if not start:
                continue
            step = _FREQ_STEP.get(l.get("emi_frequency") or "monthly", 1)
            remaining = l.get("installments_remaining")
            maturity = _parse(l.get("maturity_date"))
            lid = l.get("id") or l.get("lender")
            d = start
            count = 0
            # hard cap so a mis-entered loan can't spin forever
            while d <= win_hi and count < 600:
                if remaining is not None and count >= int(remaining):
                    break
                if maturity and d > maturity:
                    break
                if d >= win_lo:
                    out.append({
                        "key": f"loan:{lid}:{d.isoformat()}",
                        "orig_date": d.isoformat(), "source": "loan", "source_id": lid,
                        "kind": "Loan EMI", "direction": "out",
                        "label": l.get("lender") or "Loan",
                        "sub": f"{l.get('owner') or '—'} · {l.get('loan_type') or ''}".strip(" ·"),
                        "owner": l.get("owner") or "—", "amount": round(emi_inr, 2),
                        "base_status": "pending",
                    })
                d = _add_months(d, step)
                count += 1
    except Exception:
        pass
    return out


def _month_flow_reminders(today: date) -> list[dict]:
    """This month's recurring income (rent/salary/other) and expenses, dated to
    the 1st. Bond/FD kinds are excluded — they come from their own schedules."""
    out: list[dict] = []
    period_date = date(today.year, today.month, 1).isoformat()
    try:
        from ..dashboard import aggregate as dash
        inc = dash.income_due(today)
        for it in inc.get("items", []):
            k = it.get("key", "")
            if k.startswith(("coupon:", "fd:")):
                continue  # covered by the dated bond/FD sources
            out.append({
                "key": f"income:{k}", "orig_date": period_date, "source": "income",
                "source_id": k, "kind": it.get("kind") or "Income", "direction": "in",
                "label": it.get("label") or "Income", "sub": it.get("owner") or "—",
                "owner": it.get("owner") or "—", "amount": round(float(it.get("amount") or 0), 2),
                "base_status": "done" if it.get("received") else "pending",
            })
        exp = dash.expenses_due(today)
        for it in exp.get("items", []):
            out.append({
                "key": f"expense:{it.get('key','')}", "orig_date": period_date, "source": "expense",
                "source_id": it.get("key", ""), "kind": it.get("label") or "Expense", "direction": "out",
                "label": it.get("label") or "Expense", "sub": it.get("owner") or "—",
                "owner": it.get("owner") or "—", "amount": round(float(it.get("amount") or 0), 2),
                "region": it.get("region") or "india", "currency": it.get("currency") or "INR",
                "native": round(float(it.get("native") if it.get("native") is not None else it.get("amount") or 0), 2),
                "base_status": "done" if it.get("paid") else "pending",
            })
    except Exception:
        pass
    return out


def _dividend_reminders(lo: date, hi: date) -> list[dict]:
    """Stock dividends from the dividend log → money-IN reminders, so they show on
    the calendar with their received / pending / not-received status (green /
    orange / red). Ticking one here syncs back to the dividend log."""
    out: list[dict] = []
    lo_iso, hi_iso = lo.isoformat(), hi.isoformat()
    try:
        from ..portfolio import dividends as div_store
        for d in div_store.list_dividends():
            ds = str(d.get("date") or "")[:10]
            if not ds or ds < lo_iso or ds > hi_iso:
                continue
            amt = round(float(d.get("amount") or 0), 2)
            if amt <= 0:
                continue
            st = str(d.get("status") or "").lower()
            base = "done" if st == "received" else "skipped" if st == "not_received" else "pending"
            sym = (d.get("symbol") or "").strip()
            name = (d.get("name") or "").strip()
            did = str(d.get("id") or "")
            out.append({
                "key": f"dividend:{did}", "orig_date": ds, "source": "dividend",
                "source_id": did, "kind": "Dividend", "direction": "in",
                "label": name or sym or "Dividend", "sub": sym or "Stock dividend",
                "owner": d.get("person") or "—", "amount": amt, "base_status": base,
            })
    except Exception:
        pass
    return out


def _sip_reminders(win_lo: date, win_hi: date) -> list[dict]:
    """A pending bond SIP raises one action reminder on its expected completion
    date: 'log the real purchase details'. Once logged it stops appearing."""
    out: list[dict] = []
    try:
        from ..bonds import store as bs
        for s in bs.get_sips():
            if (s.get("status") or "pending") != "pending":
                continue
            d = _parse(s.get("expected_date"))
            if not d or d < win_lo or d > win_hi:
                continue
            names = ", ".join(sp.get("name", "") for sp in (s.get("splits") or []) if sp.get("name"))
            bits = [x for x in [s.get("owner"), names, s.get("note")] if x]
            out.append({
                "key": f"sip:{s.get('id')}",
                "orig_date": d.isoformat(), "source": "sip", "source_id": s.get("id"),
                "kind": "Log SIP purchase",
                "direction": "action", "label": "Log SIP purchase details",
                "sub": " · ".join(bits) or "fill in units & price once the SIP completes",
                "owner": s.get("owner") or "—", "amount": round(float(s.get("total") or 0), 2),
                "base_status": "pending",
            })
    except Exception:
        pass
    return out


# NOTE: Kite (Zerodha) re-login nudges used to live here as "action" reminders,
# but they're data-health, not money — they now live behind the dashboard bell
# (see api/fno/health.py), so the money-calendar stays purely money in/out.


# The user-facing notification categories a reminder can belong to. Order is the
# order they appear in the "Notify me about" settings panel.
CATEGORIES = ["Bonds", "FDs", "Loans", "SIPs", "Stocks", "F&O", "Rent", "Salary",
              "Dividends", "Other income", "Expenses", "Other"]


import re as _re
import calendar as _calendar
_PERIOD_RE = _re.compile(r":\d{4}-\d{2}$")


def stream_key(key: str) -> str:
    """Drop the trailing ':YYYY-MM' so a recurring task keeps one stable id
    across months (income:salary:ID:2026-07 → income:salary:ID)."""
    return _PERIOD_RE.sub("", key or "")


def is_recurring_key(key: str) -> bool:
    """True for month-after-month income/expense items (rent, salary, bills) —
    the ones whose moved day should stick. One-off logged receipts are excluded."""
    key = key or ""
    if key.startswith("income:otherincome-log:"):
        return False
    return key.startswith("income:") or key.startswith("expense:")


def expense_id_from_key(reminder_key: str) -> Optional[str]:
    """Pull the expense id out of an expense reminder key. The key is built as
    'expense:' + the month-view key ('expense:{id}:{YYYY-MM}'), i.e.
    'expense:expense:{id}:{YYYY-MM}'. Returns None for non-expense keys."""
    k = stream_key(reminder_key or "")           # drop the trailing :YYYY-MM
    if not k.startswith("expense:expense:"):
        return None
    eid = k[len("expense:expense:"):]
    return eid or None


def sync_expense_on_date(reminder_key: str, new_date: str) -> None:
    """A recurring EXPENSE reminder was moved to a new day → move the expense's
    own on_date so the Expenses tab agrees (e.g. change Kuwait rent from the 5th
    to the 10th, and the recurring expense record follows). Keeps the expense's
    year-month and swaps only the day. Best-effort — never raises."""
    eid = expense_id_from_key(reminder_key)
    if not eid or not new_date or len(new_date) < 10:
        return
    try:
        from ..expenses import store as exp_store
        exp = exp_store.get_expense(eid)
        if not exp:
            return
        day = new_date[8:10]
        cur = str(exp.get("on_date") or "")
        new_on = (cur[:8] + day) if len(cur) >= 10 else new_date   # keep YYYY-MM-, swap DD
        if new_on != cur:
            exp_store.update_expense(eid, {"on_date": new_on})
    except Exception:
        pass


def category_of(source: str, kind: str) -> str:
    """Bucket a reminder into one of CATEGORIES (drives the notify-me toggles)."""
    if source == "bond": return "Bonds"
    if source == "fd": return "FDs"
    if source == "loan": return "Loans"
    if source == "sip": return "SIPs"
    if source == "expense": return "Expenses"
    if source == "dividend": return "Dividends"
    k = (kind or "").lower()                        # income items — split by kind
    if "rent" in k: return "Rent"
    if "salary" in k: return "Salary"
    if "dividend" in k: return "Dividends"
    return "Other income"


def _custom_reminders(lo: date, hi: date) -> list[dict]:
    """User-added reminders — a task or payment on a chosen day, tagged with what
    it's for (Bonds / Stocks / F&O / …). These are the only *deletable* items in
    the feed, since everything else is computed from the underlying data."""
    out: list[dict] = []
    try:
        rows = ov_store.list_custom()
    except Exception:
        rows = []
    for c in rows:
        try:
            d = date.fromisoformat((c.get("date") or "")[:10])
        except ValueError:
            continue
        if d < lo or d > hi:
            continue
        direction = c.get("direction") if c.get("direction") in ("in", "out", "action") else "action"
        cat = c.get("category") or "Other"
        amt = round(float(c.get("amount") or 0), 2)
        out.append({
            "key": f"custom:{c.get('id')}",
            "orig_date": d.isoformat(),
            "source": "custom", "source_id": c.get("id"),
            "kind": cat, "direction": direction,
            "label": c.get("label") or "Reminder",
            "sub": (c.get("note") or "").strip() or "you added this",
            "owner": c.get("owner") or "—",
            "amount": amt,
            "category": cat,            # explicit — never re-bucketed
            "custom": True,             # frontend shows a delete control
            "base_status": "pending",
        })
    return out


def build_feed(today: Optional[date] = None, back_months: int = 2, forward_months: int = 24) -> dict:
    """The full reminder feed + rollup. Reminders are flat and date-sorted; the
    frontend groups them by date. `back_months` keeps recent overdue items visible."""
    today = today or date.today()
    win_lo = _add_months(today, -back_months)
    win_hi = _add_months(today, forward_months)

    items = (_bond_reminders(win_lo, win_hi) + _fd_reminders(win_lo, win_hi)
             + _loan_reminders(win_lo, win_hi) + _month_flow_reminders(today)
             + _dividend_reminders(win_lo, win_hi)
             + _sip_reminders(win_lo, win_hi)
             + _custom_reminders(win_lo, win_hi))

    overrides = ov_store.all_overrides()
    dom_prefs = ov_store.get_dom_prefs()
    today_iso = today.isoformat()
    for it in items:
        ov = overrides.get(it["key"]) or {}
        # repeating tasks follow a remembered day-of-month (projected onto their
        # own month, clamped to the month's length); one-offs use an absolute
        # moved date if any.
        if is_recurring_key(it["key"]):
            dom = dom_prefs.get(stream_key(it["key"]))
            if dom:
                y, m = int(it["orig_date"][:4]), int(it["orig_date"][5:7])
                d = min(int(dom), _calendar.monthrange(y, m)[1])
                it["date"] = f"{y:04d}-{m:02d}-{d:02d}"
            else:
                it["date"] = it["orig_date"]
        else:
            it["date"] = ov.get("due_date") or it["orig_date"]
        it["status"] = ov.get("status") or it.get("base_status") or "pending"
        it["moved"] = it["date"] != it["orig_date"]
        it["overdue"] = it["status"] == "pending" and it["date"] < today_iso
        if it.get("source") != "custom":            # custom items keep their chosen tag
            it["category"] = category_of(it.get("source", ""), it.get("kind", ""))
        # region + native currency for the money-calendar country filter. Only
        # expenses are region-tagged today; everything else (income, bonds, FDs,
        # dividends…) is India / INR, so native == the ₹ amount.
        it.setdefault("region", "india")
        it.setdefault("currency", "INR")
        it.setdefault("native", it.get("amount", 0.0))
        it.pop("base_status", None)

    items.sort(key=lambda x: (x["date"], 0 if x["direction"] == "in" else 1, -x["amount"]))

    pending = [i for i in items if i["status"] == "pending"]
    overdue = [i for i in pending if i["overdue"]]
    horizon30 = (today + timedelta(days=30)).isoformat()
    upcoming = [i for i in pending if today_iso <= i["date"] <= horizon30]

    def _sum(rows, d):
        return round(sum(i["amount"] for i in rows if i["direction"] == d), 2)

    return {
        "today": today_iso,
        "items": items,
        "count": len(items),
        "pending_count": len(pending),
        "overdue_count": len(overdue),
        "overdue_amount_in": _sum(overdue, "in"),
        "overdue_amount_out": _sum(overdue, "out"),
        "upcoming_count": len(upcoming),
        "upcoming_in": _sum(upcoming, "in"),
        "upcoming_out": _sum(upcoming, "out"),
        "next_30_in": _sum(upcoming, "in"),
        "next_30_out": _sum(upcoming, "out"),
    }
