"""Check wallet USDC balance on Polygon."""

import asyncio
from web3 import Web3

from src.config import settings
from src.polymarket.constants import USDC_ADDRESS

# USDC ABI (just balanceOf)
USDC_ABI = [
    {
        "inputs": [{"name": "account", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    }
]


def main() -> None:
    if not settings.polygon_wallet_address:
        print("Set POLYGON_WALLET_ADDRESS in .env")
        return

    w3 = Web3(Web3.HTTPProvider(settings.polygon_rpc_url))
    if not w3.is_connected():
        print(f"Cannot connect to {settings.polygon_rpc_url}")
        return

    # MATIC balance
    matic = w3.eth.get_balance(
        Web3.to_checksum_address(settings.polygon_wallet_address))
    matic_formatted = w3.from_wei(matic, "ether")

    # USDC balance
    usdc_contract = w3.eth.contract(
        address=Web3.to_checksum_address(USDC_ADDRESS),
        abi=USDC_ABI,
    )
    usdc_raw = usdc_contract.functions.balanceOf(
        Web3.to_checksum_address(settings.polygon_wallet_address)
    ).call()
    usdc_formatted = usdc_raw / 1e6  # USDC has 6 decimals

    print(f"\nWallet: {settings.polygon_wallet_address}")
    print(
        f"Chain:  Polygon {'Mainnet' if settings.chain_id == 137 else 'Testnet'}")
    print(f"MATIC:  {matic_formatted:.4f}")
    print(f"USDC:   ${usdc_formatted:.2f}")


if __name__ == "__main__":
    main()
