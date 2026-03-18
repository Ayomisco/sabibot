"""Prompt: Analyze a news item for prediction market impact."""


def build_news_analysis_prompt(headline: str, body: str = "") -> str:
    content = headline
    if body:
        content += f"\n\n{body[:1500]}"

    return f"""Analyze this news item for prediction market impact.

NEWS:
{content}

Return JSON with these exact fields:
{{
  "affected_topics": ["list of 1-5 topic keywords this news affects, e.g. 'US elections', 'Bitcoin price', 'Fed interest rates'"],
  "sentiment": <float -1.0 to 1.0, negative=bad outcome, positive=good outcome for the topic>,
  "magnitude": <float 0.0 to 1.0, how significant: 0.1=minor, 0.5=notable, 0.9=major breaking>,
  "time_sensitivity": <float 0.0 to 1.0, how quickly markets should react: 0.2=days, 0.5=hours, 0.9=minutes>,
  "key_entities": ["named entities: people, organizations, countries mentioned"],
  "summary": "One sentence summary of market-relevant impact"
}}

Rules:
- Be precise about affected_topics — these will be used to match against market questions
- magnitude 0.0 means no market relevance, 1.0 means world-changing event
- Only include entities explicitly mentioned, do not hallucinate
- sentiment relative to the topic being affected, not general good/bad"""
