"""
Networth MCP server — lets a Claude client (claude.ai custom connector, Claude
Desktop, Claude Code) read the family's live financial data and, later, run the
funding/tax engine, all over the Model Context Protocol.

Phase 0: prove the pipe end-to-end — OAuth (Google) + allowlist + two read-only
tools (`whoami`, `financial_snapshot`). The heavy tools (funding_plan, tax) come
in later phases; the auth + transport proven here is the hard part.

Auth: Google OAuth via FastMCP's GoogleProvider. Claude registers dynamically,
the user signs in with Google, and we then gate every tool on an allowlist of
emails (env MCP_ALLOWED_EMAILS) so only the family can read the data.

Runs in-process on the same FastAPI app / Railway service (see api/main.py); it
reuses the existing stores + dashboard aggregate, so answers are always live.
Enabled only when the Google creds + base URL env vars are set.
"""
from __future__ import annotations

import os
import pathlib
from typing import Optional

# The advisor "brain" lives as a Claude Skill in the repo (single source of truth,
# git-versioned). The connector serves it LIVE from here so editing the markdown +
# redeploying updates claude.ai with no re-upload. Claude Code reads the same
# folder natively (it's under .claude/skills/).
_SKILL_DIR = pathlib.Path(__file__).resolve().parent.parent / ".claude" / "skills" / "networth-financial-advisor"


def _read_skill_files() -> dict:
    """Every markdown file of the advisor skill, read fresh from disk."""
    out: dict[str, str] = {}
    try:
        for p in sorted(_SKILL_DIR.rglob("*.md")):
            out[str(p.relative_to(_SKILL_DIR))] = p.read_text(encoding="utf-8")
    except Exception:
        pass
    return out

# FastMCP needs Python 3.10+. Import lazily so a 3.9 dev box importing this module
# (without enabling MCP) never hard-crashes.
_ENV_CLIENT_ID = "GOOGLE_MCP_CLIENT_ID"
_ENV_CLIENT_SECRET = "GOOGLE_MCP_CLIENT_SECRET"
_ENV_BASE_URL = "MCP_BASE_URL"          # public https ROOT, e.g. https://<app>.up.railway.app
                                        # (no /mcp suffix — the endpoint path adds it)
_ENV_ALLOWED = "MCP_ALLOWED_EMAILS"     # comma-separated allowlist
_ENV_ACCESS_TTL = "MCP_ACCESS_TOKEN_TTL_SECONDS"    # how long a connection stays live before a refresh

# How long the FastMCP-issued access token lives. Google's own access token is only
# ~1 h; leaving FastMCP's token coupled to that means the connector has to silently
# refresh mid-session, and any hiccup shows up as "please reconnect". Issuing a
# full-day access token means one connect keeps a whole day's chat session live
# without a single refresh round-trip. The refresh token (year-long fallback,
# Supabase-persisted) still covers day 2 onward.
_DEFAULT_ACCESS_TTL = 24 * 60 * 60              # 1 day
_DEFAULT_REFRESH_TTL = 365 * 24 * 60 * 60       # 1 year (only used if Google omits one)


def _access_ttl() -> int:
    try:
        v = int(os.environ.get(_ENV_ACCESS_TTL, "") or _DEFAULT_ACCESS_TTL)
        return v if v > 0 else _DEFAULT_ACCESS_TTL
    except ValueError:
        return _DEFAULT_ACCESS_TTL


def mcp_enabled() -> bool:
    return bool(os.environ.get(_ENV_CLIENT_ID) and os.environ.get(_ENV_CLIENT_SECRET)
                and os.environ.get(_ENV_BASE_URL))


