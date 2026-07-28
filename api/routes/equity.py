"""
Live stock portfolio routes — multi-account Kite (Connect Personal, free).
Exposed under /api/equity (the /api/portfolio prefix is the Excel analyzer).

Flow per account:
  1. POST /accounts          → save api_key/api_secret → returns {id, login_url}
  2. (user logs in on Kite, copies the request_token from the redirect URL)
  3. POST /accounts/{id}/connect {request_token} → store access_token + pull holdings
  4. POST /accounts/{id}/refresh → re-pull holdings (re-login if the token expired)
"""
from __future__ import annotations

from typing import Optional
from fastapi import APIRouter, HTTPException, UploadFile, File, Body, Form
from pydantic import BaseModel

from ..portfolio import store, engine, kite_client, motilal_client, imports, dividends, performance
from ..auth import kite_oauth

router = APIRouter(prefix="/api/equity", tags=["equity"])


def _guard():
    if not store.tables_ready():
        raise HTTPException(503, store.MIGRATION_HINT)


class AccountIn(BaseModel):
    person: str
    account_label: str
    api_key: str
    api_secret: str
    broker: str = "kite"
    note: Optional[str] = None
    sellable_on: Optional[str] = None


class ConnectIn(BaseModel):
    request_token: str


class MotilalIn(BaseModel):
    person: str
    account_label: str
    api_key: str
    api_secret: str
    client_code: str
    password: str
    dob: str                      # 2FA, format DD/MM/YYYY
    totp: Optional[str] = None
    note: Optional[str] = None
    sellable_on: Optional[str] = None


class MotilalRelogin(BaseModel):
    password: str
    dob: str
    totp: Optional[str] = None


class ImportedIn(BaseModel):
    person: str
    account_label: str
    broker: str                   # icici | ibkr | motilal
    note: Optional[str] = None
    sellable_on: Optional[str] = None


class EditIn(BaseModel):
    person: Optional[str] = None
    account_label: Optional[str] = None
    note: Optional[str] = None
    sellable_on: Optional[str] = None
    api_key: Optional[str] = None        # this account's Kite Connect app key
    api_secret: Optional[str] = None     # paired secret (blank = keep the stored one)


@router.get("/summary")
def summary():
    _guard()
    return engine.build_summary()


@router.get("/indices")
def indices():
    """Headline market indices (Nifty / Sensex / S&P 500 / Nasdaq) with day-change,
    for the holdings hero. Public market data — no account needed."""
    from ..stocks import prices as price_feed
    return {"indices": price_feed.index_quotes()}


# ── Manual LTP overrides (for holdings the live feed can't price) ────────────────
class ManualPriceIn(BaseModel):
    symbol: str
    price: Optional[float] = None        # None / ≤0 clears the override


@router.get("/manual-prices")
def get_manual_prices():
    from ..portfolio import manual_prices as mp
    return mp.list_prices()


@router.put("/manual-prices")
def set_manual_price(body: ManualPriceIn):
    from ..portfolio import manual_prices as mp
    if not (body.symbol or "").strip():
        raise HTTPException(400, "Symbol is required.")
    res = mp.set_price(body.symbol, body.price)
    engine.invalidate(reload_inputs=False)   # price-only → reuse cached holdings, fast re-value
    return res


# ── Screener.in links (per stock; click a holding's logo to open it) ────────────
class ScreenerLinkIn(BaseModel):
    symbol: str
    url: Optional[str] = None             # blank / None clears the link


@router.get("/screener-links")
def get_screener_links():
    from ..portfolio import screener_links as sl
    return sl.list_links()


@router.put("/screener-links")
def set_screener_link(body: ScreenerLinkIn):
    from ..portfolio import screener_links as sl
    if not (body.symbol or "").strip():
        raise HTTPException(400, "Symbol is required.")
    url = (body.url or "").strip()
    if url and not url.lower().startswith(("http://", "https://")):
        url = "https://" + url            # be forgiving about a pasted bare host
    res = sl.set_link(body.symbol, url)
    engine.invalidate(reload_inputs=False)   # link-only → reuse holdings, fast re-value
    return res


@router.post("/screener-links/seed")
def seed_screener_links():
    """Populate a derived screener.in link for every held equity that lacks one
    (ETFs/funds skipped). Safe to re-run — never overwrites a link you set."""
    from ..portfolio import screener_links as sl, store as pstore
    written = sl.seed_missing(pstore.list_holdings())
    if written:
        engine.invalidate(reload_inputs=False)
    return {"written": written}


