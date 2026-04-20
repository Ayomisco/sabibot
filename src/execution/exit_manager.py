"""
Exit Manager — closes scalp positions after the configured hold time.

Called on a tight interval (default every 2 minutes) from main.py.

Logic:
  - FILLED / PARTIALLY_FILLED scalps older than scalp_hold_seconds:
      → place an offsetting SELL order at just below current mid-price
  - PENDING scalps older than scalp_hold_seconds:
      → cancel the open order (it never filled in time — skip)

Paper mode:
  Exit trades are marked immediately as filled with the current cached
  price, so P&L is simulated correctly without hitting the CLOB.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import and_, select

from src.config import settings
from src.db.database import async_session
from src.db.models import Trade, TradeSide, TradeStatus
from src.polymarket.client import Market
from src.polymarket.clob import CLOBClient, clob
from src.utils.logger import get_logger
from src.utils.notifications import AlertLevel, send_telegram

log = get_logger("exit_manager")


def _sell_token_id(entry_side: TradeSide, market: Market) -> str:
    """Return the token_id for the tokens we hold and want to sell."""
    target = "Yes" if entry_side == TradeSide.BUY_YES else "No"
    for token in market.tokens:
        if token.get("outcome", "").lower() == target.lower():
            return token["token_id"]
    # Fallback — first token for YES, second for NO
    if market.tokens:
        idx = 0 if entry_side == TradeSide.BUY_YES else min(1, len(market.tokens) - 1)
        return market.tokens[idx]["token_id"]
    return ""


def _exit_price(entry_side: TradeSide, market: Market) -> float:
    """
    Aggressive sell price to ensure fast fill.
    2¢ below current mid so we land inside the spread and get taken quickly.
    """
    if entry_side == TradeSide.BUY_YES:
        mid = market.outcome_prices.get("Yes", 0.5)
    else:
        mid = market.outcome_prices.get("No", 0.5)
    return max(round(mid - 0.02, 4), 0.01)


class ExitManager:
    """Monitors and closes aged scalp positions."""

    def __init__(self, clob_client: CLOBClient | None = None) -> None:
        self._clob = clob_client or clob

    async def check_and_exit_scalps(self, markets_cache: list[Market]) -> None:
        """
        Main entry point — called every scalp_exit_check_seconds.
        Finds all scalp trades past their hold time and exits/cancels them.
        """
        cutoff = datetime.utcnow() - timedelta(seconds=settings.scalp_hold_seconds)
        market_map: dict[str, Market] = {m.condition_id: m for m in markets_cache}

        async with async_session() as session:
            stmt = select(Trade).where(
                and_(
                    Trade.strategy == "scalp",
                    Trade.status.in_([
                        TradeStatus.FILLED,
                        TradeStatus.PARTIALLY_FILLED,
                        TradeStatus.PENDING,
                    ]),
                    Trade.exit_at.is_(None),
                    Trade.created_at <= cutoff,
                )
            )
            result = await session.execute(stmt)
            aged_trades: list[Trade] = list(result.scalars().all())

        if not aged_trades:
            return

        log.info("scalp_exits_due", count=len(aged_trades))

        for trade in aged_trades:
            market = market_map.get(trade.condition_id)
            if market is None:
                log.warning(
                    "scalp_exit_market_not_found",
                    condition_id=trade.condition_id[:20],
                )
                # Mark as cancelled so we don't keep retrying
                await self._mark_cancelled(trade, "market not in cache")
                continue

            if trade.status == TradeStatus.PENDING:
                await self._cancel_pending(trade, market)
            else:
                await self._sell_position(trade, market)

    # ── Internal helpers ─────────────────────────────────────────

    async def _sell_position(self, trade: Trade, market: Market) -> None:
        """Place an exit SELL order for a filled scalp position."""
        token_id = _sell_token_id(trade.side, market)
        exit_px = _exit_price(trade.side, market)
        now = datetime.utcnow()

        success = True
        if settings.is_live:
            result = await self._clob.place_limit_order(
                token_id=token_id,
                side="SELL",
                price=exit_px,
                size=trade.shares,
                neg_risk=getattr(market, "neg_risk", True),
                tick_size=getattr(market, "minimum_tick_size", "0.01"),
            )
            success = result.success
            if not success:
                log.error(
                    "scalp_exit_order_failed",
                    market=market.question[:60],
                    error=result.error,
                )
                return
            log.info(
                "scalp_exit_live",
                order_id=result.order_id,
                market=market.question[:60],
                exit_price=exit_px,
            )
        else:
            log.info(
                "scalp_exit_paper",
                market=market.question[:60],
                entry=trade.price,
                exit=exit_px,
            )

        if success:
            pnl = (exit_px - trade.price) * trade.shares
            async with async_session() as session:
                # Re-fetch inside this session so we can commit cleanly
                db_trade = await session.get(Trade, trade.id)
                if db_trade:
                    db_trade.exit_price = exit_px
                    db_trade.exit_at = now
                    db_trade.pnl = pnl
                    db_trade.status = TradeStatus.FILLED
                    await session.commit()

            side_label = "YES" if trade.side == TradeSide.BUY_YES else "NO"
            level = AlertLevel.PROFIT if pnl >= 0 else AlertLevel.LOSS
            await send_telegram(
                f"Scalp exit {'profit' if pnl >= 0 else 'loss'}\n"
                f"Market: {market.question[:80]}\n"
                f"Side: {side_label}  |  Entry ${trade.price:.3f} → Exit ${exit_px:.3f}\n"
                f"P&L: ${pnl:+.2f}  (held {settings.scalp_hold_seconds // 60} min)",
                level,
            )

    async def _cancel_pending(self, trade: Trade, market: Market) -> None:
        """Cancel a scalp order that never filled within the hold window."""
        if settings.is_live and trade.order_id:
            try:
                await self._clob.cancel_order(trade.order_id)
                log.info("scalp_pending_cancelled", order_id=trade.order_id)
            except Exception as exc:
                log.warning("scalp_cancel_failed", error=str(exc)[:80])

        await self._mark_cancelled(trade, "expired before fill")

    async def _mark_cancelled(self, trade: Trade, reason: str) -> None:
        async with async_session() as session:
            db_trade = await session.get(Trade, trade.id)
            if db_trade:
                db_trade.status = TradeStatus.CANCELLED
                db_trade.exit_at = datetime.utcnow()
                db_trade.notes = f"[scalp_exit] {reason}"
                await session.commit()
        log.info(
            "scalp_trade_marked_cancelled",
            trade_id=trade.id,
            reason=reason,
        )


# Singleton
exit_manager = ExitManager()
