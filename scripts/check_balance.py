"""
Polymarket CLOB + wallet balance diagnostics.
Checks both the on-chain USDC wallet balance AND the Polymarket
CLOB internal trading balance (what the bot actually uses to trade).

Run from sabibot/ directory:
  python scripts/check_balance.py
"""

import asyncio

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


def check_onchain_balance(address: str) -> tuple[float, float]:
    """Returns (matic, usdc) for the given address."""
    try:
        from web3 import Web3
        w3 = Web3(Web3.HTTPProvider(settings.polygon_rpc_url))
        if not w3.is_connected():
            print(f"  WARNING: Cannot connect to {settings.polygon_rpc_url}")
            return 0.0, 0.0
        checksum = Web3.to_checksum_address(address)
        matic = float(w3.from_wei(w3.eth.get_balance(checksum), "ether"))
        usdc_contract = w3.eth.contract(
            address=Web3.to_checksum_address(USDC_ADDRESS), abi=USDC_ABI)
        usdc = usdc_contract.functions.balanceOf(checksum).call() / 1e6
        return matic, usdc
    except Exception as e:
        print(f"  WARNING: on-chain check failed: {e}")
        return 0.0, 0.0


async def check_clob_balance() -> tuple[bool, float, float, str]:
    """
    Returns (auth_ok, balance, allowance, error).
    Uses the same flow as the live bot (signature_type, funder).
    """
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import AssetType, BalanceAllowanceParams

    if not settings.polygon_private_key:
        return False, 0.0, 0.0, "POLYGON_PRIVATE_KEY not set"

    sig_type = settings.clob_signature_type
    funder = settings.clob_funder_address or None

    client = ClobClient(
        settings.polymarket_clob_url,
        key=settings.polygon_private_key,
        chain_id=settings.chain_id,
        signature_type=sig_type,
        funder=funder,
    )

    try:
        creds = await asyncio.wait_for(
            asyncio.to_thread(client.derive_api_key), timeout=15.0
        )
        client.set_api_creds(creds)
    except asyncio.TimeoutError:
        return False, 0.0, 0.0, "derive_api_key timed out (>15s)"
    except Exception as e:
        return False, 0.0, 0.0, f"Auth failed: {e}"

    try:
        params = BalanceAllowanceParams(
            asset_type=AssetType.COLLATERAL,
            signature_type=sig_type,
        )
        result = await asyncio.to_thread(client.get_balance_allowance, params)
        if isinstance(result, dict):
            raw_bal = float(result.get("balance", 0))
            raw_allow = float(result.get("allowance", 0))
            # USDC has 6 decimals — raw value 11843111 = $11.84
            balance = raw_bal / 1e6 if raw_bal > 1000 else raw_bal
            allowance = raw_allow / 1e6 if raw_allow > 1000 else raw_allow
        else:
            balance = float(result)
            allowance = 0.0
        return True, balance, allowance, ""
    except Exception as e:
        return True, 0.0, 0.0, f"get_balance_allowance failed: {e}"


async def main() -> None:
    eoa = settings.polygon_wallet_address
    proxy = settings.polymarket_proxy_wallet
    sig_type = settings.clob_signature_type

    print()
    print("=" * 55)
    print("  SabiBot — Full Balance Diagnostic")
    print("=" * 55)
    print(f"  Mode         : {settings.trading_mode.value.upper()}")
    print(f"  EOA wallet   : {eoa[:10]}...{eoa[-6:] if eoa else '(not set)'}")
    if proxy:
        print(f"  Proxy wallet : {proxy[:10]}...{proxy[-6:]}")
        print(f"  Sig type     : 1 (POLY_PROXY / Magic Link)")
    else:
        print(f"  Proxy wallet : (none — using EOA directly)")
        print(f"  Sig type     : 0 (EOA)")
    print()

    # ── 1. On-chain balances ──────────────────────────────────────
    print("[ 1 ] On-chain balances (Polygon)")
    funder = proxy or eoa
    matic, usdc = check_onchain_balance(funder)
    print(f"  Funder address : {funder[:10]}...{funder[-6:]}")
    print(f"  MATIC          : {matic:.4f}")
    print(f"  USDC (wallet)  : ${usdc:.2f}")
    if matic < 0.01:
        print("  ⚠  Very low MATIC — may not have enough gas for approvals")
    print()

    # ── 2. Polymarket CLOB balance ────────────────────────────────
    print("[ 2 ] Polymarket CLOB balance (what the bot trades with)")
    auth_ok, clob_bal, clob_allow, err = await check_clob_balance()

    if not auth_ok:
        print(f"  Auth FAILED : {err}")
    else:
        print(f"  Auth         : OK")
        print(f"  CLOB Balance : ${clob_bal:.2f} USDC")
        print(f"  Allowance    : ${clob_allow:.2f} USDC")
        if err:
            print(f"  Balance err  : {err}")

        if clob_bal == 0.0:
            print()
            print("  *** BALANCE IS $0.00 ***")
            print("  Possible causes:")
            print("  a) USDC not yet deposited on polymarket.com")
            print("  b) Proxy wallet in .env doesn't match your Polymarket account")
            print(f"     .env proxy: {proxy}")
            print("     Check: polymarket.com → profile → your wallet address")
            print("  c) Deposit pending on-chain confirmation (wait 1-2 min)")
        else:
            print()
            print(f"  ✓ READY — bot can trade with ${clob_bal:.2f}")

    # ── 3. Summary ────────────────────────────────────────────────
    print()
    print("[ 3 ] Summary")
    print(f"  On-chain USDC  : ${usdc:.2f}  (in the wallet itself)")
    print(f"  CLOB balance   : ${clob_bal:.2f}  (what the bot can spend)")
    if usdc > 0 and clob_bal == 0:
        print()
        print("  → You have USDC on-chain but $0 in the CLOB.")
        print("    Go to polymarket.com → Deposit to move funds into CLOB.")
    print()


if __name__ == "__main__":
    asyncio.run(main())
