"""
Polymarket WebSocket feed — real-time price updates.

Subscribes to market price changes via the CLOB WebSocket API.
Used for: live position monitoring, spread detection, momentum signals.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine

import websockets
from websockets.exceptions import ConnectionClosed

from src.config import settings
from src.utils.logger import get_logger

log = get_logger("polymarket_ws")

PriceCallback = Callable[[str, float, float], Coroutine[Any, Any, None]]
# callback(condition_id, yes_price, no_price)


@dataclass
class PriceUpdate:
    condition_id: str
    token_id: str
    price: float
    side: str  # "buy" or "sell"
    size: float
    timestamp: float


class PolymarketWebSocket:
    """
    WebSocket client for Polymarket CLOB price feed.

    Usage:
        ws = PolymarketWebSocket()
        ws.on_price_update(my_callback)
        await ws.subscribe(["condition_id_1", "condition_id_2"])
        await ws.run()  # blocks, reconnects on failure
    """

    def __init__(self) -> None:
        self._callbacks: list[PriceCallback] = []
        self._subscribed_markets: set[str] = set()
        self._ws: Any = None
        self._running = False

    def on_price_update(self, callback: PriceCallback) -> None:
        """Register a callback for price updates."""
        self._callbacks.append(callback)

    async def subscribe(self, condition_ids: list[str]) -> None:
        """Subscribe to price updates for given markets."""
        self._subscribed_markets.update(condition_ids)
        if self._ws:
            for cid in condition_ids:
                await self._send_subscribe(cid)

    async def unsubscribe(self, condition_ids: list[str]) -> None:
        """Unsubscribe from market updates."""
        self._subscribed_markets -= set(condition_ids)

    async def run(self) -> None:
        """Main loop — connect, subscribe, listen. Auto-reconnects."""
        self._running = True
        while self._running:
            try:
                await self._connect_and_listen()
            except ConnectionClosed as exc:
                log.warning("ws_disconnected", code=exc.code,
                            reason=str(exc.reason)[:100])
            except Exception as exc:
                log.error("ws_error", error=str(exc))

            if self._running:
                log.info("ws_reconnecting", wait_seconds=5)
                await asyncio.sleep(5)

    async def stop(self) -> None:
        """Stop the WebSocket gracefully."""
        self._running = False
        if self._ws:
            await self._ws.close()

    async def _connect_and_listen(self) -> None:
        """Single connection lifecycle."""
        async with websockets.connect(settings.polymarket_ws_url) as ws:
            self._ws = ws
            log.info("ws_connected", url=settings.polymarket_ws_url)

            # Subscribe to all tracked markets
            for cid in self._subscribed_markets:
                await self._send_subscribe(cid)

            async for raw_message in ws:
                try:
                    data = json.loads(raw_message)
                    await self._handle_message(data)
                except json.JSONDecodeError:
                    log.debug("ws_invalid_json", raw=str(raw_message)[:100])

        self._ws = None

    async def _send_subscribe(self, condition_id: str) -> None:
        """Send subscription message for a market."""
        if self._ws:
            msg = json.dumps({
                "type": "subscribe",
                "channel": "market",
                "market": condition_id,
            })
            await self._ws.send(msg)

    async def _handle_message(self, data: dict[str, Any]) -> None:
        """Parse and dispatch a WebSocket message."""
        msg_type = data.get("type", "")
        if msg_type not in ("book", "price_change", "trade"):
            return

        market = data.get("market", "")
        if not market:
            return

        # Extract price from various message formats
        yes_price = 0.5
        no_price = 0.5

        if "price" in data:
            yes_price = float(data["price"])
            no_price = 1.0 - yes_price
        elif "outcome_prices" in data:
            prices = data["outcome_prices"]
            yes_price = float(prices.get("Yes", 0.5))
            no_price = float(prices.get("No", 0.5))

        for cb in self._callbacks:
            try:
                await cb(market, yes_price, no_price)
            except Exception as exc:
                log.error("ws_callback_error", error=str(exc))


# Singleton
ws_feed = PolymarketWebSocket()
