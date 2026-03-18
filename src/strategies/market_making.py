"""
Market Making Strategy.

CONCEPT:
  Provide liquidity on both sides of a market (YES and NO) by placing
  limit orders at the bid and ask. Profit from the bid-ask spread.

  This is the most conservative strategy — it doesn't require
  directional conviction. It profits from the spread between buyers and sellers.

EDGE SOURCE:
  - Wider spreads on less liquid markets = more profit per trade
  - Inventory risk management: rebalance when position gets one-sided

WHEN TO USE:
  - Sideways markets with no clear direction
  - Markets with wide spreads (>5¢) and decent volume
  - When the bot has no strong directional signals

RISK:
  - Adverse selection: informed traders trade against you when news breaks
  - Inventory risk: you may accumulate one side if the market moves
"""

from __future__ import annotations

from src.config import settings
from src.intelligence.signal_aggregator import AggregatedSignal
from src.polymarket.client import Market
from src.strategies.base import BaseStrategy, TradeProposal
from src.utils.logger import get_logger

log = get_logger("strategy.market_making")

MIN_SPREAD = 0.04     # Don't make markets if spread < 4¢
MAX_SPREAD = 0.30     # Skip extremely illiquid markets
MIN_LIQUIDITY = 5000  # Minimum dollar liquidity to consider


class MarketMakingStrategy(BaseStrategy):
    def __init__(self) -> None:
        super().__init__(name="market_making")

    async def evaluate(
        self,
        markets: list[Market],
        signals: dict[str, AggregatedSignal] | None = None,
    ) -> list[TradeProposal]:
        proposals: list[TradeProposal] = []

        for market in markets:
            yes_price = market.outcome_prices.get("Yes", 0.5)
            no_price = market.outcome_prices.get("No", 0.5)
            spread = abs(1.0 - yes_price - no_price)

            # Only make markets in suitable conditions
            if spread < MIN_SPREAD or spread > MAX_SPREAD:
                continue
            if market.liquidity < MIN_LIQUIDITY:
                continue

            # Don't make markets if we have a strong directional signal
            if signals:
                agg = signals.get(market.condition_id)
                if agg and abs(agg.edge) > 0.10:
                    continue  # Let directional strategies handle this

            # Place buy orders slightly inside the current bid-ask
            # Goal: be the tightest quote to get filled
            midpoint = (yes_price + (1 - no_price)) / 2
            half_spread = spread / 2
            our_spread = half_spread * 0.8  # Tighten the spread by 20%

            bid_price = round(midpoint - our_spread, 3)
            ask_price = round(midpoint + our_spread, 3)

            # Only propose the buy side (the execution layer handles the other side)
            # In practice, we'd place both sides, but each proposal is one direction
            if bid_price > 0.05 and bid_price < 0.95:
                proposals.append(TradeProposal(
                    strategy_name=self.name,
                    market=market,
                    condition_id=market.condition_id,
                    token_id=self._get_token_id(market, "BUY_YES"),
                    side="BUY_YES",
                    fair_value=midpoint,
                    market_price=yes_price,
                    edge=our_spread,
                    confidence=0.5,  # Market making = moderate confidence
                    suggested_size_usd=min(20.0, settings.max_position_size_usd * 0.3),
                    max_price=bid_price,
                    reasoning=f"Market making: spread {spread:.1%}, placing bid at {bid_price:.3f}",
                    urgency=0.3,  # Not urgent — passive strategy
                    metadata={
                        "strategy_type": "market_making",
                        "spread": spread,
                        "midpoint": midpoint,
                        "ask_price": ask_price,
                    },
                ))

        return proposals
