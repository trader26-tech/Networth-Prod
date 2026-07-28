"""
Stateless, HMAC-signed **access tokens** — the bearer attached to every API call.

Design: an access token is `base64url(payload).base64url(hmac_sha256(payload))`.
The payload carries the device id and an absolute expiry. Verifying needs only
the server secret — no DB round-trip per request, so the auth middleware stays
fast. Tokens are short-lived (= a device's auto-lock window), so revoking a
device at the DB level stops *refresh*; any already-minted access token simply
dies within the window.

The signing secret (AUTH_SECRET) is read from the environment / .env. If it's
missing we generate a strong one once and persist it to .env so tokens survive
restarts (otherwise every restart would invalidate all sessions).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Optional


_SECRET_CACHE: Optional[bytes] = None


def _read_env(key: str) -> str:
    val = os.environ.get(key, "").strip()
    if val:
        return val
    try:
        from dotenv import dotenv_values
        env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".env")
        if os.path.isfile(env_path):
            return (dotenv_values(env_path).get(key) or "").strip()
    except Exception:
        pass
    return ""


def _secret() -> bytes:
    """Return the HMAC signing secret, generating + persisting one if absent."""
    global _SECRET_CACHE
    if _SECRET_CACHE:
        return _SECRET_CACHE

    s = _read_env("AUTH_SECRET")
    if not s:
        s = secrets.token_urlsafe(48)
        os.environ["AUTH_SECRET"] = s
        try:
            from api import state
            state.write_env(AUTH_SECRET=s)
            print("✓ Generated and persisted AUTH_SECRET to .env")
        except Exception as e:
            print(f"⚠ Could not persist AUTH_SECRET (tokens won't survive restart): {e}")
    _SECRET_CACHE = s.encode()
    return _SECRET_CACHE


def reset_secret_cache() -> None:
    global _SECRET_CACHE
    _SECRET_CACHE = None


# ── base64url without padding ─────────────────────────────────────────────────
def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def mint_access(device_id: str, ttl_seconds: int) -> tuple[str, int]:
    """Return (token, expires_at_epoch) for the given device + lifetime."""
    exp = int(time.time()) + int(ttl_seconds)
    payload = {"did": device_id, "exp": exp}
    body = _b64e(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    sig = _b64e(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}", exp


def verify_access(token: str) -> Optional[dict]:
    """Return the payload dict if the token is valid + unexpired, else None."""
    try:
        body, sig = token.split(".", 1)
        expected = _b64e(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            return None
        payload = json.loads(_b64d(body))
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        return payload
    except Exception:
        return None
