"""
Cross-Market Arbitrage Strategy.

CONCEPT:
  Polymarket often has multiple related markets that should move together.
  Example: "Will Biden win 2024?" and "Will a Democrat win 2024?"
  If Biden's market drops but the Democrat market hasn't adjusted,
  there's an arbitrage opportunity.

IMPLEMENTATION:
  1. Group markets by topic/event (using embeddings)
  2. Detect price discrepancies between correlated markets
  3. Trade the lagging market toward the implied price

EDGE SOURCE:
  - Market fragmentation: related markets don't always update simultaneously
  - Mathematical relationship: P(Democrat win) >= P(Biden win)
"""

from __future__ import annotations

from src.ai.embeddings import cosine_similarity, embed_texts
from src.polymarket.client import Market
from src.intelligence.signal_aggregator import AggregatedSignal
from src.strategies.base import BaseStrategy, TradeProposal
from src.config import settings
from src.utils.logger import get_logger

log = get_logger("strategy.cross_market")

# Minimum similarity to consider markets "related"
CORRELATION_THRESHOLD = 0.75
# Minimum price discrepancy to trade
MIN_DISCREPANCY = 0.06


class CrossMarketArbStrategy(BaseStrategy):
    def __init__(self) -> None:
        super().__init__(name="cross_market_arb")
        self._embedding_cache: dict[str, list[float]] = {}

    async def evaluate(
        self,
        markets: list[Market],
        signals: dict[str, AggregatedSignal] | None = None,
    ) -> list[TradeProposal]:
        if len(markets) < 2:
            return []

        # Build/update embedding cache for market questions
        uncached = [m for m in markets if m.condition_id not in self._embedding_cache]
        if uncached:
            texts = [m.question for m in uncached]
            embeddings = await embed_texts(texts)
            for m, emb in zip(uncached, embeddings):
                self._embedding_cache[m.condition_id] = emb

        # Find correlated pairs with price discrepancies
        proposals: list[TradeProposal] = []
        checked: set[tuple[str, str]] = set()

        for i, m1 in enumerate(markets):
            emb1 = self._embedding_cache.get(m1.condition_id)
            if emb1 is None:
                continue

            for m2 in markets[i + 1:]:
                pair_key = tuple(sorted([m1.condition_id, m2.condition_id]))
                if pair_key in checked:
                    continue
                checked.add(pair_key)

                emb2 = self._embedding_cache.get(m2.condition_id)
                if emb2 is None:
                    continue

                sim = cosine_similarity(emb1, emb2)
                if sim < CORRELATION_THRESHOLD:
                    continue

                # Found correlated markets — check for price discrepancy
                p1 = m1.outcome_prices.get("Yes", 0.5)
                p2 = m2.outcome_prices.get("Yes", 0.5)
                discrepancy = abs(p1 - p2)

                if discrepancy < MIN_DISCREPANCY:
                    continue

                # Trade the cheaper one toward the more expensive
                # (assuming correlated markets should converge)
                if p1 < p2:
                    cheap, expensive = m1, m2
                    cheap_price, exp_price = p1, p2
                else:
                    cheap, expensive = m2, m1
                    cheap_price, exp_price = p2, p1

                implied_fair = (cheap_price + exp_price) / 2
                edge = implied_fair - cheap_price

                if edge < MIN_DISCREPANCY / 2:
                    continue

                proposals.append(TradeProposal(
                    strategy_name=self.name,
                    market=cheap,
                    condition_id=cheap.condition_id,
                    token_id=self._get_token_id(cheap, "BUY_YES"),
                    side="BUY_YES",
                    fair_value=implied_fair,
                    market_price=cheap_price,
                    edge=edge,
                    confidence=sim * 0.8,  # Confidence proportional to correlation
                    suggested_size_usd=min(edge * 200, settings.max_position_size_usd),
                    max_price=implied_fair - 0.01,
                    reasoning=(
                        f"Cross-market arb: '{cheap.question[:40]}' at {cheap_price:.2f} "
                        f"vs correlated '{expensive.question[:40]}' at {exp_price:.2f} "
                        f"(similarity: {sim:.2f})"
                    ),
                    urgency=0.7,
                    metadata={
                        "strategy_type": "cross_market_arb",
                        "correlated_market": expensive.condition_id,
                        "correlation": sim,
                    },
                ))

                log.info(
                    "cross_market_opportunity",
                    cheap=cheap.question[:40],
                    expensive=expensive.question[:40],
                    discrepancy=f"{discrepancy:.3f}",
                    similarity=f"{sim:.2f}",
                )

        return proposals
