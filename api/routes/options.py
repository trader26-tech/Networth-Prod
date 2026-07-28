"""Options chain, expiries, underlyings, and strategy P&L routes."""
import math
import datetime as _dt
from fastapi import APIRouter, HTTPException
from api.models import StrategyPnlRequest
from api.core.chain import spot_for, build_real_chain, _get_nfo_instruments
from api import options_engine as opt_eng
from api import state

router = APIRouter(prefix="/api/options", tags=["options"])


def _get_expiries(underlying: str) -> list[str]:
    """Return real expiry dates from Kite when connected, else mock.

    Anchors 'today' to IST so servers in UTC don't re-include expiries that
    already passed in India after midnight IST.

    Uses the shared NFO instrument cache (_get_nfo_instruments) so we don't
    re-download ~10k rows on every call — that path triggered Kite's
    "Too many requests" rate limit when several endpoints polled in parallel.
    """
    kite = state.get_kite()
    if kite:
        try:
            today = opt_eng._ist_today()   # IST date — not server-local UTC
            instruments = _get_nfo_instruments()    # 5-min cached, shared with chain builds
            seen: set[str] = set()
            expiries: list[str] = []
            for i in instruments:
                if str(i.get("name", "")).upper() != underlying.upper():
                    continue
                if i.get("instrument_type") not in ("CE", "PE"):
                    continue
                raw = i.get("expiry")
                if raw is None:
                    continue
                exp_str = raw.isoformat() if hasattr(raw, "isoformat") else str(raw)[:10]
                if exp_str in seen:
                    continue
                try:
                    exp_date = _dt.date.fromisoformat(exp_str)
                except ValueError:
                    continue
                if exp_date >= today:
                    seen.add(exp_str)
                    expiries.append(exp_str)
            if expiries:
                return sorted(expiries)[:10]
        except Exception as e:
            print(f"Kite expiry fetch error: {e}")
    return opt_eng.get_nfo_expiries(underlying)


@router.get("/underlyings")
def get_underlyings():
    return [
        {"symbol": k, "name": v["symbol"], "lot_size": opt_eng.LOT_SIZES.get(k, 50)}
        for k, v in opt_eng.UNDERLYINGS.items()
    ]


@router.get("/expiries")
def get_expiries(underlying: str = "NIFTY"):
    return _get_expiries(underlying)


@router.get("/chain")
def get_option_chain(underlying: str = "NIFTY", expiry: str = "", spot: float = 0.0):
    """Option chain for ANY NFO underlying (indices AND stocks like INFY/HDFCBANK).

    `spot` lets the caller pass the live underlying price (e.g. the F&O book's
    spot) so stock chains work without an index-style spot feed. Real Kite quotes
    are used when a session is live; otherwise a Black-Scholes mock chain is built.
    """
    info = opt_eng.UNDERLYINGS.get(underlying)
    # spot: explicit param → index spot feed → 0 (mock needs one; real chain doesn't)
    s = spot if spot and spot > 0 else (spot_for(underlying) if info else 0.0)

    if not expiry:
        expiries = _get_expiries(underlying)
        expiry   = expiries[0] if expiries else ""

    if state.get_kite():
        try:
            return build_real_chain(underlying, expiry, s or spot_for(underlying) if info else s)
        except Exception as e:
            print(f"Real chain build failed for {underlying}, falling back to mock: {e}")

    if not s or s <= 0:
        raise HTTPException(400, f"No spot for {underlying}; pass ?spot=<price>")
    return opt_eng.build_option_chain(underlying, expiry, s, None)


