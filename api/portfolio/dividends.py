"""
Dividend log — each row is one dividend event for a stock on a date
(``per_share`` × ``shares`` = ``amount``). Powers the calendar + monthly totals
on the Stocks tab, and the per-stock dividend panel on the holdings page.

  stock_dividends      one row per dividend. ``received`` distinguishes booked
                       credits (green on the calendar) from expected/forecast
                       ones you haven't been paid yet (yellow).
  stock_dividend_meta  per-symbol extras — currently a manual "received in
                       previous years" override that wins over the auto sum.

Supabase-backed with a JSON-file fallback, mirroring `portfolio/store.py`.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime
from typing import Optional

from . import store          # reuse the same Supabase client + env handling

DIV_TABLE = "stock_dividends"
META_TABLE = "stock_dividend_meta"

_DIV_FILE = os.path.join(store._DATA_DIR, "dividends.json")
_META_FILE = os.path.join(store._DATA_DIR, "dividend_meta.json")

_FIELDS = ("date", "symbol", "name", "per_share", "shares", "amount",
           "received", "received_at", "status", "currency", "person", "account_id", "note",
           "synced_at")   # when this row was first pulled from an exchange page (auto-sync/CSV)

# Three lifecycle states. ``received`` (bool) is kept in sync as a mirror so
# every existing caller/column keeps working; ``status`` carries the 3rd state.
_STATUSES = ("pending", "received", "not_received")


def _now() -> str:
    return datetime.now().isoformat()


def _f(v, d: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def _b(v, d: bool = True) -> bool:
    """Coerce a JSON-ish truthy/falsy value to bool (default for missing)."""
    if v is None:
        return d
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "y", "t")
    return bool(v)


def _coerce_status(v, received_fallback=None) -> str:
    """Normalize to one of _STATUSES. Legacy rows (no status) derive it from the
    old ``received`` boolean: received → 'received', otherwise → 'pending'."""
    s = str(v).strip().lower() if v is not None else ""
    if s in _STATUSES:
        return s
    return "received" if _b(received_fallback, False) else "pending"


# Whether the Supabase table actually has a ``status`` column. If the migration
# hasn't been run we transparently fall back to the ``received`` boolean instead
# of crashing, so nothing breaks and the 3rd state simply degrades to pending
# until the column is added. (On JSON fallback anything persists → always True.)
_status_col: Optional[bool] = None


def _has_status() -> bool:
    # Only the positive result is cached. A negative is left un-cached so that
    # adding the column later (via the migration) is picked up on the very next
    # write — no backend restart needed, no stale "column absent" verdict.
    global _status_col
    if _status_col:
        return True
    client = store._get_client()
    if not client:
        _status_col = True
        return True
    try:
        client.table(DIV_TABLE).select("status").limit(1).execute()
        _status_col = True
        return True
    except Exception:
        return False


_currency_col: Optional[bool] = None


def _has_currency() -> bool:
    global _currency_col
    if _currency_col:
        return True
    client = store._get_client()
    if not client:
        _currency_col = True
        return True
    try:
        client.table(DIV_TABLE).select("currency").limit(1).execute()
        _currency_col = True
        return True
    except Exception:
        return False


_synced_col: Optional[bool] = None


def _has_synced_at() -> bool:
    """Whether the ``synced_at`` column (when a row was pulled from an exchange
    page) exists. Degrades like status/currency: absent → silently dropped."""
    global _synced_col
    if _synced_col:
        return True
    client = store._get_client()
    if not client:
        _synced_col = True
        return True
    try:
        client.table(DIV_TABLE).select("synced_at").limit(1).execute()
        _synced_col = True
        return True
    except Exception:
        return False


def _for_db(row: dict) -> dict:
    """Drop optional columns (status/currency/synced_at) before writing if not migrated yet."""
    out = dict(row)
    if "status" in out and not _has_status():
        del out["status"]
    if "currency" in out and not _has_currency():
        del out["currency"]
    if "synced_at" in out and not _has_synced_at():
        del out["synced_at"]
    return out


def _norm_currency(c) -> str:
    return "USD" if str(c or "INR").upper() == "USD" else "INR"


def _amount_inr(per_share, shares, currency) -> float:
    """₹ amount used for all aggregation. A USD dividend is converted at the
    current rate; ``per_share`` stays native so the original $ figure is still
    recoverable for display."""
    native = _f(per_share) * _f(shares)
    if _norm_currency(currency) == "USD":
        try:
            from api.salary import fx as fx_mod
            return round(native * fx_mod.inr_per_unit("USD"), 2)
        except Exception:
            return round(native, 2)
    return round(native, 2)


# ── Dividend events ──────────────────────────────────────────────────────────────
def list_dividends() -> list[dict]:
    client = store._get_client()
    if client:
        try:
            return [_normalize(r) for r in store._fetch_all(client, DIV_TABLE)]
        except Exception:
            return []
    return [_normalize(r) for r in store._read(_DIV_FILE)]


def _normalize(row: dict) -> dict:
    """Every row carries a 3-state ``status`` (pending | received | not_received).
    New rows default to pending; ``received`` mirrors status for back-compat."""
    st = _coerce_status(row.get("status"), row.get("received"))
    row["status"] = st
    row["received"] = (st == "received")
    row["currency"] = _norm_currency(row.get("currency"))
    row.setdefault("received_at", None)
    row.setdefault("synced_at", None)
    return row


def add_dividend(payload: dict) -> dict:
    row = {k: payload.get(k) for k in _FIELDS if k in payload}
    row["per_share"] = _f(row.get("per_share"))
    row["shares"] = _f(row.get("shares"))
    row["currency"] = _norm_currency(row.get("currency"))
    # amount is authoritative server-side, always in ₹ (USD dividends converted)
    row["amount"] = _amount_inr(row["per_share"], row["shares"], row["currency"])
    row["symbol"] = (row.get("symbol") or "").strip().upper()
    row["date"] = (row.get("date") or _now()[:10])[:10]
    st = _coerce_status(row.get("status"), row.get("received"))   # new entries → pending
    row["status"] = st
    row["received"] = (st == "received")
    row["received_at"] = _now() if row["received"] else None
    row["id"] = uuid.uuid4().hex[:12]
    row["created_at"] = _now()
    client = store._get_client()
    if client:
        client.table(DIV_TABLE).insert(_for_db(row)).execute()
    else:
        data = store._read(_DIV_FILE)
        data.append(row)
        store._write(_DIV_FILE, data)
    return _normalize(row)


def set_received(div_id: str, received: bool) -> Optional[dict]:
    """Back-compat helper — flips between received and pending."""
    return update_dividend(div_id, {"status": "received" if received else "pending"})


def set_status(div_id: str, status: str) -> Optional[dict]:
    """Set the 3-state status (pending | received | not_received)."""
    return update_dividend(div_id, {"status": _coerce_status(status)})


_EDITABLE = ("date", "symbol", "name", "per_share", "shares", "received", "status",
             "currency", "person", "account_id", "note", "synced_at")


def update_dividend(div_id: str, patch: dict) -> Optional[dict]:
    """Edit any field of a dividend; re-derives amount and received_at."""
    cur = next((d for d in list_dividends() if d.get("id") == div_id), None)
    if not cur:
        return None
    fields = {k: patch[k] for k in _EDITABLE if k in patch}
    if "per_share" in fields:
        fields["per_share"] = _f(fields["per_share"])
    if "shares" in fields:
        fields["shares"] = _f(fields["shares"])
    if fields.get("symbol"):
        fields["symbol"] = fields["symbol"].strip().upper()
    if fields.get("date"):
        fields["date"] = str(fields["date"])[:10]
    # status is authoritative; keep the ``received`` mirror + timestamp in sync
    # whether the caller sent ``status`` (new) or ``received`` (legacy toggle).
    if "status" in fields or "received" in fields:
        st = _coerce_status(fields.get("status"), fields.get("received", cur.get("received")))
        fields["status"] = st
        fields["received"] = (st == "received")
        fields["received_at"] = _now() if fields["received"] else None
    if "currency" in fields:
        fields["currency"] = _norm_currency(fields["currency"])
    ps = fields.get("per_share", cur.get("per_share"))
    sh = fields.get("shares", cur.get("shares"))
    ccy = _norm_currency(fields.get("currency", cur.get("currency")))
    fields["amount"] = _amount_inr(ps, sh, ccy)
    client = store._get_client()
    if client:
        rows = client.table(DIV_TABLE).update(_for_db(fields)).eq("id", div_id).execute().data or []
        return _normalize(rows[0]) if rows else None
    data = store._read(_DIV_FILE)
    hit = None
    for d in data:
        if d.get("id") == div_id:
            d.update(fields)
            hit = d
    if hit:
        store._write(_DIV_FILE, data)
    return _normalize(hit) if hit else None


def delete_dividend(div_id: str) -> int:
    client = store._get_client()
    if client:
        res = client.table(DIV_TABLE).delete().eq("id", div_id).execute()
        return len(res.data or [])
    data = store._read(_DIV_FILE)
    keep = [d for d in data if d.get("id") != div_id]
    n = len(data) - len(keep)
    store._write(_DIV_FILE, keep)
    return n


def table_ready() -> bool:
    client = store._get_client()
    if not client:
        return True
    try:
        client.table(DIV_TABLE).select("id").limit(1).execute()
        return True
    except Exception:
        return False


# ── Per-symbol meta (manual "previous years" override) ───────────────────────────
def list_meta() -> list[dict]:
    client = store._get_client()
    if client:
        try:
            return store._fetch_all(client, META_TABLE)
        except Exception:
            return []
    return store._read(_META_FILE)


def set_meta(symbol: str, prev_years: Optional[float]) -> dict:
    """Upsert a symbol's manual prev-years figure; ``None`` clears the override."""
    sym = (symbol or "").strip().upper()
    row = {"symbol": sym,
           "prev_years": None if prev_years is None else _f(prev_years),
           "updated_at": _now()}
    client = store._get_client()
    if client:
        if prev_years is None:
            client.table(META_TABLE).delete().eq("symbol", sym).execute()
        else:
            client.table(META_TABLE).upsert(row, on_conflict="symbol").execute()
        return row
    data = [m for m in store._read(_META_FILE) if (m.get("symbol") or "").upper() != sym]
    if prev_years is not None:
        data.append(row)
    store._write(_META_FILE, data)
    return row


