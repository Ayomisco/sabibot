# SabiBot

Autonomous Polymarket trading agent. Timezone arbitrage, sentiment-driven execution, AI-powered market analysis.

## Quick Start

```bash
cp .env.example .env
# Fill in: GROQ_API_KEY, POLYGON_PRIVATE_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

make dev        # Install deps + spaCy model
make paper      # Paper trading mode (no real money)
make live       # Live trading (real money — be sure)
```

## Architecture

```
News Sources → Intelligence Layer → Signal Aggregator → Strategy Engine → Execution
     ↑              ↑                      ↑                  ↑              ↑
   RSS/APIs      AI Analysis        Bayesian Fusion      Risk Filter    Polymarket CLOB
```

## Cost

| Component | Monthly Cost |
|-----------|-------------|
| Groq (Llama 3.3 70B) | FREE |
| Telegram Bot | FREE |
| RSS Feeds | FREE |
| Claude (critical decisions) | ~$20 |
| OpenAI Embeddings | ~$0.30 |
| **Total** | **~$20.30** |
