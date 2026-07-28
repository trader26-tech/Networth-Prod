import os
from dotenv import load_dotenv
from kiteconnect import KiteConnect
from rich.console import Console

load_dotenv()
console = Console()


def get_kite() -> KiteConnect:
    api_key = os.getenv("KITE_API_KEY", "").strip()
    access_token = os.getenv("KITE_ACCESS_TOKEN", "").strip()

    if not api_key or api_key == "YOUR_API_KEY_HERE":
        console.print("[bold red]KITE_API_KEY not set.[/] Edit [cyan].env[/] and add your API key.")
        raise SystemExit(1)

    if not access_token or access_token == "YOUR_ACCESS_TOKEN_HERE":
        console.print("[bold red]KITE_ACCESS_TOKEN not set.[/] Edit [cyan].env[/] and add your access token.")
        console.print("[dim]Tip: run [bold]python config.py[/bold] to generate a fresh token via browser login.[/]")
        raise SystemExit(1)

    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(access_token)
    return kite


def generate_token():
    """Open browser login flow and print a fresh access token."""
    api_key = os.getenv("KITE_API_KEY", "").strip()
    api_secret = os.getenv("KITE_API_SECRET", "").strip()

    if not api_key or not api_secret:
        console.print("[red]Set KITE_API_KEY and KITE_API_SECRET in .env first.[/]")
        return

    kite = KiteConnect(api_key=api_key)
    login_url = kite.login_url()
    console.print(f"\n[bold]Open this URL in your browser and log in:[/]\n[link]{login_url}[/link]\n")
    request_token = input("Paste the request_token from the redirect URL: ").strip()

    try:
        session = kite.generate_session(request_token, api_secret=api_secret)
        access_token = session["access_token"]
        console.print(f"\n[green]✓ Access token generated:[/]\n[bold cyan]{access_token}[/]")
        console.print("\n[dim]Add this to your .env as KITE_ACCESS_TOKEN[/]")
    except Exception as e:
        console.print(f"[red]Failed: {e}[/]")


if __name__ == "__main__":
    generate_token()
