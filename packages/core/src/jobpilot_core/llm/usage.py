"""Usage records for every LLM call, persisted to `llm_call_log`."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


@dataclass(slots=True)
class UsageRecord:
    provider: str
    model: str
    purpose: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    estimated_tokens: int
    status_code: int
    cached: bool = False
    waited_ms: int = 0
    retries: int = 0
    error: str | None = None
    extra: dict[str, object] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class UsageSink(Protocol):
    async def record(self, usage: UsageRecord) -> None: ...


class NullUsageSink:
    async def record(self, usage: UsageRecord) -> None:
        return None


class MemoryUsageSink:
    """For tests and evals."""

    def __init__(self) -> None:
        self.records: list[UsageRecord] = []

    async def record(self, usage: UsageRecord) -> None:
        self.records.append(usage)


class DbUsageSink:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def record(self, usage: UsageRecord) -> None:
        from jobpilot_core.models import LLMCallLog

        async with self._sessionmaker() as session:
            session.add(
                LLMCallLog(
                    provider=usage.provider,
                    model=usage.model,
                    purpose=usage.purpose,
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    estimated_tokens=usage.estimated_tokens,
                    latency_ms=usage.latency_ms,
                    waited_ms=usage.waited_ms,
                    retries=usage.retries,
                    status_code=usage.status_code,
                    cached=usage.cached,
                    error=usage.error,
                )
            )
            await session.commit()
