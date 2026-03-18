"""
Sentiment analysis — extract directional signal from news text.

Uses LLM for nuanced sentiment (via analyze_news prompt) and
provides a fast rule-based fallback for when LLM is unavailable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from src.ai.reasoning import NewsAnalysis, analyze_news
from src.utils.logger import get_logger

log = get_logger("sentiment")


@dataclass
class SentimentResult:
    sentiment: float  # -1 to +1
    magnitude: float  # 0 to 1 (how significant)
    time_sensitivity: float  # 0 to 1
    topics: list[str]
    entities: list[str]
    summary: str
    method: str  # "llm" or "rule_based"


async def analyze_sentiment(headline: str, body: str = "") -> SentimentResult:
    """
    Analyze sentiment of a news item.
    Primary: LLM analysis (Groq/Llama). Fallback: rule-based keywords.
    """
    try:
        analysis = await analyze_news(headline, body)
        return SentimentResult(
            sentiment=analysis.sentiment,
            magnitude=analysis.magnitude,
            time_sensitivity=analysis.time_sensitivity,
            topics=analysis.affected_topics,
            entities=analysis.key_entities,
            summary=analysis.summary,
            method="llm",
        )
    except Exception as exc:
        log.warning("llm_sentiment_failed", error=str(
            exc), fallback="rule_based")
        return _rule_based_sentiment(headline, body)


def _rule_based_sentiment(headline: str, body: str = "") -> SentimentResult:
    """
    Fast rule-based sentiment fallback.

    Uses keyword lists weighted by domain relevance:
      - Strong positive/negative indicators
      - Domain-specific amplifiers (crypto, politics, etc.)
    """
    text = f"{headline} {body}".lower()

    # Weighted keyword lists — values are sentiment contributions
    POSITIVE = {
        "surge": 0.6, "soar": 0.6, "rally": 0.5, "gain": 0.3, "rise": 0.3,
        "win": 0.5, "victory": 0.5, "approve": 0.4, "pass": 0.3, "agree": 0.3,
        "deal": 0.3, "boost": 0.4, "success": 0.4, "breakthrough": 0.5,
        "record high": 0.6, "bullish": 0.5, "recover": 0.3, "growth": 0.3,
        "lead": 0.2, "support": 0.2, "launch": 0.3, "partnership": 0.3,
    }
    NEGATIVE = {
        "crash": -0.7, "plunge": -0.6, "collapse": -0.7, "drop": -0.3, "fall": -0.3,
        "lose": -0.4, "defeat": -0.5, "reject": -0.4, "fail": -0.4, "ban": -0.5,
        "sanction": -0.4, "war": -0.6, "attack": -0.5, "crisis": -0.5,
        "bearish": -0.5, "recession": -0.6, "inflation": -0.3, "scandal": -0.5,
        "indict": -0.5, "arrest": -0.4, "resign": -0.4, "impeach": -0.6,
        "hack": -0.5, "exploit": -0.5, "fraud": -0.6, "default": -0.6,
    }

    total_sentiment = 0.0
    matches = 0

    for word, weight in POSITIVE.items():
        if word in text:
            total_sentiment += weight
            matches += 1

    for word, weight in NEGATIVE.items():
        if word in text:
            total_sentiment += weight  # Already negative
            matches += 1

    # Normalize to -1 to +1
    if matches > 0:
        sentiment = max(-1.0, min(1.0, total_sentiment / max(matches, 1)))
        # More keyword hits = higher magnitude
        magnitude = min(1.0, matches * 0.2)
    else:
        sentiment = 0.0
        magnitude = 0.1

    # Extract capitalized words as pseudo-entities
    entities = list(
        set(re.findall(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b", headline)))[:5]

    return SentimentResult(
        sentiment=sentiment,
        magnitude=magnitude,
        time_sensitivity=0.5,  # Default to moderate
        topics=[],
        entities=entities,
        summary=headline[:200],
        method="rule_based",
    )
