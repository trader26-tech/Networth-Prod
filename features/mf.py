from kiteconnect import KiteConnect
from rich.console import Console
from rich.table import Table
from rich.prompt import Prompt, Confirm
from rich import box

console = Console()


def print_mf_holdings(kite: KiteConnect):
    try:
        holdings = kite.mf_holdings()
    except Exception as e:
        console.print(f"[red]{e}[/]")
        return

    if not holdings:
        console.print("[yellow]No MF holdings.[/]")
        return

    table = Table(title="Mutual Fund Holdings", box=box.ROUNDED)
    table.add_column("Fund Name", min_width=30)
    table.add_column("Folio")
    table.add_column("Units", justify="right")
    table.add_column("Avg NAV", justify="right")
    table.add_column("Last NAV", justify="right")
    table.add_column("Invested", justify="right")
    table.add_column("Current", justify="right")
    table.add_column("P&L", justify="right")

    total_inv = total_cur = 0.0
    for h in holdings:
        units = h.get("quantity", 0)
        avg = h.get("average_price", 0)
        nav = h.get("last_price", 0)
        invested = avg * units
        current = nav * units
        pnl = current - invested
        total_inv += invested
        total_cur += current
        color = "green" if pnl >= 0 else "red"
        table.add_row(
            h.get("fund", ""),
            h.get("folio", ""),
            f"{units:.4f}",
            f"₹{avg:.4f}",
            f"₹{nav:.4f}",
            f"₹{invested:,.2f}",
            f"₹{current:,.2f}",
            f"[{color}]{pnl:+,.2f}[/{color}]",
        )

    console.print(table)
    pnl = total_cur - total_inv
    color = "green" if pnl >= 0 else "red"
    console.print(f"\nTotal Invested: ₹{total_inv:,.2f}  |  Current: ₹{total_cur:,.2f}  |  P&L: [{color}]{pnl:+,.2f}[/{color}]")


def print_mf_orders(kite: KiteConnect):
    try:
        orders = kite.mf_orders()
    except Exception as e:
        console.print(f"[red]{e}[/]")
        return

    if not orders:
        console.print("[yellow]No MF orders.[/]")
        return

    table = Table(title="MF Order Book", box=box.ROUNDED)
    table.add_column("Order ID", style="dim")
    table.add_column("Fund")
    table.add_column("Txn")
    table.add_column("Amount", justify="right")
    table.add_column("Units", justify="right")
    table.add_column("Status")
    table.add_column("Date")

    for o in orders:
        status = o.get("status", "")
        color = {"COMPLETE": "green", "REJECTED": "red", "CANCELLED": "dim"}.get(status, "cyan")
        table.add_row(
            o.get("order_id", ""),
            o.get("fund", ""),
            o.get("transaction_type", ""),
            f"₹{o.get('amount', 0):,.2f}",
            f"{o.get('quantity', 0):.4f}",
            f"[{color}]{status}[/{color}]",
            str(o.get("order_timestamp", ""))[:10],
        )
    console.print(table)


def print_mf_sips(kite: KiteConnect):
    try:
        sips = kite.mf_sips()
    except Exception as e:
        console.print(f"[red]{e}[/]")
        return

    if not sips:
        console.print("[yellow]No active SIPs.[/]")
        return

    table = Table(title="Active SIPs", box=box.ROUNDED)
    table.add_column("SIP ID", style="dim")
    table.add_column("Fund")
    table.add_column("Amount", justify="right")
    table.add_column("Frequency")
    table.add_column("Instalments Left", justify="right")
    table.add_column("Next Date")
    table.add_column("Status")

    for s in sips:
        status = s.get("status", "")
        color = "green" if status == "active" else "dim"
        table.add_row(
            s.get("sip_id", ""),
            s.get("fund_name", ""),
            f"₹{s.get('instalment_amount', 0):,.2f}",
            s.get("frequency", ""),
            str(s.get("instalments_remaining", "∞") if s.get("instalments_remaining", -1) != -1 else "∞"),
            str(s.get("next_instalment", ""))[:10],
            f"[{color}]{status}[/{color}]",
        )
    console.print(table)


