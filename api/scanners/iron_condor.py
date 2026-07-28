"""
Iron Condor scanner — correct 10-step algorithm:

1.  Short put  (K2): delta ≈ 0.15–0.20 (configurable)
2.  Short call (K3): delta ≈ 0.15–0.20
3.  Long put   (K1) = K2 − wing  (equal-width enforced)
4.  Long call  (K4) = K3 + wing  (same wing as put side)
5.  net_credit = bid(K2) + bid(K3) − ask(K1) − ask(K4)  > 0
6.  1/3 rule:  net_credit ≥ wing × min_credit_ratio
7.  PoP ≈ 65–70% via log-normal integration
8.  Breakevens: lower = K2 − nc,  upper = K3 + nc
9.  Profit target = 50% of net credit
10. Stop-loss   = 2× net credit received
"""
from api.core.math_utils import bs_delta, ic_pnl_at_expiry, ic_pop_and_ev
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


def scan_iron_condor(
    underlying: str = "NIFTY",
    expiry: str = "",
    max_loss: int = 10000,
    min_profit: int = 500,
    min_oi: int = 10000,
    min_volume: int = 100,
    max_spread_pct: float = 5.0,
    max_atm_pct: float = 20.0,
    min_lots: int = 5,
    min_pop: float = 0.0,
    min_ev: int = -999999,
    min_short_delta: float = 0.10,
    max_short_delta: float = 0.30,
    min_credit_ratio: float = 0.30,
    max_wing_count: int = 6,
    sort_by: str = "ev,pop",
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

    # ── Detect step size ──────────────────────────────────────────────────────
    all_strikes_sorted = sorted({float(r["strike"]) for r in chain})
    step = 50.0
    if len(all_strikes_sorted) >= 2:
        diffs = [all_strikes_sorted[i+1] - all_strikes_sorted[i]
                 for i in range(len(all_strikes_sorted) - 1)]
        step  = min(diffs) if diffs else 50.0

    # ── Collect tradeable options + BS delta ──────────────────────────────────
    all_puts_by_strike:  dict[float, dict] = {}
    all_calls_by_strike: dict[float, dict] = {}

    for row in chain:
        strike = float(row["strike"])
        pe, ce = row["pe"], row["ce"]

        if pe["price"] > 0.10 and _tradeable(pe, strike):
            d        = collect_option(pe, row)
            iv       = (pe["iv"] or 20.0) / 100
            d["delta"] = bs_delta(spot, strike, T, iv, opt_type='PE') if T > 0 else 0.0
            all_puts_by_strike[strike] = d

        if ce["price"] > 0.10 and _tradeable(ce, strike):
            d        = collect_option(ce, row)
            iv       = (ce["iv"] or 20.0) / 100
            d["delta"] = bs_delta(spot, strike, T, iv, opt_type='CE') if T > 0 else 0.0
            all_calls_by_strike[strike] = d

    # Short strikes must be OTM and in target delta range
    short_puts = [
        v for v in all_puts_by_strike.values()
        if v["strike"] < spot and min_short_delta <= abs(v["delta"]) <= max_short_delta
    ]
    short_calls = [
        v for v in all_calls_by_strike.values()
        if v["strike"] > spot and min_short_delta <= abs(v["delta"]) <= max_short_delta
    ]

    wing_widths   = [step * n for n in range(1, max_wing_count + 1)]
    results       = []
    total_scanned = 0

    for sp in short_puts:
        K2, P2     = sp["strike"], sp["bid"]
        delta_sp   = abs(sp["delta"])
        if P2 <= 0:
            continue

        for wing in wing_widths:
            K1 = K2 - wing
            lp = all_puts_by_strike.get(K1)
            if lp is None:
                continue
            P1 = lp["ask"]
            if P1 <= 0:
                continue

            for sc in short_calls:
                K3, P3   = sc["strike"], sc["bid"]
                delta_sc = abs(sc["delta"])
                if P3 <= 0 or K3 <= K2:
                    continue

                K4 = K3 + wing
                lc = all_calls_by_strike.get(K4)
                if lc is None:
                    continue
                P4 = lc["ask"]
                if P4 <= 0:
                    continue

                total_scanned += 1

                nc = (P2 + P3) - (P1 + P4)
                if nc <= 0:
                    continue
                if nc < wing * min_credit_ratio:
                    continue

                profit_zone_width = K3 - K2
                if profit_zone_width <= 0:
                    continue

                max_loss_rs   = max(0.0, wing - nc) * lot_size
                max_profit_rs = nc * lot_size
                net_credit_rs = nc * lot_size

                if max_loss_rs > max_loss:
                    continue
                if max_profit_rs < min_profit:
                    continue

                be_lower         = K2 - nc
                be_upper         = K3 + nc
                profit_target_50 = net_credit_rs * 0.50
                stop_loss_2x     = net_credit_rs * 2.0
                credit_ratio     = round(nc / wing, 3)

                lots_K1 = lp["ask_qty_total"] // lot_size
                lots_K2 = sp["bid_qty_total"] // lot_size
                lots_K3 = sc["bid_qty_total"] // lot_size
                lots_K4 = lc["ask_qty_total"] // lot_size
                lots_available = int(min(lots_K1, lots_K2, lots_K3, lots_K4))

                if lots_available < min_lots:
                    continue

                liq = liquidity_score([lp, sp, sc, lc])

                iv_for_pop = ((sp["iv"] or 0) + (sc["iv"] or 0)) / 2
                if iv_for_pop <= 0:
                    iv_for_pop = lp["iv"] or lc["iv"] or 20.0
                iv_for_pop /= 100

                pop, ev_rs = ic_pop_and_ev(
                    K1, K2, K3, K4, P1, P2, P3, P4,
                    spot, T, iv_for_pop, lot_size,
                )

                if pop < min_pop:
                    continue
                if ev_rs < min_ev:
                    continue

                charges = compute_charges(
                    legs=[
                        {"action": "BUY",  "qty": 1, "lot_size": lot_size, "entry_premium": P1},
                        {"action": "SELL", "qty": 1, "lot_size": lot_size, "entry_premium": P2},
                        {"action": "SELL", "qty": 1, "lot_size": lot_size, "entry_premium": P3},
                        {"action": "BUY",  "qty": 1, "lot_size": lot_size, "entry_premium": P4},
                    ],
                    include_exit=True,
                )

                rr = round(max_profit_rs / max_loss_rs, 2) if max_loss_rs > 0 else 9999.0

                results.append({
                    "K1": int(K1), "K2": int(K2), "K3": int(K3), "K4": int(K4),
                    "P1": round(P1, 2), "P2": round(P2, 2),
                    "P3": round(P3, 2), "P4": round(P4, 2),
                    "P1_ltp": round(lp["price"], 2), "P2_ltp": round(sp["price"], 2),
                    "P3_ltp": round(sc["price"], 2), "P4_ltp": round(lc["price"], 2),
                    "sym1": lp["symbol"], "sym2": sp["symbol"],
                    "sym3": sc["symbol"], "sym4": lc["symbol"],
                    "iv1": lp["iv"], "iv2": sp["iv"], "iv3": sc["iv"], "iv4": lc["iv"],
                    "leg_liq": [
                        {"oi": lp["oi"], "vol": lp["volume"],
                         "bid": lp["bid"], "ask": lp["ask"],
                         "bid_qty_total": lp["bid_qty_total"],
                         "ask_qty_total": lp["ask_qty_total"],
                         "side": "BUY",  "available_qty": lp["ask_qty_total"], "lots": int(lots_K1)},
                        {"oi": sp["oi"], "vol": sp["volume"],
                         "bid": sp["bid"], "ask": sp["ask"],
                         "bid_qty_total": sp["bid_qty_total"],
                         "ask_qty_total": sp["ask_qty_total"],
                         "side": "SELL", "available_qty": sp["bid_qty_total"], "lots": int(lots_K2)},
                        {"oi": sc["oi"], "vol": sc["volume"],
                         "bid": sc["bid"], "ask": sc["ask"],
                         "bid_qty_total": sc["bid_qty_total"],
                         "ask_qty_total": sc["ask_qty_total"],
                         "side": "SELL", "available_qty": sc["bid_qty_total"], "lots": int(lots_K3)},
                        {"oi": lc["oi"], "vol": lc["volume"],
                         "bid": lc["bid"], "ask": lc["ask"],
                         "bid_qty_total": lc["bid_qty_total"],
                         "ask_qty_total": lc["ask_qty_total"],
                         "side": "BUY",  "available_qty": lc["ask_qty_total"], "lots": int(lots_K4)},
                    ],
                    "lots_available":     lots_available,
                    "net_credit":         round(net_credit_rs, 2),
                    "max_profit":         round(max_profit_rs, 2),
                    "max_loss":           round(max_loss_rs, 2),
                    "capital":            round(max_loss_rs, 2),
                    "lot_size":           lot_size,
                    "wing_width":         int(wing),
                    "pnl_far_left":       round((nc - wing) * lot_size, 2),
                    "pnl_profit_zone":    round(nc * lot_size, 2),
                    "pnl_far_right":      round((nc - wing) * lot_size, 2),
                    "profit_zone_width":  int(profit_zone_width),
                    "profit_zone_pct":    round(profit_zone_width / spot * 100, 2),
                    "left_wing_width":    int(wing),
                    "right_wing_width":   int(wing),
                    # Delta (steps 1 & 2)
                    "delta_short_put":    round(delta_sp, 3),
                    "delta_short_call":   round(delta_sc, 3),
                    # Management levels (steps 8, 9, 10)
                    "be_lower":           round(be_lower, 1),
                    "be_upper":           round(be_upper, 1),
                    "profit_target_50":   round(profit_target_50, 2),
                    "stop_loss_2x":       round(stop_loss_2x, 2),
                    "credit_ratio":       credit_ratio,
                    "min_oi":             liq["min_oi"],
                    "min_volume":         liq["min_volume"],
                    "max_spread_pct":     liq["max_spread_pct"],
                    "liquidity_score":    liq["score"],
                    "liquidity_tier":     liq["tier"],
                    "fillable":           lots_available >= 1,
                    "rr_ratio":           rr,
                    "atm_distance_left":  int(abs(K2 - atm_strike)),
                    "atm_distance_right": int(abs(K3 - atm_strike)),
                    "pop":                round(pop, 4),
                    "expected_value":     round(ev_rs, 2),
                    "iv_used_pct":        round(iv_for_pop * 100, 2),
                    "charges":            charges,
                    "net_max_profit":     round(max_profit_rs - charges["total"], 2),
                    "net_max_loss":       round(max_loss_rs   + charges["total"], 2),
                    "net_expected_value": round(ev_rs         - charges["total"], 2),
                })

    extra_keys = {
        "width": lambda x: -x["profit_zone_pct"],
        "delta": lambda x:  abs(x.get("delta_short_put", 0) - 0.175),
    }
    sort_results(results, sort_by, key_map=extra_keys)
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
            "min_short_delta": min_short_delta,
            "max_short_delta": max_short_delta,
            "min_credit_ratio": min_credit_ratio,
            "is_trading_day": trading_day,
        },
        "total": len(results), "page": page, "per_page": per_page,
        "total_pages": total_pages,
        "results": page_results,
    }


