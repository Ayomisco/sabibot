"""Verify all source modules can be imported."""
import sys

modules = [
    "src.config",
    "src.db.models",
    "src.db.database",
    "src.utils.logger",
    "src.utils.retry",
    "src.utils.scheduler",
    "src.utils.notifications",
    "src.ai.llm",
    "src.ai.embeddings",
    "src.ai.prompts.analyze_news",
    "src.ai.prompts.estimate_probability",
    "src.ai.prompts.explain_trade",
    "src.polymarket.constants",
    "src.polymarket.client",
    "src.polymarket.markets",
    "src.polymarket.signing",
    "src.polymarket.clob",
    "src.polymarket.websocket",
    "src.intelligence.news_scanner",
    "src.intelligence.sentiment",
    "src.intelligence.market_analyzer",
    "src.intelligence.signal_aggregator",
    "src.strategies.base",
    "src.strategies.timezone_arb",
    "src.strategies.sentiment_trade",
    "src.strategies.cross_market_arb",
    "src.strategies.market_making",
    "src.strategies.momentum",
    "src.strategies.mean_reversion",
    "src.execution.risk_manager",
    "src.execution.order_manager",
    "src.execution.portfolio",
    "src.interface.cli",
    "src.interface.telegram_bot",
]

failed = []
passed = 0
for m in modules:
    try:
        __import__(m)
        passed += 1
        print(f"  OK  {m}")
    except Exception as e:
        failed.append((m, str(e)))
        print(f"  FAIL  {m}: {e}")

print()
if failed:
    print(f"FAILED: {len(failed)}/{len(modules)}")
    sys.exit(1)
else:
    print(f"ALL {len(modules)} MODULES IMPORTED SUCCESSFULLY")
