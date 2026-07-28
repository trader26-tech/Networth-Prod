"""
Scanner registry and shared utilities.

To add a new strategy scanner:
  1. Create api/scanners/<name>.py with scan_<name>() and scan_<name>_all()
  2. Register the router in api/routes/scanners.py
  3. Add the frontend card in scanner.html
"""
import datetime


def is_trading_day() -> bool:
    return datetime.date.today().weekday() < 5  # Mon–Fri


def collect_option(o: dict, row: dict) -> dict:
    """Normalise a chain row's option side into a flat scanner-friendly dict."""
    return {
        "strike":        float(row["strike"]),
        "price":         float(o["price"]),
        "symbol":        o["symbol"],
        "oi":            int(o["oi"]),
        "volume":        int(o["volume"]),
        "bid":           float(o.get("bid", 0) or 0),
        "ask":           float(o.get("ask", 0) or 0),
        "bid_qty_total": int(o.get("bid_qty_total", 0) or 0),
        "ask_qty_total": int(o.get("ask_qty_total", 0) or 0),
        "iv":            o.get("iv"),
    }


def is_tradeable(o: dict, strike: float, spot: float, *,
                 min_oi: int, min_volume: int, max_spread_pct: float,
                 max_atm_pct: float, check_volume: bool) -> bool:
    """Universal tradeability gate used by every scanner."""
    bid = o.get("bid", 0) or 0
    ask = o.get("ask", 0) or 0
    if bid <= 0 or ask <= 0:
        return False
    if o["oi"] < min_oi:
        return False
    if check_volume and o["volume"] < min_volume:
        return False
    mid = (bid + ask) / 2
    if mid > 0 and (ask - bid) / mid * 100 > max_spread_pct:
        return False
    if abs(strike - spot) / spot * 100 > max_atm_pct:
        return False
    return True


def sort_results(results: list[dict], sort_by: str,
                 key_map: dict | None = None) -> list[dict]:
    """Sort results by a comma-separated sort_by string using key_map."""
    base_map = {
        "pop":       lambda x: -x.get("pop", 0),
        "ev":        lambda x: -x.get("expected_value", 0),
        "profit":    lambda x: -x["max_profit"],
        "rr":        lambda x: -x["rr_ratio"],
        "liquidity": lambda x: -x["liquidity_score"],
        "lots":      lambda x: -x["lots_available"],
        "capital":   lambda x:  x["capital"],
        "loss":      lambda x:  x["max_loss"],
    }
    if key_map:
        base_map.update(key_map)

    keys_list = [s.strip() for s in sort_by.split(",") if s.strip()] or ["profit"]

    def _sort_key(x):
        parts = []
        for k in keys_list:
            fn = base_map.get(k)
            parts.append(fn(x) if fn else -x.get("max_profit", 0))
        return tuple(parts)

    results.sort(key=_sort_key)
    return results


def paginate(results: list, page: int, per_page: int) -> tuple[list, int]:
    """Return (page_slice, total_pages)."""
    total_pages = max(1, (len(results) + per_page - 1) // per_page)
    return results[(page - 1) * per_page : page * per_page], total_pages
