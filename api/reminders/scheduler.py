"""
Self-contained daily scheduler for the reminders digest.

No external cron: a single daemon thread wakes each hour and, when the local
hour first reaches the target (default 08:00), fires the digest — de-duped so it
sends at most once per calendar day even across restarts within the same day.

Configure with:
  REMINDERS_DIGEST_HOUR   local hour 0–23 to send at (default 8)
  REMINDERS_DIGEST_ENABLED set to "0"/"false" to disable
"""
from __future__ import annotations

import os
import threading
import time
from datetime import date

_thread: threading.Thread | None = None
_last_sent_day: str | None = None


def _read_env(key: str, default: str = "") -> str:
    val = os.environ.get(key, "").strip()
    if val:
        return val
    try:
        from dotenv import dotenv_values
        root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        env_path = os.path.join(root, ".env")
        if os.path.isfile(env_path):
            return (dotenv_values(env_path).get(key) or "").strip() or default
    except Exception:
        pass
    return default


def _enabled() -> bool:
    return _read_env("REMINDERS_DIGEST_ENABLED", "1").lower() not in ("0", "false", "no")


def _target_hour() -> int:
    try:
        return max(0, min(23, int(_read_env("REMINDERS_DIGEST_HOUR", "8"))))
    except ValueError:
        return 8


def _loop() -> None:
    global _last_sent_day
    from . import emailer
    while True:
        try:
            if _enabled():
                now = time.localtime()
                today = date.today().isoformat()
                if now.tm_hour >= _target_hour() and _last_sent_day != today:
                    _last_sent_day = today
                    res = emailer.send_digest()
                    print(f"  ▶ reminders digest: {res}", flush=True)
        except Exception as e:
            print(f"  ✗ reminders digest tick: {type(e).__name__}: {e}", flush=True)
        time.sleep(3600)   # re-check hourly


def start() -> None:
    """Start the daily digest thread once (idempotent)."""
    global _thread
    if _thread is not None:
        return
    _thread = threading.Thread(target=_loop, name="reminders-digest", daemon=True)
    _thread.start()
