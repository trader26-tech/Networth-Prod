"""
LIC life-insurance store — file-backed local database (JSON), seeded once from
the family's real LIC policy register (25 policies for Ram Prasad & Mahalakshmi,
imported from the household spreadsheet's "LIC" tab).

Stores the *raw facts* of each policy: whose life is covered, the plan, premium
(amount + frequency), the life cover (sum assured), the guaranteed maturity value
and accrued bonus, and — for the unit-linked "Money Plus" policies — the fund
units / NAV / value. Everything derived (status, life cover in force, yearly
outgo, expected vs received, next maturity) is computed on read in engine.py.

The store is a plain JSON file (api/lic_data/policies.json) so the page works
immediately with correct data and needs no Supabase migration. Same CRUD shape as
api/ulip/store.py, so it can be swapped to Supabase later without touching routes.
"""
from __future__ import annotations

import os
import json
import uuid
from datetime import datetime
from typing import Any, Optional

# beginner-friendly categories
PLAN_TYPES = ("endowment", "money_back", "ulip", "health", "term", "whole_life")
FREQUENCIES = ("monthly", "quarterly", "half_yearly", "yearly", "single")
STATUSES = ("active", "matured", "surrendered", "lapsed")

LIC_COLUMNS = {
    "id", "holder", "policy_number", "plan", "plan_type", "term_years",
    "premium_paying_years", "premium", "premium_frequency", "premium_annual",
    "paid_by", "start_date", "maturity_date", "sum_assured", "maturity_amount",
    "bonus", "total_maturity", "status", "fund_units", "fund_nav", "fund_value",
    "invested", "remarks", "note", "created_at", "updated_at",
    # life-insurance essentials: lifelong cover + accident rider, and — most
    # important in an emergency — who receives the money and whom to call.
    "whole_life", "accident_benefit",
    "nominee", "nominee_relation", "nominee_phone",
    "agent_name", "agent_phone", "branch",
}

MIGRATION_HINT = ""   # JSON store is always ready — kept for route symmetry

# ── storage paths ─────────────────────────────────────────────────────────────
_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "lic_data")
_POLICIES_FILE = os.path.join(_DATA_DIR, "policies.json")
_SEED_MARKER = os.path.join(_DATA_DIR, ".seeded")


