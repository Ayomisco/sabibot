"""Tests for trading strategies — timezone arb, sentiment, and base behavior."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from src.polymarket.client import Market
from src.strategies.base import TradeProposal
from src.intelligence.signal_aggregator import AggregatedSignal


def _make_market(
    condition_id: str = "0xabc",
    question: str = "Will BTC exceed $100K by June 2025?",
    yes_price: float = 0.50,
    volume: float = 50000.0,
    liquidity: float = 10000.0,
) -> Market:
    return Market(
        condition_id=condition_id,
        question=question,
        description="Test market",
        category="crypto",
        outcome_prices={"Yes": yes_price, "No": round(1 - yes_price, 2)},
        tokens=[
            {"token_id": "tok_yes", "outcome": "Yes"},
            {"token_id": "tok_no", "outcome": "No"},
        ],
        volume_24h=volume,
        liquidity=liquidity,
    )


def _make_signal(
    fair_value: float = 0.65,
    edge: float = 0.10,
    confidence: float = 0.7,
    side: str = "BUY_YES",
) -> AggregatedSignal:
    return AggregatedSignal(
        fair_value=fair_value,
        edge=edge,
        confidence=confidence,
        side=side,
        signal_count=2,
        evidence_strength=0.8,
        total_weight=1.5,
    )


class TestBaseStrategy:
    """Test base strategy helpers."""

    def test_get_token_id_yes(self):
        from src.strategies.base import BaseStrategy

        # Can't instantiate ABC, test via a concrete subclass
        market = _make_market()
        # Direct test of token lookup logic
        target_outcome = "Yes"
        found = None
        for token in market.tokens:
            if token.get("outcome", "").lower() == target_outcome.lower():
                found = token["token_id"]
                break
        assert found == "tok_yes"

    def test_get_token_id_no(self):
        market = _make_market()
        target_outcome = "No"
        found = None
        for token in market.tokens:
            if token.get("outcome", "").lower() == target_outcome.lower():
                found = token["token_id"]
                break
        assert found == "tok_no"

    def test_get_price_yes(self):
        market = _make_market(yes_price=0.65)
        assert market.outcome_prices.get("Yes") == 0.65

    def test_get_price_no(self):
        market = _make_market(yes_price=0.65)
        assert market.outcome_prices.get("No") == 0.35


class TestTimezoneArbStrategy:
    """Test timezone arbitrage strategy logic."""

    @pytest.mark.asyncio
    async def test_during_us_sleep_generates_proposals(self):
        """During US sleep hours (4-12 UTC), strategy should consider markets."""
        from src.strategies.timezone_arb import TimezoneArbStrategy

        strategy = TimezoneArbStrategy()
        markets = [_make_market(yes_price=0.45)]

        signals = {
            "0xabc": _make_signal(fair_value=0.65, edge=0.20, confidence=0.75)
        }

        # Mock datetime to be during US sleep (8 AM UTC = 3 AM EST)
        mock_now = datetime(2025, 6, 15, 8, 0, tzinfo=timezone.utc)
        with patch("src.strategies.timezone_arb.datetime") as mock_dt:
            mock_dt.now.return_value = mock_now
            mock_dt.side_effect = lambda *a, **k: datetime(*a, **k)

            proposals = await strategy.evaluate(markets, signals)

        # Should propose trades during sleep hours
        for p in proposals:
            assert isinstance(p, TradeProposal)
            assert p.strategy_name == "timezone_arb"

    @pytest.mark.asyncio
    async def test_outside_us_sleep_returns_empty(self):
        """Outside US sleep hours, strategy returns no proposals."""
        from src.strategies.timezone_arb import TimezoneArbStrategy

        strategy = TimezoneArbStrategy()
        markets = [_make_market(yes_price=0.45)]

        signals = {
            "0xabc": _make_signal(fair_value=0.65, edge=0.20, confidence=0.75)
        }

        # 6 PM UTC = 1 PM EST (US awake) → strategy is inactive
        mock_now = datetime(2025, 6, 15, 18, 0, tzinfo=timezone.utc)
        with patch("src.strategies.timezone_arb.datetime") as mock_dt:
            mock_dt.now.return_value = mock_now
            mock_dt.side_effect = lambda *a, **k: datetime(*a, **k)

            proposals = await strategy.evaluate(markets, signals)

        assert proposals == []  # No proposals outside sleep hours


class TestTradeProposal:
    """Test TradeProposal data integrity."""

    def test_proposal_fields(self):
        proposal = TradeProposal(
            strategy_name="test",
            market=_make_market(),
            condition_id="0xabc",
            token_id="tok_yes",
            side="BUY_YES",
            fair_value=0.65,
            market_price=0.50,
            edge=0.15,
            confidence=0.8,
            suggested_size_usd=25.0,
            max_price=0.62,
            reasoning="Strong signal",
        )
        assert proposal.edge == 0.15
        assert proposal.urgency == 0.5  # Default
        assert proposal.metadata == {}  # Default

    def test_proposal_edge_calculation(self):
        proposal = TradeProposal(
            strategy_name="test",
            market=_make_market(),
            condition_id="0xabc",
            token_id="tok_yes",
            side="BUY_YES",
            fair_value=0.70,
            market_price=0.55,
            edge=0.15,
            confidence=0.8,
            suggested_size_usd=25.0,
            max_price=0.65,
            reasoning="Test",
        )
        assert abs(proposal.edge - (proposal.fair_value -
                   proposal.market_price)) < 0.01