def meta_table_ready() -> bool:
    client = store._get_client()
    if not client:
        return True
    try:
        client.table(META_TABLE).select("symbol").limit(1).execute()
        return True
    except Exception:
        return False


# ── Per-person TDS rates (withholding differs by person) ─────────────────────────
TDS_TABLE = "dividend_tds"
_TDS_FILE = os.path.join(store._DATA_DIR, "dividend_tds.json")


def list_tds() -> list[dict]:
    """Each row: {person, rate}. An empty person ("") is the global default."""
    client = store._get_client()
    if client:
        try:
            return store._fetch_all(client, TDS_TABLE)
        except Exception:
            return []
    return store._read(_TDS_FILE)


def set_tds(person: str, rate: Optional[float]) -> dict:
    """Upsert a person's TDS rate (0–1). ``None`` clears it (reverts to default)."""
    p = (person or "").strip()
    row = {"person": p, "rate": None if rate is None else _f(rate), "updated_at": _now()}
    client = store._get_client()
    if client:
        if rate is None:
            client.table(TDS_TABLE).delete().eq("person", p).execute()
        else:
            client.table(TDS_TABLE).upsert(row, on_conflict="person").execute()
        return row
    data = [m for m in store._read(_TDS_FILE) if (m.get("person") or "") != p]
    if rate is not None:
        data.append(row)
    store._write(_TDS_FILE, data)
    return row


