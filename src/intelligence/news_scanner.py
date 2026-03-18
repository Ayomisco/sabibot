"""
News scanner — orchestrates all news sources and deduplicates results.

The scanner runs on a timer (default: every 60s) and:
  1. Fetches from all configured sources (RSS, GNews)
  2. Deduplicates against the database (by URL)
  3. Returns only new, unprocessed items
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from src.db.database import async_session
from src.db.models import NewsItem
from src.intelligence.sources.rss import RSSItem, fetch_rss_feeds
from src.intelligence.sources.newsapi import GNewsItem, get_top_headlines
from src.utils.logger import get_logger

log = get_logger("news_scanner")


async def scan_all_sources() -> list[NewsItem]:
    """
    Scan all news sources and return only NEW items (not seen before).

    Pipeline:
      1. Fetch from RSS feeds (free, no rate limit)
      2. Fetch from GNews headlines (if API key set)
      3. Deduplicate against database by URL
      4. Persist new items to database
      5. Return the new items for processing
    """
    # Gather from all sources
    raw_items: list[dict[str, Any]] = []

    # RSS feeds — always available
    rss_items = await fetch_rss_feeds()
    for item in rss_items:
        raw_items.append(_rss_to_dict(item))

    # GNews — only if configured
    gnews_items = await get_top_headlines(category="general", max_results=10)
    for item in gnews_items:
        raw_items.append(_gnews_to_dict(item))

    if not raw_items:
        log.debug("no_news_items_fetched")
        return []

    # Deduplicate against database
    urls = [item["url"] for item in raw_items if item["url"]]
    if not urls:
        return []

    async with async_session() as session:
        existing = await session.execute(
            select(NewsItem.url).where(NewsItem.url.in_(urls))
        )
        existing_urls = {row[0] for row in existing.fetchall()}

    new_items: list[NewsItem] = []
    for item_dict in raw_items:
        if item_dict["url"] and item_dict["url"] not in existing_urls:
            news_item = NewsItem(
                source=item_dict["source"],
                title=item_dict["title"],
                url=item_dict["url"],
                summary=item_dict["summary"],
                published_at=item_dict.get("published_at"),
                processed=False,
            )
            new_items.append(news_item)

    # Persist new items
    if new_items:
        async with async_session() as session:
            session.add_all(new_items)
            await session.commit()

    log.info(
        "news_scan_complete",
        total_fetched=len(raw_items),
        new_items=len(new_items),
        duplicate_skipped=len(raw_items) - len(new_items),
    )
    return new_items


def _rss_to_dict(item: RSSItem) -> dict[str, Any]:
    return {
        "source": f"rss:{item.source}",
        "title": item.title,
        "url": item.url,
        "summary": item.summary,
        "published_at": item.published_at,
    }


def _gnews_to_dict(item: GNewsItem) -> dict[str, Any]:
    return {
        "source": item.source,
        "title": item.title,
        "url": item.url,
        "summary": item.summary,
        "published_at": item.published_at,
    }
