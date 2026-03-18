"""Notification dispatch — Telegram alerts for trade events and errors."""

from __future__ import annotations

import asyncio
from enum import Enum

import httpx

from src.config import settings
from src.utils.logger import get_logger

log = get_logger("notifications")


class AlertLevel(str, Enum):
    INFO = "ℹ️"
    TRADE = "💰"
    WARNING = "⚠️"
    ERROR = "🚨"
    PROFIT = "🟢"
    LOSS = "🔴"


_http: httpx.AsyncClient | None = None


def _client() -> httpx.AsyncClient:
    global _http
    if _http is None or _http.is_closed:
        _http = httpx.AsyncClient(timeout=10.0)
    return _http


async def send_telegram(message: str, level: AlertLevel = AlertLevel.INFO) -> None:
    """Send a Telegram message. Silent failure — notifications must never crash the bot."""
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        log.debug("telegram_not_configured")
        return

    text = f"{level.value} SabiBot\n{message}"
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    payload = {
        "chat_id": settings.telegram_chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }

    try:
        resp = await _client().post(url, json=payload)
        if resp.status_code != 200:
            log.warning("telegram_send_failed",
                        status=resp.status_code, body=resp.text[:200])
    except Exception as exc:
        log.warning("telegram_send_error", error=str(exc))


def send_telegram_sync(message: str, level: AlertLevel = AlertLevel.INFO) -> None:
    """Sync wrapper for contexts without an event loop."""
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(send_telegram(message, level))
    except RuntimeError:
        asyncio.run(send_telegram(message, level))


async def notify_trade(
    action: str, market: str, side: str, amount: float, price: float, edge: float,
    condition_id: str = "",
) -> None:
    msg = (
        f"{action}\n"
        f"Market: {market[:80]}\n"
        f"Side: {side} @ ${price:.3f}\n"
        f"Size: ${amount:.2f}\n"
        f"Edge: {edge:+.1%}\n"
        f"View: https://polymarket.com/"
    )
    await send_telegram(msg, AlertLevel.TRADE)


async def notify_error(context: str, error: str) -> None:
    msg = f"Error in {context}\n{error[:300]}"
    await send_telegram(msg, AlertLevel.ERROR)


async def notify_daily_summary(
    pnl: float, win_rate: float, trades: int, balance: float
) -> None:
    level = AlertLevel.PROFIT if pnl >= 0 else AlertLevel.LOSS
    msg = (
        f"Daily Summary\n"
        f"P&L: ${pnl:+.2f}\n"
        f"Win Rate: {win_rate:.0%}\n"
        f"Trades: {trades}\n"
        f"Balance: ${balance:.2f}"
    )
    await send_telegram(msg, level)
