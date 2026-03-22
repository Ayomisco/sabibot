"""Central configuration loaded from environment variables."""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class TradingMode(str, Enum):
    LIVE = "live"
    PAPER = "paper"
    BACKTEST = "backtest"


class LLMProvider(str, Enum):
    GROQ = "groq"
    GEMINI = "gemini"
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    OLLAMA = "ollama"
    NONE = "none"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── LLM Providers ────────────────────────────────────────────
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"

    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash"

    anthropic_api_key: str = ""
    anthropic_model: str = "claude-haiku-4-5-20251001"

    openai_api_key: str = ""
    openai_embedding_model: str = "text-embedding-3-small"

    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1:8b"

    llm_primary_provider: LLMProvider = LLMProvider.GROQ
    llm_fallback_provider: LLMProvider = LLMProvider.NONE

    @field_validator("llm_primary_provider", "llm_fallback_provider", mode="before")
    @classmethod
    def coerce_llm_provider(cls, v: Any) -> str:
        """Accept valid provider names; fall back to 'none' for anything unrecognised.
        Prevents a bad Railway env var from crashing the entire bot at startup."""
        valid = {p.value for p in LLMProvider}
        if isinstance(v, str) and v.lower() not in valid:
            return LLMProvider.NONE.value
        return v

    # ── Polymarket / Polygon ─────────────────────────────────────
    polygon_private_key: str = ""
    polygon_wallet_address: str = ""  # EOA address (signer)
    # Proxy wallet — for Magic Link / social login accounts.
    # This is the Polymarket-managed wallet that actually holds USDC.
    # If set, orders are signed with signature_type=1 (POLY_PROXY) and
    # this is passed as the 'funder' so the CLOB draws funds from it.
    # If blank, EOA is used directly (signature_type=0).
    polymarket_proxy_wallet: str = ""
    # Pre-derived CLOB API key (UUID). If blank, derived at startup via signing.
    polymarket_api_key: str = ""
    polygon_rpc_url: str = "https://polygon-rpc.com"
    polymarket_clob_url: str = "https://clob.polymarket.com"
    polymarket_ws_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    chain_id: int = 137
    builder_wallet_address: str = ""

    # ── Telegram ─────────────────────────────────────────────────
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # ── News Sources ─────────────────────────────────────────────
    gnews_api_key: str = ""

    # ── Database ─────────────────────────────────────────────────
    database_url: str = "sqlite+aiosqlite:///data/sabibot.db"

    # ── Risk Management ──────────────────────────────────────────
    max_position_size_usd: float = 50.0
    max_portfolio_exposure_usd: float = 500.0
    max_single_market_pct: float = 0.05
    max_drawdown_pct: float = 0.20
    kelly_multiplier: float = 0.25
    min_edge_threshold: float = 0.08
    min_confidence: float = 0.6

    # ── Bot Behavior ─────────────────────────────────────────────
    trading_mode: TradingMode = TradingMode.PAPER
    news_scan_interval_seconds: int = 60
    market_analysis_interval_seconds: int = 300
    portfolio_rebalance_interval_seconds: int = 3600
    log_level: str = "INFO"

    # ── Derived ──────────────────────────────────────────────────
    @property
    def data_dir(self) -> Path:
        p = Path("data")
        p.mkdir(exist_ok=True)
        return p

    @property
    def is_live(self) -> bool:
        return self.trading_mode == TradingMode.LIVE

    @property
    def is_paper(self) -> bool:
        return self.trading_mode == TradingMode.PAPER

    @property
    def clob_signature_type(self) -> int:
        """Returns 1 (POLY_PROXY) for Magic Link accounts, 0 (EOA) otherwise."""
        return 1 if self.polymarket_proxy_wallet else 0

    @property
    def clob_funder_address(self) -> str:
        """The address that funds orders — proxy wallet if set, else EOA."""
        return self.polymarket_proxy_wallet or self.polygon_wallet_address


# Singleton — import this everywhere
settings = Settings()
