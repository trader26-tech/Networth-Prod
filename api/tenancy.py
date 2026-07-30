"""
Per-request tenant context + a tenant-enforcing Supabase client wrapper.
=======================================================================

This is the ONE place data isolation is enforced. Stores keep calling
`client.table(...).select(...)` exactly as before; the wrapper transparently:

  * adds `.eq("user_id", <current user>)` to every SELECT / UPDATE / DELETE, and
  * stamps `user_id` onto every INSERT / UPSERT row,

so a store physically cannot read or write another user's data — without editing
the ~124 call sites by hand (where one miss = a leak).

Design:
  * `current_user_id()` reads a contextvar the auth middleware sets per request.
  * When multi-user is OFF, or no user is in context (e.g. a background job /
    single-user instance), the wrapper is a pass-through — behaviour is
    identical to today. Isolation only engages when there IS a signed-in user.
  * `TENANT_TABLES` lists the domain tables that carry `user_id`. Auth/registry
    tables (users, auth_otp, auth_devices) and the shared KV are NOT scoped —
    they are global by design. Anything not in the set is passed through
    unchanged, so adding scoping is opt-in per table and can't silently break
    an unmigrated one.

The leak-prevention test (tests/test_tenancy.py) asserts that for every
TENANT_TABLE, a SELECT built through the wrapper carries a user_id filter.
"""
from __future__ import annotations

import contextvars
from typing import Any, Iterable, Optional

# Set by the auth middleware for the duration of a request. None = no tenant
# (single-user, background task, or unauthenticated) → wrapper passes through.
_current_user: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "current_user_id", default=None
)

# Domain tables that are per-user. Auth/registry/shared-KV tables are global and
# deliberately absent. Keep this list as the single source of truth for what
# gets isolated.
TENANT_TABLES: set[str] = {
    "stock_accounts", "stock_holdings", "stock_dividends", "stock_dividend_meta",
    "dividend_tds", "dividend_collected", "stock_trades", "brokerage_accounts",
    "bonds", "bond_payment_status", "land_parcels", "land_documents",
    "apartment_units", "apartment_documents", "apartment_tenants",
    "built_properties", "built_documents", "gold_items", "gold_documents",
    "salary_entries", "ulip_policies", "fd_deposits", "expenses", "other_income",
    "purchase_wishlist", "cash_funds", "income_receipts", "loans",
    "loan_payment_status", "covered_call_positions", "hedge_positions",
    "option_strategy_legs", "option_strategy_bookings", "option_strategy_snapshots",
    "option_strategy_trades", "document_folders", "vault_documents",
    "reminder_overrides", "portfolio_screener_links",
    "fno_accounts", "fno_trades", "fno_daily_pnl", "fno_pnl_snapshots",
    "app_settings", "family_members",
}

# Tables that are intentionally GLOBAL (never scoped) — asserted by the test so
# a table can't drift out of isolation unnoticed.
GLOBAL_TABLES: set[str] = {
    "users", "auth_otp", "auth_devices", "app_auth", "app_cache",
    "fno_login_log",
}

_USER_COL = "user_id"


def set_current_user(user_id: Optional[str]) -> contextvars.Token:
    return _current_user.set(user_id)


def reset_current_user(token: contextvars.Token) -> None:
    _current_user.reset(token)


def current_user_id() -> Optional[str]:
    return _current_user.get()


def is_tenant_table(name: str) -> bool:
    return name in TENANT_TABLES