def _ids(accounts: Optional[str]) -> Optional[list]:
    return [a for a in accounts.split(",") if a] if accounts else None


@router.get("/performance")
def performance_series(period: str = "1y", accounts: Optional[str] = None, refresh: bool = False):
    return performance.build(period, _ids(accounts), refresh=refresh)


@router.get("/performance/holdings")
def performance_holdings(date: str, period: str = "1y", accounts: Optional[str] = None):
    return performance.holdings_on(date, period, _ids(accounts))


@router.get("/accounts")
def accounts():
    _guard()
    return [store.public_account(a) for a in store.list_accounts()]


@router.post("/accounts")
def add_account(body: AccountIn):
    _guard()
    if not body.api_key.strip() or not body.api_secret.strip():
        raise HTTPException(400, "API key and secret are required.")
    acc = store.add_account({
        "person": body.person.strip(), "account_label": body.account_label.strip(),
        "broker": body.broker, "kind": "connected", "status": "pending",
        "api_key": body.api_key.strip(), "api_secret": body.api_secret.strip(),
        "note": body.note, "sellable_on": body.sellable_on,
    })
    try:
        url = kite_oauth.begin_login("stock", acc)
    except Exception as e:
        raise HTTPException(400, f"Could not build the Kite login URL: {e}")
    return {**store.public_account(acc), "login_url": url}


@router.get("/accounts/{acc_id}/login-url")
def login_url(acc_id: str):
    _guard()
    acc = store.get_account(acc_id)
    if not acc:
        raise HTTPException(404, "Account not found.")
    try:
        return {"login_url": kite_oauth.begin_login("stock", acc)}
    except Exception as e:
        raise HTTPException(400, f"Could not build the Kite login URL: {e}")


@router.post("/accounts/{acc_id}/connect")
def connect(acc_id: str, body: ConnectIn):
    _guard()
    acc = store.get_account(acc_id)
    if not acc:
        raise HTTPException(404, "Account not found.")
    try:
        sess = kite_client.exchange(acc["api_key"], acc["api_secret"], body.request_token.strip())
    except Exception as e:
        msg = str(e).lower()
        if "checksum" in msg:
            raise HTTPException(400, "API secret doesn't match this API key — re-check the Personal app's secret.")
        if "token" in msg and ("invalid" in msg or "expired" in msg):
            raise HTTPException(400, "Request token invalid/expired (lasts ~5 min) — open Kite login and try again quickly.")
        raise HTTPException(400, f"Login failed: {e}")
    store.update_account(acc_id, {
        "access_token": sess["access_token"], "kite_user_id": sess.get("user_id"),
        "status": "connected", "token_updated_at": store._now(),
    })
    return _sync(acc_id)


@router.post("/accounts/{acc_id}/refresh")
def refresh(acc_id: str):
    _guard()
    return _sync(acc_id)


@router.post("/accounts/{acc_id}/disconnect")
def disconnect_account(acc_id: str):
    """Log off this account (and any linked F&O/Stocks account on the same
    api_key + Kite user) by clearing the stored token — mirror of the login."""
    _guard()
    acc = store.get_account(acc_id)
    if not acc:
        raise HTTPException(404, "Account not found.")
    cleared = kite_oauth.disconnect("stock", acc)
    engine.invalidate()
    return {"ok": True, "cleared": cleared}


# ── Motilal Oswal (direct login — no OAuth) ─────────────────────────────────────
@router.post("/accounts/connect-motilal")
def connect_motilal(body: MotilalIn):
    _guard()
    try:
        sess = motilal_client.login(body.api_key.strip(), body.api_secret.strip(),
                                    body.client_code.strip(), body.password,
                                    body.dob.strip(), (body.totp or "").strip() or None)
    except Exception as e:
        raise HTTPException(400, f"Motilal login failed: {e}")
    acc = store.add_account({
        "person": body.person.strip(), "account_label": body.account_label.strip(),
        "broker": "motilal", "kind": "connected", "status": "connected",
        "api_key": body.api_key.strip(), "api_secret": body.api_secret.strip(),
        "kite_user_id": body.client_code.strip(),
        "access_token": f"{sess['auth_token']}|{sess.get('access_token') or ''}",
        "token_updated_at": store._now(), "note": body.note, "sellable_on": body.sellable_on,
    })
    return _sync(acc["id"])


