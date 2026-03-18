"""
Polymarket CLOB signing — EIP-712 order signing and API key generation.

CRITICAL: EOA wallets use signature_type=0 (ECDSA).
The py-clob-client default of type 2 is for Gnosis Safe proxies only.
"""

from __future__ import annotations

from eth_account import Account
from eth_account.messages import encode_defunct

from src.config import settings
from src.utils.logger import get_logger

log = get_logger("signing")


def get_account() -> Account:
    """Get the eth_account.Account from the private key."""
    return Account.from_key(settings.polygon_private_key)


def sign_message(message: str) -> str:
    """Sign a plain text message with the EOA private key."""
    account = get_account()
    msg = encode_defunct(text=message)
    signed = account.sign_message(msg)
    return signed.signature.hex()


def get_address() -> str:
    """Get the wallet address derived from the private key."""
    if settings.polygon_wallet_address:
        return settings.polygon_wallet_address
    return get_account().address
