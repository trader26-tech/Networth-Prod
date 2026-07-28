"""
Mock market data — realistic prices that drift over time.
Used when no real Kite credentials are configured.
"""
import math, time, random

# Realistic Indian stock prices (as of early 2025)
BASE_PRICES = {
    "RELIANCE": 2850.0, "TCS": 3900.0, "INFY": 1780.0, "WIPRO": 480.0,
    "HDFCBANK": 1620.0, "ICICIBANK": 1120.0, "SBIN": 760.0, "AXISBANK": 1050.0,
    "KOTAKBANK": 1780.0, "BAJFINANCE": 6900.0, "BAJAJFINSV": 1560.0,
    "HINDUNILVR": 2340.0, "ITC": 465.0, "NESTLEIND": 2250.0, "BRITANNIA": 5100.0,
    "ASIANPAINT": 2700.0, "TITAN": 3550.0, "MARUTI": 12500.0, "M&M": 2950.0,
    "TATAMOTORS": 870.0, "HEROMOTOCO": 4500.0, "BAJAJ-AUTO": 9200.0,
    "SUNPHARMA": 1680.0, "DRREDDY": 6300.0, "CIPLA": 1490.0, "DIVISLAB": 5100.0,
    "LTIM": 5700.0, "HCLTECH": 1890.0, "TECHM": 1590.0, "PERSISTENT": 5800.0,
    "TATACONSUM": 1000.0, "DABUR": 555.0, "MARICO": 615.0,
    "ONGC": 265.0, "NTPC": 355.0, "POWERGRID": 305.0, "COALINDIA": 455.0,
    "HINDALCO": 655.0, "JSWSTEEL": 895.0, "TATASTEEL": 165.0, "SAIL": 135.0,
    "ADANIPORTS": 1280.0, "ADANIENT": 2650.0,
    "NIFTY 50": 22800.0, "BANKNIFTY": 48000.0, "SENSEX": 75200.0,
}

SECTORS = {
    "IT": ["TCS", "INFY", "WIPRO", "LTIM", "HCLTECH", "TECHM", "PERSISTENT"],
    "Banking": ["HDFCBANK", "ICICIBANK", "SBIN", "AXISBANK", "KOTAKBANK"],
    "FMCG": ["HINDUNILVR", "ITC", "NESTLEIND", "BRITANNIA", "DABUR", "MARICO", "TATACONSUM"],
    "Auto": ["MARUTI", "M&M", "TATAMOTORS", "HEROMOTOCO", "BAJAJ-AUTO"],
    "Pharma": ["SUNPHARMA", "DRREDDY", "CIPLA", "DIVISLAB"],
    "Finance": ["BAJFINANCE", "BAJAJFINSV", "RELIANCE"],
    "Metals": ["HINDALCO", "JSWSTEEL", "TATASTEEL", "SAIL"],
    "Energy": ["ONGC", "NTPC", "POWERGRID", "COALINDIA"],
}

_cache: dict[str, dict] = {}
_t0 = time.time()


def _drift(symbol: str) -> float:
    """Sine + random walk for realistic price movement."""
    t = time.time() - _t0
    seed = abs(hash(symbol)) % 997
    random.seed(int(t / 5) + seed)   # new random every 5 s
    base = BASE_PRICES.get(symbol, abs(hash(symbol)) % 4000 + 100)
    wave = math.sin(t / 60 + seed) * base * 0.003
    noise = random.gauss(0, base * 0.001)
    if symbol not in _cache:
        _cache[symbol] = {"price": base, "open": base}
    old = _cache[symbol]["price"]
    new_price = max(1.0, old + wave + noise)
    _cache[symbol]["price"] = new_price
    return round(new_price, 2)


def get_ltp(symbols: list[str]) -> dict:
    return {s: {"last_price": _drift(s)} for s in symbols}


def get_ohlc(symbols: list[str]) -> dict:
    result = {}
    for s in symbols:
        ltp = _drift(s)
        base = _cache[s]["open"]
        result[s] = {
            "last_price": ltp,
            "ohlc": {
                "open": round(base, 2),
                "high": round(max(base, ltp) * 1.005, 2),
                "low": round(min(base, ltp) * 0.995, 2),
                "close": round(base * 0.999, 2),
            }
        }
    return result


