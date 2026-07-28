"""
Dividend ingester — turns NSE's declared corporate actions into pending
dividend entries on your calendar, and keeps them consistent with holdings.

Rules (why each exists):
  • Only for stocks you HOLD — amount = declared ₹/share × shares you own.
  • No overlap — an existing entry for the same (symbol, ex-date) is never
    duplicated; if you already hold a manual one, we leave it alone.
  • Correct as holdings move — a still-declared, still-held auto entry whose
    share count drifted (you bought/sold some) is updated to the current qty.
  • Consistent on sell — if you no longer hold a symbol, its *pending* auto
    entries are removed; ones already marked received are kept as real history.

Only rows WE created are ever pruned/updated — identified by the `_AUTO_TAG`
marker in the note. Your hand-entered dividends are never touched.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from . import nse, bse

_AUTO_TAG = "auto:nse-corp-action"
_OVERLAP_DAYS = 7          # same-symbol dividend within a week = the same event


def _d(s: str) -> Optional[date]:
    try:
        return datetime.strptime((s or "")[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _held_map() -> dict[str, dict]:
    """{SYMBOL: {qty, name}} across all held accounts (merged per symbol)."""
    try:
        from ..portfolio import engine as eq_engine
        stocks = eq_engine.build_summary().get("stocks", [])
    except Exception:
        return {}
    out: dict[str, dict] = {}
    for s in stocks:
        sym = (s.get("symbol") or "").strip().upper()
        qty = float(s.get("quantity") or 0)
        if not sym or qty <= 0:
            continue
        out[sym] = {"qty": qty, "name": s.get("name") or sym}
    return out


def _is_auto(row: dict) -> bool:
    return _AUTO_TAG in (row.get("note") or "")


def _merge_declared(*sources: list) -> list[dict]:
    """Merge declared dividends from several exchanges, one entry per
    (symbol, ex-date). Earlier sources win; a later source only FILLS a gap
    (e.g. a BSE-only stock, or a name NSE hasn't published yet)."""
    seen: dict = {}
    out: list[dict] = []
    for src in sources:
        for d in src or []:
            key = ((d.get("symbol") or "").upper(), (d.get("ex_date") or "")[:10])
            if not key[0] or not key[1] or key in seen:
                continue
            seen[key] = True
            out.append(d)
    return out


def sync(dry_run: bool = False, days_ahead: int = 60) -> dict:
    """Fetch declared dividends from NSE + BSE, then ingest them (add/update/
    prune). Either exchange may be blocked on a given run (returns []); the other
    still covers its names. Returns a summary of exactly what changed."""
    declared = _merge_declared(
        nse.fetch_dividends(days_ahead=days_ahead),
        bse.fetch_dividends(days_ahead=days_ahead),
    )
    return ingest_declared(declared, dry_run=dry_run)


def ingest_declared(declared: list[dict], *, dry_run: bool = False,
                    tag: str = _AUTO_TAG, prune: bool = True) -> dict:
    """Ingest a list of declared dividends (from NSE or an uploaded CSV): add the
    new ones for held stocks, refresh share counts on entries we created, and —
    when ``prune`` — drop pending auto entries for stocks you've since exited.

    ``tag`` marks the rows we own (so re-runs update rather than duplicate, and
    only our own rows are ever pruned). A CSV upload passes ``prune=False`` so a
    one-off file never deletes anything you didn't upload.

    Returns a summary incl. per-symbol ``added``/``updated`` details and every
    declared row that DIDN'T match a holding (``unheld``), so the caller can show
    exactly what a file did and didn't touch."""
    from ..portfolio import dividends as dv

    def _is_ours(row: dict) -> bool:
        return tag in (row.get("note") or "")

    held = _held_map()
    existing = dv.list_dividends()

    # index existing dividends per symbol → [(date, row)] for a windowed overlap check
    by_sym: dict[str, list] = {}
    for r in existing:
        by_sym.setdefault((r.get("symbol") or "").upper(), []).append((_d(r.get("date")), r))

    def _overlapping(sym: str, ex: date):
        """An existing dividend for `sym` within ±_OVERLAP_DAYS of `ex`, if any."""
        best = None
        for dt, row in by_sym.get(sym, []):
            if dt and abs((dt - ex).days) <= _OVERLAP_DAYS:
                best = row
        return best

    added, updated, existing, skipped, unheld = [], [], [], 0, 0
    today = date.today()
    now_iso = datetime.now().isoformat()

    for d in declared:
        sym = d["symbol"]
        h = held.get(sym)
        if not h:
            unheld += 1                    # declared, but not a stock you hold
            continue
        ex = _d(d["ex_date"])
        if not ex or ex < today:
            continue                       # already gone ex — not a future reminder
        qty = h["qty"]
        cur = _overlapping(sym, ex)
        if cur:
            # Already on your calendar (same event). Report it as "already there",
            # carrying WHEN it was first pulled so the UI can say since when.
            first_seen = cur.get("synced_at")
            base = {"symbol": sym, "name": cur.get("name") or h["name"], "date": d["ex_date"],
                    "per_share": d["per_share"], "shares": qty,
                    "amount": round(d["per_share"] * qty, 2), "subject": d.get("subject", ""),
                    "synced_at": first_seen}
            if _is_ours(cur):
                prev_shares = float(cur.get("shares") or 0)
                # refresh share count on OUR entries when your holding drifted; also
                # backfill synced_at for rows pulled before this was tracked.
                patch = {}
                if prev_shares != qty:
                    patch["shares"], patch["per_share"] = qty, d["per_share"]
                if not first_seen:
                    patch["synced_at"] = first_seen = now_iso
                    base["synced_at"] = first_seen
                if patch and not dry_run:
                    dv.update_dividend(cur["id"], patch)
                if prev_shares != qty:
                    updated.append({**base, "prev_shares": prev_shares, "source": "auto"})
                else:
                    existing.append({**base, "source": "auto"})
            else:
                skipped += 1               # your own hand-entered dividend — never touched
                existing.append({**base, "per_share": cur.get("per_share"),
                                 "shares": cur.get("shares"), "amount": cur.get("amount"),
                                 "source": "manual"})
            continue
        payload = {
            "date": d["ex_date"], "symbol": sym, "name": h["name"],
            "per_share": d["per_share"], "shares": qty, "currency": "INR",
            "status": "pending", "synced_at": now_iso,
            "note": f"{tag} · {d['subject']}" + (f" · rec {d['record_date']}" if d.get("record_date") else ""),
        }
        if not dry_run:
            dv.add_dividend(payload)
        added.append({"symbol": sym, "name": h["name"], "date": d["ex_date"], "per_share": d["per_share"],
                      "shares": qty, "amount": round(d["per_share"] * qty, 2),
                      "subject": d.get("subject", ""), "synced_at": now_iso})

    # prune: our PENDING entries for symbols we no longer hold (kept if received).
    # Skipped for one-off CSV uploads (prune=False) so a file never deletes rows.
    pruned = []
    if prune:
        for r in existing:
            if not _is_ours(r):
                continue
            if r.get("status") == "pending" and (r.get("symbol") or "").upper() not in held:
                if not dry_run:
                    dv.delete_dividend(r["id"])
                pruned.append({"symbol": r.get("symbol"), "date": r.get("date"),
                               "amount": r.get("amount")})

    return {
        "ok": True, "dry_run": dry_run,
        "declared_count": len(declared), "held_symbols": len(held),
        "added": added, "added_count": len(added),
        "updated": updated, "updated_count": len(updated),
        "existing": existing, "existing_count": len(existing),
        "matched": added + updated, "matched_count": len(added) + len(updated),
        "skipped_existing": skipped, "unheld_count": unheld,
        "pruned": pruned, "pruned_count": len(pruned),
        "source_reachable": len(declared) > 0,
    }
