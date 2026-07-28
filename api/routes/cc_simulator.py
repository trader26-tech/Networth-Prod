"""
Covered-call simulator — paper-trading sandbox with realistic Indian charge
+ tax accounting.

Flow:
  1. POST /simulator     — create a new simulation with starting capital;
                            system auto-buys NiftyBees at live price.
  2. GET  /simulator/:id — live state (NB MTM, active call, P&L breakdown,
                            charges paid, tax owed, net realised).
  3. POST /simulator/:id/sell-call — paper-write a Nifty CE on the chain.
  4. POST /simulator/:id/close-call — buy back the active call, realise P&L.
  5. POST /simulator/:id/sell-shares — partial NB exit to free cash.
  6. POST /simulator/:id/buy-shares  — deploy cash back into NB.
  7. DELETE /simulator/:id — wipe the sandbox.

All money flows go through the cash_balance ledger. Every tick of the live
state recomputes total_charges, total_taxes, and net_pnl from the underlying
history so the UI can show the breakdown without redoing the math.
"""
from __future__ import annotations
import datetime as _dt
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from api import cc_simulator_store as _store
from api import cc_charges as _ch
from api import options_engine as opt_eng
from api.core.chain import spot_for, build_real_chain
from api import state

router = APIRouter(prefix="/api/cc-simulator", tags=["cc_simulator"])

NIFTYBEES_RATIO = 100.0


# ── Helpers ───────────────────────────────────────────────────────────────────

def _niftybees_live() -> float:
    """Live NSE:NIFTYBEES LTP, or derived from Nifty/100 if Kite isn't connected.
       This mirrors the rest of the app — paper sim should track real prices."""
    kite = state.get_kite()
    if kite:
        try:
            data = kite.ltp(["NSE:NIFTYBEES"])
            p = data.get("NSE:NIFTYBEES", {}).get("last_price")
            if p and float(p) > 0:
                return float(p)
        except Exception:
            pass
    # Offline fallback
    return round(spot_for("NIFTY") / NIFTYBEES_RATIO, 2)


def _live_call_price(strike: float, expiry: str, spot: float) -> dict | None:
    """Best-effort live LTP for a specific Nifty CE strike + expiry.
       Returns {price, iv, delta} or None if Kite is offline / strike not in chain."""
    if not state.get_kite():
        return None
    try:
        chain_data = build_real_chain("NIFTY", expiry, spot)
    except Exception:
        return None
    for row in chain_data["chain"]:
        if abs(float(row["strike"]) - float(strike)) < 0.5:
            ce = row.get("ce") or {}
            return {
                "price": float(ce.get("price") or 0),
                "iv":    ce.get("iv"),
                "delta": ce.get("delta"),
            }
    return None


def _accrue_charges(sim: dict, br: dict) -> None:
    """Add a charge breakdown to the running totals on the simulation dict.
       Mutates in place — caller is responsible for persisting."""
    sim["total_brokerage"]     = round(float(sim.get("total_brokerage", 0)) + br.get("brokerage", 0), 2)
    sim["total_stt"]           = round(float(sim.get("total_stt", 0)) + br.get("stt", 0), 2)
    sim["total_exchange"]      = round(float(sim.get("total_exchange", 0)) + br.get("exchange_charge", 0), 2)
    sim["total_gst"]           = round(float(sim.get("total_gst", 0)) + br.get("gst", 0), 2)
    other = (br.get("sebi_charge", 0) or 0) + (br.get("stamp_duty", 0) or 0) + (br.get("dp_charge", 0) or 0)
    sim["total_other_charges"] = round(float(sim.get("total_other_charges", 0)) + other, 2)