def get_full_quote(symbols: list[str]) -> dict:
    result = {}
    for s in symbols:
        ltp = _drift(s)
        base = _cache[s]["open"] if s in _cache else ltp
        change = ltp - base
        result[s] = {
            "last_price": ltp,
            "volume": random.randint(100_000, 5_000_000),
            "average_price": round((base + ltp) / 2, 2),
            "buy_quantity": random.randint(1000, 50000),
            "sell_quantity": random.randint(1000, 50000),
            "ohlc": {
                "open": round(base, 2),
                "high": round(max(base, ltp) * 1.006, 2),
                "low": round(min(base, ltp) * 0.994, 2),
                "close": round(base * 0.999, 2),
            },
            "net_change": round(change, 2),
            "oi": 0,
            "depth": {
                "buy": [
                    {"price": round(ltp - i * 0.5, 2), "quantity": random.randint(100, 5000), "orders": random.randint(1, 20)}
                    for i in range(1, 6)
                ],
                "sell": [
                    {"price": round(ltp + i * 0.5, 2), "quantity": random.randint(100, 5000), "orders": random.randint(1, 20)}
                    for i in range(1, 6)
                ],
            }
        }
    return result


def get_historical(symbol: str, interval: str, days: int) -> list:
    base = BASE_PRICES.get(symbol, abs(hash(symbol)) % 4000 + 100)
    candles = []
    import datetime
    now = datetime.datetime.now()
    intervals_per_day = {"minute": 375, "5minute": 75, "15minute": 25, "30minute": 13,
                          "60minute": 6, "day": 1}
    n = intervals_per_day.get(interval, 1)
    total = days * n
    price = base * 0.8
    random.seed(abs(hash(symbol + interval)))
    for i in range(total):
        dt = now - datetime.timedelta(minutes=(total - i) * (375 // n if n > 1 else 1440))
        open_ = price
        high = price * (1 + random.uniform(0, 0.015))
        low = price * (1 - random.uniform(0, 0.015))
        close = random.uniform(low, high)
        vol = random.randint(100_000, 5_000_000)
        candles.append({"date": dt.strftime("%Y-%m-%dT%H:%M:%S"), "open": round(open_, 2),
                         "high": round(high, 2), "low": round(low, 2),
                         "close": round(close, 2), "volume": vol})
        price = close
    return candles


def search_instruments(query: str) -> list:
    q = query.upper()
    results = []
    for symbol, price in BASE_PRICES.items():
        if q in symbol:
            results.append({
                "tradingsymbol": symbol,
                "name": symbol,
                "exchange": "NSE",
                "instrument_type": "EQ",
                "last_price": round(_drift(symbol), 2),
            })
    return results[:20]


def get_all_instruments() -> list:
    instruments = []
    for symbol, price in BASE_PRICES.items():
        instruments.append({
            "tradingsymbol": symbol,
            "name": symbol,
            "exchange": "NSE",
            "instrument_type": "EQ",
            "last_price": round(price, 2),
            "sector": next((s for s, syms in SECTORS.items() if symbol in syms), "Other"),
        })
    return instruments


def get_market_summary() -> dict:
    nifty = _drift("NIFTY 50")
    banknifty = _drift("BANKNIFTY")
    sensex = _drift("SENSEX")
    nifty_base = BASE_PRICES["NIFTY 50"]
    return {
        "nifty50": {"price": nifty, "change": round(nifty - nifty_base, 2),
                    "change_pct": round((nifty - nifty_base) / nifty_base * 100, 2)},
        "banknifty": {"price": banknifty, "change": round(banknifty - BASE_PRICES["BANKNIFTY"], 2),
                      "change_pct": round((banknifty - BASE_PRICES["BANKNIFTY"]) / BASE_PRICES["BANKNIFTY"] * 100, 2)},
        "sensex": {"price": sensex, "change": round(sensex - BASE_PRICES["SENSEX"], 2),
                   "change_pct": round((sensex - BASE_PRICES["SENSEX"]) / BASE_PRICES["SENSEX"] * 100, 2)},
    }
