"""Expense INBOX — transactions pushed in from outside (Claude / the MCP
connector) and parked for the user to verify before they touch the ledger.

Flow:
  1. Claude reads the account statement, calls `expense_categories()` on the
     Networth MCP server to learn the EXACT category vocabulary, then calls
     `submit_expense_transactions(...)` with one row per line of the statement.
  2. Every row lands here as a *staged* row — nothing is written to the expenses
     or other-income tables yet.
  3. The Expenses tab shows the batch, with filters/edit/bulk tools. The user
     ticks what's right, fixes what isn't, and approves.
  4. Only on approve do rows become real: debits → one-time expenses, credits →
     other-income log entries.

Nothing here ever guesses a number. Amounts, dates and directions are validated
on the way in; anything unparseable is flagged `needs_review` with the reason and
cannot be approved until the user fixes it in the UI.

Storage: the generic app_cache KV (Supabase `app_cache`, local-file fallback) —
one blob per batch plus a small index. Same KV the F&O statements use.
"""
from __future__ import annotations

import re
import uuid
from datetime import date, datetime
from typing import Any, Optional

# ── KV keys ───────────────────────────────────────────────────────────────────
_INDEX_KEY = "expense_inbox_index"          # {"batches": [ …summary… ]}
_BATCH_KEY = "expense_inbox_batch:"          # + batch id  → {"batch": {...}}
_REFS_KEY = "expense_statement_refs"         # shared with the file-upload import
_RULES_KEY = "expense_merchant_rules"        # merchant → the category you file it under

MAX_ROWS_PER_BATCH = 2000
_INDEX_KEEP = 40                             # keep the last N batches in the index

DIRECTIONS = ("debit", "credit")


# The ledger + rules are read on every import, every categories call and every
# edit. Each read is a ~200 ms round trip, so hold them briefly in process and
# drop the cache the moment we write. Short enough that a change made elsewhere
# in the app shows up on the next import.
_CACHE_TTL = 20.0
_cache: dict = {"rules": None, "rules_at": 0.0, "ledger": None, "ledger_at": 0.0}
_batch_cache: dict = {}          # id → (batch, fetched_at); an edit rewrites it


def _fresh(at: float) -> bool:
    import time
    return (time.time() - at) < _CACHE_TTL


def invalidate_cache(rules: bool = True, ledger: bool = True) -> None:
    """Called after anything that writes rules or the ledger."""
    if rules:
        _cache["rules"] = None
    if ledger:
        _cache["ledger"] = None


def _ledger_rows() -> tuple:
    """(expenses, income, cash) — cached briefly, fetched concurrently on a miss."""
    import time
    if _cache["ledger"] is not None and _fresh(_cache["ledger_at"]):
        return _cache["ledger"]
    from . import store as exp_store
    from ..other_income import store as inc_store
    from ..cash import store as cash_store
    got = _parallel({"expenses": exp_store.list_expenses,
                     "income": inc_store.list_income,
                     "cash": cash_store.list_items})
    rows = (got.get("expenses") or [], got.get("income") or [], got.get("cash") or [])
    _cache["ledger"], _cache["ledger_at"] = rows, time.time()
    return rows


def _kv():
    from ..portfolio import store as kv
    return kv


def _now() -> str:
    return datetime.now().isoformat()


def _new_id(n: int = 10) -> str:
    return uuid.uuid4().hex[:n]


# ── de-dup refs (shared with the manual statement upload) ─────────────────────
def imported_refs() -> set:
    """Signatures of statement rows already turned into ledger entries."""
    try:
        rec = _kv().cache_get(_REFS_KEY)
        val = (rec or {}).get("value")
        return set(val.get("refs", [])) if isinstance(val, dict) else set()
    except Exception:
        return set()


def add_imported_refs(refs: list[str]) -> None:
    try:
        cur = imported_refs() | {r for r in refs if r}
        _kv().cache_set(_REFS_KEY, {"refs": list(cur)[-8000:]})
    except Exception:
        pass


def merchant_rules() -> dict:
    """{normalised merchant: {category, owner, updated_at}} — the rules you (or
    Claude) set explicitly. These outrank anything inferred from history."""
    import time
    if _cache["rules"] is not None and _fresh(_cache["rules_at"]):
        return _cache["rules"]
    try:
        rec = _kv().cache_get(_RULES_KEY)
        val = (rec or {}).get("value")
        if isinstance(val, dict) and isinstance(val.get("rules"), dict):
            _cache["rules"], _cache["rules_at"] = val["rules"], time.time()
            return val["rules"]
    except Exception:
        pass
    _cache["rules"], _cache["rules_at"] = {}, time.time()
    return {}


def set_merchant_rules(updates: list[dict]) -> dict:
    """Store MANY rules in one round trip — [{merchant, category?, owner?}, …].
    Editing ten rows used to mean ten reads and ten writes; this is one of each."""
    if not updates:
        return merchant_rules()
    rules = dict(merchant_rules())
    for u in updates:
        key = _norm_merchant(u.get("merchant") or "")
        if not key:
            continue
        cur = dict(rules.get(key) or {})
        if u.get("category") is not None:
            cur["category"] = _clean_text(u["category"], 60)
        if u.get("owner") is not None:
            cur["owner"] = _clean_text(u["owner"], 40)
        cur["merchant"] = _clean_text(u.get("merchant"), 80)
        cur["updated_at"] = _now()
        if not cur.get("category") and not cur.get("owner"):
            rules.pop(key, None)
        else:
            rules[key] = cur
    try:
        _kv().cache_set(_RULES_KEY, {"rules": rules})
    except Exception:
        pass
    invalidate_cache(ledger=False)
    _cache["rules"] = rules
    import time as _t
    _cache["rules_at"] = _t.time()
    return rules


