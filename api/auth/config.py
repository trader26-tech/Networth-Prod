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


# ── Multi-user mode ─────────────────────────────────────────────────────────────
def multi_user() -> bool:
    """True to allow more than one account (registration + per-user login)."""
    return _read_env("MULTI_USER", "0").strip().lower() in ("1", "true", "yes", "on")


def data_isolated() -> bool:
    """
    True only once per-user DATA isolation is complete (user_id scoping on every
    domain table). Until then, all users share one dataset — so open sign-up
    would expose existing data. This flag gates that: while False the app keeps
    a soft owner-controlled allowlist even in multi-user mode.
    """
    return _read_env("MULTI_USER_ISOLATED", "0").strip().lower() in ("1", "true", "yes", "on")


def registration_open() -> bool:
    """
    Can a brand-new email self-register?

    Only when multi-user is on AND data is isolated. Before isolation ships,
    self-registration is refused (a new user would see shared data) even if
    MULTI_USER is on — the safe interlock.
    """
    return multi_user() and data_isolated()


def may_request_code(email: str) -> tuple[bool, str]:
    """
    Decide whether `email` may receive a login code, and why not if refused.

    - Single-user (MULTI_USER off): only the allowlist. (unchanged behaviour)
    - Multi-user + isolated: any email — registers on first correct code.
    - Multi-user + NOT isolated: existing registered users or the allowlist
      only — the interlock that stops open sign-up exposing shared data.
    """
    from . import users as _users

    email = (email or "").strip().lower()
    if not email:
        return False, "Enter your email."

    if not multi_user():
        return (is_allowed(email), "" if is_allowed(email) else "This email isn't authorised to sign in.")

    if data_isolated():
        return True, ""  # open registration, isolation guarantees separation

    # multi-user but not yet isolated: soft gate
    if is_allowed(email) or _users.is_registered(email):
        return True, ""
    return (
        False,
        "Sign-ups are not open yet on this instance. Ask the owner to add your "
        "email, or try again once multi-user data isolation is enabled.",
    )


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
