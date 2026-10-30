"""Distributed token-bucket rate limiter backed by Redis.

All API and worker processes share the same buckets, so a provider budget such as the Groq free
tier (8,000 tokens/minute) is enforced globally. Buckets refill continuously; the check-and-take
across *all* buckets of a limiter (e.g. TPM + RPM + TPD) is a single atomic Lua script that uses the
Redis server clock, so processes with skewed clocks still agree.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from redis.asyncio import Redis


class LimitUnit(StrEnum):
    TOKENS = "tokens"
    REQUESTS = "requests"


@dataclass(frozen=True, slots=True)
class Limit:
    name: str  # e.g. "tpm"
    capacity: int  # units allowed per window
    window_s: float  # refill window in seconds
    unit: LimitUnit = LimitUnit.TOKENS


class RateLimitExceededError(RuntimeError):
    """Raised when a request can never fit, or waiting would exceed `max_wait_s`."""


# KEYS[1]      = block key (global cooldown after a 429)
# KEYS[2..n+1] = bucket keys
# ARGV[1..3n]  = (capacity, window_ms, cost) per bucket
# Returns {status, wait_ms}: status 1 = taken, 0 = must wait, -1 = cost exceeds a capacity.
_ACQUIRE_LUA = """
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
local blocked = redis.call('PTTL', KEYS[1])
if blocked > 0 then return {0, blocked} end
local n = #KEYS - 1
local levels = {}
local wait = 0
for i = 1, n do
  local cap = tonumber(ARGV[(i-1)*3 + 1])
  local win = tonumber(ARGV[(i-1)*3 + 2])
  local cost = tonumber(ARGV[(i-1)*3 + 3])
  if cost > cap then return {-1, 0} end
  local data = redis.call('HMGET', KEYS[i+1], 'tokens', 'ts')
  local tokens = tonumber(data[1])
  local ts = tonumber(data[2])
  if tokens == nil then tokens = cap; ts = now end
  tokens = math.min(cap, tokens + (now - ts) * cap / win)
  levels[i] = tokens
  if tokens < cost then
    local w = math.ceil((cost - tokens) * win / cap)
    if w > wait then wait = w end
  end
end
if wait > 0 then return {0, wait} end
for i = 1, n do
  local cap = tonumber(ARGV[(i-1)*3 + 1])
  local win = tonumber(ARGV[(i-1)*3 + 2])
  local cost = tonumber(ARGV[(i-1)*3 + 3])
  redis.call('HSET', KEYS[i+1], 'tokens', tostring(levels[i] - cost), 'ts', tostring(now))
  redis.call('PEXPIRE', KEYS[i+1], math.ceil(win * 2))
end
return {1, 0}
"""

# KEYS[1] = bucket key; ARGV = capacity, window_ms, delta (positive = take more, negative = refund).
# Balance may go negative ("debt") when the real usage exceeded the estimate.
_ADJUST_LUA = """
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
local cap = tonumber(ARGV[1])
local win = tonumber(ARGV[2])
local delta = tonumber(ARGV[3])
local data = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local tokens = tonumber(data[1])
local ts = tonumber(data[2])
if tokens == nil then tokens = cap; ts = now end
tokens = math.min(cap, tokens + (now - ts) * cap / win) - delta
tokens = math.min(cap, tokens)
redis.call('HSET', KEYS[1], 'tokens', tostring(tokens), 'ts', tostring(now))
redis.call('PEXPIRE', KEYS[1], math.ceil(win * 2))
return tostring(tokens)
"""


class TokenBucketLimiter:
    def __init__(
        self,
        redis: Redis,
        name: str,
        limits: Sequence[Limit],
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if not limits:
            raise ValueError("at least one limit is required")
        self._redis = redis
        self.name = name
        self.limits = tuple(limits)
        self._sleep = sleep
        self._acquire = redis.register_script(_ACQUIRE_LUA)
        self._adjust = redis.register_script(_ADJUST_LUA)

    def _key(self, limit: Limit) -> str:
        return f"ratelimit:{self.name}:{limit.name}"

    @property
    def _block_key(self) -> str:
        return f"ratelimit:{self.name}:blocked"

    def _args(self, tokens: int) -> list[float]:
        args: list[float] = []
        for lim in self.limits:
            cost = tokens if lim.unit is LimitUnit.TOKENS else 1
            args += [lim.capacity, lim.window_s * 1000, cost]
        return args

    async def try_acquire(self, tokens: int) -> float:
        """Take budget for one request of `tokens` tokens.

        Returns 0.0 when the budget was taken, otherwise the seconds to wait before retrying.
        """
        keys = [self._block_key, *(self._key(lim) for lim in self.limits)]
        res = cast("list[int]", await self._acquire(keys=keys, args=self._args(max(tokens, 0))))
        status, wait_ms = int(res[0]), int(res[1])
        if status == -1:
            raise RateLimitExceededError(
                f"{self.name}: request of {tokens} tokens exceeds a bucket capacity"
            )
        return 0.0 if status == 1 else wait_ms / 1000

    async def acquire(self, tokens: int, *, max_wait_s: float | None = None) -> float:
        """Wait until the budget allows the request; returns the total time waited."""
        waited = 0.0
        while True:
            wait = await self.try_acquire(tokens)
            if wait == 0.0:
                return waited
            if max_wait_s is not None and waited + wait > max_wait_s:
                raise RateLimitExceededError(
                    f"{self.name}: would wait {waited + wait:.1f}s > max {max_wait_s:.1f}s"
                )
            # small fixed slack so we do not wake up a few ms too early and spin
            wait = wait + 0.05
            await self._sleep(wait)
            waited += wait

    async def adjust(self, token_delta: int) -> None:
        """Correct token buckets once the real usage is known (`actual - estimated`)."""
        if token_delta == 0:
            return
        for lim in self.limits:
            if lim.unit is LimitUnit.TOKENS:
                await self._adjust(
                    keys=[self._key(lim)], args=[lim.capacity, lim.window_s * 1000, token_delta]
                )

    async def block_for(self, seconds: float) -> None:
        """Pause every process using this limiter (after a provider 429 with retry-after)."""
        ms = max(1, math.ceil(seconds * 1000))
        # keep the longest pending block
        current = await self._redis.pttl(self._block_key)
        if current is None or current < ms:
            await self._redis.set(self._block_key, "1", px=ms)

    async def level(self, limit_name: str) -> float:
        """Current (un-refilled) stored level of a bucket; for diagnostics and tests."""
        lim = next(lim for lim in self.limits if lim.name == limit_name)
        raw = await self._redis.hget(self._key(lim), "tokens")  # type: ignore[misc]
        return float(lim.capacity if raw is None else raw)


def provider_limits(tpm: int, rpm: int, tpd: int = 0) -> list[Limit]:
    limits = [
        Limit("tpm", tpm, 60.0, LimitUnit.TOKENS),
        Limit("rpm", rpm, 60.0, LimitUnit.REQUESTS),
    ]
    if tpd > 0:
        limits.append(Limit("tpd", tpd, 86_400.0, LimitUnit.TOKENS))
    return limits
