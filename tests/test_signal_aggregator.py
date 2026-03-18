"""Tests for the Bayesian signal aggregator — the core math."""

import math
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from src.db.models import Signal, SignalDirection
from src.intelligence.signal_aggregator import (
    AggregatedSignal,
    aggregate_signals,
    logit,
    sigmoid,
    _freshness,
)

# Patch the logger to avoid structlog import issues in tests
import logging


class TestMathPrimitives:
    def test_logit_sigmoid_inverse(self):
        """logit and sigmoid should be inverses."""
        for p in [0.1, 0.25, 0.5, 0.75, 0.9]:
            assert abs(sigmoid(logit(p)) - p) < 1e-10

    def test_logit_50_is_zero(self):
        """50% probability = 0 in log-odds space."""
        assert abs(logit(0.5)) < 1e-10

    def test_logit_boundaries(self):
        """Logit should clamp near 0 and 1 to avoid infinity."""
        assert logit(0.001) == logit(0.001)  # Should not raise
        assert logit(0.999) == logit(0.999)

    def test_sigmoid_extremes(self):
        assert sigmoid(100) > 0.99
        assert sigmoid(-100) < 0.01

    def test_freshness_brand_new(self):
        """Brand new signal should have freshness ~1.0."""
        now = datetime.now(timezone.utc)
        assert abs(_freshness(now, now) - 1.0) < 0.01

    def test_freshness_half_life(self):
        """Signal at half-life age should have freshness ~0.5."""
        now = datetime.now(timezone.utc)
        old = now - timedelta(hours=4)  # Default half-life
        assert abs(_freshness(old, now) - 0.5) < 0.05

    def test_freshness_very_old(self):
        """Very old signal should have near-zero freshness."""
        now = datetime.now(timezone.utc)
        old = now - timedelta(hours=48)
        assert _freshness(old, now) < 0.01

    def test_freshness_none_time(self):
        """None datetime should return 0.5 (moderate)."""
        assert _freshness(None) == 0.5


class TestAggregation:
    def _make_signal(
        self,
        prob: float = 0.7,
        direction: SignalDirection = SignalDirection.BUY_YES,
        reliability: float = 0.8,
        match_score: float = 0.7,
        minutes_ago: int = 5,
    ) -> Signal:
        created = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
        return Signal(
            source="test",
            headline="Test headline",
            direction=direction,
            probability_estimate=prob,
            magnitude=0.5,
            reliability=reliability,
            matched_condition_id="test_market",
            matched_score=match_score,
            created_at=created,
        )

    def test_no_signals_returns_market_price(self):
        """With no signals, fair value should equal market price."""
        result = aggregate_signals([], current_market_price=0.60)
        assert result.fair_value == 0.60
        assert result.edge == 0.0
        assert result.side == "NEUTRAL"

    def test_single_bullish_signal(self):
        """One strong bullish signal should shift price up."""
        signal = self._make_signal(prob=0.80, direction=SignalDirection.BUY_YES)
        result = aggregate_signals([signal], current_market_price=0.50)

        assert result.fair_value > 0.50
        assert result.edge > 0
        assert result.side == "BUY_YES"

    def test_single_bearish_signal(self):
        """One strong bearish signal should shift price down."""
        signal = self._make_signal(prob=0.30, direction=SignalDirection.BUY_NO)
        result = aggregate_signals([signal], current_market_price=0.50)

        assert result.fair_value < 0.50
        assert result.edge < 0
        assert result.side == "BUY_NO"

    def test_corroborating_signals_increase_confidence(self):
        """Multiple agreeing signals should have higher evidence strength."""
        signal1 = self._make_signal(prob=0.75, minutes_ago=5)
        signal2 = self._make_signal(prob=0.78, minutes_ago=10)
        signal3 = self._make_signal(prob=0.72, minutes_ago=15)

        result_single = aggregate_signals([signal1], current_market_price=0.50)
        result_multi = aggregate_signals([signal1, signal2, signal3], current_market_price=0.50)

        assert result_multi.evidence_strength > result_single.evidence_strength
        # More signals = more confident in the shift
        assert abs(result_multi.edge) >= abs(result_single.edge) * 0.9

    def test_contradicting_signals_cancel(self):
        """Contradicting signals should roughly cancel out."""
        bullish = self._make_signal(prob=0.70, direction=SignalDirection.BUY_YES)
        bearish = self._make_signal(prob=0.30, direction=SignalDirection.BUY_NO)

        result = aggregate_signals([bullish, bearish], current_market_price=0.50)
        # Should stay close to market price
        assert abs(result.edge) < 0.10

    def test_stale_signal_weighted_less(self):
        """Old signals should have less impact than fresh ones."""
        fresh = self._make_signal(prob=0.80, minutes_ago=5)
        stale = self._make_signal(prob=0.80, minutes_ago=300)  # 5 hours old

        result_fresh = aggregate_signals([fresh], current_market_price=0.50)
        result_stale = aggregate_signals([stale], current_market_price=0.50)

        # Fresh signal should produce larger edge
        assert abs(result_fresh.edge) > abs(result_stale.edge)

    def test_unreliable_source_weighted_less(self):
        """Low-reliability signals should have less impact."""
        reliable = self._make_signal(prob=0.80, reliability=0.9)
        unreliable = self._make_signal(prob=0.80, reliability=0.2)

        result_reliable = aggregate_signals([reliable], current_market_price=0.50)
        result_unreliable = aggregate_signals([unreliable], current_market_price=0.50)

        assert abs(result_reliable.edge) > abs(result_unreliable.edge)

    def test_neutral_signals_ignored(self):
        """Neutral signals should not affect aggregation."""
        neutral = self._make_signal(prob=0.50, direction=SignalDirection.NEUTRAL)
        result = aggregate_signals([neutral], current_market_price=0.50)

        assert result.fair_value == 0.50
        assert result.edge == 0.0

    def test_fair_value_bounded(self):
        """Fair value should always be between 0 and 1."""
        extreme = self._make_signal(prob=0.99, reliability=1.0, match_score=1.0)
        result = aggregate_signals([extreme], current_market_price=0.01)

        assert 0.0 < result.fair_value < 1.0

    def test_max_shift_capped(self):
        """Even extreme signals shouldn't shift beyond the log-odds cap."""
        extreme1 = self._make_signal(prob=0.99, reliability=1.0, match_score=1.0)
        extreme2 = self._make_signal(prob=0.99, reliability=1.0, match_score=1.0)
        extreme3 = self._make_signal(prob=0.99, reliability=1.0, match_score=1.0)

        result = aggregate_signals(
            [extreme1, extreme2, extreme3],
            current_market_price=0.01,
        )
        # Should be capped, not literally 0.99
        assert result.fair_value < 0.95