def set_merchant_rule(merchant: str, category: Optional[str] = None,
                      owner: Optional[str] = None) -> dict:
    """Remember that `merchant` is filed under `category` (and optionally whose it
    is). Applies to every future import of that merchant, whatever the amount."""
    return set_merchant_rules([{"merchant": merchant, "category": category, "owner": owner}])


def delete_merchant_rule(merchant: str) -> dict:
    rules = dict(merchant_rules())
    if rules.pop(_norm_merchant(merchant), None) is not None:
        try:
            _kv().cache_set(_RULES_KEY, {"rules": rules})
        except Exception:
            pass
        invalidate_cache(ledger=False)
    return rules


def make_ref(iso_date: Optional[str], amount: float, narration: str) -> str:
    """The same signature shape the manual statement import writes, so a row
    imported one way is recognised as already-done by the other."""
    return f"{iso_date or '?'}|{round(float(amount or 0), 2)}|{(narration or '')[:60]}"


# ── coercion helpers ──────────────────────────────────────────────────────────
_NUM_RE = re.compile(r"-?[\d.]+")
_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%d-%m-%y",
                 "%Y/%m/%d", "%d %b %Y", "%d %B %Y", "%b %d, %Y", "%d.%m.%Y", "%d.%m.%y")


def _as_amount(v: Any) -> Optional[float]:
    """A positive rupee amount, or None when it can't be read. Accepts
    '₹1,234.50', '1234.5', -1234.5 (sign is carried by `direction`)."""
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
    else:
        s = str(v).strip().replace(",", "").replace("₹", "").replace("rs.", "").replace("RS.", "")
        s = s.replace("INR", "").replace("inr", "").strip()
        m = _NUM_RE.search(s)
        if not m:
            return None
        try:
            f = float(m.group(0))
        except ValueError:
            return None
    if f != f or f in (float("inf"), float("-inf")):     # NaN / inf
        return None
    return round(abs(f), 2)


def _sign_of(v: Any) -> int:
    """-1 when the raw amount was written negative (a common debit convention)."""
    try:
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return -1 if float(v) < 0 else 1
        return -1 if str(v).strip().startswith("-") else 1
    except Exception:
        return 1


def _as_date(v: Any) -> Optional[str]:
    """Normalise to YYYY-MM-DD, or None."""
    if not v:
        return None
    if isinstance(v, (datetime, date)):
        return v.strftime("%Y-%m-%d")
    s = str(v).strip()[:24]
    if not s:
        return None
    s = s.replace("T", " ").split(" ")[0]
    for fmt in _DATE_FORMATS:
        try:
            d = datetime.strptime(s, fmt).date()
            if 1990 <= d.year <= 2100:
                return d.isoformat()
        except ValueError:
            continue
    return None


def _clean_text(v: Any, limit: int = 240) -> str:
    return re.sub(r"\s+", " ", str(v or "").strip())[:limit]


# ── whose expense is it? ──────────────────────────────────────────────────────
# A statement line rarely says who spent it, so we infer, best signal first, and
# record WHICH signal won (`owner_source`) so the review screen can show its
# reasoning and let you sweep the weak guesses.
#
#   claude    → the model set it explicitly (it read the context)
#   narration → a family member's name appears in the statement text
#   history   → this merchant has consistently been that person's before
#   account   → the statement's account belongs to them (cash tab)
#   category  → this category is overwhelmingly one person's
#   default   → the batch default; nothing else matched  ← treated as a guess
_STRONG_SOURCES = ("claude", "user", "rule", "narration", "history", "account")


def _norm_merchant(s: str) -> str:
    """A stable key for 'the same merchant' across statements."""
    s = re.sub(r"[^a-z ]+", " ", (s or "").lower())
    return re.sub(r"\s+", " ", s).strip()[:40]