@router.get("/futures-price")
def futures_price(
    underlying:     str   = "NIFTY",
    risk_free_rate: float = 0.065,   # annualised, e.g. RBI repo 6.5%
    dividend_yield: float = 0.013,   # annualised Nifty 50 div yield ~1.3%
):
    """
    Theoretical futures price via cost-of-carry + market-implied price
    via put-call parity (F ≈ K_atm + C_atm − P_atm) for all near expiries.
    """
    info = opt_eng.UNDERLYINGS.get(underlying)
    if not info:
        raise HTTPException(400, f"Unknown underlying: {underlying}")

    spot     = spot_for(underlying)
    expiries = _get_expiries(underlying)

    rows = []
    for expiry in expiries[:6]:
        try:
            if state.get_kite():
                cd = build_real_chain(underlying, expiry, spot)
            else:
                cd = opt_eng.build_option_chain(underlying, expiry, spot, None)
        except Exception:
            continue

        chain = cd["chain"]
        dte   = cd["dte"]
        atm   = cd["atm_strike"]

        if dte <= 0:
            continue

        T    = dte / 365.0
        theo = round(spot * math.exp((risk_free_rate - dividend_yield) * T), 2)

        # Market-implied futures via put-call parity at the ATM strike
        atm_row = next((r for r in chain if abs(float(r["strike"]) - atm) < 0.5), None)
        implied = implied_basis = implied_rate_pct = None
        if atm_row:
            c = float(atm_row["ce"].get("price") or 0)
            p = float(atm_row["pe"].get("price") or 0)
            if c > 0 and p > 0:
                implied           = round(atm + c - p, 2)
                implied_basis     = round(implied - spot, 2)
                try:
                    implied_rate_pct = round(math.log(implied / spot) / T * 100, 3)
                except Exception:
                    pass

        rows.append({
            "expiry":               expiry,
            "dte":                  dte,
            "atm_strike":           float(atm),
            "theoretical":          theo,
            "carry_pts":            round(theo - spot, 2),
            "carry_pct":            round((theo - spot) / spot * 100, 4),
            "annualized_carry_pct": round(math.log(theo / spot) / T * 100, 3),
            "implied":              implied,
            "implied_basis":        implied_basis,
            "implied_rate_pct":     implied_rate_pct,
        })

    return {
        "underlying":     underlying,
        "spot":           spot,
        "risk_free_rate": risk_free_rate,
        "dividend_yield": dividend_yield,
        "expiries":       rows,
    }


@router.get("/cheap-pe-scan")
def cheap_pe_scan(
    underlying:     str   = "NIFTY",
    min_dte:        int   = 90,
    max_dte:        int   = 400,
    moneyness_min:  float = -15.0,
    moneyness_max:  float = -2.0,
    weights:        str   = "25,25,25,25",
    top_n:          int   = 50,
):
    """Rank far-OTM PE strikes across long-dated expiries by a composite
    'cheapness' score. Snapshot-only (no historical IV needed).

    Sub-metrics, each percentile-ranked across the candidate set:
      1. prem_pct_spot   = pe.price / spot           (lower = cheaper)
      2. theta_drag      = pe.price / dte            (lower = cheaper per day)
      3. iv_skew         = (pe.iv - atm_pe.iv)/atm   (lower = cheaper vs ATM)
      4. iv_term         = (pe.iv - ref_pe.iv)/ref   (lower = cheaper vs near expiry)

    Weights map to those 4 metrics in order. Default equal weight.
    """
    info = opt_eng.UNDERLYINGS.get(underlying)
    if not info:
        raise HTTPException(400, f"Unknown underlying: {underlying}")

    spot     = spot_for(underlying)
    today    = opt_eng._ist_today()
    expiries = _get_expiries(underlying)

    parsed: list[tuple[str, int]] = []
    for e in expiries:
        try:
            dte = (_dt.date.fromisoformat(e) - today).days
        except ValueError:
            continue
        if dte > 0:
            parsed.append((e, dte))

    in_range = [(e, d) for e, d in parsed if min_dte <= d <= max_dte]
    fallback_notice = ""
    if not in_range and parsed:
        in_range = parsed
        fallback_notice = (f"no expiries in [{min_dte}, {max_dte}] DTE — "
                           f"falling back to all {len(parsed)} available expiries")

    if not in_range:
        return {"underlying": underlying, "spot": spot, "candidates": [],
                "reason": "no expiries available"}

    chains: dict = {}
    for e, _ in in_range:
        try:
            if state.get_kite():
                chains[e] = build_real_chain(underlying, e, spot,
                                             below_atm=80, above_atm=3)
            else:
                chains[e] = opt_eng.build_option_chain(underlying, e, spot, None)
        except Exception as ex:
            print(f"cheap-pe-scan: chain build failed for {e}: {ex}")

    if not chains:
        return {"underlying": underlying, "spot": spot, "candidates": []}

    ref_expiry = min(chains.keys(), key=lambda e: chains[e]["dte"])
    ref_chain  = chains[ref_expiry]["chain"]

    def _pe_iv_at_moneyness(chain, target_pct):
        target_strike = spot * (1 + target_pct / 100)
        row = min(chain, key=lambda r: abs(float(r["strike"]) - target_strike))
        return row["pe"].get("iv") or 0.0

    candidates = []
    for e, cd in chains.items():
        dte = cd["dte"]
        if dte <= 0:
            continue
        atm = cd["atm_strike"]
        atm_row = next((r for r in cd["chain"]
                        if abs(float(r["strike"]) - atm) < 0.5), None)
        atm_pe_iv = (atm_row["pe"].get("iv") if atm_row else None) or 0.0

        for row in cd["chain"]:
            K   = float(row["strike"])
            mny = (K - spot) / spot * 100.0   # negative for OTM puts
            if not (moneyness_min <= mny <= moneyness_max):
                continue
            pe  = row["pe"]
            price = pe.get("price") or 0.0
            iv    = pe.get("iv") or 0.0
            if price <= 0 or iv <= 0:
                continue

            ref_iv = _pe_iv_at_moneyness(ref_chain, mny)
            iv_skew = ((iv - atm_pe_iv) / atm_pe_iv) if atm_pe_iv > 0 else 0.0
            iv_term = ((iv - ref_iv)    / ref_iv)    if ref_iv    > 0 else 0.0

            candidates.append({
                "expiry":        e,
                "dte":           dte,
                "strike":        K,
                "moneyness_pct": round(mny, 2),
                "pe": {
                    "price": round(float(price), 2),
                    "iv":    round(float(iv), 2),
                    "delta": pe.get("delta"),
                },
                "raw": {
                    "prem_pct_spot": price / spot,
                    "theta_drag":    price / dte,
                    "iv_skew":       iv_skew,
                    "iv_term":       iv_term,
                },
            })

    if len(candidates) < 2:
        return {"underlying": underlying, "spot": spot, "candidates": candidates}

    def _rank_lower_better(vals: list[float]) -> list[float]:
        n = len(vals)
        out = []
        for v in vals:
            below = sum(1 for x in vals if x < v)
            equal = sum(1 for x in vals if x == v)
            pct   = (below + 0.5 * equal) / n
            out.append(round((1 - pct) * 100, 1))
        return out

    keys = ["prem_pct_spot", "theta_drag", "iv_skew", "iv_term"]
    rank_arrays = {k: _rank_lower_better([c["raw"][k] for c in candidates])
                   for k in keys}

    try:
        w = [float(x) for x in weights.split(",")]
        if len(w) != 4 or sum(w) <= 0:
            w = [25.0, 25.0, 25.0, 25.0]
    except ValueError:
        w = [25.0, 25.0, 25.0, 25.0]
    wsum = sum(w)
    w_norm = [x / wsum for x in w]

    for i, c in enumerate(candidates):
        c["ranks"] = {k: rank_arrays[k][i] for k in keys}
        c["composite_score"] = round(
            sum(c["ranks"][k] * w_norm[j] for j, k in enumerate(keys)), 2
        )
        c["raw"] = {k: round(v, 6) for k, v in c["raw"].items()}

    candidates.sort(key=lambda c: c["composite_score"], reverse=True)
    for i, c in enumerate(candidates):
        c["rank"] = i + 1

    out = {
        "underlying":           underlying,
        "spot":                 spot,
        "expiries_scanned":     sorted(chains.keys()),
        "reference_expiry":     ref_expiry,
        "candidates_evaluated": len(candidates),
        "weights":              dict(zip(keys, w_norm)),
        "candidates":           candidates[:top_n],
    }
    if fallback_notice:
        out["notice"] = fallback_notice
    return out