@router.post("/accounts/{acc_id}/relogin-motilal")
def relogin_motilal(acc_id: str, body: MotilalRelogin):
    _guard()
    acc = store.get_account(acc_id)
    if not acc:
        raise HTTPException(404, "Account not found.")
    try:
        sess = motilal_client.login(acc["api_key"], acc.get("api_secret") or "",
                                    acc.get("kite_user_id") or "", body.password,
                                    body.dob.strip(), (body.totp or "").strip() or None)
    except Exception as e:
        raise HTTPException(400, f"Motilal login failed: {e}")
    store.update_account(acc_id, {
        "access_token": f"{sess['auth_token']}|{sess.get('access_token') or ''}",
        "status": "connected", "token_updated_at": store._now(),
    })
    return _sync(acc_id)


# ── Excel/CSV import (ICICI / IBKR / Motilal) ───────────────────────────────────
@router.post("/accounts/imported")
def add_imported(body: ImportedIn):
    _guard()
    acc = store.add_account({
        "person": body.person.strip(), "account_label": body.account_label.strip(),
        "broker": body.broker.strip().lower(), "kind": "imported", "status": "imported",
        "note": body.note, "sellable_on": body.sellable_on,
    })
    return store.public_account(acc)


@router.post("/accounts/{acc_id}/import-preview")
async def import_preview(acc_id: str, file: UploadFile = File(...)):
    _guard()
    acc = store.get_account(acc_id)
    if not acc:
        raise HTTPException(404, "Account not found.")
    raw = await file.read()
    try:
        rows = imports.parse(acc.get("broker"), raw)
    except Exception as e:
        raise HTTPException(400, str(e))
    return {
        "count": len(rows), "matched": sum(1 for r in rows if r.get("symbol")),
        "holdings": [{k: r.get(k) for k in ("name", "symbol", "isin", "currency",
                     "quantity", "avg_price", "import_price")} for r in rows],
    }


@router.post("/accounts/{acc_id}/import")
async def import_file(acc_id: str, file: UploadFile = File(...)):
    _guard()
    acc = store.get_account(acc_id)
    if not acc:
        raise HTTPException(404, "Account not found.")
    raw = await file.read()
    try:
        rows = imports.parse(acc.get("broker"), raw)
    except Exception as e:
        raise HTTPException(400, str(e))
    store.replace_holdings(acc, rows)
    store.update_account(acc_id, {"status": "imported", "last_synced": store._now()})
    engine.invalidate()
    return {"ok": True, "count": len(rows)}


@router.put("/accounts/{acc_id}")
def edit_account(acc_id: str, body: EditIn):
    _guard()
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    # api_key/api_secret: a blank string means "leave the stored value as-is" (the
    # edit form never pre-fills the secret), so only overwrite when a value is given.
    for k in ("api_key", "api_secret"):
        if k in patch:
            patch[k] = (patch[k] or "").strip()
            if not patch[k]:
                patch.pop(k)
    # Changing the API key means the stored token belongs to the OLD app and would
    # be rejected — drop the session so the row shows "log in" for the new app.
    cur = store.get_account(acc_id)
    if cur and "api_key" in patch and patch["api_key"] != (cur.get("api_key") or "").strip():
        patch["access_token"] = ""
        patch["status"] = "pending"
    acc = store.update_account(acc_id, patch)
    if not acc:
        raise HTTPException(404, "Account not found.")
    engine.invalidate()                 # person/label changes flow into by_person & cards
    return store.public_account(acc)


# ── Dividend log (calendar + monthly totals) ────────────────────────────────────
class DividendIn(BaseModel):
    date: str                         # YYYY-MM-DD (record / pay date)
    symbol: str
    name: Optional[str] = None
    per_share: float
    shares: float
    received: Optional[bool] = None   # legacy mirror of status (back-compat)
    status: Optional[str] = None      # pending | received | not_received
    currency: Optional[str] = None    # INR (default) | USD — for US stocks
    person: Optional[str] = None
    account_id: Optional[str] = None
    note: Optional[str] = None


class DividendPatch(BaseModel):
    date: Optional[str] = None
    symbol: Optional[str] = None
    name: Optional[str] = None
    per_share: Optional[float] = None
    shares: Optional[float] = None
    received: Optional[bool] = None
    status: Optional[str] = None      # pending | received | not_received
    currency: Optional[str] = None    # INR (default) | USD
    person: Optional[str] = None


