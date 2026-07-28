"""Open-position mark-to-market for the F&O book (carry-forward, not realised).

When the tradebook changes, some legs may still be OPEN (not yet closed). Their
profit is NOT booked — it is *unrealized* and must be carried forward, never
counted in any single day's realised P&L (that lands when the leg is finally
closed, handled by pnl.rebuild_daily_from_trades). This module:

  1. replays every fill (average-cost netting, identical to the daily rebuild)
     to find the legs still open, per account + instrument;
  2. fetches a live LTP for each through the ONE paid price-feed account
     (LTP is account-agnostic, so a single paid app prices every account);
  3. marks each open leg to market so the user always sees where their open
     positions stand — separate from booked P&L.

Unrealized = signed_qty × (ltp − avg) × contract_multiplier — the SAME ₹/point
factor the realised rebuild uses (kite.contract_multiplier). For NFO/BFO options
that factor is 1 (quantity already in full units); for MCX commodities it's the
lot size (crude = 100), which Kite hides everywhere but the live `multiplier`.
Skipping it made crude open-P&L 100× too small (and inconsistent with the chart).
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Optional

from . import store
from . import kite as fno_kite

# ── Option-expiry parsing (Zerodha F&O symbols) ───────────────────────────────
# Monthly  : NAME + YY + MMM + STRIKE + CE/PE   (e.g. NIFTY26DEC22000PE)
# Weekly   : NAME + YY + M + DD + STRIKE + CE/PE (M = 1-9 / O,N,D)  (NIFTY2631024300PE)
_MONTHS = {"JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
           "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12}
_WK = {"1": 1, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7, "8": 8, "9": 9,
       "O": 10, "N": 11, "D": 12}


def _last_thursday(year: int, month: int) -> date:
    d = date(year, 12, 31) if month == 12 else date(year, month + 1, 1) - timedelta(days=1)
    while d.weekday() != 3:      # 3 = Thursday
        d -= timedelta(days=1)
    return d


def option_expiry(sym: str) -> Optional[date]:
    """Best-effort expiry date from an F&O tradingsymbol (None if unparseable)."""
    s = (sym or "").upper()
    if s.endswith("CE") or s.endswith("PE"):
        s = s[:-2]
    elif s.endswith("FUT"):
        s = s[:-3]
    m = re.match(r"^[A-Z]+(\d{2})(.*)$", s)
    if not m:
        return None
    year = 2000 + int(m.group(1))
    rest = m.group(2)
    if rest[:3] in _MONTHS:                                  # monthly
        return _last_thursday(year, _MONTHS[rest[:3]])
    if rest[:1] in _WK and rest[1:3].isdigit():              # weekly
        try:
            return date(year, _WK[rest[0]], int(rest[1:3]))
        except ValueError:
            return None
    return None


def is_expired(sym: str, today: Optional[date] = None) -> bool:
    exp = option_expiry(sym)
    return bool(exp) and exp < (today or date.today())


# ── Full symbol parse (root · expiry · strike · type) for payoff charts ────────
# Index spot instrument keys on the Kite feed (best-effort; unknown roots fall
# back to a future leg's own LTP or put-call parity — see underlying_spots).
_INDEX_SPOT = {
    "NIFTY":      "NSE:NIFTY 50",
    "BANKNIFTY":  "NSE:NIFTY BANK",
    "FINNIFTY":   "NSE:NIFTY FIN SERVICE",
    "MIDCPNIFTY": "NSE:NIFTY MID SELECT",
    "NIFTYNXT50": "NSE:NIFTY NEXT 50",
    "SENSEX":     "BSE:SENSEX",
    "BANKEX":     "BSE:BANKEX",
}


def parse_symbol(sym: str) -> dict:
    """Root, expiry, strike & option type from an F&O tradingsymbol.

    Handles Zerodha monthly (NIFTY26DEC22000PE) and weekly (NIFTY2631024300PE)
    option formats plus futures (…FUT). The date portion is consumed FIRST so
    the trailing digits are unambiguously the strike (a naive `\\d+CE$` regex
    would swallow the weekly date into the strike). Unparseable parts → None."""
    s = (sym or "").upper()
    opt: Optional[str] = None
    body = s
    if s.endswith("CE"):
        opt, body = "CE", s[:-2]
    elif s.endswith("PE"):
        opt, body = "PE", s[:-2]
    elif s.endswith("FUT"):
        opt, body = "FUT", s[:-3]
    lead = re.match(r"^([A-Z]+)", s)
    out = {"root": (lead.group(1) if lead else s), "expiry": None,
           "strike": None, "opt_type": opt}
    m = re.match(r"^([A-Z]+)(\d{2})(.*)$", body)
    if not m:
        return out
    out["root"] = m.group(1)
    year = 2000 + int(m.group(2))
    rest = m.group(3)
    strike_str = rest
    if rest[:3] in _MONTHS:                                  # monthly
        try:
            out["expiry"] = _last_thursday(year, _MONTHS[rest[:3]]).isoformat()
        except Exception:
            pass
        strike_str = rest[3:]
    elif rest[:1] in _WK and rest[1:3].isdigit():            # weekly
        try:
            out["expiry"] = date(year, _WK[rest[0]], int(rest[1:3])).isoformat()
        except Exception:
            pass
        strike_str = rest[3:]
    if opt in ("CE", "PE") and strike_str:
        try:
            out["strike"] = float(strike_str)
        except ValueError:
            pass
    return out


def underlying_spots(rows: list[dict]) -> dict:
    """Best-effort current underlying price per root (for payoff diagrams).

    Priority: a FUT leg's own LTP → the index spot from the feed → put-call
    parity S ≈ K + C − P from any matched call/put at one strike. Roots we can't
    price are simply absent (the chart then centres on the strikes)."""
    spots: dict[str, float] = {}
    roots = {r.get("root") for r in rows if r.get("root")}

    # 1) a future leg prices its own underlying directly
    for r in rows:
        if r.get("opt_type") == "FUT" and r.get("ltp") is not None and r.get("root"):
            spots.setdefault(r["root"], float(r["ltp"]))

    # 2) index spots straight from the price feed
    idx = {root: _INDEX_SPOT[root] for root in (roots - set(spots)) if root in _INDEX_SPOT}
    if idx:
        k = _feed_client()
        if k:
            try:
                raw = k.ltp(list(idx.values()))
                inv = {key: root for root, key in idx.items()}
                for kk, v in (raw or {}).items():
                    lp = v.get("last_price")
                    root = inv.get(kk)
                    if lp is not None and root:
                        spots.setdefault(root, float(lp))
            except Exception:
                pass

    # 3) put-call parity from a call+put at the same strike
    for root in (roots - set(spots)):
        by_strike: dict = {}
        for r in rows:
            if r.get("root") != root or r.get("strike") is None or r.get("ltp") is None:
                continue
            by_strike.setdefault(r["strike"], {})[r.get("opt_type")] = r["ltp"]
        for K, cp in by_strike.items():
            if "CE" in cp and "PE" in cp:
                spots[root] = round(float(K) + cp["CE"] - cp["PE"], 2)
                break
    return spots


def compute_open_legs(account_id: Optional[str] = None) -> list[dict]:
    """Replay fills (average-cost, same matching as pnl.rebuild_daily_from_trades)
    and return the legs still open. Each carries signed qty + average cost."""
    trades = store.all_trades()
    if account_id:
        trades = [t for t in trades if t.get("account_id") == account_id]
    trades.sort(key=lambda t: (t.get("fill_ts") or t.get("trade_date") or ""))

    pos: dict = {}   # (account_id, tradingsymbol) -> position
    for t in trades:
        acc = t.get("account_id")
        sym = t.get("tradingsymbol") or ""
        key = (acc, sym)
        qty = float(t.get("quantity") or 0)
        price = float(t.get("price") or 0)
        side = 1.0 if (t.get("transaction_type") or "").upper() == "BUY" else -1.0
        signed = qty * side

        p = pos.get(key)
        if p is None:
            p = pos[key] = {"qty": 0.0, "avg": 0.0, "exchange": None,
                            "instrument_type": None, "strategy": "other"}
        # keep the latest known metadata for the instrument
        if t.get("exchange"):
            p["exchange"] = t.get("exchange")
        if t.get("instrument_type"):
            p["instrument_type"] = t.get("instrument_type")
        if t.get("strategy"):
            p["strategy"] = t.get("strategy")

        if p["qty"] == 0 or (p["qty"] > 0) == (signed > 0):
            # opening / extending → weighted average
            total = abs(p["qty"]) + qty
            p["avg"] = (abs(p["qty"]) * p["avg"] + qty * price) / total if total else 0.0
            p["qty"] += signed
            continue
        # reducing / flipping → the closed part is realised elsewhere (daily rebuild)
        p["qty"] += signed
        if (p["qty"] > 0) == (signed > 0) and p["qty"] != 0:
            p["avg"] = price          # flipped — remainder opens at this price
        elif p["qty"] == 0:
            p["avg"] = 0.0

    overrides = store.get_account_strategies()   # per-account strategy pins
    leg_pins = store.get_leg_strategies()        # per-leg overrides (open positions)
    legs: list[dict] = []
    for (acc, sym), p in pos.items():
        if abs(p["qty"]) <= 1e-9:
            continue
        legs.append({
            "account_id": acc, "tradingsymbol": sym,
            "exchange": (p["exchange"] or "NFO"),
            "instrument_type": (p["instrument_type"] or ""),
            "strategy": store.resolve_leg_strategy(p["strategy"] or "other", acc, sym, overrides, leg_pins),
            "qty": round(p["qty"], 4), "avg": round(p["avg"], 4),
        })
    return legs


def _feed_client():
    """Kite session used to price open legs. Prefers the designated paid
    price-feed account (market-data access), then any connected account, then
    the app-wide env session. LTP is account-agnostic so any works."""
    tried: set = set()
    ordered: list[dict] = []
    fid = store.get_price_feed_id()
    if fid:
        a = store.get_account(fid)
        if a:
            ordered.append(a)
    for a in store.list_accounts():
        if a.get("access_token") and a.get("status") == "connected":
            ordered.append(a)
    for a in ordered:
        aid = a.get("id")
        if aid in tried:
            continue
        tried.add(aid)
        try:
            return fno_kite.client(a)
        except Exception:
            continue
    try:
        from .. import state
        return state.get_kite()
    except Exception:
        return None


def fetch_ltps(legs: list[dict]) -> dict:
    """One batched LTP call for every open leg → {tradingsymbol: ltp}. Never raises."""
    if not legs:
        return {}
    k = _feed_client()
    if not k:
        return {}
    keys = list({f"{leg['exchange']}:{leg['tradingsymbol']}" for leg in legs})
    out: dict[str, float] = {}
    try:
        raw = k.ltp(keys)
        for kk, v in (raw or {}).items():
            lp = v.get("last_price")
            if lp is not None:
                out[kk.split(":", 1)[1]] = float(lp)
    except Exception:
        pass
    return out


def _net_open_rows(want: Optional[set], accts: dict) -> tuple[list[dict], set]:
    """Open positions read DIRECTLY from each connected account's Kite NET book.

    Used when the engine's live snapshot is empty (market shut / hasn't run). The
    net book is Kite's persistent open positions and is readable outside market
    hours, so this surfaces a genuinely-open leg even on a weekend and even if its
    opening trade was never imported into fno_trades. Marked to market with the
    net book's own last_price; day P&L is 0 off-hours (no intraday movement)."""
    rows: list[dict] = []
    covered: set = set()
    for aid, acc in accts.items():
        if want is not None and aid not in want:
            continue
        if not (acc.get("api_key") and acc.get("access_token")):
            continue                                   # not connected — nothing to read
        try:
            k = fno_kite.client(acc)
            book = fno_kite.fetch_positions(k)
        except Exception:
            continue                                   # a bad token for one account must not sink the others
        # Reaching Kite IS the source of truth for this account — mark it covered even
        # if the book is empty, so the history fallback can't resurrect stale/expired
        # legs Kite has already dropped. (A failed read above stays uncovered → history
        # still carries it forward.)
        covered.add(aid)
        for p in book.get("net", []):
            qty = float(p.get("quantity") or 0)
            if not qty:                                # net book: only actually-open legs
                continue
            avg = float(p.get("average_price") or 0)
            lp = p.get("last_price")
            ltp = float(lp) if lp not in (None, "") else None
            sym = p.get("tradingsymbol") or ""
            ex = p.get("exchange") or "NFO"
            mult = fno_kite.contract_multiplier(ex, sym)
            itype = p.get("instrument_type") or ("CE" if sym.endswith("CE") else "PE" if sym.endswith("PE") else "")
            unreal = (round(qty * (ltp - avg) * mult, 2) if (qty and ltp is not None) else 0.0)
            rows.append({
                "account_id": aid,
                "account_label": acc.get("account_label") or acc.get("kite_user_id") or "—",
                "person": acc.get("person"),
                "tradingsymbol": sym, "exchange": ex,
                "instrument_type": itype, "strategy": p.get("strategy") or "other",
                "side": "LONG" if qty > 0 else "SHORT" if qty < 0 else "CLOSED",
                "qty": qty, "avg": round(avg, 2),
                "ltp": (round(ltp, 2) if ltp is not None else None),
                "invested": round(abs(qty) * avg * mult, 2),
                "unrealized": unreal,
                "day_pnl": None,                        # market shut → no live day P&L
                "realized": 0.0,
                "closed": False,
                "live": True,
            })
    return rows, covered


