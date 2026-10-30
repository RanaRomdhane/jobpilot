"""httpx2 transport that enforces the shared rate limit under any OpenAI-compatible client.

Installing the limiter at the HTTP layer means *every* caller — our own `LLMProvider.complete`
and the OpenAI Agents SDK, which drives `AsyncOpenAI` itself — reserves budget before each request,
reconciles it with the real `usage` afterwards, and backs off on 429 with `retry-after`.
"""

from __future__ import annotations

import json
import logging
import random
import time
from collections.abc import Awaitable, Callable
from email.utils import parsedate_to_datetime
from typing import Any, cast

import httpx2

from jobpilot_core.llm.tokens import estimate_request_tokens
from jobpilot_core.llm.usage import NullUsageSink, UsageRecord, UsageSink
from jobpilot_core.ratelimit import TokenBucketLimiter

log = logging.getLogger(__name__)

RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


def parse_retry_after(headers: httpx2.Headers) -> float | None:
    """Seconds to wait, from `retry-after` (seconds or HTTP date) or Groq/OpenAI reset headers."""
    value = headers.get("retry-after")
    if value:
        try:
            return max(0.0, float(value))
        except ValueError:
            try:
                return max(0.0, parsedate_to_datetime(value).timestamp() - time.time())
            except (TypeError, ValueError):
                pass
    for name in ("x-ratelimit-reset-tokens", "x-ratelimit-reset-requests"):
        reset = headers.get(name)
        if reset:
            seconds = _parse_duration(reset)
            if seconds is not None:
                return seconds
    return None


def _parse_duration(value: str) -> float | None:
    """Parse '7.66s', '2m59.56s', '120ms' style durations."""
    total, num = 0.0, ""
    i = 0
    try:
        while i < len(value):
            ch = value[i]
            if ch.isdigit() or ch == ".":
                num += ch
            elif value.startswith("ms", i):
                total += float(num) / 1000
                num = ""
                i += 1
            elif ch in "hms":
                total += float(num) * {"h": 3600, "m": 60, "s": 1}[ch]
                num = ""
            else:
                return None
            i += 1
        if num:
            total += float(num)
    except ValueError:
        return None
    return total


class RateLimitedTransport(httpx2.AsyncBaseTransport):
    def __init__(
        self,
        limiter: TokenBucketLimiter,
        *,
        provider: str,
        purpose: str,
        usage_sink: UsageSink | None = None,
        max_retries: int = 4,
        output_reserve: int = 1024,
        max_wait_s: float | None = None,
        base_backoff_s: float = 1.0,
        inner: httpx2.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        import asyncio

        self._limiter = limiter
        self._provider = provider
        self._purpose = purpose
        self._sink: UsageSink = usage_sink or NullUsageSink()
        self._max_retries = max_retries
        self._output_reserve = output_reserve
        self._max_wait_s = max_wait_s
        self._base_backoff_s = base_backoff_s
        self._inner = inner or httpx2.AsyncHTTPTransport()
        self._sleep = sleep or asyncio.sleep

    async def aclose(self) -> None:
        await self._inner.aclose()

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        body = _json_body(request)
        if body is None:  # not an LLM call (e.g. models list): pass through, count as 1 request
            await self._limiter.acquire(0, max_wait_s=self._max_wait_s)
            return await self._inner.handle_async_request(request)

        estimate = estimate_request_tokens(body, self._output_reserve)
        model = str(body.get("model", "unknown"))
        streaming = bool(body.get("stream"))
        waited = 0.0
        attempt = 0

        while True:
            waited += await self._limiter.acquire(estimate, max_wait_s=self._max_wait_s)
            started = time.perf_counter()
            try:
                response = await self._inner.handle_async_request(request)
            except httpx2.TransportError as exc:
                await self._limiter.adjust(-estimate)
                if attempt >= self._max_retries:
                    await self._record(
                        model, estimate, 0, 0, started, waited, attempt, 0, repr(exc)
                    )
                    raise
                await self._sleep(self._backoff(attempt, None))
                attempt += 1
                continue

            if response.status_code in RETRYABLE_STATUS and attempt < self._max_retries:
                retry_after = parse_retry_after(response.headers)
                await response.aclose()
                # rejected requests are not billed: give the reservation back
                await self._limiter.adjust(-estimate)
                if response.status_code == 429:
                    await self._limiter.block_for(retry_after or self._backoff(attempt, None))
                delay = self._backoff(attempt, retry_after)
                log.warning(
                    "llm call retry",
                    extra={
                        "data": {
                            "provider": self._provider,
                            "status": response.status_code,
                            "attempt": attempt + 1,
                            "delay_s": round(delay, 2),
                        }
                    },
                )
                await self._sleep(delay)
                attempt += 1
                continue

            if streaming:
                # usage is not known up front for streams; keep the conservative reservation
                return response

            # read the still-encoded bytes straight from the transport stream; the rebuilt
            # response is decoded later by the client exactly as the original would have been
            stream = cast("httpx2.AsyncByteStream", response.stream)
            raw = b"".join([chunk async for chunk in stream])
            await response.aclose()
            rebuilt = httpx2.Response(
                status_code=response.status_code,
                headers=response.headers,
                content=raw,
                request=request,
                extensions=response.extensions,
            )
            input_tokens, output_tokens = _usage(rebuilt)
            actual = input_tokens + output_tokens
            if response.status_code >= 400 and actual == 0:
                await self._limiter.adjust(-estimate)
            elif actual:
                await self._limiter.adjust(actual - estimate)
            await self._record(
                model,
                estimate,
                input_tokens,
                output_tokens,
                started,
                waited,
                attempt,
                response.status_code,
                None if response.status_code < 400 else rebuilt.text[:500],
            )
            return rebuilt

    def _backoff(self, attempt: int, retry_after: float | None) -> float:
        exp: float = self._base_backoff_s * 2.0**attempt
        jitter = random.uniform(0, self._base_backoff_s)  # noqa: S311 - not security sensitive
        return max(retry_after or 0.0, exp) + jitter

    async def _record(
        self,
        model: str,
        estimate: int,
        input_tokens: int,
        output_tokens: int,
        started: float,
        waited: float,
        retries: int,
        status: int,
        error: str | None,
    ) -> None:
        try:
            await self._sink.record(
                UsageRecord(
                    provider=self._provider,
                    model=model,
                    purpose=self._purpose,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    estimated_tokens=estimate,
                    status_code=status,
                    waited_ms=int(waited * 1000),
                    retries=retries,
                    error=error,
                )
            )
        except Exception:  # usage logging must never break the call
            log.exception("failed to record llm usage")


def _json_body(request: httpx2.Request) -> dict[str, Any] | None:
    if request.method != "POST":
        return None
    try:
        data = json.loads(request.content or b"{}")
    except (ValueError, httpx2.RequestNotRead):
        return None
    return data if isinstance(data, dict) and "model" in data else None


def _usage(response: httpx2.Response) -> tuple[int, int]:
    try:
        data = response.json()
    except ValueError:
        return 0, 0
    usage = data.get("usage") if isinstance(data, dict) else None
    if not isinstance(usage, dict):
        return 0, 0
    inp = usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0
    out = usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0
    return int(inp), int(out)
