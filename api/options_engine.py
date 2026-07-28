"""
Options engine: Black-Scholes pricing, Greeks, option chain generation.
No scipy dependency — uses math module only.
"""
import math
import random
import datetime
from typing import Optional

try:
    from zoneinfo import ZoneInfo
    IST = ZoneInfo("Asia/Kolkata")
except Exception:  # pragma: no cover — Python <3.9 fallback
    IST = None


def _ist_now() -> datetime.datetime:
    """Production servers (Railway) run in UTC. NSE expiry math + DTE math
    all key off the Indian calendar day, so we anchor on IST regardless of
    the host TZ. Returns a naive datetime in IST wall-clock to keep the rest
    of the module's arithmetic simple."""
    if IST is None:
        return datetime.datetime.now()
    return datetime.datetime.now(IST).replace(tzinfo=None)


def _ist_today() -> datetime.date:
    return _ist_now().date()


RISK_FREE_RATE = 0.065  # 6.5% RBI repo rate

# NFO lot sizes
LOT_SIZES = {
    "NIFTY": 75, "BANKNIFTY": 30, "FINNIFTY": 40,
    "MIDCPNIFTY": 75, "SENSEX": 10, "BANKEX": 15,
}

UNDERLYINGS = {
    "NIFTY":      {"exchange": "NSE", "symbol": "NIFTY 50",   "spot": 24500},
    "BANKNIFTY":  {"exchange": "NSE", "symbol": "NIFTY BANK", "spot": 52000},
    "FINNIFTY":   {"exchange": "NSE", "symbol": "NIFTY FIN SERVICE", "spot": 23500},
    "MIDCPNIFTY": {"exchange": "NSE", "symbol": "NIFTY MIDCAP SELECT", "spot": 11800},
}


# ── Normal distribution helpers (no scipy) ───────────────────────────────────

def _norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))

def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


# ── Black-Scholes ─────────────────────────────────────────────────────────────

def black_scholes(S: float, K: float, T: float, r: float, sigma: float, opt_type: str) -> dict:
    """
    S = spot price, K = strike, T = years to expiry,
    r = risk-free rate, sigma = IV (decimal), opt_type = 'CE' | 'PE'
    Returns: price, delta, gamma, theta, vega, iv
    """
    if T <= 0:
        intrinsic = max(S - K, 0) if opt_type == 'CE' else max(K - S, 0)
        return {"price": intrinsic, "delta": (1.0 if opt_type == 'CE' else -1.0) if intrinsic > 0 else 0.0,
                "gamma": 0.0, "theta": 0.0, "vega": 0.0, "iv": sigma}

    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)

    if opt_type == 'CE':
        price = S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)
        delta = _norm_cdf(d1)
        theta = ((-S * _norm_pdf(d1) * sigma / (2 * math.sqrt(T)))
                 - r * K * math.exp(-r * T) * _norm_cdf(d2)) / 365
    else:
        price = K * math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)
        delta = _norm_cdf(d1) - 1
        theta = ((-S * _norm_pdf(d1) * sigma / (2 * math.sqrt(T)))
                 + r * K * math.exp(-r * T) * _norm_cdf(-d2)) / 365

    gamma = _norm_pdf(d1) / (S * sigma * math.sqrt(T))
    vega = S * _norm_pdf(d1) * math.sqrt(T) / 100  # per 1% IV move

    return {
        "price": round(max(price, 0.05), 2),
        "delta": round(delta, 4),
        "gamma": round(gamma, 6),
        "theta": round(theta, 4),
        "vega": round(vega, 4),
        "iv": round(sigma * 100, 2),  # return as %
    }


def implied_volatility(S: float, K: float, T: float, r: float, market_price: float, opt_type: str) -> float:
    """Newton-Raphson IV solver."""
    if T <= 0 or market_price <= 0:
        return 0.0
    sigma = 0.25
    for _ in range(50):
        bs = black_scholes(S, K, T, r, sigma, opt_type)
        price = bs["price"]
        vega = bs["vega"] * 100  # convert back to full vega
        diff = price - market_price
        if abs(diff) < 0.001:
            break
        if abs(vega) < 1e-10:
            break
        sigma -= diff / vega
        sigma = max(0.001, min(sigma, 5.0))
    return round(sigma * 100, 2)


