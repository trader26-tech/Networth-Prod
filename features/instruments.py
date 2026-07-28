import csv
import io
from kiteconnect import KiteConnect
from rich.console import Console
from rich.table import Table
from rich.prompt import Prompt
from rich import box

console = Console()

EXCHANGES = ["NSE", "BSE", "NFO", "BFO", "CDS", "MCX", "BCD"]


def search_instrument(kite: KiteConnect):
    console.print("\n[bold cyan]Instrument Search[/]")
    exchange = Prompt.ask("Exchange", choices=EXCHANGES, default="NSE")
    query = Prompt.ask("Search symbol / name (partial match OK)").upper()

    console.print(f"[dim]Fetching instruments for {exchange}...[/]")
    try:
        instruments = kite.instruments(exchange)
    except Exception as e:
        console.print(f"[red]{e}[/]")
        return

    results = [i for i in instruments if query in i["tradingsymbol"].upper() or query in i["name"].upper()]

    if not results:
        console.print(f"[yellow]No instruments matching '{query}' on {exchange}.[/]")
        return

    table = Table(title=f"Results for '{query}' on {exchange}", box=box.ROUNDED)
    table.add_column("Token", justify="right", style="dim")
    table.add_column("Symbol", style="bold cyan")
    table.add_column("Name")
    table.add_column("Segment")
    table.add_column("Expiry")
    table.add_column("Strike", justify="right")
    table.add_column("Type")
    table.add_column("Lot Size", justify="right")
    table.add_column("Tick", justify="right")

    for i in results[:50]:
        table.add_row(
            str(i["instrument_token"]),
            i["tradingsymbol"],
            i.get("name", ""),
            i.get("segment", ""),
            str(i.get("expiry", "") or ""),
            str(i.get("strike", "") or ""),
            i.get("instrument_type", ""),
            str(i.get("lot_size", "")),
            str(i.get("tick_size", "")),
        )
    console.print(table)
    if len(results) > 50:
        console.print(f"[dim]Showing 50 of {len(results)} results. Refine your query.[/]")


def download_instruments(kite: KiteConnect):
    console.print("\n[bold cyan]Download Instrument List[/]")
    exchange = Prompt.ask("Exchange (leave blank for ALL)", default="")

    console.print("[dim]Downloading...[/]")
    try:
        instruments = kite.instruments(exchange or None)
    except Exception as e:
        console.print(f"[red]{e}[/]")
        return

    filename = f"instruments_{exchange or 'ALL'}.csv"
    if instruments:
        keys = instruments[0].keys()
        with open(filename, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=keys)
            writer.writeheader()
            writer.writerows(instruments)
        console.print(f"[green]✓ Saved {len(instruments):,} instruments to [bold]{filename}[/][/]")
    else:
        console.print("[yellow]No instruments returned.[/]")


def get_token_for_symbol(kite: KiteConnect):
    console.print("\n[bold cyan]Get Instrument Token[/]")
    exchange = Prompt.ask("Exchange", default="NSE")
    symbol = Prompt.ask("Exact symbol").upper()

    try:
        instruments = kite.instruments(exchange)
    except Exception as e:
        console.print(f"[red]{e}[/]")
        return

    for i in instruments:
        if i["tradingsymbol"] == symbol:
            console.print(f"\n[green]Token for [bold]{exchange}:{symbol}[/]: [bold cyan]{i['instrument_token']}[/][/]")
            return
    console.print(f"[red]{symbol} not found on {exchange}.[/]")
