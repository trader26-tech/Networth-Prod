"""
Per-reminder overrides — the user's tweaks on top of the *computed* reminder
feed (bond payouts, loan EMIs, FD interest, income & expenses).

A reminder is derived on read (see aggregate.py), so there is nothing to store
for the reminder itself. What we DO persist is the small delta a user applies:

  • status    — 'done' (received / paid) or 'skipped', vs the default 'pending'
  • due_date  — a moved date ("I'll actually get this on the 12th, not the 10th")

One flat table `reminder_overrides`, keyed by the reminder's stable `key`.
Supabase-primary with a JSON-file fallback for dev, and — importantly — every
read is best-effort: if the table isn't migrated yet, we return an empty map so
the reminders page still renders the full computed feed (only the tweaks are
lost until the one-time migration is run). See SUPABASE.md → "Reminder overrides".
"""
from __future__ import annotations

import os
import json
from datetime import datetime
from typing import Optional

TABLE = "reminder_overrides"

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "reminders_data")
_FILE = os.path.join(_DATA_DIR, "overrides.json")

_client = None
_init_attempted = False


def _read_env(key: str) -> str:
    val = os.environ.get(key, "").strip()
    if val:
        return val
    try:
        from dotenv import dotenv_values
        root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        env_path = os.path.join(root, ".env")
        if os.path.isfile(env_path):
            return (dotenv_values(env_path).get(key) or "").strip()
    except Exception:
        pass
    return ""


def _get_client():
    global _client, _init_attempted
    if _client is not None or _init_attempted:
        return _client
    _init_attempted = True
    url = _read_env("SUPABASE_URL").rstrip("/")
    key = _read_env("SUPABASE_SERVICE_KEY") or _read_env("SUPABASE_KEY")
    if not url or not key:
        return None
    try:
        from supabase import create_client
        _client = create_client(url, key)
        return _client
    except Exception:
        return None


def _read_json() -> dict:
    try:
        with open(_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _write_json(data: dict) -> None:
    os.makedirs(_DATA_DIR, exist_ok=True)
    tmp = _FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)
    os.replace(tmp, _FILE)


def all_overrides() -> dict[str, dict]:
    """{key: {due_date, status}} for every override. Best-effort: if the Supabase
    table isn't migrated yet we fall back to the JSON file, and if that too fails
    we return {} so the feed always renders (only the tweaks are lost)."""
    client = _get_client()
    if client:
        try:
            rows = client.table(TABLE).select("*").execute().data or []
            return {r["key"]: {"due_date": r.get("due_date"), "status": r.get("status")}
                    for r in rows if r.get("key")}
        except Exception:
            return _read_json()      # table missing → dev/JSON fallback
    return _read_json()


def set_override(key: str, due_date: Optional[str] = None, status: Optional[str] = None) -> dict:
    """Upsert one reminder's override. A None field clears that field. When both
    fields end up empty the row is deleted so we don't accumulate no-op rows."""
    key = (key or "").strip()
    if not key:
        return {}
    due_date = (due_date or "").strip() or None
    status = (status or "").strip() or None
    row = {"key": key, "due_date": due_date, "status": status,
           "updated_at": datetime.now().isoformat()}

    client = _get_client()
    if client:
        try:
            if not due_date and not status:
                client.table(TABLE).delete().eq("key", key).execute()
            else:
                client.table(TABLE).upsert(row, on_conflict="key").execute()
            return row
        except Exception:
            pass  # fall through to JSON so a dev without the table still works

    data = _read_json()
    if not due_date and not status:
        data.pop(key, None)
    else:
        data[key] = {"due_date": due_date, "status": status}
    _write_json(data)
    return row


# ── custom (user-added) reminders ────────────────────────────────────────────
# Unlike the computed reminders (bond/FD/loan/income/expense), these are entered
# by the user: "fill bond details on the 12th", tagged with what it's for (Bonds
# / Stocks / F&O / …). Stored as a list in the app-cache KV so no migration is
# needed; status (done/skipped) + a moved date reuse the normal override table.
_CUSTOM_KEY = "reminders_custom"


def _kv():
    from ..portfolio import store as kv
    return kv


def list_custom() -> list:
    try:
        rec = _kv().cache_get(_CUSTOM_KEY)
        if rec and isinstance(rec.get("value"), dict):
            return rec["value"].get("items", []) or []
    except Exception:
        pass
    return []


