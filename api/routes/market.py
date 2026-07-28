"""Market data routes."""
from fastapi import APIRouter
from api import mock_market, state

router = APIRouter(prefix="/api/market", tags=["market"])


@router.get("/summary")
def market_summary():
    kite = state.get_kite()
    if kite:
        try:
            keys = ["NSE:NIFTY 50", "NSE:NIFTY BANK"]
            data = kite.ltp(keys)
            n    = data.get("NSE:NIFTY 50",   {}).get("last_price", 22800)
            b    = data.get("NSE:NIFTY BANK",  {}).get("last_price", 48000)
            return {
                "nifty50":   {"price": n, "change": 0, "change_pct": 0},
                "banknifty": {"price": b, "change": 0, "change_pct": 0},
                "sensex":    {"price": 75200, "change": 0, "change_pct": 0},
            }
        except Exception:
            pass
    return mock_market.get_market_summary()


@router.get("/instruments")
def get_instruments(query: str = ""):
    kite = state.get_kite()
    if query:
        if kite:
            try:
                r = kite.instruments("NSE")
                q = query.upper()
                return [i for i in r if q in i.get("tradingsymbol", "")][:20]
            except Exception:
                pass
        return mock_market.search_instruments(query)
    return mock_market.get_all_instruments()


@router.get("/ltp")
def get_ltp(symbols: str):
    sym_list = [s.strip() for s in symbols.split(",")]
    kite     = state.get_kite()
    if kite:
        try:
            keys = [f"NSE:{s}" if ":" not in s else s for s in sym_list]
            data = kite.ltp(keys)
            return {k.split(":")[-1]: v for k, v in data.items()}
        except Exception:
            pass
    return mock_market.get_ltp(sym_list)


@router.get("/ohlc")
def get_ohlc(symbols: str):
    sym_list = [s.strip() for s in symbols.split(",")]
    kite     = state.get_kite()
    if kite:
        try:
            keys = [f"NSE:{s}" if ":" not in s else s for s in sym_list]
            data = kite.ohlc(keys)
            return {k.split(":")[-1]: v for k, v in data.items()}
        except Exception:
            pass
    return mock_market.get_ohlc(sym_list)


@router.get("/quote")
def get_quote(symbols: str):
    sym_list = [s.strip() for s in symbols.split(",")]
    kite     = state.get_kite()
    if kite:
        try:
            keys = [f"NSE:{s}" if ":" not in s else s for s in sym_list]
            data = kite.quote(keys)
            return {k.split(":")[-1]: v for k, v in data.items()}
        except Exception:
            pass
    return mock_market.get_full_quote(sym_list)


@router.get("/historical")
def get_historical(symbol: str, exchange: str = "NSE", interval: str = "day", days: int = 30):
    kite = state.get_kite()
    if kite:
        try:
            from datetime import datetime, timedelta
            instruments = kite.instruments(exchange)
            token = next(
                (i["instrument_token"] for i in instruments
                 if i["tradingsymbol"] == symbol.upper()), None
            )
            if token:
                to_dt   = datetime.now()
                from_dt = to_dt - timedelta(days=days)
                candles = kite.historical_data(
                    token, from_dt.strftime("%Y-%m-%d"), to_dt.strftime("%Y-%m-%d"), interval
                )
                return [{"date": str(c["date"])[:19], "open": c["open"], "high": c["high"],
                         "low": c["low"], "close": c["close"], "volume": c.get("volume", 0)}
                        for c in candles]
        except Exception:
            pass
    return mock_market.get_historical(symbol, interval, days)
