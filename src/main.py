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
from datetime import datetime, timedelta, timezone

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
from src.strategies.scalp_strategy import ScalpStrategy
from src.strategies.sentiment_trade import SentimentTradeStrategy
from src.strategies.timezone_arb import TimezoneArbStrategy
from src.execution.exit_manager import exit_manager
from src.utils.logger import get_logger, setup_logging
from src.utils.notifications import AlertLevel, send_telegram
from src.utils import scheduler as sched

log = get_logger("main")

# ── Strategy registry ────────────────────────────────────────────
STRATEGIES = [
    TimezoneArbStrategy(),
    SentimentTradeStrategy(),
    ScalpStrategy(),
    CrossMarketArbStrategy(),
    MarketMakingStrategy(),
    MomentumStrategy(),
    MeanReversionStrategy(),
]

# ── Cached state ─────────────────────────────────────────────────
_markets_cache: list[Market] = []
_signals_cache: dict = {}


async def _refresh_markets() -> None:
    """Fetch active markets and update cache. Retries up to 3x with backoff."""
    global _markets_cache

    for attempt in range(1, 4):
        try:
            # No hard timeout — Railway cold start can be slow.
            # get_all_active_markets has its own internal timeout via httpx (30s).
            markets = await polymarket.get_all_active_markets(max_markets=300)
            if markets:
                # Include markets with no end_date (CLOB /sampling-markets rarely returns
                # end_date_iso, so None-end_date markets must be kept).
                # Only exclude markets with a known end_date beyond the configured window.
                deadline = datetime.now(timezone.utc) + timedelta(
                    hours=settings.max_market_resolution_hours)
                filtered = [
                    m for m in markets
                    if m.end_date is None or m.end_date <= deadline
                ]
                log.info("markets_refreshed",
                         total=len(markets),
                         within_window=len(filtered),
                         window_hours=settings.max_market_resolution_hours)
                _markets_cache = filtered if filtered else markets
                return
            else:
                log.warning("markets_empty_response", attempt=attempt)
                if attempt < 3:
                    await asyncio.sleep(5 * attempt)
        except Exception as exc:
            log.error("markets_error", attempt=attempt, error=str(exc)[:120])
            if attempt < 3:
                await asyncio.sleep(5 * attempt)

    log.critical("markets_load_failed", cached=len(_markets_cache))
    if not _markets_cache:
        await send_telegram(
            "Markets failed to load (API unreachable?). Bot cannot trade.",
            AlertLevel.ERROR,
        )


async def news_scan_cycle() -> None:
    """One iteration of the news scan → analyze → trade pipeline."""
    if is_paused():
        return

    try:
        # 1. Always ensure markets are loaded first
        if not _markets_cache:
            log.info("markets_empty_before_scan_reloading")
            await _refresh_markets()
            if not _markets_cache:
                log.warning("markets_still_empty_skipping_scan")
                return

        # 2. Scan for new news items
        new_items = await scan_all_sources()
        if not new_items:
            log.debug("scan_cycle_no_new_news")
            return

        log.info("scan_cycle_start", new_items=len(new_items),
                 markets=len(_markets_cache))

        # 3. Analyze each news item
        all_proposals: list[TradeProposal] = []
        signals_this_cycle: list[Signal] = []

        # Process up to 3 items per cycle — Groq free tier is 30 RPM.
        # Each item = 1 sentiment call + up to 2 probability calls = 3 calls max.
        # 3 items × 3 calls = 9 calls; the rate limiter in llm.py enforces 2.5s gaps
        # → ~22.5s of Groq time per cycle, well under the 45s window.
        for news_item in new_items[:3]:
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


async def scalp_exit_cycle() -> None:
    """Close any scalp positions that have exceeded their hold time."""
    if is_paused():
        return
    try:
        await exit_manager.check_and_exit_scalps(_markets_cache)
    except Exception as exc:
        log.error("scalp_exit_cycle_error", error=str(exc))


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


