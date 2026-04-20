"""
Scalp Strategy — quick 15-minute in/out on liquid markets.

CONCEPT:
  Enter on any directional signal with sufficient liquidity.
  The ExitManager closes the position after `scalp_hold_seconds` (default 15 min)
  regardless of outcome — capturing short-term price momentum without waiting
  for full market resolution.

  Think of it as copy-trading the immediate news reaction: we enter fast,
  ride any price movement for 15 min, then cash out and play the next one.

EDGE SOURCE:
  - Fast reaction to news before manual traders reprice
  - Short exposure window = less time at risk from adverse resolution moves
  - Targets only high-liquidity markets where we can exit cleanly

ENTRY CRITERIA:
  1. 24h volume  >= scalp_min_volume_usd  (default $5 000) — exit liquidity
  2. Liquidity   >= scalp_min_liquidity_usd (default $2 000) — tight spread
  3. Edge        >= 3%  (lower bar than regular strategies — hold time is short)
  4. Confidence  >= 50%
  5. YES price in 15%–85% range — avoids near-resolved or longshot markets
     that are hard to exit profitably
"""

from __future__ import annotations

from src.config import settings
from src.intelligence.signal_aggregator import AggregatedSignal
from src.polymarket.client import Market
from src.strategies.base import BaseStrategy, TradeProposal
from src.utils.logger import get_logger

log = get_logger("strategy.scalp")

_SCALP_MIN_EDGE = 0.03
_SCALP_MIN_CONFIDENCE = 0.50
_PRICE_RANGE = (0.15, 0.85)  # avoid extremes — hard to exit near 0 or 1


class ScalpStrategy(BaseStrategy):
    """15-minute scalp: enter on signal, exit_manager closes after hold time."""

    def __init__(self) -> None:
        super().__init__(name="scalp")

    async def evaluate(
        self,
        markets: list[Market],
        signals: dict[str, AggregatedSignal] | None = None,
    ) -> list[TradeProposal]:
        if not signals:
            return []

        proposals: list[TradeProposal] = []

        for condition_id, agg in signals.items():
            # Edge and confidence gates (relaxed — short hold compensates)
            if abs(agg.edge) < _SCALP_MIN_EDGE:
                continue
            if agg.confidence < _SCALP_MIN_CONFIDENCE:
                continue
            if agg.side == "NEUTRAL":
                continue

            market = next(
                (m for m in markets if m.condition_id == condition_id), None
            )
            if market is None:
                continue

            # Liquidity gates — we MUST be able to exit cleanly
            if market.volume_24h < settings.scalp_min_volume_usd:
                continue
            if market.liquidity < settings.scalp_min_liquidity_usd:
                continue

            # Stay away from prices near resolution — impossible to profit quickly
            yes_price = market.outcome_prices.get("Yes", 0.5)
            if not (_PRICE_RANGE[0] <= yes_price <= _PRICE_RANGE[1]):
                continue

            side = agg.side
            market_price = self._get_price(market, side)

            # Accept up to 1% slippage on entry — scalps need to fill immediately
            max_price = min(market_price * 1.01, 0.95)

            # Use half of normal max position size — scalps are higher turnover
            size_usd = settings.max_position_size_usd * 0.5

            proposals.append(
                TradeProposal(
                    strategy_name=self.name,
                    market=market,
                    condition_id=condition_id,
                    token_id=self._get_token_id(market, side),
                    side=side,
                    fair_value=agg.fair_value,
                    market_price=market_price,
                    edge=agg.edge,
                    confidence=agg.confidence,
                    suggested_size_usd=size_usd,
                    max_price=max_price,
                    reasoning=(
                        f"Scalp {side} | edge={agg.edge:+.3f} "
                        f"conf={agg.confidence:.2f} | {agg.reasoning[:160]}"
                    ),
                    urgency=0.9,  # high — needs to fill fast
                    metadata={
                        "scalp": True,
                        "hold_seconds": settings.scalp_hold_seconds,
                    },
                )
            )

        return proposals
