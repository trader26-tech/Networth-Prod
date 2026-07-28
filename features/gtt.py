from kiteconnect import KiteConnect
from rich.console import Console
from rich.table import Table
from rich.prompt import Prompt, Confirm
from rich import box

console = Console()


def print_gtts(kite: KiteConnect):
    try:
        gtts = kite.get_gtts()
    except Exception as e:
        console.print(f"[red]{e}[/]")
        return

    if not gtts:
        console.print("[yellow]No GTTs found.[/]")
        return

    table = Table(title="GTT Orders", box=box.ROUNDED)
    table.add_column("ID", justify="right", style="dim")
    table.add_column("Type")
    table.add_column("Symbol", style="bold cyan")
    table.add_column("Exch")
    table.add_column("Trigger(s)")
    table.add_column("Status")
    table.add_column("Created")

    status_color = {"active": "green", "triggered": "cyan", "disabled": "dim", "expired": "red"}

    for g in gtts:
        status = g.get("status", "")
        color = status_color.get(status, "white")
        triggers = ", ".join(str(t) for t in g.get("condition", {}).get("trigger_values", []))
        table.add_row(
            str(g["id"]),
            g.get("type", ""),
            g.get("condition", {}).get("tradingsymbol", ""),
            g.get("condition", {}).get("exchange", ""),
            triggers,
            f"[{color}]{status}[/{color}]",
            str(g.get("created_at", ""))[:16],
        )
    console.print(table)


def place_single_gtt(kite: KiteConnect):
    console.print("\n[bold cyan]Place Single GTT (One trigger → one order)[/]")
    exchange = Prompt.ask("Exchange", default="NSE")
    symbol = Prompt.ask("Symbol").upper()
    last_price = float(Prompt.ask("Current market price"))
    trigger_price = float(Prompt.ask("Trigger price (when LTP hits this, order fires)"))
    txn = Prompt.ask("Transaction", choices=["BUY", "SELL"])
    quantity = int(Prompt.ask("Quantity"))
    order_price = float(Prompt.ask("Order limit price"))
    product = Prompt.ask("Product", choices=["CNC", "NRML", "MIS"], default="CNC")

    if not Confirm.ask(f"Place GTT: {txn} {quantity} {symbol} when price hits {trigger_price}?"):
        return

    try:
        gtt_id = kite.place_gtt(
            trigger_type=kite.GTT_TYPE_SINGLE,
            tradingsymbol=symbol,
            exchange=exchange,
            trigger_values=[trigger_price],
            last_price=last_price,
            orders=[{
                "transaction_type": txn,
                "quantity": quantity,
                "order_type": "LIMIT",
                "product": product,
                "price": order_price,
            }],
        )
        console.print(f"[green]✓ GTT placed. ID: [bold]{gtt_id}[/][/]")
    except Exception as e:
        console.print(f"[red]{e}[/]")


def place_oco_gtt(kite: KiteConnect):
    console.print("\n[bold cyan]Place OCO GTT (Target + Stop-Loss, whichever hits first)[/]")
    exchange = Prompt.ask("Exchange", default="NSE")
    symbol = Prompt.ask("Symbol").upper()
    last_price = float(Prompt.ask("Current market price"))
    txn = Prompt.ask("Transaction type for BOTH legs", choices=["BUY", "SELL"])
    quantity = int(Prompt.ask("Quantity"))
    product = Prompt.ask("Product", choices=["CNC", "NRML", "MIS"], default="CNC")

    console.print("\n[dim]Lower trigger (stop-loss leg):[/]")
    lower_trigger = float(Prompt.ask("Lower trigger price"))
    lower_price = float(Prompt.ask("Lower limit price"))

    console.print("\n[dim]Upper trigger (target leg):[/]")
    upper_trigger = float(Prompt.ask("Upper trigger price"))
    upper_price = float(Prompt.ask("Upper limit price"))

    if not Confirm.ask(f"Place OCO GTT for {symbol}?"):
        return

    try:
        gtt_id = kite.place_gtt(
            trigger_type=kite.GTT_TYPE_OCO,
            tradingsymbol=symbol,
            exchange=exchange,
            trigger_values=[lower_trigger, upper_trigger],
            last_price=last_price,
            orders=[
                {"transaction_type": txn, "quantity": quantity,
                 "order_type": "LIMIT", "product": product, "price": lower_price},
                {"transaction_type": txn, "quantity": quantity,
                 "order_type": "LIMIT", "product": product, "price": upper_price},
            ],
        )
        console.print(f"[green]✓ OCO GTT placed. ID: [bold]{gtt_id}[/][/]")
    except Exception as e:
        console.print(f"[red]{e}[/]")


def delete_gtt(kite: KiteConnect):
    trigger_id = int(Prompt.ask("GTT ID to delete"))
    if Confirm.ask(f"Delete GTT {trigger_id}?"):
        try:
            kite.delete_gtt(trigger_id)
            console.print(f"[green]✓ GTT {trigger_id} deleted.[/]")
        except Exception as e:
            console.print(f"[red]{e}[/]")
