"""
Portfolio tracker — tracks open positions, P&L, and generates summaries.

Queries the database for trade history and computes:
  - Current open positions
  - Unrealized P&L (based on current market prices)
  - Realized P&L (from closed positions)
  - Daily/weekly performance summaries
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, func

from src.config import settings
from src.db.database import async_session
from src.db.models import PortfolioSnapshot, Trade, TradeStatus
from src.polymarket.client import polymarket
from src.utils.logger import get_logger

log = get_logger("portfolio")


@dataclass
class Position:
    condition_id: str
    market_question: str
    side: str
    shares: float
    entry_price: float
    current_price: float
    unrealized_pnl: float
    cost_basis: float


@dataclass
class PortfolioSummary:
    total_value_usd: float = 0.0
    cash_usd: float = 0.0
    positions_value_usd: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl_today: float = 0.0
    realized_pnl_total: float = 0.0
    open_positions: list[Position] = field(default_factory=list)
    total_trades: int = 0
    win_rate: float = 0.0


class PortfolioTracker:
    """Tracks positions and P&L from the trade database."""

    async def get_open_positions(self) -> list[Position]:
        """Get all open positions (trades without exits)."""
        async with async_session() as session:
            result = await session.execute(
                select(Trade).where(
                    Trade.status.in_(
                        [TradeStatus.FILLED, TradeStatus.PENDING]),
                    Trade.exit_at.is_(None),
                )
            )
            trades = result.scalars().all()

        # Group by market
        market_trades: dict[str, list[Trade]] = {}
        for trade in trades:
            market_trades.setdefault(trade.condition_id, []).append(trade)

        positions: list[Position] = []
        for condition_id, trade_list in market_trades.items():
            total_shares = sum(t.shares for t in trade_list)
            total_cost = sum(t.amount_usd for t in trade_list)
            if total_shares == 0:
                continue

            avg_entry = total_cost / total_shares if total_shares else 0
            side = trade_list[0].side.value

            # Get current price
            market = await polymarket.get_market(condition_id)
            if market:
                if "YES" in side:
                    current = market.outcome_prices.get("Yes", 0.5)
                else:
                    current = market.outcome_prices.get("No", 0.5)
            else:
                current = avg_entry  # Can't determine, assume flat

            unrealized = (current - avg_entry) * total_shares

            positions.append(Position(
                condition_id=condition_id,
                market_question=trade_list[0].market_question,
                side=side,
                shares=total_shares,
                entry_price=avg_entry,
                current_price=current,
                unrealized_pnl=unrealized,
                cost_basis=total_cost,
            ))

        return positions

    async def get_summary(self, initial_cash: float = 0.0) -> PortfolioSummary:
        """Generate a full portfolio summary."""
        positions = await self.get_open_positions()
        positions_value = sum(p.current_price * p.shares for p in positions)
        unrealized = sum(p.unrealized_pnl for p in positions)

        # In live mode, use the real CLOB balance as cash so the value
        # reflects actual on-chain funds rather than the ephemeral DB.
        if settings.is_live:
            try:
                from src.polymarket.clob import clob
                initial_cash = await clob.get_balance()
            except Exception as exc:
                log.warning("portfolio_balance_fetch_failed", error=str(exc))

        # Realized P&L from closed trades
        async with async_session() as session:
            # Today's realized P&L
            today_start = datetime.now(timezone.utc).replace(
                hour=0, minute=0, second=0, microsecond=0)
            result = await session.execute(
                select(func.sum(Trade.pnl)).where(
                    Trade.pnl.isnot(None),
                    Trade.exit_at >= today_start,
                )
            )
            realized_today = result.scalar() or 0.0

            # Total realized P&L
            result = await session.execute(
                select(func.sum(Trade.pnl)).where(Trade.pnl.isnot(None))
            )
            realized_total = result.scalar() or 0.0

            # Win rate
            result = await session.execute(
                select(func.count(Trade.id)).where(Trade.pnl.isnot(None))
            )
            closed_trades = result.scalar() or 0

            result = await session.execute(
                select(func.count(Trade.id)).where(
                    Trade.pnl.isnot(None), Trade.pnl > 0)
            )
            winning_trades = result.scalar() or 0

            win_rate = winning_trades / closed_trades if closed_trades > 0 else 0.0

            # Total trades
            result = await session.execute(select(func.count(Trade.id)))
            total_trades = result.scalar() or 0

        total_value = initial_cash + positions_value + realized_total

        return PortfolioSummary(
            total_value_usd=total_value,
            cash_usd=initial_cash,
            positions_value_usd=positions_value,
            unrealized_pnl=unrealized,
            realized_pnl_today=realized_today,
            realized_pnl_total=realized_total,
            open_positions=positions,
            total_trades=total_trades,
            win_rate=win_rate,
        )

    async def take_snapshot(self, summary: PortfolioSummary) -> None:
        """Save a portfolio snapshot to the database for historical tracking."""
        snapshot = PortfolioSnapshot(
            total_value_usd=summary.total_value_usd,
            cash_usd=summary.cash_usd,
            positions_value_usd=summary.positions_value_usd,
            unrealized_pnl=summary.unrealized_pnl,
            realized_pnl_today=summary.realized_pnl_today,
            open_positions=len(summary.open_positions),
        )
        async with async_session() as session:
            session.add(snapshot)
            await session.commit()


# Singleton
portfolio = PortfolioTracker()