class DividendMetaIn(BaseModel):
    symbol: str
    prev_years: Optional[float] = None   # None clears the manual override


class TdsIn(BaseModel):
    person: Optional[str] = ""           # "" = global default rate
    rate: Optional[float] = None         # 0–1; None clears the override


class CollectedIn(BaseModel):
    symbol: str
    person: Optional[str] = ""
    collected: Optional[float] = None    # None clears the per-(stock,person) override


_DIV_HINT = ("Dividend table not found. Run the migration in SUPABASE.md "
             "(\"Dividend log\") once in the Supabase SQL editor to create "
             "stock_dividends, then retry.")
_META_HINT = ("Dividend-meta table not found. Run the migration in SUPABASE.md "
              "(\"Dividend log\") to create stock_dividend_meta, then retry.")


@router.get("/dividends")
def list_dividends():
    return dividends.list_dividends()


# ── Per-account reconciliation working-state ─────────────────────────────────
# The rate + "received" tick a user sets per account BEFORE pressing "Add as
# received" (which commits them to Logged). Held in the durable app_cache KV so
# these in-progress marks survive a refresh, navigation, or a revert — instead
# of living only in browser memory and vanishing.  Blob shape:
#   { "<dividendId>|<accountId>": { "ps": <number|null>, "received": <bool> }, … }
@router.get("/dividends/recon")
def get_dividend_recon():
    row = store.cache_get("dividend_recon")
    return (row or {}).get("value") or {}


@router.put("/dividends/recon")
def set_dividend_recon(body: dict = Body(default={})):
    store.cache_set("dividend_recon", body or {})
    return {"ok": True}


@router.get("/dividends/sync-declared/preview")
def preview_declared():
    """Dry-run: what the declared-dividend sync WOULD add/prune, without writing."""
    from ..corporate_actions import ingest
    return ingest.sync(dry_run=True)


@router.post("/dividends/sync-declared")
def sync_declared():
    """Read NSE's declared corporate-action dividends and log the new ones for the
    stocks you hold (no overlap), refresh share counts, and drop pending auto
    entries for stocks you've exited. Received history is kept intact."""
    if not dividends.table_ready():
        raise HTTPException(503, _DIV_HINT)
    from ..corporate_actions import ingest
    return ingest.sync(dry_run=False)


# ── Corporate-actions CSV upload (manual declared-dividend import) ────────────────
# The exchange Corporate Actions export → dividends for the stocks you hold. This
# is the manual alternative to /sync-declared, which 403s from cloud IPs (NSE
# blocks datacenters). Multiple files can be uploaded (e.g. one per week); each
# is APPENDED and de-duplicated against existing entries, and recorded so the UI
# can show what every file contributed.
_CSV_TAG = "auto:csv-corp-action"


def _parse_corp_csv(file: UploadFile, raw: bytes) -> dict:
    from ..corporate_actions import bse_csv
    try:
        return bse_csv.parse(file.filename or "", raw)
    except Exception as e:
        raise HTTPException(400, str(e))


@router.post("/dividends/corporate-actions/preview")
async def preview_corp_actions(file: UploadFile = File(...)):
    """Dry-run a Corporate Actions CSV: what it WOULD add/update for your
    holdings, plus the full action mix, without writing anything."""
    from ..corporate_actions import ingest
    parsed = _parse_corp_csv(file, await file.read())
    result = ingest.ingest_declared(parsed["dividends"], dry_run=True, tag=_CSV_TAG, prune=False)
    return {**result, "kinds": parsed["kinds"], "rows_total": len(parsed["rows"]),
            "dividend_rows": len(parsed["dividends"]),
            "date_from": parsed["date_from"], "date_to": parsed["date_to"]}


