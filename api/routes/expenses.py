"""Monthly-expenses routes: per-expense CRUD + a converted, normalised summary
(by category / person / currency, subscriptions, essential vs discretionary).

Each expense is stored raw (amount + currency + frequency) and converted to a
monthly ₹-equivalent at read time (live FX via the salary feed). Storage:
Supabase (expenses) with a JSON-file fallback. See SUPABASE.md → "Monthly
expenses table".
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, UploadFile, File
from pydantic import BaseModel

from datetime import date

from ..expenses import store
from ..expenses import engine
from ..salary import fx
from ..dashboard import store as receipt_store
from ..dashboard import aggregate

router = APIRouter(prefix="/api/expenses", tags=["expenses"])


def _require_ready() -> None:
    if not store.tables_ready():
        raise HTTPException(503, store.MIGRATION_HINT)


class ExpenseIn(BaseModel):
    owner: Optional[str] = None
    name: str
    category: Optional[str] = None
    amount: Optional[float] = None
    currency: str = "INR"
    frequency: str = "monthly"          # weekly|monthly|quarterly|half_yearly|yearly|one_time
    payment_method: Optional[str] = None
    is_subscription: bool = False
    essential: bool = True
    active: bool = True
    on_date: Optional[str] = None       # for one-time: the exact date it falls
    note: Optional[str] = None


class LogIn(BaseModel):
    owner: Optional[str] = None
    name: str
    category: Optional[str] = None
    amount: Optional[float] = None
    currency: str = "INR"
    on_date: Optional[str] = None         # date the expense occurred (default today)
    payment_method: Optional[str] = None
    is_subscription: bool = False
    essential: bool = True
    template_id: Optional[str] = None
    note: Optional[str] = None


class ReminderIn(BaseModel):
    template_id: str
    period: str                            # YYYY-MM


class EntryIn(BaseModel):
    """A single logged expense in the unified (region + recurring) model.
    Region fixes the currency (India→INR, Kuwait→KWD) so logging is one tap."""
    region: str = "india"                  # india | kuwait
    name: str
    category: Optional[str] = None
    amount: Optional[float] = None
    recurring: bool = False                # carries forward every month
    frequency: str = "monthly"             # only used when recurring
    owner: Optional[str] = None
    essential: bool = True
    is_subscription: bool = False
    payment_method: Optional[str] = None
    on_date: Optional[str] = None          # one-off: date; recurring: start month
    end_date: Optional[str] = None         # recurring: the month it stops (inclusive-ish)
    note: Optional[str] = None


class StopIn(BaseModel):
    period: str                            # stop the recurring expense FROM this YYYY-MM


def _cur_period() -> str:
    t = date.today()
    return f"{t.year:04d}-{t.month:02d}"


def _entry_to_store(body: EntryIn, period: Optional[str] = None) -> dict:
    """Map the friendly (region/recurring) shape onto the raw store columns."""
    region = (body.region or "india").lower()
    freq = (body.frequency if body.recurring else "one_time")
    if body.recurring and freq == "one_time":
        freq = "monthly"
    on_date = body.on_date
    if not on_date:
        if body.recurring:
            on_date = f"{period or _cur_period()}-01"      # recurring starts this month
        else:
            on_date = date.today().isoformat()
    return {
        "owner": body.owner, "name": body.name, "category": body.category,
        "amount": body.amount, "currency": store.currency_of(region),
        "frequency": freq, "payment_method": body.payment_method,
        "is_subscription": body.is_subscription, "essential": body.essential,
        "active": True, "is_template": False, "on_date": on_date,
        "end_date": (body.end_date or None) if body.recurring else None, "note": body.note,
    }


@router.get("/meta")
def meta():
    return {"frequencies": list(store.FREQUENCIES), "categories": store.all_categories(),
            "income_categories": inbox.income_categories(),
            "owners": _owner_names(), "currencies": fx.CURRENCIES,
            "regions": [{"region": k, "label": store.REGION_LABELS[k], "currency": v}
                        for k, v in store.REGIONS.items()],
            "end_date_ready": store.end_date_ready(),
            "end_date_migration": store.END_DATE_MIGRATION}


class CategoryIn(BaseModel):
    name: str


@router.post("/categories")
def add_category(body: CategoryIn):
    """Add a user-defined category so it shows in the picker + as a category card."""
    return {"categories": store.add_custom_category(body.name)}


@router.delete("/categories/{name}")
def delete_category(name: str):
    """Remove a user-added category (built-in suggestions can't be removed)."""
    return {"categories": store.delete_custom_category(name)}


def _commitments() -> dict:
    """Recurring outflows that live on OTHER tabs — active loan EMIs and pending
    bond SIPs — read live so the expenses page reflects them without duplication."""
    canon = aggregate.canon_owner
    loans_out: list[dict] = []
    sips_out: list[dict] = []
    by_person: dict[str, float] = {}
    total = 0.0
    try:
        from ..loans import store as lstore, engine as lengine
        for l in lengine.build_summary(lstore.list_loans())["loans"]:
            if l.get("closed"):
                continue
            monthly = float(l.get("emi_monthly_inr") or 0)
            if monthly <= 0:
                continue
            owner = canon(l.get("owner"))
            loans_out.append({
                "id": l.get("id"), "label": l.get("lender") or "Loan", "owner": owner,
                "monthly_inr": round(monthly, 2), "until": l.get("maturity_date"),
                "remaining": l.get("installments_remaining"), "sub": l.get("loan_type") or "Loan EMI",
            })
            by_person[owner] = by_person.get(owner, 0.0) + monthly
            total += monthly
    except Exception:
        pass
    try:
        from ..bonds import store as bstore
        for s in bstore.get_sips():
            if (s.get("status") or "pending") != "pending":
                continue
            monthly = float(s.get("total") or 0)
            if monthly <= 0:
                continue
            owner = canon(s.get("owner"))
            names = ", ".join(sp.get("name", "") for sp in (s.get("splits") or []) if sp.get("name"))
            sips_out.append({
                "id": s.get("id"), "label": names or "Bond SIP", "owner": owner,
                "monthly_inr": round(monthly, 2), "until": s.get("expected_date"), "sub": "Bond SIP",
            })
            by_person[owner] = by_person.get(owner, 0.0) + monthly
            total += monthly
    except Exception:
        pass
    return {
        "total_monthly_inr": round(total, 2),
        "by_person": {k: round(v, 2) for k, v in by_person.items()},
        "loans": loans_out, "sips": sips_out,
        "count": len(loans_out) + len(sips_out),
    }


def _person_cards(summ: dict, commit: dict) -> list[dict]:
    """One card per canonical family member: what they spent this month + their
    recurring load + synced loan/SIP commitments, even when zero."""
    from ..people.store import CANON_NAMES
    spent = {p["person"]: p for p in summ.get("by_person", [])}
    cp = commit.get("by_person", {})
    out = []
    for name in CANON_NAMES:
        p = spent.get(name, {})
        committed = float(cp.get(name, 0.0))
        out.append({
            "person": name,
            "spent_inr": round(float(p.get("spent_inr", 0.0)), 2),
            "recurring_inr": round(float(p.get("recurring_inr", 0.0)), 2),
            "onetime_inr": round(float(p.get("onetime_inr", 0.0)), 2),
            "committed_inr": round(committed, 2),
            "total_inr": round(float(p.get("spent_inr", 0.0)) + committed, 2),
            "count": int(p.get("count", 0)),
        })
    return sorted(out, key=lambda x: -x["total_inr"])


# ── unified month view (per-person cards + carry-forward recurring) ────────────
@router.get("/month")
def month(period: Optional[str] = None):
    _require_ready()
    period = period or _cur_period()
    summ = engine.month_summary(store.list_expenses(), period, _cur_period(),
                                canon=aggregate.canon_owner)
    commit = _commitments()
    summ["commitments"] = commit
    summ["person_cards"] = _person_cards(summ, commit)
    summ["end_date_ready"] = store.end_date_ready()
    return summ


@router.post("/entry")
def create_entry(body: EntryIn):
    _require_ready()
    if not (body.name or "").strip():
        raise HTTPException(400, "Name is required.")
    return engine.enrich(store.create_expense(_entry_to_store(body)))


@router.put("/entry/{eid}")
def update_entry(eid: str, body: EntryIn):
    _require_ready()
    existing = store.get_expense(eid)
    patch = _entry_to_store(body, period=str(existing.get("on_date") or "")[:7] if existing else None)
    # don't clobber the original start month of a recurring expense on edit
    if existing and body.recurring and not body.on_date and existing.get("on_date"):
        patch["on_date"] = existing["on_date"]
    updated = store.update_expense(eid, patch)
    if not updated:
        raise HTTPException(404, "Expense not found.")
    return engine.enrich(updated)


@router.post("/entry/{eid}/stop")
def stop_entry(eid: str, body: StopIn):
    """Stop a recurring expense from `period` onward — past months keep it."""
    _require_ready()
    updated = store.update_expense(eid, {"end_date": f"{body.period}-01"})
    if not updated:
        raise HTTPException(404, "Expense not found.")
    return {"ok": True, "persisted": store.end_date_ready(), "entry": engine.enrich(updated)}


@router.delete("/entry/{eid}")
def delete_entry(eid: str):
    _require_ready()
    if not store.delete_expense(eid):
        raise HTTPException(404, "Expense not found.")
    return {"ok": True}


# ── Import expenses from a bank statement ─────────────────────────────────────
# Upload a bank statement → parse every debit (spend) + credit (income), auto-tag
# categories, let the user edit inline, then import the chosen debits as one-time
# expenses. Already-imported rows are flagged so re-uploading never double-counts.
# The de-dup ledger is shared with the Claude/MCP inbox (api/expenses/inbox.py),
# so a statement imported one way is recognised as done by the other.
from ..expenses import inbox


def _imported_refs() -> set:
    return inbox.imported_refs()


def _add_imported_refs(refs: list[str]) -> None:
    inbox.add_imported_refs(refs)


@router.post("/statement/preview")
async def statement_preview(file: UploadFile = File(...), owner: str = ""):
    """Parse a bank statement into spend/earn rows with guessed categories."""
    _require_ready()
    from ..expenses import statement as stmt
    content = await file.read()
    if not content:
        raise HTTPException(400, "The uploaded file is empty.")
    try:
        res = stmt.parse_transactions(content, file.filename or "")
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(400, f"Could not read the statement: {e}")
    done = _imported_refs()
    for t in res["transactions"]:
        t["owner"] = owner or None
        t["already"] = t["ref"] in done
    res["categories"] = store.all_categories()
    res["owner"] = owner or None
    return res


class StmtItem(BaseModel):
    date: str
    amount: float
    name: str
    category: Optional[str] = None
    owner: Optional[str] = None
    note: Optional[str] = None
    ref: Optional[str] = None
    direction: str = "debit"                 # debit → expense; credit → other-income


class StmtImportIn(BaseModel):
    items: list[StmtItem]


@router.post("/statement/import")
def statement_import(body: StmtImportIn):
    """Route each chosen row: debits → one-time expenses, credits → other-income
    log entries (both INR). Returns how many of each were created."""
    _require_ready()
    spends, income, refs = 0, 0, []
    for it in body.items:
        if not it.name or not it.amount:
            continue
        on_date = (it.date or "")[:10] or None
        if (it.direction or "debit") == "credit":
            try:
                from ..other_income import store as inc_store
                inc_store.create_income({
                    "owner": it.owner, "source": it.name.strip(),
                    "category": (it.category or "Other income").strip(), "amount": float(it.amount),
                    "currency": "INR", "frequency": "one_time", "active": True,
                    "is_template": False, "on_date": on_date, "note": it.note or None,
                })
                income += 1
            except Exception:
                continue
        else:
            store.create_expense({
                "owner": it.owner, "name": it.name.strip(),
                "category": (it.category or "Miscellaneous").strip(), "amount": float(it.amount),
                "currency": "INR", "frequency": "one_time", "payment_method": "bank",
                "is_subscription": False, "essential": True, "active": True,
                "is_template": False, "on_date": on_date, "end_date": None, "note": it.note or None,
            })
            spends += 1
        if it.ref:
            refs.append(it.ref)
    _add_imported_refs(refs)
    return {"ok": True, "imported": spends + income, "spends": spends, "income": income}


# ── Inbox: transactions pushed in by Claude over MCP, awaiting verification ───
# Claude reads the account statement, categorises each line against THIS app's
# category list, and pushes them here. Nothing reaches the ledger until the user
# reviews and approves in the Expenses tab.
@router.get("/inbox")
def inbox_list():
    """All batches (newest first) + the badge counts for the tab."""
    st = inbox.inbox_status()
    return {
        "batches": inbox.list_batches(),          # the list — st["batches"] is only a count
        "batch_count": st.get("batches", 0),
        "pending_rows": st.get("pending_rows", 0),
        "needs_review": st.get("needs_review", 0),
        "durable": st.get("durable", False),
    }


@router.get("/inbox/{bid}")
def inbox_get(bid: str):
    batch = inbox.batch_with_totals(bid)
    if not batch:
        raise HTTPException(404, "That import batch is gone — ask Claude to send it again.")
    return {
        "batch": batch,
        "categories": store.all_categories(),
        "income_categories": inbox.income_categories(),
        "owners": _owner_names(),
    }


class RowPatchIn(BaseModel):
    patches: list[dict]                      # [{id, category?, owner?, name?, amount?, date?, direction?, status?}]


@router.patch("/inbox/{bid}/rows")
def inbox_patch(bid: str, body: RowPatchIn):
    batch = inbox.patch_rows(bid, body.patches)
    if not batch:
        raise HTTPException(404, "Import batch not found.")
    full = inbox.batch_with_totals(batch)
    return {"batch": full, "also_updated": batch.pop("_applied", 0)}


class RowIdsIn(BaseModel):
    row_ids: Optional[list[str]] = None       # None on approve = every pending row


@router.post("/inbox/{bid}/approve")
def inbox_approve(bid: str, body: RowIdsIn):
    """Commit the chosen rows: debits → expenses, credits → other-income."""
    _require_ready()
    res = inbox.approve(bid, body.row_ids)
    if res is None:
        raise HTTPException(404, "Import batch not found.")
    return res


@router.post("/inbox/{bid}/reject")
def inbox_reject(bid: str, body: RowIdsIn):
    batch = inbox.reject(bid, body.row_ids or [])
    if not batch:
        raise HTTPException(404, "Import batch not found.")
    return inbox.batch_with_totals(bid)


@router.post("/inbox/{bid}/restore")
def inbox_restore(bid: str, body: RowIdsIn):
    batch = inbox.restore(bid, body.row_ids or [])
    if not batch:
        raise HTTPException(404, "Import batch not found.")
    return inbox.batch_with_totals(bid)


@router.delete("/inbox/{bid}")
def inbox_delete(bid: str):
    if not inbox.delete_batch(bid):
        raise HTTPException(404, "Import batch not found.")
    return {"ok": True}


class DiscardIn(BaseModel):
    ids: Optional[list[str]] = None      # specific pushes…
    all_pending: bool = False            # …or everything still awaiting review


@router.post("/inbox/discard")
def inbox_discard(body: DiscardIn):
    """Remove pushes from the inbox. Already-approved rows stay in the ledger."""
    if body.all_pending:
        res = inbox.clear_inbox(only_pending=True)
    elif body.ids:
        res = inbox.delete_batches(body.ids)
    else:
        raise HTTPException(400, "Pass ids, or all_pending: true.")
    return {"ok": True, **res, "remaining": inbox.list_batches()}


# ── merchant OWNER rules (who a merchant belongs to) ──────────────────────────
# Categories are NOT remembered by design: every statement is re-classified fresh,
# so a wrong category never propagates to future transactions of the same merchant.
@router.get("/rules")
def list_rules():
    """The explicit merchant→owner rules."""
    rules = inbox.merchant_rules()
    return {"rules": [{"key": k, **v} for k, v in rules.items()], "learned": []}


class RuleIn(BaseModel):
    merchant: str
    category: Optional[str] = None      # accepted but ignored — categories aren't remembered
    owner: Optional[str] = None


@router.post("/rules")
def set_rule(body: RuleIn):
    if not (body.merchant or "").strip():
        raise HTTPException(400, "Which merchant?")
    rules = inbox.set_merchant_rule(body.merchant, owner=body.owner)   # owner only
    return {"ok": True, "rules": len(rules)}


@router.delete("/rules/{merchant}")
def delete_rule(merchant: str):
    return {"ok": True, "rules": len(inbox.delete_merchant_rule(merchant))}


def _owner_names() -> list[str]:
    try:
        from ..people.store import CANON_NAMES
        return list(CANON_NAMES)
    except Exception:
        return ["Ranjeev", "Sanjeev", "Ramprasad", "Maha"]


# ── Money view: income for the month + what's actually in hand ────────────────
@router.get("/overview")
def overview(period: Optional[str] = None):
    """The other half of the tab: this month's INCOME (logged receipts, by
    category/person) and the live cash + bank balance — so the page answers
    "what came in, what went out, what's left"."""
    period = period or _cur_period()
    income: dict = {"count": 0, "monthly_total": 0.0, "by_category": [], "by_person": [], "entries": []}
    try:
        from ..other_income import store as inc_store, engine as inc_engine
        income = inc_engine.log_summary(inc_store.list_log(period), canon=aggregate.canon_owner)
    except Exception:
        pass
    income["period"] = period

    cash: dict = {"total": 0.0, "cash": 0.0, "bank": 0.0, "by_person": [], "count": 0}
    try:
        from ..cash import store as cash_store, engine as cash_engine
        cash = cash_engine.build_summary(cash_store.list_items(), canon=aggregate.canon_owner)
    except Exception:
        pass

    spend = 0.0
    try:
        summ = engine.month_summary(store.list_expenses(), period, _cur_period(),
                                    canon=aggregate.canon_owner)
        spend = float(summ.get("total_inr") or 0)
    except Exception:
        pass

    income_total = float(income.get("monthly_total") or 0)
    return {
        "period": period,
        "income": {
            "total_inr": round(income_total, 2),
            "count": income.get("count", 0),
            "by_category": income.get("by_category", []),
            "by_person": income.get("by_person", []),
            "entries": income.get("entries", []),
        },
        "in_hand": {
            "total_inr": round(float(cash.get("total") or 0), 2),
            "cash_inr": round(float(cash.get("cash") or 0), 2),
            "bank_inr": round(float(cash.get("bank") or 0), 2),
            "accounts": cash.get("count", 0),
            "by_person": cash.get("by_person", []),
            "entries": [{"owner": e.get("owner"), "where": e.get("where"), "type": e.get("type"),
                         "label": e.get("account_label"), "balance_inr": e.get("balance_inr"),
                         "currency": e.get("currency"), "balance": e.get("balance"),
                         "as_of_date": e.get("as_of_date")} for e in (cash.get("entries") or [])],
        },
        "spend_inr": round(spend, 2),
        "net_inr": round(income_total - spend, 2),
        "inbox": inbox.inbox_status(),
    }


class IncomeRowIn(BaseModel):
    """Inline edit of one logged income row from the Expenses tab."""
    source: Optional[str] = None
    category: Optional[str] = None
    owner: Optional[str] = None
    amount: Optional[float] = None
    on_date: Optional[str] = None
    note: Optional[str] = None


@router.post("/income")
def create_income_row(body: IncomeRowIn):
    """Log a receipt straight from the Expenses tab (money in, same month view)."""
    from ..other_income import store as inc_store, engine as inc_engine
    if not (body.source or "").strip():
        raise HTTPException(400, "What was the income? (source is required)")
    if not body.amount or float(body.amount) <= 0:
        raise HTTPException(400, "An amount above zero is required.")
    return inc_engine.enrich(inc_store.create_income({
        "owner": body.owner, "source": body.source.strip(),
        "category": (body.category or "Other income").strip(),
        "amount": float(body.amount), "currency": "INR", "frequency": "one_time",
        "active": True, "is_template": False,
        "on_date": body.on_date or date.today().isoformat(), "note": body.note,
    }))


@router.put("/income/{eid}")
def update_income_row(eid: str, body: IncomeRowIn):
    from ..other_income import store as inc_store, engine as inc_engine
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    patch["is_template"] = False
    updated = inc_store.update_income(eid, patch)
    if not updated:
        raise HTTPException(404, "Income entry not found.")
    return inc_engine.enrich(updated)


@router.delete("/income/{eid}")
def delete_income_row(eid: str):
    from ..other_income import store as inc_store
    if not inc_store.delete_income(eid):
        raise HTTPException(404, "Income entry not found.")
    return {"ok": True}


@router.get("/fx")
def fx_rates(refresh: bool = False):
    return fx.get_rates(force=refresh)


@router.get("/methods")
def methods():
    _require_ready()
    return {"methods": store.payment_methods()}


@router.get("/summary")
def summary():
    """Recurring-template projection (monthly-normalised) — feeds surplus."""
    _require_ready()
    return engine.build_summary(store.list_templates(), canon=aggregate.canon_owner)


# ── reminder templates (recurring definitions) ────────────────────────────────
@router.get("/templates")
def templates(period: Optional[str] = None):
    """Templates for the reminders, each flagged whether it's already been
    logged in `period`."""
    _require_ready()
    period = period or _cur_period()
    logged = store.log_template_ids(period)
    out = []
    for t in store.list_templates():
        e = engine.enrich(t)
        e["added"] = t["id"] in logged
        out.append(e)
    out.sort(key=lambda x: (x.get("added"), -(x.get("monthly_inr") or 0)))
    return {"period": period, "templates": out}


@router.post("/templates")
def create_template(body: ExpenseIn):
    _require_ready()
    if not (body.name or "").strip():
        raise HTTPException(400, "Name is required.")
    data = body.model_dump()
    data["is_template"] = True
    return engine.enrich(store.create_expense(data))


@router.put("/templates/{eid}")
def update_template(eid: str, patch: dict):
    _require_ready()
    updated = store.update_expense(eid, patch)
    if not updated:
        raise HTTPException(404, "Template not found.")
    return engine.enrich(updated)


@router.delete("/templates/{eid}")
def delete_template(eid: str):
    _require_ready()
    if not store.delete_expense(eid):
        raise HTTPException(404, "Template not found.")
    return {"ok": True}


@router.post("/reminder")
def toggle_reminder(body: ReminderIn):
    """Tick a reminder: add it to the log for `period` (or remove if already
    there). Returns the created log entry, or the removed flag."""
    _require_ready()
    tpl = store.get_expense(body.template_id)
    if not tpl:
        raise HTTPException(404, "Template not found.")
    existing = [e for e in store.list_log(body.period) if e.get("template_id") == body.template_id]
    if existing:
        for e in existing:
            store.delete_expense(e["id"])
        return {"added": False, "removed": len(existing)}
    on_date = date.today().isoformat() if body.period == _cur_period() else f"{body.period}-01"
    entry = {
        "owner": tpl.get("owner"), "name": tpl.get("name"), "category": tpl.get("category"),
        "amount": tpl.get("amount"), "currency": tpl.get("currency"), "frequency": "one_time",
        "payment_method": tpl.get("payment_method"), "is_subscription": tpl.get("is_subscription"),
        "essential": tpl.get("essential"), "active": True, "is_template": False,
        "template_id": body.template_id, "on_date": on_date, "note": tpl.get("note"),
    }
    return {"added": True, "entry": engine.enrich(store.create_expense(entry))}


# ── actual expense log (the month-by-month table) ─────────────────────────────
@router.get("/log")
def log(period: Optional[str] = None):
    """Actual logged expenses for a month (default current) + totals/splits."""
    _require_ready()
    period = period or _cur_period()
    summ = engine.log_summary(store.list_log(period), canon=aggregate.canon_owner)
    summ["period"] = period
    return summ


@router.post("/log")
def create_log(body: LogIn):
    _require_ready()
    if not (body.name or "").strip():
        raise HTTPException(400, "Name is required.")
    data = body.model_dump()
    data["is_template"] = False
    data["frequency"] = "one_time"
    data["active"] = True
    if not data.get("on_date"):
        data["on_date"] = date.today().isoformat()
    return engine.enrich(store.create_expense(data))


@router.put("/log/{eid}")
def update_log(eid: str, patch: dict):
    _require_ready()
    patch = dict(patch); patch["is_template"] = False
    updated = store.update_expense(eid, patch)
    if not updated:
        raise HTTPException(404, "Expense not found.")
    return engine.enrich(updated)


@router.delete("/log/{eid}")
def delete_log(eid: str):
    _require_ready()
    if not store.delete_expense(eid):
        raise HTTPException(404, "Expense not found.")
    return {"ok": True}
