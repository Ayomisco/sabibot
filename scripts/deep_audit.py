"""
SabiBot Deep Audit Script
Run: python scripts/deep_audit.py
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

DIVIDER = "─" * 60


async def audit_markets():
    print(f"\n{'='*60}")
    print("1. POLYMARKET — MARKET FETCH")
    print(DIVIDER)
    try:
        from src.polymarket.client import PolymarketClient
        client = PolymarketClient()
        markets = await client.get_active_markets()
        print(f"✅  Markets fetched: {len(markets)}")
        accepting = [m for m in markets if m.accepting_orders]
        print(f"✅  Accepting orders: {len(accepting)}")
        if markets:
            for m in markets[:3]:
                yes_price = m.tokens[0].price if m.tokens else "?"
                no_price = m.tokens[1].price if len(m.tokens) > 1 else "?"
                print(f"\n  📊 {m.question[:70]}")
                print(f"     YES={yes_price}  NO={no_price}  neg_risk={m.neg_risk}  tick={m.minimum_tick_size}")
        return markets
    except Exception as e:
        print(f"❌  FAILED: {e}")
        return []


async def audit_news():
    print(f"\n{'='*60}")
    print("2. NEWS SCANNER")
    print(DIVIDER)
    try:
        from src.intelligence.news_scanner import NewsScanner
        scanner = NewsScanner()
        items = await scanner.scan()
        print(f"✅  News items fetched: {len(items)}")
        for item in items[:5]:
            print(f"\n  📰 {item.title[:70]}")
            print(f"     Source: {item.source}  Published: {item.published_at}")
        return items
    except Exception as e:
        print(f"❌  FAILED: {e}")
        import traceback
        traceback.print_exc()
        return []


async def audit_market_matching(markets, news_items):
    print(f"\n{'='*60}")
    print("3. MARKET MATCHING (News → Markets)")
    print(DIVIDER)
    if not markets or not news_items:
        print("⚠️  Skipped — no markets or news")
        return []

    try:
        from src.polymarket.markets import match_news_to_markets
        matches = []
        for item in news_items[:10]:
            matched = match_news_to_markets(item, markets)
            if matched:
                matches.append((item, matched))
                print(f"\n  📰 '{item.title[:60]}'")
                for (mkt, score) in matched[:2]:
                    print(f"     → [{score:.2f}] {mkt.question[:60]}")

        print(f"\n  Total: {len(news_items[:10])} news items → {len(matches)} matched")
        if len(matches) == 0:
            print("  ⚠️  ZERO MATCHES — market matching is too strict or broken!")
        return matches
    except Exception as e:
        print(f"❌  FAILED: {e}")
        import traceback
        traceback.print_exc()
        return []


async def audit_signals(markets):
    print(f"\n{'='*60}")
    print("4. SIGNAL AGGREGATOR")
    print(DIVIDER)
    if not markets:
        print("⚠️  Skipped — no markets")
        return []

    try:
        from src.intelligence.signal_aggregator import SignalAggregator
        from src.db.models import Signal
        from datetime import datetime, timezone

        agg = SignalAggregator()
        results = []
        for m in markets[:5]:
            # Simulate a signal
            test_signal = Signal(
                condition_id=m.condition_id,
                source="test",
                raw_probability=0.7,
                weight=1.0,
                created_at=datetime.now(timezone.utc),
            )
            fair_value, confidence = agg.aggregate([test_signal], float(m.tokens[0].price) if m.tokens else 0.5)
            results.append((m, fair_value, confidence))
            print(f"\n  📊 {m.question[:60]}")
            print(f"     Market: {m.tokens[0].price if m.tokens else '?'}  →  Fair: {fair_value:.3f}  Confidence: {confidence:.3f}")
            edge = fair_value - (m.tokens[0].price if m.tokens else 0.5)
            if abs(edge) > 0.05:
                print(f"     ✅ EDGE FOUND: {edge:+.3f}")
            else:
                print(f"     ❌ Edge too small: {edge:+.3f}")

        return results
    except Exception as e:
        print(f"❌  FAILED: {e}")
        import traceback
        traceback.print_exc()
        return []


async def audit_strategies(markets):
    print(f"\n{'='*60}")
    print("5. STRATEGY ENGINE")
    print(DIVIDER)
    if not markets:
        print("⚠️  Skipped — no markets")
        return

    try:
        from src.strategies.timezone_arb import TimezoneArbStrategy
        from src.strategies.sentiment_trade import SentimentTradeStrategy
        from src.strategies.momentum import MomentumStrategy
        from src.strategies.mean_reversion import MeanReversionStrategy
        from src.intelligence.signal_aggregator import AggregatedResult

        strategies = [
            ("TimezoneArb", TimezoneArbStrategy()),
            ("SentimentTrade", SentimentTradeStrategy()),
            ("Momentum", MomentumStrategy()),
            ("MeanReversion", MeanReversionStrategy()),
        ]

        for name, strategy in strategies:
            try:
                # Use first 20 markets
                test_markets = markets[:20]
                # Build fake aggregated results
                results = []
                for m in test_markets:
                    price = float(m.tokens[0].price) if m.tokens else 0.5
                    # Simulate signal with slight bias
                    import random
                    bias = random.uniform(-0.15, 0.15)
                    fair_value = max(0.01, min(0.99, price + bias))
                    from src.intelligence.signal_aggregator import AggregatedResult
                    ar = AggregatedResult(
                        condition_id=m.condition_id,
                        market=m,
                        fair_value=fair_value,
                        confidence=random.uniform(0.50, 0.75),
                        signal_count=3,
                        evidence_strength=0.6,
                    )
                    results.append(ar)

                proposals = await strategy.evaluate(results)
                print(f"\n  🎯 {name}: {len(proposals)} proposals")
                for p in proposals[:2]:
                    print(f"     → {p.side} {p.market.question[:50]}")
                    print(f"       edge={p.edge:+.3f} conf={p.confidence:.2f} price={p.market_price:.3f}")
            except Exception as e:
                print(f"  ❌ {name} FAILED: {e}")
                import traceback
                traceback.print_exc()

    except Exception as e:
        print(f"❌  FAILED: {e}")
        import traceback
        traceback.print_exc()


async def audit_risk(markets):
    print(f"\n{'='*60}")
    print("6. RISK MANAGER — Parameter Audit")
    print(DIVIDER)
    try:
        from src.config import settings
        from src.execution.risk_manager import RiskManager

        rm = RiskManager()
        print(f"  TRADING_MODE:              {settings.trading_mode}")
        print(f"  MAX_POSITION_SIZE_USD:     ${settings.max_position_size_usd}")
        print(f"  MAX_PORTFOLIO_EXPOSURE:    ${settings.max_portfolio_exposure_usd}")
        print(f"  MAX_SINGLE_MARKET_PCT:     {settings.max_single_market_pct*100:.0f}%")
        print(f"  MAX_DRAWDOWN_PCT:          {settings.max_drawdown_pct*100:.0f}%")
        print(f"  KELLY_MULTIPLIER:          {settings.kelly_multiplier}")
        print(f"  MIN_EDGE_THRESHOLD:        {settings.min_edge_threshold*100:.0f}%")
        print(f"  MIN_CONFIDENCE:            {settings.min_confidence*100:.0f}%")

        # Simulate what happens when a good proposal comes in
        if markets:
            from src.strategies.base import TradeProposal
            m = markets[0]
            price = float(m.tokens[0].price) if m.tokens else 0.5
            test_proposal = TradeProposal(
                condition_id=m.condition_id,
                token_id=m.tokens[0].token_id if m.tokens else "",
                market=m,
                side="YES",
                market_price=price,
                fair_value=price + 0.10,  # 10% edge
                confidence=0.70,
                strategy_name="test",
                reasoning="audit test",
                max_price=price + 0.03,
            )
            check = rm.check_proposal(test_proposal)
            print(f"\n  TEST PROPOSAL (10% edge, 70% confidence):")
            print(f"  → Approved: {check.approved}")
            print(f"  → Size: ${check.adjusted_size_usd:.2f}")
            if not check.approved:
                print(f"  → Rejected: {check.rejection_reason}")
                print(f"\n  ⚠️  PROBLEM: Even a 10% edge trade is being REJECTED!")
    except Exception as e:
        print(f"❌  FAILED: {e}")
        import traceback
        traceback.print_exc()


async def audit_clob():
    print(f"\n{'='*60}")
    print("7. CLOB CLIENT — Order Placement Check")
    print(DIVIDER)
    try:
        from src.config import settings
        print(f"  Wallet: {settings.polygon_wallet_address}")
        print(f"  Has private key: {'yes' if settings.polygon_private_key else 'NO ❌'}")
        print(f"  CLOB URL: {settings.polymarket_clob_url}")
        print(f"  Builder wallet: {settings.builder_wallet_address or 'NOT SET ❌'}")

        if not settings.polygon_private_key:
            print("\n  ❌ CRITICAL: No private key — cannot place orders!")
            return

        from src.polymarket.clob import CLOBClient
        client = CLOBClient()
        print(f"\n  CLOB client initialized: {'✅' if client else '❌'}")

        # Check order book on a real market
        from src.polymarket.client import PolymarketClient
        pm = PolymarketClient()
        markets = await pm.get_active_markets()
        if markets:
            m = markets[0]
            token_id = m.tokens[0].token_id if m.tokens else None
            if token_id:
                try:
                    book = await client.get_order_book(token_id)
                    bids = book.get("bids", [])
                    asks = book.get("asks", [])
                    print(f"\n  ORDER BOOK ({m.question[:50]}):")
                    print(f"  Top bid: {bids[0] if bids else 'empty'}")
                    print(f"  Top ask: {asks[0] if asks else 'empty'}")
                    if bids and asks:
                        spread = float(asks[0]["price"]) - float(bids[0]["price"])
                        print(f"  Spread: {spread:.4f} ({spread*100:.2f}¢)")
                    print(f"\n  ✅ Order book accessible — CLOB is live")
                except Exception as e:
                    print(f"  ❌ Order book failed: {e}")
    except Exception as e:
        print(f"❌  FAILED: {e}")
        import traceback
        traceback.print_exc()


async def check_db():
    print(f"\n{'='*60}")
    print("8. DATABASE — Trade & Signal History")
    print(DIVIDER)
    try:
        import sqlite3
        conn = sqlite3.connect("data/sabibot.db")
        c = conn.cursor()
        c.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in c.fetchall()]
        print(f"  Tables: {tables}")
        for t in tables:
            c.execute(f"SELECT COUNT(*) FROM {t}")
            count = c.fetchone()[0]
            print(f"  {t}: {count} rows")
            if count > 0 and t == "trade":
                c.execute("SELECT strategy, side, amount_usd, price, edge, status, created_at FROM trade ORDER BY created_at DESC LIMIT 5")
                rows = c.fetchall()
                for row in rows:
                    print(f"    → {row}")
        conn.close()
    except Exception as e:
        print(f"❌  FAILED: {e}")


async def main():
    print("\n🔍 SABIBOT DEEP AUDIT")
    print("=" * 60)

    markets = await audit_markets()
    news_items = await audit_news()
    await audit_market_matching(markets, news_items)
    # await audit_signals(markets)   # requires DB
    await audit_strategies(markets)
    await audit_risk(markets)
    await audit_clob()
    await check_db()

    print(f"\n{'='*60}")
    print("AUDIT COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
