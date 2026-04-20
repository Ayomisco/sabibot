"""
Telegram bot — real-time control and monitoring interface.

Commands:
  /start    — Welcome message + how the bot works
  /help     — List all commands
  /guide    — Step-by-step guide on how SabiBot works
  /how      — How to fund, trade, and earn money
  /status   — Portfolio summary, P&L, drawdown
  /trades   — Recent trades
  /markets  — Top watched markets and prices
  /signals  — Current signals the bot is tracking
  /pause    — Pause trading (no new orders)
  /resume   — Resume trading
  /reset    — Reset drawdown halt
  /kill     — Emergency: cancel all orders and halt
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, BotCommand, MenuButtonCommands
from telegram.ext import (
    Application,
    CallbackQueryHandler,
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
        chat_id = update.effective_chat.id if update.effective_chat else None
        if str(chat_id) != settings.telegram_chat_id:
            if update.message:
                await update.message.reply_text("Unauthorized.")
            return
        return await func(update, context)
    return wrapper


# ── /start — Welcome ─────────────────────────────────────────────

@_authorized
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Welcome message with interactive menu."""
    from src.main import _markets_cache, _signals_cache
    market_count = len(_markets_cache)
    signal_count = len(_signals_cache)
    status_line = (
        f"Markets: {market_count} loaded | Signals: {signal_count}"
        if market_count else "Markets: loading..."
    )

    msg = (
        "SabiBot\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "Autonomous Polymarket trading agent\n\n"
        f"Mode: {settings.trading_mode.value.upper()}\n"
        f"{status_line}\n\n"
        "Tap a button below or use the Menu button at the bottom of the chat."
    )

    keyboard = [
        [
            InlineKeyboardButton("Portfolio Status", callback_data="status"),
            InlineKeyboardButton("View Markets", callback_data="markets"),
        ],
        [
            InlineKeyboardButton("Active Signals", callback_data="signals"),
            InlineKeyboardButton("Open Trades", callback_data="trades"),
        ],
        [
            InlineKeyboardButton("How It Works", callback_data="guide"),
            InlineKeyboardButton("How To Fund", callback_data="how"),
        ],
        [
            InlineKeyboardButton("All Commands", callback_data="help"),
        ],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(msg, reply_markup=reply_markup)


# ── /help — All commands ─────────────────────────────────────────

@_authorized
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = (
        "SabiBot Commands\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "INFO\n"
        "/start - Welcome + quick start\n"
        "/guide - How the bot works A-Z\n"
        "/how - How to fund & earn\n"
        "/help - This list\n\n"
        "MONITORING\n"
        "/status - Portfolio, P&L, risk\n"
        "/trades - Open positions\n"
        "/markets - Top markets being watched\n"
        "/signals - Active trading signals\n\n"
        "CONTROL\n"
        "/pause - Stop placing new trades\n"
        "/resume - Resume trading\n"
        "/reset - Reset drawdown safety halt\n"
        "/kill - EMERGENCY: cancel all & halt"
    )
    await update.message.reply_text(msg)


# ── /guide — How SabiBot works A-Z ──────────────────────────────

@_authorized
async def cmd_guide(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = (
        "How SabiBot Works (A to Z)\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

        "STEP 1: News Scanning (every 60 seconds)\n"
        "The bot scans 9+ RSS news feeds (Reuters, AP, BBC, "
        "Bloomberg, CoinDesk, etc.) for breaking news.\n\n"

        "STEP 2: AI Analysis\n"
        "Each news item is sent to Groq AI (Llama 3.3 70B) which:\n"
        "- Extracts key entities (people, countries, events)\n"
        "- Classifies sentiment (bullish/bearish/neutral)\n"
        "- Estimates probability impact on markets\n\n"

        "STEP 3: Market Matching\n"
        "The bot matches news to Polymarket markets using:\n"
        "- Named Entity Recognition (spaCy NLP)\n"
        "- Keyword overlap scoring\n"
        "- Semantic similarity\n\n"

        "STEP 4: Signal Aggregation\n"
        "Multiple signals are fused using Bayesian math:\n"
        "- Each signal gets a confidence weight\n"
        "- Fresh signals count more (decay over 4 hours)\n"
        "- Final probability is calculated in log-odds space\n\n"

        "STEP 5: Strategy Evaluation\n"
        "6 strategies compete for the best trades:\n"
        "1. Timezone Arb - Trade when Americans sleep\n"
        "2. Sentiment - Follow breaking news sentiment\n"
        "3. Momentum - Ride price trends\n"
        "4. Mean Reversion - Fade overreactions\n"
        "5. Cross-Market Arb - Exploit related markets\n"
        "6. Market Making - Provide liquidity for spread\n\n"

        "STEP 6: Risk Check\n"
        "Every trade must pass:\n"
        f"- Max position: ${settings.max_position_size_usd}\n"
        f"- Max exposure: ${settings.max_portfolio_exposure_usd}\n"
        f"- Max drawdown: {settings.max_drawdown_pct:.0%}\n"
        "- Kelly criterion sizing (quarter Kelly)\n\n"

        "STEP 7: Execution\n"
        "Approved trades are placed on Polymarket CLOB.\n"
        "You earn builder fees on every trade!\n\n"

        "STEP 8: Monitoring\n"
        "The bot tracks all positions, calculates P&L, "
        "and sends you alerts on Telegram."
    )
    await update.message.reply_text(msg)


# ── /how — How to fund and earn ──────────────────────────────────

@_authorized
async def cmd_how(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    mode = settings.trading_mode.value.upper()

    msg = (
        "How SabiBot Makes You Money\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

        "WHAT IS THIS?\n"
        "SabiBot trades on Polymarket — the world's largest\n"
        "prediction market. It uses AI to find mispriced\n"
        "predictions and bets on them automatically.\n\n"

        "EXAMPLE:\n"
        "News breaks: 'Country X announces election results'\n"
        "Bot sees market priced at 30% but AI estimates 70%\n"
        "Bot buys YES at $0.30\n"
        "Market corrects to $0.70 -> You profit $0.40/share!\n\n"

        "SETUP (5 minutes):\n"
        "1. Install MetaMask wallet (metamask.io)\n"
        "2. Buy USDC on Coinbase, Binance, or any exchange\n"
        "3. Send USDC to MetaMask (use Polygon network!)\n"
        "4. Go to polymarket.com -> Connect wallet -> Deposit\n"
        "5. The bot handles everything from here!\n\n"

        "HOW YOU EARN:\n"
        "1. Trading Profits - AI finds mispriced predictions\n"
        "   Buys low, market corrects, you keep the profit\n"
        "2. Builder Fees - Polymarket pays the bot a fee\n"
        "   on every single trade it places\n\n"

        "WHAT IT TRADES:\n"
        "- Politics (elections, policy decisions)\n"
        "- Crypto (Bitcoin price, ETH events)\n"
        "- Sports (NBA, NFL, soccer outcomes)\n"
        "- Weather (hurricane paths, temperature records)\n"
        "- Entertainment (award shows, pop culture)\n"
        "- Economics (Fed rates, GDP, jobs data)\n\n"

        "SAFETY RULES (built-in):\n"
        "- Max $10 per trade (adjustable)\n"
        "- Max $100 total exposure\n"
        "- Auto-stop if losses exceed 25%\n"
        "- AI must be 55%+ confident to trade\n\n"

        "RECOMMENDED START:\n"
        "- Start with $10-50 USDC\n"
        "- Never risk money you can't afford to lose\n"
        "- Check /status daily to monitor progress\n\n"

        f"Current mode: {mode}\n"
        "Browse markets: https://polymarket.com/"
    )
    await update.message.reply_text(msg)


# ── /status — Portfolio status ───────────────────────────────────

@_authorized
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show portfolio status."""
    summary = await portfolio.get_summary()
    risk_status = risk_manager.get_status()

    msg = (
        "Portfolio Status\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Mode: {settings.trading_mode.value.upper()}\n"
        f"Paused: {'Yes' if _paused else 'No'}\n\n"
        f"Value: ${summary.total_value_usd:.2f}\n"
        f"Positions: {len(summary.open_positions)}\n"
        f"Unrealized P&L: ${summary.unrealized_pnl:+.2f}\n"
        f"Today P&L: ${summary.realized_pnl_today:+.2f}\n"
        f"Total P&L: ${summary.realized_pnl_total + summary.unrealized_pnl:+.2f}\n"
        f"Win Rate: {summary.win_rate:.0%}\n"
        f"Total Trades: {summary.total_trades}\n\n"
        "Risk\n"
        f"Exposure: ${risk_status['total_exposure']:.2f}\n"
        f"Drawdown: {risk_status['drawdown']:.1%}\n"
        f"Halted: {'Yes' if risk_status['halted'] else 'No'}"
    )

    keyboard = [
        [
            InlineKeyboardButton("Trades", callback_data="trades"),
            InlineKeyboardButton("Markets", callback_data="markets"),
        ],
        [
            InlineKeyboardButton("Signals", callback_data="signals"),
            InlineKeyboardButton("Refresh", callback_data="status"),
        ],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(msg, reply_markup=reply_markup)


# ── /trades — Open positions ─────────────────────────────────────

@_authorized
async def cmd_trades(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show recent trades."""
    positions = await portfolio.get_open_positions()
    if not positions:
        await update.message.reply_text(
            "No open positions yet.\n\n"
            "The bot is scanning markets and analyzing news.\n"
            "It will trade when it finds a strong enough signal.\n\n"
            "Use /guide to learn how it works."
        )
        return

    lines = ["Open Positions\n" + "━" * 22 + "\n"]
    for p in positions[:10]:
        lines.append(
            f"\n{p.market_question[:50]}\n"
            f"  {p.side} | {p.shares:.1f} shares @ ${p.entry_price:.3f}\n"
            f"  Now: ${p.current_price:.3f} | P&L: ${p.unrealized_pnl:+.2f}"
        )

    await update.message.reply_text("\n".join(lines))
# ── Markets helper ───────────────────────────────────────────────

def _build_markets_text(markets_cache: list, limit_per_section: int = 5) -> str:
    """Build markets display with a 'Resolving This Week' section on top."""
    now = datetime.now(timezone.utc)
    week_cutoff = now + timedelta(days=7)

    this_week = []
    rest = []
    for m in markets_cache:
        if m.end_date:
            end = m.end_date if m.end_date.tzinfo else m.end_date.replace(tzinfo=timezone.utc)
            # Only include in "this week" if it ends in the FUTURE (not already past)
            if now <= end <= week_cutoff:
                this_week.append(m)
            else:
                rest.append(m)
        else:
            rest.append(m)

    this_week.sort(key=lambda m: m.volume_24h, reverse=True)
    rest.sort(key=lambda m: m.volume_24h, reverse=True)

    lines = []

    if this_week:
        lines.append("Resolving This Week")
        lines.append("━" * 22)
        for i, m in enumerate(this_week[:limit_per_section], 1):
            yes = m.outcome_prices.get("Yes", 0.5)
            end = m.end_date.strftime("%b %d %Y")
            slug = m.market_slug
            link = f"https://polymarket.com/market/{slug}" if slug else "https://polymarket.com/"
            lines.append(
                f"\n{i}. {m.question[:60]}\n"
                f"   YES: ${yes:.3f} | Ends: {end} | Vol: ${m.volume_24h:,.0f}\n"
                f"   {link}"
            )
        lines.append("")

    lines.append("Top Active Markets (30d)")
    lines.append("━" * 22)
    for i, m in enumerate(rest[:limit_per_section], 1):
        yes = m.outcome_prices.get("Yes", 0.5)
        end = m.end_date.strftime("%b %d %Y") if m.end_date else "unknown"
        slug = m.market_slug
        link = f"https://polymarket.com/market/{slug}" if slug else "https://polymarket.com/"
        lines.append(
            f"\n{i}. {m.question[:60]}\n"
            f"   YES: ${yes:.3f} | Ends: {end} | Vol: ${m.volume_24h:,.0f}\n"
            f"   {link}"
        )

    lines.append(f"\nShowing top {min(len(this_week), limit_per_section) + min(len(rest), limit_per_section)} of {len(markets_cache)} markets loaded")
    return "\n".join(lines)


# ── /markets — Top watched markets ───────────────────────────────

@_authorized
async def cmd_markets(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show top markets the bot is watching."""
    from src.main import _markets_cache

    if not _markets_cache:
        await update.message.reply_text("No markets loaded yet. Wait for next refresh cycle.")
        return

    await update.message.reply_text(_build_markets_text(_markets_cache, limit_per_section=10))


# ── /signals — Active signals ────────────────────────────────────

@_authorized
async def cmd_signals(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show current trading signals."""
    from src.main import _signals_cache, _markets_cache

    if not _signals_cache:
        await update.message.reply_text(
            "No signals yet.\n\n"
            "The bot scans news every 60 seconds.\n"
            "When it finds news matching a market, "
            "it creates a signal.\n\n"
            "Check back in a few minutes!"
        )
        return

    lines = ["Active Signals\n" + "━" * 22 + "\n"]
    # Match signals to market names
    market_map = {m.condition_id: m.question for m in _markets_cache}
    for cid, agg in list(_signals_cache.items())[:10]:
        name = market_map.get(cid, cid[:16] + "...")
        lines.append(
            f"\n{name[:50]}\n"
            f"  Fair value: {agg.fair_value:.3f}\n"
            f"  Confidence: {agg.confidence:.1%}\n"
            f"  Sources: {agg.num_signals}"
        )

    await update.message.reply_text("\n".join(lines))


# ── Control commands ─────────────────────────────────────────────

@_authorized
async def cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global _paused
    _paused = True
    await update.message.reply_text("Paused. No new orders will be placed.\nUse /resume to continue.")
    log.warning("trading_paused_via_telegram")


@_authorized
async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global _paused
    _paused = False
    await update.message.reply_text("Resumed! Trading is active again.")
    log.info("trading_resumed_via_telegram")


@_authorized
async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    risk_manager.reset_drawdown_halt()
    await update.message.reply_text("Drawdown halt reset. Trading may resume.")


@_authorized
async def cmd_kill(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Emergency stop — cancel all orders and halt."""
    global _paused
    _paused = True

    from src.polymarket.clob import clob
    cancelled = await clob.cancel_all()

    msg = "EMERGENCY STOP\nAll orders cancelled. Trading halted."
    if not cancelled:
        msg += "\n(No open positions to cancel)"
    await update.message.reply_text(msg)
    log.error("emergency_kill_via_telegram")

@_authorized
async def cmd_debug(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show full internal state + live API test for debugging."""
    from src.main import _markets_cache, _signals_cache
    from src.polymarket.clob import clob
    from src.polymarket.client import polymarket
    from src.intelligence.news_scanner import _last_gnews_call, _gnews_cycle
    import sys
    import httpx

    now = datetime.now(timezone.utc)

    # CLOB state
    clob_ok = clob._client is not None

    # Balance
    balance_str = "N/A"
    if clob_ok:
        try:
            bal = await clob.get_balance()
            balance_str = f"${bal:.2f}" if bal > 0 else "$0.00 (check USDC approved on Polymarket)"
        except Exception as e:
            balance_str = f"ERR: {str(e)[:60]}"

    # Last market fetch error
    last_mkt_err = polymarket.last_error or "none"

    # Live API test — hit the Polymarket API right now to see if it works
    api_test = "not run"
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            resp = await c.get("https://clob.polymarket.com/sampling-markets", params={"limit": 1})
            data = resp.json()
            raw = data.get("data", [])
            api_test = f"HTTP {resp.status_code}, {len(raw)} mkts"
            if raw:
                api_test += f", first: {raw[0].get('question', '?')[:30]}"
    except Exception as e:
        api_test = f"FAIL: {type(e).__name__}: {str(e)[:50]}"

    gnews_last = str(_last_gnews_call)[:19] if _last_gnews_call else "never"

    lines = [
        "Diagnostics",
        "━" * 30,
        "",
        f"UTC: {now.strftime('%H:%M:%S')}",
        f"Python: {sys.version.split()[0]}",
        f"Mode: {settings.trading_mode.value.upper()}",
        f"Paused: {'Yes' if _paused else 'No'}",
        "",
        "CLOB Auth",
        f"  Client: {'OK' if clob_ok else 'NOT INIT'}",
        f"  Balance: {balance_str}",
        "",
        "API Test (live)",
        f"  {api_test}",
        "",
        "Markets",
        f"  Loaded: {len(_markets_cache)}",
        f"  Last error: {last_mkt_err[:60]}",
        "",
        f"Signals: {len(_signals_cache)}",
        "",
        f"News: GNews cycle {_gnews_cycle}, last {gnews_last}",
        "",
        "Use /refresh to force market reload",
    ]
    await update.message.reply_text("\n".join(lines))


@_authorized
async def cmd_refresh(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Manually trigger market refresh with step-by-step diagnostics."""
    import httpx as _httpx
    from src.main import _refresh_markets

    await update.message.reply_text("Running market diagnostics...")

    lines = ["Market Refresh Diagnostics\n" + "━" * 28]

    # Step 1: raw API call (same as debug test)
    try:
        async with _httpx.AsyncClient(timeout=15.0) as c:
            r = await c.get("https://clob.polymarket.com/sampling-markets", params={"limit": 5})
            raw_data = r.json().get("data", [])
            lines.append("\nStep 1 — Raw API call")
            lines.append(f"  Status : {r.status_code}")
            lines.append(f"  Raw mkts: {len(raw_data)}")
            if raw_data:
                m0 = raw_data[0]
                lines.append(f"  First  : {m0.get('question', '?')[:45]}")
                lines.append(f"  cond_id: {'yes' if m0.get('condition_id') else 'MISSING'}")
                lines.append(f"  accp_ord: {m0.get('accepting_orders', 'MISSING')}")
                accepting = sum(1 for m in raw_data if m.get("accepting_orders"))
                lines.append(f"  Accepting orders (of {len(raw_data)}): {accepting}")
    except Exception as e:
        lines.append(f"\nStep 1 FAILED: {e}")

    # Step 2: full pipeline refresh
    from src.main import _markets_cache as cache_before_ref
    before = len(cache_before_ref)
    try:
        await _refresh_markets()
    except Exception as e:
        lines.append(f"\nStep 2 _refresh_markets error: {e}")

    from src.main import _markets_cache as cache_after_ref
    after = len(cache_after_ref)
    from src.polymarket.client import polymarket
    lines.append("\nStep 2 — Pipeline refresh")
    lines.append(f"  Before : {before}")
    lines.append(f"  After  : {after}")
    lines.append(f"  Last err: {polymarket.last_error or 'none'}")
    if after > 0:
        lines.append(f"  Sample : {cache_after_ref[0].question[:45]}")

    await update.message.reply_text("\n".join(lines))


# ── Callback query handler (button presses) ─────────────────────

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle inline keyboard button presses."""
    query = update.callback_query
    await query.answer()

    # Check authorization
    if str(query.from_user.id) != settings.telegram_chat_id:
        return

    data = query.data

    # Create a fake Update with message for reuse of cmd_ handlers
    # We'll just edit the existing message or send a new one
    if data == "guide":
        await _send_guide(query)
    elif data == "how":
        await _send_how(query)
    elif data == "status":
        await _send_status(query)
    elif data == "markets":
        await _send_markets(query)
    elif data == "signals":
        await _send_signals(query)
    elif data == "trades":
        await _send_trades(query)
    elif data == "help":
        await _send_help(query)
    elif data == "back":
        await _send_main_menu(query)
    elif data == "debug":
        await _send_debug(query)


async def _send_main_menu(query) -> None:
    msg = (
        "SabiBot Menu\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Mode: {settings.trading_mode.value.upper()}\n"
        f"Paused: {'Yes' if _paused else 'No'}"
    )
    keyboard = [
        [
            InlineKeyboardButton("How It Works", callback_data="guide"),
            InlineKeyboardButton("How To Fund", callback_data="how"),
        ],
        [
            InlineKeyboardButton("Status", callback_data="status"),
            InlineKeyboardButton("Markets", callback_data="markets"),
        ],
        [
            InlineKeyboardButton("Signals", callback_data="signals"),
            InlineKeyboardButton("Trades", callback_data="trades"),
        ],
        [
            InlineKeyboardButton("Debug", callback_data="debug"),
        ],
    ]
    await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))


async def _send_guide(query) -> None:
    msg = (
        "How SabiBot Works\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "1. SCAN - Checks 9+ news feeds every 60s\n"
        "2. ANALYZE - AI reads each headline\n"
        "3. MATCH - Links news to Polymarket events\n"
        "4. CALCULATE - Bayesian probability estimation\n"
        "5. DECIDE - 6 strategies compete for best trade\n"
        "6. RISK CHECK - Kelly criterion + limits\n"
        "7. EXECUTE - Places order on Polymarket\n"
        "8. MONITOR - Tracks P&L, alerts you\n\n"
        "Use /guide for the full detailed breakdown."
    )
    keyboard = [[InlineKeyboardButton("Back to Menu", callback_data="back")]]
    await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))


async def _send_how(query) -> None:
    msg = (
        "How SabiBot Earns Money\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "1. AI scans news 24/7\n"
        "2. Finds mispriced predictions on Polymarket\n"
        "3. Buys low (e.g. YES at $0.30)\n"
        "4. Market corrects -> You profit!\n\n"
        "SETUP:\n"
        "1. Get MetaMask wallet\n"
        "2. Buy USDC on any exchange\n"
        "3. Send USDC to wallet (Polygon network)\n"
        "4. Deposit on polymarket.com\n"
        "5. Bot trades automatically 24/7!\n\n"
        "Start with $10-50. Never risk what you can't lose.\n"
        "https://polymarket.com/"
    )
    keyboard = [[InlineKeyboardButton("Back to Menu", callback_data="back")]]
    await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))


