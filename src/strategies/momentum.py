"""
Momentum Strategy.

CONCEPT:
  When a market's price is moving consistently in one direction over
  a short timeframe, the momentum often continues before mean-reverting.

  This is the "trend is your friend" approach — ride the wave, exit
  before it crashes.

IMPLEMENTATION:
  Uses recent price changes to detect momentum. Enters when momentum
  is strong, exits when it weakens.

EDGE SOURCE:
  - Prediction markets have delayed information diffusion
  - A price moving 5¢+ in an hour often continues another 2-3¢
  - Requires the WebSocket feed for real-time price tracking

NOTE:
  This strategy requires price history from the WebSocket feed.
  It evaluates based on stored price snapshots, not just current price.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from src.config import settings
from src.intelligence.signal_aggregator import AggregatedSignal
from src.polymarket.client import Market
from src.strategies.base import BaseStrategy, TradeProposal
from src.utils.logger import get_logger

log = get_logger("strategy.momentum")

# Minimum price change over the lookback window to consider it "momentum"
MIN_MOMENTUM = 0.04  # 4¢ move
# Lookback window for price history
LOOKBACK_MINUTES = 60


class MomentumStrategy(BaseStrategy):
    def __init__(self) -> None:
        super().__init__(name="momentum")
        # Price history: condition_id -> list of (timestamp, price)
        self._price_history: dict[str,
                                  list[tuple[datetime, float]]] = defaultdict(list)

    def record_price(self, condition_id: str, price: float) -> None:
        """Called by the WebSocket handler to record price updates."""
        now = datetime.now(timezone.utc)
        history = self._price_history[condition_id]
        history.append((now, price))
        # Keep only recent
        cutoff = now.timestamp() - (LOOKBACK_MINUTES * 60 * 2)
        self._price_history[condition_id] = [
            (t, p) for t, p in history if t.timestamp() > cutoff
        ]

    async def evaluate(
        self,
        markets: list[Market],
        signals: dict[str, AggregatedSignal] | None = None,
    ) -> list[TradeProposal]:
        proposals: list[TradeProposal] = []
        now = datetime.now(timezone.utc)
        cutoff_ts = now.timestamp() - (LOOKBACK_MINUTES * 60)

        for market in markets:
            history = self._price_history.get(market.condition_id)
            if not history or len(history) < 3:
                continue

            # Get prices within lookback window
            recent = [(t, p) for t, p in history if t.timestamp() > cutoff_ts]
            if len(recent) < 3:
                continue

            first_price = recent[0][1]
            last_price = recent[-1][1]
            price_change = last_price - first_price

            if abs(price_change) < MIN_MOMENTUM:
                continue

            # Check consistency — is the move mostly in one direction?
            ups = sum(1 for i in range(1, len(recent))
                      if recent[i][1] > recent[i-1][1])
            downs = len(recent) - 1 - ups
            consistency = max(ups, downs) / max(1, ups + downs)

            if consistency < 0.6:
                continue  # Too choppy, not real momentum

            # Momentum direction
            if price_change > 0:
                side = "BUY_YES"
                # Expect 30% continuation
                projected = last_price + (price_change * 0.3)
            else:
                side = "BUY_NO"
                projected = (1 - last_price) + (abs(price_change) * 0.3)

            edge = abs(price_change) * 0.3 * consistency
            market_price = self._get_price(market, side)
            # Willing to pay up a bit for momentum
            max_price = market_price + (edge * 0.5)

            if edge < 0.02:
                continue

            proposals.append(TradeProposal(
                strategy_name=self.name,
                market=market,
                condition_id=market.condition_id,
                token_id=self._get_token_id(market, side),
                side=side,
                fair_value=projected,
                market_price=market_price,
                edge=edge,
                confidence=consistency * 0.7,
                suggested_size_usd=min(
                    edge * 150, settings.max_position_size_usd * 0.5),
                max_price=min(max_price, 0.95),
                reasoning=(
                    f"Momentum: {price_change:+.3f} over {LOOKBACK_MINUTES}min, "
                    f"consistency {consistency:.0%}"
                ),
                urgency=0.8,  # Momentum is time-sensitive
                metadata={
                    "strategy_type": "momentum",
                    "price_change": price_change,
                    "consistency": consistency,
                    "data_points": len(recent),
                },
            ))

        return proposals
