from __future__ import annotations

import asyncio
import time

import pytest
from fakeredis import FakeAsyncRedis

from jobpilot_core.ratelimit import (
    Limit,
    LimitUnit,
    RateLimitExceededError,
    TokenBucketLimiter,
    provider_limits,
)


def make(redis: FakeAsyncRedis, *limits: Limit, name: str = "t") -> TokenBucketLimiter:
    return TokenBucketLimiter(redis, name, list(limits))


async def test_takes_within_budget_then_requires_wait(redis: FakeAsyncRedis) -> None:
    lim = make(redis, Limit("tpm", 1000, 60))
    assert await lim.try_acquire(600) == 0.0
    wait = await lim.try_acquire(600)
    # 200 tokens missing at 1000/60s ≈ 12s
    assert 11.0 < wait <= 12.1


async def test_concurrent_acquires_never_exceed_capacity(redis: FakeAsyncRedis) -> None:
    lim = make(redis, Limit("tpm", 8000, 60))
    results = await asyncio.gather(*(lim.try_acquire(1000) for _ in range(20)))
    assert sum(1 for r in results if r == 0.0) == 8


async def test_all_buckets_must_allow(redis: FakeAsyncRedis) -> None:
    lim = make(redis, *provider_limits(tpm=100_000, rpm=2))
    assert await lim.try_acquire(10) == 0.0
    assert await lim.try_acquire(10) == 0.0
    # tokens left, but the request bucket is empty -> nothing is taken from either bucket
    before = await lim.level("tpm")
    assert await lim.try_acquire(10) > 0
    assert await lim.level("tpm") == before


async def test_refills_continuously(redis: FakeAsyncRedis) -> None:
    lim = make(redis, Limit("fast", 100, 0.5))
    assert await lim.try_acquire(100) == 0.0
    assert await lim.try_acquire(50) > 0
    await asyncio.sleep(0.3)  # ~60 tokens back
    assert await lim.try_acquire(50) == 0.0


async def test_request_larger_than_capacity_is_rejected(redis: FakeAsyncRedis) -> None:
    lim = make(redis, Limit("tpm", 8000, 60))
    with pytest.raises(RateLimitExceededError):
        await lim.try_acquire(9000)


async def test_acquire_waits_with_injected_sleep(redis: FakeAsyncRedis) -> None:
    lim = make(redis, Limit("fast", 100, 0.4))
    await lim.acquire(100)
    t0 = time.perf_counter()
    waited = await lim.acquire(100)
    assert waited >= 0.35
    assert time.perf_counter() - t0 >= 0.35


async def test_acquire_respects_max_wait(redis: FakeAsyncRedis) -> None:
    lim = make(redis, Limit("tpm", 1000, 60))
    await lim.acquire(1000)
    with pytest.raises(RateLimitExceededError):
        await lim.acquire(500, max_wait_s=1.0)


async def test_adjust_reconciles_estimate_with_actual(redis: FakeAsyncRedis) -> None:
    lim = make(redis, Limit("tpm", 1000, 60), Limit("rpm", 10, 60, LimitUnit.REQUESTS))
    await lim.acquire(800)  # estimate
    await lim.adjust(-500)  # actual usage was 300
    assert 690 <= await lim.level("tpm") <= 710
    assert await lim.level("rpm") == pytest.approx(9, abs=0.01)  # request buckets untouched


async def test_adjust_can_create_debt(redis: FakeAsyncRedis) -> None:
    lim = make(redis, Limit("tpm", 1000, 60))
    await lim.acquire(1000)
    await lim.adjust(300)  # real usage exceeded the estimate
    assert await lim.level("tpm") < 0
    assert await lim.try_acquire(100) > 20  # must first pay back the debt


async def test_block_for_pauses_everyone(redis: FakeAsyncRedis) -> None:
    a = make(redis, Limit("tpm", 1000, 60), name="shared")
    b = make(redis, Limit("tpm", 1000, 60), name="shared")
    await a.block_for(5)
    wait = await b.try_acquire(1)
    assert 4.0 < wait <= 5.0


async def test_limiters_are_isolated_by_name(redis: FakeAsyncRedis) -> None:
    groq = make(redis, Limit("tpm", 100, 60), name="groq")
    openai = make(redis, Limit("tpm", 100, 60), name="openai")
    await groq.acquire(100)
    assert await openai.try_acquire(100) == 0.0
