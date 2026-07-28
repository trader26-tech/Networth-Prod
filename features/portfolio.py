from kiteconnect import KiteConnect
from rich.console import Console
from rich.table import Table
from rich import box

console = Console()


def print_holdings(kite: KiteConnect):
    holdings = kite.holdings()
    if not holdings:
        console.print("[yellow]No holdings found.[/]")
        return

    table = Table(title="Equity Holdings", box=box.ROUNDED, show_footer=True)
    table.add_column("Symbol", style="bold cyan")
    table.add_column("Exch")
    table.add_column("Qty", justify="right")
    table.add_column("Avg Price", justify="right")
    table.add_column("LTP", justify="right")
    table.add_column("Invested", justify="right")
    table.add_column("Current", justify="right")
    table.add_column("P&L", justify="right")
    table.add_column("P&L %", justify="right")

    total_invested = total_current = 0.0
    for h in holdings:
        qty = h["quantity"]
        avg = h["average_price"]
        ltp = h["last_price"]
        invested = avg * qty
        current = ltp * qty
        pnl = current - invested
        pnl_pct = (pnl / invested * 100) if invested else 0.0
        total_invested += invested
        total_current += current

        color = "green" if pnl >= 0 else "red"
        table.add_row(
            h["tradingsymbol"],
            h["exchange"],
            str(qty),
            f"₹{avg:.2f}",
            f"₹{ltp:.2f}",
            f"₹{invested:,.2f}",
            f"₹{current:,.2f}",
            f"[{color}]{pnl:+,.2f}[/{color}]",
            f"[{color}]{pnl_pct:+.2f}%[/{color}]",
        )

    console.print(table)
    total_pnl = total_current - total_invested
    color = "green" if total_pnl >= 0 else "red"
    console.print(
        f"\n  Invested: [bold]₹{total_invested:,.2f}[/]  |  "
        f"Current: [bold]₹{total_current:,.2f}[/]  |  "
        f"P&L: [bold {color}]{total_pnl:+,.2f} ({total_pnl/total_invested*100:+.2f}%)[/]"
    )


def print_positions(kite: KiteConnect):
    positions = kite.positions()
    net = [p for p in positions.get("net", []) if p["quantity"] != 0]

    if not net:
        console.print("[yellow]No open positions.[/]")
        return

    table = Table(title="Open Positions", box=box.ROUNDED)
    table.add_column("Symbol", style="bold cyan")
    table.add_column("Exch")
    table.add_column("Product")
    table.add_column("Qty", justify="right")
    table.add_column("Avg", justify="right")
    table.add_column("LTP", justify="right")
    table.add_column("P&L", justify="right")
    table.add_column("Unrealised", justify="right")
    table.add_column("Realised", justify="right")

    for p in net:
        pnl = p.get("pnl", 0)
        color = "green" if pnl >= 0 else "red"
        table.add_row(
            p["tradingsymbol"],
            p["exchange"],
            p["product"],
            str(p["quantity"]),
            f"₹{p['average_price']:.2f}",
            f"₹{p['last_price']:.2f}",
            f"[{color}]{pnl:+,.2f}[/{color}]",
            f"₹{p.get('unrealised', 0):,.2f}",
            f"₹{p.get('realised', 0):,.2f}",
        )
    console.print(table)