def _live_state(sim: dict) -> dict:
    """Compute the full live snapshot for one simulation."""
    spot   = spot_for("NIFTY")
    nb_live = _niftybees_live()

    nb_shares    = int(sim.get("nb_shares", 0) or 0)
    nb_avg_cost  = float(sim.get("nb_avg_cost", 0) or 0)
    nb_mtm_value = round(nb_shares * nb_live, 2)
    nb_unrealised_pnl = round(nb_shares * (nb_live - nb_avg_cost), 2)

    # Active call live MTM
    active_call_live: dict | None = None
    options_unrealised_pnl = 0.0
    ac = sim.get("active_call")
    if ac:
        prem_recv  = float(ac["premium_received"])
        qty        = int(ac["lots"]) * int(ac["lot_size"])
        prem_total = prem_recv * qty
        live = _live_call_price(float(ac["strike"]), ac["expiry"], spot)
        if live:
            current_call = max(float(live["price"]), 0.05)
            iv           = live.get("iv")
            delta        = live.get("delta")
        else:
            # BS fallback
            T = max(opt_eng.days_to_expiry(ac["expiry"]), 0.0)
            iv_dec = 0.16
            current_call = max(opt_eng.black_scholes(spot, float(ac["strike"]), T,
                                                     opt_eng.RISK_FREE_RATE, iv_dec, "CE")["price"], 0.05)
            iv    = round(iv_dec * 100, 1)
            delta = None
        # MTM if we closed right now
        buyback_cost      = current_call * qty
        buyback_charges   = _ch.option_charges(current_call, qty, "BUY")
        net_close_now     = prem_total - (buyback_cost + buyback_charges["total_charges"])
        options_unrealised_pnl = round(net_close_now - sim_already_collected_net(sim, ac), 2)

        dte = max(int(round(opt_eng.days_to_expiry(ac["expiry"]) * 365)), 0)

        active_call_live = {
            "strike":             float(ac["strike"]),
            "expiry":             ac["expiry"],
            "dte":                dte,
            "lots":               int(ac["lots"]),
            "lot_size":           int(ac["lot_size"]),
            "qty":                qty,
            "premium_received":   round(prem_recv, 2),
            "premium_total":      round(prem_total, 2),
            "current_price":      round(current_call, 2),
            "buyback_cost_total": round(buyback_cost, 2),
            "buyback_charges":    buyback_charges,
            "iv":                 iv,
            "delta":              round(float(delta), 3) if delta is not None else None,
            "is_itm":             spot > float(ac["strike"]),
            "intrinsic":          round(max(spot - float(ac["strike"]), 0), 2),
            "entry_date":         ac.get("entry_date"),
            "entry_charges":      ac.get("entry_charges"),
        }

    # Cumulative numbers
    total_charges = round(
        float(sim.get("total_brokerage", 0))
        + float(sim.get("total_stt", 0))
        + float(sim.get("total_exchange", 0))
        + float(sim.get("total_other_charges", 0))
        + float(sim.get("total_gst", 0)),
    2)

    realised_options = float(sim.get("realised_options_pnl", 0) or 0)
    realised_etf     = float(sim.get("realised_etf_pnl", 0) or 0)
    realised_tax     = float(sim.get("realised_tax_paid", 0) or 0)

    realised_pnl_pretax  = round(realised_options + realised_etf, 2)
    realised_pnl_posttax = round(realised_pnl_pretax - realised_tax, 2)
    unrealised_pnl       = round(nb_unrealised_pnl + options_unrealised_pnl, 2)
    total_value          = round(float(sim.get("cash_balance", 0)) + nb_mtm_value, 2)
    starting_capital     = float(sim.get("starting_capital", 0))
    net_pnl_pretax       = round(total_value - starting_capital
                                 + realised_options + realised_etf
                                 - sim_options_collected(sim), 2)  # see helper below
    # Simpler: net P&L = (current portfolio value + realised) - starting
    # Where portfolio value already includes cash + NB MTM. The calls were
    # netted into cash_balance at trade time, so the cash already reflects them.
    net_pnl_pretax = round(total_value - starting_capital, 2)
    net_pnl_posttax = round(net_pnl_pretax - realised_tax, 2)

    # Annualised yield since start
    days_open = max(1, (_dt.datetime.now() - _dt.datetime.fromisoformat(sim["created_at"])).days)
    annualised_pct = round((net_pnl_posttax / starting_capital) * (365 / days_open) * 100, 2) if starting_capital > 0 else 0.0

    return {
        "id":               sim["id"],
        "name":             sim.get("name", ""),
        "created_at":       sim["created_at"],
        "days_open":        days_open,
        "starting_capital": round(starting_capital, 2),
        "slab_rate_pct":    float(sim.get("slab_rate_pct", 30.0)),

        # Live market
        "nifty_spot":       round(spot, 2),
        "nb_live_price":    round(nb_live, 2),

        # NiftyBees leg
        "nb_shares":        nb_shares,
        "nb_avg_cost":      round(nb_avg_cost, 2),
        "nb_invested":      round(nb_shares * nb_avg_cost, 2),
        "nb_mtm_value":     nb_mtm_value,
        "nb_unrealised_pnl": nb_unrealised_pnl,

        # Cash leg
        "cash_balance":     round(float(sim.get("cash_balance", 0)), 2),

        # Options leg
        "active_call":      active_call_live,
        "call_history":     sim.get("call_history", []),
        "total_premium_received": round(float(sim.get("total_premium_received", 0)), 2),
        "total_premium_paid_back": round(float(sim.get("total_premium_paid_back", 0)), 2),

        # Cumulative charges
        "charges_breakdown": {
            "brokerage":     round(float(sim.get("total_brokerage", 0)), 2),
            "stt":           round(float(sim.get("total_stt", 0)), 2),
            "exchange":      round(float(sim.get("total_exchange", 0)), 2),
            "other":         round(float(sim.get("total_other_charges", 0)), 2),
            "gst":           round(float(sim.get("total_gst", 0)), 2),
            "total":         total_charges,
        },

        # P&L summary
        "realised_options_pnl": round(realised_options, 2),
        "realised_etf_pnl":     round(realised_etf, 2),
        "realised_pnl_pretax":  realised_pnl_pretax,
        "realised_tax_paid":    round(realised_tax, 2),
        "realised_pnl_posttax": realised_pnl_posttax,
        "unrealised_pnl":       unrealised_pnl,
        "total_portfolio_value": total_value,
        "net_pnl_pretax":       net_pnl_pretax,
        "net_pnl_posttax":      net_pnl_posttax,
        "net_pnl_pct":          round(net_pnl_posttax / starting_capital * 100, 2) if starting_capital > 0 else 0.0,
        "annualised_pct":       annualised_pct,

        # Ledger
        "nb_history":       sim.get("nb_history", []),
        "notes":            sim.get("notes", ""),
    }