@router.post("/dividends/corporate-actions/import")
async def import_corp_actions(file: UploadFile = File(...)):
    """Import a Corporate Actions CSV: add/refresh pending dividends for the stocks
    you hold (no duplicates, never deletes), and record the upload so you can see
    later exactly what this file contributed."""
    if not dividends.table_ready():
        raise HTTPException(503, _DIV_HINT)
    from ..corporate_actions import ingest, uploads
    parsed = _parse_corp_csv(file, await file.read())
    result = ingest.ingest_declared(parsed["dividends"], dry_run=False, tag=_CSV_TAG, prune=False)
    record = uploads.add_upload({
        "filename": file.filename or "corporate_actions.csv",
        "rows_total": len(parsed["rows"]),
        "dividend_rows": len(parsed["dividends"]),
        "kinds": parsed["kinds"],
        "date_from": parsed["date_from"], "date_to": parsed["date_to"],
        "matched_count": result["matched_count"],
        "added_count": result["added_count"],
        "updated_count": result["updated_count"],
        "unheld_count": result["unheld_count"],
        "matched": result["matched"],          # per-symbol dividends that hit your holdings
    })
    return {**result, "kinds": parsed["kinds"], "rows_total": len(parsed["rows"]),
            "dividend_rows": len(parsed["dividends"]),
            "date_from": parsed["date_from"], "date_to": parsed["date_to"],
            "upload": record}


@router.get("/dividends/corporate-actions")
def list_corp_action_uploads():
    """Every Corporate Actions CSV imported, newest first — filename, when, the
    action mix, coverage dates, and the dividends it matched to your holdings."""
    from ..corporate_actions import uploads
    return uploads.list_uploads()


@router.delete("/dividends/corporate-actions/{upload_id}")
def delete_corp_action_upload(upload_id: str):
    """Forget an upload record. The dividends it added stay in your log (delete
    those individually if you want them gone)."""
    from ..corporate_actions import uploads
    return {"ok": uploads.delete_upload(upload_id)}


# ── Tax P&L (Zerodha Tax P&L statement → per-person, per-FY realized gains) ────
@router.post("/tax-pnl/import")
async def import_tax_pnl(file: UploadFile = File(...)):
    """Upload a Zerodha Tax P&L statement (XLSX). Parses the realized-gain buckets
    (short/long term, intraday, F&O, dividends) for that client + financial year,
    stored per (client, FY) so you can add more people and years over time."""
    from ..stocks import taxpnl
    raw = await file.read()
    try:
        parsed = taxpnl.parse_tax_pnl(file.filename or "taxpnl.xlsx", raw)
    except Exception as e:
        raise HTTPException(400, f"Couldn't read that Tax P&L file: {e}")
    if not parsed.get("client_id") and not parsed.get("fy"):
        raise HTTPException(400, "Not a Zerodha Tax P&L statement — no client id / financial year found.")
    if not parsed.get("fy"):
        raise HTTPException(400, "Couldn't read the financial year from the filename "
                                 "(expected e.g. taxpnl-VWM579-2025_2026-…xlsx).")
    return {"ok": True, "statement": taxpnl.save_statement(parsed)}


@router.get("/tax-pnl")
def list_tax_pnl():
    """Every stored Tax P&L statement — one per client × financial year.
    `durable` is False until the `app_cache` table exists (until then uploads
    live in an ephemeral file and vanish on redeploy — the frontend warns)."""
    from ..stocks import taxpnl
    return {"statements": taxpnl.list_statements(), "ltcg_exempt": taxpnl.LTCG_EXEMPT,
            "durable": store.cache_durable()}


@router.get("/kv-status")
def kv_status():
    """Whether durable KV (the `app_cache` table) is available. When False, the
    F&O P&L statements, tradebooks, corporate-action uploads and tax statements
    persist only to a local file that a Railway redeploy / 2nd replica loses."""
    return {"durable": store.cache_durable()}


@router.delete("/tax-pnl/{client_id}/{fy}")
def delete_tax_pnl(client_id: str, fy: str):
    from ..stocks import taxpnl
    return {"ok": taxpnl.delete_statement(client_id, fy)}


@router.post("/dividends")
def add_dividend(body: DividendIn):
    if not body.symbol.strip():
        raise HTTPException(400, "Stock symbol is required.")
    if body.per_share <= 0 or body.shares <= 0:
        raise HTTPException(400, "Per-share amount and shares must be greater than zero.")
    if not dividends.table_ready():
        raise HTTPException(503, _DIV_HINT)
    return dividends.add_dividend(body.model_dump())


@router.patch("/dividends/{div_id}")
def patch_dividend(div_id: str, body: DividendPatch):
    patch = body.model_dump(exclude_unset=True)
    if not patch:
        raise HTTPException(400, "Nothing to update.")
    row = dividends.update_dividend(div_id, patch)
    if not row:
        raise HTTPException(404, "Dividend entry not found.")
    return row


