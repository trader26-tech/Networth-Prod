"""
Batman strategy scanner = Put BWB (left side) + Call BWB (right side).

Left side (puts):  +1 K1 PE, -2 K2 PE, +1 K3 PE  (K1 < K2 < K3 ≤ ATM)
Right side (calls): +1 K4 CE, -2 K5 CE, +1 K6 CE  (ATM ≤ K4 < K5 < K6)
"""
from api.core.math_utils import batman_pnl_at_expiry, batman_pop_and_ev
from api.core.charges    import compute_charges
from api.core.liquidity  import liquidity_score
from api.core.chain      import spot_for, build_real_chain
from api.scanners        import collect_option, is_tradeable, is_trading_day, sort_results, paginate
from api                 import options_engine as opt_eng
from api                 import state


def _get_chain(underlying, expiry, spot):
    if state.get_kite():
        try:
            return build_real_chain(underlying, expiry, spot)
        except Exception:
            pass
    return opt_eng.build_option_chain(underlying, expiry, spot, None)


def scan_batman(
    underlying: str = "NIFTY",
    expiry: str = "",
    max_loss: int = 5000,
    min_profit: int = 1000,
    min_oi: int = 10000,
    min_volume: int = 100,
    max_spread_pct: float = 5.0,
    max_atm_pct: float = 8.0,
    min_lots: int = 5,
    min_pop: float = 0.0,
    min_ev: int = -999999,
    sort_by: str = "pop,ev",
    max_strikes: int = 6,
    page: int = 1,
    per_page: int = 30,
) -> dict:
    spot = spot_for(underlying)
    if not expiry:
        exps   = opt_eng.get_nfo_expiries(underlying)
        expiry = exps[0] if exps else ""

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

    puts  = []
    calls = []
    for row in chain:
        strike = float(row["strike"])
        pe, ce = row["pe"], row["ce"]
        if pe["price"] > 0.10 and _tradeable(pe, strike):
            puts.append(collect_option(pe, row))
        if ce["price"] > 0.10 and _tradeable(ce, strike):
            calls.append(collect_option(ce, row))

    puts.sort(key=lambda x: x["strike"])
    calls.sort(key=lambda x: x["strike"])

    results       = []
    total_scanned = 0
    np_len        = len(puts)
    nc_len        = len(calls)

    for pi in range(np_len):
        for pj in range(pi + 1, np_len):
            for pk in range(pj + 1, np_len):
                if (pk - pi) > max_strikes:
                    continue
                K1, K2, K3 = puts[pi]["strike"], puts[pj]["strike"], puts[pk]["strike"]
                P1 = puts[pi]["ask"]
                P2 = puts[pj]["bid"]
                P3 = puts[pk]["ask"]
                if P1 <= 0 or P2 <= 0 or P3 <= 0:
                    continue
                a_p = K2 - K1
                b_p = K3 - K2
                if a_p == 0 or b_p == 0:
                    continue
                nc_put = -P1 + 2 * P2 - P3

                for ci in range(nc_len):
                    for cj in range(ci + 1, nc_len):
                        for ck in range(cj + 1, nc_len):
                            if (ck - ci) > max_strikes:
                                continue
                            K4, K5, K6 = calls[ci]["strike"], calls[cj]["strike"], calls[ck]["strike"]
                            P4 = calls[ci]["ask"]
                            P5 = calls[cj]["bid"]
                            P6 = calls[ck]["ask"]
                            if P4 <= 0 or P5 <= 0 or P6 <= 0:
                                continue
                            a_c = K5 - K4
                            b_c = K6 - K5
                            if a_c == 0 or b_c == 0:
                                continue
                            total_scanned += 1

                            nc_call  = -P4 + 2 * P5 - P6
                            nc_total = nc_put + nc_call

                            pnl_far_left   = (b_p - a_p) + nc_total
                            pnl_left_peak  = b_p + nc_total
                            pnl_middle     = nc_total
                            pnl_right_peak = a_c + nc_total
                            pnl_far_right  = (a_c - b_c) + nc_total

                            if pnl_middle < 0:
                                continue

                            worst_pts = min(pnl_far_left, pnl_left_peak, pnl_middle,
                                           pnl_right_peak, pnl_far_right)
                            best_pts  = max(pnl_far_left, pnl_left_peak, pnl_middle,
                                           pnl_right_peak, pnl_far_right)

                            max_loss_rs   = max(0.0, -worst_pts) * lot_size
                            max_profit_rs = best_pts * lot_size
                            nc_rs         = nc_total * lot_size

                            if max_loss_rs > max_loss:
                                continue
                            if max_profit_rs < min_profit:
                                continue

                            capital     = max(0.0, -nc_rs) + max_loss_rs
                            leg_data_all = [puts[pi], puts[pj], puts[pk],
                                            calls[ci], calls[cj], calls[ck]]
                            liq = liquidity_score(leg_data_all)

                            lots_K1 = puts[pi]["ask_qty_total"]  // lot_size
                            lots_K2 = puts[pj]["bid_qty_total"]  // (2 * lot_size)
                            lots_K3 = puts[pk]["ask_qty_total"]  // lot_size
                            lots_K4 = calls[ci]["ask_qty_total"] // lot_size
                            lots_K5 = calls[cj]["bid_qty_total"] // (2 * lot_size)
                            lots_K6 = calls[ck]["ask_qty_total"] // lot_size
                            lots_available = int(min(lots_K1, lots_K2, lots_K3,
                                                     lots_K4, lots_K5, lots_K6))

                            if lots_available < min_lots:
                                continue

                            iv_pop = (
                                puts[pj]["iv"] or puts[pk]["iv"]
                                or calls[ci]["iv"] or calls[cj]["iv"] or 20.0
                            ) / 100
                            pop, ev_rs = batman_pop_and_ev(
                                K1, K2, K3, K4, K5, K6,
                                P1, P2, P3, P4, P5, P6,
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
                                    {"action": "BUY",  "qty": 1, "lot_size": lot_size, "entry_premium": P4},
                                    {"action": "SELL", "qty": 2, "lot_size": lot_size, "entry_premium": P5},
                                    {"action": "BUY",  "qty": 1, "lot_size": lot_size, "entry_premium": P6},
                                ],
                                include_exit=True,
                            )

                            results.append({
                                "K1": int(K1), "K2": int(K2), "K3": int(K3),
                                "K4": int(K4), "K5": int(K5), "K6": int(K6),
                                "P1": round(P1, 2), "P2": round(P2, 2), "P3": round(P3, 2),
                                "P4": round(P4, 2), "P5": round(P5, 2), "P6": round(P6, 2),
                                "P1_ltp": round(puts[pi]["price"], 2),
                                "P2_ltp": round(puts[pj]["price"], 2),
                                "P3_ltp": round(puts[pk]["price"], 2),
                                "P4_ltp": round(calls[ci]["price"], 2),
                                "P5_ltp": round(calls[cj]["price"], 2),
                                "P6_ltp": round(calls[ck]["price"], 2),
                                "sym1": puts[pi]["symbol"],  "sym2": puts[pj]["symbol"],
                                "sym3": puts[pk]["symbol"],  "sym4": calls[ci]["symbol"],
                                "sym5": calls[cj]["symbol"], "sym6": calls[ck]["symbol"],
                                "iv1": puts[pi]["iv"],  "iv2": puts[pj]["iv"],  "iv3": puts[pk]["iv"],
                                "iv4": calls[ci]["iv"], "iv5": calls[cj]["iv"], "iv6": calls[ck]["iv"],
                                "leg_liq": [
                                    {"oi": puts[pi]["oi"], "vol": puts[pi]["volume"],
                                     "bid": puts[pi]["bid"], "ask": puts[pi]["ask"],
                                     "bid_qty_total": puts[pi]["bid_qty_total"],
                                     "ask_qty_total": puts[pi]["ask_qty_total"],
                                     "side": "BUY",  "available_qty": puts[pi]["ask_qty_total"], "lots": int(lots_K1)},
                                    {"oi": puts[pj]["oi"], "vol": puts[pj]["volume"],
                                     "bid": puts[pj]["bid"], "ask": puts[pj]["ask"],
                                     "bid_qty_total": puts[pj]["bid_qty_total"],
                                     "ask_qty_total": puts[pj]["ask_qty_total"],
                                     "side": "SELL", "available_qty": puts[pj]["bid_qty_total"], "lots": int(lots_K2)},
                                    {"oi": puts[pk]["oi"], "vol": puts[pk]["volume"],
                                     "bid": puts[pk]["bid"], "ask": puts[pk]["ask"],
                                     "bid_qty_total": puts[pk]["bid_qty_total"],
                                     "ask_qty_total": puts[pk]["ask_qty_total"],
                                     "side": "BUY",  "available_qty": puts[pk]["ask_qty_total"], "lots": int(lots_K3)},
                                    {"oi": calls[ci]["oi"], "vol": calls[ci]["volume"],
                                     "bid": calls[ci]["bid"], "ask": calls[ci]["ask"],
                                     "bid_qty_total": calls[ci]["bid_qty_total"],
                                     "ask_qty_total": calls[ci]["ask_qty_total"],
                                     "side": "BUY",  "available_qty": calls[ci]["ask_qty_total"], "lots": int(lots_K4)},
                                    {"oi": calls[cj]["oi"], "vol": calls[cj]["volume"],
                                     "bid": calls[cj]["bid"], "ask": calls[cj]["ask"],
                                     "bid_qty_total": calls[cj]["bid_qty_total"],
                                     "ask_qty_total": calls[cj]["ask_qty_total"],
                                     "side": "SELL", "available_qty": calls[cj]["bid_qty_total"], "lots": int(lots_K5)},
                                    {"oi": calls[ck]["oi"], "vol": calls[ck]["volume"],
                                     "bid": calls[ck]["bid"], "ask": calls[ck]["ask"],
                                     "bid_qty_total": calls[ck]["bid_qty_total"],
                                     "ask_qty_total": calls[ck]["ask_qty_total"],
                                     "side": "BUY",  "available_qty": calls[ck]["ask_qty_total"], "lots": int(lots_K6)},
                                ],
                                "lots_available":    lots_available,
                                "net_credit":        round(nc_rs, 2),
                                "max_profit":        round(max_profit_rs, 2),
                                "max_loss":          round(max_loss_rs, 2),
                                "capital":           round(capital, 2),
                                "lot_size":          lot_size,
                                "pnl_far_left":      round(pnl_far_left   * lot_size, 2),
                                "pnl_left_peak":     round(pnl_left_peak  * lot_size, 2),
                                "pnl_middle":        round(pnl_middle     * lot_size, 2),
                                "pnl_right_peak":    round(pnl_right_peak * lot_size, 2),
                                "pnl_far_right":     round(pnl_far_right  * lot_size, 2),
                                "left_inner_wing":   int(a_p),
                                "left_outer_wing":   int(b_p),
                                "right_inner_wing":  int(a_c),
                                "right_outer_wing":  int(b_c),
                                "min_oi":            liq["min_oi"],
                                "min_volume":        liq["min_volume"],
                                "max_spread_pct":    liq["max_spread_pct"],
                                "liquidity_score":   liq["score"],
                                "liquidity_tier":    liq["tier"],
                                "fillable":          lots_available >= 1,
                                "rr_ratio":          round(max_profit_rs / max_loss_rs, 2) if max_loss_rs > 0 else 9999.0,
                                "atm_distance_left":  int(abs(K2 - atm_strike)),
                                "atm_distance_right": int(abs(K5 - atm_strike)),
                                "pop":               round(pop, 4),
                                "expected_value":    round(ev_rs, 2),
                                "iv_used_pct":       round(iv_pop * 100, 2),
                                "charges":           charges,
                                "net_max_profit":    round(max_profit_rs - charges["total"], 2),
                                "net_max_loss":      round(max_loss_rs   + charges["total"], 2),
                                "net_expected_value": round(ev_rs        - charges["total"], 2),
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


def scan_batman_all(
    max_loss: int = 5000,
    min_profit: int = 1000,
    min_oi: int = 10000,
    min_volume: int = 100,
    max_spread_pct: float = 5.0,
    max_atm_pct: float = 8.0,
    min_lots: int = 5,
    min_pop: float = 0.0,
    min_ev: int = -999999,
    expiries_per_underlying: int = 10,
    sort_by: str = "pop,ev",
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
                        resp = scan_batman(
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
