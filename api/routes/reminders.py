"""Reminders routes — one unified, date-wise feed of money moving in and out
(bond payouts, FD interest, loan EMIs, recurring income & expenses).

Reminders are computed on read (see api/reminders/aggregate.py). The only thing
persisted is a per-reminder override — a moved date or a "done" tick. A daily
digest of what's due can be emailed via /send-digest (also driven by a
background scheduler in api/main.py).
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from ..reminders import aggregate, store, emailer

router = APIRouter(prefix="/api/reminders", tags=["reminders"])

# reminder status ('done'/'skipped'/'pending') → the native bond payout status,
# so ticking a bond payout here shows up on the Bonds calendar too (and back).
_BOND_STATUS = {"done": "received", "skipped": "not_received", "pending": "pending"}


@router.get("")
def feed():
    return aggregate.build_feed()


class OverrideIn(BaseModel):
    key: str
    due_date: Optional[str] = None   # move the date; null/empty clears it
    status: Optional[str] = None     # 'pending' | 'done' | 'skipped'; null clears


@router.put("/override")
def set_override(body: OverrideIn):
    """Apply a tweak to one reminder — a moved date and/or a status tick.

    Marking is *connected*: a status change on a bond payout is written to the
    bond payment-status store (so it also shows on the Bonds calendar), and an
    income/expense tick is written to the dashboard receipt store (so the
    dashboard's income bell agrees). A moved date is always kept in the reminder
    overrides table. Everything else (loans, FDs) stores its status there too.
    """
    key = (body.key or "").strip()
    raw = (body.status or "").strip()
    status_changed = raw in ("done", "skipped", "pending")

    # 1) route the status to the native store when there is one → keeps marks in sync
    handled_natively = False
    if status_changed:
        try:
            if key.startswith("bond:") and key.count(":") >= 2:
                _, bid, d = key.split(":", 2)
                from ..bonds import store as bond_store
                bond_store.set_payment_status(bid, d, _BOND_STATUS.get(raw, "pending"))
                handled_natively = True
            elif key.startswith("dividend:"):
                from ..portfolio import dividends as div_store
                did = key.split(":", 1)[1]
                div_store.set_status(did, {"done": "received", "skipped": "not_received"}.get(raw, "pending"))
                handled_natively = True
            elif key.startswith(("income:", "expense:")):
                from ..dashboard import store as receipt_store
                orig_key = key.split(":", 1)[1]
                receipt_store.set_receipt(date.today().strftime("%Y-%m"), orig_key, received=(raw == "done"))
                handled_natively = True
        except Exception:
            handled_natively = False   # fall through to the overrides table

    # 2a) a moved date on a *repeating* task (rent, salary, a monthly bill) is
    #     remembered as a day-of-month, so every future month lands on that day.
    if body.due_date is not None and aggregate.is_recurring_key(key):
        d = (body.due_date or "").strip()
        if d and len(d) >= 10:
            store.set_dom_pref(aggregate.stream_key(key), int(d[8:10]))
            # a recurring EXPENSE (e.g. Kuwait rent) → also move the expense's own
            # on_date, so the change shows up on the Expenses tab, not just here.
            aggregate.sync_expense_on_date(key, d)
        else:
            store.clear_dom_pref(aggregate.stream_key(key))

    # 2b) merge into the overrides row: absolute dates for one-off items; status
    #     only when there was no native store to take it.
    cur = store.all_overrides().get(key, {})
    due = cur.get("due_date")
    if body.due_date is not None and not aggregate.is_recurring_key(key):
        due = (body.due_date or "").strip() or None
    st = cur.get("status")
    if status_changed and not handled_natively:
        st = None if raw == "pending" else raw
    store.set_override(key, due_date=due, status=st)

    return aggregate.build_feed()


class CustomIn(BaseModel):
    date: str                              # YYYY-MM-DD
    label: str
    category: Optional[str] = "Other"      # Bonds / Stocks / F&O / …
    direction: Optional[str] = "action"    # 'action' (a to-do) | 'in' | 'out'
    amount: Optional[float] = 0.0
    owner: Optional[str] = None
    note: Optional[str] = None


@router.post("/custom")
def add_custom(body: CustomIn):
    """Add a user-defined reminder (e.g. "fill bond details on the 12th"). Tagged
    with a category so the feed shows what it's for; deletable, unlike computed items."""
    store.add_custom(body.model_dump())
    return aggregate.build_feed()


@router.delete("/custom/{cid}")
def delete_custom(cid: str):
    """Remove a user-added reminder. Also drops any override (moved date / tick)."""
    store.delete_custom(cid)
    try:
        store.set_override(f"custom:{cid}", due_date=None, status=None)
    except Exception:
        pass
    return aggregate.build_feed()


@router.get("/prefs")
def get_prefs():
    """Categories + which ones are shown (None → all), plus per-item mutes.
    Both the page and the digest respect these (hidden = not shown, not emailed)."""
    return {"categories": aggregate.CATEGORIES, "enabled": store.get_notify_prefs(),
            "muted": store.get_muted_items()}


class PrefsIn(BaseModel):
    enabled: list[str]


@router.put("/prefs")
def set_prefs(body: PrefsIn):
    valid = [c for c in body.enabled if c in aggregate.CATEGORIES]
    store.set_notify_prefs(valid)
    return {"categories": aggregate.CATEGORIES, "enabled": store.get_notify_prefs(),
            "muted": store.get_muted_items()}


class MutedIn(BaseModel):
    muted: list[str]


@router.put("/muted")
def set_muted(body: MutedIn):
    """Hide specific reminders ("source:source_id") from the page + digest."""
    store.set_muted_items(body.muted)
    return {"categories": aggregate.CATEGORIES, "enabled": store.get_notify_prefs(),
            "muted": store.get_muted_items()}


@router.post("/send-digest")
def send_digest():
    """Build + email the daily digest now (also used to test the email wiring)."""
    return emailer.send_digest()


@router.get("/digest-preview")
def digest_preview():
    """The digest payload without sending — handy for previewing the email."""
    return emailer.digest_items()
