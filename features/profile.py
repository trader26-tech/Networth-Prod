from kiteconnect import KiteConnect
from rich.console import Console
from rich.panel import Panel

console = Console()


def print_profile(kite: KiteConnect):
    try:
        p = kite.profile()
    except Exception as e:
        console.print(f"[red]{e}[/]")
        return

    exchanges = ", ".join(p.get("exchanges", []))
    products = ", ".join(p.get("products", []))
    order_types = ", ".join(p.get("order_types", []))

    info = (
        f"[bold]Name:[/]       {p.get('user_name', '')}\n"
        f"[bold]User ID:[/]    {p.get('user_id', '')}\n"
        f"[bold]Email:[/]      {p.get('email', '')}\n"
        f"[bold]Broker:[/]     {p.get('broker', '')}\n"
        f"[bold]Type:[/]       {p.get('user_type', '')}\n"
        f"\n"
        f"[bold]Exchanges:[/]  {exchanges}\n"
        f"[bold]Products:[/]   {products}\n"
        f"[bold]Order Types:[/] {order_types}\n"
        f"[bold]API Key:[/]    {p.get('api_key', '')}\n"
    )
    console.print(Panel(info, title="[bold cyan]Account Profile[/]", expand=False))
