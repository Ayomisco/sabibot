"""
SabiBot — main entry point.

Orchestrates all subsystems:
  1. Initialize database, CLOB client, AI gateway
  2. Start Telegram bot (background)
  3. Start WebSocket price feed (background)
  4. Schedule periodic tasks:
     - News scan (every 60s)
     - Market analysis (every 5min)
     - Portfolio rebalance (every 1hr)
  5. Run forever
"""

from __future__ import annotations

import asyncio
import signal
import sys

from src.config import settings
from src.db.database import async_session, init_db
from src.db.models import Signal
from src.execution.order_manager import order_manager
from src.execution.portfolio import portfolio
from src.execution.risk_manager import risk_manager
from src.intelligence.market_analyzer import analyze_news_item
from src.intelligence.news_scanner import scan_all_sources
from src.intelligence.signal_aggregator import aggregate_signals
from src.interface.telegram_bot import is_paused, start_telegram_bot, stop_telegram_bot
from src.polymarket.client import Market, polymarket
from src.polymarket.clob import clob
from src.strategies.base import TradeProposal
from src.strategies.cross_market_arb import CrossMarketArbStrategy
from src.strategies.market_making import MarketMakingStrategy
from src.strategies.mean_reversion import MeanReversionStrategy
from src.strategies.momentum import MomentumStrategy
from src.strategies.sentiment_trade import SentimentTradeStrategy
from src.strategies.timezone_arb import TimezoneArbStrategy
from src.utils.logger import get_logger, setup_logging
from src.utils.notifications import AlertLevel, send_telegram
from src.utils import scheduler as sched

log = get_logger("main")

# ── Strategy registry ────────────────────────────────────────────
STRATEGIES = [
    TimezoneArbStrategy(),
    SentimentTradeStrategy(),
    CrossMarketArbStrategy(),
    MarketMakingStrategy(),
    MomentumStrategy(),
    MeanReversionStrategy(),
]

# ── Cached state ─────────────────────────────────────────────────
_markets_cache: list[Market] = []
_signals_cache: dict = {}


async def _refresh_markets() -> None:
    """Fetch active markets and update cache."""
    global _markets_cache
    _markets_cache = await polymarket.get_all_active_markets(max_markets=300)
    log.info("markets_refreshed", count=len(_markets_cache))


async def news_scan_cycle() -> None:
    """One iteration of the news scan → analyze → trade pipeline."""
    if is_paused():
        return

    try:
        # 1. Scan for new news items
        new_items = await scan_all_sources()
        if not new_items:
            return

        # 2. Ensure we have fresh market data
        if not _markets_cache:
            await _refresh_markets()

        # 3. Analyze each news item
        all_proposals: list[TradeProposal] = []
        signals_this_cycle: list[Signal] = []

        # Process up to 10 items per cycle (was 5 — more coverage)
        for news_item in new_items[:10]:
            opportunities = await analyze_news_item(
                headline=news_item.title,
                body=news_item.summary,
                source=news_item.source,
                markets=_markets_cache,
            )

            if not opportunities:
                continue

            # 4. Build aggregated signals + persist to DB
            for opp in opportunities:
                cid = opp.market.condition_id
                market_price = opp.market.outcome_prices.get("Yes", 0.5)
                agg = aggregate_signals([opp.signal], market_price)
                _signals_cache[cid] = agg
                signals_this_cycle.append(opp.signal)

        # Persist all new signals to database
        if signals_this_cycle:
            async with async_session() as session:
                for sig in signals_this_cycle:
                    session.add(sig)
                await session.commit()
            log.info("signals_saved", count=len(signals_this_cycle))

        # 5. Run all strategies with current signals
        for strategy in STRATEGIES:
            if not strategy.enabled:
                continue
            try:
                proposals = await strategy.evaluate(_markets_cache, _signals_cache)
                all_proposals.extend(proposals)
            except Exception as exc:
                log.error("strategy_error",
                          strategy=strategy.name, error=str(exc))

        # 6. Execute approved proposals
        if all_proposals:
            # Sort by edge * confidence (best opportunities first)
            all_proposals.sort(key=lambda p: abs(p.edge) *
                               p.confidence, reverse=True)
            # Max 3 trades per cycle
            trades = await order_manager.execute_proposals(all_proposals[:3])
            log.info("cycle_complete", proposals=len(
                all_proposals), executed=len(trades))

    except Exception as exc:
        log.error("news_scan_cycle_error", error=str(exc))
        await send_telegram(f"Scan cycle error: {str(exc)[:200]}", AlertLevel.ERROR)


