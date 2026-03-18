"""
RSS feed scanner — free, no API key, runs during US sleep hours for timezone edge.

Curated feeds targeting prediction-market-relevant categories:
  - World politics (Reuters, AP, BBC)
  - US politics (Politico, The Hill)
  - Crypto/finance (CoinDesk, Bloomberg)
  - Sports (ESPN)
  - Science/tech (Ars Technica, Wired)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import feedparser
from dateutil import parser as dateparse

from src.utils.logger import get_logger

log = get_logger("rss")


@dataclass
class RSSItem:
    source: str
    title: str
    url: str
    summary: str = ""
    published_at: Optional[datetime] = None


# ── Curated Feed URLs ────────────────────────────────────────────
# Each tuple: (source_name, feed_url, category)
RSS_FEEDS: list[tuple[str, str, str]] = [
    # World / Politics
    ("reuters_world", "https://feeds.reuters.com/Reuters/worldNews", "world"),
    ("ap_topnews", "https://rsshub.app/apnews/topics/apf-topnews", "world"),
    ("bbc_world", "https://feeds.bbci.co.uk/news/world/rss.xml", "world"),

    # US Politics
    ("politico", "https://www.politico.com/rss/politicopicks.xml", "us_politics"),
    ("thehill", "https://thehill.com/feed/", "us_politics"),

    # Finance / Crypto
    ("coindesk", "https://www.coindesk.com/arc/outboundfeeds/rss/", "crypto"),
    ("cointelegraph", "https://cointelegraph.com/rss", "crypto"),
    ("decrypt", "https://decrypt.co/feed", "crypto"),

    # Finance / Economics
    ("cnbc_top", "https://www.cnbc.com/id/100003114/device/rss/rss.html", "finance"),

    # Science / Tech
    ("arstechnica", "https://feeds.arstechnica.com/arstechnica/index", "tech"),

    # Sports
    ("espn", "https://www.espn.com/espn/rss/news", "sports"),
    ("bbc_sport", "https://feeds.bbci.co.uk/sport/rss.xml", "sports"),

    # Entertainment / Pop Culture
    ("variety", "https://variety.com/feed/", "entertainment"),
]


async def fetch_rss_feeds(
    categories: list[str] | None = None,
    max_items_per_feed: int = 15,
) -> list[RSSItem]:
    """
    Fetch items from all RSS feeds (or filtered by category).

    Returns deduplicated items sorted by recency.
    """
    feeds_to_check = RSS_FEEDS
    if categories:
        cat_set = set(categories)
        feeds_to_check = [f for f in RSS_FEEDS if f[2] in cat_set]

    all_items: list[RSSItem] = []
    seen_urls: set[str] = set()

    for source_name, feed_url, _category in feeds_to_check:
        try:
            items = _parse_feed(source_name, feed_url, max_items_per_feed)
            for item in items:
                if item.url not in seen_urls:
                    seen_urls.add(item.url)
                    all_items.append(item)
        except Exception as exc:
            log.warning("rss_feed_error", source=source_name, error=str(exc))

    # Sort by recency (newest first, items without dates go last)
    all_items.sort(
        key=lambda x: x.published_at or datetime.min.replace(
            tzinfo=timezone.utc),
        reverse=True,
    )

    log.info("rss_fetched", total_items=len(all_items),
             feeds_checked=len(feeds_to_check))
    return all_items


def _parse_feed(source: str, url: str, max_items: int) -> list[RSSItem]:
    """Parse a single RSS feed. Runs synchronously (feedparser is sync)."""
    feed = feedparser.parse(url)
    items: list[RSSItem] = []

    for entry in feed.entries[:max_items]:
        published = None
        if hasattr(entry, "published"):
            try:
                published = dateparse.parse(entry.published)
                if published.tzinfo is None:
                    published = published.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                pass

        summary = ""
        if hasattr(entry, "summary"):
            # Strip HTML tags
            summary = _strip_html(entry.summary)[:500]

        link = entry.get("link", "")

        items.append(RSSItem(
            source=source,
            title=entry.get("title", "").strip(),
            url=link,
            summary=summary,
            published_at=published,
        ))

    return items


def _strip_html(text: str) -> str:
    """Remove HTML tags from text."""
    import re
    clean = re.sub(r"<[^>]+>", " ", text)
    clean = re.sub(r"\s+", " ", clean)
    return clean.strip()
