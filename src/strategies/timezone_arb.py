"""
Timezone Arbitrage Strategy — the @0x_Discover $43.8K playbook.

CONCEPT:
  Global news breaks 24/7. US traders sleep 11PM-7AM EST.
  During those hours, prediction markets react slowly to non-US news
  because the dominant trading population is offline.

  This strategy:
  1. Monitors news from non-US timezones during US sleep hours
  2. Identifies markets that should move but haven't yet
  3. Buys before the US wakes up and corrects the price

WHY IT WORKS:
  Polymarket's liquidity is ~70% US-based. When something happens in
  Europe/Asia/Africa at 3AM EST, the order book is thin and prices
  are stale. The edge disappears by ~8AM EST when US traders arrive.

RISK:
  - Edge window is 2-8 hours (must execute quickly)
  - False positives: news that seems impactful but isn't
  - Thin liquidity = wider spreads during these hours
"""

from __future__ import annotations

from datetime import datetime, timezone

from src.config import settings
from src.intelligence.signal_aggregator import AggregatedSignal
from src.polymarket.client import Market
from src.strategies.base import BaseStrategy, TradeProposal
from src.utils.logger import get_logger

log = get_logger("strategy.timezone_arb")

# US East Coast sleep hours in UTC (11PM-7AM EST = 4AM-12PM UTC)
US_SLEEP_START_UTC = 4   # 4:00 UTC = 11:00 PM EST
US_SLEEP_END_UTC = 12    # 12:00 UTC = 7:00 AM EST


class TimezoneArbStrategy(BaseStrategy):
    def __init__(self) -> None:
        super().__init__(name="timezone_arb")

    async def evaluate(
        self,
        markets: list[Market],
        signals: dict[str, AggregatedSignal] | None = None,
    ) -> list[TradeProposal]:
        """Only produce proposals during US sleep hours when signals show edge."""
        if not self._is_us_sleep_hours():
            log.debug("timezone_arb_inactive", reason="outside_us_sleep_hours")
            return []

        if not signals:
            return []

        proposals: list[TradeProposal] = []

        for condition_id, agg_signal in signals.items():
            # Need meaningful edge and confidence
            if abs(agg_signal.edge) < settings.min_edge_threshold:
                continue
            if agg_signal.confidence < settings.min_confidence:
                continue
            if agg_signal.side == "NEUTRAL":
                continue

            # Find the market
            market = next(
                (m for m in markets if m.condition_id == condition_id), None)
            if market is None:
                continue

            # Timezone arb gets urgency boost — edge is time-decaying
            urgency = min(1.0, agg_signal.confidence + 0.2)

            # Price we're willing to pay: our fair value minus a safety margin
            if agg_signal.side == "BUY_YES":
                max_price = min(agg_signal.fair_value - 0.02, 0.95)
                market_price = market.outcome_prices.get("Yes", 0.5)
            else:
                max_price = min((1 - agg_signal.fair_value) + 0.02, 0.95)
                market_price = market.outcome_prices.get("No", 0.5)

            proposals.append(TradeProposal(
                strategy_name=self.name,
                market=market,
                condition_id=condition_id,
                token_id=self._get_token_id(market, agg_signal.side),
                side=agg_signal.side,
                fair_value=agg_signal.fair_value,
                market_price=market_price,
                edge=agg_signal.edge,
                confidence=agg_signal.confidence,
                suggested_size_usd=self._size_from_edge(
                    agg_signal.edge, agg_signal.confidence),
                max_price=max_price,
                reasoning=(
                    f"Timezone arb: US markets sleeping, edge of {agg_signal.edge:+.1%} "
                    f"detected from {agg_signal.signal_count} signals"
                ),
                urgency=urgency,
                metadata={"strategy_type": "timezone_arb",
                          "signal_count": agg_signal.signal_count},
            ))

            log.info(
                "timezone_arb_proposal",
                market=market.question[:60],
                edge=f"{agg_signal.edge:+.3f}",
                confidence=f"{agg_signal.confidence:.2f}",
            )

        return proposals

    def _is_us_sleep_hours(self) -> bool:
        """Check if we're in US sleep window (4AM-12PM UTC)."""
        now_utc = datetime.now(timezone.utc)
        return US_SLEEP_START_UTC <= now_utc.hour < US_SLEEP_END_UTC

    def _size_from_edge(self, edge: float, confidence: float) -> float:
        """
        Position size based on edge magnitude and confidence.
        Larger edge + higher confidence = bigger position.
        Capped by max_position_size_usd from config.
        """
        # Base size proportional to edge (10¢ edge = $30 position at default max)
        base = abs(edge) * 300
        # Scale by confidence
        sized = base * confidence
        return min(sized, settings.max_position_size_usd)
