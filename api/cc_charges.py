"""
Indian brokerage + statutory charge calculations for the covered-call simulator.

Numbers reflect Zerodha's published rate card as of 2025-2026 and the prevailing
NSE/SEBI/GST rates. They're kept in one place here so the simulator stays a
single source of truth for "what would this trade actually cost me?".

Equity delivery (NiftyBees ETF):
  - Brokerage              : ₹0 (Zerodha free for delivery)
  - STT                    : 0.001% on both buy and sell (0.1 bps each side)
  - Exchange transaction   : NSE 0.00322% on turnover
  - SEBI charges           : ₹10 per crore = 0.0001%
  - Stamp duty             : 0.015% on buy only (0.001% min)
  - GST                    : 18% on (brokerage + exchange + SEBI)
  - DP charges             : ₹13.5 + 18% GST = ₹15.93 per scrip per day on sell

Options F&O (Nifty index calls/puts):
  - Brokerage              : ₹20 flat per executed order, or 0.03% (whichever lower)
  - STT                    : 0.1% on premium (sell side only — buy is STT-free)
                              0.125% on intrinsic (only if exercised at expiry)
  - Exchange transaction   : NSE 0.0503% on premium
  - SEBI charges           : ₹10 per crore = 0.0001%
  - Stamp duty             : 0.003% on buy only (premium × qty)
  - GST                    : 18% on (brokerage + exchange + SEBI)

Tax on F&O P&L:
  - Treated as non-speculative business income
  - Slab rate (default 30% — user-configurable per simulation)

Tax on ETF gains:
  - LTCG (>1 yr held): 12.5% above ₹1.25 lakh exempt
  - STCG (≤1 yr held): 20%

All helpers return a dict so the UI can show the breakdown line-by-line.
"""
from __future__ import annotations


# ── Equity delivery (NiftyBees) ───────────────────────────────────────────────

def equity_delivery_charges(
    qty: int,
    price: float,
    side: str,              # "BUY" or "SELL"
) -> dict:
    """Returns full breakdown for a NiftyBees ETF transaction.

    Total cost (BUY)  = price*qty + charges
    Net proceeds (SELL) = price*qty − charges
    """
    side = side.upper()
    turnover = price * qty

    brokerage           = 0.0                                  # Zerodha free for delivery
    stt                 = turnover * 0.00001                   # 0.001% both sides
    exchange_charge     = turnover * 0.0000322                 # NSE 0.00322%
    sebi_charge         = turnover * 0.000001                  # ₹10/crore
    stamp_duty          = turnover * 0.00015 if side == "BUY" else 0.0
    dp_charge           = (13.5 + 13.5 * 0.18) if side == "SELL" else 0.0   # only on sell
    gst                 = (brokerage + exchange_charge + sebi_charge) * 0.18

    total_charges = round(brokerage + stt + exchange_charge + sebi_charge
                          + stamp_duty + dp_charge + gst, 2)

    return {
        "side":             side,
        "qty":              qty,
        "price":            round(price, 2),
        "turnover":         round(turnover, 2),
        "brokerage":        round(brokerage, 2),
        "stt":              round(stt, 2),
        "exchange_charge":  round(exchange_charge, 2),
        "sebi_charge":      round(sebi_charge, 2),
        "stamp_duty":       round(stamp_duty, 2),
        "dp_charge":        round(dp_charge, 2),
        "gst":              round(gst, 2),
        "total_charges":    total_charges,
        "net_amount":       round(turnover + total_charges if side == "BUY"
                                   else turnover - total_charges, 2),
    }


# ── Options F&O (Nifty CE/PE) ─────────────────────────────────────────────────

