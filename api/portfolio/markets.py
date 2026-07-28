"""
Market trading sessions — single source of truth for "is a market open right
now", used to drive how often live prices are refreshed.

Holdings span more than one exchange (NSE/BSE in India, NYSE/NASDAQ in the US),
so prices must keep updating whenever *any* held market is open — not just the
Indian session. Add a market by appending one row to SESSIONS; DST is handled
automatically via the IANA timezone.
"""
from __future__ import annotations

from datetime import datetime, time, timezone, timedelta

# (label, IANA timezone, open, close). Regular cash-session hours, Mon–Fri.
SESSIONS: list[tuple[str, str, time, time]] = [
    ("IN", "Asia/Kolkata",     time(9, 15), time(15, 30)),
    ("US", "America/New_York", time(9, 30), time(16, 0)),
]

# Fixed-offset fallback used only if the tz database is unavailable. India has no
# DST; the US offset is an approximation (off by an hour in winter) — acceptable
# for a fallback that, in practice, almost never triggers (tzdata ships via pandas).
_FALLBACK_OFFSET = {
    "Asia/Kolkata":     timedelta(hours=5, minutes=30),
    "America/New_York": timedelta(hours=-4),
}


def _now_in(tz: str) -> datetime:
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(tz))
    except Exception:
        return datetime.now(timezone.utc) + _FALLBACK_OFFSET.get(tz, timedelta())


def _is_open(tz: str, o: time, c: time) -> bool:
    n = _now_in(tz)
    if n.weekday() >= 5:          # Sat/Sun
        return False
    return o <= n.time() <= c


def any_open() -> bool:
    """True if any market we track is currently in its regular session."""
    return any(_is_open(tz, o, c) for _id, tz, o, c in SESSIONS)


def open_markets() -> list[str]:
    """Ids of the markets open right now (e.g. ['US'])."""
    return [mid for mid, tz, o, c in SESSIONS if _is_open(tz, o, c)]