@router.get("/rich-pe-scan")
def rich_pe_scan(
    underlying:     str   = "NIFTY",
    min_dte:        int   = 30,
    max_dte:        int   = 180,
    moneyness_min:  float = -10.0,
    moneyness_max:  float = 0.0,
    weights:        str   = "25,25,25,25",
    top_n:          int   = 50,
):
    """Mirror of cheap-pe-scan but ranks PE strikes by 'richness' — best for
    SELLING (Phase 2 of the bull-thesis put-spread strategy).

    Sub-metrics, each percentile-ranked across the candidate set
    (HIGHER raw = HIGHER rank):
      1. annualized_yield = (price/strike) * 365/dte   (% return on capital)
      2. prem_pct_spot    = price / spot               (absolute richness)
      3. iv_vs_atm        = (pe.iv - atm_pe.iv)/atm    (rich vs ATM strikes)
      4. iv_vs_term       = (pe.iv - ref_pe.iv)/ref    (rich vs near expiry)
    """
    info = opt_eng.UNDERLYINGS.get(underlying)
    if not info:
        raise HTTPException(400, f"Unknown underlying: {underlying}")

    spot     = spot_for(underlying)
    today    = opt_eng._ist_today()
    expiries = _get_expiries(underlying)

    parsed: list[tuple[str, int]] = []
    for e in expiries:
        try:
            dte = (_dt.date.fromisoformat(e) - today).days
        except ValueError:
            continue
        if dte > 0:
            parsed.append((e, dte))

    in_range = [(e, d) for e, d in parsed if min_dte <= d <= max_dte]
    fallback_notice = ""
    if not in_range and parsed:
        in_range = parsed
        fallback_notice = (f"no expiries in [{min_dte}, {max_dte}] DTE — "
                           f"falling back to all {len(parsed)} available expiries")

    if not in_range:
        return {"underlying": underlying, "spot": spot, "candidates": [],
                "reason": "no expiries available"}

    chains: dict = {}
    for e, _ in in_range:
        try:
            if state.get_kite():
                chains[e] = build_real_chain(underlying, e, spot,
                                             below_atm=80, above_atm=3)
            else:
                chains[e] = opt_eng.build_option_chain(underlying, e, spot, None)
        except Exception as ex:
            print(f"rich-pe-scan: chain build failed for {e}: {ex}")

    if not chains:
        return {"underlying": underlying, "spot": spot, "candidates": []}

    ref_expiry = min(chains.keys(), key=lambda e: chains[e]["dte"])
    ref_chain  = chains[ref_expiry]["chain"]

    def _pe_iv_at_moneyness(chain, target_pct):
        target_strike = spot * (1 + target_pct / 100)
        row = min(chain, key=lambda r: abs(float(r["strike"]) - target_strike))
        return row["pe"].get("iv") or 0.0

    candidates = []
    for e, cd in chains.items():
        dte = cd["dte"]
        if dte <= 0:
            continue
        atm = cd["atm_strike"]
        atm_row = next((r for r in cd["chain"]
                        if abs(float(r["strike"]) - atm) < 0.5), None)
        atm_pe_iv = (atm_row["pe"].get("iv") if atm_row else None) or 0.0

        for row in cd["chain"]:
            K   = float(row["strike"])
            mny = (K - spot) / spot * 100.0   # negative for OTM puts
            if not (moneyness_min <= mny <= moneyness_max):
                continue
            pe  = row["pe"]
            price = pe.get("price") or 0.0
            iv    = pe.get("iv") or 0.0
            if price <= 0 or iv <= 0 or K <= 0:
                continue

            ref_iv     = _pe_iv_at_moneyness(ref_chain, mny)
            iv_vs_atm  = ((iv - atm_pe_iv) / atm_pe_iv) if atm_pe_iv > 0 else 0.0
            iv_vs_term = ((iv - ref_iv)    / ref_iv)    if ref_iv    > 0 else 0.0
            ann_yield  = (price / K) * (365.0 / dte)

            candidates.append({
                "expiry":        e,
                "dte":           dte,
                "strike":        K,
                "moneyness_pct": round(mny, 2),
                "pe": {
                    "price": round(float(price), 2),
                    "iv":    round(float(iv), 2),
                    "delta": pe.get("delta"),
                },
                "raw": {
                    "annualized_yield": ann_yield,
                    "prem_pct_spot":    price / spot,
                    "iv_vs_atm":        iv_vs_atm,
                    "iv_vs_term":       iv_vs_term,
                },
            })

    if len(candidates) < 2:
        return {"underlying": underlying, "spot": spot, "candidates": candidates}

    def _rank_higher_better(vals: list[float]) -> list[float]:
        n = len(vals)
        out = []
        for v in vals:
            below = sum(1 for x in vals if x < v)
            equal = sum(1 for x in vals if x == v)
            pct   = (below + 0.5 * equal) / n   # higher v → higher pct
            out.append(round(pct * 100, 1))
        return out

    keys = ["annualized_yield", "prem_pct_spot", "iv_vs_atm", "iv_vs_term"]
    rank_arrays = {k: _rank_higher_better([c["raw"][k] for c in candidates])
                   for k in keys}

    try:
        w = [float(x) for x in weights.split(",")]
        if len(w) != 4 or sum(w) <= 0:
            w = [25.0, 25.0, 25.0, 25.0]
    except ValueError:
        w = [25.0, 25.0, 25.0, 25.0]
    wsum = sum(w)
    w_norm = [x / wsum for x in w]

    for i, c in enumerate(candidates):
        c["ranks"] = {k: rank_arrays[k][i] for k in keys}
        c["composite_score"] = round(
            sum(c["ranks"][k] * w_norm[j] for j, k in enumerate(keys)), 2
        )
        c["raw"] = {k: round(v, 6) for k, v in c["raw"].items()}

    candidates.sort(key=lambda c: c["composite_score"], reverse=True)
    for i, c in enumerate(candidates):
        c["rank"] = i + 1

    out = {
        "underlying":           underlying,
        "spot":                 spot,
        "expiries_scanned":     sorted(chains.keys()),
        "reference_expiry":     ref_expiry,
        "candidates_evaluated": len(candidates),
        "weights":              dict(zip(keys, w_norm)),
        "candidates":           candidates[:top_n],
    }
    if fallback_notice:
        out["notice"] = fallback_notice
    return out


@router.post("/strategy-pnl")
def strategy_pnl(req: StrategyPnlRequest):
    step      = (req.spot_to - req.spot_from) / max(req.steps - 1, 1)
    spot_range = [req.spot_from + i * step for i in range(req.steps)]
    legs      = [l.model_dump() for l in req.legs]
    return opt_eng.strategy_pnl(legs, spot_range)
