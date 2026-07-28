from kiteconnect import KiteConnect
from rich.console import Console
from rich.table import Table
from rich.prompt import Prompt, IntPrompt
from rich import box

console = Console()

EXCHANGES = ["NSE", "BSE", "NFO", "BFO", "CDS", "MCX"]
PRODUCTS = ["CNC", "MIS", "NRML"]
ORDER_TYPES = ["MARKET", "LIMIT", "SL", "SL-M"]
VARIETIES = ["regular", "amo", "co"]


def calculate_basket_margin(kite: KiteConnect):
    console.print("\n[bold cyan]Basket Order Margin Calculator[/]")
    console.print("Add orders to the basket (type 'done' when finished).\n")

    orders = []
    while True:
        exchange = Prompt.ask("Exchange (or 'done')", default="done")
        if exchange.lower() == "done":
            break

        symbol = Prompt.ask("Symbol").upper()
        txn = Prompt.ask("Transaction", choices=["BUY", "SELL"])
        variety = Prompt.ask("Variety", choices=VARIETIES, default="regular")
        product = Prompt.ask("Product", choices=PRODUCTS, default="NRML")
        order_type = Prompt.ask("Order Type", choices=ORDER_TYPES, default="MARKET")
        quantity = int(Prompt.ask("Quantity"))
        price = float(Prompt.ask("Price (0 for MARKET)", default="0"))
        trigger_price = float(Prompt.ask("Trigger Price (0 if none)", default="0"))

        orders.append({
            "exchange": exchange.upper(),
            "tradingsymbol": symbol,
            "transaction_type": txn,
            "variety": variety,
            "product": product,
            "order_type": order_type,
            "quantity": quantity,
            "price": price,
            "trigger_price": trigger_price,
        })
        console.print(f"[green]Added {txn} {quantity} {symbol}. Total orders: {len(orders)}[/]\n")

    if not orders:
        console.print("[yellow]No orders in basket.[/]")
        return

    try:
        result = kite.order_margins(orders)
    except Exception as e:
        console.print(f"[red]{e}[/]")
        return

    table = Table(title="Basket Margin Breakdown", box=box.ROUNDED)
    table.add_column("Symbol", style="bold cyan")
    table.add_column("Txn")
    table.add_column("Type")
    table.add_column("Total Margin", justify="right", style="bold")
    table.add_column("SPAN", justify="right")
    table.add_column("Exposure", justify="right")
    table.add_column("Option Premium", justify="right")
    table.add_column("Additional", justify="right")

    total = 0.0
    for m in result:
        total += m.get("total", 0)
        table.add_row(
            m["tradingsymbol"],
            m["type"],
            m.get("order_type", ""),
            f"₹{m.get('total', 0):,.2f}",
            f"₹{m.get('span', 0):,.2f}",
            f"₹{m.get('exposure', 0):,.2f}",
            f"₹{m.get('option_premium', 0):,.2f}",
            f"₹{m.get('additional', 0):,.2f}",
        )

    console.print(table)
    console.print(f"\n[bold]Total Margin Required: ₹{total:,.2f}[/]")
