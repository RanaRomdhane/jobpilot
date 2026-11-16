"""Rate-limiter load test on a real Redis: several "processes" share one budget and never exceed it.

The API and every worker hold their own limiter object and Redis connection, like the four
clients here. The window is shortened to 2 seconds so the test sees several refills.
"""

from __future__ import annotations

import asyncio
import random
import time

import pytest
from redis.asyncio import Redis

from jobpilot_core.config import Settings
from jobpilot_core.ratelimit import Limit, LimitUnit, TokenBucketLimiter

pytestmark = pytest.mark.integration

CAPACITY = 2_000  # tokens per window
WINDOW_S = 2.0
RATE = CAPACITY / WINDOW_S
REQUESTS = 40  # requests per window
DURATION_S = 6.0
CLIENTS = 4
TASKS_PER_CLIENT = 8


async def test_many_clients_share_one_budget(
    integration_settings: Settings, real_redis: Redis
) -> None:
    limits = [
        Limit("tpm", CAPACITY, WINDOW_S),
        Limit("rpm", REQUESTS, WINDOW_S, LimitUnit.REQUESTS),
    ]
    connections = [Redis.from_url(integration_settings.redis_url) for _ in range(CLIENTS)]
    limiters = [TokenBucketLimiter(c, "load-test", limits) for c in connections]
    granted: list[tuple[float, int]] = []
    rng = random.Random(7)
    start = time.monotonic()
    deadline = start + DURATION_S

    async def worker(limiter: TokenBucketLimiter, sizes: list[int]) -> None:
        for size in sizes:
            if time.monotonic() >= deadline:
                return
            await limiter.acquire(size, max_wait_s=DURATION_S)
            if time.monotonic() < deadline:
                granted.append((time.monotonic() - start, size))

    try:
        await asyncio.gather(
            *(
                worker(limiter, [rng.randint(20, 300) for _ in range(200)])
                for limiter in limiters
                for _ in range(TASKS_PER_CLIENT)
            ),
            return_exceptions=True,
        )
    finally:
        for c in connections:
            await c.aclose()

    assert len(granted) > 50  # the clients really competed
    elapsed = max(t for t, _ in granted)
    slack = 1.05  # clock differences between this process and Redis

    # a token bucket never grants more than a full bucket plus what refilled meanwhile
    total = sum(size for _, size in granted)
    assert total <= (CAPACITY + RATE * elapsed) * slack
    assert len(granted) <= (REQUESTS + REQUESTS / WINDOW_S * elapsed) * slack

    # ...and that holds in every window, not just on average
    granted.sort()
    for i, (t0, _) in enumerate(granted):
        in_window = sum(size for t, size in granted[i:] if t - t0 <= WINDOW_S)
        assert in_window <= (CAPACITY + RATE * WINDOW_S) * slack, f"burst at {t0:.2f}s"

    # the budget is used, not wasted: waiting clients are woken up in time
    binding = min(RATE * elapsed, REQUESTS / WINDOW_S * elapsed * 160)  # 160 = mean request size
    assert total >= 0.6 * binding
