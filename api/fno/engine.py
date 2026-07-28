"""
Live F&O P&L engine — a daemon thread (same pattern as api/portfolio/engine.py's
refresher) that runs whether or not a browser is open:

  every 1s   recompute each connected account's day P&L: positions come from
             Kite every ~20s; in between, only LTPs are re-fetched (1 quote call
             per account per second — well inside Kite's rate limits) and the
             m2m is recomputed locally with Kite's own formula:
                 m2m = (sell_value − buy_value) + net_qty × ltp × multiplier
  every 60s  persist a minute snapshot per account (fno_pnl_snapshots) and
             upsert today's per-strategy row in fno_daily_pnl (source='live') —
             the last write before close is the day's final number.
  every 5m   sync today's fills (kite.trades()) into fno_trades.

The WebSocket route just broadcasts `latest()` once a second — the per-second
series lives only in the browser; the DB keeps 1-minute resolution.
"""
from __future__ import annotations

import threading
import time
import traceback
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import store, kite as fno_kite, pnl as fno_pnl

IST = ZoneInfo("Asia/Kolkata")
PRUNE_EVERY = 300           # s — how often to run snapshot retention (rolling + day cleanup)
SNAP_RETAIN_MIN = 24 * 60   # min — keep the WHOLE trading session's minute snapshots
                            # (the chart draws the full day, not just the last hour);
                            # the session-date prune drops previous days at rollover.


def _live_snapshot_date(now: datetime) -> str:
    """The only date whose minute snapshots we keep. A trading day is considered
    complete at ~3 AM IST the next morning, so before 3 AM we still keep the
    previous calendar day's snapshots (grace); after 3 AM only today's remain."""
    d = now.date() if now.hour >= 3 else now.date() - timedelta(days=1)
    return d.isoformat()

POSITIONS_EVERY = 20        # s — full kite.positions() refresh while trades are open
IDLE_POSITIONS_EVERY = 120  # s — light "any new trades?" check when the book is flat
TRADES_EVERY = 300          # s — kite.trades() sync
SNAPSHOT_EVERY = 60         # s — minute snapshot + daily upsert
ACCOUNTS_EVERY = 30         # s — reload the account list

_latest: dict = {"ts": None, "market_open": False, "accounts": [], "total_day_pnl": 0.0,
                 "by_strategy": {}}
_lock = threading.Lock()
_thread: threading.Thread | None = None

# per-account runtime state
_acc_state: dict = {}       # id → {positions, kite, pos_at, trades_at, expired_logged}


def latest() -> dict:
    with _lock:
        return dict(_latest)


def market_open(now: datetime | None = None) -> bool:
    """True during the trading day, anchored at 09:00 IST and running through
    03:00 IST the NEXT morning so late-night US crude / MCX evening sessions stay
    live. 00:00–03:00 belongs to the PREVIOUS calendar day's session; 03:00–09:00
    is the daily gap; closed instruments just stop moving, so the span is free.
    Weekends: a Fri session may spill into Sat up to 03:00, but no session opens
    Sat/Sun (nor in the Mon-early-morning hours, which trail a Sunday)."""
    now = now or datetime.now(IST)
    h = now.hour
    if h < 3:                                   # early morning → prior day's session
        return (now - timedelta(days=1)).weekday() < 5
    if h < 9:                                   # 03:00–09:00 → flat gap between days
        return False
    return now.weekday() < 5                     # 09:00–23:59 → today's session


def _live_m2m(p: dict, ltp: float | None) -> float:
    """Kite's m2m marked to a fresher LTP. m2m is linear in price with slope
    qty × multiplier, so this is exact for both intraday and carried positions
    (and a no-op for positions already closed today, where qty = 0)."""
    m2m = float(p.get("m2m") or 0)
    if not ltp:
        return m2m
    qty = float(p.get("quantity") or 0)
    mult = float(p.get("multiplier") or 1)
    last = float(p.get("last_price") or 0)
    return m2m + qty * mult * (ltp - last)


