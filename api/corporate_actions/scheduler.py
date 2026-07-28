"""
Daily auto-sync of declared dividends.

A single daemon thread wakes hourly and, once a day (default 07:00 local), reads
NSE + BSE declared corporate actions and logs new dividends for held stocks (see
ingest.sync). De-duped so it runs at most once per calendar day, and fully
best-effort — a blocked exchange fetch just logs "0 added" and tries again
tomorrow (the other exchange still covers its names).

Configure with:
  DIVIDEND_SYNC_HOUR      local hour 0–23 to run at (default 7)
  DIVIDEND_SYNC_ENABLED   set to "0"/"false" to disable
"""
from __future__ import annotations

import os
import threading
import time
from datetime import date

_thread: threading.Thread | None = None
_last_run_day: str | None = None


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
    return _read_env("DIVIDEND_SYNC_ENABLED", "1").lower() not in ("0", "false", "no")


def _hour() -> int:
    try:
        return max(0, min(23, int(_read_env("DIVIDEND_SYNC_HOUR", "7"))))
    except ValueError:
        return 7


def _loop() -> None:
    global _last_run_day
    from . import ingest
    while True:
        try:
            if _enabled():
                now = time.localtime()
                today = date.today().isoformat()
                if now.tm_hour >= _hour() and _last_run_day != today:
                    _last_run_day = today
                    res = ingest.sync(dry_run=False)
                    print(f"  ▶ dividend sync: +{res['added_count']} added · "
                          f"{res['pruned_count']} pruned · {res['skipped_existing']} existing "
                          f"(reachable={res['source_reachable']})", flush=True)
        except Exception as e:
            print(f"  ✗ dividend sync tick: {type(e).__name__}: {e}", flush=True)
        time.sleep(3600)


def start() -> None:
    global _thread
    if _thread is not None:
        return
    _thread = threading.Thread(target=_loop, name="dividend-sync", daemon=True)
    _thread.start()
