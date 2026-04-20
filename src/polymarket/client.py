"""
Polymarket REST client — market discovery, pricing, and metadata.

Queries the Gamma API (public, no auth) for market data,
and the CLOB API for live pricing.
"""

from __future__ import annotations

import json
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
    market_slug: str = ""  # URL slug for polymarket.com/market/{slug}
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
        self.last_error: str = ""  # Track last error for diagnostics

    @with_retry(max_attempts=3, min_wait=1.0, retry_on=(httpx.HTTPError, httpx.TimeoutException))
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
        # /sampling-markets only returns accepting-orders markets by design,
        # so we don't filter on accepting_orders — the field may be absent.
        markets = [
            self._parse_market(m)
            for m in raw_markets
            if m.get("condition_id")
        ]
        log.info("market_raw_fetch", raw=len(raw_markets), accepted=len(markets), cursor=str(cursor)[:20] if cursor else "none")
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
        """
        Fetch active tradeable markets from the Gamma API.
        Gamma returns real volume, end_date, and token IDs in a single place.
        Falls back to CLOB /sampling-markets if Gamma is unreachable.
        """
        all_markets: list[Market] = []
        offset = 0
        limit = 100

        log.info("market_fetch_start", source="gamma", max_markets=max_markets)

        while len(all_markets) < max_markets:
            try:
                resp = await self._gamma.get("/markets", params={
                    "active": "true",
                    "closed": "false",
                    "limit": limit,
                    "offset": offset,
                })
                resp.raise_for_status()
                raw = resp.json()
                # Gamma returns a list or {"data": [...]}
                batch_raw: list[dict] = raw if isinstance(raw, list) else raw.get("data", [])

                if not batch_raw:
                    break

                batch = [
                    m for raw_m in batch_raw
                    if (m := self._parse_gamma_market(raw_m)) is not None
                ]
                all_markets.extend(batch)
                self.last_error = ""
                log.info("gamma_page_fetched", offset=offset, batch=len(batch))

                if len(batch_raw) < limit:
                    break  # Last page
                offset += limit

            except Exception as exc:
                self.last_error = f"Gamma offset {offset}: {type(exc).__name__}: {str(exc)[:120]}"
                log.error("gamma_market_error", error=self.last_error)
                # Fall back to CLOB sampling if Gamma fails entirely
                if not all_markets:
                    log.warning("gamma_fallback_clob")
                    return await self._get_markets_clob_fallback(max_markets)
                break

        log.info("fetched_markets", count=len(all_markets), source="gamma")
        return all_markets[:max_markets]

    async def _get_markets_clob_fallback(self, max_markets: int) -> list[Market]:
        """CLOB /sampling-markets fallback when Gamma is unavailable."""
        all_markets: list[Market] = []
        cursor: str | None = None
        for _ in range(10):
            try:
                batch, cursor = await self.get_markets(limit=100, next_cursor=cursor)
                all_markets.extend(batch)
                if not cursor or len(all_markets) >= max_markets:
                    break
            except Exception as exc:
                self.last_error = f"CLOB fallback: {str(exc)[:120]}"
                break
        return all_markets[:max_markets]

    @with_retry(max_attempts=2, min_wait=1.0, retry_on=(httpx.HTTPError,))
    async def get_events(self, limit: int = 50) -> list[dict[str, Any]]:
        """Fetch events (groups of related markets) from Gamma API."""
        resp = await self._gamma.get("/events", params={"limit": limit, "active": "true"})
        resp.raise_for_status()
        return resp.json()

    def _parse_gamma_market(self, raw: dict[str, Any]) -> "Market | None":
        """Parse Gamma API market JSON into a Market dataclass."""
        condition_id = raw.get("conditionId", "")
        if not condition_id:
            return None

        # outcomes and prices are JSON-encoded strings in Gamma
        try:
            outcomes: list[str] = json.loads(raw.get("outcomes", '["Yes","No"]'))
        except (json.JSONDecodeError, TypeError):
            outcomes = ["Yes", "No"]

        try:
            prices_raw = json.loads(raw.get("outcomePrices", "[0.5,0.5]"))
            prices = [float(p) for p in prices_raw]
        except (json.JSONDecodeError, TypeError, ValueError):
            prices = [0.5] * len(outcomes)

        outcome_prices = dict(zip(outcomes, prices, strict=False))

        # clobTokenIds is a JSON-encoded list of token IDs
        try:
            token_ids: list[str] = json.loads(raw.get("clobTokenIds", "[]"))
        except (json.JSONDecodeError, TypeError):
            token_ids = []
        tokens = [
            {"token_id": t, "outcome": outcomes[i] if i < len(outcomes) else str(i)}
            for i, t in enumerate(token_ids)
        ]

        # end_date — Gamma uses "endDate" or "end_date_iso"
        end_date = None
        for key in ("endDate", "end_date_iso", "endDateIso"):
            val = raw.get(key)
            if val:
                try:
                    end_date = datetime.fromisoformat(
                        str(val).replace("Z", "+00:00"))
                    break
                except (ValueError, TypeError):
                    pass

        # Volume — prefer 24h, fall back to total
        try:
            volume_24h = float(raw.get("volume24hr") or raw.get("volume") or 0)
        except (ValueError, TypeError):
            volume_24h = 0.0

        try:
            liquidity = float(raw.get("liquidity") or 0)
        except (ValueError, TypeError):
            liquidity = 0.0

        # Gamma uses camelCase for some fields
        accepting = raw.get("acceptingOrders", raw.get("accepting_orders",
                    not raw.get("closed", False) and raw.get("active", True)))

        return Market(
            condition_id=condition_id,
            question=raw.get("question", ""),
            description=raw.get("description", ""),
            category=raw.get("category", ""),
            market_slug=raw.get("slug", raw.get("market_slug", "")),
            end_date=end_date,
            outcome_prices=outcome_prices,
            tokens=tokens,
            volume_24h=volume_24h,
            liquidity=liquidity,
            active=raw.get("active", True),
            neg_risk=raw.get("negRisk", raw.get("neg_risk", False)),
            accepting_orders=bool(accepting),
            minimum_order_size=float(raw.get("minimum_order_size") or 5),
            minimum_tick_size=float(raw.get("minimum_tick_size") or 0.01),
            tags=raw.get("tags") or [],
        )

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
            market_slug=raw.get("market_slug", ""),
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