def _refresh_positions(acc: dict, st: dict, now_s: float) -> list[dict]:
    """Ensure a session + refresh this account's positions. Raises a token error
    when the session died. Positions (and their day P&L) come free on any Kite
    app — Personal (free) or paid — so this works for every account."""
    k = st.get("kite")
    if k is None:
        k = fno_kite.client(acc)
        st["kite"] = k
    # Flat book → idle: an occasional positions check so the engine wakes itself
    # when a new trade is taken.
    poll_every = POSITIONS_EVERY if st.get("active") else IDLE_POSITIONS_EVERY
    if now_s - st.get("pos_at", 0) >= poll_every or "positions" not in st:
        st["positions"] = fno_kite.fetch_positions(k)
        st["pos_at"] = now_s
    net_book = st["positions"]["net"]
    st["active"] = any(p.get("quantity") for p in net_book)
    return net_book


def _pnl_from(acc: dict, net_book: list[dict], ltps: dict, real_by_strat: dict | None = None) -> dict:
    """One account's live day P&L from its positions + a SHARED LTP map. The LTPs
    come from the paid 'price-feed' account (market data is app-level, and an LTP
    is the same whoever holds the instrument), so free accounts get per-second
    P&L too. When ltps is empty we fall back to Kite's own position m2m."""
    # Strategy LABELS are configuration read from the DB, never computed here:
    #   • base = the instrument's own class (crude / sentinel / other) that
    #     kite.fetch_positions already stamped on each leg via classify();
    #   • the account's stored strategy label (fno_accounts.strategy column) then
    #     overrides that base for non-crude legs, and a per-leg pin overrides both.
    # resolve_leg_strategy applies exactly that precedence — the daemon does not
    # decide or change any label, it only reads what's set in the database.
    # The account's label is read straight off the account row the loop already
    # holds (fno_accounts.strategy, refreshed every ACCOUNTS_EVERY s) — no extra
    # per-second DB call. Pre-migration (no column) we fall back to the KV blob.
    sv = (acc.get("strategy") or "").strip().lower()
    if sv:
        overrides = {acc["id"]: sv}
    elif "strategy" in acc:
        overrides = {}                           # column present but unset → no pin
    else:
        overrides = store.get_account_strategies()   # pre-migration KV fallback
    leg_pins = store.get_leg_strategies()        # per-leg overrides (open positions)
    by_strategy: dict[str, dict] = {}
    positions_out = []
    for p in net_book:
        ltp = ltps.get(p["tradingsymbol"])
        m2m = _live_m2m(p, ltp)
        strat = store.resolve_leg_strategy(p.get("strategy") or "other", acc["id"], p["tradingsymbol"], overrides, leg_pins)
        s = by_strategy.setdefault(strat, {"day_pnl": 0.0, "realized": 0.0,
                                           "unrealized": 0.0, "positions": 0,
                                           "open_positions": 0, "trades": 0})
        s["day_pnl"] += m2m
        s["realized"] += float(p.get("realised") or 0)     # provisional — overridden below
        s["unrealized"] += float(p.get("unrealised") or 0)
        s["positions"] += 1
        if p.get("quantity"):
            s["open_positions"] += 1
        positions_out.append({**p, "strategy": strat, "last_price": ltp or p.get("last_price"),
                              "m2m": round(m2m, 2)})

    # Authoritative realised = FILL-REPLAY (Kite's position `realised` reports ~0
    # for intraday MIS and for legs carried in then closed today, which is why the
    # live 'Booked' showed ₹0 despite dozens of closes). Take booked from the fill
    # log and derive unrealized = day_pnl − booked, so the split reconciles with
    # the KPI/calendar 'Booked today' AND sums to the live day P&L (= the chart &
    # Kite). Only applied when we have the fill-replay figures (market-hours live).
    if real_by_strat is not None:
        blank = {"day_pnl": 0.0, "realized": 0.0, "unrealized": 0.0,
                 "positions": 0, "open_positions": 0, "trades": 0}
        for strat in set(by_strategy) | set(real_by_strat):
            s = by_strategy.setdefault(strat, dict(blank))
            booked = float(real_by_strat.get(strat, 0.0))
            s["realized"] = booked
            s["unrealized"] = s["day_pnl"] - booked

    day_pnl = sum(s["day_pnl"] for s in by_strategy.values())
    return {
        "id": acc["id"], "account_label": acc.get("account_label"),
        "person": acc.get("person"), "kite_user_id": acc.get("kite_user_id"),
        "day_pnl": round(day_pnl, 2),
        "by_strategy": {k2: {**v, "day_pnl": round(v["day_pnl"], 2),
                             "realized": round(v["realized"], 2),
                             "unrealized": round(v["unrealized"], 2)}
                        for k2, v in by_strategy.items()},
        "positions": positions_out,
    }


