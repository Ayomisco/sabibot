"""
GNews API adapter — free tier provides 100 requests/day.

Use when RSS feeds aren't enough or for targeted keyword searches.
Falls back gracefully if GNEWS_API_KEY is not set.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import httpx
from dateutil import parser as dateparse

from src.config import settings
from src.utils.logger import get_logger
from src.utils.retry import with_retry

log = get_logger("gnews")

GNEWS_BASE = "https://gnews.io/api/v4"


@dataclass
class GNewsItem:
    source: str
    title: str
    url: str
    summary: str = ""
    published_at: Optional[datetime] = None


@with_retry(max_attempts=2, min_wait=2.0, retry_on=(httpx.HTTPError,))
async def search_gnews(
    query: str,
    max_results: int = 10,
    lang: str = "en",
) -> list[GNewsItem]:
    """
    Search GNews for articles matching a query.
    Returns empty list if API key is not configured.
    """
    if not settings.gnews_api_key:
        return []

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{GNEWS_BASE}/search",
            params={
                "q": query,
                "lang": lang,
                "max": min(max_results, 10),
                "apikey": settings.gnews_api_key,
            },
        )
        resp.raise_for_status()
        data = resp.json()

    articles = data.get("articles", [])
    items: list[GNewsItem] = []

    for article in articles:
        published = None
        if article.get("publishedAt"):
            try:
                published = dateparse.parse(article["publishedAt"])
                if published.tzinfo is None:
                    published = published.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                pass

        items.append(GNewsItem(
            source=f"gnews:{article.get('source', {}).get('name', 'unknown')}",
            title=article.get("title", ""),
            url=article.get("url", ""),
            summary=article.get("description", "")[:500],
            published_at=published,
        ))

    log.info("gnews_search", query=query[:50], results=len(items))
    return items


async def get_top_headlines(
    category: str = "general",
    max_results: int = 10,
) -> list[GNewsItem]:
    """Get top headlines by category. Useful for the general news scan."""
    if not settings.gnews_api_key:
        return []

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{GNEWS_BASE}/top-headlines",
            params={
                "category": category,
                "lang": "en",
                "max": min(max_results, 10),
                "apikey": settings.gnews_api_key,
            },
        )
        resp.raise_for_status()
        data = resp.json()

    articles = data.get("articles", [])
    return [
        GNewsItem(
            source=f"gnews:{a.get('source', {}).get('name', 'unknown')}",
            title=a.get("title", ""),
            url=a.get("url", ""),
            summary=a.get("description", "")[:500],
            published_at=dateparse.parse(a["publishedAt"]) if a.get("publishedAt") else None,
        )
        for a in articles
    ]