async def _send_status(query) -> None:
    summary = await portfolio.get_summary()
    risk_status = risk_manager.get_status()
    msg = (
        "Portfolio Status\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Mode: {settings.trading_mode.value.upper()}\n"
        f"Value: ${summary.total_value_usd:.2f}\n"
        f"Positions: {len(summary.open_positions)}\n"
        f"P&L Today: ${summary.realized_pnl_today:+.2f}\n"
        f"Total P&L: ${summary.realized_pnl_total:+.2f}\n"
        f"Win Rate: {summary.win_rate:.0%}\n"
        f"Trades: {summary.total_trades}\n"
        f"Drawdown: {risk_status['drawdown']:.1%}"
    )
    keyboard = [
        [
            InlineKeyboardButton("Refresh", callback_data="status"),
            InlineKeyboardButton("Trades", callback_data="trades"),
        ],
        [InlineKeyboardButton("Back to Menu", callback_data="back")],
    ]
    await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))


async def _send_markets(query) -> None:
    from src.main import _markets_cache

    if not _markets_cache:
        msg = "No markets loaded yet."
    else:
        msg = _build_markets_text(_markets_cache, limit_per_section=4)

    keyboard = [
        [
            InlineKeyboardButton("Refresh", callback_data="markets"),
            InlineKeyboardButton("Signals", callback_data="signals"),
        ],
        [InlineKeyboardButton("Back to Menu", callback_data="back")],
    ]
    await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))


