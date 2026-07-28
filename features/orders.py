from kiteconnect import KiteConnect
from rich.console import Console
from rich.table import Table
from rich.prompt import Prompt, Confirm
from rich import box

console = Console()

VARIETIES = ["regular", "amo", "co", "iceberg"]
EXCHANGES = ["NSE", "BSE", "NFO", "BFO", "CDS", "MCX", "BCD"]
PRODUCTS = ["CNC", "MIS", "NRML"]
ORDER_TYPES = ["MARKET", "LIMIT", "SL", "SL-M"]
TRANSACTION_TYPES = ["BUY", "SELL"]
VALIDITIES = ["DAY", "IOC", "TTL"]


def print_order_book(kite: KiteConnect):
    orders = kite.orders()
    if not orders:
        console.print("[yellow]No orders today.[/]")
        return

    table = Table(title="Order Book", box=box.ROUNDED)
    for col in ["Order ID", "Symbol", "Type", "Txn", "Product", "Qty", "Price", "Trig", "Status", "Time"]:
        table.add_column(col, overflow="fold")

    status_color = {
        "COMPLETE": "green", "REJECTED": "red", "CANCELLED": "dim",
        "OPEN": "cyan", "TRIGGER PENDING": "yellow",
    }

    for o in orders:
        status = o.get("status", "")
        color = status_color.get(status, "white")
        table.add_row(
            o["order_id"],
            o["tradingsymbol"],
            o["order_type"],
            o["transaction_type"],
            o["product"],
            str(o["quantity"]),
            str(o.get("price", 0)),
            str(o.get("trigger_price", 0)),
            f"[{color}]{status}[/{color}]",
            str(o.get("order_timestamp", ""))[:16],
        )
    console.print(table)


def print_trade_book(kite: KiteConnect):
    trades = kite.trades()
    if not trades:
        console.print("[yellow]No trades today.[/]")
        return

    table = Table(title="Trade Book", box=box.ROUNDED)
    for col in ["Trade ID", "Order ID", "Symbol", "Exch", "Txn", "Product", "Qty", "Price", "Time"]:
        table.add_column(col, overflow="fold")

    for t in trades:
        table.add_row(
            t.get("trade_id", ""),
            t.get("order_id", ""),
            t["tradingsymbol"],
            t["exchange"],
            t["transaction_type"],
            t["product"],
            str(t["quantity"]),
            str(t["average_price"]),
            str(t.get("fill_timestamp", ""))[:16],
        )
    console.print(table)


def place_order_interactive(kite: KiteConnect):
    console.print("\n[bold cyan]Place Order[/]")

    variety = Prompt.ask("Variety", choices=VARIETIES, default="regular")
    exchange = Prompt.ask("Exchange", choices=EXCHANGES, default="NSE")
    symbol = Prompt.ask("Symbol (e.g. INFY, NIFTY24JANFUT)").upper()
    txn = Prompt.ask("Transaction", choices=TRANSACTION_TYPES, default="BUY")
    product = Prompt.ask("Product", choices=PRODUCTS, default="CNC")
    order_type = Prompt.ask("Order Type", choices=ORDER_TYPES, default="LIMIT")
    quantity = int(Prompt.ask("Quantity"))

    price = 0.0
    trigger_price = 0.0

    if order_type in ("LIMIT", "SL"):
        price = float(Prompt.ask("Price"))
    if order_type in ("SL", "SL-M"):
        trigger_price = float(Prompt.ask("Trigger Price"))

    validity = Prompt.ask("Validity", choices=VALIDITIES, default="DAY")
    tag = Prompt.ask("Tag (optional, press Enter to skip)", default="")

    console.print(f"\n[bold]Order Summary:[/]  {txn} {quantity} {symbol} @ {order_type}"
                  f" {price if price else ''} | {product} | {exchange}")

    if not Confirm.ask("Confirm?"):
        console.print("[yellow]Cancelled.[/]")
        return

    try:
        order_id = kite.place_order(
            variety=variety,
            exchange=exchange,
            tradingsymbol=symbol,
            transaction_type=txn,
            quantity=quantity,
            product=product,
            order_type=order_type,
            price=price or None,
            trigger_price=trigger_price or None,
            validity=validity,
            tag=tag or None,
        )
        console.print(f"[green]✓ Order placed. ID: [bold]{order_id}[/][/]")
    except Exception as e:
        console.print(f"[red]Order failed: {e}[/]")


def modify_order_interactive(kite: KiteConnect):
    console.print("\n[bold cyan]Modify Order[/]")
    order_id = Prompt.ask("Order ID")
    variety = Prompt.ask("Variety", choices=VARIETIES, default="regular")

    quantity = int(Prompt.ask("New Quantity (0 = keep)") or "0") or None
    price = float(Prompt.ask("New Price (0 = keep)") or "0") or None
    trigger_price = float(Prompt.ask("New Trigger Price (0 = keep)") or "0") or None
    order_type = Prompt.ask("Order Type (Enter to keep)", choices=ORDER_TYPES + [""], default="")
    validity = Prompt.ask("Validity (Enter to keep)", choices=VALIDITIES + [""], default="")

    kwargs = dict(variety=variety, order_id=order_id)
    if quantity:
        kwargs["quantity"] = quantity
    if price:
        kwargs["price"] = price
    if trigger_price:
        kwargs["trigger_price"] = trigger_price
    if order_type:
        kwargs["order_type"] = order_type
    if validity:
        kwargs["validity"] = validity

    try:
        kite.modify_order(**kwargs)
        console.print(f"[green]✓ Order {order_id} modified.[/]")
    except Exception as e:
        console.print(f"[red]Modify failed: {e}[/]")


def cancel_order_interactive(kite: KiteConnect):
    console.print("\n[bold cyan]Cancel Order[/]")
    order_id = Prompt.ask("Order ID")
    variety = Prompt.ask("Variety", choices=VARIETIES, default="regular")
    if Confirm.ask(f"Cancel order {order_id}?"):
        try:
            kite.cancel_order(variety=variety, order_id=order_id)
            console.print(f"[green]✓ Order {order_id} cancelled.[/]")
        except Exception as e:
            console.print(f"[red]Cancel failed: {e}[/]")


def print_order_history(kite: KiteConnect):
    order_id = Prompt.ask("Order ID")
    try:
        history = kite.order_history(order_id)
        table = Table(title=f"History: {order_id}", box=box.SIMPLE)
        for col in ["Status", "Qty", "Price", "Message", "Timestamp"]:
            table.add_column(col)
        for h in history:
            table.add_row(
                h.get("status", ""),
                str(h.get("quantity", "")),
                str(h.get("price", "")),
                h.get("status_message", "") or "",
                str(h.get("order_timestamp", ""))[:19],
            )
        console.print(table)
    except Exception as e:
        console.print(f"[red]{e}[/]")