class OwnerMatcher:
    """Learns who-owns-what from the existing ledger + the cash accounts, then
    attributes each incoming row. Built once per batch."""

    def __init__(self, account: Optional[str] = None, default_owner: Optional[str] = None,
                 cash_items: Optional[list] = None):
        from ..people.store import CANON_NAMES, ALIASES
        self.canon = list(CANON_NAMES)
        self.default = default_owner if default_owner in CANON_NAMES else None
        self.by_merchant: dict[str, dict[str, int]] = {}
        self.by_category: dict[str, dict[str, int]] = {}
        # Only OWNER rules are consulted (who pays is stable). Categories are NOT
        # remembered — Claude re-classifies every transaction, so a wrong category
        # never carries forward.
        self.rules = merchant_rules()
        # aliases long enough to match safely on a word boundary ("ram" is too
        # short — it hides inside real merchant names)
        self.aliases = [(a, who) for a, who in ALIASES.items() if len(a) >= 4]
        self.account_owner = self._account_owner(account, cash_items)

    def learn(self, name: str, category: str, owner: str) -> None:
        # Learns OWNER signal only (merchant→who, category→who). Category is never
        # learned as a merchant's filing — see the class note.
        key = _norm_merchant(name)
        cat = (category or "").strip()
        if owner not in self.canon:
            return
        if key:
            self.by_merchant.setdefault(key, {})[owner] = self.by_merchant.setdefault(key, {}).get(owner, 0) + 1
        if cat:
            self.by_category.setdefault(cat, {})[owner] = self.by_category.setdefault(cat, {}).get(owner, 0) + 1

    @staticmethod
    def _account_owner(account: Optional[str], cash_items: Optional[list] = None) -> Optional[str]:
        """The person whose bank/cash account this statement came from."""
        acc = (account or "").strip().lower()
        if not acc:
            return None
        try:
            from ..people.store import canon_owner, CANON_NAMES
            if cash_items is None:
                from ..cash import store as cash_store
                cash_items = cash_store.list_items()
            for it in cash_items:
                for field in (it.get("where"), it.get("account_label")):
                    label = (field or "").strip().lower()
                    if not label:
                        continue
                    if label in acc or acc in label:
                        who = canon_owner(it.get("owner"))
                        return who if who in CANON_NAMES else None
        except Exception:
            pass
        return None

    def _from_text(self, text: str) -> Optional[str]:
        low = (text or "").lower()
        for alias, who in self.aliases:
            if re.search(rf"\b{re.escape(alias)}\b", low):
                return who
        return None

    @staticmethod
    def _dominant(counts: dict, min_rows: int, min_share: float) -> Optional[str]:
        total = sum(counts.values())
        if total < min_rows:
            return None
        who, n = max(counts.items(), key=lambda kv: kv[1])
        return who if (n / total) >= min_share else None

    def remembered_owner(self, name: str) -> str:
        rule = self.rules.get(_norm_merchant(name))
        return (rule or {}).get("owner") or ""

    def match(self, *, claude_owner: str, name: str, narration: str, category: str) -> tuple[str, str]:
        """→ (owner, source). Owner is '' when nothing at all is known."""
        ruled = self.remembered_owner(name)
        if ruled in self.canon:
            return ruled, "rule"
        if claude_owner in self.canon:
            return claude_owner, "claude"
        who = self._from_text(f"{name} {narration}")
        if who:
            return who, "narration"
        who = self._dominant(self.by_merchant.get(_norm_merchant(name), {}), min_rows=2, min_share=0.6)
        if who:
            return who, "history"
        if self.account_owner:
            return self.account_owner, "account"
        who = self._dominant(self.by_category.get((category or "").strip(), {}), min_rows=3, min_share=0.7)
        if who:
            return who, "category"
        return (self.default or ""), ("default" if self.default else "none")


def _direction_of(raw: dict, amount_sign: int) -> Optional[str]:
    """debit = money out (expense), credit = money in (income)."""
    for key in ("direction", "type", "kind", "flow", "dr_cr"):
        val = str(raw.get(key) or "").strip().lower()
        if not val:
            continue
        if val in ("debit", "dr", "withdrawal", "withdrawl", "spend", "expense", "out", "paid", "payment"):
            return "debit"
        if val in ("credit", "cr", "deposit", "income", "in", "received", "receipt", "earned"):
            return "credit"
    if raw.get("withdrawal") not in (None, "", 0) and _as_amount(raw.get("withdrawal")):
        return "debit"
    if raw.get("deposit") not in (None, "", 0) and _as_amount(raw.get("deposit")):
        return "credit"
    if amount_sign < 0:
        return "debit"
    return None


