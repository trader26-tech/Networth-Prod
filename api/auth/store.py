"""
Persistence for auth: pending OTP codes + trusted devices.

Backed by Supabase when configured (tables `auth_otp`, `auth_devices`), falling
back to local JSON files (api/auth_data/*.json) so local dev works with zero
setup. Mirrors the dual-store pattern used across the app.

Nothing sensitive is stored in the clear: OTP codes and PINs are kept only as
salted hashes; the trusted-device token is stored as a SHA-256 hash (the raw
token lives only in the user's httpOnly cookie).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from . import config

_OTP_TABLE = "auth_otp"
_DEV_TABLE = "auth_devices"

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "auth_data")
_OTP_JSON = os.path.join(_DATA_DIR, "otp.json")
_DEV_JSON = os.path.join(_DATA_DIR, "devices.json")


# ── Supabase client (shared with the rest of the app) ─────────────────────────
# Probed once: if the auth tables haven't been created yet (SUPABASE.md SQL not
# run), we transparently fall back to local JSON so auth still works. In a
# production container that fallback is ephemeral (devices reset on redeploy) —
# the warning nudges you to run the SQL — but the app is never locked out.
_TABLES_OK: Optional[bool] = None


def _client():
    global _TABLES_OK
    try:
        from api import cc_supabase_store
        cl = cc_supabase_store._get_client()
    except Exception:
        return None
    if cl is None:
        return None
    if _TABLES_OK is None:
        try:
            cl.table(_DEV_TABLE).select("device_id").limit(1).execute()
            _TABLES_OK = True
        except Exception as e:
            msg = str(e).lower()
            if any(s in msg for s in ("auth_devices", "pgrst205", "does not exist", "schema cache", "could not find")):
                print("⚠ Supabase auth tables missing — using local JSON for auth. "
                      "Run the auth SQL from SUPABASE.md to persist devices across redeploys.")
                _TABLES_OK = False
            else:
                _TABLES_OK = True   # transient error → let real calls surface it
    return cl if _TABLES_OK else None


def reset_tables_cache() -> None:
    global _TABLES_OK
    _TABLES_OK = None


# ── tiny JSON helpers (fallback) ──────────────────────────────────────────────
def _load(path: str) -> dict:
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def _save(path: str, data: dict) -> None:
    os.makedirs(_DATA_DIR, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def _now() -> int:
    return int(time.time())


def _iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


# ── hashing ───────────────────────────────────────────────────────────────────
def _sha(value: str, salt: str = "") -> str:
    return hashlib.sha256((salt + value).encode()).hexdigest()


def _pbkdf2(value: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", value.encode(), salt.encode(), 100_000).hex()


def hash_token(raw: str) -> str:
    return _sha(raw)


def new_device_token() -> str:
    return secrets.token_urlsafe(32)


# ══════════════════════════════════════════════════════════════════════════════
# OTP
# ══════════════════════════════════════════════════════════════════════════════
def otp_put(email: str, code: str) -> None:
    """Store a fresh OTP for an email (replacing any previous one)."""
    email = email.strip().lower()
    expires = _now() + config.OTP_TTL_SECONDS
    code_hash = _sha(code, salt=email)
    c = _client()
    if c:
        c.table(_OTP_TABLE).delete().eq("email", email).execute()
        c.table(_OTP_TABLE).insert({
            "email": email, "code_hash": code_hash,
            "expires_at": _iso(expires), "attempts": 0,
            "created_at": _iso(_now()),
        }).execute()
        return
    data = _load(_OTP_JSON)
    data[email] = {"code_hash": code_hash, "expires_at": expires, "attempts": 0,
                   "created_at": _now()}
    _save(_OTP_JSON, data)


def otp_check(email: str, code: str) -> tuple[bool, str]:
    """Verify a submitted code. Returns (ok, error_message)."""
    email = email.strip().lower()
    code_hash = _sha(code, salt=email)
    c = _client()
    if c:
        rows = c.table(_OTP_TABLE).select("*").eq("email", email).limit(1).execute().data or []
        if not rows:
            return False, "Request a new code."
        row = rows[0]
        exp = _parse_iso(row.get("expires_at"))
        if exp < _now():
            c.table(_OTP_TABLE).delete().eq("email", email).execute()
            return False, "Code expired — request a new one."
        if int(row.get("attempts") or 0) >= config.OTP_MAX_ATTEMPTS:
            c.table(_OTP_TABLE).delete().eq("email", email).execute()
            return False, "Too many attempts — request a new code."
        if hmac.compare_digest(row.get("code_hash") or "", code_hash):
            c.table(_OTP_TABLE).delete().eq("email", email).execute()
            return True, ""
        c.table(_OTP_TABLE).update({"attempts": int(row.get("attempts") or 0) + 1}).eq("email", email).execute()
        return False, "Incorrect code."

    data = _load(_OTP_JSON)
    row = data.get(email)
    if not row:
        return False, "Request a new code."
    if int(row.get("expires_at") or 0) < _now():
        data.pop(email, None); _save(_OTP_JSON, data)
        return False, "Code expired — request a new one."
    if int(row.get("attempts") or 0) >= config.OTP_MAX_ATTEMPTS:
        data.pop(email, None); _save(_OTP_JSON, data)
        return False, "Too many attempts — request a new code."
    if hmac.compare_digest(row.get("code_hash") or "", code_hash):
        data.pop(email, None); _save(_OTP_JSON, data)
        return True, ""
    row["attempts"] = int(row.get("attempts") or 0) + 1
    _save(_OTP_JSON, data)
    return False, "Incorrect code."


# ── request-otp rate limiting (in-memory; resets on restart, which is fine) ────
_otp_sends: dict[str, list[int]] = {}


def otp_rate_ok(email: str) -> tuple[bool, str]:
    email = email.strip().lower()
    now = _now()
    hits = [t for t in _otp_sends.get(email, []) if now - t < 3600]
    if hits and now - hits[-1] < config.OTP_RESEND_COOLDOWN:
        wait = config.OTP_RESEND_COOLDOWN - (now - hits[-1])
        return False, f"Please wait {wait}s before requesting another code."
    if len(hits) >= config.OTP_MAX_PER_HOUR:
        return False, "Too many codes requested. Try again later."
    hits.append(now)
    _otp_sends[email] = hits
    return True, ""


# ══════════════════════════════════════════════════════════════════════════════
# Devices (trusted devices + their PIN + per-device auto-lock setting)
# ══════════════════════════════════════════════════════════════════════════════
def device_create(email: str) -> tuple[str, str]:
    """Create a trusted device. Returns (device_id, raw_device_token)."""
    email = email.strip().lower()
    device_id = uuid.uuid4().hex
    raw = new_device_token()
    expires = _now() + config.DEVICE_TTL_DAYS * 86400
    row = {
        "device_id": device_id, "email": email,
        "token_hash": hash_token(raw),
        "pin_hash": None, "pin_salt": None, "pin_attempts": 0,
        "lock_minutes": config.DEFAULT_LOCK_MINUTES,
        "expires_at": _iso(expires), "created_at": _iso(_now()),
        "last_used": _iso(_now()), "revoked": False,
    }
    c = _client()
    if c:
        c.table(_DEV_TABLE).insert(row).execute()
    else:
        data = _load(_DEV_JSON)
        row["expires_at"] = expires
        row["created_at"] = _now()
        row["last_used"] = _now()
        data[device_id] = row
        _save(_DEV_JSON, data)
    return device_id, raw


def device_get(device_id: str) -> Optional[dict]:
    if not device_id:
        return None
    c = _client()
    if c:
        rows = c.table(_DEV_TABLE).select("*").eq("device_id", device_id).limit(1).execute().data or []
        return rows[0] if rows else None
    return _load(_DEV_JSON).get(device_id)


def device_valid(device_id: str, raw_token: str) -> Optional[dict]:
    """Return the device row iff the cookie token matches and it's live."""
    row = device_get(device_id)
    if not row or row.get("revoked"):
        return None
    if _parse_iso(row.get("expires_at")) < _now():
        return None
    if not hmac.compare_digest(row.get("token_hash") or "", hash_token(raw_token or "")):
        return None
    return row