def _fetch_shared_ltps(feed_st: dict, live: list[tuple]) -> dict:
    """One batched LTP call through the price-feed (paid) session, covering every
    open instrument across all accounts. Never raises — a market-data blip just
    degrades to Kite's server-side position prices."""
    keys = set()
    for _, _, net_book in live:
        for p in net_book:
            if p.get("quantity"):
                keys.add(f"{p['exchange']}:{p['tradingsymbol']}")
    if not keys or not feed_st or not feed_st.get("kite"):
        return {}
    try:
        raw = feed_st["kite"].ltp(list(keys))
        return {k.split(":", 1)[1]: v.get("last_price")
                for k, v in raw.items() if v.get("last_price")}
    except Exception:
        return {}


def _snapshot(acc_out: dict, now: datetime) -> None:
    ts_min = now.replace(second=0, microsecond=0).isoformat()
    date = _live_snapshot_date(now)              # 9AM-anchored trading day (see below)
    by_strat = {k: v["day_pnl"] for k, v in acc_out["by_strategy"].items()}
    store.add_snapshot(acc_out["id"], ts_min, date, acc_out["day_pnl"], by_strat)
    _write_daily(acc_out, date)


def _write_daily(acc_out: dict, date: str) -> None:
    """Upsert today's live daily row per strategy. realised = FILL-REPLAY (Kite's
    position `realised` reports 0 for a position carried in and closed today, so
    trusting it makes 'Booked today' 0 despite real closes); unrealized + total
    (day_pnl) come from live prices. Iterate the UNION of strategies so one fully
    closed today (booked, no open leg left) still records its realised."""
    real = fno_pnl.realized_by_strategy(acc_out["id"], date)
    strats = set(acc_out["by_strategy"]) | set(real)
    for strat in strats:
        v = acc_out["by_strategy"].get(strat, {})
        store.upsert_daily(acc_out["id"], date, strat,
                           realized=real.get(strat, 0.0),
                           unrealized=v.get("unrealized", 0.0),
                           total=v.get("day_pnl", real.get(strat, 0.0)),
                           trades_count=v.get("trades", 0), source="live")
    # drop any live rows for today under a strategy that no longer has open legs
    # OR realised (e.g. after a re-pin) so they can't linger / double-count.
    store.prune_live_daily(acc_out["id"], date, strats)


def _snapshot_open_series(now: datetime) -> None:
    """Once a minute, mark ALL carry-forward open legs to market via the paid feed
    and append a per-account unrealized point — this powers the intraday "open
    positions P&L" graph even for accounts that aren't individually logged in."""
    try:
        from . import openpos
        op = openpos.open_positions(None)
        if op.get("priced_count", 0) <= 0:
            return                                    # feed down / nothing priced → skip
        by_acc: dict = {}
        for leg in op.get("positions", []):
            u = leg.get("unrealized")
            if u is not None:
                by_acc[leg["account_id"]] = round(by_acc.get(leg["account_id"], 0.0) + u, 2)
        if by_acc:
            store.append_open_series(_live_snapshot_date(now),   # 9AM-anchored session day
                                     now.replace(second=0, microsecond=0).isoformat(), by_acc)
    except Exception:
        pass


def _sync_trades(acc: dict, st: dict) -> None:
    try:
        rows = fno_kite.fetch_trades(st["kite"], acc["id"])
        added = store.upsert_trades(rows)
        if added:
            store.update_account(acc["id"], {"last_synced": store._now()})
    except Exception:
        pass