async def _send_signals(query) -> None:
    from src.main import _signals_cache, _markets_cache

    if not _signals_cache:
        msg = "No signals yet. Bot scans news every 60s.\nCheck back soon!"
    else:
        market_map = {m.condition_id: m.question for m in _markets_cache}
        lines = ["Active Signals\n" + "━" * 22 + "\n"]
        for cid, agg in list(_signals_cache.items())[:8]:
            name = market_map.get(cid, cid[:16] + "...")
            lines.append(
                f"\n{name[:45]}\n"
                f"  Value: {agg.fair_value:.3f} | Conf: {agg.confidence:.0%}"
            )
        msg = "\n".join(lines)

    keyboard = [
        [
            InlineKeyboardButton("Refresh", callback_data="signals"),
            InlineKeyboardButton("Markets", callback_data="markets"),
        ],
        [InlineKeyboardButton("Back to Menu", callback_data="back")],
    ]
    await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))


async def _send_trades(query) -> None:
    positions = await portfolio.get_open_positions()
    if not positions:
        msg = "No open positions.\nBot will trade when it finds a strong signal."
    else:
        lines = ["Open Positions\n" + "━" * 22 + "\n"]
        for p in positions[:8]:
            lines.append(
                f"\n{p.market_question[:45]}\n"
                f"  {p.side} {p.shares:.1f} @ ${p.entry_price:.3f}\n"
                f"  Now: ${p.current_price:.3f} | P&L: ${p.unrealized_pnl:+.2f}"
            )
        msg = "\n".join(lines)

    keyboard = [
        [
            InlineKeyboardButton("Refresh", callback_data="trades"),
            InlineKeyboardButton("Status", callback_data="status"),
        ],
        [InlineKeyboardButton("Back to Menu", callback_data="back")],
    ]
    await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))


