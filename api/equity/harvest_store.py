"""Persist the LTCG-harvesting P&L a user uploads per broker account + FY.

Small and durable: one app_cache row holds every account's filed years, so the
booked long-term gain and the remaining ₹1.25 L allowance survive redeploys and
are shared across replicas. Nothing here recomputes tax — it just remembers what
the uploaded Tax P&L said, so the Stocks page can show "booked / left to book"
without re-parsing on every load.
"""
from __future__ import annotations

import time
from typing import Any, Optional

_KEY = "equity_ltcg_harvest"          # app_cache row: { records: { "<acc>|<fy>": {...} } }


def _kv():
    from ..portfolio import store as kv
    return kv


def _all() -> dict:
    try:
        rec = _kv().cache_get(_KEY)
    except Exception:
        rec = None
    val = (rec or {}).get("value") if rec else None
    recs = (val or {}).get("records") if isinstance(val, dict) else None
    return recs if isinstance(recs, dict) else {}


def _write(records: dict) -> None:
    try:
        _kv().cache_set(_KEY, {"records": records})
    except Exception:
        pass


def _rid(account_id: str, fy_label: str) -> str:
    return f"{account_id}|{fy_label}"


def get_for_account(account_id: str) -> list[dict]:
    """Every filed year for one account, newest FY first."""
    out = [v for k, v in _all().items()
           if isinstance(v, dict) and v.get("account_id") == account_id]
    return sorted(out, key=lambda r: r.get("fy_label") or "", reverse=True)


def get(account_id: str, fy_label: str) -> Optional[dict]:
    return _all().get(_rid(account_id, fy_label))


def all_records() -> list[dict]:
    return [v for v in _all().values() if isinstance(v, dict)]


def save(account_id: str, harvest: dict, *, file_name: str = "",
         person: Optional[str] = None, account_label: Optional[str] = None) -> dict:
    """Upsert one account+FY harvest record. `harvest` is taxpnl.harvest_view()
    merged with the raw parsed totals. Re-filing the same FY overwrites it."""
    records = _all()
    fy_label = str(harvest.get("fy_label") or "")
    rid = _rid(account_id, fy_label)
    row = {
        "id": rid,
        "account_id": account_id,
        "person": person,
        "account_label": account_label,
        "file_name": file_name,
        "updated_at": _stamp(),
        **harvest,
    }
    records[rid] = row
    _write(records)
    return row


def delete(account_id: str, fy_label: str) -> bool:
    records = _all()
    rid = _rid(account_id, fy_label)
    if rid in records:
        records.pop(rid, None)
        _write(records)
        return True
    return False


def _stamp() -> str:
    # cache_set stamps updated_at on the row too; this is the per-record time
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
