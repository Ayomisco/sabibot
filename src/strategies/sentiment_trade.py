"""
Sentiment-Driven Trading Strategy.

CONCEPT:
  When strong, high-confidence sentiment signals emerge from news,
  trade the corresponding market before the price fully adjusts.

  Unlike timezone arb (which requires US sleep hours), this runs 24/7.
  It's looking for any news that the market hasn't priced in yet.

EDGE SOURCE:
  - Speed: we process news in ~30 seconds, manual traders take minutes
  - Analysis: LLM catches nuanced sentiment that keyword scanners miss
  - Breadth: we monitor 9+ sources simultaneously

ENTRY CRITERIA:
  1. Aggregated edge >= min_edge_threshold (default 8%)
  2. Confidence >= min_confidence (default 0.6)
  3. At least 1 corroborating signal
"""

from __future__ import annotations

from src.config import settings
from src.intelligence.signal_aggregator import AggregatedSignal
from src.polymarket.client import Market
from src.strategies.base import BaseStrategy, TradeProposal
from src.utils.logger import get_logger

log = get_logger("strategy.sentiment")


class SentimentTradeStrategy(BaseStrategy):
    def __init__(self) -> None:
        super().__init__(name="sentiment_trade")

    async def evaluate(
        self,
        markets: list[Market],
        signals: dict[str, AggregatedSignal] | None = None,
    ) -> list[TradeProposal]:
        if not signals:
            return []

        proposals: list[TradeProposal] = []

        for condition_id, agg in signals.items():
            if abs(agg.edge) < settings.min_edge_threshold:
                continue
            if agg.confidence < settings.min_confidence:
                continue
            if agg.side == "NEUTRAL":
                continue
            if agg.signal_count < 1:
                continue

            market = next(
                (m for m in markets if m.condition_id == condition_id), None)
            if market is None:
                continue

            # Avoid very illiquid markets
            if market.liquidity < 1000:
                continue

            side = agg.side
            market_price = self._get_price(market, side)

            # Safety margin: don't pay more than fair_value - 1.5%
            if side == "BUY_YES":
                max_price = min(agg.fair_value - 0.015, 0.95)
            else:
                max_price = min((1 - agg.fair_value) + 0.015, 0.95)

            proposals.append(TradeProposal(
                strategy_name=self.name,
                market=market,
                condition_id=condition_id,
                token_id=self._get_token_id(market, side),
                side=side,
                fair_value=agg.fair_value,
                market_price=market_price,
                edge=agg.edge,
                confidence=agg.confidence,
                suggested_size_usd=self._kelly_size(
                    agg.edge, market_price, agg.confidence),
                max_price=max_price,
                reasoning=(
                    f"Sentiment signal: {agg.signal_count} sources indicate "
                    f"{agg.edge:+.1%} edge, confidence {agg.confidence:.0%}"
                ),
                urgency=min(1.0, agg.evidence_strength),
                metadata={"strategy_type": "sentiment_trade"},
            ))

        return proposals

    def _kelly_size(self, edge: float, price: float, confidence: float) -> float:
        """
        Half-Kelly position sizing.

        Kelly fraction = (bp - q) / b
        where:
          b = odds (payout ratio) = (1/price) - 1
          p = our estimated probability of winning = price + edge
          q = 1 - p

        We use quarter-Kelly (kelly_multiplier=0.25) for safety.
        """
        p = max(0.01, min(0.99, price + edge))
        q = 1 - p
        b = max(0.01, (1 / max(0.01, price)) - 1)

        kelly = (b * p - q) / b
        if kelly <= 0:
            return 0.0

        # Scale by kelly_multiplier and confidence
        size = kelly * settings.max_portfolio_exposure_usd * \
            settings.kelly_multiplier * confidence
        return min(size, settings.max_position_size_usd)
