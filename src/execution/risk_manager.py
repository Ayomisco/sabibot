"""
Risk Manager — the guardrail between strategy signals and real money.

Every trade proposal passes through here before execution. The risk manager
enforces hard limits that CANNOT be overridden by strategies:

RULES (non-negotiable):
  1. Max single position: max_position_size_usd (default $50)
  2. Max portfolio exposure: max_portfolio_exposure_usd (default $500)
  3. Max per-market concentration: max_single_market_pct (default 5%)
  4. Max drawdown hard stop: max_drawdown_pct (default 20%)
  5. No trading if drawdown limit is hit (until manual reset)
  6. Kelly criterion position sizing with conservative multiplier

POSITION SIZING — KELLY CRITERION:
  The Kelly criterion maximizes long-term growth rate.
    f* = (bp - q) / b
  where:
    b = decimal odds (payout if win)
    p = probability of winning (our estimate)
    q = probability of losing = 1 - p

  We use fractional Kelly (default 0.25 = quarter Kelly) because:
    - Full Kelly is too volatile for prediction markets
    - Quarter Kelly has ~94% of the growth rate with ~50% of the variance
"""

from __future__ import annotations

from dataclasses import dataclass

from src.config import settings
from src.strategies.base import TradeProposal
from src.utils.logger import get_logger

log = get_logger("risk_manager")


@dataclass
class RiskCheck:
    """Result of passing a trade through risk management."""
    approved: bool
    adjusted_size_usd: float
    rejection_reason: str = ""
    kelly_fraction: float = 0.0
    portfolio_utilization: float = 0.0


