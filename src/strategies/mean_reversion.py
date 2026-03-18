"""
Mean Reversion Strategy.

CONCEPT:
  After a sharp price move in a prediction market, the price often
  overshoots and then reverts toward a "fair" value. This strategy
  fades (trades against) sharp moves.

  Example: A market drops from 0.70 to 0.55 in 30 minutes on news.
  If the news doesn't actually justify a 15¢ drop, the price will
  bounce back toward 0.62-0.65.

EDGE SOURCE:
  - Prediction markets overreact to news in the short term
  - Retail traders panic-sell/buy, creating temporary mispricings
  - The overreaction typically corrects within 2-12 hours

RISK:
  - Sometimes the move is justified and continues (trending market)
  - Must distinguish overreaction from justified repricing
  - Uses signals from the aggregator to avoid fading real news
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from src.config import settings
from src.intelligence.signal_aggregator import AggregatedSignal
from src.polymarket.client import Market
from src.strategies.base import BaseStrategy, TradeProposal
from src.utils.logger import get_logger

log = get_logger("strategy.mean_reversion")

# Minimum sharp move to consider fading
MIN_SHARP_MOVE = 0.08  # 8¢ move
LOOKBACK_HOURS = 6


class MeanReversionStrategy(BaseStrategy):
    def __init__(self) -> None:
        super().__init__(name="mean_reversion")
        # Store recent price baselines: condition_id -> (timestamp, baseline_price)
        self._baselines: dict[str, tuple[datetime, float]] = {}

    def update_baseline(self, condition_id: str, price: float) -> None:
        """Called periodically to track price baselines."""
        now = datetime.now(timezone.utc)
        existing = self._baselines.get(condition_id)
        if existing is None:
            self._baselines[condition_id] = (now, price)
            return
        # Update baseline slowly (EMA with alpha=0.1)
        _, old_price = existing
        new_baseline = old_price * 0.9 + price * 0.1
        self._baselines[condition_id] = (now, new_baseline)

    async def evaluate(
        self,
        markets: list[Market],
        signals: dict[str, AggregatedSignal] | None = None,
    ) -> list[TradeProposal]:
        proposals: list[TradeProposal] = []

        for market in markets:
            baseline_entry = self._baselines.get(market.condition_id)
            if baseline_entry is None:
                continue

            baseline_time, baseline_price = baseline_entry
            current_yes = market.outcome_prices.get("Yes", 0.5)
            deviation = current_yes - baseline_price

            if abs(deviation) < MIN_SHARP_MOVE:
                continue

            # Check if signals support the move — if they do, don't fade it
            if signals:
                agg = signals.get(market.condition_id)
                if agg:
                    # If signals agree with the move, skip (the move is justified)
                    if deviation > 0 and agg.edge > 0.03:
                        continue  # Price went up and signals say it should → justified
                    if deviation < 0 and agg.edge < -0.03:
                        continue  # Price went down and signals agree → justified

            # Fade the move — trade toward the baseline
            if deviation > 0:
                # Price spiked up → buy NO (bet on reversion down)
                side = "BUY_NO"
                # Expect 50% reversion toward baseline
                reversion_target = current_yes - (deviation * 0.5)
                edge = deviation * 0.5
                market_price = market.outcome_prices.get("No", 0.5)
                max_price = market_price + 0.03
            else:
                # Price dropped → buy YES (bet on reversion up)
                side = "BUY_YES"
                reversion_target = current_yes - (deviation * 0.5)
                edge = abs(deviation) * 0.5
                market_price = current_yes
                max_price = market_price + 0.03

            # Confidence is inversely proportional to how much signals support the move
            confidence = 0.55  # Base confidence for mean reversion

            proposals.append(TradeProposal(
                strategy_name=self.name,
                market=market,
                condition_id=market.condition_id,
                token_id=self._get_token_id(market, side),
                side=side,
                fair_value=reversion_target,
                market_price=market_price,
                edge=edge,
                confidence=confidence,
                suggested_size_usd=min(
                    edge * 150, settings.max_position_size_usd * 0.4),
                max_price=min(max_price, 0.95),
                reasoning=(
                    f"Mean reversion: price deviated {deviation:+.3f} from "
                    f"baseline {baseline_price:.3f}, expecting partial reversion"
                ),
                urgency=0.5,
                metadata={
                    "strategy_type": "mean_reversion",
                    "deviation": deviation,
                    "baseline": baseline_price,
                    "reversion_target": reversion_target,
                },
            ))

        return proposals
