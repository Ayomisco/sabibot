"""
Order Manager — executes approved trade proposals on the CLOB.

This is the final step before real money moves:
  Strategy → Risk Check → Order Manager → CLOB

Responsibilities:
  1. Convert approved proposals to CLOB orders
  2. Handle paper vs live mode
  3. Log every order to database
  4. Send Telegram notifications
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.config import settings
from src.db.database import async_session
from src.db.models import Trade, TradeSide, TradeStatus
from src.execution.risk_manager import RiskManager, risk_manager
from src.polymarket.clob import CLOBClient, clob
from src.strategies.base import TradeProposal
from src.utils.logger import get_logger
from src.utils.notifications import notify_trade

log = get_logger("order_manager")


class OrderManager:
    """Executes trade proposals after risk approval."""

    def __init__(
        self,
        clob_client: CLOBClient | None = None,
        risk_mgr: RiskManager | None = None,
    ) -> None:
        self._clob = clob_client or clob
        self._risk = risk_mgr or risk_manager
        # Cooldown maps: condition_id → timestamp of last trade attempt
        self._pending_markets: dict[str, datetime] = {}  # has open PENDING order
        self._failed_markets: dict[str, datetime] = {}   # recently failed

    async def execute_proposals(self, proposals: list[TradeProposal]) -> list[Trade]:
        """
        Process a batch of trade proposals:
          1. Risk check each one
          2. Execute approved ones on CLOB (or paper-log)
          3. Persist to database
          4. Notify via Telegram
        """
        executed_trades: list[Trade] = []

        now = datetime.now(timezone.utc)

        for proposal in proposals:
            cid = proposal.condition_id

            # Skip markets with an existing pending order (30-min cooldown)
            pending_since = self._pending_markets.get(cid)
            if pending_since and now - pending_since < timedelta(minutes=30):
                log.info("proposal_skipped_pending", market=proposal.market.question[:50])
                continue

            # Skip markets that recently failed (10-min cooldown)
            failed_since = self._failed_markets.get(cid)
            if failed_since and now - failed_since < timedelta(minutes=10):
                log.info("proposal_skipped_failed_cooldown", market=proposal.market.question[:50])
                continue

            risk_check = self._risk.check_proposal(proposal)

            if not risk_check.approved:
                log.info(
                    "proposal_rejected",
                    strategy=proposal.strategy_name,
                    market=proposal.market.question[:50],
                    reason=risk_check.rejection_reason,
                )
                continue

            final_size = risk_check.adjusted_size_usd

            # Calculate shares from size and price
            shares = final_size / max(proposal.market_price, 0.01)

            if settings.is_live:
                trade = await self._execute_live(proposal, final_size, shares)
            else:
                trade = await self._execute_paper(proposal, final_size, shares)

            if trade:
                # Update risk tracking
                self._risk.record_trade(cid, final_size)
                executed_trades.append(trade)

                # Update cooldown maps
                if trade.status == TradeStatus.PENDING:
                    self._pending_markets[cid] = now
                elif trade.status == TradeStatus.FAILED:
                    self._failed_markets[cid] = now

                # Persist to database
                async with async_session() as session:
                    session.add(trade)
                    await session.commit()

                # Notify via Telegram
                await notify_trade(
                    action="LIVE TRADE" if settings.is_live else "PAPER TRADE",
                    market=proposal.market.question,
                    side=proposal.side,
                    amount=final_size,
                    price=proposal.market_price,
                    edge=proposal.edge,
                    condition_id=proposal.condition_id,
                    market_slug=getattr(proposal.market, "market_slug", ""),
                    order_id=trade.order_id or "",
                    status=trade.status.value,
                    error=trade.notes if trade.status == TradeStatus.FAILED else "",
                )

        return executed_trades

    async def _execute_live(
        self, proposal: TradeProposal, size_usd: float, shares: float
    ) -> Trade | None:
        """Place a real order on the CLOB."""
        # Enforce minimum order size (CLOB rejects orders below this threshold)
        min_shares = getattr(proposal.market, "minimum_order_size", 5.0)
        if shares < min_shares:
            shares = min_shares
            size_usd = shares * proposal.max_price
            log.info("order_size_adjusted_to_minimum",
                     market=proposal.market.question[:50],
                     min_shares=min_shares, size_usd=f"${size_usd:.2f}")

        result = await self._clob.place_limit_order(
            token_id=proposal.token_id,
            side="BUY",
            price=proposal.max_price,
            size=shares,
            neg_risk=getattr(proposal.market, "neg_risk", True),
            tick_size=getattr(proposal.market, "minimum_tick_size", "0.01"),
        )

        status = TradeStatus.PENDING if result.success else TradeStatus.FAILED

        trade = Trade(
            condition_id=proposal.condition_id,
            market_question=proposal.market.question[:500],
            strategy=proposal.strategy_name,
            side=TradeSide(proposal.side),
            amount_usd=size_usd,
            price=proposal.max_price,
            shares=shares,
            edge=proposal.edge,
            confidence=proposal.confidence,
            status=status,
            order_id=result.order_id,
            notes=result.error[:500] if not result.success else proposal.reasoning[:500],
        )

        if result.success:
            log.info(
                "live_order_placed",
                order_id=result.order_id,
                market=proposal.market.question[:50],
                side=proposal.side,
                size=f"${size_usd:.2f}",
                price=f"${proposal.max_price:.3f}",
            )
        else:
            log.error(
                "live_order_failed",
                error=result.error,
                market=proposal.market.question[:50],
            )

        return trade

    async def _execute_paper(
        self, proposal: TradeProposal, size_usd: float, shares: float
    ) -> Trade:
        """Log a paper trade (no real money)."""
        trade = Trade(
            condition_id=proposal.condition_id,
            market_question=proposal.market.question[:500],
            strategy=proposal.strategy_name,
            side=TradeSide(proposal.side),
            amount_usd=size_usd,
            price=proposal.market_price,
            shares=shares,
            edge=proposal.edge,
            confidence=proposal.confidence,
            status=TradeStatus.FILLED,  # Paper trades are immediately "filled"
            order_id=f"paper_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
            notes=f"[PAPER] {proposal.reasoning[:450]}",
        )

        log.info(
            "paper_trade",
            market=proposal.market.question[:50],
            side=proposal.side,
            size=f"${size_usd:.2f}",
            price=f"${proposal.market_price:.3f}",
            edge=f"{proposal.edge:+.3f}",
        )

        return trade


# Singleton
order_manager = OrderManager()
