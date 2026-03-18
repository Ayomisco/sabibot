"""
Paper trading script — runs the full pipeline in paper mode.

Use this to validate the bot logic before going live.
Identical to `make paper` but with extra verbosity.
"""

from src.main import run
import asyncio
import os

# Force paper mode
os.environ["TRADING_MODE"] = "paper"
os.environ["LOG_LEVEL"] = "DEBUG"


if __name__ == "__main__":
    print("=" * 60)
    print("  SABIBOT — PAPER TRADING MODE")
    print("  No real money will be used.")
    print("  Press Ctrl+C to stop.")
    print("=" * 60)
    asyncio.run(run())