def _loop() -> None:
    global _latest
    from . import metrics
    accounts: list[dict] = []
    accounts_at = 0.0
    pledged_at = 0.0
    prune_at = 0.0
    while True:
        try:
            now = datetime.now(IST)
            now_s = time.time()

            # pledged-capital refresh runs regardless of market hours (drives the
            # hero CAGR); cached so /summary never makes a live Kite call.
            if now_s - pledged_at >= metrics._REFRESH_EVERY or pledged_at == 0.0:
                pledged_at = now_s
                metrics.refresh_pledged()

            # Rolling retention: keep only the last SNAP_RETAIN_MIN of minute
            # snapshots (that's the whole live window the chart draws) + the
            # completed-day cleanup at ~3 AM. Together the DB stays tiny.
            if now_s - prune_at >= PRUNE_EVERY or prune_at == 0.0:
                prune_at = now_s
                try:
                    store.prune_snapshots_before_ts((now - timedelta(minutes=SNAP_RETAIN_MIN)).isoformat())
                    store.prune_snapshots_before(_live_snapshot_date(now))
                except Exception:
                    pass

            if not market_open(now):
                with _lock:
                    _latest = {"ts": now.isoformat(), "market_open": False, "accounts": [],
                               "total_day_pnl": 0.0, "by_strategy": {}}
                time.sleep(30)
                continue

            if now_s - accounts_at >= ACCOUNTS_EVERY or not accounts:
                accounts = [a for a in store.list_accounts()
                            if a.get("access_token") and a.get("status") == "connected"]
                accounts_at = now_s
                for gone in set(_acc_state) - {a["id"] for a in accounts}:
                    _acc_state.pop(gone, None)

            # Phase 1 — refresh every account's positions on its own session
            # (works on free Personal apps; only a dead LOGIN marks it expired).
            live: list[tuple] = []          # (acc, st, net_book)
            for acc in accounts:
                st = _acc_state.setdefault(acc["id"], {})
                try:
                    net_book = _refresh_positions(acc, st, now_s)
                    live.append((acc, st, net_book))
                    st["expired_logged"] = False
                except Exception as e:
                    if fno_kite.is_token_error(e):
                        if not st.get("expired_logged"):
                            store.update_account(acc["id"], {"status": "expired"})
                            store.add_log(acc["id"], "token_expired", str(e)[:200])
                            st["expired_logged"] = True
                            accounts_at = 0     # force account-list reload
                        st.pop("kite", None)
                    # transient errors: keep last computed state silently
                if now_s - st.get("trades_at", 0) >= TRADES_EVERY and st.get("kite"):
                    st["trades_at"] = now_s
                    _sync_trades(acc, st)
                    st.pop("real_by_strat", None)         # fills changed → recompute booked
                # cache fill-replay booked-today per strategy (authoritative realised);
                # refreshed only when the fill log changes, so it's ~free per tick.
                if "real_by_strat" not in st:
                    try:
                        st["real_by_strat"] = fno_pnl.realized_by_strategy(
                            acc["id"], _live_snapshot_date(now))
                    except Exception:
                        st["real_by_strat"] = {}

            # Phase 2 — one shared LTP fetch through the paid price-feed session
            # (falls back to the designated feed, else any connected session, else
            # Kite's own position prices).
            feed_id = store.get_price_feed_id()
            by_id = {a["id"]: s for a, s, _ in live}
            feed_st = by_id.get(feed_id) if feed_id else None
            if not feed_st and live:
                feed_st = live[0][1]        # no explicit feed → try the first session
            ltps = _fetch_shared_ltps(feed_st, live)

            # Phase 3 — compute each account's P&L from the shared LTPs
            outs = [_pnl_from(acc, net_book, ltps, st.get("real_by_strat"))
                    for acc, st, net_book in live]

            by_strategy: dict[str, float] = {}
            for o in outs:
                for strat, v in o["by_strategy"].items():
                    by_strategy[strat] = round(by_strategy.get(strat, 0.0) + v["day_pnl"], 2)

            with _lock:
                _latest = {
                    "ts": now.isoformat(),
                    "market_open": True,
                    "accounts": outs,
                    "total_day_pnl": round(sum(o["day_pnl"] for o in outs), 2),
                    "by_strategy": by_strategy,
                }

            # minute boundary → persist snapshots + daily rows, but ONLY while
            # trades are open (plus one final point when the book goes flat,
            # so the day's chart ends on the realised total). No open trades →
            # nothing is written; the chart simply has no points to add.
            if now.second < 1 or now_s - _loop_last_snap[0] >= SNAPSHOT_EVERY:
                _loop_last_snap[0] = now_s
                for o in outs:
                    st = _acc_state.get(o["id"], {})
                    open_now = any(v.get("open_positions", 0) > 0
                                   for v in o["by_strategy"].values())
                    if open_now:
                        st["was_open"] = True
                        _snapshot(o, now)
                    elif st.pop("was_open", False):
                        _snapshot(o, now)          # final flat point
                _snapshot_open_series(now)         # intraday open-position P&L graph

        except Exception:
            traceback.print_exc()
        time.sleep(1)


