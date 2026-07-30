"""
Leak-prevention test for per-user data isolation.
==================================================

Runs WITHOUT pytest: `python tests/test_tenancy.py`. Uses a fake Supabase
client that records every filter/insert, so it verifies the TenantClient
wrapper's behaviour exactly, offline.

Asserts, for a signed-in user:
  * every TENANT table's select/update/delete carries a user_id == <uid> filter
  * every TENANT insert/upsert row is stamped with user_id
  * GLOBAL tables are NEVER scoped (no user_id filter, no stamping)
  * with no user in context, NOTHING is scoped (single-user parity)

If any tenant table stops being scoped, this fails — that's the guard against a
silent cross-user data leak.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api import tenancy


# ── a fake supabase client that records what the wrapper does ───────────────────
class FakeBuilder:
    def __init__(self, table, op):
        self.table = table
        self.op = op
        self.filters = []          # list of (col, val) from .eq()
        self.rows = None

    def eq(self, col, val):
        self.filters.append((col, val))
        return self

    # common chained no-ops that just return self
    def order(self, *a, **k): return self
    def limit(self, *a, **k): return self
    def range(self, *a, **k): return self
    def single(self, *a, **k): return self

    def execute(self):
        class R: data = []
        return R()


class FakeTable:
    def __init__(self, name, sink):
        self.name = name
        self.sink = sink

    def _mk(self, op, rows=None):
        b = FakeBuilder(self.name, op)
        b.rows = rows
        self.sink.append(b)
        return b

    def select(self, *a, **k): return self._mk("select")
    def update(self, values, *a, **k): return self._mk("update", values)
    def delete(self, *a, **k): return self._mk("delete")
    def insert(self, rows, *a, **k): return self._mk("insert", rows)
    def upsert(self, rows, *a, **k): return self._mk("upsert", rows)


class FakeClient:
    def __init__(self):
        self.sink = []

    def table(self, name):
        return FakeTable(name, self.sink)


UID = "user-abc-123"
_failures = []


def check(cond, msg):
    if not cond:
        _failures.append(msg)


def run():
    # ---- 1. tenant tables ARE scoped when a user is in context ----------------
    tok = tenancy.set_current_user(UID)
    try:
        fake = FakeClient()
        tc = tenancy.wrap(fake)

        for tbl in sorted(tenancy.TENANT_TABLES):
            fake.sink.clear()
            tc.table(tbl).select("*").execute()
            b = fake.sink[-1]
            check(
                (tenancy._USER_COL, UID) in b.filters,
                f"SELECT on tenant table '{tbl}' NOT scoped by user_id — LEAK RISK",
            )

            fake.sink.clear()
            tc.table(tbl).delete().eq("id", "x").execute()
            b = fake.sink[-1]
            check(
                (tenancy._USER_COL, UID) in b.filters,
                f"DELETE on tenant table '{tbl}' NOT scoped by user_id — LEAK RISK",
            )

            fake.sink.clear()
            tc.table(tbl).insert({"id": "x"}).execute()
            b = fake.sink[-1]
            check(
                isinstance(b.rows, dict) and b.rows.get(tenancy._USER_COL) == UID,
                f"INSERT on tenant table '{tbl}' NOT stamped with user_id",
            )

            # insert list form
            fake.sink.clear()
            tc.table(tbl).insert([{"id": "a"}, {"id": "b"}]).execute()
            b = fake.sink[-1]
            check(
                all(r.get(tenancy._USER_COL) == UID for r in b.rows),
                f"INSERT[list] on tenant table '{tbl}' NOT stamped with user_id",
            )

        # ---- 2. global tables are NEVER scoped --------------------------------
        for tbl in sorted(tenancy.GLOBAL_TABLES):
            fake.sink.clear()
            tc.table(tbl).select("*").execute()
            b = fake.sink[-1]
            check(
                (tenancy._USER_COL, UID) not in b.filters,
                f"SELECT on GLOBAL table '{tbl}' was scoped by user_id — must stay global",
            )
            fake.sink.clear()
            tc.table(tbl).insert({"k": "v"}).execute()
            b = fake.sink[-1]
            check(
                tenancy._USER_COL not in (b.rows or {}),
                f"INSERT on GLOBAL table '{tbl}' was stamped with user_id — must stay global",
            )
    finally:
        tenancy.reset_current_user(tok)

    # ---- 3. no user in context => nothing scoped (single-user parity) ---------
    fake = FakeClient()
    tc = tenancy.wrap(fake)
    tc.table("stock_holdings").select("*").execute()
    b = fake.sink[-1]
    check(
        b.filters == [],
        "With no user in context, tenant SELECT was still scoped — breaks single-user mode",
    )

    # ---- report ---------------------------------------------------------------
    if _failures:
        print(f"FAIL — {len(_failures)} isolation problem(s):")
        for f in _failures:
            print(f"  ✗ {f}")
        sys.exit(1)
    n = len(tenancy.TENANT_TABLES)
    print(f"PASS — {n} tenant tables scoped, {len(tenancy.GLOBAL_TABLES)} global tables unscoped, "
          f"no-user passthrough OK.")


if __name__ == "__main__":
    run()