def _allowed_emails() -> set[str]:
    raw = os.environ.get(_ENV_ALLOWED, "ranjeevfortrade@gmail.com")
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def build_mcp():
    """Construct the FastMCP server (with Google auth). Call only when enabled."""
    from fastmcp import FastMCP
    from fastmcp.server.dependencies import get_access_token
    from fastmcp.server.auth.providers.google import GoogleProvider

    # Persist the OAuth state (client registrations + issued tokens) in Supabase.
    # FastMCP's default is a directory under the process home, which Railway wipes
    # on every deploy — that is what made Claude re-authenticate constantly and
    # forced every new chat to register from scratch.
    from .mcp_store import build_client_storage, durable as _storage_durable

    client_secret = os.environ[_ENV_CLIENT_SECRET]
    storage = build_client_storage(client_secret)
    if _storage_durable():
        print("✓ MCP auth state: Supabase (survives redeploys, shared across chats)")
    else:
        print("⚠  MCP auth state: local file only — run the app_cache migration or "
              "Claude will ask you to sign in again after every deploy")

    _google_args = dict(
        client_id=os.environ[_ENV_CLIENT_ID],
        client_secret=client_secret,
        base_url=os.environ[_ENV_BASE_URL].rstrip("/"),
        required_scopes=["openid", "email", "profile"],
    )
    access_ttl = _access_ttl()
    # Keep the connection alive across a whole chat session: issue a long FastMCP
    # access token, and give the refresh token a long fallback so day-2+ silent
    # refresh keeps working even if Google omits refresh_expires_in.
    _ttl_args = dict(
        fastmcp_access_token_expiry_seconds=access_ttl,
        fallback_refresh_token_expiry_seconds=_DEFAULT_REFRESH_TTL,
    )

    # Build the provider with as many of these knobs as this fastmcp build accepts.
    # Older builds lack client_storage and/or the expiry kwargs, so peel them off in
    # order of importance rather than losing everything to one TypeError.
    def _make(**extra):
        return GoogleProvider(**_google_args, **extra)

    try:
        auth = _make(client_storage=storage, **_ttl_args)
        print(f"✓ MCP connection lifetime: access token {access_ttl // 3600} h · "
              "refresh persisted (one connect lasts the session)")
    except TypeError:
        try:
            auth = _make(client_storage=storage)
            print("⚠  MCP: this fastmcp build ignores token-lifetime settings — a "
                  "connection may need refreshing after ~1 h")
        except TypeError:
            print("⚠  MCP: this fastmcp build has no client_storage hook — auth state stays ephemeral")
            auth = _make()

    mcp = FastMCP(
        name="Networth",
        instructions=(
            "Live access to one family's complete finances (India + Kuwait): net "
            "worth, holdings by asset class and person, income, and liabilities. "
            "Use financial_snapshot for the big picture. All figures are in INR "
            "unless noted. Do not invent numbers — only report what the tools return. "
            "For ANY planning / tax / funding / goal question, FIRST call "
            "advisor_playbook() and follow it strictly — it is the maintained, "
            "tax-accurate advisor brain (persona, per-asset Indian tax incl. the NRI "
            "case, frameworks, and required output format). "
            "When the user shares a BANK / CREDIT-CARD STATEMENT and wants their "
            "expenses tracked: use submit_statement_lines() — one compact "
            "`date|amount|D or C|narration` line per transaction, no per-row JSON, no "
            "merchant/owner/category unless you disagree with the app. It is many times "
            "faster than submit_expense_transactions on a real statement and the app "
            "fills in the rest. Either way the rows are STAGED in the Expenses tab for "
            "the user to approve — nothing touches the ledger directly."
        ),
        auth=auth,
    )

    def _require_allowed() -> str:
        """Gate every tool: the Google-authenticated email must be allowlisted."""
        tok = get_access_token()
        claims = (getattr(tok, "claims", None) or {}) if tok else {}
        email = str(claims.get("email") or "").strip().lower()
        if email not in _allowed_emails():
            raise ValueError(
                f"Access denied for '{email or 'unknown'}'. This connector is "
                "restricted to the family allowlist."
            )
        return email

    @mcp.tool
    def whoami() -> dict:
        """Confirm who is connected and that access is authorised. Handy first call."""
        email = _require_allowed()
        return {"email": email, "authorised": True, "server": "Networth", "phase": "0"}

    @mcp.tool
    def advisor_playbook() -> dict:
        """The family financial-advisor playbook — persona, ACCURATE per-asset Indian
        taxation (incl. the resident-vs-NRI split; Ram is an NRI), the funding-
        waterfall / goal / income frameworks, the stored goals (₹100 Cr, ₹5L/mo, a
        house), and the required output format. Read FRESH from the repo's Claude
        Skill, so edits + a redeploy update this with no re-upload. Call this at the
        start of any planning/tax/funding/goal question and follow it strictly;
        never invent numbers — pull them from the data + engine tools."""
        _require_allowed()
        files = _read_skill_files()
        return {
            "skill": "networth-financial-advisor",
            "files": files,                       # SKILL.md + references/*.md, live from git
            "loaded": len(files),
            "note": ("Follow SKILL.md; references/tax-india.md is the tax core, "
                     "family-profile.md the stored goals/residency, frameworks.md the "
                     "methods. This is served live from the repo — edit the markdown "
                     "and redeploy to update."),
        }

    @mcp.tool
    def financial_snapshot() -> dict:
        """The whole financial picture in one call: total net worth, a breakdown by
        asset class, per-person totals, monthly income, and liabilities. All INR."""
        _require_allowed()
        from api.dashboard import aggregate
        d = aggregate.build_dashboard()

        def _classes():
            out = []
            for c in (d.get("by_class") or d.get("allocation") or []):
                out.append({
                    "asset_class": c.get("asset_class") or c.get("class") or c.get("name"),
                    "value_inr": round(float(c.get("value") or c.get("value_inr") or 0), 2),
                    "monthly_income_inr": round(float(c.get("monthly_income") or 0), 2),
                })
            return [c for c in out if c["value_inr"] > 0]

        def _people():
            out = []
            for p in (d.get("by_person") or []):
                out.append({
                    "person": p.get("person") or p.get("name"),
                    "net_worth_inr": round(float(p.get("net_worth") or p.get("value") or 0), 2),
                    "monthly_income_inr": round(float(p.get("monthly_income") or 0), 2),
                })
            return out

        liabilities = d.get("liabilities") or {}
        return {
            "currency": "INR",
            "net_worth_inr": round(float(d.get("net_worth") or 0), 2),
            "position_count": d.get("position_count"),
            "monthly_income_inr": round(float(d.get("monthly_income") or 0), 2),
            "by_asset_class": _classes(),
            "by_person": _people(),
            "liabilities": {
                "outstanding_inr": round(float(liabilities.get("outstanding") or 0), 2),
                "monthly_emi_inr": round(float(liabilities.get("monthly_emi") or 0), 2),
            },
            "note": "Live snapshot. Report only these figures; do not extrapolate.",
        }

    # ── Phase 1 — granular read tools (the advisor's eyes) ──────────────────────
    def _dash():
        from api.dashboard import aggregate
        return aggregate.build_dashboard()

    def _f(v) -> float:
        try:
            return round(float(v or 0), 2)
        except (TypeError, ValueError):
            return 0.0

    @mcp.tool
    def family_profile() -> dict:
        """The 4 people with residency (Ram/Ramprasad = NRI; Maha, Ranjeev, Sanjeev
        = residents), each one's live net worth + monthly income, and the standing
        GOALS (₹100 Cr target, sustainable ₹5L/month) with the gap to target.
        Residency drives all tax; use it on every per-person tax calculation."""
        _require_allowed()
        from api.planner import advisor_profile as prof
        d = _dash()
        people = []
        for p in (d.get("by_person") or []):
            name = p.get("person") or p.get("name")
            people.append({
                "person": name,
                "residency": prof.residency_of(name),
                "net_worth_inr": _f(p.get("net_worth") or p.get("value")),
                "monthly_income_inr": _f(p.get("monthly_income")),
            })
        nw = _f(d.get("net_worth"))
        g = prof.GOALS
        return {
            "currency": "INR",
            "people": people,
            "residency_note": "NRI = TDS at source, NRE/NRO split, DTAA (India–Kuwait), "
                              "§197 for property, no basic-exemption offset. Residents get the full slab regime + 87A.",
            "goals": {
                "net_worth_target_inr": g["net_worth_target_inr"],
                "net_worth_horizon_year": g["net_worth_horizon_year"],
                "monthly_income_target_inr": g["monthly_income_target_inr"],
                "current_net_worth_inr": nw,
                "gap_to_target_inr": round(g["net_worth_target_inr"] - nw, 2),
                "notes": g.get("notes"),
            },
            "assumptions": prof.ASSUMPTIONS,
            "note": "Goals + residency are policy (code). Balances are live. Confirm the horizon year with the user if planning a date.",
        }

    @mcp.tool
    def cashflow() -> dict:
        """Monthly money in vs out: total income, total expenses, surplus, savings
        rate — with income by source (rent, salary, dividends, interest, F&O) and
        per person, plus committed outflows (EMIs, SIPs). Feeds the ₹5L/month plan."""
        _require_allowed()
        d = _dash()
        classes = {c.get("asset_class"): c for c in (d.get("by_class") or [])}
        rent = _f((classes.get("apartments") or {}).get("monthly_income"))
        interest = _f((classes.get("bonds") or {}).get("monthly_income")) + _f((classes.get("fd") or {}).get("monthly_income"))
        liab = d.get("liabilities") or {}
        by_person = [{"person": p.get("person"), "monthly_income_inr": _f(p.get("monthly_income"))}
                     for p in (d.get("by_person") or [])]
        return {
            "currency": "INR",
            "monthly_income_inr": _f(d.get("monthly_income")),
            "monthly_expenses_inr": _f(d.get("monthly_expenses")),
            "monthly_surplus_inr": _f(d.get("monthly_surplus")),
            "savings_rate": _f(d.get("savings_rate")),
            "income_by_source_inr": {
                "salary": _f((d.get("salary") or {}).get("monthly_total")),
                "rent": rent,
                "dividends": _f((d.get("dividends") or {}).get("monthly")),
                "interest_bonds_fd": interest,
                "other_income": _f((d.get("other_income") or {}).get("monthly_total")),
            },
            "income_by_person_inr": by_person,
            "committed_outflows": {
                "loan_emi_monthly_inr": _f(liab.get("monthly_emi")),
                "planner_committed_inr": _f(d.get("planner_committed")),
            },
            "income_target_gap_inr": round(500_000 - _f(d.get("monthly_income")), 2),
            "note": "Live monthly figures. Rent = apartments income; interest = bond+FD payouts. "
                    "For the sustainable-income question, only recurring/portfolio income counts (exclude one-offs).",
        }

    @mcp.tool
    def tax_position(fy: Optional[str] = None) -> dict:
        """Per-person tax state: remaining equity-LTCG tax-free headroom (₹1.25L each
        for residents AND Ram, but Ram gets no basic-exemption offset), booked gains
        from any uploaded Tax P&L, and the F&O = business-income flag. The RATES for
        an actual sale come from tax_impact (Phase 3); this is the standing position."""
        _require_allowed()
        from api.planner import advisor_profile as prof
        from api.stocks import taxpnl
        d = _dash()
        people = [p.get("person") for p in (d.get("by_person") or []) if p.get("person")]
        stmts = {(s.get("person"), s.get("fy")): s for s in taxpnl.list_statements()}
        rows = []
        for name in people:
            st = None
            for (pn, sfy), s in stmts.items():
                if pn == name and (fy is None or sfy == fy):
                    st = s
            booked_lt = _f((st or {}).get("long_term"))
            rows.append({
                "person": name,
                "residency": prof.residency_of(name),
                "ltcg_exemption_inr": prof.LTCG_EXEMPT_PER_PERSON_INR,
                "ltcg_booked_this_fy_inr": booked_lt,
                "ltcg_headroom_left_inr": round(max(0.0, prof.LTCG_EXEMPT_PER_PERSON_INR - booked_lt), 2),
                "stcg_booked_inr": _f((st or {}).get("short_term")),
                "fno_booked_inr": _f((st or {}).get("fno_gain")),
                "has_tax_statement": st is not None,
            })
        return {
            "currency": "INR",
            "fy": fy or "latest",
            "by_person": rows,
            "family_ltcg_headroom_left_inr": round(sum(r["ltcg_headroom_left_inr"] for r in rows), 2),
            "rules": {
                "equity_ltcg": "12.5% above ₹1.25L/person; long-term after 12 months.",
                "equity_stcg": "20%.",
                "fno": "Business income at slab (not capital gains).",
                "nri_ram": "TDS deducted at source on gains; no basic-exemption offset; DTAA/TRC for lower rates.",
            },
            "data_gaps": ([] if stmts else
                          ["No Tax P&L statements uploaded — booked gains show 0. "
                           "Headroom assumes nothing booked yet. Upload Console → Reports → Tax P&L per person."]),
            "note": "Rates change each Budget (stamped FY2025-26). Exact sale tax comes from the tax_impact engine.",
        }

    @mcp.tool
    def list_holdings(asset_class: Optional[str] = None) -> dict:
        """Holdings by asset class (value, count, monthly income, CAGR, liquidity),
        optionally filtered to one class. Note: per-lot buy dates aren't all present,
        so long-term vs short-term status may be incomplete — flag it when it matters
        for a sale's tax."""
        _require_allowed()
        d = _dash()
        classes = []
        for c in (d.get("by_class") or []):
            ac = c.get("asset_class")
            if asset_class and ac != asset_class.lower():
                continue
            classes.append({
                "asset_class": ac, "label": c.get("label"),
                "value_inr": _f(c.get("value")), "count": c.get("count"),
                "monthly_income_inr": _f(c.get("monthly_income")),
                "cagr": _f(c.get("cagr")),
                "liquidity_tier": c.get("liquidity_tier"), "liquidity_label": c.get("liquidity_label"),
                "pct_of_assets": _f(c.get("pct")),
            })
        return {
            "currency": "INR",
            "gross_assets_inr": _f(d.get("gross_assets")),
            "by_asset_class": classes,
            "caveats": ["Holding-period (LT/ST) and per-stock cost basis aren't fully captured here — "
                        "confirm before computing a sale's capital-gains tax."],
            "note": "Filter with asset_class (e.g. 'stocks', 'gold', 'apartments', 'fd', 'bonds', 'ulip', 'land', 'cash').",
        }

    @mcp.tool
    def sellable_assets() -> dict:
        """GRANULAR, per-holding sell candidates for raising cash tax-efficiently —
        the family's real edge. Per person it surfaces:
          • equity holdings with unrealized gain/loss, sorted losses-first;
          • a suggested TAX-FREE harvest set = loss holdings (offset gains) + gain
            holdings that fit inside that person's remaining ₹1.25L LTCG headroom;
          • the taxable-above set (would incur LTCG).
        Plus bonds (with tax-free flag + hold-to-maturity note), FDs (break cost),
        and gold (SGB maturity is tax-free — don't sell early). Equity has no per-lot
        buy date, so LONG-TERM is ASSUMED for held equity and flagged — confirm
        before booking. Exact rupee tax comes from tax_impact (Phase 3)."""
        _require_allowed()
        from api.planner import advisor_profile as prof
        from api.stocks import taxpnl
        # ── equity, per person ──
        try:
            from api.portfolio import engine as eq_engine
            hd = eq_engine.build_summary().get("holdings_detail") or []
        except Exception:
            hd = []
        # booked LTCG this FY per person → remaining headroom
        booked_lt = {}
        for s in taxpnl.list_statements():
            booked_lt[s.get("person")] = booked_lt.get(s.get("person"), 0.0) + float(s.get("long_term") or 0)

        by_person: dict[str, list] = {}
        for h in hd:
            person = h.get("person") or "—"
            by_person.setdefault(person, []).append({
                "symbol": h.get("symbol"), "qty": _f(h.get("quantity")),
                "cost_inr": _f(h.get("invested")), "value_inr": _f(h.get("value")),
                "unrealized_gain_inr": _f(h.get("pnl")), "gain_pct": _f(h.get("pnl_pct")),
            })

        _CAP = 20                                              # cap listed holdings per bucket (keep payload small)
        equity = []
        for person, lots in by_person.items():
            headroom = max(0.0, prof.LTCG_EXEMPT_PER_PERSON_INR - booked_lt.get(person, 0.0))
            losses = sorted([l for l in lots if l["unrealized_gain_inr"] < 0],
                            key=lambda x: x["unrealized_gain_inr"])                 # biggest loss first
            gains = sorted([l for l in lots if l["unrealized_gain_inr"] > 0],
                           key=lambda x: -x["unrealized_gain_inr"])                 # biggest gain first
            free_set, running = [], 0.0
            for l in gains:                                     # fewest trades to fill the tax-free headroom
                if running + l["unrealized_gain_inr"] <= headroom:
                    free_set.append(l); running += l["unrealized_gain_inr"]
            free_ids = {id(l) for l in free_set}
            taxable = [l for l in gains if id(l) not in free_ids]
            equity.append({
                "person": person, "residency": prof.residency_of(person),
                "ltcg_headroom_left_inr": round(headroom, 2),
                "holdings_count": len(lots),
                "total_unrealized_gain_inr": round(sum(l["unrealized_gain_inr"] for l in lots), 2),
                "loss_harvest": {
                    "count": len(losses),
                    "harvestable_loss_inr": round(sum(l["unrealized_gain_inr"] for l in losses), 2),
                    "top_holdings": losses[:_CAP],             # biggest losses to book (offset other gains)
                },
                "tax_free_ltcg_harvest": {
                    "count": len(free_set), "gain_used_inr": round(running, 2),
                    "holdings": free_set[:_CAP],               # sell these LT winners → 0 tax (fits headroom)
                    "note": "Long-term winners whose gain fits the ₹1.25L headroom → 0 tax. Rebuy to reset cost basis.",
                },
                "taxable_above_headroom": {
                    "count": len(taxable),
                    "gain_inr": round(sum(l["unrealized_gain_inr"] for l in taxable), 2),
                    "biggest_gainers": taxable[:5],            # only a few — selling these books 12.5% LTCG
                },
            })
        equity.sort(key=lambda e: -e["ltcg_headroom_left_inr"])

        # ── bonds ──
        bonds = []
        try:
            from api.bonds import store as bs
            for b in bs.list_bonds():
                val = _f(b.get("redemption_value") or (_f(b.get("face_value")) * _f(b.get("quantity"))))
                bonds.append({
                    "issuer": b.get("issuer"), "owner": b.get("owner"),
                    "value_inr": val, "coupon_rate": _f(b.get("coupon_rate")),
                    "tax_free": bool(b.get("tax_free")), "maturity_date": b.get("maturity_date"),
                    "purchase_date": b.get("purchase_date"),
                    "note": ("TAX-FREE bond — its coupon is tax-free; selling loses that stream. Prefer holding."
                             if b.get("tax_free") else
                             "Taxable coupon (slab). Sale = capital gain (LT 12.5% if listed & >12m)."),
                })
        except Exception:
            pass

        # ── FDs ──
        fds = []
        try:
            from api.fd import store as fds_store
            for f in fds_store.list_fds():
                fds.append({
                    "bank": f.get("bank"), "owner": f.get("owner"),
                    "principal_inr": _f(f.get("principal")), "rate": _f(f.get("interest_rate")),
                    "sellable_on": f.get("sellable_on"), "tenure_months": f.get("tenure_months"),
                    "note": "Breaking early forfeits a rate penalty + reverses accrued interest; interest is slab-taxed. Break the lowest-rate / nearest-maturity first.",
                })
        except Exception:
            pass

        # ── real estate (apartments + land) — indivisible, biggest chunk ──
        real_estate = []
        try:
            from api.dashboard import aggregate as agg
            for p in agg.gather_positions():
                if p.get("asset_class") not in ("apartments", "land", "built"):
                    continue
                owner = p.get("owner")
                gain = round(_f(p.get("value")) - _f(p.get("invested")), 2)
                real_estate.append({
                    "asset_class": p.get("asset_class"), "name": p.get("name"),
                    "owner": owner, "residency": prof.residency_of(owner),
                    "value_inr": _f(p.get("value")), "invested_inr": _f(p.get("invested")),
                    "unrealized_gain_inr": gain, "monthly_rent_inr": _f(p.get("monthly_income")),
                    "liquidity": p.get("liquidity_label"), "sellable_on": p.get("sellable_on"),
                })
        except Exception:
            pass
        re_note = ("Indivisible — you sell a whole parcel, a big one-off LTCG event (12.5% after 24m; "
                   "§54/§54F to reinvest in a house, §54EC bonds up to ₹50L to shelter). Faster + no tax: "
                   "loan-against-property. For Ram (NRI), the BUYER withholds TDS on the FULL sale value "
                   "unless a §197 lower-TDS certificate is arranged in advance — plan it early.")

        # ── cash, and INDIVIDUAL gold + ULIP items (every asset, not a rollup) ──
        d = _dash()
        classes = {c.get("asset_class"): c for c in (d.get("by_class") or [])}
        cash_inr = _f((classes.get("cash") or {}).get("value"))
        gold_items, ulip_items = [], []
        try:
            from api.dashboard import aggregate as agg2
            for p in agg2.gather_positions():
                ac = p.get("asset_class")
                if ac == "gold":
                    nm = str(p.get("name") or "")
                    is_sgb = any(w in nm.lower() for w in ("sgb", "sovereign"))
                    liquid = any(w in nm.lower() for w in ("coin", "bar", "biscuit", "etf", "digital"))
                    gold_items.append({
                        "name": p.get("name"), "owner": p.get("owner"),
                        "residency": prof.residency_of(p.get("owner")), "value_inr": _f(p.get("value")),
                        "kind": "SGB" if is_sgb else ("liquid_gold" if liquid else "jewellery"),
                        "note": ("SGB — maturity redemption is TAX-FREE; don't sell early." if is_sgb else
                                 "Coin/bar/ETF — easy to sell (12.5% LTCG >24m) or take a gold loan (no sale, no tax)." if liquid else
                                 "Jewellery — usually kept; a gold loan avoids selling. 12.5% LTCG >24m if sold."),
                    })
                elif ac == "ulip":
                    ulip_items.append({
                        "name": p.get("name"), "owner": p.get("owner"),
                        "residency": prof.residency_of(p.get("owner")), "value_inr": _f(p.get("value")),
                        "note": "Locked ~5 years; surrender early loses value. Post-lock partial withdrawal possible. "
                                "If annual premium > ₹2.5L (post-Feb-2021), gains taxed like equity.",
                    })
        except Exception:
            pass
        gold_items.sort(key=lambda g: -g["value_inr"])
        gold = {"total_value_inr": round(sum(g["value_inr"] for g in gold_items), 2),
                "count": len(gold_items), "items": gold_items[:30],
                "note": "All items listed (biggest first). SGB maturity is tax-free; coins/bars/ETF are liquid; "
                        "a gold loan raises cash with no sale and no tax."}
        ulip = {"total_value_inr": round(sum(u["value_inr"] for u in ulip_items), 2), "items": ulip_items}

        # ── liabilities (per loan) — for prepay / debt-capacity decisions ──
        liabilities = []
        try:
            from api.loans import store as ls, engine as le
            for l in le.build_summary(ls.list_loans()).get("loans", []):
                if l.get("closed"):
                    continue
                liabilities.append({
                    "lender": l.get("lender"), "owner": l.get("owner"),
                    "residency": prof.residency_of(l.get("owner")),
                    "loan_type": l.get("loan_type"),
                    "outstanding_inr": _f(l.get("outstanding_inr") or l.get("outstanding")),
                    "emi_monthly_inr": _f(l.get("emi_inr")),
                    "interest_rate": _f(l.get("interest_rate")),
                    "note": "Prepay only if the rate exceeds the after-tax return of what you'd sell to repay. "
                            "A home loan's §24 interest + §80C principal deductions often justify keeping it.",
                })
        except Exception:
            pass

        return {
            "currency": "INR",
            "equity_by_person": equity,
            "real_estate": real_estate,
            "real_estate_note": re_note,
            "cash_inr": cash_inr,
            "ulip": ulip,
            "gold": gold,
            "liabilities": liabilities,
            "bonds": bonds,
            "fds": fds,
            "harvest_order": "1) sell loss holdings (offset gains) → 2) sell the tax_free_ltcg_harvest set (0 tax) → "
                             "3) pledge/loan-against-securities instead of selling → 4) sell taxable_above holdings (12.5%) → "
                             "5) break lowest FD → avoid selling tax-free bonds / SGBs early.",
            "caveats": ["Equity buy dates aren't stored → long-term is ASSUMED; confirm before booking (Tax P&L / contract notes).",
                        "Headroom assumes booked LTCG = uploaded Tax P&L (0 if none). Exact tax = tax_impact (Phase 3)."],
        }

    @mcp.tool
    def tax_impact(items: list) -> dict:
        """Phase 3 — exact per-person capital-gains tax for a proposed set of sells.
        Each item: {person, asset_class ('equity'|'property'|'gold'|'debt'|'bond'),
        gain_inr, term ('long'|'short')}. Uses each person's remaining ₹1.25L equity-
        LTCG headroom. Rates FY2025-26 + 4% cess. Ram (NRI): same liability, TDS at
        source. This is the source of truth for a sale's tax — don't hand-compute."""
        _require_allowed()
        from api.planner import engines as eng
        # remaining per-person equity-LTCG headroom (from any uploaded Tax P&L)
        from api.stocks import taxpnl
        from api.planner import advisor_profile as prof
        booked = {}
        for s in taxpnl.list_statements():
            booked[s.get("person")] = booked.get(s.get("person"), 0.0) + float(s.get("long_term") or 0)
        headroom = {}
        for p in {i.get("person") for i in (items or [])}:
            headroom[p] = max(0.0, prof.LTCG_EXEMPT_PER_PERSON_INR - booked.get(p, 0.0))
        return eng.tax_on_sale(list(items or []), headroom=headroom)

    @mcp.tool
    def funding_plan(amount_inr: float) -> dict:
        """Phase 3 — raise `amount_inr` the tax-efficient way. Builds the funding
        waterfall from live capacity: idle cash → tax-free-LTCG harvest (₹1.25L/
        person) → loan-against-securities (pledge, no tax) → sell LT equity → loan-
        against-property. Returns the mix, interest cost, and shortfall. Pair with
        sellable_assets (which lots) + tax_impact (exact tax on any equity sold)."""
        _require_allowed()
        from api.planner import advisor_profile as prof, engines as eng
        d = _dash()
        classes = {c.get("asset_class"): c for c in (d.get("by_class") or [])}
        cash = _f((classes.get("cash") or {}).get("value"))
        equity_val = _f((classes.get("stocks") or {}).get("value"))
        re_val = _f((classes.get("apartments") or {}).get("value")) + _f((classes.get("land") or {}).get("value"))
        # tax-free harvest capacity ≈ ₹1.25L headroom × number of holders (rough; sellable_assets is exact)
        holders = len([p for p in (d.get("by_person") or []) if p.get("person")])
        sources = {
            "cash": cash,
            "tax_free_harvest": prof.LTCG_EXEMPT_PER_PERSON_INR * holders,  # gain that's tax-free; ≈ cash raised at ~that level
            "pledge_capacity": round(equity_val * 0.5, 2),                 # ~50% LTV on equity
            "sellable_lt_equity": equity_val,
            "loan_against_property": round(re_val * 0.6, 2),               # ~60% LTV on property
        }
        plan = eng.funding_waterfall(float(amount_inr), sources)
        plan["capacity_inr"] = {k: round(v, 2) for k, v in sources.items()}
        plan["reminder"] = ("Prefer pledge/LAS + a home loan over selling compounders; for Ram's property, arrange "
                            "§197 before any sale. Use sellable_assets for the exact lots and tax_impact for the tax.")
        return plan

    @mcp.tool
    def full_data_dump() -> dict:
        """EVERYTHING, raw — every asset with ALL its fields so you fully understand
        the family before planning. Real estate (location, area_sqft, bought_date,
        bought_price, rate_per_sqft, current_estimated_price, rent, owner), salary,
        bonds, FDs, gold items, ULIP, cash, loans, and per-person equity totals — each
        tagged with residency (Ram/Ramprasad = NRI). For PROPERTIES you MUST web-search
        the current market rate/sqft for each `location` and reconcile it against
        current_estimated_price before valuing a sale. Call sellable_assets for the
        granular per-stock harvest lots."""
        _require_allowed()
        from api.planner import advisor_profile as prof

        def _tag(rows):
            return [{**dict(r), "residency": prof.residency_of(r.get("owner") or r.get("person"))} for r in rows]

        out: dict = {"currency": "INR"}
        for key, imp in (("land", "api.land"), ("apartments", "api.apartments")):
            try:
                mod = __import__(imp, fromlist=["store"]).store
                rows = mod.list_parcels() if key == "land" else mod.list_units()
                out[key] = _tag(rows)
            except Exception:
                out[key] = []
        try:
            from api.salary import store as ss
            out["salary"] = ss.list_entries()
        except Exception:
            out["salary"] = []
        try:
            from api.bonds import store as bs
            out["bonds"] = _tag(bs.list_bonds())
        except Exception:
            out["bonds"] = []
        try:
            from api.fd import store as fds
            out["fds"] = _tag(fds.list_fds())
        except Exception:
            out["fds"] = []
        try:
            from api.dashboard import aggregate as agg
            pos = agg.gather_positions()
            out["gold"] = [{"name": p.get("name"), "owner": p.get("owner"), "value_inr": _f(p.get("value")),
                            "residency": prof.residency_of(p.get("owner"))} for p in pos if p.get("asset_class") == "gold"]
            out["ulip"] = [{"name": p.get("name"), "owner": p.get("owner"), "value_inr": _f(p.get("value")),
                            "residency": prof.residency_of(p.get("owner"))} for p in pos if p.get("asset_class") == "ulip"]
        except Exception:
            out["gold"], out["ulip"] = [], []
        try:
            from api.loans import store as lo, engine as le
            out["loans"] = le.build_summary(lo.list_loans()).get("loans", [])
        except Exception:
            out["loans"] = []
        d = _dash()
        classes = {c.get("asset_class"): c for c in (d.get("by_class") or [])}
        out["cash_inr"] = _f((classes.get("cash") or {}).get("value"))
        try:
            from api.portfolio import engine as eqe
            per: dict = {}
            for h in eqe.build_summary().get("holdings_detail") or []:
                p = h.get("person") or "—"
                g = per.setdefault(p, {"person": p, "residency": prof.residency_of(p),
                                       "value_inr": 0.0, "gain_inr": 0.0, "holdings": 0})
                g["value_inr"] += float(h.get("value") or 0); g["gain_inr"] += float(h.get("pnl") or 0); g["holdings"] += 1
            out["equity_by_person"] = [{**v, "value_inr": round(v["value_inr"], 2), "gain_inr": round(v["gain_inr"], 2)}
                                       for v in per.values()]
        except Exception:
            out["equity_by_person"] = []
        out["net_worth_inr"] = _f(d.get("net_worth"))
        out["allocation_pct"] = {c.get("asset_class"): _f(c.get("pct")) for c in (d.get("by_class") or [])}
        out["reallocation_hint"] = ("Assets are heavily skewed to real estate — buying an apartment while SELLING an "
                                    "apartment keeps the mix roughly flat; funding purely by selling equity/liquid tilts "
                                    "it even more toward property.")
        return out

    # ── Expenses: statement → categorised rows → the app's review inbox ────────
    @mcp.tool
    def expense_categories() -> dict:
        """THE category vocabulary of the Expenses tab — call this BEFORE
        categorising a bank/credit-card statement. Returns the exact expense
        categories, the income categories, the four family members (valid owners),
        and the submission contract. Use ONLY these strings: a category that isn't
        on the list is kept as a *suggestion* and flagged for the user, so prefer an
        existing one and leave `category` empty when nothing genuinely fits —
        never invent a category to look complete."""
        _require_allowed()
        from api.expenses import store as exp_store, inbox
        try:
            from api.people.store import CANON_NAMES
            owners = list(CANON_NAMES)
        except Exception:
            owners = ["Ranjeev", "Sanjeev", "Ramprasad", "Maha"]
        # who owns which bank account, and which merchants have historically been
        # whose — so a row can be attributed to the right person, not just the
        # account holder. The app infers this too; sending it is a bonus.
        accounts: list[dict] = []
        try:
            from api.cash import store as cash_store
            from api.people.store import canon_owner
            for it in cash_store.list_items():
                label = (it.get("account_label") or it.get("where") or "").strip()
                if label:
                    accounts.append({"account": label, "owner": canon_owner(it.get("owner")),
                                     "type": it.get("type")})
        except Exception:
            pass
        hints: list[dict] = []
        try:
            m = inbox.OwnerMatcher()
            exp_rows, inc_rows, _cash = inbox._ledger_rows()      # cached, ~0 ms warm
            inbox._index_ledger(exp_rows, inc_rows, m)
            for merchant, counts in sorted(m.by_merchant.items(), key=lambda kv: -sum(kv[1].values()))[:40]:
                who = m._dominant(counts, min_rows=2, min_share=0.6)
                if who:
                    hints.append({"merchant": merchant, "usually": who, "seen": sum(counts.values())})
        except Exception:
            pass

        return {
            "expense_categories": exp_store.all_categories(),
            "income_categories": inbox.income_categories(),
            "owners": owners,
            "accounts_by_owner": accounts,
            "merchant_owner_hints": hints,
            "currency": "INR",
            "how_to_categorise": [
                "Every DEBIT / withdrawal is an expense; every CREDIT / deposit is income.",
                "FIRST, always try to match one of the EXACT category strings from the lists "
                "above — that is strongly preferred, and you must use the list's exact spelling "
                "when one fits.",
                "ONLY IF nothing on the list genuinely fits, make up a short, sensible category "
                "name for it (e.g. 'Pet care', 'Temple') rather than leaving it blank. The app "
                "keeps your new category and flags it so the user can accept it in one tap.",
                "Classify EACH transaction from THIS row's narration (merchant, UPI handle, NACH "
                "mandate, ATM, fuel pump, salary credit…). There is no merchant memory, so a "
                "category is never carried over from another transaction — decide every row on "
                "its own. (Every row should get a category; only leave it blank if you truly "
                "cannot tell what the transaction is at all.)",
                "Set `confidence` 0–1 per row so low-confidence rows surface first for review.",
                "WHOSE is it: set `owner` when you can tell — a family member named in the "
                "narration, a merchant in merchant_owner_hints, or something the user told you "
                "in the conversation. Otherwise LEAVE IT OUT: the app then infers it (name in "
                "the narration → merchant history → the account holder from accounts_by_owner → "
                "category history) and marks anything it had to guess for the user to confirm. "
                "A wrong owner is worse than no owner — it silently misattributes the spend.",
                "`name` should be a short human merchant name; put the raw statement text in "
                "`narration` so the user can verify each row against the statement.",
                "Amounts are positive numbers in INR — the direction carries the sign.",
                "Send EVERY line of the statement, including ones you couldn't categorise. "
                "Nothing is written to the ledger until the user approves in the app.",
            ],
            "submit_with": ("submit_statement_lines(lines='date|amount|D or C|narration' per line) "
                            "— the fast path, use it for any real statement. "
                            "submit_expense_transactions(transactions=[…]) is the verbose "
                            "per-row form; only worth it for a handful of rows."),
            "speed_note": ("Writing JSON per row is what makes a big import slow — it is "
                           "thousands of output tokens. The compact lines cost ~60% fewer, and "
                           "leaving category/name/owner out (the app derives them) cuts it "
                           "further. Transcribe straight through; do not reason row by row."),
        }

    @mcp.tool
    def submit_expense_transactions(transactions: list, account: Optional[str] = None,
                                    owner: Optional[str] = None, source: Optional[str] = None,
                                    note: Optional[str] = None) -> dict:
        """Push the categorised statement lines into the Expenses tab's REVIEW
        INBOX (staging — nothing hits the ledger until the user approves in the
        app). Call expense_categories() first for the valid category strings.

        Each item of `transactions` is an object:
          date        "YYYY-MM-DD"  (required — the transaction date)
          amount      positive number in INR (required)
          direction   "debit" (money out → expense) | "credit" (money in → income)
          name        short merchant/payee name shown in the app
          narration   the raw statement text (so the user can verify the row)
          category    one of the strings from expense_categories()
          owner       one of the family members, else the batch `owner` is used
          confidence  0–1, how sure you are of the category
          note        optional one-line reason for the categorisation

        `account` is the account the statement came from (e.g. "HDFC ••4321"),
        `owner` the default person for rows without one.

        Returns what landed: counts, spend/income totals, how many need review, and
        which rows look like DUPLICATES of what's already logged (re-sending the
        same statement is safe — repeats are flagged, not double-counted)."""
        _require_allowed()
        from api.expenses import inbox
        if not isinstance(transactions, list) or not transactions:
            raise ValueError("`transactions` must be a non-empty list of transaction objects.")
        res = inbox.create_batch(list(transactions), source=source or "Claude",
                                 account=account, owner=owner, note=note)
        res["next_step"] = ("Tell the user: open the Expenses tab → the 'From Claude' inbox banner, "
                            f"review {res.get('pending', 0)} rows and hit Approve. "
                            "Nothing is in their expenses until they do.")
        from api.portfolio import store as kv
        if not kv.cache_durable():
            res["warning"] = ("The app_cache table isn't set up in Supabase, so this batch is stored "
                              "on the server's local disk and will be lost on the next redeploy. "
                              "Tell the user to run the app_cache migration (SUPABASE.md) and to "
                              "review this batch soon.")
        return res

    @mcp.tool
    def submit_statement_lines(lines: str, account: Optional[str] = None,
                               owner: Optional[str] = None, source: Optional[str] = None,
                               note: Optional[str] = None) -> dict:
        """THE FAST WAY to import a statement — prefer this over
        submit_expense_transactions for anything longer than ~20 rows.

        Send ONE compact line per transaction in a single string:

            date|amount|D or C|narration

        e.g.  2026-07-24|285|D|UPI-ZEPTO-ZEPTOONLINE@YBL-620566734991
              2026-07-24|1210.50|C|NEFT CR-AUBL0002011-LAXMI INDIA FINANCE

        Rules that make this fast — follow them exactly:
        • DO NOT write the merchant name, the owner, or a confidence. The app
          derives all three itself.
        • DO categorise every row: append a 5th field `|Category`. FIRST match an
          exact string from expense_categories() (strongly preferred, exact spelling).
          Only if nothing on the list fits, make up a short sensible category for that
          row instead of leaving it blank — the app keeps it and flags it for one-tap
          acceptance. Decide each row from its own narration; there is no merchant
          memory, so nothing carries over from another row.
        • DO NOT reason row by row or comment between lines — transcribe the
          statement straight through in one go. The whole point is to spend as few
          output tokens as possible; a 130-row statement is seconds this way and
          minutes as JSON.
        • Amounts are positive; D = money out, C = money in. Any of | tab ; ~
          works as the separator. A header line is ignored.

        Everything else behaves exactly like submit_expense_transactions: rows are
        STAGED for the user to approve, duplicates are flagged, nothing touches the
        ledger."""
        _require_allowed()
        from api.expenses import inbox
        rows, bad = inbox.parse_compact_lines(lines or "")
        if not rows:
            raise ValueError(
                "No transactions found. Each line must be `date|amount|D or C|narration`, "
                f"e.g. 2026-07-24|285|D|UPI-ZEPTO-... (skipped: {bad[:3]})")
        res = inbox.create_batch(rows, source=source or "Claude (fast import)",
                                 account=account, owner=owner, note=note)
        res["unreadable_lines"] = bad[:20]
        res["next_step"] = ("Tell the user: open the Expenses tab → the 'From Claude' banner, "
                            f"review {res.get('pending', 0)} rows and hit Approve. "
                            "Nothing is in their ledger until they approve.")
        return res

    @mcp.tool
    def remember_merchant(merchant: str, owner: Optional[str] = None,
                          category: Optional[str] = None) -> dict:
        """Teach the app who a merchant usually belongs to (an OWNER rule). Use it
        when the user says "the Amazon card is always Sanjeev's". Pass an empty
        owner to clear the rule.

        NOTE: categories are intentionally NOT remembered. Every statement is
        re-classified fresh, so a category is never carried forward from a past
        transaction — a `category` argument here is ignored. This is by design: it
        prevents one wrong categorisation from propagating to every future row of
        the same merchant."""
        _require_allowed()
        from api.expenses import inbox
        if not (merchant or "").strip():
            raise ValueError("Which merchant? `merchant` is required.")
        rules = inbox.set_merchant_rule(merchant, owner=owner)   # owner only — category ignored
        key = inbox._norm_merchant(merchant)
        return {"merchant": merchant, "rule": rules.get(key), "rules_total": len(rules),
                "note": ("Owner rule saved. Categories are not remembered — each transaction is "
                         "re-categorised fresh, so a wrong category never carries forward.")}

    @mcp.tool
    def merchant_memory() -> dict:
        """The OWNER rules the app will apply on the next import (merchant → who).
        There is NO category memory: every statement is re-classified fresh, so a
        category is never carried over from a past transaction."""
        _require_allowed()
        from api.expenses import inbox
        return {
            "owner_rules": [{"merchant": r.get("merchant") or k, "owner": r.get("owner"),
                             "set_at": r.get("updated_at")}
                            for k, r in (inbox.merchant_rules() or {}).items() if r.get("owner")],
            "category_memory": "disabled — every transaction is categorised fresh from the "
                               "category list, so nothing propagates from earlier rows",
            "precedence": "owner rule > name in narration > merchant history > account holder",
        }

    @mcp.tool
    def expense_inbox_status() -> dict:
        """What's currently waiting in the Expenses review inbox — batches pushed
        in, how many rows are still pending, and how many need the user's attention
        (missing/unknown category, or a suspected duplicate)."""
        _require_allowed()
        from api.expenses import inbox
        st = inbox.inbox_status()
        st["note"] = ("Rows stay here until the user approves them in the Expenses tab. "
                      "Approved rows become one-time expenses / other-income entries.")
        return st

    @mcp.tool
    def expense_month(period: Optional[str] = None) -> dict:
        """What the family actually spent and earned in a month (YYYY-MM, default
        the current month): total spend with the by-category and by-person splits,
        logged income by category, and the live cash + bank balance in hand. Use it
        to answer 'where did the money go' and to check a statement import landed."""
        _require_allowed()
        from datetime import date as _date
        from api.expenses import store as exp_store, engine as exp_engine
        from api.dashboard import aggregate as agg
        p = period or f"{_date.today().year:04d}-{_date.today().month:02d}"
        summ = exp_engine.month_summary(exp_store.list_expenses(), p, p, canon=agg.canon_owner)
        income = {"total_inr": 0.0, "by_category": []}
        try:
            from api.other_income import store as inc_store, engine as inc_engine
            ilog = inc_engine.log_summary(inc_store.list_log(p), canon=agg.canon_owner)
            income = {"total_inr": _f(ilog.get("monthly_total")),
                      "by_category": ilog.get("by_category", []),
                      "by_person": ilog.get("by_person", [])}
        except Exception:
            pass
        in_hand = 0.0
        try:
            from api.cash import store as cstore, engine as cengine
            in_hand = _f(cengine.build_summary(cstore.list_items()).get("total"))
        except Exception:
            pass
        return {
            "currency": "INR", "period": p,
            "spend_inr": _f(summ.get("total_inr")),
            "spend_by_category": summ.get("by_category", []),
            "spend_by_person": [{"person": x.get("person"), "spent_inr": x.get("spent_inr")}
                                for x in summ.get("by_person", [])],
            "recurring_inr": _f(summ.get("recurring_inr")),
            "one_off_inr": _f(summ.get("onetime_inr")),
            "income_inr": income["total_inr"], "income_by_category": income.get("by_category", []),
            "net_inr": round(income["total_inr"] - _f(summ.get("total_inr")), 2),
            "cash_in_hand_inr": in_hand,
            "note": "Logged figures only — anything still sitting in the review inbox is NOT counted.",
        }

    @mcp.tool
    def liquidity_profile() -> dict:
        """How fast each asset class converts to cash and the friction involved
        (lock-ins, penalties, surrender loss, pledge option, NRI repatriation) —
        the raw material for the tax-efficient funding waterfall."""
        _require_allowed()
        d = _dash()
        # per-class friction notes (rules-based; complements the live liquidity tier)
        friction = {
            "cash": "Instant. Use first. For Ram, NRE cash is freely repatriable; NRO is capped.",
            "fd": "2–3 days but breaking early forfeits a rate penalty + reverses accrued interest. Ram: NRE interest tax-free, NRO taxable @30% TDS.",
            "stocks": "T+1 settlement. Can PLEDGE for a loan-against-securities instead of selling (no tax, keeps compounding). Sale → LTCG/STCG; Ram pays TDS at source.",
            "gold": "1–2 days (ETF/digital) or a gold loan (no sale, no tax). SGB: maturity redemption tax-free.",
            "bonds": "1–2 weeks to sell; coupons are the income. Interest slab-taxed.",
            "ulip": "Locked ~5 years; surrender before that loses value. Post-lock partial withdrawal possible.",
            "apartments": "3–4 months to sell; illiquid. Loan-against-property is faster. Ram: TDS on FULL sale value unless §197 certificate; §54/54EC to shelter gains.",
            "land": "~6 months; least liquid. Same NRI property-sale rules as apartments.",
        }
        rows = []
        for c in (d.get("by_class") or []):
            ac = c.get("asset_class")
            rows.append({
                "asset_class": ac, "value_inr": _f(c.get("value")),
                "liquidity_tier": c.get("liquidity_tier"),          # 0=instant … 4=~6 months
                "speed": c.get("liquidity_label"),
                "friction": friction.get(ac, "See asset-class rules."),
            })
        rows.sort(key=lambda r: (r["liquidity_tier"] if r["liquidity_tier"] is not None else 9))
        return {
            "currency": "INR",
            "by_asset_class": rows,
            "waterfall_hint": "Cheapest cash first: idle cash → liquid funds → harvest LTCG within headroom "
                              "→ pledge/loan-against-securities (no tax) → gold/LIC loan → sell LT equity above "
                              "headroom → avoid STCG / breaking FDs / surrendering LIC → consider a home loan.",
            "note": "Tiers/labels are live; friction notes are standing rules. Ram (NRI) has extra TDS + repatriation friction.",
        }

    return mcp


def build_http_app(path: str = "/"):
    """The mounted ASGI app for the MCP server (Streamable HTTP). Its `.lifespan`
    must be wired into the parent FastAPI app so the session manager runs."""
    return build_mcp().http_app(path=path, transport="http")
