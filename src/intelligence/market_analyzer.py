"""
Market analyzer — connects news intelligence to market matching and probability estimation.

This is the brain that turns "a news headline" into "a potential trade opportunity".

Pipeline:
  1. Receive a new news item
  2. Run sentiment analysis
  3. Match to relevant markets (entity + keyword + semantic)
  4. For top matches, estimate fair probability via LLM
  5. Calculate edge (fair value - market price)
  6. Produce structured signals for the aggregator
"""

from __future__ import annotations

from dataclasses import dataclass

from src.ai.reasoning import estimate_probability
from src.db.models import Signal, SignalDirection
from src.intelligence.sentiment import SentimentResult, analyze_sentiment
from src.polymarket.client import Market, polymarket
from src.polymarket.markets import MatchResult, match_news_to_markets
from src.utils.logger import get_logger

log = get_logger("market_analyzer")


@dataclass
class TradeOpportunity:
    """A potential trade with all the context needed for execution."""
    market: Market
    signal: Signal
    fair_value: float
    edge: float  # fair_value - market_price (positive = underpriced YES)
    confidence: float
    reasoning: str
    match_score: float
    sentiment: SentimentResult


async def analyze_news_item(
    headline: str,
    body: str = "",
    source: str = "",
    markets: list[Market] | None = None,
) -> list[TradeOpportunity]:
    """
    Full analysis pipeline: news → sentiment → market match → probability → edge.

    Returns a list of trade opportunities sorted by edge magnitude.
    """
    # Step 1: Sentiment analysis
    sentiment = await analyze_sentiment(headline, body)

    # Skip low-magnitude news — not worth the LLM calls
    if sentiment.magnitude < 0.2:
        log.debug("news_too_low_magnitude",
                  headline=headline[:60], magnitude=sentiment.magnitude)
        return []

    # Step 2: Get active markets if not provided
    if markets is None:
        markets = await polymarket.get_all_active_markets(max_markets=300)

    if not markets:
        log.warning("no_active_markets")
        return []

    # Step 3: Match news to markets
    matches = await match_news_to_markets(
        headline=headline,
        body=body,
        markets=markets,
        topics=sentiment.topics,
        entities=sentiment.entities,
        top_k=3,  # Only analyze top 3 matches (limit LLM calls)
    )

    if not matches:
        log.debug("no_market_matches", headline=headline[:60])
        return []

    # Step 4: For each match, estimate probability and compute edge
    opportunities: list[TradeOpportunity] = []

    for match in matches:
        market = match.market
        current_yes_price = market.outcome_prices.get("Yes", 0.5)

        # Build evidence list for the LLM
        evidence = [
            f"News: {headline}",
            f"Sentiment: {sentiment.sentiment:+.2f} (magnitude: {sentiment.magnitude:.2f})",
            f"Match confidence: {match.score:.2f}",
        ]
        if sentiment.summary:
            evidence.append(f"Summary: {sentiment.summary}")
        if match.matched_entities:
            evidence.append(
                f"Matched entities: {', '.join(match.matched_entities)}")

        try:
            # Step 5: LLM probability estimation (SMART tier — real money decision)
            prob_est = await estimate_probability(
                market_question=market.question,
                current_price=current_yes_price,
                evidence=evidence,
            )

            edge = prob_est.probability - current_yes_price

            # Determine signal direction
            if edge > 0:
                direction = SignalDirection.BUY_YES
            elif edge < 0:
                direction = SignalDirection.BUY_NO
            else:
                direction = SignalDirection.NEUTRAL

            signal = Signal(
                source=source or "market_analyzer",
                headline=headline[:500],
                summary=sentiment.summary[:500],
                direction=direction,
                probability_estimate=prob_est.probability,
                magnitude=sentiment.magnitude,
                reliability=_source_reliability(source),
                matched_condition_id=market.condition_id,
                matched_score=match.score,
                raw_sentiment=sentiment.sentiment,
            )

            opportunities.append(TradeOpportunity(
                market=market,
                signal=signal,
                fair_value=prob_est.probability,
                edge=edge,
                confidence=prob_est.confidence,
                reasoning=prob_est.reasoning,
                match_score=match.score,
                sentiment=sentiment,
            ))

            log.info(
                "opportunity_found",
                market=market.question[:60],
                fair_value=f"{prob_est.probability:.3f}",
                market_price=f"{current_yes_price:.3f}",
                edge=f"{edge:+.3f}",
                confidence=f"{prob_est.confidence:.2f}",
            )

        except Exception as exc:
            log.error("probability_estimation_failed",
                      market=market.question[:60], error=str(exc))

    # Sort by absolute edge (strongest opportunities first)
    opportunities.sort(key=lambda o: abs(o.edge), reverse=True)
    return opportunities


def _source_reliability(source: str) -> float:
    """
    Assign a reliability weight based on news source.
    Higher = more trustworthy / historically accurate for markets.
    """
    source_lower = source.lower()

    # Tier 1: Wire services and major outlets
    if any(s in source_lower for s in ("reuters", "ap_", "apnews", "bbc")):
        return 0.85

    # Tier 2: Domain-specific quality sources
    if any(s in source_lower for s in ("politico", "coindesk", "bloomberg")):
        return 0.75

    # Tier 3: General news
    if any(s in source_lower for s in ("thehill", "arstechnica", "espn")):
        return 0.65

    # Tier 4: Aggregators and unknown
    if "gnews" in source_lower:
        return 0.55

    return 0.50
