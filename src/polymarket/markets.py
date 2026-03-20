"""
Market matching — the hardest engineering problem in the bot.

Given a news headline, find which Polymarket markets it affects.
Uses a layered approach:

Layer 1: Entity extraction (spaCy NER) → filter markets mentioning same entities
Layer 2: Keyword overlap (TF-IDF inspired) → score by shared important terms
Layer 3: Semantic similarity (embeddings) → cosine similarity between news and market questions
Layer 4: LLM verification (optional, SMART tier) → confirm the match makes sense

Final score = weighted combination. Only matches above threshold are returned.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from src.ai.embeddings import cosine_similarity, embed_texts
from src.config import settings
from src.polymarket.client import Market
from src.utils.logger import get_logger

log = get_logger("market_match")

# Weights for composite scoring — adjust based on embedding quality.
# OpenAI embeddings are high-quality (1536-dim) → lean on semantic.
# spaCy en_core_web_md has real 300-dim vectors — usable but not great.
if settings.openai_api_key:
    W_ENTITY = 0.25
    W_KEYWORD = 0.20
    W_SEMANTIC = 0.55
else:
    # spaCy fallback — semantic is unreliable, boost entity & keyword
    W_ENTITY = 0.45
    W_KEYWORD = 0.40
    W_SEMANTIC = 0.15

MATCH_THRESHOLD = 0.15  # Lowered — spaCy fallback scores are lower than OpenAI embeds

# Common words to exclude from keyword matching
STOP_WORDS = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "will", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "shall", "should", "may", "might",
    "can", "could", "would", "to", "of", "in", "for", "on", "with", "at", "by",
    "from", "as", "into", "through", "during", "before", "after", "above", "below",
    "between", "out", "off", "over", "under", "again", "further", "then", "once",
    "that", "this", "these", "those", "it", "its", "and", "but", "or", "nor",
    "not", "no", "so", "than", "too", "very", "just", "about", "up", "if",
    "what", "which", "who", "whom", "when", "where", "why", "how", "all", "each",
    "market", "prediction", "yes", "no",
})


@dataclass
class MatchResult:
    market: Market
    score: float
    entity_score: float
    keyword_score: float
    semantic_score: float
    matched_entities: list[str]
    matched_keywords: list[str]


def _extract_keywords(text: str) -> set[str]:
    """Extract significant keywords from text, excluding stop words."""
    words = re.findall(r"[a-zA-Z]{2,}", text.lower())
    return {w for w in words if w not in STOP_WORDS}


def _extract_entities_simple(text: str) -> set[str]:
    """
    Extract named entities using spaCy NER.
    Falls back to capitalized word extraction if spaCy fails.
    """
    try:
        from src.ai.embeddings import _get_spacy
        nlp = _get_spacy()
        doc = nlp(text[:512])
        entities = set()
        for ent in doc.ents:
            if ent.label_ in ("PERSON", "ORG", "GPE", "NORP", "EVENT", "LOC", "FAC"):
                entities.add(ent.text.lower())
        return entities
    except Exception:
        # Fallback: extract Capitalized Words as pseudo-entities
        caps = re.findall(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b", text)
        return {c.lower() for c in caps}


def _entity_overlap_score(news_entities: set[str], market_text: str) -> tuple[float, list[str]]:
    """Score how many news entities appear in the market text."""
    if not news_entities:
        return 0.0, []

    market_lower = market_text.lower()
    matched = [e for e in news_entities if e in market_lower]
    score = len(matched) / len(news_entities) if news_entities else 0.0
    return min(1.0, score), matched


def _keyword_overlap_score(
    news_keywords: set[str], market_keywords: set[str]
) -> tuple[float, list[str]]:
    """Jaccard-inspired keyword overlap with term frequency weighting."""
    if not news_keywords or not market_keywords:
        return 0.0, []

    overlap = news_keywords & market_keywords
    if not overlap:
        return 0.0, []

    # Jaccard similarity
    union = news_keywords | market_keywords
    score = len(overlap) / len(union)
    # Scale up since Jaccard is naturally low
    return min(1.0, score * 2), list(overlap)


async def match_news_to_markets(
    headline: str,
    body: str,
    markets: list[Market],
    topics: list[str] | None = None,
    entities: list[str] | None = None,
    top_k: int = 5,
) -> list[MatchResult]:
    """
    Given a news item, find the most relevant Polymarket markets.

    Args:
        headline: News headline
        body: News body/summary
        markets: List of active markets to match against
        topics: Pre-extracted topics from LLM analysis (optional, improves matching)
        entities: Pre-extracted entities from LLM analysis (optional)
        top_k: Maximum number of matches to return

    Returns:
        Sorted list of MatchResult (highest score first)
    """
    if not markets:
        return []

    full_news = f"{headline} {body}"
    if topics:
        full_news += " " + " ".join(topics)

    # Layer 1: Entity extraction
    news_entities = _extract_entities_simple(full_news)
    if entities:
        news_entities.update(e.lower() for e in entities)

    # Layer 2: Keyword extraction
    news_keywords = _extract_keywords(full_news)

    # Layer 3: Precompute embeddings for news and all market questions
    texts_to_embed = [full_news] + [m.question for m in markets]
    embeddings = await embed_texts(texts_to_embed)
    news_embedding = embeddings[0]
    market_embeddings = embeddings[1:]

    # Score each market
    results: list[MatchResult] = []
    near_misses: list[tuple[float, str]] = []  # (score, question) for debugging
    for i, market in enumerate(markets):
        market_text = f"{market.question} {market.description} {market.category}"
        market_kw = _extract_keywords(market_text)

        # Layer 1: Entity overlap
        ent_score, matched_ents = _entity_overlap_score(
            news_entities, market_text)

        # Layer 2: Keyword overlap
        kw_score, matched_kws = _keyword_overlap_score(
            news_keywords, market_kw)

        # Layer 3: Semantic similarity
        sem_score = cosine_similarity(news_embedding, market_embeddings[i])
        sem_score = max(0.0, sem_score)  # Clamp negative similarities

        # Composite score
        composite = (W_ENTITY * ent_score) + (W_KEYWORD *
                                              kw_score) + (W_SEMANTIC * sem_score)

        if composite >= MATCH_THRESHOLD:
            results.append(MatchResult(
                market=market,
                score=composite,
                entity_score=ent_score,
                keyword_score=kw_score,
                semantic_score=sem_score,
                matched_entities=matched_ents,
                matched_keywords=matched_kws,
            ))
        elif composite > 0.10:  # Track near-misses for debugging
            near_misses.append((composite, market.question))

    near_misses.sort(key=lambda x: x[0], reverse=True)
    near_misses = near_misses[:3]  # Keep top 3 near-misses

    results.sort(key=lambda r: r.score, reverse=True)

    if results:
        log.info(
            "market_match",
            headline=headline[:80],
            matches=len(results),
            top_score=f"{results[0].score:.3f}",
            top_market=results[0].market.question[:60],
        )
    else:
        # Log top near-miss to diagnose why nothing matched
        if near_misses:
            best = near_misses[0]
            log.info(
                "no_match_near_miss",
                headline=headline[:80],
                best_score=f"{best[0]:.3f}",
                best_market=best[1][:60],
                threshold=MATCH_THRESHOLD,
                weights=f"E={W_ENTITY} K={W_KEYWORD} S={W_SEMANTIC}",
            )
        else:
            log.info("no_match_all_zero", headline=headline[:80],
                     markets_checked=len(markets))

    return results[:top_k]