# ---------------------------------------------------------------------------
# Query-builder proxies
# ---------------------------------------------------------------------------
class _FilterProxy:
    """
    Wraps a PostgREST filter builder (the object returned by .select/.update/
    .delete). Chained calls (.eq/.order/.limit/…) return the underlying builder
    again; .execute() runs it. The user_id filter is applied up front, so every
    read/mutate is already scoped no matter what the store chains afterwards.
    """

    def __init__(self, builder: Any):
        self._b = builder

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._b, name)
        if not callable(attr):
            return attr

        def wrapped(*args, **kwargs):
            res = attr(*args, **kwargs)
            # PostgREST builders return themselves (or a new builder) for
            # chaining; re-wrap so the whole chain stays proxied.
            if res is self._b or type(res).__name__.endswith(
                ("RequestBuilder", "QueryRequestBuilder", "SelectRequestBuilder",
                 "FilterRequestBuilder")
            ):
                self._b = res
                return self
            return res

        return wrapped


class _TableProxy:
    """Wraps client.table(name). Applies user_id scoping when name is a tenant
    table and a user is in context; otherwise passes straight through."""

    def __init__(self, table: Any, name: str, user_id: Optional[str]):
        self._t = table
        self._name = name
        self._uid = user_id

    def _scoped(self) -> bool:
        return self._uid is not None and self._name in TENANT_TABLES

    def select(self, *args, **kwargs):
        b = self._t.select(*args, **kwargs)
        if self._scoped():
            b = b.eq(_USER_COL, self._uid)
        return _FilterProxy(b)

    def update(self, values, *args, **kwargs):
        b = self._t.update(values, *args, **kwargs)
        if self._scoped():
            b = b.eq(_USER_COL, self._uid)
        return _FilterProxy(b)

    def delete(self, *args, **kwargs):
        b = self._t.delete(*args, **kwargs)
        if self._scoped():
            b = b.eq(_USER_COL, self._uid)
        return _FilterProxy(b)

    def insert(self, rows, *args, **kwargs):
        return _FilterProxy(self._t.insert(self._stamp(rows), *args, **kwargs))

    def upsert(self, rows, *args, **kwargs):
        return _FilterProxy(self._t.upsert(self._stamp(rows), *args, **kwargs))

    def _stamp(self, rows):
        """Force user_id onto inserted/upserted rows for tenant tables."""
        if not self._scoped():
            return rows
        if isinstance(rows, dict):
            return {**rows, _USER_COL: self._uid}
        if isinstance(rows, (list, tuple)):
            return [
                {**r, _USER_COL: self._uid} if isinstance(r, dict) else r
                for r in rows
            ]
        return rows

    def __getattr__(self, name: str) -> Any:
        # any other builder method (rpc-ish) — pass through unscoped
        return getattr(self._t, name)


class TenantClient:
    """
    Drop-in wrapper around a supabase client. Only `.table()` is intercepted;
    everything else (auth, storage, rpc) passes through untouched.
    """

    def __init__(self, client: Any):
        self._c = client

    def table(self, name: str):
        return _TableProxy(self._c.table(name), name, current_user_id())

    def __getattr__(self, name: str) -> Any:
        return getattr(self._c, name)


def wrap(client: Any) -> Any:
    """Wrap a real client so its .table() calls are tenant-scoped. Returns the
    client unchanged if it's falsy (no Supabase configured)."""
    if client is None:
        return client
    if isinstance(client, TenantClient):
        return client
    return TenantClient(client)


_patched = False


def install() -> None:
    """
    Route every store's Supabase client through the tenant wrapper from ONE
    place, by patching supabase.create_client. All 25 stores build their client
    via `from supabase import create_client` inside their _get_client(), so this
    single patch scopes them all — no per-store edits, no site that can be
    missed. Idempotent.

    Isolation only actually engages when a user is in context AND the table is a
    tenant table (see _TableProxy), so this is a no-op for single-user / global
    tables — safe to always install.
    """
    global _patched
    if _patched:
        return
    try:
        import supabase

        _orig = supabase.create_client

        def _create(url, key, *args, **kwargs):
            return wrap(_orig(url, key, *args, **kwargs))

        supabase.create_client = _create
        _patched = True
    except Exception:
        # supabase not importable (e.g. JSON-only dev) — nothing to patch.
        pass