def _save_custom(items: list) -> None:
    try:
        _kv().cache_set(_CUSTOM_KEY, {"items": items})
    except Exception:
        pass


def add_custom(rec: dict) -> dict:
    import uuid
    items = list_custom()
    rec = {
        "id": uuid.uuid4().hex[:10],
        "date": (rec.get("date") or "")[:10],
        "label": (rec.get("label") or "Reminder").strip() or "Reminder",
        "category": (rec.get("category") or "Other").strip() or "Other",
        "direction": rec.get("direction") if rec.get("direction") in ("in", "out", "action") else "action",
        "amount": round(float(rec.get("amount") or 0), 2),
        "owner": (rec.get("owner") or "").strip() or None,
        "note": (rec.get("note") or "").strip() or None,
        "created_at": datetime.now().isoformat(),
    }
    items.append(rec)
    _save_custom(items)
    return rec


def delete_custom(cid: str) -> bool:
    items = list_custom()
    kept = [i for i in items if i.get("id") != cid]
    if len(kept) != len(items):
        _save_custom(kept)
        return True
    return False


# ── notification preferences ────────────────────────────────────────────────
# Which categories the user wants the daily digest to cover. Stored as a single
# reserved override row (no extra table): status holds the enabled categories
# joined by "|", or the sentinel "NONE" when the user has muted everything.
_PREFS_KEY = "__notify_prefs__"


def get_notify_prefs() -> Optional[list[str]]:
    """Enabled notification categories. None → never set (treat as: notify about
    everything). [] → the user explicitly muted every category."""
    ov = all_overrides().get(_PREFS_KEY)
    s = ov.get("status") if ov else None
    if s is None:
        return None
    if s == "NONE":
        return []
    return [c for c in s.split("|") if c]


def set_notify_prefs(categories: list[str]) -> list[str]:
    cats = [c for c in (categories or []) if c]
    set_override(_PREFS_KEY, status=("|".join(cats) if cats else "NONE"))
    return cats


def category_enabled(category: str) -> bool:
    prefs = get_notify_prefs()
    return prefs is None or category in prefs


# ── per-item mutes ──────────────────────────────────────────────────────────
# Individual reminders the user has hidden from the page + digest (e.g. one
# specific recurring expense). Stored like the notify prefs: a single reserved
# override row whose `status` holds the muted item ids ("source:source_id")
# joined by "|". Muting hides every occurrence of that item everywhere.
_MUTED_KEY = "__muted_items__"


def get_muted_items() -> list[str]:
    ov = all_overrides().get(_MUTED_KEY)
    s = ov.get("status") if ov else None
    if not s or s == "NONE":
        return []
    return [c for c in s.split("|") if c]


def set_muted_items(ids: list[str]) -> list[str]:
    clean = [i for i in (ids or []) if i]
    set_override(_MUTED_KEY, status=("|".join(clean) if clean else "NONE"))
    return clean


def item_muted(source: str, source_id: str) -> bool:
    return f"{source}:{source_id}" in set(get_muted_items())


# ── day-of-month preferences for repeating tasks ────────────────────────────
# Moving a recurring reminder (rent, salary, a monthly bill) to another day
# remembers the DAY, not the absolute date, so every future month lands on that
# day. Keyed by a period-stripped "stream key". Stored in one reserved override
# row: status = "streamkey=DD|streamkey2=DD" (or "NONE" when empty).
_DOM_KEY = "__dom_prefs__"


def get_dom_prefs() -> dict[str, int]:
    ov = all_overrides().get(_DOM_KEY)
    s = ov.get("status") if ov else None
    if not s or s == "NONE":
        return {}
    out: dict[str, int] = {}
    for part in s.split("|"):
        if "=" in part:
            k, _, v = part.rpartition("=")
            try:
                out[k] = int(v)
            except ValueError:
                pass
    return out


def _write_dom_prefs(prefs: dict[str, int]) -> None:
    enc = "|".join(f"{k}={v}" for k, v in prefs.items() if k) or "NONE"
    set_override(_DOM_KEY, status=enc)


def set_dom_pref(stream_key: str, dom: int) -> None:
    prefs = get_dom_prefs()
    prefs[stream_key] = max(1, min(31, int(dom)))
    _write_dom_prefs(prefs)


def clear_dom_pref(stream_key: str) -> None:
    prefs = get_dom_prefs()
    if stream_key in prefs:
        del prefs[stream_key]
        _write_dom_prefs(prefs)