# ── the real policy register (from the "LIC" spreadsheet tab) ──────────────────
# premium is per-installment; premium_annual is the yearly outgo. Quarterly plans
# → premium × 4. Amounts in ₹. Money-back / survival benefits are noted in remarks.
def _seed_rows() -> list[dict]:
    P = []

    def add(**kw):
        P.append(kw)

    # ── Ram Prasad — traditional endowment / money-back ──────────────────────
    add(policy_number="711968844", holder="Ram Prasad", plan="Plan 88",
        plan_type="endowment", term_years=20, premium=267, premium_frequency="quarterly",
        premium_annual=1068, paid_by="Ram", start_date="1994-03-28", maturity_date="2014-03-28",
        sum_assured=None, maturity_amount=None, bonus=None, total_maturity=None,
        status="matured", remarks="Received — matured at age 42.")

    add(policy_number="712559890", holder="Ram Prasad", plan="Plan 93 (Money-Back)",
        plan_type="money_back", term_years=25, premium=2835, premium_frequency="quarterly",
        premium_annual=11340, paid_by="Ram", start_date="1997-09-28", maturity_date="2022-03-28",
        sum_assured=200000, maturity_amount=80000, bonus=181200, total_maturity=261200,
        status="matured",
        remarks="Money-back: ₹30,000 survival benefit paid out at ages 30, 35, 40 & 45; final maturity received at age 50.")

    add(policy_number="712770411", holder="Ram Prasad", plan="Plan 14 (Endowment)",
        plan_type="endowment", term_years=25, premium=1052, premium_frequency="quarterly",
        premium_annual=4208, paid_by="Ram", start_date="1998-01-07", maturity_date="2023-01-07",
        sum_assured=100000, maturity_amount=100000, bonus=98400, total_maturity=198400,
        status="matured", remarks="Received — matured at age 51.")

    add(policy_number="713629084", holder="Ram Prasad", plan="Plan 90",
        plan_type="endowment", term_years=21, premium=8986, premium_frequency="yearly",
        premium_annual=8986, paid_by="Ram", start_date="2004-01-09", maturity_date="2025-01-09",
        sum_assured=200000, maturity_amount=400000, bonus=108400, total_maturity=508400,
        status="matured", remarks="Received — matured at age 53.")

    # ── Mahalakshmi — traditional ────────────────────────────────────────────
    add(policy_number="713620770", holder="Mahalakshmi", plan="Plan 48",
        plan_type="endowment", term_years=25, premium_paying_years=15, premium=14919,
        premium_frequency="yearly", premium_annual=14919, paid_by="Ram", start_date="2003-05-08",
        maturity_date="2028-05-08", sum_assured=300000, maturity_amount=500000, bonus=163500,
        total_maturity=663500, status="active",
        remarks="Premiums payable for 15 years; matures at age 56.")

    add(policy_number="715573990", holder="Mahalakshmi", plan="Plan 14 (Endowment)",
        plan_type="endowment", term_years=20, premium=1469, premium_frequency="yearly",
        premium_annual=1469, paid_by="Maha (mother)", start_date="1996-06-28", maturity_date="2016-06-28",
        sum_assured=30000, maturity_amount=30000, bonus=2240, total_maturity=32240,
        status="matured", remarks="Received — matured 2016.")

    # ── Health cover ─────────────────────────────────────────────────────────
    add(policy_number="715167710", holder="Ram Prasad", plan="Health Plus",
        plan_type="health", term_years=20, premium=18500, premium_frequency="yearly",
        premium_annual=18500, paid_by="Ram", start_date="2008-06-02", maturity_date="2037-06-02",
        sum_assured=None, maturity_amount=None, bonus=97273, total_maturity=None,
        status="active", remarks="Health cover plan; accumulated health-account value ≈ ₹97,273.")

    # ── Unit-linked "Money Plus" (surrendered / pre-matured) ─────────────────
    add(policy_number="715161116", holder="Mahalakshmi", plan="Money Plus (ULIP)",
        plan_type="ulip", term_years=20, premium=10000, premium_frequency="yearly",
        premium_annual=10000, paid_by="Maha (mother)", start_date="2007-08-10", maturity_date="2027-11-17",
        sum_assured=100000, status="surrendered", fund_units=2488.13, fund_nav=11.351,
        fund_value=28242.76, invested=20000, remarks="Pre-matured (surrendered). Growth fund.")

    add(policy_number="714897696", holder="Mahalakshmi", plan="Money Plus (ULIP)",
        plan_type="ulip", term_years=20, premium=10000, premium_frequency="yearly",
        premium_annual=10000, paid_by="Maha (mother)", start_date="2007-08-10", maturity_date="2027-11-17",
        sum_assured=100000, status="surrendered", fund_units=2488.13, fund_nav=11.351,
        fund_value=28242.76, invested=20000, remarks="Pre-matured (surrendered). Growth fund.")

    add(policy_number="714902427", holder="Ram Prasad", plan="Money Plus (ULIP)",
        plan_type="ulip", term_years=20, premium=10000, premium_frequency="yearly",
        premium_annual=10000, paid_by="Ram", start_date="2007-03-30", maturity_date="2027-03-30",
        sum_assured=100000, status="surrendered", fund_units=2714.133, fund_nav=11.351,
        fund_value=30808.12, invested=30000, remarks="Pre-matured (surrendered). Growth fund.")

    add(policy_number="714902429", holder="Ram Prasad", plan="Money Plus (ULIP)",
        plan_type="ulip", term_years=20, premium=10000, premium_frequency="yearly",
        premium_annual=10000, paid_by="Ram", start_date="2007-03-30", maturity_date="2027-03-30",
        sum_assured=100000, status="surrendered", fund_units=2712.512, fund_nav=11.351,
        fund_value=30789.72, invested=30000, remarks="Pre-matured (surrendered). Growth fund.")

    # ── Plan 149 (Jeevan Anand) staggered ladder — Ram then Maha ─────────────
    # all start 2008-06-08, maturities staggered 2028→2034, sum assured ₹1,00,000 each
    ram_149 = [
        ("715167664", 20, 5807, "2028-06-08", 25600, 125600, 56),
        ("715167665", 21, 5487, "2029-06-08", 28000, 128000, 57),
        ("715167666", 22, 5311, "2030-06-08", 28000, 128000, 58),
        ("715167667", 23, 5044, "2031-06-08", 28000, 128000, 59),
        ("715167668", 24, 4802, "2032-06-08", 28000, 128000, 60),
        ("715167669", 25, 4578, "2033-06-08", 28000, 128000, 61),
        ("715167670", 26, 4375, "2034-06-08", 28000, 128000, 62),
    ]
    maha_149 = [
        ("715167671", 20, 5545, "2028-06-08", 25600, 125600, 56),
        ("715167672", 21, 5049, "2029-06-08", 28000, 128000, 57),
        ("715167673", 22, 5230, "2030-06-08", 28000, 128000, 58),
        ("715167674", 23, 4802, "2031-06-08", 28000, 128000, 59),
        ("715167675", 24, 4559, "2032-06-08", 28000, 128000, 60),
        ("715167676", 25, 4346, "2033-06-08", 28000, 128000, 61),
        ("715167677", 26, 4142, "2034-06-08", 28000, 128000, 62),
    ]
    # Jeevan Anand = endowment + whole-life: after the endowment term pays out, the
    # ₹1L cover *continues for life* at no further premium, plus an equal accident
    # benefit up to age 70 (per the Table-149 brochure). premium_paying_years = term.
    for who, rows in (("Ram Prasad", ram_149), ("Mahalakshmi", maha_149)):
        for pol, term, prem, mat, bonus, total, age in rows:
            add(policy_number=pol, holder=who, plan="Plan 149 (Jeevan Anand)",
                plan_type="endowment", term_years=term, premium_paying_years=term,
                premium=prem, premium_frequency="yearly",
                premium_annual=prem, paid_by="Ram", start_date="2008-06-08", maturity_date=mat,
                sum_assured=100000, maturity_amount=100000, bonus=bonus, total_maturity=total,
                whole_life=True, accident_benefit=100000,
                status="active",
                remarks=f"Endowment + whole-life ladder — pays ₹{total:,} at age {age}, "
                        f"then ₹1,00,000 life cover continues for life.")

    # stamp ids + timestamps
    now = datetime.now().isoformat()
    seeded = []
    for i, row in enumerate(P):
        r = {k: row.get(k) for k in LIC_COLUMNS}
        r["id"] = f"lic{row['policy_number']}"
        r["created_at"] = now
        r["updated_at"] = now
        r["_order"] = i          # keep register order stable in the UI
        seeded.append(r)
    return seeded