# ── normalise one incoming row ────────────────────────────────────────────────
def normalise_row(raw: dict, *, default_owner: Optional[str],
                  known_expense_cats: set, known_income_cats: set,
                  matcher: Optional[OwnerMatcher] = None) -> dict:
    """One submitted transaction → a staged row. Never raises; anything that
    can't be read is recorded as an issue so the user fixes it in the UI."""
    issues: list[str] = []

    raw_amount = raw.get("amount")
    if raw_amount in (None, "") and raw.get("withdrawal") not in (None, ""):
        raw_amount = raw.get("withdrawal")
    if raw_amount in (None, "") and raw.get("deposit") not in (None, ""):
        raw_amount = raw.get("deposit")
    amount = _as_amount(raw_amount)
    direction = _direction_of(raw, _sign_of(raw_amount))

    if amount is None:
        issues.append("amount")
        amount = 0.0
    elif amount <= 0:
        issues.append("amount")
    if direction not in DIRECTIONS:
        direction = "debit"
        issues.append("direction")

    iso = _as_date(raw.get("date") or raw.get("txn_date") or raw.get("value_date")
                   or raw.get("posted_at") or raw.get("on_date"))
    if not iso:
        issues.append("date")

    narration = _clean_text(raw.get("narration") or raw.get("description") or
                            raw.get("particulars") or raw.get("details") or raw.get("remarks"))
    name = _clean_text(raw.get("name") or raw.get("merchant") or raw.get("label")
                       or raw.get("payee") or narration.split("/")[0], 80)
    if not name:
        name = "Transaction"
        issues.append("name")

    # The category is ALWAYS Claude's fresh classification for THIS transaction.
    # We deliberately do NOT carry a category forward from how the merchant was filed
    # before: a single wrong edit used to propagate to every future transaction from
    # the same merchant. Claude re-decides each row from the defined category list, so
    # a mistake stays local — fix that one row and nothing else changes.
    category = _clean_text(raw.get("category"), 60)
    category_source = "claude" if category else "none"
    suggested = ""
    known = known_income_cats if direction == "credit" else known_expense_cats
    category_known = bool(category) and category.lower() in known
    if not category:
        issues.append("category")

    conf = raw.get("confidence")
    try:
        confidence = None if conf is None else max(0.0, min(1.0, float(conf)))
    except (TypeError, ValueError):
        confidence = None

    # whose is it? — canonicalise what Claude sent, then infer if it told us nothing
    claude_owner = _clean_text(raw.get("owner") or raw.get("person") or "", 40)
    try:
        from ..people.store import canon_owner, CANON_NAMES
        claude_owner = canon_owner(claude_owner) if claude_owner else ""
        if claude_owner not in CANON_NAMES:          # "—", "wife", a typo → not one of the four
            claude_owner = ""
    except Exception:
        pass
    m = matcher or OwnerMatcher(default_owner=default_owner)
    owner, owner_source = m.match(claude_owner=claude_owner, name=name,
                                  narration=narration, category=category)

    return {
        "id": _new_id(),
        "date": iso,
        "amount": amount,
        "direction": direction,
        "name": name,
        "narration": narration,
        "category": category or "",
        "category_known": category_known,
        "category_source": category_source,      # rule | memory | claude | none | user
        "category_suggested": suggested,         # what Claude said, when memory overrode it
        "owner": owner,
        "owner_source": owner_source,          # how we decided — shown in the UI
        "owner_guess": owner_source not in _STRONG_SOURCES,
        "confidence": confidence,
        "note": _clean_text(raw.get("note") or raw.get("reason"), 200),
        "balance": _as_amount(raw.get("balance")),
        "ref": make_ref(iso, amount, narration or name),
        "status": "pending",                 # pending | approved | rejected
        "duplicate": False,                  # looks already-logged
        "duplicate_of": None,
        "issues": issues,                    # blocks approval until cleared
        "linked_id": None,                   # ledger row created on approve
        "edited": False,
    }


def _needs_review(row: dict) -> bool:
    return bool(row.get("issues")) or not row.get("category") or row.get("duplicate")


# ── duplicate detection against what's already in the ledger ──────────────────
def _parallel(tasks: dict) -> dict:
    """Run independent store/KV reads at the same time. Every one is a ~200 ms
    Supabase round trip, so doing six in sequence is most of a statement import's
    wall clock. A task that blows up yields None rather than sinking the import."""
    from concurrent.futures import ThreadPoolExecutor

    def _run(fn):
        try:
            return fn()
        except Exception:
            return None

    if not tasks:
        return {}
    with ThreadPoolExecutor(max_workers=min(8, len(tasks))) as pool:
        futures = {key: pool.submit(_run, fn) for key, fn in tasks.items()}
        return {key: f.result() for key, f in futures.items()}


def _index_ledger(expenses: list, income: list, matcher: Optional[OwnerMatcher] = None) -> dict:
    """{(date, amount, direction): name} for de-dup, and — in the same pass —
    teach `matcher` who each merchant/category has belonged to."""
    seen: dict[tuple, str] = {}
    for e in expenses or []:
        if e.get("is_template"):
            continue
        d = str(e.get("on_date") or "")[:10]
        amt = round(float(e.get("amount") or 0), 2)
        if d and amt:
            seen[(d, amt, "debit")] = e.get("name") or "expense"
        if matcher:
            matcher.learn(e.get("name") or "", e.get("category") or "", e.get("owner") or "")
    for e in income or []:
        if e.get("is_template"):
            continue
        d = str(e.get("on_date") or "")[:10]
        amt = round(float(e.get("amount") or 0), 2)
        if d and amt:
            seen[(d, amt, "credit")] = e.get("source") or "income"
        if matcher:
            matcher.learn(e.get("source") or "", e.get("category") or "", e.get("owner") or "")
    return seen


def _ledger_signatures(matcher: Optional[OwnerMatcher] = None) -> tuple[set, dict]:
    """(refs already imported, {(date, amount, direction): existing-name}) from the
    real expense + income logs — so a re-sent statement never double-counts. The
    same single pass teaches `matcher` who each merchant/category belongs to."""
    seen: dict[tuple, str] = {}
    try:
        from . import store as exp_store
        for e in exp_store.list_expenses():
            if e.get("is_template"):
                continue
            d = str(e.get("on_date") or "")[:10]
            amt = round(float(e.get("amount") or 0), 2)
            if d and amt:
                seen[(d, amt, "debit")] = e.get("name") or "expense"
            if matcher:
                matcher.learn(e.get("name") or "", e.get("category") or "", e.get("owner") or "")
    except Exception:
        pass
    try:
        from ..other_income import store as inc_store
        for e in inc_store.list_income():
            if e.get("is_template"):
                continue
            d = str(e.get("on_date") or "")[:10]
            amt = round(float(e.get("amount") or 0), 2)
            if d and amt:
                seen[(d, amt, "credit")] = e.get("source") or "income"
            if matcher:
                matcher.learn(e.get("source") or "", e.get("category") or "", e.get("owner") or "")
    except Exception:
        pass
    return imported_refs(), seen


