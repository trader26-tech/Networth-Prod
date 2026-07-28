from datetime import datetime, timedelta
from kiteconnect import KiteConnect
from rich.console import Console
from rich.table import Table
from rich.prompt import Prompt
from rich import box

console = Console()

INTERVALS = ["minute", "3minute", "5minute", "10minute", "15minute", "30minute", "60minute", "day"]

# Max days Kite allows per interval
INTERVAL_MAX_DAYS = {
    "minute": 60, "3minute": 100, "5minute": 100,
    "10minute": 100, "15minute": 200, "30minute": 200,
    "60minute": 400, "day": 2000,
}


def _lookup_token(kite: KiteConnect, symbol: str, exchange: str) -> int:
    instruments = kite.instruments(exchange)
    symbol = symbol.upper()
    for inst in instruments:
        if inst["tradingsymbol"] == symbol:
            return inst["instrument_token"]
    raise ValueError(f"{symbol} not found on {exchange}")


def fetch_historical(
    kite: KiteConnect,
    instrument_token: int,
    interval: str,
    days: int,
    continuous: bool = False,
    oi: bool = False,
):
    to_dt = datetime.now()
    from_dt = to_dt - timedelta(days=days)
    data = kite.historical_data(
        instrument_token=instrument_token,
        from_date=from_dt.strftime("%Y-%m-%d %H:%M:%S"),
        to_date=to_dt.strftime("%Y-%m-%d %H:%M:%S"),
        interval=interval,
        continuous=continuous,
        oi=oi,
    )
    return data


def print_historical_interactive(kite: KiteConnect):
    console.print("\n[bold cyan]Historical Candles[/]")
    exchange = Prompt.ask("Exchange", default="NSE")
    symbol = Prompt.ask("Symbol (e.g. INFY, NIFTY24JANFUT)").upper()
    interval = Prompt.ask("Interval", choices=INTERVALS, default="day")
    max_days = INTERVAL_MAX_DAYS[interval]
    days = int(Prompt.ask(f"Days back (max {max_days})", default=str(min(30, max_days))))
    include_oi = interval != "day" and Prompt.ask("Include OI?", choices=["y", "n"], default="n") == "y"

    console.print(f"[dim]Looking up token for {symbol}:{exchange}...[/]")
    try:
        token = _lookup_token(kite, symbol, exchange)
    except ValueError as e:
        console.print(f"[red]{e}[/]")
        return

    try:
        candles = fetch_historical(kite, token, interval, days, oi=include_oi)
    except Exception as e:
        console.print(f"[red]API error: {e}[/]")
        return

    if not candles:
        console.print("[yellow]No data returned.[/]")
        return

    cols = ["Date", "Open", "High", "Low", "Close", "Volume"]
    if include_oi:
        cols.append("OI")

    table = Table(title=f"{symbol} | {interval} | last {days}d", box=box.MINIMAL_DOUBLE_HEAD)
    for c in cols:
        table.add_column(c, justify="right" if c != "Date" else "left")

    for row in candles[-50:]:  # show last 50 rows
        r = [
            str(row["date"])[:19],
            f"{row['open']:.2f}",
            f"{row['high']:.2f}",
            f"{row['low']:.2f}",
            f"{row['close']:.2f}",
            f"{row.get('volume', 0):,}",
        ]
        if include_oi:
            r.append(f"{row.get('oi', 0):,}")
        table.add_row(*r)

    console.print(table)
    console.print(f"[dim]Total candles: {len(candles)} | showing last 50[/]")
