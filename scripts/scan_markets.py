"""Scan active Polymarket markets and display them."""

import asyncio
from rich.console import Console
from rich.table import Table

from src.polymarket.client import polymarket


async def main() -> None:
    console = Console()
    console.print("[bold]Fetching active Polymarket markets...[/bold]\n")

    markets = await polymarket.get_all_active_markets(max_markets=50)

    table = Table(title=f"Active Tradeable Markets ({len(markets)})")
    table.add_column("#", style="dim", width=4)
    table.add_column("Question", style="white", max_width=55)
    table.add_column("YES", style="green", justify="right", width=7)
    table.add_column("NO", style="red", justify="right", width=7)
    table.add_column("Tags", style="cyan", max_width=15)
    table.add_column("Min $", style="dim", justify="right", width=6)

    for i, m in enumerate(markets, 1):
        yes = m.outcome_prices.get("Yes", 0)
        no = m.outcome_prices.get("No", 0)
        tags = ", ".join(m.tags[:2]) if m.tags else ""
        table.add_row(
            str(i),
            m.question[:55],
            f"${yes:.2f}",
            f"${no:.2f}",
            tags,
            f"${m.minimum_order_size:.0f}",
        )

    console.print(table)
    await polymarket.close()


if __name__ == "__main__":
    asyncio.run(main())