def _pending_batch_refs() -> set:
    """Signatures still sitting unreviewed in other batches, so the same statement
    sent twice doesn't queue twice. Capped — each batch is a separate KV read."""
    pending = [x for x in list_batches() if x.get("pending")][:4]
    if not pending:
        return set()
    got = _parallel({b["id"]: (lambda bid=b["id"]: get_batch(bid)) for b in pending})
    out: set = set()
    for batch in got.values():
        for r in (batch or {}).get("rows", []):
            if r.get("status") == "pending":
                out.add(r.get("ref"))
    return out


def _mark_duplicates(rows: list[dict], ledger_data: Optional[tuple] = None) -> None:
    """Flag rows that are already in the ledger, already imported once, or that
    repeat inside this very batch. Pass `ledger_data` to reuse an earlier scan."""
    if ledger_data:
        refs, ledger, pending_refs = ledger_data
    else:
        refs, ledger = _ledger_signatures()
        pending_refs = _pending_batch_refs()
    within: set = set()
    for r in rows:
        key = (r["date"], r["amount"], r["direction"])
        if r["ref"] in refs:
            r["duplicate"], r["duplicate_of"] = True, "already imported"
        elif key in ledger:
            r["duplicate"], r["duplicate_of"] = True, f"already logged — {ledger[key]}"
        elif r["ref"] in pending_refs:
            r["duplicate"], r["duplicate_of"] = True, "already waiting in another batch"
        elif r["ref"] in within:
            r["duplicate"], r["duplicate_of"] = True, "repeated in this statement"
        within.add(r["ref"])


# ── batch persistence ─────────────────────────────────────────────────────────
def _read_index() -> list[dict]:
    try:
        rec = _kv().cache_get(_INDEX_KEY)
        val = (rec or {}).get("value")
        if isinstance(val, dict):
            return [b for b in (val.get("batches") or []) if isinstance(b, dict)]
    except Exception:
        pass
    return []


def _write_index(batches: list[dict]) -> None:
    try:
        _kv().cache_set(_INDEX_KEY, {"batches": batches[-_INDEX_KEEP:]})
    except Exception:
        pass


def _summary_of(batch: dict) -> dict:
    rows = batch.get("rows") or []
    pending = [r for r in rows if r.get("status") == "pending"]
    approved = [r for r in rows if r.get("status") == "approved"]
    debit = sum(r["amount"] for r in rows if r["direction"] == "debit")
    credit = sum(r["amount"] for r in rows if r["direction"] == "credit")
    # what's still on the table — the figures the review screen decides on
    p_debit = sum(r["amount"] for r in pending if r["direction"] == "debit")
    p_credit = sum(r["amount"] for r in pending if r["direction"] == "credit")
    dates = sorted(r["date"] for r in rows if r.get("date"))
    return {
        "id": batch["id"],
        "source": batch.get("source"),
        "account": batch.get("account"),
        "owner": batch.get("owner"),
        "note": batch.get("note"),
        "created_at": batch.get("created_at"),
        "updated_at": batch.get("updated_at"),
        "count": len(rows),
        "pending": len(pending),
        "approved": len(approved),
        "rejected": len(rows) - len(pending) - len(approved),
        "needs_review": sum(1 for r in pending if _needs_review(r)),
        "duplicates": sum(1 for r in rows if r.get("duplicate")),
        "owner_guesses": sum(1 for r in pending if r.get("owner_guess") or not r.get("owner")),
        "spend_inr": round(p_debit, 2),                 # pending only — what approving would add
        "income_inr": round(p_credit, 2),
        "net_inr": round(p_credit - p_debit, 2),
        "statement_spend_inr": round(debit, 2),          # the whole statement, incl. done rows
        "statement_income_inr": round(credit, 2),
        "date_from": dates[0] if dates else None,
        "date_to": dates[-1] if dates else None,
        "status": "pending" if pending else "done",
    }


def _persist(batch: dict, index: Optional[list] = None) -> dict:
    """Write the batch blob and the index — concurrently; they're independent."""
    import time
    batch["updated_at"] = _now()
    _batch_cache[batch["id"]] = (batch, time.time())
    summ = _summary_of(batch)
    idx = [b for b in (index if index is not None else _read_index()) if b.get("id") != batch["id"]]
    idx.append(summ)
    idx.sort(key=lambda b: b.get("created_at") or "")
    _parallel({
        "blob": lambda: _kv().cache_set(_BATCH_KEY + batch["id"], {"batch": batch}),
        "index": lambda: _write_index(idx),
    })
    return summ


def get_batch(bid: str) -> Optional[dict]:
    """The staged batch. Held in process briefly — a review session reads the same
    blob on every keystroke-debounced edit, and it can be a few hundred KB."""
    import time
    hit = _batch_cache.get(bid)
    if hit and _fresh(hit[1]):
        return hit[0]
    try:
        rec = _kv().cache_get(_BATCH_KEY + bid)
        val = (rec or {}).get("value")
        if isinstance(val, dict) and isinstance(val.get("batch"), dict):
            _batch_cache[bid] = (val["batch"], time.time())
            return val["batch"]
    except Exception:
        pass
    return None