def sim_options_collected(sim: dict) -> float:
    """Helper: total premium NET (received - paid back). Already in cash_balance.
       Used to avoid double-counting when computing portfolio value."""
    return float(sim.get("total_premium_received", 0)) - float(sim.get("total_premium_paid_back", 0))


def sim_already_collected_net(sim: dict, active_call: dict) -> float:
    """For an active call: how much net premium has already been credited to
       cash. The sell adds (premium - charges); buyback later subtracts
       (premium + charges)."""
    return float(active_call.get("net_premium_collected", 0))


# ── Pydantic input models ────────────────────────────────────────────────────

class CreateSimIn(BaseModel):
    name: str = Field(default="My CC Sandbox")
    capital: float                                # ₹ to start with
    slab_rate_pct: float = 30.0                  # tax slab for F&O income
    deploy_pct: float = Field(default=98.0, ge=10, le=100)   # % to deploy into NB

class SellCallIn(BaseModel):
    strike: float
    expiry: str                                   # "YYYY-MM-DD"
    lots: int = 1
    lot_size: int = 75
    premium: float                                # ₹ per unit (the chain LTP / mid)

class CloseCallIn(BaseModel):
    close_price: float                            # current call price you'd buy back at
    kind: str = "closed"                          # "closed" | "expired" | "rolled"

class TradeSharesIn(BaseModel):
    qty: int                                      # >0
    price: float | None = None                    # default to live; explicit to test scenarios

class UpdateSlabIn(BaseModel):
    slab_rate_pct: float


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/list")
def list_sims():
    """Returns list of simulation summaries with live P&L for the hub."""
    sims = _store.list_simulations()
    return [_live_state(s) for s in sims]


@router.get("/{sim_id}")
def get_sim(sim_id: str):
    sim = _store.get_simulation(sim_id)
    if not sim:
        raise HTTPException(404, "Simulation not found")
    return _live_state(sim)


