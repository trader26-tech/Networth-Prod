"""
Indian F&O brokerage and tax computation (Zerodha rates, valid 2024-2026).
"""


def compute_charges(legs: list[dict], include_exit: bool = True) -> dict:
    """
    Compute total round-trip charges for an options trade.

    Each leg dict: {action: 'BUY'|'SELL', qty (lots), lot_size, entry_premium}

    Rates (NSE F&O):
      Brokerage:    ₹20 per order (capped at 0.03% of turnover)
      STT:          0.1% of premium on SELL side only
      Exchange:     0.03503% of premium
      SEBI:         0.0001% of premium
      Stamp duty:   0.003% of premium on BUY side only
      GST:          18% of (brokerage + exchange + SEBI)
    """
    BROKER, STT, EXCH, SEBI, STAMP, GST = 20.0, 0.001, 0.0003503, 0.000001, 0.00003, 0.18

    brokerage = stt = exchange = sebi = stamp = 0.0

    def _add(action: str, notional: float, is_entry: bool):
        nonlocal brokerage, stt, exchange, sebi, stamp
        brokerage += BROKER
        side = action if is_entry else ("SELL" if action == "BUY" else "BUY")
        if side == "SELL":
            stt += notional * STT
        else:
            stamp += notional * STAMP
        exchange += notional * EXCH
        sebi     += notional * SEBI

    for leg in legs:
        notional = float(leg["entry_premium"]) * int(leg["qty"]) * int(leg["lot_size"])
        _add(leg["action"], notional, is_entry=True)
        if include_exit:
            _add(leg["action"], notional, is_entry=False)

    gst   = (brokerage + exchange + sebi) * GST
    total = brokerage + stt + exchange + sebi + stamp + gst

    return {
        "brokerage": round(brokerage, 2),
        "stt":       round(stt, 2),
        "exchange":  round(exchange, 2),
        "sebi":      round(sebi, 2),
        "stamp":     round(stamp, 2),
        "gst":       round(gst, 2),
        "total":     round(total, 2),
    }
