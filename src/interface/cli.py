"""
CLI interface — quick commands for development and debugging.

Usage:
  python -m src.interface.cli scan       # Run one news scan cycle
  python -m src.interface.cli markets    # List top active markets
  python -m src.interface.cli balance    # Check wallet balance
  python -m src.interface.cli status     # Portfolio status
"""

from __future__ import annotations

import asyncio
import sys

from rich.console import Console
from rich.table import Table

console = Console()


async def cmd_scan() -> None:
    """Run a single news scan cycle and print results."""
    from src.intelligence.news_scanner import scan_all_sources

    console.print("[bold]Scanning news sources...[/bold]")
    items = await scan_all_sources()

    if not items:
        console.print("[dim]No new items found.[/dim]")
        return

    table = Table(title=f"New Items ({len(items)})")
    table.add_column("Source", style="cyan", max_width=20)
    table.add_column("Title", style="white", max_width=60)
    table.add_column("Time", style="dim", max_width=20)

    for item in items[:20]:
        time_str = item.published_at.strftime("%H:%M") if item.published_at else "?"
        table.add_row(item.source[:20], item.title[:60], time_str)

    console.print(table)


async def cmd_markets() -> None:
    """List top active Polymarket markets."""
    from src.polymarket.client import polymarket

    console.print("[bold]Fetching active markets...[/bold]")
    markets = await polymarket.get_all_active_markets(max_markets=30)

    table = Table(title=f"Active Markets ({len(markets)})")
    table.add_column("Question", style="white", max_width=50)
    table.add_column("YES", style="green", justify="right")
    table.add_column("NO", style="red", justify="right")
    table.add_column("Vol 24h", style="dim", justify="right")

    for m in markets:
        yes = m.outcome_prices.get("Yes", 0)
        no = m.outcome_prices.get("No", 0)
        table.add_row(
            m.question[:50],
            f"${yes:.2f}",
            f"${no:.2f}",
            f"${m.volume_24h:,.0f}",
        )

    console.print(table)


async def cmd_status() -> None:
    """Show portfolio status."""
    from src.db.database import init_db
    from src.execution.portfolio import portfolio

    await init_db()
    summary = await portfolio.get_summary()

    console.print(f"\n[bold]Portfolio Summary[/bold]")
    console.print(f"  Total Value:    ${summary.total_value_usd:.2f}")
    console.print(f"  Positions:      {len(summary.open_positions)}")
    console.print(f"  Unrealized P&L: ${summary.unrealized_pnl:+.2f}")
    console.print(f"  Today's P&L:    ${summary.realized_pnl_today:+.2f}")
    console.print(f"  Total P&L:      ${summary.realized_pnl_total:+.2f}")
    console.print(f"  Win Rate:       {summary.win_rate:.0%}")
    console.print(f"  Total Trades:   {summary.total_trades}")


def main() -> None:
    if len(sys.argv) < 2:
        console.print("Usage: python -m src.interface.cli [scan|markets|status]")
        sys.exit(1)

    cmd = sys.argv[1]
    commands = {
        "scan": cmd_scan,
        "markets": cmd_markets,
        "status": cmd_status,
    }

    fn = commands.get(cmd)
    if fn is None:
        console.print(f"Unknown command: {cmd}")
        console.print(f"Available: {', '.join(commands.keys())}")
        sys.exit(1)

    asyncio.run(fn())


if __name__ == "__main__":
    main()
