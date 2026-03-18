"""Prompt: Estimate the fair probability of a market outcome."""


def build_probability_prompt(
    market_question: str,
    current_price: float,
    evidence: list[str],
) -> str:
    evidence_block = "\n".join(f"- {e}" for e in evidence) if evidence else "- No specific evidence provided"

    return f"""You are estimating the fair probability for a prediction market.

MARKET QUESTION: {market_question}
CURRENT MARKET PRICE (YES): ${current_price:.3f} (implies {current_price:.1%} probability)

EVIDENCE:
{evidence_block}

Your task:
1. Consider the base rate for this type of event
2. Weigh each piece of evidence for and against
3. Consider what the market might be missing or overweighting
4. Produce a calibrated probability estimate

Return JSON:
{{
  "probability": <float 0.01 to 0.99, your estimated true probability of YES>,
  "confidence": <float 0.0 to 1.0, how confident you are in your estimate>,
  "reasoning": "2-3 sentence explanation of your logic",
  "key_factors": ["list of 2-4 factors driving your estimate"]
}}

Calibration rules:
- If you have no edge over the market, return the current price ± 0.02
- Only deviate significantly from market price if evidence is strong and specific
- confidence < 0.4 means you're guessing. confidence > 0.8 means strong evidence
- Never return exactly 0.0 or 1.0 — nothing is certain"""
