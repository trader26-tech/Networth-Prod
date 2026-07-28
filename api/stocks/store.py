"""
Supabase store for parsed tradebook trades (JSON-file fallback for offline dev).

One flat table — `stock_trades` — one row per executed trade, tagged with an
`owner` (e.g. "Maha") and `account` (the broker client id). Re-uploading the
same tradebook is idempotent: we skip trade_ids already stored for that account.
Holdings / P&L / XIRR are computed on read by api/stocks/engine.py.
"""
from __future__ import annotations

import os
import json
import uuid
from datetime import datetime
from typing import Any, Optional

TABLE = "stock_trades"

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "stocks_data")
_TRADES_FILE = os.path.join(_DATA_DIR, "trades.json")

_client = None
_init_attempted = False
_tables_ok = False


def _read_env(key: str) -> str:
    val = os.environ.get(key, "").strip()
    if val:
        return val
    try:
        from dotenv import dotenv_values
        root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        env_path = os.path.join(root, ".env")
        if os.path.isfile(env_path):
            return (dotenv_values(env_path).get(key) or "").strip()
    except Exception:
        pass
    return ""


def _get_client():
    global _client, _init_attempted
    if _client is not None or _init_attempted:
        return _client
    _init_attempted = True
    url = _read_env("SUPABASE_URL").rstrip("/")
    key = _read_env("SUPABASE_SERVICE_KEY") or _read_env("SUPABASE_KEY")
    if not url or not key:
        return None
    try:
        from supabase import create_client
        _client = create_client(url, key)
        print(f"✓ Stocks store: Supabase active ({url})")
        return _client
    except Exception as e:
        print(f"⚠  Stocks store: Supabase init failed (using JSON): {e}")
        return None


def is_active() -> bool:
    return _get_client() is not None


def reset_client_cache():
    global _client, _init_attempted, _tables_ok
    _client = None
    _init_attempted = False
    _tables_ok = False


MIGRATION_HINT = (
    "Stock table not found in Supabase. Run the migration in SUPABASE.md "
    "(\"Stocks tradebook table\") once in the Supabase SQL editor to create "
    "stock_trades, then retry."
)


def tables_ready() -> bool:
    global _tables_ok
    if _tables_ok:
        return True
    client = _get_client()
    if not client:
        return True
    try:
        client.table(TABLE).select("id").limit(1).execute()
        _tables_ok = True
        return True
    except Exception:
        return False


# ── JSON fallback ──────────────────────────────────────────────────────────────
def _read_json() -> list:
    try:
        with open(_TRADES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _write_json(data: list) -> None:
    os.makedirs(_DATA_DIR, exist_ok=True)
    tmp = _TRADES_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)
    os.replace(tmp, _TRADES_FILE)


# ── API ────────────────────────────────────────────────────────────────────────
def add_trades(trades: list[dict], owner: str, account: str) -> dict:
    """Insert trades, skipping any trade_id already stored for this account."""
    owner = (owner or "default").strip() or "default"
    account = (account or "unknown").strip() or "unknown"
    now = datetime.now().isoformat()

    client = _get_client()
    existing: set[str] = set()
    if client:
        rows = client.table(TABLE).select("trade_id").eq("account", account).execute().data or []
        existing = {r["trade_id"] for r in rows if r.get("trade_id")}
    else:
        existing = {t["trade_id"] for t in _read_json()
                    if t.get("account") == account and t.get("trade_id")}

    rows_to_add: list[dict] = []
    for t in trades:
        tid = t.get("trade_id") or uuid.uuid4().hex[:16]
        if tid in existing:
            continue
        existing.add(tid)
        rows_to_add.append({
            "id": uuid.uuid4().hex[:12],
            "owner": owner, "account": account,
            "symbol": t["symbol"], "isin": t.get("isin"),
            "trade_date": t.get("trade_date"), "trade_type": t["trade_type"],
            "quantity": float(t["quantity"]), "price": float(t["price"]),
            "exchange": t.get("exchange"), "trade_id": tid,
            "order_time": t.get("order_time"), "created_at": now,
        })

    if rows_to_add:
        if client:
            for i in range(0, len(rows_to_add), 400):
                client.table(TABLE).insert(rows_to_add[i:i + 400]).execute()
        else:
            data = _read_json()
            data.extend(rows_to_add)
            _write_json(data)

    return {"added": len(rows_to_add), "skipped": len(trades) - len(rows_to_add),
            "owner": owner, "account": account}


def list_trades(owner: Optional[str] = None) -> list[dict]:
    client = _get_client()
    if client:
        q = client.table(TABLE).select("*")
        if owner:
            q = q.eq("owner", owner)
        # page through (Supabase caps at 1000/req)
        rows: list[dict] = []
        start = 0
        while True:
            res = q.range(start, start + 999).execute().data or []
            rows.extend(res)
            if len(res) < 1000:
                break
            start += 1000
        return rows
    data = _read_json()
    return [t for t in data if (not owner or t.get("owner") == owner)]


def owners() -> list[dict]:
    """Distinct owners with account + trade counts + date span."""
    rows = list_trades()
    agg: dict[str, dict] = {}
    for t in rows:
        o = t.get("owner") or "default"
        a = agg.setdefault(o, {"owner": o, "accounts": set(), "trades": 0, "from": None, "to": None})
        a["accounts"].add(t.get("account") or "?")
        a["trades"] += 1
        d = t.get("trade_date")
        if d:
            a["from"] = d if (a["from"] is None or d < a["from"]) else a["from"]
            a["to"] = d if (a["to"] is None or d > a["to"]) else a["to"]
    return [{**v, "accounts": sorted(v["accounts"])} for v in agg.values()]


def delete_owner(owner: str) -> int:
    client = _get_client()
    if client:
        res = client.table(TABLE).delete().eq("owner", owner).execute()
        return len(res.data or [])
    data = _read_json()
    keep = [t for t in data if t.get("owner") != owner]
    n = len(data) - len(keep)
    _write_json(keep)
    return n
