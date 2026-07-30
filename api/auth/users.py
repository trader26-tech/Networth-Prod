"""
User registry for multi-user mode.
==================================

One row per person who can sign in. Registration is email-validated: a user is
created (status 'pending') when they first request a code, and only becomes
'active' once they enter the correct OTP — so an unverified email never yields a
usable account.

Persistence mirrors api/auth/store.py: Supabase `users` table when configured,
else a local JSON file for dev. Nothing here sends email or mints tokens — it
only records who exists and their verification state.

IMPORTANT (isolation): creating a user does NOT by itself separate their data
from anyone else's. Per-user data isolation is a separate, larger change
(user_id on every domain table + query scoping). Until that ships, the app runs
with MULTI_USER_ISOLATED off and treats registration as a soft, owner-controlled
gate so open sign-up can't expose existing data. See MULTI_USER_ISOLATED in
api/auth/config.py.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

_TABLE = "users"
_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "auth_data")
_FILE = os.path.join(_DATA_DIR, "users.json")

_client_cache = None
_tables_ok: Optional[bool] = None


def _read_env(key: str, default: str = "") -> str:
    val = os.environ.get(key, "").strip()
    if val:
        return val
    try:
        from dotenv import dotenv_values

        env_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".env"
        )
        if os.path.isfile(env_path):
            return (dotenv_values(env_path).get(key) or "").strip() or default
    except Exception:
        pass
    return default


def _client():
    global _client_cache, _tables_ok
    if _client_cache is not None or _tables_ok is False:
        return _client_cache
    url = _read_env("SUPABASE_URL").rstrip("/")
    key = _read_env("SUPABASE_SERVICE_KEY") or _read_env("SUPABASE_KEY")
    if not url or not key:
        _tables_ok = False
        return None
    try:
        from supabase import create_client

        cl = create_client(url, key)
        cl.table(_TABLE).select("id").limit(1).execute()  # probe table exists
        _client_cache = cl
        _tables_ok = True
        return cl
    except Exception as e:
        # Table missing (migration not run) → fall back to JSON in dev.
        print(f"⚠  users store: Supabase unavailable ({e}) — using JSON file")
        _tables_ok = False
        return None


def reset_cache() -> None:
    global _client_cache, _tables_ok
    _client_cache = None
    _tables_ok = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm(email: str) -> str:
    return (email or "").strip().lower()


# ── JSON fallback helpers ──────────────────────────────────────────────────────
def _load_json() -> list[dict]:
    if not os.path.isfile(_FILE):
        return []
    try:
        return json.loads(open(_FILE).read()) or []
    except Exception:
        return []


def _save_json(rows: list[dict]) -> None:
    os.makedirs(_DATA_DIR, exist_ok=True)
    open(_FILE, "w").write(json.dumps(rows, indent=2))


# ── public API ─────────────────────────────────────────────────────────────────
def get_by_email(email: str) -> Optional[dict]:
    email = _norm(email)
    if not email:
        return None
    cl = _client()
    if cl:
        rows = cl.table(_TABLE).select("*").eq("email", email).limit(1).execute().data or []
        return rows[0] if rows else None
    return next((u for u in _load_json() if u.get("email") == email), None)


def get_by_id(user_id: str) -> Optional[dict]:
    if not user_id:
        return None
    cl = _client()
    if cl:
        rows = cl.table(_TABLE).select("*").eq("id", user_id).limit(1).execute().data or []
        return rows[0] if rows else None
    return next((u for u in _load_json() if u.get("id") == user_id), None)


def ensure_pending(email: str, display_name: str = "") -> dict:
    """
    Create a 'pending' user if none exists for this email, else return it.

    Called when a code is requested. Registration is email-validated: the user
    only flips to 'active' after a correct OTP (mark_verified), so a pending row
    alone never grants access.
    """
    email = _norm(email)
    existing = get_by_email(email)
    if existing:
        return existing

    row = {
        "id": uuid.uuid4().hex,
        "email": email,
        "display_name": (display_name or "").strip() or None,
        "status": "pending",
        "is_admin": False,
        "created_at": _now(),
        "last_login": None,
    }
    cl = _client()
    if cl:
        cl.table(_TABLE).insert(row).execute()
    else:
        rows = _load_json()
        rows.append(row)
        _save_json(rows)
    return row


def mark_verified(email: str) -> Optional[dict]:
    """Flip a user to 'active' on their first successful OTP, stamp last_login."""
    email = _norm(email)
    u = get_by_email(email)
    if not u:
        return None
    patch = {"status": "active", "last_login": _now()}
    cl = _client()
    if cl:
        cl.table(_TABLE).update(patch).eq("email", email).execute()
    else:
        rows = _load_json()
        for r in rows:
            if r.get("email") == email:
                r.update(patch)
        _save_json(rows)
    u.update(patch)
    return u


def list_users() -> list[dict]:
    cl = _client()
    if cl:
        return cl.table(_TABLE).select("*").order("created_at").execute().data or []
    return _load_json()


def is_registered(email: str) -> bool:
    return get_by_email(_norm(email)) is not None
