"""
Broken Wing Butterfly scanner.

Call BWB:  +1 K1 CE, -2 K2 CE, +1 K3 CE  (a = K2-K1, b = K3-K2, a ≠ b)
Put  BWB:  +1 K1 PE, -2 K2 PE, +1 K3 PE
"""
from api.core.math_utils  import bwb_pnl_at_expiry, bwb_pop_and_ev
from api.core.charges     import compute_charges
from api.core.liquidity   import liquidity_score
from api.core.chain       import spot_for, build_real_chain
from api.scanners         import collect_option, is_tradeable, is_trading_day, sort_results, paginate
from api                  import options_engine as opt_eng
from api                  import state

import datetime as _dt


def _get_chain(underlying, expiry, spot):
    if state.get_kite():
        try:
            return build_real_chain(underlying, expiry, spot)
        except Exception:
            pass
    return opt_eng.build_option_chain(underlying, expiry, spot, None)


def scan_bwb(
    underlying: str = "NIFTY",
    expiry: str = "",
    max_loss: int = 0,
    min_profit: int = 1000,
    min_oi: int = 10000,
    min_volume: int = 100,
    max_spread_pct: float = 5.0,
    max_atm_pct: float = 5.0,
    min_liquidity: int = 0,
    min_lots: int = 5,
    min_pop: float = 0.0,
    min_ev: int = -999999,
    sort_by: str = "profit",
    max_strikes: int = 8,
    page: int = 1,
    per_page: int = 30,
) -> dict:
    spot = spot_for(underlying)
    if not expiry:
        expiries = opt_eng.get_nfo_expiries(underlying)
        expiry   = expiries[0] if expiries else ""

    chain_data = _get_chain(underlying, expiry, spot)
    chain      = chain_data["chain"]
    lot_size   = chain_data["lot_size"]
    atm_strike = chain_data["atm_strike"]

    trading_day = is_trading_day()
    T           = opt_eng.days_to_expiry(expiry)

    def _tradeable(o, strike):
        return is_tradeable(o, strike, spot,
                            min_oi=min_oi, min_volume=min_volume,
                            max_spread_pct=max_spread_pct, max_atm_pct=max_atm_pct,
                            check_volume=trading_day)

    results       = []
    total_scanned = 0

    for opt_type in ("CE", "PE"):
        side = opt_type.lower()
        opts = []
        for row in chain:
            o = row[side]
            if o["price"] > 0.10 and _tradeable(o, row["strike"]):
                opts.append(collect_option(o, row))

        n = len(opts)
        for i in range(n):
            for j in range(i + 1, n):
                for k in range(j + 1, n):
                    if (k - i) > max_strikes:
                        continue
                    total_scanned += 1

                    K1, K2, K3 = opts[i]["strike"], opts[j]["strike"], opts[k]["strike"]
                    P1 = opts[i]["ask"]   # BUY K1 → pay ask
                    P2 = opts[j]["bid"]   # SELL K2 → receive bid  (×2)
                    P3 = opts[k]["ask"]   # BUY K3 → pay ask
                    a, b = K2 - K1, K3 - K2
                    if a == 0 or b == 0 or a == b:
                        continue
                    if P1 <= 0 or P2 <= 0 or P3 <= 0:
                        continue

                    nc = -P1 + 2 * P2 - P3

                    if opt_type == "CE":
                        pnl_below = nc
                        pnl_peak  = a + nc
                        pnl_above = (a - b) + nc
                    else:
                        pnl_below = (b - a) + nc
                        pnl_peak  = b + nc
                        pnl_above = nc

                    worst_pts = min(pnl_below, pnl_peak, pnl_above)
                    best_pts  = max(pnl_below, pnl_peak, pnl_above)

                    max_loss_rs   = max(0.0, -worst_pts) * lot_size
                    max_profit_rs = best_pts * lot_size
                    net_credit_rs = nc * lot_size

                    if max_loss_rs > max_loss:
                        continue
                    if max_profit_rs < min_profit:
                        continue

                    capital  = max(0.0, -net_credit_rs) + max_loss_rs
                    leg_data = [opts[i], opts[j], opts[k]]
                    liq      = liquidity_score(leg_data)

                    if liq["score"] < min_liquidity:
                        continue

                    lots_K1 = opts[i]["ask_qty_total"] // lot_size
                    lots_K2 = opts[j]["bid_qty_total"] // (2 * lot_size)
                    lots_K3 = opts[k]["ask_qty_total"] // lot_size
                    lots_available = int(min(lots_K1, lots_K2, lots_K3))

                    if lots_available < min_lots:
                        continue

                    iv_pop = (opts[j]["iv"] or opts[i]["iv"] or opts[k]["iv"] or 20.0) / 100
                    pop, ev_rs = bwb_pop_and_ev(
                        opt_type, K1, K2, K3, P1, P2, P3,
                        spot, T, iv_pop, lot_size,
                    )

                    if pop < min_pop:
                        continue
                    if ev_rs < min_ev:
                        continue

                    charges = compute_charges(
                        legs=[
                            {"action": "BUY",  "qty": 1, "lot_size": lot_size, "entry_premium": P1},
                            {"action": "SELL", "qty": 2, "lot_size": lot_size, "entry_premium": P2},
                            {"action": "BUY",  "qty": 1, "lot_size": lot_size, "entry_premium": P3},
                        ],
                        include_exit=True,
                    )

                    results.append({
                        "type": opt_type,
                        "K1": int(K1), "K2": int(K2), "K3": int(K3),
                        "P1": round(P1, 2), "P2": round(P2, 2), "P3": round(P3, 2),
                        "P1_ltp": round(opts[i]["price"], 2),
                        "P2_ltp": round(opts[j]["price"], 2),
                        "P3_ltp": round(opts[k]["price"], 2),
                        "sym1": opts[i]["symbol"], "sym2": opts[j]["symbol"], "sym3": opts[k]["symbol"],
                        "iv1": opts[i]["iv"], "iv2": opts[j]["iv"], "iv3": opts[k]["iv"],
                        "leg_liq": [
                            {"oi": opts[i]["oi"], "vol": opts[i]["volume"],
                             "bid": opts[i]["bid"], "ask": opts[i]["ask"],
                             "bid_qty_total": opts[i]["bid_qty_total"],
                             "ask_qty_total": opts[i]["ask_qty_total"],
                             "side": "BUY", "available_qty": opts[i]["ask_qty_total"],
                             "lots": int(lots_K1)},
                            {"oi": opts[j]["oi"], "vol": opts[j]["volume"],
                             "bid": opts[j]["bid"], "ask": opts[j]["ask"],
                             "bid_qty_total": opts[j]["bid_qty_total"],
                             "ask_qty_total": opts[j]["ask_qty_total"],
                             "side": "SELL", "available_qty": opts[j]["bid_qty_total"],
                             "lots": int(lots_K2)},
                            {"oi": opts[k]["oi"], "vol": opts[k]["volume"],
                             "bid": opts[k]["bid"], "ask": opts[k]["ask"],
                             "bid_qty_total": opts[k]["bid_qty_total"],
                             "ask_qty_total": opts[k]["ask_qty_total"],
                             "side": "BUY", "available_qty": opts[k]["ask_qty_total"],
                             "lots": int(lots_K3)},
                        ],
                        "lots_available":   lots_available,
                        "net_credit":       round(net_credit_rs, 2),
                        "max_profit":       round(max_profit_rs, 2),
                        "max_loss":         round(max_loss_rs, 2),
                        "capital":          round(capital, 2),
                        "lot_size":         lot_size,
                        "wing_skew":        "right" if b > a else "left",
                        "left_wing":        int(a),
                        "right_wing":       int(b),
                        "min_oi":           liq["min_oi"],
                        "min_volume":       liq["min_volume"],
                        "max_spread_pct":   liq["max_spread_pct"],
                        "liquidity_score":  liq["score"],
                        "liquidity_tier":   liq["tier"],
                        "fillable":         lots_available >= 1,
                        "rr_ratio":         round(max_profit_rs / max_loss_rs, 2) if max_loss_rs > 0 else 9999.0,
                        "pnl_below":        round(pnl_below * lot_size, 2),
                        "pnl_peak":         round(pnl_peak  * lot_size, 2),
                        "pnl_above":        round(pnl_above * lot_size, 2),
                        "atm_distance":     int(abs(K2 - atm_strike)),
                        "pop":              round(pop, 4),
                        "expected_value":   round(ev_rs, 2),
                        "iv_used_pct":      round(iv_pop * 100, 2),
                        "charges":          charges,
                        "net_max_profit":   round(max_profit_rs - charges["total"], 2),
                        "net_max_loss":     round(max_loss_rs   + charges["total"], 2),
                        "net_expected_value": round(ev_rs       - charges["total"], 2),
                    })

    sort_results(results, sort_by)
    page_results, total_pages = paginate(results, page, per_page)

    return {
        "underlying": underlying, "expiry": expiry, "spot": spot,
        "lot_size": lot_size, "dte": chain_data["dte"], "atm_strike": atm_strike,
        "scanned": total_scanned, "found": len(results), "sort_by": sort_by,
        "filters": {
            "min_oi": min_oi,
            "min_volume": min_volume if trading_day else 0,
            "max_spread_pct": max_spread_pct,
            "max_atm_pct": max_atm_pct,
            "min_lots": min_lots,
            "is_trading_day": trading_day,
        },
        "total": len(results), "page": page, "per_page": per_page,
        "total_pages": total_pages,
        "results": page_results,
    }