def place_mf_order(kite: KiteConnect):
    console.print("\n[bold cyan]Place MF Order[/]")
    console.print("[dim]Use 'Browse MF Instruments' to find the tradingsymbol (ISIN).[/]")

    symbol = Prompt.ask("Fund tradingsymbol (ISIN, e.g. INF846K01EW2)")
    txn = Prompt.ask("Transaction", choices=["BUY", "SELL"])

    if txn == "BUY":
        amount = float(Prompt.ask("Amount in ₹ (e.g. 5000)"))
        if not Confirm.ask(f"Buy ₹{amount:,.2f} of {symbol}?"):
            return
        try:
            order_id = kite.place_mf_order(tradingsymbol=symbol, transaction_type="BUY", amount=amount)
            console.print(f"[green]✓ MF order placed. ID: [bold]{order_id}[/][/]")
        except Exception as e:
            console.print(f"[red]{e}[/]")
    else:
        console.print("[dim]For SELL, enter quantity (units) to redeem.[/]")
        quantity = float(Prompt.ask("Units to redeem"))
        if not Confirm.ask(f"Sell {quantity} units of {symbol}?"):
            return
        try:
            order_id = kite.place_mf_order(tradingsymbol=symbol, transaction_type="SELL", quantity=quantity)
            console.print(f"[green]✓ MF order placed. ID: [bold]{order_id}[/][/]")
        except Exception as e:
            console.print(f"[red]{e}[/]")


def browse_mf_instruments(kite: KiteConnect):
    console.print("\n[bold cyan]Browse MF Instruments[/]")
    query = Prompt.ask("Search fund name (partial match)")

    try:
        instruments = kite.mf_instruments()
    except Exception as e:
        console.print(f"[red]{e}[/]")
        return

    results = [i for i in instruments if query.lower() in i.get("name", "").lower()
               or query.upper() in i.get("tradingsymbol", "").upper()]

    if not results:
        console.print(f"[yellow]No funds matching '{query}'.[/]")
        return

    table = Table(title=f"MF Search: {query}", box=box.ROUNDED)
    table.add_column("Tradingsymbol (ISIN)", style="cyan")
    table.add_column("Fund Name")
    table.add_column("AMC")
    table.add_column("Plan")
    table.add_column("Min Purchase", justify="right")
    table.add_column("Div Freq")

    for i in results[:40]:
        table.add_row(
            i.get("tradingsymbol", ""),
            i.get("name", ""),
            i.get("amc", ""),
            i.get("plan", ""),
            f"₹{i.get('minimum_purchase_amount', 0):,.2f}",
            i.get("dividend_type", ""),
        )

    console.print(table)
    if len(results) > 40:
        console.print(f"[dim]Showing 40 of {len(results)} results.[/]")


def place_mf_sip(kite: KiteConnect):
    console.print("\n[bold cyan]Create SIP[/]")
    symbol = Prompt.ask("Fund tradingsymbol (ISIN)")
    amount = float(Prompt.ask("Monthly amount ₹"))
    frequency = Prompt.ask("Frequency", choices=["monthly", "weekly", "quarterly"], default="monthly")
    instalments = int(Prompt.ask("Number of instalments (-1 for perpetual)", default="-1"))
    instalment_day = int(Prompt.ask("Day of month/week (1-28 for monthly)", default="1"))

    if not Confirm.ask(f"Create SIP: ₹{amount:,.0f} {frequency} for {symbol}?"):
        return
    try:
        sip_id = kite.place_mf_sip(
            tradingsymbol=symbol,
            amount=amount,
            instalments=instalments,
            frequency=frequency,
            instalment_day=instalment_day,
        )
        console.print(f"[green]✓ SIP created. ID: [bold]{sip_id}[/][/]")
    except Exception as e:
        console.print(f"[red]{e}[/]")