async def _send_help(query) -> None:
    msg = (
        "All Commands\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "/start - Welcome\n"
        "/guide - How it works\n"
        "/how - How to fund & earn\n"
        "/status - Portfolio\n"
        "/trades - Open positions\n"
        "/markets - Watched markets\n"
        "/signals - Active signals\n"
        "/pause - Stop trading\n"
        "/resume - Resume trading\n"
        "/kill - Emergency stop"
    )
    keyboard = [[InlineKeyboardButton("Back to Menu", callback_data="back")]]
    await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))


async def _send_debug(query) -> None:
    from src.main import _markets_cache, _signals_cache
    from src.polymarket.clob import clob
    from src.polymarket.client import polymarket
    import httpx

    now = datetime.now(timezone.utc)
    clob_ok = clob._client is not None

    balance_str = "N/A"
    if clob_ok:
        try:
            bal = await clob.get_balance()
            balance_str = f"${bal:.2f}"
        except Exception:
            balance_str = "ERROR"

    last_mkt_err = polymarket.last_error or "none"

    # Live API test
    api_test = "not run"
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            resp = await c.get("https://clob.polymarket.com/sampling-markets", params={"limit": 1})
            data = resp.json()
            raw = data.get("data", [])
            api_test = f"HTTP {resp.status_code}, {len(raw)} mkts"
    except Exception as e:
        api_test = f"FAIL: {type(e).__name__}: {str(e)[:50]}"

    msg = (
        f"Diagnostics\n{'━' * 30}\n\n"
        f"UTC: {now.strftime('%H:%M:%S')}\n"
        f"Mode: {settings.trading_mode.value.upper()}\n"
        f"Paused: {'Yes' if _paused else 'No'}\n\n"
        f"CLOB: {'OK' if clob_ok else 'NOT INIT'}\n"
        f"Balance: {balance_str}\n\n"
        f"API Test: {api_test}\n\n"
        f"Markets: {len(_markets_cache)}\n"
        f"Last err: {last_mkt_err[:60]}\n\n"
        f"Signals: {len(_signals_cache)}\n"
    )
    keyboard = [
        [
            InlineKeyboardButton("Refresh", callback_data="debug"),
            InlineKeyboardButton("Back", callback_data="back"),
        ],
    ]
    await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))