def _live_open_rows(want: Optional[set], accts: dict) -> tuple[list[dict], set]:
    """Open positions straight from the LIVE engine (Kite's own positions() book),
    which is the ground truth for what's actually open right now. Returns the rows
    plus the set of accounts it covered. Empty when the market is shut / the engine
    hasn't run — callers then fall back to the trade-history book below.

    This is what keeps the Open Positions card consistent with the live P&L: an
    account (e.g. one whose past trades were never imported) can have a real open
    Kite book generating live P&L but ZERO rows in fno_trades — the history book
    can't see it, but the engine can, so we surface it here."""
    rows: list[dict] = []
    covered: set = set()
    try:
        from . import engine as fno_engine
        live = fno_engine.latest() or {}
    except Exception:
        live = {}

    # The engine only refreshes its snapshot while the market is open. Outside those
    # hours (evenings, weekends) that snapshot is empty — but Kite's NET positions
    # book is the persistent open book and is readable any time. So when the engine
    # has nothing, read each connected account's net book DIRECTLY from Kite. This is
    # what keeps a genuinely-open position visible when the market is shut, even if it
    # was never imported into the trade history.
    if not live.get("market_open") or not live.get("accounts"):
        return _net_open_rows(want, accts)

    for acc_out in live.get("accounts", []):
        aid = acc_out.get("id")
        if want is not None and aid not in want:
            continue
        acc = accts.get(aid) or {}
        for p in acc_out.get("positions", []):
            qty = float(p.get("quantity") or 0)
            m2m = float(p.get("m2m") or 0)              # Kite's live day P&L for the leg
            # keep OPEN legs (qty≠0) and legs CLOSED today that still booked P&L
            # (qty=0 but m2m≠0) — Kite's Positions screen lists these too, and
            # they're what make the card's total reconcile with the live day P&L.
            if not qty and abs(m2m) < 0.005:
                continue
            covered.add(aid)
            avg = float(p.get("average_price") or 0)
            lp = p.get("last_price")
            ltp = float(lp) if lp not in (None, "") else None
            sym = p.get("tradingsymbol") or ""
            ex = p.get("exchange") or "NFO"
            mult = fno_kite.contract_multiplier(ex, sym)
            itype = p.get("instrument_type") or ("CE" if sym.endswith("CE") else "PE" if sym.endswith("PE") else "")
            # pure carry-forward unrealized = mark-to-market of the NET open qty
            # only (0 for a leg closed today). Kept for the strategy carry-forward
            # math; the CARD shows day_pnl (below) so it matches Kite.
            unreal = (round(qty * (ltp - avg) * mult, 2) if (qty and ltp is not None) else 0.0)
            rows.append({
                "account_id": aid,
                "account_label": acc.get("account_label") or acc.get("kite_user_id") or "—",
                "person": acc.get("person"),
                "tradingsymbol": sym, "exchange": ex,
                "instrument_type": itype, "strategy": p.get("strategy") or "other",
                "side": "LONG" if qty > 0 else "SHORT" if qty < 0 else "CLOSED",
                "qty": qty, "avg": round(avg, 2),
                "ltp": (round(ltp, 2) if ltp is not None else None),
                "invested": round(abs(qty) * avg * mult, 2),
                "unrealized": unreal,
                "day_pnl": round(m2m, 2),                # Kite's live P&L for the leg (= chart & terminal)
                "realized": round(m2m - unreal, 2),      # intraday booked component (round-trips today)
                "closed": qty == 0,
                "live": True,
            })
    return rows, covered