def batch_with_totals(bid_or_batch) -> Optional[dict]:
    """The batch's rows PLUS its live totals/counts — what the review screen reads.
    The stored blob only holds the rows; every count is derived here so it can
    never drift from the rows themselves."""
    batch = bid_or_batch if isinstance(bid_or_batch, dict) else get_batch(bid_or_batch)
    if not batch:
        return None
    return {**batch, **_summary_of(batch)}


def list_batches(full: bool = False) -> list[dict]:
    """Newest first. `full` re-reads each blob (used sparingly)."""
    idx = sorted(_read_index(), key=lambda b: b.get("created_at") or "", reverse=True)
    if not full:
        return idx
    out = []
    for b in idx:
        batch = get_batch(b["id"])
        out.append(batch or b)
    return out


def create_batch(transactions: list, *, source: str = "Claude", account: Optional[str] = None,
                 owner: Optional[str] = None, note: Optional[str] = None,
                 currency: str = "INR") -> dict:
    """Stage a set of transactions for review. Returns the batch summary plus a
    per-row rejection report so the caller knows exactly what landed."""
    from . import store as exp_store

    # The ledger comes from the short-lived cache; the rest fire together.
    got = _parallel({
        "ledger": _ledger_rows,
        "refs": imported_refs,
        "categories": exp_store.all_categories,
        "pending_refs": _pending_batch_refs,
    })
    expenses, income, cash = got.get("ledger") or ([], [], [])

    known_exp = {c.lower() for c in (got.get("categories") or exp_store.CATEGORIES)}
    known_inc = {c.lower() for c in income_categories()}

    # ONE ledger pass: it both trains the owner matcher (who each merchant and
    # category has belonged to) and yields the de-dup signatures.
    matcher = OwnerMatcher(account=account, default_owner=owner, cash_items=cash)
    ledger_data = (got.get("refs") or set(),
                   _index_ledger(expenses, income, matcher),
                   got.get("pending_refs") or set())

    rows: list[dict] = []
    skipped: list[dict] = []
    for i, raw in enumerate(transactions or []):
        if len(rows) >= MAX_ROWS_PER_BATCH:
            skipped.append({"index": i, "why": "batch row limit reached"})
            continue
        if not isinstance(raw, dict):
            skipped.append({"index": i, "why": "not an object"})
            continue
        rows.append(normalise_row(raw, default_owner=owner, matcher=matcher,
                                  known_expense_cats=known_exp, known_income_cats=known_inc))
    _mark_duplicates(rows, ledger_data)
    # newest transaction first — that's how a statement is usually reviewed
    rows.sort(key=lambda r: (r.get("date") or "", r.get("amount") or 0), reverse=True)

    batch = {
        "id": _new_id(8),
        "source": _clean_text(source, 60) or "Claude",
        "account": _clean_text(account, 80),
        "owner": _clean_text(owner, 40),
        "note": _clean_text(note, 300),
        "currency": (currency or "INR").upper(),
        "created_at": _now(),
        "rows": rows,
    }
    summ = _persist(batch)
    summ["skipped"] = skipped
    summ["unknown_categories"] = sorted({r["category"] for r in rows
                                         if r["category"] and not r["category_known"]})
    # categories are no longer auto-filled from memory — always Claude's own call
    summ["auto_filled_from_memory"] = 0
    return summ


# ── the fast wire format ──────────────────────────────────────────────────────
# Writing a JSON object per transaction is what makes a big statement slow: the
# model spends thousands of output tokens repeating the same keys. One compact
# line per row costs ~60% fewer tokens, and every field the server can work out
# for itself (merchant name, category, owner) is simply left off.
#
#   2026-07-24|285|D|UPI-ZEPTO-ZEPTOONLINE@YBL-...         ← let the app categorise
#   2026-07-24|285|D|UPI-ZEPTO-...|Groceries               ← or say so explicitly
_SEPARATORS = ("|", "\t", ";", "~")


def parse_compact_lines(text: str) -> tuple[list[dict], list[dict]]:
    """`date|amount|D or C|narration[|category]` per line → (rows, skipped).

    Deliberately forgiving: any of | tab ; ~ separates, the direction accepts
    D/C/DR/CR/debit/credit/-/+, and a header line or blank line is ignored."""
    from . import statement as stmt

    rows: list[dict] = []
    skipped: list[dict] = []
    for n, raw in enumerate((text or "").splitlines(), start=1):
        line = raw.strip().lstrip("\ufeff")
        if not line:
            continue
        sep = next((c for c in _SEPARATORS if c in line), None)
        if not sep:
            skipped.append({"line": n, "text": line[:80], "why": "no | separator"})
            continue
        parts = [c.strip() for c in line.split(sep)]
        if len(parts) < 3:
            skipped.append({"line": n, "text": line[:80], "why": "needs date|amount|direction|narration"})
            continue
        if _as_date(parts[0]) is None:                     # a header row, or prose
            skipped.append({"line": n, "text": line[:80], "why": "first field isn't a date"})
            continue
        d = (parts[2] or "").strip().lower()
        direction = ("credit" if d in ("c", "cr", "credit", "+", "deposit", "in")
                     else "debit" if d in ("d", "dr", "debit", "-", "withdrawal", "out")
                     else None)
        narration = parts[3] if len(parts) > 3 else ""
        category = parts[4] if len(parts) > 4 else ""
        if direction is None:                              # fall back to the sign
            direction = "credit" if _sign_of(parts[1]) > 0 and d in ("", "+") else "debit"
        rows.append({
            "date": parts[0], "amount": parts[1], "direction": direction,
            "narration": narration,
            # the server derives these — the model never has to write them
            "name": stmt._merchant(narration),
            "category": category or stmt.guess_category(narration, direction),
        })
    return rows, skipped


