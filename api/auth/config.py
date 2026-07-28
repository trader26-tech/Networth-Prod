"""Auth tunables + the email allowlist. Most are overridable via env vars."""
from __future__ import annotations

import os


def _read_env(key: str, default: str = "") -> str:
    val = os.environ.get(key, "").strip()
    if val:
        return val
    try:
        from dotenv import dotenv_values
        env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".env")
        if os.path.isfile(env_path):
            v = (dotenv_values(env_path).get(key) or "").strip()
            if v:
                return v
    except Exception:
        pass
    return default


def _allowlist_ordered() -> list[str]:
    raw = _read_env("OTP_ALLOWLIST", "ranjeevfortrade@gmail.com")
    return [e.strip().lower() for e in raw.split(",") if e.strip()]


def allowlist() -> set[str]:
    """Emails permitted to request an OTP (comma-separated env override)."""
    return set(_allowlist_ordered())


def primary_email() -> str:
    """The default sign-in address — first entry in the allowlist. Used when the
    client doesn't send an email (single-user: no email box, code goes here)."""
    lst = _allowlist_ordered()
    return lst[0] if lst else ""


def is_allowed(email: str) -> bool:
    return (email or "").strip().lower() in allowlist()


# ── OTP ────────────────────────────────────────────────────────────────────────
OTP_TTL_SECONDS = 5 * 60          # code valid for 5 minutes
OTP_MAX_ATTEMPTS = 5              # wrong-code tries before the code is burned
OTP_RESEND_COOLDOWN = 30         # seconds between request-otp calls (per email)
OTP_MAX_PER_HOUR = 6             # request-otp calls per email per hour

# ── PIN ────────────────────────────────────────────────────────────────────────
PIN_MIN_LEN = 4
PIN_MAX_LEN = 8
PIN_MAX_ATTEMPTS = 5             # wrong-PIN tries before the device is revoked

# ── Sessions ────────────────────────────────────────────────────────────────────
DEVICE_TTL_DAYS = 30             # trusted-device lifetime (the httpOnly cookie)
DEFAULT_LOCK_MINUTES = 10        # idle auto-lock window (per device, editable)
MIN_LOCK_MINUTES = 1
MAX_LOCK_MINUTES = 120
LOCK_PRESETS = [1, 5, 10, 30, 60]

DEVICE_COOKIE = "nw_device"      # httpOnly cookie holding the trusted-device token