def scan_iron_condor_all(
    max_loss: int = 10000,
    min_profit: int = 500,
    min_oi: int = 10000,
    min_volume: int = 100,
    max_spread_pct: float = 5.0,
    max_atm_pct: float = 10.0,
    min_lots: int = 5,
    min_pop: float = 0.0,
    min_ev: int = -999999,
    min_short_delta: float = 0.10,
    max_short_delta: float = 0.30,
    min_credit_ratio: float = 0.30,
    expiries_per_underlying: int = 10,
    sort_by: str = "ev,pop",
    auto_relax: bool = True,
    page: int = 1,
    per_page: int = 30,
) -> dict:
    underlyings = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"]

    if auto_relax:
        ladders = [
            {"max_loss": max_loss,             "min_profit": min_profit, "label": "user filters"},
            {"max_loss": max(max_loss, 10000), "min_profit": min_profit, "label": "loss ≤ ₹10k"},
            {"max_loss": max(max_loss, 30000), "min_profit": min_profit, "label": "loss ≤ ₹30k"},
            {"max_loss": 999999,               "min_profit": 0,          "label": "any positive credit"},
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
                        resp = scan_iron_condor(
                            underlying=ul, expiry=exp,
                            max_loss=rung["max_loss"], min_profit=rung["min_profit"],
                            min_oi=min_oi, min_volume=min_volume,
                            max_spread_pct=max_spread_pct, max_atm_pct=max_atm_pct,
                            min_lots=min_lots, min_pop=min_pop, min_ev=min_ev,
                            min_short_delta=min_short_delta,
                            max_short_delta=max_short_delta,
                            min_credit_ratio=min_credit_ratio,
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

    extra_keys = {
        "width": lambda x: -x.get("profit_zone_pct", 0),
        "delta": lambda x:  abs(x.get("delta_short_put", 0) - 0.175),
    }
    sort_results(all_results, sort_by, key_map=extra_keys)
    page_results, total_pages = paginate(all_results, page, per_page)

    return {
        "scanned": total_scanned, "found": len(all_results),
        "scan_log": scan_log, "relaxed_to": relaxed_to,
        "filters": {
            "min_oi": min_oi, "min_volume": min_volume,
            "max_spread_pct": max_spread_pct, "max_atm_pct": max_atm_pct,
            "min_lots": min_lots,
            "min_short_delta": min_short_delta,
            "max_short_delta": max_short_delta,
            "min_credit_ratio": min_credit_ratio,
            "expiries_per_underlying": expiries_per_underlying,
        },
        "total": len(all_results), "page": page, "per_page": per_page,
        "total_pages": total_pages,
        "results": page_results,
    }
