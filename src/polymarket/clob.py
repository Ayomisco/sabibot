"""
Polymarket CLOB client — order placement, book queries, position management.

Wraps py-clob-client with correct EOA signing (signature_type=0).
The signing flow:
  1. Derive API credentials by ECDSA-signing a nonce from the CLOB server
  2. Use those HMAC credentials for all subsequent API calls
  3. Orders are signed with EIP-712 typed data using signature_type=0
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, OrderArgs, OrderType, PartialCreateOrderOptions

from src.config import settings
from src.polymarket.constants import (
    BUY,
    SELL,
    SIGNATURE_TYPE_EOA,
)
from src.utils.logger import get_logger
from src.utils.retry import with_retry

log = get_logger("clob")


@dataclass
class OrderResult:
    success: bool
    order_id: str = ""
    error: str = ""
    raw: dict[str, Any] | None = None


class CLOBClient:
    """
    Wraps py-clob-client with:
    - Correct EOA signature_type=0
    - API credential caching
    - Structured logging on every operation
    """

    def __init__(self) -> None:
        self._client: ClobClient | None = None
        self._creds: ApiCreds | None = None

    async def initialize(self) -> None:
        """
        Create the ClobClient and derive API credentials.
        Must be called once at startup.
        """
        if not settings.polygon_private_key:
            log.warning("clob_no_private_key",
                        msg="CLOB client requires POLYGON_PRIVATE_KEY")
            return

        # signature_type=1 (POLY_PROXY) for Magic Link / proxy wallet accounts.
        # signature_type=0 (EOA) for plain private-key wallets.
        sig_type = settings.clob_signature_type
        funder = settings.clob_funder_address or None

        self._client = ClobClient(
            settings.polymarket_clob_url,
            key=settings.polygon_private_key,
            chain_id=settings.chain_id,
            signature_type=sig_type,
            funder=funder,
        )

        # Use pre-derived API key if provided, otherwise derive fresh
        if settings.polymarket_api_key:
            # The API key UUID maps to derived creds stored server-side.
            # Just set the key; secret/passphrase are derived from the same signing.
            self._creds = self._client.derive_api_key()
            log.info("clob_using_configured_api_key",
                     api_key=settings.polymarket_api_key[:8] + "...")
        else:
            self._creds = self._client.derive_api_key()

        self._client.set_api_creds(self._creds)

        log.info(
            "clob_initialized",
            chain_id=settings.chain_id,
            signature_type=sig_type,
            funder=funder[:10] + "..." if funder else "none",
            eoa=settings.polygon_wallet_address[:10] + "..." if settings.polygon_wallet_address else "derived",
        )

    @property
    def client(self) -> ClobClient:
        if self._client is None:
            raise RuntimeError(
                "CLOBClient not initialized. Call initialize() first.")
        return self._client

    # ── Order Operations ─────────────────────────────────────────

    @with_retry(max_attempts=2, min_wait=1.0, retry_on=(Exception,))
    async def place_limit_order(
        self,
        token_id: str,
        side: str,
        price: float,
        size: float,
        neg_risk: bool = True,
        tick_size: str = "0.01",
    ) -> OrderResult:
        """
        Place a limit order on the CLOB.

        Args:
            token_id: The conditional token ID (YES or NO token)
            side: "BUY" or "SELL"
            price: Limit price (0.01 to 0.99)
            size: Number of shares
            neg_risk: Whether this market uses the NegRisk exchange (most binary markets do)
            tick_size: Market tick size (from market metadata)
        """
        try:
            # Builder fee: 1% (100 bps) earns revenue on every trade
            fee_bps = 100 if settings.builder_wallet_address else 0

            order_args = OrderArgs(
                token_id=token_id,
                price=price,
                size=size,
                side=BUY if side == "BUY" else SELL,
                fee_rate_bps=fee_bps,
            )

            options = PartialCreateOrderOptions(
                tick_size=tick_size,
                neg_risk=neg_risk,
            )

            # create_order signs the order; post_order sends it to CLOB
            signed_order = self.client.create_order(order_args, options)
            resp = self.client.post_order(
                signed_order, orderType=OrderType.GTC)

            order_id = resp.get("orderID", "")
            log.info(
                "order_placed",
                order_id=order_id,
                token_id=token_id[:16] + "...",
                side=side,
                price=price,
                size=size,
                neg_risk=neg_risk,
            )
            return OrderResult(success=True, order_id=order_id, raw=resp)

        except Exception as exc:
            log.error("order_failed", error=str(exc),
                      side=side, price=price, size=size)
            return OrderResult(success=False, error=str(exc))

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order."""
        try:
            self.client.cancel(order_id)
            log.info("order_cancelled", order_id=order_id)
            return True
        except Exception as exc:
            log.error("cancel_failed", order_id=order_id, error=str(exc))
            return False

    async def cancel_all(self) -> bool:
        """Cancel all open orders."""
        try:
            self.client.cancel_all()
            log.info("all_orders_cancelled")
            return True
        except Exception as exc:
            log.error("cancel_all_failed", error=str(exc))
            return False

    # ── Book Queries ─────────────────────────────────────────────

    def get_order_book(self, token_id: str) -> dict[str, Any]:
        """Get the order book for a token."""
        return self.client.get_order_book(token_id)

    def get_midpoint(self, token_id: str) -> float:
        """Get the midpoint price for a token."""
        try:
            book = self.get_order_book(token_id)
            bids = book.get("bids", [])
            asks = book.get("asks", [])
            if bids and asks:
                best_bid = float(bids[0]["price"])
                best_ask = float(asks[0]["price"])
                return (best_bid + best_ask) / 2
            if bids:
                return float(bids[0]["price"])
            if asks:
                return float(asks[0]["price"])
            return 0.5
        except Exception:
            return 0.5

    def get_spread(self, token_id: str) -> float:
        """Get the bid-ask spread for a token."""
        try:
            book = self.get_order_book(token_id)
            bids = book.get("bids", [])
            asks = book.get("asks", [])
            if bids and asks:
                return float(asks[0]["price"]) - float(bids[0]["price"])
            return 1.0
        except Exception:
            return 1.0

    # ── Position Queries ─────────────────────────────────────────

    def get_open_orders(self) -> list[dict[str, Any]]:
        """Get all open orders for the wallet."""
        try:
            return self.client.get_orders()
        except Exception as exc:
            log.error("get_orders_failed", error=str(exc))
            return []


# Singleton
clob = CLOBClient()
