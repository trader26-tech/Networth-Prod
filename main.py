#!/usr/bin/env python3
"""
Zerodha Kite Pro — Full Feature Suite
======================================
Steps to run:
  1. pip install -r requirements.txt
  2. Edit .env  →  set KITE_API_KEY and KITE_ACCESS_TOKEN
  3. python main.py
"""

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.prompt import Prompt
from rich import box
from rich.text import Text

from config import get_kite

console = Console()

MENU = [
    # (label, section_header)
    ("─── ACCOUNT ───", None),
    ("Profile & API info", "profile"),

    ("─── PORTFOLIO & FUNDS ───", None),
    ("Holdings (equity demat)", "holdings"),
    ("Positions (F&O / intraday)", "positions"),
    ("Account margins & funds", "margins_funds"),

    ("─── ORDERS ───", None),
    ("View order book", "order_book"),
    ("View trade book", "trade_book"),
    ("View order history", "order_history"),
    ("Place order", "place_order"),
    ("Modify order", "modify_order"),
    ("Cancel order", "cancel_order"),

    ("─── MARKET DATA ───", None),
    ("Live LTP (last traded price)", "ltp"),
    ("Live OHLC quote", "ohlc"),
    ("Full quote + market depth (order book)", "full_quote"),
    ("Historical OHLCV candles", "historical"),
    ("Live WebSocket ticker (real-time stream)", "ticker"),

    ("─── INSTRUMENTS ───", None),
    ("Search instrument / get token", "search_inst"),
    ("Get exact token for symbol", "get_token"),
    ("Download full instrument list to CSV", "download_inst"),

    ("─── GTT ORDERS ───", None),
    ("View all GTTs", "view_gtt"),
    ("Place single GTT (one trigger)", "single_gtt"),
    ("Place OCO GTT (target + stop-loss)", "oco_gtt"),
    ("Delete GTT", "delete_gtt"),

    ("─── MARGIN CALCULATOR ───", None),
    ("Basket order margin calculator", "basket_margin"),

    ("─── MUTUAL FUNDS ───", None),
    ("MF holdings", "mf_holdings"),
    ("MF order book", "mf_orders"),
    ("Place MF order (buy/sell)", "place_mf"),
    ("Active SIPs", "mf_sips"),
    ("Create SIP", "create_sip"),
    ("Browse MF instruments", "browse_mf"),
]


def build_menu_table() -> Table:
    table = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
    table.add_column("Num", style="bold yellow", justify="right", no_wrap=True)
    table.add_column("Feature")

    num = 1
    for label, key in MENU:
        if key is None:
            table.add_row("", f"[bold dim]{label}[/]")
        else:
            table.add_row(str(num), label)
            num += 1
    return table


def get_action(choice: int):
    items = [(label, key) for label, key in MENU if key is not None]
    if 1 <= choice <= len(items):
        return items[choice - 1][1]
    return None


def run(kite):
    from features import portfolio, funds, orders, market_data, quotes, ticker, instruments, gtt, margins, mf, profile

    dispatch = {
        "profile": lambda: profile.print_profile(kite),
        "holdings": lambda: portfolio.print_holdings(kite),
        "positions": lambda: portfolio.print_positions(kite),
        "margins_funds": lambda: funds.print_margins(kite),
        "order_book": lambda: orders.print_order_book(kite),
        "trade_book": lambda: orders.print_trade_book(kite),
        "order_history": lambda: orders.print_order_history(kite),
        "place_order": lambda: orders.place_order_interactive(kite),
        "modify_order": lambda: orders.modify_order_interactive(kite),
        "cancel_order": lambda: orders.cancel_order_interactive(kite),
        "ltp": lambda: quotes.print_ltp(kite),
        "ohlc": lambda: quotes.print_ohlc(kite),
        "full_quote": lambda: quotes.print_full_quote(kite),
        "historical": lambda: market_data.print_historical_interactive(kite),
        "ticker": lambda: ticker.start_ticker(kite),
        "search_inst": lambda: instruments.search_instrument(kite),
        "get_token": lambda: instruments.get_token_for_symbol(kite),
        "download_inst": lambda: instruments.download_instruments(kite),
        "view_gtt": lambda: gtt.print_gtts(kite),
        "single_gtt": lambda: gtt.place_single_gtt(kite),
        "oco_gtt": lambda: gtt.place_oco_gtt(kite),
        "delete_gtt": lambda: gtt.delete_gtt(kite),
        "basket_margin": lambda: margins.calculate_basket_margin(kite),
        "mf_holdings": lambda: mf.print_mf_holdings(kite),
        "mf_orders": lambda: mf.print_mf_orders(kite),
        "place_mf": lambda: mf.place_mf_order(kite),
        "mf_sips": lambda: mf.print_mf_sips(kite),
        "create_sip": lambda: mf.place_mf_sip(kite),
        "browse_mf": lambda: mf.browse_mf_instruments(kite),
    }

    total_items = sum(1 for _, k in MENU if k is not None)

    while True:
        console.clear()
        console.print(Panel(
            "[bold white]Zerodha Kite Pro — Full Feature Suite[/]\n"
            "[dim]All Kite Connect paid API features in one place[/]",
            style="bold blue", expand=False
        ))
        console.print(build_menu_table())
        console.print(f"\n[dim]Enter a number (1–{total_items}), or [bold]0[/] to exit.[/]")

        raw = Prompt.ask("\n[bold yellow]>[/]")
        if raw.strip() == "0":
            console.print("[bold green]Goodbye.[/]")
            break

        try:
            choice = int(raw.strip())
        except ValueError:
            console.print("[red]Invalid input.[/]")
            input("\nPress Enter to continue...")
            continue

        action_key = get_action(choice)
        if not action_key:
            console.print("[red]Invalid choice.[/]")
            input("\nPress Enter to continue...")
            continue

        console.rule()
        try:
            dispatch[action_key]()
        except KeyboardInterrupt:
            console.print("\n[yellow]Interrupted.[/]")
        except Exception as e:
            console.print(f"[bold red]Error:[/] {e}")

        input("\nPress Enter to return to menu...")


def main():
    console.print("[dim]Connecting to Kite Connect...[/]")
    kite = get_kite()

    try:
        profile = kite.profile()
        console.print(f"[green]✓ Authenticated as [bold]{profile['user_name']}[/] ({profile['user_id']})[/]")
    except Exception as e:
        console.print(f"[red]Authentication failed: {e}[/]")
        console.print("[dim]Make sure your KITE_ACCESS_TOKEN is valid (tokens expire daily).[/]")
        raise SystemExit(1)

    run(kite)


if __name__ == "__main__":
    main()