@router.post("")
def create_sim(body: CreateSimIn):
    """Create a new simulation: auto-buys NiftyBees with `deploy_pct` of capital
       at the live price, accounting for buy-side charges. Remaining sits in cash."""
    if body.capital <= 0:
        raise HTTPException(400, "Capital must be positive")

    nb_price  = _niftybees_live()
    if nb_price <= 0:
        raise HTTPException(400, "Could not fetch live NiftyBees price")

    deploy_amt = body.capital * (body.deploy_pct / 100.0)
    # Solve: shares × price + charges(shares × price) ≤ deploy_amt
    # Charges scale linearly with turnover, so we approximate by 1 round.
    rough_shares = int(deploy_amt / nb_price)
    if rough_shares <= 0:
        raise HTTPException(400, "Capital too small to buy any NiftyBees at live price")

    while rough_shares > 0:
        br = _ch.equity_delivery_charges(rough_shares, nb_price, "BUY")
        if br["net_amount"] <= deploy_amt:
            break
        rough_shares -= 1

    if rough_shares <= 0:
        raise HTTPException(400, "Capital insufficient after charges")

    br = _ch.equity_delivery_charges(rough_shares, nb_price, "BUY")
    cash_balance = round(body.capital - br["net_amount"], 2)
    spot         = spot_for("NIFTY")

    sim = _store.create_simulation({
        "name":             body.name,
        "starting_capital": body.capital,
        "slab_rate_pct":    body.slab_rate_pct,
        "nb_shares":        rough_shares,
        "nb_avg_cost":      nb_price,
        "nb_entry_nifty":   spot,
        "cash_balance":     cash_balance,
        "nb_history": [{
            "date":    _dt.datetime.now().isoformat(),
            "side":    "BUY",
            "qty":     rough_shares,
            "price":   nb_price,
            "charges": br,
            "note":    f"Initial deployment ({body.deploy_pct:.0f}% of capital)",
        }],
    })
    _accrue_charges(sim, br)
    sim = _store.update_simulation(sim["id"], {
        "total_brokerage":     sim["total_brokerage"],
        "total_stt":           sim["total_stt"],
        "total_exchange":      sim["total_exchange"],
        "total_other_charges": sim["total_other_charges"],
        "total_gst":           sim["total_gst"],
    })
    return _live_state(sim)


@router.delete("/{sim_id}")
def delete_sim(sim_id: str):
    if not _store.delete_simulation(sim_id):
        raise HTTPException(404, "Simulation not found")
    return {"ok": True}


@router.post("/{sim_id}/slab")
def update_slab(sim_id: str, body: UpdateSlabIn):
    sim = _store.update_simulation(sim_id, {"slab_rate_pct": body.slab_rate_pct})
    if not sim:
        raise HTTPException(404, "Simulation not found")
    return _live_state(sim)


@router.post("/{sim_id}/sell-call")
def sell_call(sim_id: str, body: SellCallIn):
    """Paper-write a Nifty CE. Validates that NB shares cover the lots.
       Adds (premium − sell-side charges) to cash, sets active_call."""
    sim = _store.get_simulation(sim_id)
    if not sim:
        raise HTTPException(404, "Simulation not found")
    if sim.get("active_call"):
        raise HTTPException(400, "Already have an active call. Close or roll it first.")

    qty = body.lots * body.lot_size
    nb_shares_needed = int(qty * NIFTYBEES_RATIO)   # 1 lot × 75 × 100 = 7,500 NB shares cover 1 lot
    if int(sim["nb_shares"]) < nb_shares_needed:
        raise HTTPException(
            400,
            f"Need {nb_shares_needed} NiftyBees shares to cover {body.lots} lot(s); "
            f"you have {sim['nb_shares']}. Buy more or reduce lots."
        )

    br = _ch.option_charges(body.premium, qty, "SELL")
    net_credit = br["net_cash_flow"]    # +ve (premium received minus charges)

    active = {
        "strike":           body.strike,
        "expiry":           body.expiry,
        "lots":             body.lots,
        "lot_size":         body.lot_size,
        "premium_received": body.premium,
        "premium_total":    round(body.premium * qty, 2),
        "entry_date":       _dt.datetime.now().isoformat(),
        "entry_charges":    br,
        "net_premium_collected": net_credit,
    }

    _accrue_charges(sim, br)
    new_cash = round(float(sim.get("cash_balance", 0)) + net_credit, 2)
    new_total_received = round(float(sim.get("total_premium_received", 0)) + active["premium_total"], 2)

    sim = _store.update_simulation(sim_id, {
        "active_call":             active,
        "cash_balance":            new_cash,
        "total_brokerage":         sim["total_brokerage"],
        "total_stt":               sim["total_stt"],
        "total_exchange":          sim["total_exchange"],
        "total_other_charges":     sim["total_other_charges"],
        "total_gst":               sim["total_gst"],
        "total_premium_received":  new_total_received,
    })
    return _live_state(sim)


