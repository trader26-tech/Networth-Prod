import threading
from kiteconnect import KiteConnect, KiteTicker
from rich.console import Console
from rich.prompt import Prompt
from rich.live import Live
from rich.table import Table
from rich import box
import os

console = Console()

_price_store: dict[int, dict] = {}
_token_name_map: dict[int, str] = {}


def _on_ticks(ws, ticks):
    for tick in ticks:
        _price_store[tick["instrument_token"]] = tick


def _on_connect(ws, response):
    tokens = list(_token_name_map.keys())
    ws.subscribe(tokens)
    ws.set_mode(ws.MODE_FULL, tokens)
    console.print(f"[green]✓ Subscribed to {len(tokens)} instruments.[/]")


def _on_error(ws, code, reason):
    console.print(f"[red]Ticker error {code}: {reason}[/]")


def _on_close(ws, code, reason):
    console.print(f"[yellow]Ticker closed: {reason}[/]")


def _build_table() -> Table:
    table = Table(title="Live Ticker (CTRL+C to stop)", box=box.ROUNDED, expand=False)
    table.add_column("Instrument", style="bold cyan", min_width=20)
    table.add_column("LTP", justify="right", style="bold")
    table.add_column("Change", justify="right")
    table.add_column("Volume", justify="right")
    table.add_column("OI", justify="right")
    table.add_column("Buy Qty", justify="right", style="green")
    table.add_column("Sell Qty", justify="right", style="red")

    for token, name in _token_name_map.items():
        tick = _price_store.get(token, {})
        ltp = tick.get("last_price", 0)
        change = tick.get("net_change", 0)
        color = "green" if change >= 0 else "red"
        depth = tick.get("depth", {})
        buy_qty = sum(d.get("quantity", 0) for d in depth.get("buy", []))
        sell_qty = sum(d.get("quantity", 0) for d in depth.get("sell", []))
        table.add_row(
            name,
            f"₹{ltp:.2f}" if ltp else "—",
            f"[{color}]{change:+.2f}[/{color}]" if ltp else "—",
            f"{tick.get('volume', 0):,}",
            f"{tick.get('oi', 0):,}",
            f"{buy_qty:,}",
            f"{sell_qty:,}",
        )
    return table


def start_ticker(kite: KiteConnect):
    console.print("\n[bold cyan]Live WebSocket Ticker[/]")
    console.print("Enter instrument tokens (comma-separated). Use the Instrument Search to find tokens.\n")

    raw = Prompt.ask("Instrument tokens (e.g. 738561,256265 for INFY,NIFTY50)")
    tokens = [int(t.strip()) for t in raw.split(",") if t.strip().isdigit()]
    if not tokens:
        console.print("[red]No valid tokens entered.[/]")
        return

    names_raw = Prompt.ask("Labels for display (comma-separated, same order)", default="")
    names = [n.strip() for n in names_raw.split(",")] if names_raw else []
    for i, token in enumerate(tokens):
        _token_name_map[token] = names[i] if i < len(names) else str(token)

    api_key = os.getenv("KITE_API_KEY", "")
    access_token = os.getenv("KITE_ACCESS_TOKEN", "")

    kt = KiteTicker(api_key, access_token)
    kt.on_ticks = _on_ticks
    kt.on_connect = _on_connect
    kt.on_error = _on_error
    kt.on_close = _on_close

    t = threading.Thread(target=kt.connect, kwargs={"threaded": True}, daemon=True)
    t.start()

    console.print("[dim]Connecting... press CTRL+C to stop streaming.[/]")
    try:
        with Live(console=console, refresh_per_second=2) as live:
            while True:
                live.update(_build_table())
    except KeyboardInterrupt:
        console.print("\n[yellow]Ticker stopped.[/]")
        kt.close()

    _token_name_map.clear()
    _price_store.clear()
