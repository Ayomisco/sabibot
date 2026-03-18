"""Central configuration loaded from environment variables."""

from __future__ import annotations

from enum import Enum
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class TradingMode(str, Enum):
    LIVE = "live"
    PAPER = "paper"
    BACKTEST = "backtest"


class LLMProvider(str, Enum):
    GROQ = "groq"
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

    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-20250514"

    openai_api_key: str = ""
    openai_embedding_model: str = "text-embedding-3-small"

    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1:8b"

    llm_primary_provider: LLMProvider = LLMProvider.GROQ
    llm_fallback_provider: LLMProvider = LLMProvider.ANTHROPIC

    # ── Polymarket / Polygon ─────────────────────────────────────
    polygon_private_key: str = ""
    polygon_wallet_address: str = ""
    polygon_rpc_url: str = "https://polygon-rpc.com"
    polymarket_clob_url: str = "https://clob.polymarket.com"
    polymarket_ws_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    chain_id: int = 137

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


# Singleton — import this everywhere
settings = Settings()
