"""Tests for market matching — the core news→market mapping logic."""

import pytest
from unittest.mock import patch, AsyncMock

from src.polymarket.client import Market
from src.polymarket.markets import (
    _extract_keywords,
    _keyword_overlap_score,
)


def _make_market(
    condition_id: str = "0xabc",
    question: str = "Will Bitcoin exceed $100K by June 2025?",
) -> Market:
    return Market(
        condition_id=condition_id,
        question=question,
        description="Bitcoin price prediction",
        category="crypto",
        outcome_prices={"Yes": 0.50, "No": 0.50},
        tokens=[],
        volume_24h=50000.0,
        liquidity=10000.0,
    )


class TestKeywordExtraction:
    """Test keyword extraction from text."""

    def test_extracts_meaningful_words(self):
        text = "Bitcoin surges past $95,000 amid ETF inflows"
        keywords = _extract_keywords(text)
        assert "bitcoin" in keywords
        assert "surges" in keywords or "surge" in keywords

    def test_filters_stopwords(self):
        text = "The price of Bitcoin is increasing in the market"
        keywords = _extract_keywords(text)
        assert "the" not in keywords
        assert "of" not in keywords
        assert "is" not in keywords
        assert "in" not in keywords

    def test_empty_text(self):
        keywords = _extract_keywords("")
        assert keywords == set() or len(keywords) == 0


class TestKeywordOverlap:
    """Test Jaccard-inspired keyword overlap scoring."""

    def test_identical_sets(self):
        score, matched = _keyword_overlap_score({"bitcoin", "price"}, {"bitcoin", "price"})
        assert score > 0.9

    def test_no_overlap(self):
        score, matched = _keyword_overlap_score({"bitcoin", "crypto"}, {"football", "soccer"})
        assert score == 0.0
        assert matched == []

    def test_partial_overlap(self):
        score, matched = _keyword_overlap_score(
            {"bitcoin", "price", "surge"},
            {"bitcoin", "etf", "approval"},
        )
        assert 0.0 < score < 1.0
        assert "bitcoin" in matched

    def test_empty_sets(self):
        score, matched = _keyword_overlap_score(set(), set())
        assert score == 0.0

    def test_one_empty(self):
        score, matched = _keyword_overlap_score({"bitcoin"}, set())
        assert score == 0.0


class TestMarketMatching:
    """Test end-to-end market matching."""

    def test_relevant_news_matches_market(self):
        """A Bitcoin news headline should match a Bitcoin market."""
        market = _make_market(question="Will Bitcoin exceed $100K by June 2025?")
        news_keywords = _extract_keywords("Bitcoin surges past $95,000 amid ETF inflows")
        market_keywords = _extract_keywords(market.question)
        score, matched = _keyword_overlap_score(news_keywords, market_keywords)
        # Bitcoin appears in both → should have some overlap
        assert score > 0
        assert "bitcoin" in matched

    def test_unrelated_news_low_score(self):
        """Unrelated news should have low overlap with market."""
        market = _make_market(question="Will Bitcoin exceed $100K by June 2025?")
        news_keywords = _extract_keywords("Major earthquake hits Japan's Fukushima region")
        market_keywords = _extract_keywords(market.question)
        score, _ = _keyword_overlap_score(news_keywords, market_keywords)
        assert score < 0.15