@router.delete("/dividends/{div_id}")
def delete_dividend(div_id: str):
    if not dividends.delete_dividend(div_id):
        raise HTTPException(404, "Dividend entry not found.")
    return {"ok": True}


# ── Dividend reconciliation from a bank statement ─────────────────────────────
# Upload a bank statement → match its dividend credits to logged dividend entries
# (by amount, then company name + date), confirm, and mark them received. It only
# flips existing entries' status — never inserts — so duplicates can't happen.
_DIV_UPLOADS_KEY = "dividend_statement_uploads"


@router.post("/dividends/reconcile/preview")
async def dividend_reconcile_preview(file: UploadFile = File(...), person: str = ""):
    from ..bonds import statement
    from ..portfolio import div_reconcile
    content = await file.read()
    if not content:
        raise HTTPException(400, "The uploaded file is empty.")
    try:
        credits = statement.parse_credits(content, file.filename or "")
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(400, f"Could not read the statement: {e}")
    return div_reconcile.reconcile(credits, dividends.list_dividends(), holder=person or "")


class DivReconcileConfirmIn(BaseModel):
    div_ids: list[str]
    filename: Optional[str] = None
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    person: Optional[str] = None
    credits: Optional[int] = None
    amount: Optional[float] = None


@router.post("/dividends/reconcile/confirm")
def dividend_reconcile_confirm(body: DivReconcileConfirmIn):
    """Mark each confirmed dividend entry received (idempotent — never inserts)."""
    marked = 0
    for did in body.div_ids:
        if did and dividends.set_status(did, "received"):
            marked += 1
    # record the upload in the durable KV history
    upload = None
    try:
        from ..auth import config as authcfg
        who = authcfg.primary_email()
    except Exception:
        who = None
    try:
        import uuid
        row = {
            "id": uuid.uuid4().hex[:10], "filename": (body.filename or "statement"),
            "uploaded_by": who, "uploaded_at": store._now(),
            "date_from": (body.date_from or None), "date_to": (body.date_to or None),
            "person": body.person, "credits": int(body.credits or 0),
            "marked": marked, "amount": round(float(body.amount or 0), 2),
        }
        rec = store.cache_get(_DIV_UPLOADS_KEY)
        items = (rec or {}).get("value") if rec and isinstance((rec or {}).get("value"), list) else []
        items = (items or []) + [row]
        store.cache_set(_DIV_UPLOADS_KEY, items[-200:])
        upload = row
    except Exception:
        pass
    return {"ok": True, "marked": marked, "upload": upload}


@router.get("/dividends/reconcile/uploads")
def dividend_reconcile_uploads():
    rec = store.cache_get(_DIV_UPLOADS_KEY)
    items = (rec or {}).get("value") if rec and isinstance((rec or {}).get("value"), list) else []
    return sorted(items or [], key=lambda u: (u.get("uploaded_at") or ""), reverse=True)


@router.get("/dividend-meta")
def list_dividend_meta():
    return dividends.list_meta()


@router.put("/dividend-meta")
def set_dividend_meta(body: DividendMetaIn):
    if not body.symbol.strip():
        raise HTTPException(400, "Stock symbol is required.")
    if not dividends.meta_table_ready():
        raise HTTPException(503, _META_HINT)
    return dividends.set_meta(body.symbol, body.prev_years)


@router.get("/dividend-tds")
def list_dividend_tds():
    return dividends.list_tds()


@router.put("/dividend-tds")
def set_dividend_tds(body: TdsIn):
    if not dividends.tds_table_ready():
        raise HTTPException(503, _META_HINT)
    return dividends.set_tds(body.person or "", body.rate)


@router.get("/dividend-collected")
def list_dividend_collected():
    return dividends.list_collected()


@router.put("/dividend-collected")
def set_dividend_collected(body: CollectedIn):
    if not body.symbol.strip():
        raise HTTPException(400, "Stock symbol is required.")
    if not dividends.collected_table_ready():
        raise HTTPException(503, _META_HINT)
    return dividends.set_collected(body.symbol, body.person or "", body.collected)


@router.delete("/accounts/{acc_id}")
def delete_account(acc_id: str):
    _guard()
    if not store.delete_account(acc_id):
        raise HTTPException(404, "Account not found.")
    engine.invalidate()
    return {"ok": True}