# ── file helpers ──────────────────────────────────────────────────────────────
def _read_json(path: str) -> list:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _write_json(path: str, data: list) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)
    os.replace(tmp, path)


def _now() -> str:
    return datetime.now().isoformat()


def _new_id() -> str:
    return "lic" + uuid.uuid4().hex[:9]


def _as_float(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _ensure_seeded() -> None:
    """Populate the file once, on first ever run, with the real register."""
    if os.path.exists(_SEED_MARKER):
        return
    if not _read_json(_POLICIES_FILE):
        _write_json(_POLICIES_FILE, _seed_rows())
    os.makedirs(_DATA_DIR, exist_ok=True)
    with open(_SEED_MARKER, "w") as f:
        f.write(_now())


def tables_ready() -> bool:
    _ensure_seeded()
    return True


def _clean_payload(data: dict) -> dict:
    out: dict[str, Any] = {}
    for k in ("holder", "policy_number", "plan", "plan_type", "premium_frequency",
              "paid_by", "start_date", "maturity_date", "status", "remarks", "note",
              "nominee", "nominee_relation", "nominee_phone",
              "agent_name", "agent_phone", "branch"):
        if k in data and data[k] is not None:
            out[k] = str(data[k]).strip() or None
    for k in ("term_years", "premium_paying_years", "premium", "premium_annual",
              "sum_assured", "maturity_amount", "bonus", "total_maturity",
              "fund_units", "fund_nav", "fund_value", "invested", "accident_benefit"):
        if k in data:
            out[k] = _as_float(data[k])
    if "whole_life" in data:
        out["whole_life"] = bool(data["whole_life"]) if data["whole_life"] not in (None, "") else None
    if out.get("plan_type") not in (None, *PLAN_TYPES):
        out["plan_type"] = "endowment"
    if out.get("premium_frequency") not in (None, *FREQUENCIES):
        out["premium_frequency"] = "yearly"
    if out.get("status") not in (None, *STATUSES):
        out["status"] = "active"
    return out


def _decorate(row: dict) -> dict:
    d = {k: row.get(k) for k in LIC_COLUMNS}
    d["_order"] = row.get("_order", 9999)
    return d


# ── CRUD ──────────────────────────────────────────────────────────────────────
def list_policies() -> list[dict]:
    _ensure_seeded()
    rows = _read_json(_POLICIES_FILE)
    rows.sort(key=lambda r: r.get("_order", 9999))
    return [_decorate(r) for r in rows]


def get_policy(pid: str) -> Optional[dict]:
    _ensure_seeded()
    for r in _read_json(_POLICIES_FILE):
        if r.get("id") == pid:
            return _decorate(r)
    return None


def create_policy(data: dict) -> dict:
    _ensure_seeded()
    payload = _clean_payload(data)
    payload["id"] = _new_id()
    payload["created_at"] = _now()
    payload["updated_at"] = payload["created_at"]
    payload.setdefault("plan", "LIC policy")
    payload.setdefault("plan_type", "endowment")
    payload.setdefault("premium_frequency", "yearly")
    payload.setdefault("status", "active")
    rows = _read_json(_POLICIES_FILE)
    payload["_order"] = max([r.get("_order", 0) for r in rows], default=0) + 1
    rows.append(payload)
    _write_json(_POLICIES_FILE, rows)
    return _decorate(payload)


def update_policy(pid: str, patch: dict) -> Optional[dict]:
    _ensure_seeded()
    updates = _clean_payload(patch)
    if not updates:
        return get_policy(pid)
    updates["updated_at"] = _now()
    rows = _read_json(_POLICIES_FILE)
    found = False
    for r in rows:
        if r.get("id") == pid:
            r.update(updates)
            found = True
            break
    if not found:
        return None
    _write_json(_POLICIES_FILE, rows)
    return get_policy(pid)


def delete_policy(pid: str) -> bool:
    _ensure_seeded()
    rows = _read_json(_POLICIES_FILE)
    remaining = [r for r in rows if r.get("id") != pid]
    if len(remaining) == len(rows):
        return False
    _write_json(_POLICIES_FILE, remaining)
    return True


def holders() -> list[str]:
    seen: dict[str, str] = {}
    for r in list_policies():
        h = (r.get("holder") or "").strip()
        if h:
            seen.setdefault(h.lower(), h)
    return sorted(seen.values(), key=lambda s: s.lower())
