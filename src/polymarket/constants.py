"""Polymarket constants — addresses, chain config, API endpoints."""

# ── Chain IDs ────────────────────────────────────────────────────
POLYGON_MAINNET = 137
AMOY_TESTNET = 80002

# ── Contract Addresses (Polygon Mainnet) ─────────────────────────
# Polymarket CTF Exchange (Conditional Token Framework)
CTF_EXCHANGE = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"

# Neg Risk CTF Exchange (for binary markets)
NEG_RISK_CTF_EXCHANGE = "0xC5d563A36AE78145C45a50134d48A1215220f80a"

# Neg Risk Adapter
NEG_RISK_ADAPTER = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"

# USDC on Polygon
USDC_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"

# Conditional Tokens
CONDITIONAL_TOKENS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"

# ── API Endpoints ────────────────────────────────────────────────
CLOB_BASE_URL = "https://clob.polymarket.com"
GAMMA_BASE_URL = "https://gamma-api.polymarket.com"

# ── Signature Types ──────────────────────────────────────────────
# CRITICAL: EOA wallets use type 0 (ECDSA). Type 2 is for Gnosis Safe proxies.
SIGNATURE_TYPE_EOA = 0          # ECDSA — standard wallet signing
SIGNATURE_TYPE_POLY_PROXY = 1  # Polymarket proxy wallet
SIGNATURE_TYPE_POLY_GNOSIS = 2  # Gnosis Safe (NOT for standard EOA!)

# ── Order Types ──────────────────────────────────────────────────
BUY = "BUY"
SELL = "SELL"

# Token IDs for binary markets: index 0 = YES, index 1 = NO
YES_TOKEN_INDEX = 0
NO_TOKEN_INDEX = 1

# ── Rate Limits ──────────────────────────────────────────────────
MAX_REQUESTS_PER_SECOND = 10
ORDER_BOOK_DEPTH = 20