def income_categories() -> list[str]:
    """The income vocabulary — the other-income tab's categories plus the ones the
    statement parser can produce, deduped, order preserved."""
    out: list[str] = []
    seen: set = set()
    try:
        from ..other_income import store as inc_store
        for c in inc_store.CATEGORIES:
            if c.lower() not in seen:
                out.append(c); seen.add(c.lower())
    except Exception:
        pass
    try:
        from . import statement as stmt
        for c in stmt.INCOME_CATEGORIES:
            if c.lower() not in seen:
                out.append(c); seen.add(c.lower())
    except Exception:
        pass
    return out


# ── edit / approve / discard ──────────────────────────────────────────────────
_EDITABLE = ("date", "amount", "direction", "name", "category", "owner", "note", "status")


def patch_rows(bid: str, patches: list[dict]) -> Optional[dict]:
    """Apply user edits. Re-validates every touched field and recomputes issues."""
    batch = get_batch(bid)
    if not batch:
        return None
    from . import store as exp_store
    known_exp = {c.lower() for c in exp_store.all_categories()}
    known_inc = {c.lower() for c in income_categories()}
    by_id = {r["id"]: r for r in batch["rows"]}
    owner_rules: list[dict] = []

    for p in patches or []:
        row = by_id.get(str(p.get("id") or ""))
        if not row or row.get("status") == "approved":
            continue
        issues = [i for i in row.get("issues", [])]
        for key in _EDITABLE:
            if key not in p:
                continue
            val = p[key]
            if key == "amount":
                amt = _as_amount(val)
                if amt is not None and amt > 0:
                    row["amount"] = amt
                    issues = [i for i in issues if i != "amount"]
            elif key == "date":
                iso = _as_date(val)
                if iso:
                    row["date"] = iso
                    issues = [i for i in issues if i != "date"]
            elif key == "direction":
                if str(val).lower() in DIRECTIONS:
                    row["direction"] = str(val).lower()
                    issues = [i for i in issues if i != "direction"]
            elif key == "status":
                if str(val) in ("pending", "rejected"):
                    row["status"] = str(val)
            elif key == "category":
                # Correcting a category changes ONLY this row. It is deliberately not
                # remembered and not applied to other rows — a wrong fix used to
                # propagate to every future transaction from the same merchant, which
                # is exactly the behaviour we're removing.
                row["category"] = _clean_text(val, 60)
                row["category_source"] = "user"
                row["category_suggested"] = ""
                if row["category"]:
                    issues = [i for i in issues if i != "category"]
            elif key == "name":
                row["name"] = _clean_text(val, 80) or row["name"]
                if row["name"]:
                    issues = [i for i in issues if i != "name"]
            elif key == "owner":
                row["owner"] = _clean_text(val, 40)
                row["owner_source"] = "user"          # you said so — that's the strongest signal
                row["owner_guess"] = False
                if row["owner"]:
                    owner_rules.append({"merchant": row.get("name") or "", "owner": row["owner"]})
            else:
                row[key] = _clean_text(val, 200)
        known = known_inc if row["direction"] == "credit" else known_exp
        row["category_known"] = bool(row["category"]) and row["category"].lower() in known
        row["issues"] = issues
        row["edited"] = True
        # an explicit edit is the user's call — stop nagging about the duplicate
        if "duplicate" in p:
            row["duplicate"] = bool(p["duplicate"])
    # A category edit no longer touches any other row, so nothing is "also updated".
    batch["_applied"] = 0
    # only owner corrections are still remembered (who-pays doesn't change per txn);
    # categories are left to Claude to re-decide fresh every time.
    _parallel({
        "rules": (lambda: set_merchant_rules(owner_rules)) if owner_rules else (lambda: None),
        "batch": lambda: _persist(batch),
    })
    return batch


