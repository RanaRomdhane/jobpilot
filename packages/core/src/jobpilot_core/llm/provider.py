"""LLM provider abstraction.

Groq and OpenAI both speak the OpenAI chat-completions protocol, so a single implementation is
configured per provider. Switching provider or model is a `.env` change only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Protocol

import httpx2
from openai import AsyncOpenAI

from jobpilot_core.config import LLMPurpose, ProviderName, Settings
from jobpilot_core.llm.cache import ResponseCache, cache_key
from jobpilot_core.llm.transport import RateLimitedTransport
from jobpilot_core.llm.usage import NullUsageSink, UsageRecord, UsageSink
from jobpilot_core.ratelimit import RateLimitExceededError, TokenBucketLimiter, provider_limits

if TYPE_CHECKING:
    from redis.asyncio import Redis


class LLMConfigurationError(RuntimeError):
    pass


ReasoningEffort = Literal["low", "medium", "high"]
_REASONING_MODELS = ("openai/gpt-oss", "gpt-oss", "o1", "o3", "o4", "gpt-5")


def supports_reasoning_effort(model: str) -> bool:
    """Only reasoning models accept `reasoning_effort` (gpt-4.1 rejects it)."""
    return model.startswith(_REASONING_MODELS)


@dataclass(frozen=True, slots=True)
class ChatResult:
    content: str
    model: str
    provider: str
    input_tokens: int
    output_tokens: int
    cached: bool = False
    finish_reason: str | None = None


class LLMProvider(Protocol):
    name: ProviderName

    def model(self, purpose: LLMPurpose) -> str: ...

    def client(self, purpose: LLMPurpose) -> AsyncOpenAI:
        """A rate-limited OpenAI-compatible client, e.g. for the Agents SDK."""
        ...

    def guard_client(self) -> AsyncOpenAI:
        """A client for the injection guard model, rate-limited on its own budget."""
        ...

    async def complete(
        self,
        purpose: LLMPurpose,
        messages: list[dict[str, Any]],
        *,
        response_format: dict[str, Any] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        use_cache: bool = True,
        reasoning_effort: ReasoningEffort | None = None,
        store: bool = True,
    ) -> ChatResult: ...

    async def remember(
        self,
        purpose: LLMPurpose,
        messages: list[dict[str, Any]],
        result: ChatResult,
        *,
        response_format: dict[str, Any] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        reasoning_effort: ReasoningEffort | None = None,
    ) -> None:
        """Store `result` as the answer to this request, once the caller has validated it."""
        ...


class OpenAICompatibleProvider:
    def __init__(
        self,
        name: ProviderName,
        settings: Settings,
        redis: Redis,
        *,
        usage_sink: UsageSink | None = None,
        inner_transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        key = settings.api_key_for(name)
        if key is None or not key.get_secret_value():
            raise LLMConfigurationError(f"{name.upper()}_API_KEY is not set")
        self.name: ProviderName = name
        self._settings = settings
        self._api_key = key.get_secret_value()
        self._sink = usage_sink or NullUsageSink()
        self._inner = inner_transport
        tpm, rpm, tpd = settings.limits_for(name)
        self.limiter = TokenBucketLimiter(redis, name, provider_limits(tpm, rpm, tpd))
        self._cache = ResponseCache(redis, settings.llm_cache_ttl_s)
        self._clients: dict[LLMPurpose, AsyncOpenAI] = {}
        self._guard_client: AsyncOpenAI | None = None
        self._redis = redis

    def model(self, purpose: LLMPurpose) -> str:
        return self._settings.model_for(purpose, self.name)

    def client(self, purpose: LLMPurpose, *, max_wait_s: float | None = None) -> AsyncOpenAI:
        if max_wait_s is None and purpose in self._clients:
            return self._clients[purpose]
        transport = RateLimitedTransport(
            self.limiter,
            provider=self.name,
            purpose=purpose.value,
            usage_sink=self._sink,
            max_retries=self._settings.llm_max_retries,
            output_reserve=self._settings.llm_output_reserve_tokens,
            request_overhead=self._settings.request_overhead_for(self.name),
            max_wait_s=max_wait_s if max_wait_s is not None else self._settings.llm_max_wait_s,
            inner=self._inner,
        )
        client = AsyncOpenAI(
            api_key=self._api_key,
            base_url=self._settings.base_url_for(self.name),
            # retries, backoff and 429 handling live in the transport (shared budget)
            max_retries=0,
            timeout=self._settings.llm_request_timeout_s,
            http_client=httpx2.AsyncClient(transport=transport),
        )
        if max_wait_s is None:
            self._clients[purpose] = client
        return client

    def guard_client(self) -> AsyncOpenAI:
        """Client for the guard model, with its own budget (Groq rate limits are per model)."""
        if self._guard_client is not None:
            return self._guard_client
        tpm, rpm = self._settings.guard_limits_for(self.name)
        limiter = TokenBucketLimiter(self._redis, f"{self.name}:guard", provider_limits(tpm, rpm))
        transport = RateLimitedTransport(
            limiter,
            provider=self.name,
            purpose=LLMPurpose.GUARD.value,
            usage_sink=self._sink,
            max_retries=self._settings.llm_max_retries,
            output_reserve=16,  # classifiers return a single number
            max_wait_s=self._settings.llm_max_wait_s,
            inner=self._inner,
        )
        self._guard_client = AsyncOpenAI(
            api_key=self._api_key,
            base_url=self._settings.base_url_for(self.name),
            max_retries=0,
            timeout=self._settings.llm_request_timeout_s,
            http_client=httpx2.AsyncClient(transport=transport),
        )
        return self._guard_client

    @staticmethod
    def _payload(
        model: str,
        messages: list[dict[str, Any]],
        response_format: dict[str, Any] | None,
        temperature: float,
        max_tokens: int | None,
        reasoning_effort: ReasoningEffort | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"messages": messages, "temperature": temperature}
        if response_format is not None:
            payload["response_format"] = response_format
        if max_tokens is not None:
            payload["max_completion_tokens"] = max_tokens
        if reasoning_effort is not None and supports_reasoning_effort(model):
            payload["reasoning_effort"] = reasoning_effort
        return payload

    async def _store(self, key: str, result: ChatResult) -> None:
        if result.finish_reason not in ("stop", None):
            return
        await self._cache.set(
            key,
            {
                "content": result.content,
                "model": result.model,
                "provider": result.provider,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "finish_reason": result.finish_reason,
            },
        )

    async def remember(
        self,
        purpose: LLMPurpose,
        messages: list[dict[str, Any]],
        result: ChatResult,
        *,
        response_format: dict[str, Any] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        reasoning_effort: ReasoningEffort | None = None,
    ) -> None:
        model = self.model(purpose)
        payload = self._payload(
            model, messages, response_format, temperature, max_tokens, reasoning_effort
        )
        await self._store(cache_key(self.name, model, payload), result)

    async def complete(
        self,
        purpose: LLMPurpose,
        messages: list[dict[str, Any]],
        *,
        response_format: dict[str, Any] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        use_cache: bool = True,
        reasoning_effort: ReasoningEffort | None = None,
        store: bool = True,
        max_wait_s: float | None = None,
    ) -> ChatResult:
        """`use_cache` reads and writes the cache; `store=False` leaves the writing to the caller
        (`remember`), for answers that must be validated first."""
        model = self.model(purpose)
        payload = self._payload(
            model, messages, response_format, temperature, max_tokens, reasoning_effort
        )
        key = cache_key(self.name, model, payload)
        if use_cache:
            hit = await self._cache.get(key)
            if hit is not None:
                await self._sink.record(
                    UsageRecord(self.name, model, purpose.value, 0, 0, 0, 0, 200, cached=True)
                )
                return ChatResult(**{**hit, "cached": True})

        resp = await self.client(purpose, max_wait_s=max_wait_s).chat.completions.create(
            model=model, **payload
        )
        choice = resp.choices[0]
        result = ChatResult(
            content=choice.message.content or "",
            model=resp.model,
            provider=self.name,
            input_tokens=resp.usage.prompt_tokens if resp.usage else 0,
            output_tokens=resp.usage.completion_tokens if resp.usage else 0,
            finish_reason=choice.finish_reason,
        )
        if use_cache and store:
            await self._store(key, result)
        return result


class FallbackProvider:
    """Uses the primary provider; if its budget would make us wait too long, uses the fallback."""

    def __init__(
        self,
        primary: OpenAICompatibleProvider,
        fallback: OpenAICompatibleProvider,
        *,
        max_primary_wait_s: float = 20.0,
    ) -> None:
        self.primary = primary
        self.fallback = fallback
        self.name: ProviderName = primary.name
        self._max_primary_wait_s = max_primary_wait_s

    def model(self, purpose: LLMPurpose) -> str:
        return self.primary.model(purpose)

    def client(self, purpose: LLMPurpose) -> AsyncOpenAI:
        # Agents keep a single model for a whole run, so they stay on the primary provider.
        return self.primary.client(purpose)

    def guard_client(self) -> AsyncOpenAI:
        return self.primary.guard_client()

    async def complete(
        self,
        purpose: LLMPurpose,
        messages: list[dict[str, Any]],
        *,
        response_format: dict[str, Any] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        use_cache: bool = True,
        reasoning_effort: ReasoningEffort | None = None,
        store: bool = True,
    ) -> ChatResult:
        kwargs: dict[str, Any] = {
            "response_format": response_format,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "use_cache": use_cache,
            "reasoning_effort": reasoning_effort,
            "store": store,
        }
        try:
            return await self.primary.complete(
                purpose, messages, max_wait_s=self._max_primary_wait_s, **kwargs
            )
        except RateLimitExceededError:
            return await self.fallback.complete(purpose, messages, **kwargs)

    async def remember(
        self,
        purpose: LLMPurpose,
        messages: list[dict[str, Any]],
        result: ChatResult,
        *,
        response_format: dict[str, Any] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        reasoning_effort: ReasoningEffort | None = None,
    ) -> None:
        # stored under the provider that answered, like `complete` does
        answered_by = self.fallback if result.provider == self.fallback.name else self.primary
        await answered_by.remember(
            purpose,
            messages,
            result,
            response_format=response_format,
            temperature=temperature,
            max_tokens=max_tokens,
            reasoning_effort=reasoning_effort,
        )


def build_provider(
    settings: Settings, redis: Redis, *, usage_sink: UsageSink | None = None
) -> LLMProvider:
    primary = OpenAICompatibleProvider(
        settings.llm_provider, settings, redis, usage_sink=usage_sink
    )
    if settings.llm_fallback_to_openai and settings.llm_provider != "openai":
        fallback = OpenAICompatibleProvider("openai", settings, redis, usage_sink=usage_sink)
        return FallbackProvider(primary, fallback)
    return primary