# Correct weekly expiry weekday per underlying (NSE rules as of 2025)
# NIFTY 50: Tuesday (changed from Thursday after NSE rescheduled weekly expirations)
EXPIRY_WEEKDAY = {
    "NIFTY":      1,  # Tuesday (NSE changed from Thursday)
    "BANKNIFTY":  2,  # Wednesday
    "FINNIFTY":   1,  # Tuesday
    "MIDCPNIFTY": 0,  # Monday
    "SENSEX":     1,  # Tuesday
    "BANKEX":     3,  # Thursday
}


def get_nfo_expiries(underlying: str) -> list[str]:
    """Return next 8 expiry dates using the correct weekday for each underlying.
    This is the mock/offline fallback. When Kite is connected, real dates come
    from the NFO instrument list via _get_expiries() in options.py.
    """
    today = _ist_today()
    weekday = EXPIRY_WEEKDAY.get(underlying.upper(), 3)  # default Thursday
    expiries: list[str] = []
    d = today
    while len(expiries) < 8:
        d += datetime.timedelta(days=1)
        if d.weekday() == weekday:
            expiries.append(d.isoformat())
    return expiries


def days_to_expiry(expiry_str: str) -> float:
    """
    Time to expiry as fraction of year.
    On expiry day, returns fractional time until 3:30 PM IST market close
    (so options expiring today still have a non-zero T for greek/PoP calculations).
    """
    expiry_date = datetime.date.fromisoformat(expiry_str)
    now = _ist_now()
    today = now.date()

    if expiry_date < today:
        return 0.0

    if expiry_date == today:
        # NSE F&O closes at 15:30 IST on expiry day
        market_close = datetime.datetime.combine(expiry_date, datetime.time(15, 30))
        if now >= market_close:
            return 0.0
        seconds_left = (market_close - now).total_seconds()
        return seconds_left / (365.0 * 24.0 * 3600.0)

    # Future expiry: full days
    return (expiry_date - today).days / 365.0


# ── Mock IV smile ─────────────────────────────────────────────────────────────

def _mock_iv(moneyness: float, base_iv: float = 0.18) -> float:
    """Volatility smile: higher IV for deep OTM options."""
    skew = 0.05 * moneyness ** 2 + 0.02 * max(-moneyness, 0)
    return max(0.08, base_iv + skew)


# ── Option chain builder ───────────────────────────────────────────────────────