class RiskManager:
    """
    Enforces risk limits and computes final position sizes.
    Stateful — tracks current portfolio exposure and drawdown.
    """

    def __init__(self) -> None:
        self._total_exposure: float = 0.0
        # condition_id → USD exposure
        self._market_exposure: dict[str, float] = {}
        self._peak_portfolio_value: float = 0.0
        self._current_portfolio_value: float = 0.0
        self._drawdown_halt: bool = False

    def check_proposal(self, proposal: TradeProposal) -> RiskCheck:
        """
        Run all risk checks on a trade proposal.
        Returns adjusted size or rejection.
        """
        # ── Check 1: Drawdown hard stop ──────────────────────────
        if self._drawdown_halt:
            return RiskCheck(
                approved=False,
                adjusted_size_usd=0.0,
                rejection_reason="DRAWDOWN_HALT: Max drawdown exceeded. Manual reset required.",
            )

        # ── Check 2: Minimum edge and confidence ────────────────
        if abs(proposal.edge) < settings.min_edge_threshold:
            return RiskCheck(
                approved=False,
                adjusted_size_usd=0.0,
                rejection_reason=f"Edge {proposal.edge:.3f} below min {settings.min_edge_threshold}",
            )

        if proposal.confidence < settings.min_confidence:
            return RiskCheck(
                approved=False,
                adjusted_size_usd=0.0,
                rejection_reason=f"Confidence {proposal.confidence:.2f} below min {settings.min_confidence}",
            )

        # ── Check 3: Kelly criterion position sizing ─────────────
        kelly_size = self._kelly_position_size(
            edge=proposal.edge,
            price=proposal.market_price,
            confidence=proposal.confidence,
        )

        # ── Check 4: Cap by maximum position size ────────────────
        size = min(kelly_size, proposal.suggested_size_usd,
                   settings.max_position_size_usd)

        # ── Check 5: Cap by portfolio exposure limit ─────────────
        remaining_capacity = settings.max_portfolio_exposure_usd - self._total_exposure
        if remaining_capacity <= 0:
            return RiskCheck(
                approved=False,
                adjusted_size_usd=0.0,
                rejection_reason="Portfolio exposure limit reached",
                portfolio_utilization=1.0,
            )
        size = min(size, remaining_capacity)

        # ── Check 6: Per-market concentration limit ──────────────
        current_market_exp = self._market_exposure.get(
            proposal.condition_id, 0.0)
        max_market_exp = settings.max_portfolio_exposure_usd * \
            settings.max_single_market_pct
        market_remaining = max_market_exp - current_market_exp
        if market_remaining <= 0:
            return RiskCheck(
                approved=False,
                adjusted_size_usd=0.0,
                rejection_reason=f"Market concentration limit: {current_market_exp:.2f}/{max_market_exp:.2f}",
            )
        size = min(size, market_remaining)

        # ── Check 7: Minimum viable trade size ───────────────────
        if size < 1.0:  # Don't bother with <$1 trades
            return RiskCheck(
                approved=False,
                adjusted_size_usd=0.0,
                rejection_reason=f"Position size ${size:.2f} below minimum $1.00",
            )

        utilization = self._total_exposure / \
            max(settings.max_portfolio_exposure_usd, 1)

        log.info(
            "risk_approved",
            strategy=proposal.strategy_name,
            market=proposal.market.question[:40],
            kelly_size=f"${kelly_size:.2f}",
            final_size=f"${size:.2f}",
            utilization=f"{utilization:.0%}",
        )

        return RiskCheck(
            approved=True,
            adjusted_size_usd=round(size, 2),
            kelly_fraction=kelly_size /
            max(settings.max_portfolio_exposure_usd, 1),
            portfolio_utilization=utilization,
        )

    def _kelly_position_size(self, edge: float, price: float, confidence: float) -> float:
        """
        Kelly criterion position sizing with fractional scaling.

        f* = (b*p - q) / b * kelly_multiplier * confidence * bankroll

        where:
          b = decimal odds = (1/price) - 1
          p = estimated true probability = price + edge
          q = 1 - p
        """
        p = max(0.01, min(0.99, price + edge))
        q = 1 - p
        b = max(0.01, (1 / max(0.01, price)) - 1)

        kelly = (b * p - q) / b

        if kelly <= 0:
            return 0.0

        # Apply conservative multiplier (default 0.25 = quarter Kelly)
        fraction = kelly * settings.kelly_multiplier * confidence

        # Convert fraction to dollar size
        bankroll = settings.max_portfolio_exposure_usd
        size = fraction * bankroll

        return max(0.0, size)

    # ── State management ─────────────────────────────────────────

    def record_trade(self, condition_id: str, amount_usd: float) -> None:
        """Update exposure tracking after a trade is placed."""
        self._total_exposure += amount_usd
        self._market_exposure[condition_id] = (
            self._market_exposure.get(condition_id, 0.0) + amount_usd
        )

    def record_exit(self, condition_id: str, amount_usd: float) -> None:
        """Update exposure tracking after closing a position."""
        self._total_exposure = max(0, self._total_exposure - amount_usd)
        current = self._market_exposure.get(condition_id, 0.0)
        self._market_exposure[condition_id] = max(0, current - amount_usd)

    def update_portfolio_value(self, total_value: float) -> None:
        """
        Update portfolio value and check drawdown.
        Call this periodically (e.g., every hour).
        """
        self._current_portfolio_value = total_value
        if total_value > self._peak_portfolio_value:
            self._peak_portfolio_value = total_value

        if self._peak_portfolio_value > 0:
            drawdown = (self._peak_portfolio_value - total_value) / \
                self._peak_portfolio_value
            if drawdown >= settings.max_drawdown_pct:
                self._drawdown_halt = True
                log.error(
                    "DRAWDOWN_HALT",
                    drawdown=f"{drawdown:.1%}",
                    peak=f"${self._peak_portfolio_value:.2f}",
                    current=f"${total_value:.2f}",
                    msg="Trading halted. Manual reset required.",
                )

    def reset_drawdown_halt(self) -> None:
        """Manual reset after drawdown halt. Called via Telegram /reset command."""
        self._drawdown_halt = False
        self._peak_portfolio_value = self._current_portfolio_value
        log.warning("drawdown_halt_reset",
                    new_peak=f"${self._current_portfolio_value:.2f}")

    @property
    def is_halted(self) -> bool:
        return self._drawdown_halt

    @property
    def current_drawdown(self) -> float:
        if self._peak_portfolio_value <= 0:
            return 0.0
        return (self._peak_portfolio_value - self._current_portfolio_value) / self._peak_portfolio_value

    def get_status(self) -> dict:
        return {
            "total_exposure": self._total_exposure,
            "portfolio_value": self._current_portfolio_value,
            "peak_value": self._peak_portfolio_value,
            "drawdown": self.current_drawdown,
            "halted": self._drawdown_halt,
            "market_count": len(self._market_exposure),
        }


# Singleton
risk_manager = RiskManager()