@router.post("/{sim_id}/close-call")
def close_call(sim_id: str, body: CloseCallIn):
    """Buy back the active call. Realises options P&L, accrues tax."""
    sim = _store.get_simulation(sim_id)
    if not sim:
        raise HTTPException(404, "Simulation not found")
    ac = sim.get("active_call")
    if not ac:
        raise HTTPException(400, "No active call to close.")

    qty       = int(ac["lots"]) * int(ac["lot_size"])
    br_close  = _ch.option_charges(body.close_price, qty, "BUY")
    buyback_cost     = body.close_price * qty
    cash_paid        = buyback_cost + br_close["total_charges"]
    net_pnl_on_call  = round(ac["net_premium_collected"] - cash_paid, 2)

    # Tax (applied on positive P&L only — losses can offset other income but
    # we don't aggregate here)
    tax = _ch.tax_on_fno_income(net_pnl_on_call, sim.get("slab_rate_pct", 30.0))

    cycle = {
        "strike":          ac["strike"],
        "expiry":          ac["expiry"],
        "lots":            ac["lots"],
        "lot_size":        ac["lot_size"],
        "premium_received": ac["premium_received"],
        "entry_date":      ac["entry_date"],
        "entry_charges":   ac["entry_charges"],
        "exit_date":       _dt.datetime.now().isoformat(),
        "exit_price":      body.close_price,
        "exit_charges":    br_close,
        "kind":            body.kind,
        "pnl_pretax":      net_pnl_on_call,
        "tax":             tax,
        "pnl_posttax":     round(net_pnl_on_call - tax["total"], 2),
    }

    _accrue_charges(sim, br_close)
    new_cash = round(float(sim.get("cash_balance", 0)) - cash_paid, 2)
    new_paid_back = round(float(sim.get("total_premium_paid_back", 0)) + buyback_cost, 2)
    new_realised  = round(float(sim.get("realised_options_pnl", 0)) + net_pnl_on_call, 2)
    new_tax       = round(float(sim.get("realised_tax_paid", 0)) + tax["total"], 2)

    history = list(sim.get("call_history", []))
    history.append(cycle)

    sim = _store.update_simulation(sim_id, {
        "active_call":             None,
        "cash_balance":            new_cash,
        "total_brokerage":         sim["total_brokerage"],
        "total_stt":               sim["total_stt"],
        "total_exchange":          sim["total_exchange"],
        "total_other_charges":     sim["total_other_charges"],
        "total_gst":               sim["total_gst"],
        "total_premium_paid_back": new_paid_back,
        "realised_options_pnl":    new_realised,
        "realised_tax_paid":       new_tax,
        "call_history":            history,
    })
    return _live_state(sim)


