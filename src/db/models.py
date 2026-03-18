"""Database models — every trade, signal, and market cached here."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from sqlmodel import Field, SQLModel


# ── Enums ────────────────────────────────────────────────────────

class TradeSide(str, Enum):
    BUY_YES = "BUY_YES"
    BUY_NO = "BUY_NO"
    SELL_YES = "SELL_YES"
    SELL_NO = "SELL_NO"


class TradeStatus(str, Enum):
    PENDING = "PENDING"
    FILLED = "FILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class SignalDirection(str, Enum):
    BUY_YES = "BUY_YES"
    BUY_NO = "BUY_NO"
    NEUTRAL = "NEUTRAL"


# ── Models ───────────────────────────────────────────────────────

class MarketCache(SQLModel, table=True):
    """Cached Polymarket market data."""
    __tablename__ = "market_cache"

    id: Optional[int] = Field(default=None, primary_key=True)
    condition_id: str = Field(index=True, unique=True)
    question: str
    description: str = ""
    category: str = ""
    end_date: Optional[datetime] = None
    outcome_yes_price: float = 0.5
    outcome_no_price: float = 0.5
    volume_24h: float = 0.0
    liquidity: float = 0.0
    active: bool = True
    # Embedding stored as JSON array of floats (or null if not yet computed)
    question_embedding: Optional[str] = None
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Trade(SQLModel, table=True):
    """Every trade placed by the bot."""
    __tablename__ = "trades"

    id: Optional[int] = Field(default=None, primary_key=True)
    condition_id: str = Field(index=True)
    market_question: str = ""
    strategy: str = ""
    side: TradeSide
    amount_usd: float
    price: float
    shares: float = 0.0
    edge: float = 0.0
    confidence: float = 0.0
    status: TradeStatus = TradeStatus.PENDING
    order_id: str = ""
    pnl: Optional[float] = None
    exit_price: Optional[float] = None
    exit_at: Optional[datetime] = None
    notes: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Signal(SQLModel, table=True):
    """Raw intelligence signals before aggregation."""
    __tablename__ = "signals"

    id: Optional[int] = Field(default=None, primary_key=True)
    source: str  # e.g. "rss:bbc", "gnews", "llm_reasoning"
    headline: str
    summary: str = ""
    direction: SignalDirection = SignalDirection.NEUTRAL
    probability_estimate: Optional[float] = None
    magnitude: float = 0.0  # 0-1 strength
    reliability: float = 0.5  # 0-1 source reliability weight
    matched_condition_id: Optional[str] = Field(default=None, index=True)
    matched_score: float = 0.0
    raw_sentiment: float = 0.0  # -1 to +1
    processed: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)


class PortfolioSnapshot(SQLModel, table=True):
    """Periodic portfolio state snapshots for drawdown tracking."""
    __tablename__ = "portfolio_snapshots"

    id: Optional[int] = Field(default=None, primary_key=True)
    total_value_usd: float
    cash_usd: float
    positions_value_usd: float
    unrealized_pnl: float = 0.0
    realized_pnl_today: float = 0.0
    drawdown_from_peak: float = 0.0
    open_positions: int = 0
    created_at: datetime = Field(default_factory=datetime.utcnow)


class NewsItem(SQLModel, table=True):
    """Deduplicated news items to avoid processing the same story twice."""
    __tablename__ = "news_items"

    id: Optional[int] = Field(default=None, primary_key=True)
    source: str
    title: str
    url: str = Field(index=True, unique=True)
    summary: str = ""
    published_at: Optional[datetime] = None
    processed: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)
