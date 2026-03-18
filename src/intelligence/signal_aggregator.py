"""
Signal aggregator — Bayesian fusion of multiple intelligence signals.

THE MATH:
=========
We combine multiple signals into one actionable trade recommendation using
log-odds Bayesian updating. This is mathematically principled, not arbitrary.

DEFINITIONS:
  - prior: The current market price (efficient market hypothesis = our starting belief)
  - signal: An intelligence output with a probability estimate and metadata
  - logit(p) = ln(p / (1-p))  — maps probability to log-odds space
  - sigmoid(x) = 1 / (1 + e^(-x))  — maps log-odds back to probability

ALGORITHM:
  1. Start with the market price as our prior (in log-odds space)
  2. Each signal shifts our belief proportional to:
     - How far it disagrees with the market (signal_logodds - prior_logodds)
     - Weighted by: freshness × reliability × match_quality
  3. The total shift is the weighted average of individual shifts,
     scaled by evidence strength (more corroborating signals = more confident)
  4. Convert back to probability space via sigmoid

WHY LOG-ODDS:
  - Probabilities near 0 or 1 are "sticky" — a small change from 0.95 to 0.97
    means more than 0.50 to 0.52. Log-odds space handles this naturally.
  - Bayesian updates are additive in log-odds space, making aggregation clean.

EVIDENCE STRENGTH SCALING:
  - 1 signal alone shouldn't move us much (could be noise)
  - 3+ corroborating signals = full confidence in the shift
  - Formula: evidence_strength = min(1.0, sqrt(total_weight) / 1.5)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone

from src.db.models import Signal, SignalDirection
from src.utils.logger import get_logger

log = get_logger("signal_aggregator")

# ── Tunable parameters ───────────────────────────────────────────
# How much we trust our signals vs the market
MAX_SHIFT_LOGODDS = 2.0        # Cap the maximum shift (prevents wild swings)
FRESHNESS_HALF_LIFE_HOURS = 4  # Signal loses 50% weight after this many hours
CORROBORATION_SCALE = 1.5      # Denominator for evidence strength sqrt formula


def logit(p: float) -> float:
    """Probability → log-odds. Clamps to avoid infinity."""
    p = max(0.001, min(0.999, p))
    return math.log(p / (1 - p))


def sigmoid(x: float) -> float:
    """Log-odds → probability."""
    return 1.0 / (1.0 + math.exp(-x))


def _freshness(signal_time: datetime | None, now: datetime | None = None) -> float:
    """
    Exponential time decay for signal freshness.
    Returns 1.0 for brand-new signals, decays to 0.5 after FRESHNESS_HALF_LIFE_HOURS.
    """
    if signal_time is None:
        return 0.5  # Unknown time = moderate freshness

    if now is None:
        now = datetime.now(timezone.utc)

    # Ensure timezone awareness
    if signal_time.tzinfo is None:
        signal_time = signal_time.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    age_hours = max(0, (now - signal_time).total_seconds() / 3600)
    # Exponential decay: freshness = 2^(-age / half_life)
    return math.pow(2, -age_hours / FRESHNESS_HALF_LIFE_HOURS)


@dataclass
class AggregatedSignal:
    """The output of signal aggregation — a single trade recommendation."""
    fair_value: float          # Our estimated true probability (0-1)
    edge: float                # fair_value - current_market_price
    confidence: float          # 0-1, how confident we are in the edge
    side: str                  # "BUY_YES" or "BUY_NO"
    signal_count: int          # How many signals contributed
    evidence_strength: float   # 0-1, based on signal corroboration
    total_weight: float        # Sum of all signal weights (for diagnostics)
    signals: list[Signal] = field(default_factory=list)


def aggregate_signals(
    signals: list[Signal],
    current_market_price: float,
    now: datetime | None = None,
) -> AggregatedSignal:
    """
    Bayesian signal aggregation.

    Takes multiple signals (each with a probability estimate, reliability, and timestamp)
    and combines them with the market price prior to produce a single fair value estimate.

    Args:
        signals: List of Signal objects with probability_estimate, reliability, matched_score, created_at
        current_market_price: Current YES price on the market (our prior)
        now: Current time (for freshness calculation, defaults to utcnow)

    Returns:
        AggregatedSignal with fair_value, edge, confidence, and side
    """
    if not signals:
        return AggregatedSignal(
            fair_value=current_market_price,
            edge=0.0,
            confidence=0.0,
            side="NEUTRAL",
            signal_count=0,
            evidence_strength=0.0,
            total_weight=0.0,
        )

    # Prior: market price in log-odds space
    prior_logodds = logit(current_market_price)

    total_shift = 0.0
    total_weight = 0.0
    valid_signals: list[Signal] = []

    for signal in signals:
        # Skip signals without probability estimates
        if signal.probability_estimate is None:
            continue

        # Skip neutral signals — they don't shift our belief
        if signal.direction == SignalDirection.NEUTRAL:
            continue

        # Compute signal weight: freshness × reliability × match_quality
        freshness = _freshness(signal.created_at, now)
        reliability = signal.reliability
        match_quality = signal.matched_score  # How well this signal matched the market

        weight = freshness * reliability * max(0.1, match_quality)

        if weight < 0.01:
            continue  # Too stale/unreliable to matter

        # How far this signal's estimate is from the market consensus (in log-odds)
        signal_logodds = logit(signal.probability_estimate)
        shift = signal_logodds - prior_logodds

        total_shift += shift * weight
        total_weight += weight
        valid_signals.append(signal)

    if total_weight == 0 or not valid_signals:
        return AggregatedSignal(
            fair_value=current_market_price,
            edge=0.0,
            confidence=0.0,
            side="NEUTRAL",
            signal_count=len(signals),
            evidence_strength=0.0,
            total_weight=0.0,
            signals=valid_signals,
        )

    # Weighted average shift in log-odds space
    avg_shift = total_shift / total_weight

    # Evidence strength: more corroborating signals = more confident
    # sqrt scaling: 1 signal → 0.67, 2 → 0.94, 3+ → ~1.0
    evidence_strength = min(1.0, math.sqrt(total_weight) / CORROBORATION_SCALE)

    # Apply evidence-scaled shift to prior, with cap
    adjusted_shift = avg_shift * evidence_strength
    adjusted_shift = max(-MAX_SHIFT_LOGODDS,
                         min(MAX_SHIFT_LOGODDS, adjusted_shift))

    # Convert back to probability
    posterior_logodds = prior_logodds + adjusted_shift
    fair_value = sigmoid(posterior_logodds)

    # Edge = our estimate minus market price
    edge = fair_value - current_market_price

    # Confidence: combination of edge magnitude and evidence strength
    # A 10¢ edge with strong evidence = high confidence
    # A 2¢ edge with one signal = low confidence
    edge_magnitude_factor = min(1.0, abs(edge) * 10)  # 10¢ edge = factor 1.0
    confidence = evidence_strength * edge_magnitude_factor

    # Determine side
    if edge > 0.01:
        side = "BUY_YES"
    elif edge < -0.01:
        side = "BUY_NO"
    else:
        side = "NEUTRAL"

    result = AggregatedSignal(
        fair_value=round(fair_value, 4),
        edge=round(edge, 4),
        confidence=round(confidence, 4),
        side=side,
        signal_count=len(valid_signals),
        evidence_strength=round(evidence_strength, 4),
        total_weight=round(total_weight, 4),
        signals=valid_signals,
    )

    log.info(
        "signal_aggregated",
        market_price=f"{current_market_price:.3f}",
        fair_value=f"{fair_value:.3f}",
        edge=f"{edge:+.3f}",
        confidence=f"{confidence:.3f}",
        signal_count=len(valid_signals),
        evidence_strength=f"{evidence_strength:.3f}",
        side=side,
    )

    return result
