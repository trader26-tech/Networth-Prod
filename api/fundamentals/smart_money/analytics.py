"""
Aggregate metrics over the smart-money trade history.

Hottest-stocks view computes everything we discussed:

  Per-stock:
    • Buy value / Sell value / Net inflow
    • Buy:sell ratio (by value)
    • Unique buyer count / unique seller count / net unique participants
    • Concentration (top buyer share of total buy value)
    • VWAP-buy / VWAP-sell / Sell-Buy spread
    • Deal count, first/latest deal dates
    • Top-3 buyers, top-3 sellers (with name + value)
    • Daily net-flow series (for sparkline)
    • Category split (FII/DII/MF/Promoter/Other) by value

  Overall (dashboard header):
    • Total trades, distinct stocks, distinct parties, date range
    • Category split across the whole dataset
    • Total buy / sell / net flow ₹
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Iterable


def _to_date(s: str) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except Exception:
        return None


def _f(val) -> float | None:
    """Best-effort float parse; '' → None."""
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _within(rows: Iterable[dict], days: int | None) -> list[dict]:
    """Filter to last `days` days from the dataset's most recent date.
    `days=None` returns everything."""
    rows = list(rows)
    if not rows or not days:
        return rows
    dates = [_to_date(r.get("date") or "") for r in rows]
    dates = [d for d in dates if d]
    if not dates:
        return rows
    cutoff = max(dates) - timedelta(days=days)
    return [r for r in rows if (_to_date(r.get("date") or "") or date.min) >= cutoff]


def overall_summary(rows: list[dict]) -> dict:
    if not rows:
        return {"available": False, "total_trades": 0}
    parties  = set()
    stocks   = set()
    dates    = []
    cat_buy  = defaultdict(float)
    cat_sell = defaultdict(float)
    total_buy = total_sell = 0.0
    for r in rows:
        if r.get("party"):    parties.add(r["party"])
        if r.get("stock"):    stocks.add(r["stock"])
        d = _to_date(r.get("date") or "")
        if d: dates.append(d)
        v = _f(r.get("value")) or 0.0
        cat = r.get("category") or "Other"
        if r.get("txn_type") == "buy":
            total_buy += v; cat_buy[cat] += v
        elif r.get("txn_type") == "sell":
            total_sell += v; cat_sell[cat] += v

    return {
        "available":         True,
        "total_trades":      len(rows),
        "distinct_parties":  len(parties),
        "distinct_stocks":   len(stocks),
        "from_date":         min(dates).isoformat() if dates else None,
        "to_date":           max(dates).isoformat() if dates else None,
        "total_buy_value":   round(total_buy, 0),
        "total_sell_value":  round(total_sell, 0),
        "net_flow":          round(total_buy - total_sell, 0),
        "category_buy":      {k: round(v, 0) for k, v in cat_buy.items()},
        "category_sell":     {k: round(v, 0) for k, v in cat_sell.items()},
    }


def _vwap(rows: list[dict]) -> float | None:
    num_total = 0.0
    qty_total = 0.0
    for r in rows:
        p = _f(r.get("price")); q = _f(r.get("quantity"))
        if p is None or q is None:
            continue
        num_total += p * q
        qty_total += q
    return round(num_total / qty_total, 2) if qty_total else None


def _stock_aggregate(stock: str, rows: list[dict]) -> dict:
    buy_rows  = [r for r in rows if r.get("txn_type") == "buy"]
    sell_rows = [r for r in rows if r.get("txn_type") == "sell"]
    buy_val   = sum((_f(r.get("value")) or 0.0) for r in buy_rows)
    sell_val  = sum((_f(r.get("value")) or 0.0) for r in sell_rows)
    net_flow  = buy_val - sell_val
    buyers    = {r.get("party") for r in buy_rows  if r.get("party")}
    sellers   = {r.get("party") for r in sell_rows if r.get("party")}

    # Concentration — top buyer's share of all buy ₹
    buyer_totals: dict[str, float] = defaultdict(float)
    for r in buy_rows:
        if r.get("party"):
            buyer_totals[r["party"]] += _f(r.get("value")) or 0.0
    seller_totals: dict[str, float] = defaultdict(float)
    for r in sell_rows:
        if r.get("party"):
            seller_totals[r["party"]] += _f(r.get("value")) or 0.0

    top_buyers = sorted(buyer_totals.items(), key=lambda x: -x[1])[:5]
    top_sellers = sorted(seller_totals.items(), key=lambda x: -x[1])[:5]

    # Daily net flow — for the sparkline
    by_day: dict[str, float] = defaultdict(float)
    for r in rows:
        d = r.get("date") or ""
        if not d:
            continue
        v = _f(r.get("value")) or 0.0
        if r.get("txn_type") == "buy":  by_day[d] += v
        elif r.get("txn_type") == "sell": by_day[d] -= v
    daily_flow = sorted(by_day.items())

    vwap_buy  = _vwap(buy_rows)
    vwap_sell = _vwap(sell_rows)

    dates = [_to_date(r.get("date") or "") for r in rows]
    dates = sorted(d for d in dates if d)

    concentration = (top_buyers[0][1] / buy_val) if buy_val and top_buyers else None

    return {
        "stock":              stock,
        "buy_value":          round(buy_val, 0),
        "sell_value":         round(sell_val, 0),
        "net_flow":           round(net_flow, 0),
        "buy_sell_ratio":     round(buy_val / sell_val, 2) if sell_val > 0 else (float('inf') if buy_val > 0 else 0),
        "unique_buyers":      len(buyers),
        "unique_sellers":     len(sellers),
        "net_breadth":        len(buyers) - len(sellers),
        "concentration":      round(concentration, 3) if concentration is not None else None,
        "vwap_buy":           vwap_buy,
        "vwap_sell":          vwap_sell,
        "spread":             round((vwap_sell - vwap_buy), 2) if (vwap_buy and vwap_sell) else None,
        "deal_count":         len(rows),
        "first_date":         dates[0].isoformat() if dates else None,
        "last_date":          dates[-1].isoformat() if dates else None,
        "top_buyers":         [{"party": p, "value": round(v, 0)} for p, v in top_buyers],
        "top_sellers":        [{"party": p, "value": round(v, 0)} for p, v in top_sellers],
        "daily_flow":         [{"date": d, "net": round(v, 0)} for d, v in daily_flow],
    }


def hottest_stocks(rows: list[dict], days: int | None = 90,
                   limit: int = 30, sort_by: str = "net_flow") -> list[dict]:
    """Top stocks ranked by net inflow / breadth / total activity.

    sort_by ∈ {'net_flow', 'buy_value', 'net_breadth', 'deal_count'}
    """
    rows = _within(rows, days)
    by_stock: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        s = (r.get("stock") or "").strip()
        if s:
            by_stock[s].append(r)

    agg = [_stock_aggregate(s, rs) for s, rs in by_stock.items()]

    if sort_by == "net_flow":
        agg.sort(key=lambda x: -(x["net_flow"] or 0))
    elif sort_by == "buy_value":
        agg.sort(key=lambda x: -(x["buy_value"] or 0))
    elif sort_by == "net_breadth":
        agg.sort(key=lambda x: -(x["net_breadth"] or 0))
    elif sort_by == "deal_count":
        agg.sort(key=lambda x: -(x["deal_count"] or 0))
    elif sort_by == "net_flow_neg":   # biggest distribution / sells
        agg.sort(key=lambda x: (x["net_flow"] or 0))
    return agg[:limit]


def stock_detail(stock: str, rows: list[dict], days: int | None = None) -> dict:
    rows = _within(rows, days)
    stock_rows = [r for r in rows if (r.get("stock") or "").strip().upper() == stock.upper()]
    if not stock_rows:
        return {"stock": stock, "found": False}
    agg = _stock_aggregate(stock, stock_rows)
    agg["found"] = True
    agg["all_trades"] = sorted(stock_rows, key=lambda r: r.get("date") or "", reverse=True)
    return agg


def category_breakdown(rows: list[dict], days: int | None = 90) -> dict:
    rows = _within(rows, days)
    buy: dict[str, float] = defaultdict(float)
    sell: dict[str, float] = defaultdict(float)
    for r in rows:
        c = r.get("category") or "Other"
        v = _f(r.get("value")) or 0.0
        if r.get("txn_type") == "buy":   buy[c]  += v
        elif r.get("txn_type") == "sell": sell[c] += v
    cats = sorted(set(buy) | set(sell))
    return {
        "categories": [
            {"category": c,
             "buy":     round(buy.get(c, 0), 0),
             "sell":    round(sell.get(c, 0), 0),
             "net":     round(buy.get(c, 0) - sell.get(c, 0), 0)}
            for c in cats
        ]
    }


def top_parties(rows: list[dict], days: int | None = 90, limit: int = 20) -> list[dict]:
    """Parties ranked by total activity (capital deployed + withdrawn). Lays
    the groundwork for the full leaderboard after we have more historical
    data — for now it's just 'who's moving the most ₹'."""
    rows = _within(rows, days)
    stats: dict[str, dict] = defaultdict(lambda: {
        "party": "", "buy_value": 0.0, "sell_value": 0.0,
        "buys": 0, "sells": 0, "stocks": set(),
    })
    for r in rows:
        p = r.get("party")
        if not p:
            continue
        s = stats[p]; s["party"] = p
        v = _f(r.get("value")) or 0.0
        if r.get("txn_type") == "buy":
            s["buy_value"] += v;  s["buys"] += 1
        elif r.get("txn_type") == "sell":
            s["sell_value"] += v; s["sells"] += 1
        if r.get("stock"):
            s["stocks"].add(r["stock"])

    out = []
    for s in stats.values():
        out.append({
            "party":         s["party"],
            "buy_value":     round(s["buy_value"], 0),
            "sell_value":    round(s["sell_value"], 0),
            "net_flow":      round(s["buy_value"] - s["sell_value"], 0),
            "total_activity":round(s["buy_value"] + s["sell_value"], 0),
            "buys":          s["buys"],
            "sells":         s["sells"],
            "distinct_stocks": len(s["stocks"]),
        })
    out.sort(key=lambda x: -x["total_activity"])
    return out[:limit]