def build_option_chain(underlying: str, expiry: str, spot: float, kite=None) -> dict:
    """
    Returns a dict with strikes list where each item has:
    strike, ce (price, greeks, oi, volume), pe (price, greeks, oi, volume)
    """
    T = days_to_expiry(expiry)
    r = RISK_FREE_RATE
    lot_size = LOT_SIZES.get(underlying, 50)

    # Asymmetric window: 3 strikes below ATM + 50 strikes above ATM.
    # Matches the live-chain window so the Best-Trade scan can score
    # far-OTM calls all the way to ~+10% above spot.
    atm = round(spot / _strike_step(underlying)) * _strike_step(underlying)
    step = _strike_step(underlying)
    strikes = [atm + i * step for i in range(-3, 51)]

    chain = []
    for K in strikes:
        moneyness = (spot - K) / spot
        ce_iv = _mock_iv(-moneyness)   # OTM calls: spot < K
        pe_iv = _mock_iv(moneyness)    # OTM puts: spot > K

        if kite:
            # Try to fetch real LTP from NFO
            ce_sym = _option_symbol(underlying, expiry, K, "CE")
            pe_sym = _option_symbol(underlying, expiry, K, "PE")
            ce_price = _fetch_ltp(kite, ce_sym) or black_scholes(spot, K, T, r, ce_iv, "CE")["price"]
            pe_price = _fetch_ltp(kite, pe_sym) or black_scholes(spot, K, T, r, pe_iv, "PE")["price"]
            ce_bs = black_scholes(spot, K, T, r, ce_iv, "CE")
            pe_bs = black_scholes(spot, K, T, r, pe_iv, "PE")
            ce_bs["price"] = ce_price
            pe_bs["price"] = pe_price
        else:
            ce_bs = black_scholes(spot, K, T, r, ce_iv, "CE")
            pe_bs = black_scholes(spot, K, T, r, pe_iv, "PE")

        # Mock OI (higher near ATM)
        dist_factor = max(0, 1 - abs(K - atm) / (step * 8))
        base_oi = int(dist_factor * 500000 * random.uniform(0.7, 1.3))

        chain.append({
            "strike": K,
            "atm": K == atm,
            "ce": {
                "price": ce_bs["price"],
                "delta": ce_bs["delta"],
                "gamma": ce_bs["gamma"],
                "theta": ce_bs["theta"],
                "vega": ce_bs["vega"],
                "iv": ce_bs["iv"],
                "oi": base_oi + random.randint(-50000, 50000),
                "volume": int(base_oi * 0.15 * random.uniform(0.5, 1.5)),
                "symbol": _option_symbol(underlying, expiry, K, "CE"),
            },
            "pe": {
                "price": pe_bs["price"],
                "delta": pe_bs["delta"],
                "gamma": pe_bs["gamma"],
                "theta": pe_bs["theta"],
                "vega": pe_bs["vega"],
                "iv": pe_bs["iv"],
                "oi": base_oi + random.randint(-50000, 50000),
                "volume": int(base_oi * 0.15 * random.uniform(0.5, 1.5)),
                "symbol": _option_symbol(underlying, expiry, K, "PE"),
            },
        })

    return {
        "underlying": underlying,
        "spot": spot,
        "expiry": expiry,
        "dte": round(T * 365),
        "lot_size": lot_size,
        "atm_strike": atm,
        "chain": chain,
    }


def _strike_step(underlying: str) -> int:
    steps = {"NIFTY": 50, "BANKNIFTY": 100, "FINNIFTY": 50, "MIDCPNIFTY": 25, "SENSEX": 100}
    return steps.get(underlying, 50)


def _option_symbol(underlying: str, expiry: str, strike: int, opt_type: str) -> str:
    """Build NFO tradingsymbol like NIFTY24APR25000CE"""
    d = datetime.date.fromisoformat(expiry)
    month_abbr = d.strftime("%b").upper()
    year_short = d.strftime("%y")
    return f"{underlying}{year_short}{month_abbr}{int(strike)}{opt_type}"


def _fetch_ltp(kite, symbol: str) -> Optional[float]:
    try:
        data = kite.ltp([f"NFO:{symbol}"])
        return data.get(f"NFO:{symbol}", {}).get("last_price")
    except Exception:
        return None


# ── Strategy P&L calculator ───────────────────────────────────────────────────

def strategy_pnl(legs: list[dict], spot_range: list[float]) -> list[dict]:
    """
    legs: [{type, strike, premium, qty, lot_size, transaction_type}]
    spot_range: list of spot prices to evaluate
    Returns: [{spot, pnl}]
    """
    result = []
    for spot in spot_range:
        total_pnl = 0.0
        for leg in legs:
            K = leg["strike"]
            opt_type = leg["type"]  # CE or PE
            premium = leg["premium"]
            qty = leg["qty"] * leg.get("lot_size", 1)
            is_buy = leg["transaction_type"].upper() == "BUY"

            if opt_type == "CE":
                intrinsic = max(spot - K, 0)
            else:
                intrinsic = max(K - spot, 0)

            if is_buy:
                pnl = (intrinsic - premium) * qty
            else:
                pnl = (premium - intrinsic) * qty

            total_pnl += pnl

        result.append({"spot": round(spot, 2), "pnl": round(total_pnl, 2)})
    return result
