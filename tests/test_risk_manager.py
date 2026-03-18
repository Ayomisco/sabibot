"""Tests for the risk manager — position sizing, drawdown, exposure limits."""

from unittest.mock import patch

from src.execution.risk_manager import RiskManager, RiskCheck
from src.strategies.base import TradeProposal
from src.polymarket.client import Market


def _make_market(condition_id: str = "cond_abc") -> Market:
    return Market(
        condition_id=condition_id,
        question="Will X happen by end of 2025?",
        outcome_prices={"Yes": 0.50, "No": 0.50},
        tokens=[
            {"token_id": "tok_yes", "outcome": "Yes"},
            {"token_id": "tok_no", "outcome": "No"},
        ],
        volume_24h=50000.0,
        liquidity=10000.0,
    )


def _make_proposal(
    edge: float = 0.10,
    confidence: float = 0.7,
    suggested_size_usd: float = 20.0,
    max_price: float = 0.60,
    side: str = "BUY_YES",
    market_price: float = 0.50,
    fair_value: float = 0.60,
    condition_id: str = "cond_abc",
) -> TradeProposal:
    return TradeProposal(
        strategy_name="test_strategy",
        market=_make_market(condition_id),
        condition_id=condition_id,
        token_id="tok_yes",
        side=side,
        fair_value=fair_value,
        market_price=market_price,
        edge=edge,
        confidence=confidence,
        suggested_size_usd=suggested_size_usd,
        max_price=max_price,
        reasoning="Test reasoning.",
        urgency=0.5,
        metadata={},
    )


# Patch settings for all tests so we don't need a .env file
_SETTINGS_PATCH = {
    "min_edge_threshold": 0.05,
    "min_confidence": 0.3,
    "max_position_size_usd": 50.0,
    "max_portfolio_exposure_usd": 500.0,
    "max_single_market_pct": 0.30,
    "max_drawdown_pct": 0.20,
    "kelly_multiplier": 0.25,
}


def _patch_settings(**overrides):
    """Return a dict-based mock settings object."""
    vals = {**_SETTINGS_PATCH, **overrides}

    class _S:
        pass

    s = _S()
    for k, v in vals.items():
        setattr(s, k, v)
    return s


class TestKellySizing:
    """Test Kelly criterion position sizing."""

    def test_basic_kelly(self):
        s = _patch_settings()
        with patch("src.execution.risk_manager.settings", s):
            rm = RiskManager()
            # edge=0.10, price=0.50 → p=0.60, q=0.40, b=1.0
            # kelly = (1.0*0.60 - 0.40)/1.0 = 0.20
            # fraction = 0.20 * 0.25(kelly_mult) * 0.7(confidence) = 0.035
            # size = 0.035 * 500 = 17.50
            size = rm._kelly_position_size(edge=0.10, price=0.50, confidence=0.70)
            assert size > 0
            assert size <= 50.0

    def test_negative_edge_returns_zero(self):
        s = _patch_settings()
        with patch("src.execution.risk_manager.settings", s):
            rm = RiskManager()
            size = rm._kelly_position_size(edge=-0.10, price=0.50, confidence=0.70)
            assert size == 0.0

    def test_zero_confidence_returns_zero(self):
        s = _patch_settings()
        with patch("src.execution.risk_manager.settings", s):
            rm = RiskManager()
            size = rm._kelly_position_size(edge=0.10, price=0.50, confidence=0.0)
            assert size == 0.0

    def test_kelly_capped_at_max(self):
        s = _patch_settings(max_position_size_usd=10.0, max_portfolio_exposure_usd=10000.0)
        with patch("src.execution.risk_manager.settings", s):
            rm = RiskManager()
            # Huge edge → large kelly, but check_proposal caps the final size
            proposal = _make_proposal(edge=0.30, fair_value=0.80, confidence=0.95)
            result = rm.check_proposal(proposal)
            if result.approved:
                assert result.adjusted_size_usd <= 10.0