# ── Public API ───────────────────────────────────────────────────

def is_paused() -> bool:
    """Check if trading is paused (used by main loop)."""
    return _paused


async def start_telegram_bot() -> Application | None:
    """Initialize and start the Telegram bot. Returns None if not configured."""
    if not settings.telegram_bot_token:
        log.info("telegram_not_configured")
        return None

    app = Application.builder().token(settings.telegram_bot_token).build()

    # Info commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("guide", cmd_guide))
    app.add_handler(CommandHandler("how", cmd_how))

    # Monitoring commands
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("trades", cmd_trades))
    app.add_handler(CommandHandler("markets", cmd_markets))
    app.add_handler(CommandHandler("signals", cmd_signals))

    # Control commands
    app.add_handler(CommandHandler("pause", cmd_pause))
    app.add_handler(CommandHandler("resume", cmd_resume))
    app.add_handler(CommandHandler("reset", cmd_reset))
    app.add_handler(CommandHandler("kill", cmd_kill))

    # Debug
    app.add_handler(CommandHandler("debug", cmd_debug))
    app.add_handler(CommandHandler("refresh", cmd_refresh))

    # Button callbacks
    app.add_handler(CallbackQueryHandler(handle_callback))

    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)

    # Register commands → shows the blue "Menu" button in Telegram clients
    commands = [
        BotCommand("start",   "Menu & quick start"),
        BotCommand("status",  "Portfolio, P&L, risk"),
        BotCommand("markets", "Top watched markets"),
        BotCommand("signals", "Active trading signals"),
        BotCommand("trades",  "Open positions"),
        BotCommand("guide",   "How SabiBot works A-Z"),
        BotCommand("how",     "How to fund & earn"),
        BotCommand("pause",   "Pause trading"),
        BotCommand("resume",  "Resume trading"),
        BotCommand("kill",    "Emergency stop"),
        BotCommand("debug",   "Internal diagnostics"),
        BotCommand("refresh", "Force market reload"),
    ]
    await app.bot.set_my_commands(commands)
    await app.bot.set_chat_menu_button(menu_button=MenuButtonCommands())

    log.info("telegram_bot_started")
    return app


async def stop_telegram_bot(app: Application | None) -> None:
    if app:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
