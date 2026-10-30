from __future__ import annotations

import json
from typing import Any

import httpx2
import pytest
from fakeredis import FakeAsyncRedis

from jobpilot_core.config import LLMPurpose, Settings
from jobpilot_core.llm import MemoryUsageSink, OpenAICompatibleProvider, build_provider
from jobpilot_core.llm.provider import FallbackProvider, LLMConfigurationError
from jobpilot_core.llm.tokens import estimate_request_tokens
from jobpilot_core.llm.transport import RateLimitedTransport, parse_retry_after
from jobpilot_core.ratelimit import Limit, TokenBucketLimiter


def completion(content: str = "ok", prompt: int = 50, out: int = 10) -> dict[str, Any]:
    return {
        "id": "c1",
        "object": "chat.completion",
        "created": 0,
        "model": "m",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": content},
            }
        ],
        "usage": {"prompt_tokens": prompt, "completion_tokens": out, "total_tokens": prompt + out},
    }


class Backend:
    """Scripted fake provider: returns queued responses and records requests."""

    def __init__(self, *responses: httpx2.Response) -> None:
        self.queue = list(responses)
        self.requests: list[dict[str, Any]] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(json.loads(request.content))
        return self.queue.pop(0) if self.queue else httpx2.Response(200, json=completion())


async def no_sleep(_: float) -> None:
    return None


def chat_request(max_tokens: int = 100) -> httpx2.Request:
    body = {
        "model": "m",
        "messages": [{"role": "user", "content": "x" * 300}],
        "max_completion_tokens": max_tokens,
    }
    return httpx2.Request("POST", "https://llm.test/v1/chat/completions", json=body)


async def test_success_reconciles_budget_and_records_usage(redis: FakeAsyncRedis) -> None:
    limiter = TokenBucketLimiter(redis, "p", [Limit("tpm", 8000, 60)])
    sink = MemoryUsageSink()
    backend = Backend(httpx2.Response(200, json=completion(prompt=40, out=20)))
    transport = RateLimitedTransport(
        limiter,
        provider="groq",
        purpose="triage",
        usage_sink=sink,
        inner=httpx2.MockTransport(backend),
        sleep=no_sleep,
    )
    resp = await transport.handle_async_request(chat_request())
    assert resp.status_code == 200
    assert resp.json()["choices"][0]["message"]["content"] == "ok"
    # estimate was released, only the real 60 tokens stay consumed
    assert await limiter.level("tpm") == pytest.approx(8000 - 60, abs=2)
    [rec] = sink.records
    assert (rec.input_tokens, rec.output_tokens, rec.purpose) == (40, 20, "triage")
    assert rec.estimated_tokens > rec.total_tokens


async def test_429_blocks_globally_retries_and_refunds(redis: FakeAsyncRedis) -> None:
    limiter = TokenBucketLimiter(redis, "p", [Limit("tpm", 8000, 60)])
    sleeps: list[float] = []

    async def record_sleep(s: float) -> None:
        sleeps.append(s)
        await redis.delete("ratelimit:p:blocked")  # simulate the block expiring

    backend = Backend(
        httpx2.Response(429, headers={"retry-after": "7"}, json={"error": "rate"}),
        httpx2.Response(200, json=completion(prompt=10, out=5)),
    )
    sink = MemoryUsageSink()
    transport = RateLimitedTransport(
        limiter,
        provider="groq",
        purpose="triage",
        usage_sink=sink,
        inner=httpx2.MockTransport(backend),
        sleep=record_sleep,
        base_backoff_s=0.01,
    )
    resp = await transport.handle_async_request(chat_request())
    assert resp.status_code == 200
    assert len(backend.requests) == 2
    assert sleeps and sleeps[0] >= 7
    assert sink.records[0].retries == 1
    assert await limiter.level("tpm") == pytest.approx(8000 - 15, abs=2)


async def test_gives_up_after_max_retries(redis: FakeAsyncRedis) -> None:
    limiter = TokenBucketLimiter(redis, "p", [Limit("tpm", 8000, 60)])
    backend = Backend(*[httpx2.Response(503) for _ in range(5)])
    transport = RateLimitedTransport(
        limiter,
        provider="groq",
        purpose="triage",
        max_retries=2,
        inner=httpx2.MockTransport(backend),
        sleep=no_sleep,
    )
    resp = await transport.handle_async_request(chat_request())
    assert resp.status_code == 503
    assert len(backend.requests) == 3


def test_parse_retry_after_variants() -> None:
    assert parse_retry_after(httpx2.Headers({"retry-after": "2.5"})) == 2.5
    assert parse_retry_after(httpx2.Headers({"x-ratelimit-reset-tokens": "1m30.5s"})) == 90.5
    assert parse_retry_after(httpx2.Headers({"x-ratelimit-reset-requests": "120ms"})) == 0.12
    assert parse_retry_after(httpx2.Headers({})) is None


def test_estimate_includes_output_reservation() -> None:
    body: dict[str, Any] = {"model": "m", "messages": [{"role": "user", "content": "a" * 3000}]}
    assert estimate_request_tokens(body, 1024) >= 1000 + 1024
    body["max_completion_tokens"] = 200
    assert estimate_request_tokens(body, 1024) < 1000 + 300


async def test_provider_complete_uses_cache(redis: FakeAsyncRedis, settings: Settings) -> None:
    backend = Backend(httpx2.Response(200, json=completion("hello")))
    provider = OpenAICompatibleProvider(
        "groq", settings, redis, inner_transport=httpx2.MockTransport(backend)
    )
    msgs = [{"role": "user", "content": "hi"}]
    first = await provider.complete(LLMPurpose.TRIAGE, msgs)
    second = await provider.complete(LLMPurpose.TRIAGE, msgs)
    assert first.content == second.content == "hello"
    assert not first.cached and second.cached
    assert len(backend.requests) == 1
    assert backend.requests[0]["model"] == settings.groq_model_triage


async def test_provider_switch_is_config_only(redis: FakeAsyncRedis, settings: Settings) -> None:
    settings.llm_provider = "openai"
    provider = build_provider(settings, redis)
    assert provider.name == "openai"
    assert provider.model(LLMPurpose.TAILOR) == settings.openai_model_tailor


async def test_fallback_only_when_enabled(redis: FakeAsyncRedis, settings: Settings) -> None:
    assert not isinstance(build_provider(settings, redis), FallbackProvider)
    settings.llm_fallback_to_openai = True
    assert isinstance(build_provider(settings, redis), FallbackProvider)


async def test_missing_key_is_a_clear_error(redis: FakeAsyncRedis, settings: Settings) -> None:
    settings.groq_api_key = None
    with pytest.raises(LLMConfigurationError, match="GROQ_API_KEY"):
        build_provider(settings, redis)


def test_estimate_covers_provider_overhead() -> None:
    # measured on Groq gpt-oss-120b: this prompt was billed 78 input tokens
    body: dict[str, Any] = {
        "model": "m",
        "messages": [{"role": "user", "content": "Reply with exactly the word: pong"}],
        "max_completion_tokens": 64,
    }
    assert estimate_request_tokens(body, 1024, request_overhead=80) >= 78 + 64
