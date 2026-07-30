"""
Import a parsed Aionion workbook into the app's stores.
=======================================================

Equities, ETFs and MFs → stock_holdings (one manual account); the equity engine
re-prices them LIVE by symbol, so market value is never the stale statement
number. Bonds → the bonds store with a coupon/maturity schedule, so the payout
timeline is generated correctly.

Key correctness choices:
  * avg_price is the REAL cost basis from the statement (Aionion provides it),
    so gain/XIRR are correct out of the box — unlike a CAS which has no cost.
  * MFs have no exchange symbol; they price by ISIN via the MF-NAV path, and we
    seed their current NAV/value from the statement so they show a value even
    before the first live NAV refresh.
  * Bonds: coupon + maturity come straight from the statement; the schedule
    engine derives the per-date interest/principal from them. face_value is
    derived from principal/qty when not stated.
"""
from __future__ import annotations

import uuid
from datetime import date
from typing import Any


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def build_preview(parsed: dict[str, Any], owner: str | None = None) -> dict[str, Any]:
    """Map a parsed workbook to draft records for review. Nothing is written."""
    investor = parsed.get("investor") or {}
    person = owner or investor.get("name") or "Self"
    as_of = date.today().isoformat()

    account = {
        "id": _new_id("acct"),
        "person": person,
        "broker": "Aionion",
        "account_label": f"Aionion {investor.get('client_id') or ''}".strip(),
        "kind": "manual",
        "status": "connected",
    }

    holdings: list[dict[str, Any]] = []
    for e in parsed.get("equities") or []:
        holdings.append({
            "id": _new_id("hold"),
            "account_id": account["id"],
            "person": person,
            "broker": "Aionion",
            "account_label": account["account_label"],
            "symbol": e["symbol"],
            "name": e["symbol"],
            "exchange": "NSE",
            "isin": e.get("isin"),
            "currency": "INR",
            "quantity": e.get("quantity"),
            # REAL cost basis from the statement → correct gains/XIRR.
            "avg_price": e.get("avg_price"),
            "import_price": e.get("market_price"),
            "_value": e.get("market_value"),
            "_kind": e.get("kind"),
            "_source": "aionion",
        })

    # MFs → holdings too. They have no exchange ticker, but each needs a UNIQUE
    # symbol key or the engine (which groups + prices by symbol) would merge all
    # funds into one row. Use "MF:<amfi scheme code>" — stable, unique, and the
    # AMFI code is exactly what a future live-NAV feed keys on. Until that feed
    # exists, value falls back to the statement NAV (import_price), so the fund
    # still shows a correct value and stays a distinct line.
    for m in parsed.get("mutual_funds") or []:
        code = (m.get("scheme_code") or m.get("isin") or "").strip()
        holdings.append({
            "id": _new_id("hold"),
            "account_id": account["id"],
            "person": person,
            "broker": "Aionion",
            "account_label": account["account_label"],
            "symbol": f"MF:{code}" if code else None,
            "name": m.get("scheme"),
            "exchange": "MF",
            "isin": m.get("isin"),
            "currency": "INR",
            "quantity": m.get("units"),
            "avg_price": m.get("avg_cost"),
            "import_price": m.get("nav"),      # statement NAV → value until live NAV lands
            "_value": m.get("value"),
            "_kind": "mf",
            "_scheme_code": m.get("scheme_code"),
            "_folio": m.get("folio"),
            "_source": "aionion",
        })

    bond_rows: list[dict[str, Any]] = []
    for b in parsed.get("bonds") or []:
        qty = b.get("quantity") or 0
        principal = b.get("invested") or b.get("value") or 0
        face = round(principal / qty, 2) if qty else 1000.0
        bond_rows.append({
            "id": _new_id("bond"),
            "owner": person,
            "broker": "Aionion",
            "issuer": b.get("issuer"),
            "bond_type": "Corporate NCD",
            "isin": b.get("isin"),
            "rating": "",
            "tax_free": False,
            "face_value": face,
            "quantity": qty,
            # Cost = principal per unit; a statement rarely splits premium/discount.
            "buy_price": round(principal / qty, 2) if qty else face,
            "coupon_rate": b.get("coupon_rate") or 0.0,
            # NCDs from these issuers pay monthly; the schedule engine anchors to
            # the maturity day. Users can change frequency on the bonds page.
            "coupon_freq": "monthly",
            "repayment_type": "bullet",
            "purchase_date": as_of,
            "first_payment_date": None,
            "maturity_date": b.get("maturity_date"),
            "ytm_input": b.get("ytm"),
            "note": f"Imported from Aionion {investor.get('client_id') or ''}".strip(),
            "_value": b.get("value"),
            "_needs": [] if b.get("maturity_date") else ["maturity_date"],
            "_source": "aionion",
        })

    return {
        "investor": investor,
        "as_of": as_of,
        "owner": person,
        "account": account,
        "holdings": holdings,
        "bonds": bond_rows,
        "totals": parsed.get("totals") or {},
        "warnings": parsed.get("warnings") or [],
        "counts": {
            "holdings": len(holdings),
            "equities": sum(1 for h in holdings if h["_kind"] in ("equity", "etf")),
            "mutual_funds": sum(1 for h in holdings if h["_kind"] == "mf"),
            "bonds": len(bond_rows),
        },
    }


def _strip_private(rec: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in rec.items() if not k.startswith("_")}


def commit(preview: dict[str, Any], what: set[str] | None = None) -> dict[str, Any]:
    """Persist a reviewed preview into the stores."""
    what = what or {"holdings", "bonds"}
    report: dict[str, Any] = {"holdings": 0, "bonds": 0, "accounts": 0, "errors": []}

    if "holdings" in what and preview.get("holdings"):
        try:
            from ..portfolio import store as pstore

            acct_draft = preview["account"]
            # Reuse an existing Aionion account for this client id, else create.
            existing = pstore.list_accounts()
            label = acct_draft["account_label"]
            match = next((a for a in existing if a.get("account_label") == label), None)
            account = match or pstore.add_account({
                "person": acct_draft["person"],
                "broker": acct_draft["broker"],
                "account_label": label,
                "kind": "manual",
                "status": "connected",
            })
            if not match:
                report["accounts"] = 1
            rows = [_strip_private(h) for h in preview["holdings"]]
            pstore.replace_holdings(account, rows)
            report["holdings"] = len(rows)
        except Exception as e:
            report["errors"].append(f"holdings: {e}")

    if "bonds" in what and preview.get("bonds"):
        try:
            from ..bonds import store as bstore

            n = 0
            for rec in preview["bonds"]:
                payload = _strip_private(rec)
                if not payload.get("maturity_date"):
                    report["errors"].append(f"{payload.get('issuer')}: no maturity date, skipped")
                    continue
                payload.pop("id", None)
                bstore.add_bond(payload)
                n += 1
            report["bonds"] = n
        except Exception as e:
            report["errors"].append(f"bonds: {e}")

    return report
