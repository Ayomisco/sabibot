"""
Multi-provider LLM gateway.

Routes requests through Groq (free) → Anthropic (paid fallback) → Ollama (local).
Switch providers by changing LLM_PRIMARY_PROVIDER in .env.
All providers expose the same interface: complete() and complete_json().
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import httpx

from src.config import LLMProvider, settings
from src.utils.logger import get_logger
from src.utils.retry import with_retry

log = get_logger("llm")

# Groq rate limiter — free tier is 30 RPM; enforce 2.5s gap → max 24 RPM.
# Lock serializes calls; _groq_last_call tracks the timestamp of the last request.
_groq_lock: asyncio.Lock | None = None
_groq_last_call: float = 0.0
_GROQ_MIN_GAP: float = 2.5  # seconds


def _get_groq_lock() -> asyncio.Lock:
    global _groq_lock
    if _groq_lock is None:
        _groq_lock = asyncio.Lock()
    return _groq_lock


class ModelTier(str, Enum):
    """Route calls by importance. FREE for bulk, SMART for critical decisions."""
    FREE = "free"       # Groq / Ollama — news scanning, sentiment, classification
    SMART = "smart"     # Claude / GPT-4 — fair value estimation, trade reasoning


@dataclass
class LLMResponse:
    content: str
    provider: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached: bool = False


@dataclass
class LLMGateway:
    """Unified interface to all LLM providers."""

    _http: httpx.AsyncClient = field(
        default_factory=lambda: httpx.AsyncClient(timeout=60.0))

    # ── Provider dispatch ────────────────────────────────────────

    async def complete(
        self,
        prompt: str,
        system: str = "",
        tier: ModelTier = ModelTier.FREE,
        temperature: float = 0.3,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        """Send a completion request, routing by tier and config."""
        provider = self._resolve_provider(tier)
        try:
            return await self._dispatch(provider, prompt, system, temperature, max_tokens)
        except Exception as exc:
            log.warning("llm_primary_failed",
                        provider=provider.value, error=str(exc))
            fallback = self._resolve_fallback(provider)
            if fallback == LLMProvider.NONE:
                raise
            log.info("llm_falling_back", to=fallback.value)
            return await self._dispatch(fallback, prompt, system, temperature, max_tokens)

    async def complete_json(
        self,
        prompt: str,
        system: str = "",
        tier: ModelTier = ModelTier.FREE,
        temperature: float = 0.1,
        max_tokens: int = 1024,
    ) -> dict[str, Any]:
        """Like complete(), but parse the response as JSON."""
        system_with_json = (system + "\n" if system else "") + (
            "You MUST respond with valid JSON only. No markdown, no explanation, no code fences."
        )
        resp = await self.complete(prompt, system_with_json, tier, temperature, max_tokens)
        text = resp.content.strip()
        # Strip markdown fences if the model ignores instructions
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0]
        return json.loads(text)

    # ── Provider resolution ──────────────────────────────────────

    def _resolve_provider(self, tier: ModelTier) -> LLMProvider:
        # Always use the configured primary provider (Groq by default).
        # SMART tier only upgrades to Anthropic/OpenAI if explicitly set as primary.
        # This prevents accidentally burning paid API credits for every scan cycle.
        return settings.llm_primary_provider

    def _resolve_fallback(self, failed: LLMProvider) -> LLMProvider:
        fb = settings.llm_fallback_provider
        if fb == failed:
            return LLMProvider.NONE
        return fb

    # ── Provider implementations ─────────────────────────────────

    async def _dispatch(
        self,
        provider: LLMProvider,
        prompt: str,
        system: str,
        temperature: float,
        max_tokens: int,
    ) -> LLMResponse:
        match provider:
            case LLMProvider.GROQ:
                return await self._call_groq(prompt, system, temperature, max_tokens)
            case LLMProvider.ANTHROPIC:
                return await self._call_anthropic(prompt, system, temperature, max_tokens)
            case LLMProvider.OPENAI:
                return await self._call_openai(prompt, system, temperature, max_tokens)
            case LLMProvider.OLLAMA:
                return await self._call_ollama(prompt, system, temperature, max_tokens)
            case _:
                raise ValueError(f"Unknown LLM provider: {provider}")

    @with_retry(max_attempts=2, min_wait=3.0, retry_on=(httpx.TimeoutException,))
    async def _call_groq(
        self, prompt: str, system: str, temperature: float, max_tokens: int
    ) -> LLMResponse:
        global _groq_last_call

        # Serialize all Groq calls and enforce minimum gap to stay under 30 RPM
        async with _get_groq_lock():
            wait = _GROQ_MIN_GAP - (time.monotonic() - _groq_last_call)
            if wait > 0:
                await asyncio.sleep(wait)
            _groq_last_call = time.monotonic()

            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})

            resp = await self._http.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {settings.groq_api_key}"},
                json={
                    "model": settings.groq_model,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                },
            )

            if resp.status_code == 429:
                # Rate limit hit despite the gate — surface as non-retryable
                # so the LLM gateway falls back to a different provider
                raise RuntimeError("Groq rate limited (429) — falling back")

            resp.raise_for_status()
        data = resp.json()
        usage = data.get("usage", {})

        return LLMResponse(
            content=data["choices"][0]["message"]["content"],
            provider="groq",
            model=settings.groq_model,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
        )

    @with_retry(max_attempts=2, min_wait=2.0, retry_on=(httpx.HTTPError, httpx.TimeoutException))
    async def _call_anthropic(
        self, prompt: str, system: str, temperature: float, max_tokens: int
    ) -> LLMResponse:
        resp = await self._http.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": settings.anthropic_api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": settings.anthropic_model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "system": system or "You are a prediction market analyst.",
                "messages": [{"role": "user", "content": prompt}],
            },
        )
        resp.raise_for_status()
        data = resp.json()
        usage = data.get("usage", {})

        return LLMResponse(
            content=data["content"][0]["text"],
            provider="anthropic",
            model=settings.anthropic_model,
            prompt_tokens=usage.get("input_tokens", 0),
            completion_tokens=usage.get("output_tokens", 0),
        )

    @with_retry(max_attempts=2, min_wait=2.0, retry_on=(httpx.HTTPError, httpx.TimeoutException))
    async def _call_openai(
        self, prompt: str, system: str, temperature: float, max_tokens: int
    ) -> LLMResponse:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        resp = await self._http.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {settings.openai_api_key}"},
            json={
                "model": "gpt-4o-mini",
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        usage = data.get("usage", {})

        return LLMResponse(
            content=data["choices"][0]["message"]["content"],
            provider="openai",
            model="gpt-4o-mini",
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
        )

    @with_retry(max_attempts=2, min_wait=2.0, retry_on=(httpx.HTTPError, httpx.ConnectError))
    async def _call_ollama(
        self, prompt: str, system: str, temperature: float, max_tokens: int
    ) -> LLMResponse:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        resp = await self._http.post(
            f"{settings.ollama_base_url}/api/chat",
            json={
                "model": settings.ollama_model,
                "messages": messages,
                "options": {"temperature": temperature, "num_predict": max_tokens},
                "stream": False,
            },
        )
        resp.raise_for_status()
        data = resp.json()

        return LLMResponse(
            content=data["message"]["content"],
            provider="ollama",
            model=settings.ollama_model,
        )

    async def close(self) -> None:
        await self._http.aclose()


# Singleton
llm = LLMGateway()
