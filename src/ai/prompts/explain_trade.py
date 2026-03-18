"""Prompt: Explain a trade decision in human-readable form."""


def build_trade_explanation_prompt(
    market_question: str,
    side: str,
    edge: float,
    evidence_summary: str,
) -> str:
    return f"""Explain this prediction market trade decision.

MARKET: {market_question}
POSITION: {side}
ESTIMATED EDGE: {edge:+.1%}

EVIDENCE SUMMARY:
{evidence_summary}

Return JSON:
{{
  "rationale": "2-3 sentence plain-English explanation of why this trade makes sense",
  "risk_factors": ["list of 2-3 things that could make this trade lose"],
  "expected_timeframe": "when we expect this edge to resolve (e.g. '2-4 hours', '1-3 days')"
}}"""