def option_charges(
    premium: float,
    qty: int,                 # = lots × lot_size (e.g. 1 lot Nifty = 75)
    side: str,                # "SELL" (write) or "BUY" (buy back)
    exercised: bool = False,
    intrinsic: float = 0.0,
) -> dict:
    """Returns full breakdown for one option leg.

    For covered call cycles:
      - Sell call : side="SELL", premium=entry_premium, qty=lots*lot_size
      - Close/buy back : side="BUY", premium=current_call_price
      - Assignment at expiry : side="SELL", exercised=True, intrinsic=spot-K
    """
    side = side.upper()
    turnover = premium * qty

    # Brokerage: ₹20 flat or 0.03%, whichever lower (Zerodha)
    brokerage = min(20.0, 0.0003 * turnover) if turnover > 0 else 0.0

    # STT: only on the SELL side of premium (when writing OR squaring off short)
    # Buy-back of a short call is technically a BUY → STT-free on premium.
    if side == "SELL" and not exercised:
        stt = turnover * 0.001              # 0.1% on premium
    elif exercised:
        stt = (intrinsic * qty) * 0.00125   # 0.125% on intrinsic at exercise
    else:
        stt = 0.0

    exchange_charge = turnover * 0.000503   # NSE 0.0503%
    sebi_charge     = turnover * 0.000001   # ₹10/crore
    stamp_duty      = turnover * 0.00003 if side == "BUY" else 0.0
    gst             = (brokerage + exchange_charge + sebi_charge) * 0.18

    total_charges = round(brokerage + stt + exchange_charge + sebi_charge
                          + stamp_duty + gst, 2)

    return {
        "side":             side,
        "qty":              qty,
        "premium":          round(premium, 2),
        "turnover":         round(turnover, 2),
        "brokerage":        round(brokerage, 2),
        "stt":              round(stt, 2),
        "exchange_charge":  round(exchange_charge, 2),
        "sebi_charge":      round(sebi_charge, 2),
        "stamp_duty":       round(stamp_duty, 2),
        "gst":              round(gst, 2),
        "total_charges":    total_charges,
        # On a SELL we collect (premium − charges), on a BUY we pay (premium + charges).
        "net_cash_flow":    round(turnover - total_charges if side == "SELL"
                                  else -(turnover + total_charges), 2),
    }


# ── Income tax (post-trade, on realised P&L) ─────────────────────────────────

def tax_on_fno_income(realised_pnl: float, slab_rate_pct: float = 30.0) -> dict:
    """F&O P&L is non-speculative business income → slab rate.
       Cess: 4% on the tax (Health & Education Cess).
       Negative P&L returns zero tax (loss can offset other business income but
       that's outside the simulator's scope)."""
    if realised_pnl <= 0:
        return {"taxable": 0.0, "tax": 0.0, "cess": 0.0, "total": 0.0,
                "slab_rate_pct": slab_rate_pct}
    base_tax = realised_pnl * slab_rate_pct / 100.0
    cess     = base_tax * 0.04
    return {
        "taxable":       round(realised_pnl, 2),
        "tax":           round(base_tax, 2),
        "cess":          round(cess, 2),
        "total":         round(base_tax + cess, 2),
        "slab_rate_pct": slab_rate_pct,
    }


def tax_on_etf_gain(realised_gain: float, holding_days: int) -> dict:
    """ETF capital gains.
       LTCG (>365d) : 12.5% on amount above ₹1.25L exemption (per FY).
       STCG (≤365d) : 20% flat.
       This is a per-trade computation and ignores cross-trade aggregation —
       the UI can later sum into an annual rollup if needed.
    """
    if realised_gain <= 0:
        return {"taxable": 0.0, "tax": 0.0, "kind": "none",
                "exemption_used": 0.0, "rate_pct": 0.0}
    if holding_days > 365:
        exempt = min(125000.0, realised_gain)
        taxable = max(0.0, realised_gain - exempt)
        tax = taxable * 0.125
        return {"taxable": round(taxable, 2), "tax": round(tax, 2),
                "kind": "LTCG", "exemption_used": round(exempt, 2),
                "rate_pct": 12.5}
    tax = realised_gain * 0.20
    return {"taxable": round(realised_gain, 2), "tax": round(tax, 2),
            "kind": "STCG", "exemption_used": 0.0, "rate_pct": 20.0}