async def _market_retry_background() -> None:
    """Background safety-net: retry market load every 30s while cache is empty.

    Runs independently of APScheduler so it works even if the scheduler
    has trouble with async jobs on certain Railway builds.
    Stops retrying once markets are loaded (they are kept fresh by the
    scheduled market_refresh_cycle afterwards).
    """
    while not _markets_cache:
        await asyncio.sleep(30)
        if not _markets_cache:
            log.info("background_market_retry_attempt")
            await _refresh_markets()
    log.info("background_market_retry_done", count=len(_markets_cache))


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

    # Fetch markets FIRST — retry every 10s for up to 90s if needed
    await _refresh_markets()
    if not _markets_cache:
        log.warning("startup_markets_failed_retrying")
        for retry in range(1, 7):
            await asyncio.sleep(10)
            log.info("startup_markets_retry", attempt=retry)
            await _refresh_markets()
            if _markets_cache:
                break

    if _markets_cache:
        log.info("startup_markets_loaded", count=len(_markets_cache))
    else:
        log.error("startup_markets_all_attempts_failed")

    # CLOB auth (can be slow/fail — don't block other startup)
    if settings.is_live and settings.polygon_private_key:
        try:
            await clob.initialize()
        except Exception as exc:
            log.error("clob_init_failed", error=str(exc))
            await send_telegram(
                f"CLOB auth failed: {str(exc)[:100]}\n"
                "Bot will still scan news but CANNOT place trades.",
                AlertLevel.ERROR,
            )

    # Start Telegram bot
    telegram_app = await start_telegram_bot()

    # ── Balance check: query Polymarket CLOB internal balance ───
    if settings.is_live:
        try:
            usdc_balance = await clob.get_balance()
            log.info("clob_balance", usdc=f"${usdc_balance:.2f}")
            funder = settings.clob_funder_address or settings.polygon_wallet_address
            await send_telegram(
                f"Started in LIVE mode\n"
                f"API wallet: {funder[:10]}...\n"
                f"Available to trade: ${usdc_balance:.2f} USDC\n"
                f"Markets loaded: {len(_markets_cache)}",
                AlertLevel.INFO,
            )
            if usdc_balance < 1.0:
                log.warning("low_clob_balance", usdc=usdc_balance)
                await send_telegram(
                    f"Low balance: ${usdc_balance:.2f} — top up via Polymarket Deposit",
                    AlertLevel.WARNING,
                )
        except Exception as exc:
            log.warning("balance_check_failed", error=str(exc))

    # Alert now if markets still empty (Telegram bot is running at this point)
    if not _markets_cache:
        await send_telegram(
            "Markets failed to load at startup.\n"
            "Run /refresh to reload — bot cannot trade until markets are available.",
            AlertLevel.WARNING,
        )

    # Schedule periodic tasks
    sched.add_interval_job(
        news_scan_cycle, settings.news_scan_interval_seconds, job_id="news_scan")
    sched.add_interval_job(
        market_refresh_cycle, settings.market_analysis_interval_seconds, job_id="market_refresh")
    sched.add_interval_job(
        portfolio_cycle, settings.portfolio_rebalance_interval_seconds, job_id="portfolio")
    sched.add_interval_job(
        scalp_exit_cycle, settings.scalp_exit_check_seconds, job_id="scalp_exit")
    sched.start()

    # Background safety-net: retry market load every 30s if still empty
    if not _markets_cache:
        asyncio.create_task(_market_retry_background())

    startup_suffix = "\n⚠️ Run /refresh — markets not loaded." if not _markets_cache else ""
    startup_level = AlertLevel.WARNING if not _markets_cache else AlertLevel.INFO
    await send_telegram(
        f"Bot started in {settings.trading_mode.value} mode\n"
        f"LLM: {settings.llm_primary_provider.value}\n"
        f"Markets loaded: {len(_markets_cache)}"
        + startup_suffix,
        startup_level,
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