def _sync(acc_id: str) -> dict:
    """Pull holdings for one account using its stored token(s), per broker."""
    acc = store.get_account(acc_id)
    if not acc:
        raise HTTPException(404, "Account not found.")
    token = acc.get("access_token")
    if not token:
        raise HTTPException(409, "Account not connected — log in first.")
    broker = (acc.get("broker") or "kite").lower()
    try:
        if broker == "motilal":
            auth, _, access = token.partition("|")     # stored as "auth|access"
            if not auth:
                raise HTTPException(409, "Account not connected — log in first.")
            holds = motilal_client.fetch_holdings(acc["api_key"], acc.get("api_secret") or "",
                                                  auth, access, acc.get("kite_user_id") or "")
        else:
            holds = kite_client.fetch_holdings(acc["api_key"], token)
    except HTTPException:
        raise
    except Exception as e:
        msg = str(e).lower()
        if any(w in msg for w in ("token", "session", "expire", "unauth", "login", "403", "api_key")):
            store.update_account(acc_id, {"status": "expired"})
            raise HTTPException(401, "Session expired — log in to this account again to refresh.")
        raise HTTPException(400, f"Could not fetch holdings: {e}")
    store.replace_holdings(acc, holds)
    store.update_account(acc_id, {"status": "connected", "last_synced": store._now()})
    engine.invalidate()
    return {"ok": True, "holdings": len(holds), "account": store.public_account(store.get_account(acc_id))}


# ── LTCG harvesting: booked long-term gain vs the ₹1.25 L tax-free allowance ──────
# Upload a Zerodha Tax P&L per account + FY; we read the Long-Term realised profit
# and show how much of the ₹1,25,000 tax-free LTCG is left to book this year.
from ..equity import taxpnl as _taxpnl, harvest_store as _harvest


def _acc_or_404(acc_id: str):
    acc = store.get_account(acc_id)
    if not acc:
        raise HTTPException(404, "No such account.")
    return acc


@router.get("/harvest")
def harvest_all():
    """Every filed LTCG record, so the page can badge each person/account without
    a per-account round trip."""
    _guard()
    return {"allowance": _taxpnl.LTCG_FREE_ALLOWANCE, "records": _harvest.all_records()}


@router.get("/accounts/{acc_id}/harvest")
def harvest_for_account(acc_id: str):
    """The filed years for one account (newest first)."""
    _guard()
    _acc_or_404(acc_id)
    return {"allowance": _taxpnl.LTCG_FREE_ALLOWANCE, "records": _harvest.get_for_account(acc_id)}


@router.post("/accounts/{acc_id}/harvest/preview")
async def harvest_preview(acc_id: str, file: UploadFile = File(...)):
    """Parse an uploaded Tax P&L (no save). The FY is read FROM the file's own
    period — the user never types it — so a wrong year can't be filed."""
    _guard()
    _acc_or_404(acc_id)
    data = await file.read()
    try:
        parsed = _taxpnl.parse(data)
        fy = _taxpnl.fy_from_period(parsed)
        view = _taxpnl.harvest_view(parsed, fy)
    except _taxpnl.TaxPnlError as e:
        raise HTTPException(422, str(e))
    return {"file_name": file.filename, "parsed": parsed, "harvest": view}


class HarvestSaveIn(BaseModel):
    fy: str
    # the client sends back the numbers it previewed, so a save is one call; the
    # server still recomputes the harvest math from the raw totals to stay authoritative
    parsed: dict
    file_name: Optional[str] = None


@router.post("/accounts/{acc_id}/harvest")
def harvest_save(acc_id: str, body: HarvestSaveIn):
    """File the previewed Tax P&L for this account + FY (upsert)."""
    _guard()
    acc = _acc_or_404(acc_id)
    try:
        view = _taxpnl.harvest_view(body.parsed or {}, body.fy)
    except _taxpnl.TaxPnlError as e:
        raise HTTPException(422, str(e))
    merged = {**(body.parsed or {}), **view}
    row = _harvest.save(acc_id, merged, file_name=body.file_name or "",
                        person=acc.get("person"), account_label=acc.get("account_label"))
    return {"ok": True, "record": row}


@router.delete("/accounts/{acc_id}/harvest/{fy_label}")
def harvest_delete(acc_id: str, fy_label: str):
    _guard()
    _acc_or_404(acc_id)
    return {"ok": _harvest.delete(acc_id, fy_label)}
