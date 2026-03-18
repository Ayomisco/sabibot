"""
AI reasoning chains — structured LLM calls for specific analytical tasks.

Each function wraps a prompt + parsing pattern for one step in the pipeline:
  1. Analyze news impact
  2. Estimate fair probability
  3. Generate trade rationale
"""

from __future__ import annotations

from dataclasses import dataclass

from src.ai.llm import ModelTier, llm
from src.ai.prompts.analyze_news import build_news_analysis_prompt
from src.ai.prompts.estimate_probability import build_probability_prompt
from src.ai.prompts.explain_trade import build_trade_explanation_prompt
from src.utils.logger import get_logger

log = get_logger("reasoning")


@dataclass
class NewsAnalysis:
    affected_topics: list[str]
    sentiment: float  # -1 to +1
    magnitude: float  # 0 to 1, how significant
    time_sensitivity: float  # 0 to 1, how quickly market should react
    key_entities: list[str]
    summary: str


@dataclass
class ProbabilityEstimate:
    probability: float  # 0 to 1
    confidence: float  # 0 to 1
    reasoning: str
    key_factors: list[str]


@dataclass
class TradeExplanation:
    rationale: str
    risk_factors: list[str]
    expected_timeframe: str


async def analyze_news(headline: str, body: str = "") -> NewsAnalysis:
    """Analyze a news item for market impact. Uses FREE tier (Groq)."""
    prompt = build_news_analysis_prompt(headline, body)
    data = await llm.complete_json(
        prompt,
        system="You are a prediction market analyst. Analyze news for market impact.",
        tier=ModelTier.FREE,
        temperature=0.2,
    )
    return NewsAnalysis(
        affected_topics=data.get("affected_topics", []),
        sentiment=float(data.get("sentiment", 0)),
        magnitude=float(data.get("magnitude", 0)),
        time_sensitivity=float(data.get("time_sensitivity", 0)),
        key_entities=data.get("key_entities", []),
        summary=data.get("summary", ""),
    )


async def estimate_probability(
    market_question: str,
    current_price: float,
    evidence: list[str],
) -> ProbabilityEstimate:
    """
    Estimate fair probability of a market outcome.
    Uses SMART tier (Claude) — this is where real money decisions are made.
    """
    prompt = build_probability_prompt(market_question, current_price, evidence)
    data = await llm.complete_json(
        prompt,
        system=(
            "You are an expert forecaster calibrated on prediction market outcomes. "
            "Your probability estimates should be well-calibrated: when you say 70%, "
            "the event should happen ~70% of the time."
        ),
        tier=ModelTier.SMART,
        temperature=0.2,
    )
    return ProbabilityEstimate(
        probability=max(0.01, min(0.99, float(data.get("probability", 0.5)))),
        confidence=max(0.0, min(1.0, float(data.get("confidence", 0.5)))),
        reasoning=data.get("reasoning", ""),
        key_factors=data.get("key_factors", []),
    )


async def explain_trade(
    market_question: str,
    side: str,
    edge: float,
    evidence_summary: str,
) -> TradeExplanation:
    """Generate human-readable trade explanation. FREE tier."""
    prompt = build_trade_explanation_prompt(market_question, side, edge, evidence_summary)
    data = await llm.complete_json(
        prompt,
        system="You are a trading analyst explaining a prediction market position.",
        tier=ModelTier.FREE,
        temperature=0.3,
    )
    return TradeExplanation(
        rationale=data.get("rationale", ""),
        risk_factors=data.get("risk_factors", []),
        expected_timeframe=data.get("expected_timeframe", "unknown"),
    )