class TestDrawdown:
    """Test drawdown halt mechanism."""

    def test_no_drawdown_no_halt(self):
        s = _patch_settings()
        with patch("src.execution.risk_manager.settings", s):
            rm = RiskManager()
            rm.update_portfolio_value(1000.0)
            assert not rm.is_halted

    def test_drawdown_triggers_halt(self):
        s = _patch_settings(max_drawdown_pct=0.20)
        with patch("src.execution.risk_manager.settings", s):
            rm = RiskManager()
            rm.update_portfolio_value(1000.0)  # Peak
            rm.update_portfolio_value(790.0)   # 21% drawdown → halt
            assert rm.is_halted

    def test_drawdown_within_limit_no_halt(self):
        s = _patch_settings(max_drawdown_pct=0.20)
        with patch("src.execution.risk_manager.settings", s):
            rm = RiskManager()
            rm.update_portfolio_value(1000.0)
            rm.update_portfolio_value(850.0)  # 15% drawdown → OK
            assert not rm.is_halted

    def test_reset_clears_halt(self):
        s = _patch_settings(max_drawdown_pct=0.20)
        with patch("src.execution.risk_manager.settings", s):
            rm = RiskManager()
            rm.update_portfolio_value(1000.0)
            rm.update_portfolio_value(790.0)
            assert rm.is_halted
            rm.reset_drawdown_halt()
            assert not rm.is_halted


class TestCheckProposal:
    """Test the full 7-check pipeline via check_proposal()."""

    def test_edge_too_small_rejected(self):
        s = _patch_settings(min_edge_threshold=0.08)
        with patch("src.execution.risk_manager.settings", s):
            rm = RiskManager()
            proposal = _make_proposal(edge=0.03)
            result = rm.check_proposal(proposal)
            assert not result.approved
            assert "edge" in result.rejection_reason.lower()

    def test_confidence_too_low_rejected(self):
        s = _patch_settings(min_confidence=0.6)
        with patch("src.execution.risk_manager.settings", s):
            rm = RiskManager()
            proposal = _make_proposal(confidence=0.2)
            result = rm.check_proposal(proposal)
            assert not result.approved
            assert "confidence" in result.rejection_reason.lower()

    def test_portfolio_exposure_exceeded(self):
        s = _patch_settings(max_portfolio_exposure_usd=100.0)
        with patch("src.execution.risk_manager.settings", s):
            rm = RiskManager()
            # Fill up exposure
            rm.record_trade("other_market", 100.0)
            proposal = _make_proposal()
            result = rm.check_proposal(proposal)
            assert not result.approved
            assert "exposure" in result.rejection_reason.lower() or "limit" in result.rejection_reason.lower()

    def test_market_concentration_exceeded(self):
        s = _patch_settings(max_single_market_pct=0.05, max_portfolio_exposure_usd=1000.0)
        with patch("src.execution.risk_manager.settings", s):
            rm = RiskManager()
            # Already have $50 in this market (== 5% of 1000)
            rm.record_trade("cond_abc", 50.0)
            proposal = _make_proposal(condition_id="cond_abc")
            result = rm.check_proposal(proposal)
            assert not result.approved
            assert "concentration" in result.rejection_reason.lower() or "market" in result.rejection_reason.lower()

    def test_good_proposal_approved(self):
        s = _patch_settings()
        with patch("src.execution.risk_manager.settings", s):
            rm = RiskManager()
            proposal = _make_proposal(edge=0.10, confidence=0.7)
            result = rm.check_proposal(proposal)
            assert result.approved
            assert result.adjusted_size_usd > 0
            assert result.adjusted_size_usd <= 50.0

    def test_drawdown_halt_rejects_everything(self):
        s = _patch_settings(max_drawdown_pct=0.20)
        with patch("src.execution.risk_manager.settings", s):
            rm = RiskManager()
            rm.update_portfolio_value(1000.0)
            rm.update_portfolio_value(790.0)  # Trigger halt
            proposal = _make_proposal(edge=0.15, confidence=0.9)
            result = rm.check_proposal(proposal)
            assert not result.approved
            assert "drawdown" in result.rejection_reason.lower()

    def test_tiny_kelly_below_min_rejected(self):
        s = _patch_settings(min_edge_threshold=0.01, min_confidence=0.1)
        with patch("src.execution.risk_manager.settings", s):
            rm = RiskManager()
            # Very small edge + low confidence → kelly < $1
            proposal = _make_proposal(edge=0.02, confidence=0.12)
            result = rm.check_proposal(proposal)
            # Either rejected for size or approved with >= $1
            if result.approved:
                assert result.adjusted_size_usd >= 1.0

    def test_record_and_exit_exposure(self):
        s = _patch_settings()
        with patch("src.execution.risk_manager.settings", s):
            rm = RiskManager()
            rm.record_trade("cond_abc", 30.0)
            assert rm.get_status()["total_exposure"] == 30.0
            rm.record_exit("cond_abc", 30.0)
            assert rm.get_status()["total_exposure"] == 0.0