@router.post("/{sim_id}/sell-shares")
def sell_shares(sim_id: str, body: TradeSharesIn):
    """Realise cash by selling NB shares (e.g. to free margin or pull income)."""
    sim = _store.get_simulation(sim_id)
    if not sim:
        raise HTTPException(404, "Simulation not found")
    if body.qty <= 0:
        raise HTTPException(400, "Quantity must be positive")
    if int(sim["nb_shares"]) < body.qty:
        raise HTTPException(400, f"Only {sim['nb_shares']} shares available")

    # If covered call active, ensure we still have enough NB shares to cover it
    ac = sim.get("active_call")
    if ac:
        qty_calls = int(ac["lots"]) * int(ac["lot_size"])
        nb_needed = int(qty_calls * NIFTYBEES_RATIO)
        if int(sim["nb_shares"]) - body.qty < nb_needed:
            raise HTTPException(400, (
                f"Active call needs {nb_needed} NB shares to stay covered. "
                f"Close the call first or reduce sell qty."
            ))

    price = body.price if (body.price and body.price > 0) else _niftybees_live()
    br    = _ch.equity_delivery_charges(body.qty, price, "SELL")
    proceeds = br["net_amount"]

    avg_cost  = float(sim.get("nb_avg_cost", 0))
    realised  = round((price - avg_cost) * body.qty - br["total_charges"], 2)

    # Holding period for tax
    first_buy = next((h for h in sim.get("nb_history", []) if h["side"] == "BUY"), None)
    holding_days = 0
    if first_buy:
        try:
            holding_days = (_dt.datetime.now() - _dt.datetime.fromisoformat(first_buy["date"])).days
        except Exception:
            holding_days = 0
    tax = _ch.tax_on_etf_gain(realised, holding_days)

    new_shares = int(sim["nb_shares"]) - body.qty
    new_cash   = round(float(sim.get("cash_balance", 0)) + proceeds, 2)

    nb_hist = list(sim.get("nb_history", []))
    nb_hist.append({
        "date":     _dt.datetime.now().isoformat(),
        "side":     "SELL",
        "qty":      body.qty,
        "price":    price,
        "charges":  br,
        "realised": realised,
        "tax":      tax,
        "note":     "Shares sold to realise cash",
    })

    _accrue_charges(sim, br)

    new_realised_etf = round(float(sim.get("realised_etf_pnl", 0)) + realised, 2)
    new_tax = round(float(sim.get("realised_tax_paid", 0)) + tax["tax"], 2)

    sim = _store.update_simulation(sim_id, {
        "nb_shares":           new_shares,
        "cash_balance":        new_cash,
        "total_brokerage":     sim["total_brokerage"],
        "total_stt":           sim["total_stt"],
        "total_exchange":      sim["total_exchange"],
        "total_other_charges": sim["total_other_charges"],
        "total_gst":           sim["total_gst"],
        "realised_etf_pnl":    new_realised_etf,
        "realised_tax_paid":   new_tax,
        "nb_history":          nb_hist,
    })
    return _live_state(sim)


@router.post("/{sim_id}/buy-shares")
def buy_shares(sim_id: str, body: TradeSharesIn):
    """Deploy cash by buying more NB. Updates avg cost via running weighted avg."""
    sim = _store.get_simulation(sim_id)
    if not sim:
        raise HTTPException(404, "Simulation not found")
    if body.qty <= 0:
        raise HTTPException(400, "Quantity must be positive")

    price = body.price if (body.price and body.price > 0) else _niftybees_live()
    br    = _ch.equity_delivery_charges(body.qty, price, "BUY")
    cost  = br["net_amount"]
    if cost > float(sim.get("cash_balance", 0)) + 1.0:
        raise HTTPException(400, f"Need ₹{cost:,.2f}; cash balance is ₹{sim.get('cash_balance', 0):,.2f}")

    old_qty   = int(sim["nb_shares"])
    old_avg   = float(sim.get("nb_avg_cost", 0))
    new_qty   = old_qty + body.qty
    new_avg   = round(((old_qty * old_avg) + (body.qty * price)) / new_qty, 4) if new_qty > 0 else 0.0
    new_cash  = round(float(sim.get("cash_balance", 0)) - cost, 2)

    nb_hist = list(sim.get("nb_history", []))
    nb_hist.append({
        "date":    _dt.datetime.now().isoformat(),
        "side":    "BUY",
        "qty":     body.qty,
        "price":   price,
        "charges": br,
        "note":    "Cash deployed into NB",
    })

    _accrue_charges(sim, br)

    sim = _store.update_simulation(sim_id, {
        "nb_shares":           new_qty,
        "nb_avg_cost":         new_avg,
        "cash_balance":        new_cash,
        "total_brokerage":     sim["total_brokerage"],
        "total_stt":           sim["total_stt"],
        "total_exchange":      sim["total_exchange"],
        "total_other_charges": sim["total_other_charges"],
        "total_gst":           sim["total_gst"],
        "nb_history":          nb_hist,
    })
    return _live_state(sim)
