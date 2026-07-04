"""
providers.py — async LLM provider fallback chain with a rate-limit
circuit breaker.

Azure OpenAI is the primary provider (production-grade, used by the
FastAPI integration this module is built for). groq/cerebras/openai/
openrouter are free-tier fallbacks for local dev and resilience when
Azure has an outage or its own rate limit.

Free-tier providers have daily/per-minute token caps. When one is hit,
every call fails identically until the cap resets — so a rate-limited
provider is put in cooldown (parsed from its own "try again in Xm Ys"
message when available) and skipped entirely until that cooldown
expires, instead of being retried on every subsequent question.

Chain order: azure_openai -> groq -> cerebras -> openai -> openrouter.
Providers without credentials in .env are skipped.

Fully async — every provider call and the retry/cooldown sleeps use
asyncio, so this never blocks the event loop when used from FastAPI.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from ai_service.prompts import build_messages

# Errors worth a same-provider retry: one-off blips (timeout, connection
# reset, a transient 5xx) where a second attempt might succeed.
RETRYABLE_ERROR_SUBSTRINGS = (
    "429", "rate limit", "rate_limit", "too many requests",
    "503", "502", "504", "service unavailable", "bad gateway",
    "timeout", "timed out", "connection", "reset by peer",
    "temporarily unavailable",
)

# Rate-limit errors specifically mean "this provider won't succeed again
# soon" — retrying immediately just burns another call against the same
# exhausted quota. These get a cooldown instead of a same-provider retry.
RATE_LIMIT_SUBSTRINGS = (
    "429", "rate limit", "rate_limit", "too many requests", "queue_exceeded",
)

# Used when a rate-limit error doesn't include a parseable duration
# (e.g. Cerebras's queue_exceeded message has no "try again in" hint).
DEFAULT_RATE_LIMIT_COOLDOWN_SECONDS = 60.0

_RETRY_AFTER_RE = re.compile(
    r"try again in\s+(?:(\d+)h)?\s*(?:(\d+)m)?\s*([\d.]+)?s?",
    re.IGNORECASE,
)

MAX_ATTEMPTS_PER_PROVIDER = 2


@dataclass
class Provider:
    """One LLM provider in the fallback chain."""
    name: str
    model: str
    api_key: str | None
    base_url: str | None = None  # only for OpenAI-compatible endpoints
    azure_endpoint: str | None = None  # only for provider "azure_openai"
    azure_api_version: str | None = None  # only for provider "azure_openai"
    cooldown_until: float = 0.0  # time.monotonic() timestamp; 0 = not cooling down
    _client: Any = field(default=None, repr=False, compare=False)  # lazily built, reused across calls

    @property
    def available(self) -> bool:
        if self.name == "azure_openai":
            return bool(self.api_key and self.azure_endpoint)
        return bool(self.api_key)

    def get_client(self):
        """Build the SDK client on first use and reuse it for every
        subsequent call — avoids opening a new HTTP connection pool
        per request."""
        if self._client is None:
            self._client = _build_client(self)
        return self._client

    @property
    def cooling_down(self) -> bool:
        return time.monotonic() < self.cooldown_until

    def cooldown_remaining(self) -> float:
        return max(0.0, self.cooldown_until - time.monotonic())

    def set_cooldown(self, seconds: float) -> None:
        self.cooldown_until = time.monotonic() + seconds


def build_provider_chain() -> list[Provider]:
    """Construct the ordered fallback list from .env. Providers with no
    credentials are still included but `available` will be False."""
    return [
        Provider(
            name="azure_openai",
            model=os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o-mini"),
            api_key=os.getenv("AZURE_OPENAI_API_KEY"),
            azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
            azure_api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-08-01-preview"),
        ),
        Provider(
            name="groq",
            model="llama-3.3-70b-versatile",
            api_key=os.getenv("GROQ_API_KEY"),
        ),
        Provider(
            name="cerebras",
            model="gpt-oss-120b",
            api_key=os.getenv("CEREBRAS_API_KEY"),
        ),
        Provider(
            name="openai",
            model="gpt-4o-mini",
            api_key=os.getenv("OPENAI_API_KEY"),
            base_url=os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1",
        ),
        Provider(
            name="openrouter",
            model="openai/gpt-oss-120b:free",
            api_key=os.getenv("OPENROUTER_API_KEY"),
            base_url="https://openrouter.ai/api/v1",
        ),
    ]


def is_retryable(exc: Exception) -> bool:
    msg = (str(exc) or "").lower()
    return any(s in msg for s in RETRYABLE_ERROR_SUBSTRINGS)


def is_rate_limit(exc: Exception) -> bool:
    msg = (str(exc) or "").lower()
    return any(s in msg for s in RATE_LIMIT_SUBSTRINGS)


def parse_retry_after_seconds(exc: Exception) -> float:
    """Parse "Please try again in 21m36.864s" (Groq's format) out of an
    error message. Falls back to DEFAULT_RATE_LIMIT_COOLDOWN_SECONDS."""
    match = _RETRY_AFTER_RE.search(str(exc))
    if not match:
        return DEFAULT_RATE_LIMIT_COOLDOWN_SECONDS
    hours, minutes, seconds = match.groups()
    total = float(hours or 0) * 3600 + float(minutes or 0) * 60 + float(seconds or 0)
    return total if total > 0 else DEFAULT_RATE_LIMIT_COOLDOWN_SECONDS


def _build_client(provider: Provider):
    """Construct the SDK client for one provider. Each provider needs a
    different class/constructor, but they all expose the same
    `.chat.completions.create()` call once built."""
    if provider.name == "azure_openai":
        from openai import AsyncAzureOpenAI
        return AsyncAzureOpenAI(
            azure_endpoint=provider.azure_endpoint,
            api_key=provider.api_key,
            api_version=provider.azure_api_version,
        )
    if provider.name == "groq":
        from groq import AsyncGroq
        return AsyncGroq(api_key=provider.api_key)
    if provider.name == "cerebras":
        from cerebras.cloud.sdk import AsyncCerebras
        return AsyncCerebras(api_key=provider.api_key)
    if provider.name in ("openrouter", "openai"):
        from openai import AsyncOpenAI
        return AsyncOpenAI(api_key=provider.api_key, base_url=provider.base_url)
    raise RuntimeError(f"Unknown provider: {provider.name}")


async def call_provider(provider: Provider, messages: list[dict], *, json_mode: bool) -> str:
    """Make one chat-completions call against the given provider, return
    the assistant text. Lets provider-specific exceptions propagate so
    the caller can decide whether to retry or fall through.

    json_mode=True is used for SQL generation (strict {"sql", ...} shape).
    json_mode=False is used for answer synthesis (free-form markdown).
    """
    client = provider.get_client()
    kwargs = {"model": provider.model, "messages": messages}
    if provider.name != "azure_openai":
        kwargs["temperature"] = 0  # some Azure deployments reject this param
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    try:
        resp = await client.chat.completions.create(**kwargs)
    except Exception as exc:
        # A few providers reject response_format entirely — retry once
        # without it rather than failing the whole call.
        if json_mode and "response_format" in str(exc).lower():
            resp = await client.chat.completions.create(**{k: v for k, v in kwargs.items() if k != "response_format"})
        else:
            raise

    if provider.name == "azure_openai":
        logger.debug(f"[azure_openai] usage: {resp.usage}")
    return resp.choices[0].message.content or ""


async def _try_provider(provider: Provider, messages: list[dict], *, json_mode: bool) -> str:
    """Attempt one provider, with up to MAX_ATTEMPTS_PER_PROVIDER tries
    for genuinely transient errors. A rate limit sets a cooldown and
    raises immediately — retrying against an exhausted quota is waste.

    Returns the raw assistant text (caller parses JSON if it needs to).
    """
    last_exc: Exception | None = None

    for attempt in range(1, MAX_ATTEMPTS_PER_PROVIDER + 1):
        start = time.monotonic()
        try:
            raw = await call_provider(provider, messages, json_mode=json_mode)
            elapsed_ms = int((time.monotonic() - start) * 1000)
            logger.info(f"[{provider.name}] answered in {elapsed_ms}ms")
            provider.cooldown_until = 0.0  # clear any stale cooldown on success
            return raw
        except Exception as exc:
            last_exc = exc
            if is_rate_limit(exc):
                cooldown = parse_retry_after_seconds(exc)
                provider.set_cooldown(cooldown)
                logger.warning(f"[{provider.name}] rate limited — cooling down for {cooldown:.0f}s: {exc}")
                raise
            if is_retryable(exc) and attempt < MAX_ATTEMPTS_PER_PROVIDER:
                logger.warning(f"[{provider.name}] transient error (attempt {attempt}/{MAX_ATTEMPTS_PER_PROVIDER}): {exc}")
                await asyncio.sleep(1.5 * attempt)
                continue
            logger.warning(f"[{provider.name}] error (attempt {attempt}/{MAX_ATTEMPTS_PER_PROVIDER}): {exc}")
            if attempt < MAX_ATTEMPTS_PER_PROVIDER:
                await asyncio.sleep(0.5 * attempt)
                continue
            raise

    assert last_exc is not None
    raise last_exc


async def run_chain(messages: list[dict], *, chain: list[Provider], json_mode: bool) -> tuple[str, str]:
    """Try each available, non-cooling-down provider in order.

    Returns (raw_text, provider_name_used). Raises RuntimeError if every
    provider fails or all are cooling down.
    """
    available = [p for p in chain if p.available]

    if not available:
        raise RuntimeError(
            "No LLM provider is configured. Set AZURE_OPENAI_API_KEY + "
            "AZURE_OPENAI_ENDPOINT, or one of GROQ_API_KEY, "
            "CEREBRAS_API_KEY, OPENAI_API_KEY, OPENROUTER_API_KEY in .env."
        )

    last_exc: Exception | None = None
    attempted_any = False

    for provider in available:
        if provider.cooling_down:
            logger.debug(f"[{provider.name}] skipped — cooling down for {provider.cooldown_remaining():.0f}s more")
            continue

        attempted_any = True
        try:
            raw = await _try_provider(provider, messages, json_mode=json_mode)
        except Exception as exc:
            last_exc = exc
            logger.warning(f"[{provider.name}] giving up, falling through to next provider")
            continue

        return raw, provider.name

    if not attempted_any:
        raise RuntimeError(
            "All configured providers are cooling down from rate limits. "
            "Wait a moment and try again, or add another provider's API key."
        )

    assert last_exc is not None
    raise RuntimeError(f"All {len(available)} providers failed. Last error: {last_exc}")


async def generate_sql(question: str, schema_text: str, role: str, *, chain: list[Provider]) -> tuple[dict, str]:
    """SQL-generation call: JSON mode, parses the {"sql", "explanation"} shape.

    Returns (parsed_json_response, provider_name_used).
    """
    messages = build_messages(question, schema_text, role)
    raw, provider_name = await run_chain(messages, chain=chain, json_mode=True)
    return json.loads(raw), provider_name


async def complete_text(messages: list[dict], *, chain: list[Provider]) -> tuple[str, str]:
    """Free-form text call (no JSON mode) — used for answer synthesis.

    Returns (text, provider_name_used).
    """
    return await run_chain(messages, chain=chain, json_mode=False)