async def market_refresh_cycle() -> None:
    """Refresh market data and update strategy baselines."""
    try:
        await _refresh_markets()

        # Update mean reversion baselines
        for strategy in STRATEGIES:
            if isinstance(strategy, MeanReversionStrategy):
                for market in _markets_cache:
                    yes_price = market.outcome_prices.get("Yes", 0.5)
                    strategy.update_baseline(market.condition_id, yes_price)

    except Exception as exc:
        log.error("market_refresh_error", error=str(exc))


async def portfolio_cycle() -> None:
    """Periodic portfolio snapshot and risk update."""
    try:
        summary = await portfolio.get_summary()
        risk_manager.update_portfolio_value(summary.total_value_usd)
        await portfolio.take_snapshot(summary)
        log.info(
            "portfolio_snapshot",
            value=f"${summary.total_value_usd:.2f}",
            positions=len(summary.open_positions),
            pnl_today=f"${summary.realized_pnl_today:+.2f}",
        )
    except Exception as exc:
        log.error("portfolio_cycle_error", error=str(exc))


async def run() -> None:
    """Main async entry point."""
    setup_logging()
    log.info(
        "sabibot_starting",
        mode=settings.trading_mode.value,
        primary_llm=settings.llm_primary_provider.value,
        chain_id=settings.chain_id,
    )

    # Initialize subsystems
    await init_db()

    if settings.is_live and settings.polygon_private_key:
        await clob.initialize()

    # Start Telegram bot
    telegram_app = await start_telegram_bot()

    # Fetch initial market data
    await _refresh_markets()

    # ── Balance check: log USDC in both EOA and proxy wallet ─────
    if settings.is_live:
        try:
            from web3 import Web3
            from web3.middleware import ExtraDataToPOAMiddleware
            USDC_CONTRACT = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
            USDC_ABI = [{"inputs":[{"name":"account","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"stateMutability":"view","type":"function"}]
            w3 = Web3(Web3.HTTPProvider(settings.polygon_rpc_url))
            w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
            usdc = w3.eth.contract(address=Web3.to_checksum_address(USDC_CONTRACT), abi=USDC_ABI)

            # Check whichever address holds funds
            check_addr = settings.clob_funder_address or settings.polygon_wallet_address
            raw_balance = usdc.functions.balanceOf(Web3.to_checksum_address(check_addr)).call()
            usdc_balance = raw_balance / 1_000_000  # 6 decimals

            log.info("usdc_balance", wallet=check_addr[:10] + "...", usdc=f"${usdc_balance:.2f}")
            await send_telegram(
                f"Started in LIVE mode\n"
                f"Funder wallet: {check_addr[:10]}...\n"
                f"On-chain USDC: ${usdc_balance:.2f}\n"
                f"Markets loaded: {len(_markets_cache)}",
                AlertLevel.INFO,
            )
        except Exception as exc:
            log.warning("balance_check_failed", error=str(exc))

    # Schedule periodic tasks
    sched.add_interval_job(
        news_scan_cycle, settings.news_scan_interval_seconds, job_id="news_scan")
    sched.add_interval_job(
        market_refresh_cycle, settings.market_analysis_interval_seconds, job_id="market_refresh")
    sched.add_interval_job(
        portfolio_cycle, settings.portfolio_rebalance_interval_seconds, job_id="portfolio")
    sched.start()

    await send_telegram(
        f"Bot started in {settings.trading_mode.value} mode\n"
        f"LLM: {settings.llm_primary_provider.value}\n"
        f"Markets loaded: {len(_markets_cache)}",
        AlertLevel.INFO,
    )

    log.info("sabibot_running", markets=len(_markets_cache))

    # Run forever — handle graceful shutdown
    stop_event = asyncio.Event()

    def handle_signal(sig: int, frame) -> None:
        log.info("shutdown_signal_received", signal=sig)
        stop_event.set()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    try:
        await stop_event.wait()
    finally:
        log.info("sabibot_shutting_down")
        sched.shutdown()
        await stop_telegram_bot(telegram_app)
        await polymarket.close()
        await send_telegram("Bot stopped.", AlertLevel.WARNING)


def main() -> None:
    """Sync entry point."""
    asyncio.run(run())


if __name__ == "__main__":
    main()