def open_positions(account_ids: Optional[list[str]] = None) -> dict:
    """The open book, marked to market. Prefers the LIVE Kite positions (the real
    open book, whatever's actually open right now) and falls back to the imported
    trade-history book only for accounts the live engine isn't currently covering
    (market shut, or a not-connected account). Never touches realised/daily P&L."""
    want = set(account_ids) if account_ids else None
    today = date.today()
    accts = {a.get("id"): a for a in store.list_accounts()}

    # 1) live open book (ground truth) — keeps the card consistent with live P&L
    live_rows, covered = _live_open_rows(want, accts)

    # 2) trade-history carry-forward, only for accounts NOT already covered live
    legs = [leg for leg in compute_open_legs()
            if leg["account_id"] not in covered
            and (want is None or leg["account_id"] in want)]
    live_legs = [leg for leg in legs if not is_expired(leg["tradingsymbol"], today)]
    expired_count = len(legs) - len(live_legs)
    legs = live_legs
    ltps = fetch_ltps(legs)

    hist_rows: list[dict] = []
    for leg in legs:
        acc = accts.get(leg["account_id"]) or {}
        ltp = ltps.get(leg["tradingsymbol"])
        qty = leg["qty"]
        avg = leg["avg"]
        hist_rows.append({
            "account_id": leg["account_id"],
            "account_label": acc.get("account_label") or acc.get("kite_user_id") or "—",
            "person": acc.get("person"),
            "tradingsymbol": leg["tradingsymbol"], "exchange": leg["exchange"],
            "instrument_type": leg["instrument_type"], "strategy": leg["strategy"],
            "side": "LONG" if qty > 0 else "SHORT", "qty": qty, "avg": round(avg, 2),
            "ltp": (round(ltp, 2) if ltp is not None else None),
            "invested": round(abs(qty) * avg * fno_kite.contract_multiplier(leg["exchange"], leg["tradingsymbol"]), 2),
            "unrealized": (round(qty * (ltp - avg) * fno_kite.contract_multiplier(leg["exchange"], leg["tradingsymbol"]), 2) if ltp is not None else None),
            "day_pnl": None,        # market shut → no live day P&L; this IS the carry-forward unrealized
            "realized": 0.0, "closed": False, "live": False,
        })

    rows = live_rows + hist_rows
    # enrich every row with parsed instrument geometry so the frontend can build
    # the payoff diagram (strike/type/expiry) and scale it (₹-per-point multiplier)
    for r in rows:
        meta = parse_symbol(r["tradingsymbol"])
        r["root"] = meta["root"]
        r["strike"] = meta["strike"]
        r["opt_type"] = meta["opt_type"]
        r["expiry"] = meta["expiry"]
        r["multiplier"] = fno_kite.contract_multiplier(r["exchange"], r["tradingsymbol"])
    total_unreal = round(sum(r["unrealized"] for r in rows if r["unrealized"] is not None), 2)
    total_invested = round(sum(r["invested"] for r in rows), 2)
    # LIVE day P&L across the live legs (matches the chart & Kite's Positions total);
    # None when no account is covered live (pure carry-forward / market shut).
    live_rows_present = any(r.get("live") for r in rows)
    total_day_pnl = (round(sum(r["day_pnl"] for r in rows if r.get("day_pnl") is not None), 2)
                     if live_rows_present else None)
    priced = sum(1 for r in rows if r["unrealized"] is not None)
    # live view → order by day-P&L magnitude (matches how you read Kite); carried →
    # by unrealized. Open legs before closed-today legs before unpriced.
    rows.sort(key=lambda r: (r["unrealized"] is None, r.get("closed", False),
                             -abs((r.get("day_pnl") if r.get("live") else r["unrealized"]) or 0)))

    by_strategy: dict = {}
    for r in rows:
        b = by_strategy.setdefault(r["strategy"], {"strategy": r["strategy"],
                                                   "unrealized": 0.0, "day_pnl": 0.0, "count": 0})
        b["count"] += 1
        if r["unrealized"] is not None:
            b["unrealized"] += r["unrealized"]
        if r.get("day_pnl") is not None:
            b["day_pnl"] += r["day_pnl"]

    return {
        "positions": rows,
        "count": len(rows),
        "priced_count": priced,
        "unpriced_count": len(rows) - priced,
        "total_unrealized": round(total_unreal, 2),
        "total_day_pnl": total_day_pnl,       # live positions P&L (matches Kite) or None
        "live_mode": live_rows_present,       # true → card mirrors Kite's live Positions
        "total_invested": round(total_invested, 2),
        "by_strategy": [{"strategy": k, "unrealized": round(v["unrealized"], 2),
                         "day_pnl": round(v["day_pnl"], 2), "count": v["count"]}
                        for k, v in by_strategy.items()],
        "feed_ok": (priced > 0) or (len(rows) == 0),
        "expired_count": expired_count,       # expired legs hidden from the live view
        "spots": underlying_spots(rows),      # best-effort underlying price per root (payoff x-axis)
    }
