"""
Polymarket REST client — market discovery, pricing, and metadata.

Queries the Gamma API (public, no auth) for market data,
and the CLOB API for live pricing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx

from src.polymarket.constants import CLOB_BASE_URL, GAMMA_BASE_URL
from src.utils.logger import get_logger
from src.utils.retry import with_retry

log = get_logger("polymarket_client")


@dataclass
class Market:
    """Parsed Polymarket market."""
    condition_id: str
    question: str
    description: str = ""
    category: str = ""
    end_date: datetime | None = None
    outcome_prices: dict[str, float] = field(
        default_factory=dict)  # {"Yes": 0.65, "No": 0.35}
    tokens: list[dict[str, str]] = field(
        default_factory=list)  # [{token_id, outcome}]
    volume_24h: float = 0.0
    liquidity: float = 0.0
    active: bool = True
    neg_risk: bool = False
    accepting_orders: bool = False
    minimum_order_size: float = 5.0
    minimum_tick_size: float = 0.01
    tags: list[str] = field(default_factory=list)


class PolymarketClient:
    """HTTP client for Polymarket CLOB + Gamma APIs."""

    def __init__(self) -> None:
        self._clob = httpx.AsyncClient(timeout=30.0, base_url=CLOB_BASE_URL)
        self._gamma = httpx.AsyncClient(timeout=30.0, base_url=GAMMA_BASE_URL)

    @with_retry(max_attempts=3, min_wait=1.0, retry_on=(httpx.HTTPError,))
    async def get_markets(
        self,
        limit: int = 100,
        next_cursor: str | None = None,
    ) -> tuple[list[Market], str | None]:
        """Fetch active tradeable markets from the CLOB sampling-markets endpoint."""
        params: dict[str, Any] = {"limit": min(limit, 100)}
        if next_cursor:
            params["next_cursor"] = next_cursor
        resp = await self._clob.get("/sampling-markets", params=params)
        resp.raise_for_status()
        data = resp.json()
        raw_markets = data.get("data", [])
        cursor = data.get("next_cursor")
        # Only return markets that are actively accepting orders
        markets = [
            self._parse_market(m)
            for m in raw_markets
            if m.get("condition_id") and m.get("accepting_orders")
        ]
        return markets, cursor if cursor != "LTE=" else None

    @with_retry(max_attempts=3, min_wait=1.0, retry_on=(httpx.HTTPError,))
    async def get_market(self, condition_id: str) -> Market | None:
        """Fetch a single market by condition_id from CLOB."""
        resp = await self._clob.get(f"/markets/{condition_id}")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return self._parse_market(resp.json())

    async def get_all_active_markets(self, max_markets: int = 500) -> list[Market]:
        """Paginate through all active tradeable markets."""
        all_markets: list[Market] = []
        cursor: str | None = None
        empty_streak = 0
        page = 0

        while len(all_markets) < max_markets:
            try:
                batch, cursor = await self.get_markets(limit=100, next_cursor=cursor)
                page += 1
                log.debug("market_page_fetched", page=page, batch_size=len(batch), cursor=str(cursor)[:20] if cursor else None)
                
                if not batch:
                    empty_streak += 1
                    if empty_streak >= 3 or not cursor:
                        log.warning("market_pagination_stopped", empty_streak=empty_streak, has_cursor=bool(cursor), total=len(all_markets))
                        break
                    continue
                    
                empty_streak = 0
                all_markets.extend(batch)
                if not cursor:
                    break
            except Exception as exc:
                log.error("market_page_error", page=page, error=str(exc), total_so_far=len(all_markets))
                break

        log.info("fetched_markets", count=len(all_markets), pages=page)
        return all_markets[:max_markets]

    @with_retry(max_attempts=2, min_wait=1.0, retry_on=(httpx.HTTPError,))
    async def get_events(self, limit: int = 50) -> list[dict[str, Any]]:
        """Fetch events (groups of related markets) from Gamma API."""
        resp = await self._gamma.get("/events", params={"limit": limit, "active": "true"})
        resp.raise_for_status()
        return resp.json()

    def _parse_market(self, raw: dict[str, Any]) -> Market:
        """Parse CLOB API JSON into a Market dataclass."""
        tokens = []
        outcome_prices = {}

        for tok in raw.get("tokens", []):
            token_id = tok.get("token_id", "")
            outcome = tok.get("outcome", "")
            price = float(tok.get("price", 0.5))
            tokens.append({"token_id": token_id, "outcome": outcome})
            outcome_prices[outcome] = price

        end_date = None
        if raw.get("end_date_iso"):
            try:
                end_date = datetime.fromisoformat(
                    raw["end_date_iso"].replace("Z", "+00:00"))
            except (ValueError, TypeError):
                pass

        return Market(
            condition_id=raw.get("condition_id", ""),
            question=raw.get("question", ""),
            description=raw.get("description", ""),
            category=raw.get("category", ""),
            end_date=end_date,
            outcome_prices=outcome_prices,
            tokens=tokens,
            volume_24h=float(raw.get("volume", 0) or 0),
            liquidity=float(raw.get("liquidity", 0) or 0),
            active=raw.get("active", True),
            neg_risk=raw.get("neg_risk", False),
            accepting_orders=raw.get("accepting_orders", False),
            minimum_order_size=float(raw.get("minimum_order_size", 5)),
            minimum_tick_size=float(raw.get("minimum_tick_size", 0.01)),
            tags=raw.get("tags", []),
        )

    async def close(self) -> None:
        await self._clob.aclose()
        await self._gamma.aclose()


# Singleton
polymarket = PolymarketClient()
