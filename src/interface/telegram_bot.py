"""
Telegram bot — real-time control and monitoring interface.

Commands:
  /status   — Portfolio summary, P&L, drawdown
  /trades   — Recent trades
  /balance  — Current USDC balance on Polygon
  /markets  — Top watched markets and prices
  /pause    — Pause trading (no new orders)
  /resume   — Resume trading
  /reset    — Reset drawdown halt
  /kill     — Emergency: cancel all orders and halt
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

from src.config import settings
from src.execution.portfolio import portfolio
from src.execution.risk_manager import risk_manager
from src.utils.logger import get_logger

log = get_logger("telegram_bot")

_paused = False


def _authorized(func):
    """Decorator: only allow commands from the configured chat_id."""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if str(update.effective_chat.id) != settings.telegram_chat_id:
            await update.message.reply_text("Unauthorized.")
            return
        return await func(update, context)
    return wrapper


@_authorized
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show portfolio status."""
    summary = await portfolio.get_summary()
    risk_status = risk_manager.get_status()

    msg = (
        f"📊 *SabiBot Status*\n"
        f"Mode: `{settings.trading_mode.value}`\n"
        f"Paused: `{_paused}`\n\n"
        f"💰 *Portfolio*\n"
        f"Value: ${summary.total_value_usd:.2f}\n"
        f"Positions: {len(summary.open_positions)}\n"
        f"Unrealized P&L: ${summary.unrealized_pnl:+.2f}\n"
        f"Today's P&L: ${summary.realized_pnl_today:+.2f}\n"
        f"Total P&L: ${summary.realized_pnl_total:+.2f}\n"
        f"Win Rate: {summary.win_rate:.0%}\n"
        f"Total Trades: {summary.total_trades}\n\n"
        f"🛡️ *Risk*\n"
        f"Exposure: ${risk_status['total_exposure']:.2f}\n"
        f"Drawdown: {risk_status['drawdown']:.1%}\n"
        f"Halted: `{risk_status['halted']}`"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


@_authorized
async def cmd_trades(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show recent trades."""
    positions = await portfolio.get_open_positions()
    if not positions:
        await update.message.reply_text("No open positions.")
        return

    lines = ["📋 *Open Positions*\n"]
    for p in positions[:10]:
        emoji = "🟢" if p.unrealized_pnl >= 0 else "🔴"
        lines.append(
            f"{emoji} {p.market_question[:50]}\n"
            f"   {p.side} | {p.shares:.1f} shares @ ${p.entry_price:.3f}\n"
            f"   Now: ${p.current_price:.3f} | P&L: ${p.unrealized_pnl:+.2f}"
        )

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


@_authorized
async def cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global _paused
    _paused = True
    await update.message.reply_text("⏸️ Trading paused. No new orders will be placed.")
    log.warning("trading_paused_via_telegram")


@_authorized
async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global _paused
    _paused = False
    await update.message.reply_text("▶️ Trading resumed.")
    log.info("trading_resumed_via_telegram")


@_authorized
async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    risk_manager.reset_drawdown_halt()
    await update.message.reply_text("🔄 Drawdown halt reset. Trading may resume.")


@_authorized
async def cmd_kill(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Emergency stop — cancel all orders and halt."""
    global _paused
    _paused = True

    from src.polymarket.clob import clob
    cancelled = await clob.cancel_all()

    msg = "🚨 *EMERGENCY STOP*\nAll orders cancelled. Trading halted."
    if not cancelled:
        msg += "\n⚠️ Failed to cancel some orders — check manually."

    await update.message.reply_text(msg, parse_mode="Markdown")
    log.error("emergency_kill_via_telegram")


def is_paused() -> bool:
    """Check if trading is paused (used by main loop)."""
    return _paused


async def start_telegram_bot() -> Application | None:
    """Initialize and start the Telegram bot. Returns None if not configured."""
    if not settings.telegram_bot_token:
        log.info("telegram_not_configured")
        return None

    app = Application.builder().token(settings.telegram_bot_token).build()

    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("trades", cmd_trades))
    app.add_handler(CommandHandler("pause", cmd_pause))
    app.add_handler(CommandHandler("resume", cmd_resume))
    app.add_handler(CommandHandler("reset", cmd_reset))
    app.add_handler(CommandHandler("kill", cmd_kill))

    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)

    log.info("telegram_bot_started")
    return app


async def stop_telegram_bot(app: Application | None) -> None:
    if app:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