_loop_last_snap = [0.0]


def start() -> None:
    """Idempotent daemon-thread start (called from api/main.py at boot)."""
    global _thread
    if _thread and _thread.is_alive():
        return
    _thread = threading.Thread(target=_loop, name="fno-live-engine", daemon=True)
    _thread.start()


def capture_once() -> dict:
    """Run ONE capture pass on demand — the same work the live daemon does each
    minute (refresh positions → shared LTP → compute P&L → write the minute
    snapshot + live daily row), but callable from an EXTERNAL per-minute cron.

    This is what makes minute capture happen even when nobody has the app open
    AND the web process is idle/asleep: the cron's HTTP request wakes the host
    and drives the capture. It's fully self-contained (its own transient state,
    its own Kite client objects) so it never races the in-process daemon, and
    every write is an idempotent upsert keyed on (account, minute) — so if both
    the daemon and the cron fire for the same minute, they converge to one row.

    Also runs the completed-day prune so `fno_pnl_snapshots` stays lean without
    the daemon (only the live day's minutes are kept; daily totals live on)."""
    now = datetime.now(IST)
    now_s = time.time()
    try:
        from . import metrics
        metrics.refresh_pledged()
    except Exception:
        pass
    try:
        store.prune_snapshots_before_ts((now - timedelta(minutes=SNAP_RETAIN_MIN)).isoformat())
        store.prune_snapshots_before(_live_snapshot_date(now))
    except Exception:
        pass

    if not market_open(now):
        return {"market_open": False, "captured": 0}

    accounts = [a for a in store.list_accounts()
                if a.get("access_token") and a.get("status") == "connected"]
    live: list[tuple] = []
    for acc in accounts:
        st: dict = {}                                  # fresh state → never shared
        try:
            net_book = _refresh_positions(acc, st, now_s)
            live.append((acc, st, net_book))
            # Log today's fills too — the in-process daemon does this every 5 min,
            # but when the web app is asleep ONLY this cron runs, so without it
            # trades never get recorded (open positions/P&L capture, but the
            # trade history stays empty). trades() is light + the upsert is
            # idempotent (keyed on trade_id), so a per-minute sync is safe.
            _sync_trades(acc, st)
        except Exception as e:
            if fno_kite.is_token_error(e):
                store.update_account(acc["id"], {"status": "expired"})
                store.add_log(acc["id"], "token_expired", f"cron: {str(e)[:180]}")

    feed_id = store.get_price_feed_id()
    by_id = {a["id"]: s for a, s, _ in live}
    feed_st = (by_id.get(feed_id) if feed_id else None) or (live[0][1] if live else None)
    ltps = _fetch_shared_ltps(feed_st, live)
    outs = [_pnl_from(acc, net_book, ltps) for acc, st, net_book in live]

    day = _live_snapshot_date(now)               # 9AM-anchored trading day
    captured = 0
    for o in outs:
        open_now = any(v.get("open_positions", 0) > 0 for v in o["by_strategy"].values())
        if open_now:
            _snapshot(o, now)                          # minute snapshot + live daily row
            captured += 1
        else:
            # flat → no minute snapshot, but STILL record the day's realised (from
            # fills — a full intraday round-trip leaves day_pnl≈0 and Kite realised
            # =0, so the old has_pnl guard skipped it and Booked today read 0).
            _write_daily(o, day)
    _snapshot_open_series(now)
    return {"market_open": True, "accounts": len(outs), "captured": captured,
            "total_day_pnl": round(sum(o["day_pnl"] for o in outs), 2), "ts": now.isoformat()}