def scan_bwb_all(
    max_loss: int = 0,
    min_profit: int = 1000,
    min_oi: int = 10000,
    min_volume: int = 100,
    max_spread_pct: float = 5.0,
    max_atm_pct: float = 5.0,
    min_lots: int = 5,
    min_pop: float = 0.0,
    min_ev: int = -999999,
    expiries_per_underlying: int = 10,
    sort_by: str = "profit",
    auto_relax: bool = True,
    page: int = 1,
    per_page: int = 30,
) -> dict:
    underlyings = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"]

    if auto_relax:
        ladders = [
            {"max_loss": max_loss,             "min_profit": min_profit, "label": "user filters"},
            {"max_loss": max(max_loss, 5000),  "min_profit": min_profit, "label": "loss ≤ ₹5k"},
            {"max_loss": max(max_loss, 25000), "min_profit": min_profit, "label": "loss ≤ ₹25k"},
            {"max_loss": 999999,               "min_profit": 0,          "label": "any positive setup"},
        ]
    else:
        ladders = [{"max_loss": max_loss, "min_profit": min_profit, "label": "user filters"}]

    relaxed_to    = ""
    all_results   = []
    scan_log      = []
    total_scanned = 0

    for rung in ladders:
        all_results = []
        scan_log    = []
        for ul in underlyings:
            try:
                exps = opt_eng.get_nfo_expiries(ul)
                if not exps:
                    scan_log.append({"underlying": ul, "error": "no expiries"})
                    continue
                for exp in exps[:expiries_per_underlying]:
                    try:
                        resp = scan_bwb(
                            underlying=ul, expiry=exp,
                            max_loss=rung["max_loss"], min_profit=rung["min_profit"],
                            min_oi=min_oi, min_volume=min_volume,
                            max_spread_pct=max_spread_pct, max_atm_pct=max_atm_pct,
                            min_lots=min_lots, min_pop=min_pop, min_ev=min_ev,
                            sort_by=sort_by,
                        )
                        for r in resp["results"]:
                            r["_underlying"] = ul
                            r["_expiry"]     = exp
                            r["_dte"]        = resp["dte"]
                            r["_spot"]       = resp["spot"]
                            all_results.append(r)
                        total_scanned += resp["scanned"]
                        scan_log.append({
                            "underlying": ul, "expiry": exp,
                            "dte": resp["dte"], "scanned": resp["scanned"], "found": resp["found"],
                        })
                    except Exception as e:
                        scan_log.append({"underlying": ul, "expiry": exp, "error": str(e)[:80]})
            except Exception as e:
                scan_log.append({"underlying": ul, "error": str(e)[:80]})

        if all_results:
            relaxed_to = rung["label"]
            break

    sort_results(all_results, sort_by)
    page_results, total_pages = paginate(all_results, page, per_page)

    return {
        "scanned": total_scanned, "found": len(all_results),
        "scan_log": scan_log, "relaxed_to": relaxed_to,
        "filters": {
            "min_oi": min_oi, "min_volume": min_volume,
            "max_spread_pct": max_spread_pct, "max_atm_pct": max_atm_pct,
            "min_lots": min_lots, "expiries_per_underlying": expiries_per_underlying,
        },
        "total": len(all_results), "page": page, "per_page": per_page,
        "total_pages": total_pages,
        "results": page_results,
    }
