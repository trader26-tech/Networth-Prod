"""
Corporate-action upload history — a record of every Corporate Actions CSV you've
imported, so the Dividends page can show exactly what each file added.

Stored as one KV blob (``app_cache`` via the portfolio store, same pattern as
the F&O tradebooks) → no schema migration. Each record keeps the filename, when
it was uploaded, the action mix (dividends / bonuses / splits / …), the date
range it covered, and the list of dividends that MATCHED your holdings (symbol,
ex-date, ₹/share × shares = amount). Best-effort: any storage failure degrades
to an empty list rather than blocking the import.
"""
from __future__ import annotations

import uuid
from datetime import datetime

_KEY = "corp_action_uploads"
_MAX = 40                    # keep the most recent N uploads


def _kv():
    from ..portfolio import store as kv
    return kv


def list_uploads() -> list[dict]:
    """Newest first."""
    try:
        rec = _kv().cache_get(_KEY)
        items = rec.get("value") if rec and isinstance(rec.get("value"), list) else []
        return sorted(items, key=lambda u: u.get("uploaded_at") or "", reverse=True)
    except Exception:
        return []


def _save(items: list[dict]) -> None:
    try:
        _kv().cache_set(_KEY, items[:_MAX])
    except Exception:
        pass


def add_upload(record: dict) -> dict:
    record = dict(record)
    record.setdefault("id", uuid.uuid4().hex[:12])
    record.setdefault("uploaded_at", datetime.now().isoformat())
    items = [u for u in list_uploads()]
    items.insert(0, record)
    _save(items)
    return record


def delete_upload(upload_id: str) -> bool:
    items = list_uploads()
    keep = [u for u in items if u.get("id") != upload_id]
    if len(keep) == len(items):
        return False
    _save(keep)
    return True
