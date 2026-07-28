from kiteconnect import KiteConnect
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.prompt import Prompt
from rich import box

console = Console()


def _parse_instruments(raw: str) -> list[str]:
    return [s.strip().upper() for s in raw.split(",") if s.strip()]


def print_ltp(kite: KiteConnect):
    raw = Prompt.ask("Instruments (e.g. NSE:INFY,NSE:RELIANCE,NFO:NIFTY24JANFUT)")
    instruments = _parse_instruments(raw)
    try:
        data = kite.ltp(instruments)
    except Exception as e:
        console.print(f"[red]{e}[/]")
        return

    table = Table(title="Last Traded Price", box=box.ROUNDED)
    table.add_column("Instrument", style="bold cyan")
    table.add_column("LTP", justify="right", style="bold")
    table.add_column("Instrument Token", justify="right", style="dim")

    for inst, v in data.items():
        table.add_row(inst, f"₹{v['last_price']:.2f}", str(v["instrument_token"]))
    console.print(table)


def print_ohlc(kite: KiteConnect):
    raw = Prompt.ask("Instruments (e.g. NSE:INFY,BSE:SENSEX)")
    instruments = _parse_instruments(raw)
    try:
        data = kite.ohlc(instruments)
    except Exception as e:
        console.print(f"[red]{e}[/]")
        return

    table = Table(title="OHLC Quote", box=box.ROUNDED)
    table.add_column("Instrument", style="bold cyan")
    table.add_column("Open", justify="right")
    table.add_column("High", justify="right")
    table.add_column("Low", justify="right")
    table.add_column("Close (Prev)", justify="right")
    table.add_column("LTP", justify="right", style="bold")

    for inst, v in data.items():
        ohlc = v["ohlc"]
        table.add_row(
            inst,
            f"₹{ohlc['open']:.2f}",
            f"₹{ohlc['high']:.2f}",
            f"₹{ohlc['low']:.2f}",
            f"₹{ohlc['close']:.2f}",
            f"₹{v['last_price']:.2f}",
        )
    console.print(table)


def print_full_quote(kite: KiteConnect):
    raw = Prompt.ask("Instruments (e.g. NSE:INFY)")
    instruments = _parse_instruments(raw)
    try:
        data = kite.quote(instruments)
    except Exception as e:
        console.print(f"[red]{e}[/]")
        return

    for inst, q in data.items():
        ohlc = q.get("ohlc", {})
        depth = q.get("depth", {})
        buy_orders = depth.get("buy", [])
        sell_orders = depth.get("sell", [])

        info = (
            f"[bold cyan]{inst}[/]\n"
            f"LTP: [bold]₹{q['last_price']:.2f}[/]  |  "
            f"Change: {q.get('net_change', 0):+.2f}  |  "
            f"Volume: {q.get('volume', 0):,}  |  OI: {q.get('oi', 0):,}\n"
            f"Open: {ohlc.get('open',0):.2f}  High: {ohlc.get('high',0):.2f}  "
            f"Low: {ohlc.get('low',0):.2f}  Close(Prev): {ohlc.get('close',0):.2f}\n"
            f"52W High: {q.get('upper_circuit_limit',0):.2f}  "
            f"52W Low: {q.get('lower_circuit_limit',0):.2f}"
        )
        console.print(Panel(info, title="Quote"))

        depth_table = Table(title="Market Depth (Order Book)", box=box.SIMPLE)
        depth_table.add_column("Buy Qty", justify="right", style="green")
        depth_table.add_column("Buy Price", justify="right", style="green")
        depth_table.add_column("Sell Price", justify="right", style="red")
        depth_table.add_column("Sell Qty", justify="right", style="red")

        for i in range(5):
            b = buy_orders[i] if i < len(buy_orders) else {}
            s = sell_orders[i] if i < len(sell_orders) else {}
            depth_table.add_row(
                str(b.get("quantity", "")),
                f"₹{b['price']:.2f}" if b.get("price") else "",
                f"₹{s['price']:.2f}" if s.get("price") else "",
                str(s.get("quantity", "")),
            )
        console.print(depth_table)
