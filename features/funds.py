from kiteconnect import KiteConnect
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box

console = Console()


def print_margins(kite: KiteConnect):
    margins = kite.margins()

    for segment in ("equity", "commodity"):
        m = margins.get(segment)
        if not m:
            continue

        table = Table(title=f"{segment.upper()} Margins", box=box.SIMPLE_HEAVY)
        table.add_column("Field", style="bold")
        table.add_column("Value", justify="right", style="cyan")

        net = m.get("net", 0)
        available = m.get("available", {})
        utilised = m.get("utilised", {})

        table.add_row("Net Available", f"₹{net:,.2f}")
        table.add_row("Cash Balance", f"₹{available.get('cash', 0):,.2f}")
        table.add_row("Opening Balance", f"₹{available.get('opening_balance', 0):,.2f}")
        table.add_row("Intraday Payin", f"₹{available.get('intraday_payin', 0):,.2f}")
        table.add_row("Live Balance", f"₹{available.get('live_balance', 0):,.2f}")
        table.add_row("Collateral", f"₹{available.get('collateral', 0):,.2f}")
        table.add_row("─" * 20, "─" * 12)
        table.add_row("Debits (Used)", f"₹{utilised.get('debits', 0):,.2f}")
        table.add_row("Span Margin", f"₹{utilised.get('span', 0):,.2f}")
        table.add_row("Option Premium", f"₹{utilised.get('option_premium', 0):,.2f}")
        table.add_row("Holding Sales", f"₹{utilised.get('holding_sales', 0):,.2f}")
        table.add_row("Turnover Charges", f"₹{utilised.get('turnover', 0):,.2f}")
        table.add_row("Exposure Margin", f"₹{utilised.get('exposure', 0):,.2f}")
        table.add_row("Delivery Margin", f"₹{utilised.get('delivery', 0):,.2f}")

        console.print(table)
        console.print()
