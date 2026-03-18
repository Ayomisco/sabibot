"""
Base strategy interface — all strategies inherit from this.

A strategy receives signals and market data, then decides whether to trade.
Strategies do NOT place orders directly — they return TradeProposals
which the execution layer validates and executes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime

from src.intelligence.signal_aggregator import AggregatedSignal
from src.polymarket.client import Market


@dataclass
class TradeProposal:
    """A proposed trade from a strategy. The execution layer decides whether to act."""
    strategy_name: str
    market: Market
    condition_id: str
    token_id: str           # The specific token to buy (YES or NO)
    side: str               # "BUY_YES" or "BUY_NO"
    fair_value: float       # Our estimated true probability
    market_price: float     # Current market price
    edge: float             # fair_value - market_price
    confidence: float       # 0-1
    suggested_size_usd: float  # Strategy's suggested position size
    max_price: float        # Maximum price willing to pay
    reasoning: str          # Human-readable explanation
    urgency: float = 0.5   # 0-1, how quickly this needs to execute
    metadata: dict = field(default_factory=dict)


class BaseStrategy(ABC):
    """Abstract base for all trading strategies."""

    def __init__(self, name: str, enabled: bool = True) -> None:
        self.name = name
        self.enabled = enabled
        self._last_run: datetime | None = None

    @abstractmethod
    async def evaluate(
        self,
        markets: list[Market],
        signals: dict[str, AggregatedSignal] | None = None,
    ) -> list[TradeProposal]:
        """
        Evaluate markets and return trade proposals.

        Args:
            markets: Active markets to consider
            signals: Aggregated signals keyed by condition_id (optional)

        Returns:
            List of TradeProposal objects (may be empty)
        """
        ...

    def _get_token_id(self, market: Market, side: str) -> str:
        """Get the token_id for YES or NO side of a market."""
        target_outcome = "Yes" if "YES" in side else "No"
        for token in market.tokens:
            if token.get("outcome", "").lower() == target_outcome.lower():
                return token["token_id"]
        # Fallback: first token for YES, second for NO
        if market.tokens:
            idx = 0 if "YES" in side else min(1, len(market.tokens) - 1)
            return market.tokens[idx]["token_id"]
        return ""

    def _get_price(self, market: Market, side: str) -> float:
        """Get current price for a side."""
        if "YES" in side:
            return market.outcome_prices.get("Yes", 0.5)
        return market.outcome_prices.get("No", 0.5)
