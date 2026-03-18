"""
Embeddings — semantic similarity for news-to-market matching.

Uses OpenAI text-embedding-3-small when available, falls back to
spaCy word vectors (free, no API key, lower quality but workable).
"""

from __future__ import annotations

import numpy as np
import httpx

from src.config import settings
from src.utils.logger import get_logger

log = get_logger("embeddings")

_http: httpx.AsyncClient | None = None
_spacy_nlp = None


def _client() -> httpx.AsyncClient:
    global _http
    if _http is None or _http.is_closed:
        _http = httpx.AsyncClient(timeout=30.0)
    return _http


def _get_spacy():
    """Lazy-load spaCy model."""
    global _spacy_nlp
    if _spacy_nlp is None:
        import spacy
        _spacy_nlp = spacy.load("en_core_web_sm")
    return _spacy_nlp


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """
    Embed a batch of texts. Returns list of float vectors.

    Uses OpenAI API if key is available, otherwise falls back to spaCy.
    """
    if settings.openai_api_key:
        return await _embed_openai(texts)
    return _embed_spacy(texts)


async def _embed_openai(texts: list[str]) -> list[list[float]]:
    """Embed via OpenAI text-embedding-3-small."""
    resp = await _client().post(
        "https://api.openai.com/v1/embeddings",
        headers={"Authorization": f"Bearer {settings.openai_api_key}"},
        json={
            "model": settings.openai_embedding_model,
            "input": texts,
        },
    )
    resp.raise_for_status()
    data = resp.json()
    return [item["embedding"] for item in data["data"]]


def _embed_spacy(texts: list[str]) -> list[list[float]]:
    """Fallback: spaCy word vector averaging. Lower quality but free."""
    nlp = _get_spacy()
    vectors = []
    for text in texts:
        doc = nlp(text[:512])
        vec = doc.vector
        if np.any(vec):
            vectors.append(vec.tolist())
        else:
            vectors.append([0.0] * len(vec))
    return vectors


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two vectors."""
    a_arr = np.array(a)
    b_arr = np.array(b)
    dot = np.dot(a_arr, b_arr)
    norm_a = np.linalg.norm(a_arr)
    norm_b = np.linalg.norm(b_arr)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(dot / (norm_a * norm_b))