def device_update(device_id: str, **fields: Any) -> None:
    c = _client()
    if c:
        c.table(_DEV_TABLE).update(fields).eq("device_id", device_id).execute()
        return
    data = _load(_DEV_JSON)
    if device_id in data:
        data[device_id].update(fields)
        _save(_DEV_JSON, data)


def device_touch(device_id: str) -> None:
    device_update(device_id, last_used=_iso(_now()) if _client() else _now())


def device_delete(device_id: str) -> None:
    c = _client()
    if c:
        c.table(_DEV_TABLE).delete().eq("device_id", device_id).execute()
        return
    data = _load(_DEV_JSON)
    data.pop(device_id, None)
    _save(_DEV_JSON, data)


def device_set_pin(device_id: str, pin: str) -> None:
    salt = secrets.token_hex(16)
    device_update(device_id, pin_hash=_pbkdf2(pin, salt), pin_salt=salt, pin_attempts=0)


def device_check_pin(device_id: str, pin: str) -> tuple[bool, str]:
    """Verify a PIN. On too many failures the device is revoked (→ re-OTP)."""
    row = device_get(device_id)
    if not row or row.get("revoked"):
        return False, "Device not recognised — sign in with email."
    if not row.get("pin_hash"):
        return False, "No PIN set on this device."
    if int(row.get("pin_attempts") or 0) >= config.PIN_MAX_ATTEMPTS:
        device_update(device_id, revoked=True)
        return False, "Too many wrong PINs — locked. Sign in with email."
    if hmac.compare_digest(row.get("pin_hash") or "", _pbkdf2(pin, row.get("pin_salt") or "")):
        device_update(device_id, pin_attempts=0)
        return True, ""
    attempts = int(row.get("pin_attempts") or 0) + 1
    if attempts >= config.PIN_MAX_ATTEMPTS:
        device_update(device_id, pin_attempts=attempts, revoked=True)
        return False, "Too many wrong PINs — locked. Sign in with email."
    device_update(device_id, pin_attempts=attempts)
    left = config.PIN_MAX_ATTEMPTS - attempts
    return False, f"Incorrect PIN — {left} attempt{'s' if left != 1 else ''} left."


# ── misc ──────────────────────────────────────────────────────────────────────
def _parse_iso(v: Any) -> int:
    """Accept either an epoch int (JSON store) or ISO string (Supabase)."""
    if v is None:
        return 0
    if isinstance(v, (int, float)):
        return int(v)
    try:
        return int(datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp())
    except Exception:
        return 0