def approve(bid: str, row_ids: Optional[list[str]] = None) -> Optional[dict]:
    """Turn the chosen staged rows into real ledger entries.
      debit  → a one-time expense (api.expenses.store)
      credit → an other-income log entry (api.other_income.store)
    Rows with unresolved issues are skipped and reported, never guessed at."""
    batch = get_batch(bid)
    if not batch:
        return None
    from . import store as exp_store
    from ..other_income import store as inc_store

    wanted = set(row_ids) if row_ids else None
    skipped: list[dict] = []
    refs: list[str] = []
    account = batch.get("account") or ""
    stamp = _now()
    expense_rows: list[tuple] = []          # (staged row, ledger payload)
    income_rows: list[tuple] = []

    for row in batch["rows"]:
        if row.get("status") != "pending":
            continue
        if wanted is not None and row["id"] not in wanted:
            continue
        # "approve everything pending" must never quietly double-count: a row we
        # flagged as already-logged only goes through when it's named explicitly.
        if wanted is None and row.get("duplicate"):
            skipped.append({"id": row["id"], "name": row["name"],
                            "why": f"looks like a duplicate ({row.get('duplicate_of')}) — approve it individually if it's real"})
            continue
        if row.get("issues"):
            skipped.append({"id": row["id"], "name": row["name"],
                            "why": "fix " + ", ".join(row["issues"])})
            continue
        if not row.get("date") or float(row.get("amount") or 0) <= 0:
            skipped.append({"id": row["id"], "name": row["name"], "why": "needs a date and an amount"})
            continue
        note = (row.get("note") or row.get("narration") or "").strip() or None
        cur = batch.get("currency") or "INR"
        if row["direction"] == "credit":
            income_rows.append((row, {
                "owner": row.get("owner") or None,
                "source": row["name"],
                "category": row.get("category") or "Other income",
                "amount": float(row["amount"]), "currency": cur,
                "frequency": "one_time", "active": True, "is_template": False,
                "account": account or None, "on_date": row["date"], "note": note,
            }))
        else:
            expense_rows.append((row, {
                "owner": row.get("owner") or None,
                "name": row["name"],
                "category": row.get("category") or "Miscellaneous",
                "amount": float(row["amount"]), "currency": cur,
                "frequency": "one_time", "payment_method": account or "bank",
                "is_subscription": False, "essential": True, "active": True,
                "is_template": False, "on_date": row["date"], "end_date": None, "note": note,
            }))

    # ONE round trip per ledger instead of one per row — a 100-row statement went
    # from ~40s of sequential inserts to well under a second.
    def _commit(pairs: list, writer) -> tuple[int, float]:
        if not pairs:
            return 0, 0.0
        try:
            made = writer([p for _, p in pairs])
        except Exception as e:
            for row, _ in pairs:
                skipped.append({"id": row["id"], "name": row["name"], "why": f"could not save — {e}"})
            return 0, 0.0
        # the store echoes back the ids it generated, in the order we sent them
        n = total = 0.0
        for (row, _), rec in zip(pairs, made):
            row["status"] = "approved"
            row["linked_id"] = (rec or {}).get("id")
            row["approved_at"] = stamp
            refs.append(row["ref"])
            n += 1
            total += float(row["amount"])
        for row, _ in pairs[len(made):]:                 # anything the store dropped
            skipped.append({"id": row["id"], "name": row["name"], "why": "the store rejected this row"})
        return int(n), total

    created_exp, total_exp = _commit(expense_rows, exp_store.create_expenses)
    created_inc, total_inc = _commit(income_rows, inc_store.create_incomes)

    add_imported_refs(refs)
    invalidate_cache(rules=False)          # the ledger just grew
    summ = _persist(batch)
    summ.update({
        "expenses_created": created_exp, "income_created": created_inc,
        "expenses_total_inr": round(total_exp, 2), "income_total_inr": round(total_inc, 2),
        "skipped": skipped,
    })
    return summ


def reject(bid: str, row_ids: list[str]) -> Optional[dict]:
    batch = get_batch(bid)
    if not batch:
        return None
    wanted = set(row_ids or [])
    for row in batch["rows"]:
        if row["id"] in wanted and row.get("status") == "pending":
            row["status"] = "rejected"
    return _persist(batch)


def restore(bid: str, row_ids: list[str]) -> Optional[dict]:
    batch = get_batch(bid)
    if not batch:
        return None
    wanted = set(row_ids or [])
    for row in batch["rows"]:
        if row["id"] in wanted and row.get("status") == "rejected":
            row["status"] = "pending"
    return _persist(batch)


def delete_batch(bid: str) -> bool:
    idx = _read_index()
    kept = [b for b in idx if b.get("id") != bid]
    if len(kept) == len(idx) and not get_batch(bid):
        return False
    _write_index(kept)
    _batch_cache.pop(bid, None)
    try:
        _kv().cache_set(_BATCH_KEY + bid, {"batch": None, "deleted_at": _now()})
    except Exception:
        pass
    return True


def delete_batches(ids: list[str]) -> dict:
    """Remove several pushes at once. Anything already approved stays in the
    ledger — this only throws away the staging record."""
    removed, missing = [], []
    for bid in ids or []:
        (removed if delete_batch(bid) else missing).append(bid)
    return {"removed": removed, "missing": missing, "count": len(removed)}


def clear_inbox(only_pending: bool = True) -> dict:
    """Throw away every push (or only the ones still awaiting review)."""
    ids = [b["id"] for b in list_batches()
           if not only_pending or b.get("status") == "pending"]
    return delete_batches(ids)


def inbox_status() -> dict:
    """Small payload for the tab's badge + for Claude to read back after a push."""
    batches = list_batches()
    pending = sum(b.get("pending", 0) for b in batches)
    return {
        "batches": len(batches),
        "pending_rows": pending,
        "needs_review": sum(b.get("needs_review", 0) for b in batches),
        "waiting": [b for b in batches if b.get("status") == "pending"],
        "durable": bool(_kv().cache_durable()),
    }