def tds_table_ready() -> bool:
    client = store._get_client()
    if not client:
        return True
    try:
        client.table(TDS_TABLE).select("person").limit(1).execute()
        return True
    except Exception:
        return False


# ── Per-(stock, person) collected override ───────────────────────────────────────
# The auto split-by-shares of a stock's dividend is an estimate; this lets you
# correct exactly what a given person collected for a given stock.
COLLECTED_TABLE = "dividend_collected"
_COLLECTED_FILE = os.path.join(store._DATA_DIR, "dividend_collected.json")


def list_collected() -> list[dict]:
    client = store._get_client()
    if client:
        try:
            return store._fetch_all(client, COLLECTED_TABLE)
        except Exception:
            return []
    return store._read(_COLLECTED_FILE)


def set_collected(symbol: str, person: str, collected: Optional[float]) -> dict:
    """Upsert a person's collected amount for a stock. ``None`` clears it."""
    sym = (symbol or "").strip().upper()
    per = (person or "").strip()
    row = {"symbol": sym, "person": per,
           "collected": None if collected is None else _f(collected),
           "updated_at": _now()}
    client = store._get_client()
    if client:
        if collected is None:
            client.table(COLLECTED_TABLE).delete().eq("symbol", sym).eq("person", per).execute()
        else:
            client.table(COLLECTED_TABLE).upsert(row, on_conflict="symbol,person").execute()
        return row
    data = [m for m in store._read(_COLLECTED_FILE)
            if not ((m.get("symbol") or "").upper() == sym and (m.get("person") or "") == per)]
    if collected is not None:
        data.append(row)
    store._write(_COLLECTED_FILE, data)
    return row


def collected_table_ready() -> bool:
    client = store._get_client()
    if not client:
        return True
    try:
        client.table(COLLECTED_TABLE).select("symbol").limit(1).execute()
        return True
    except Exception:
        return False
