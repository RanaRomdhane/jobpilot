"""Process-wide resources created in the app lifespan."""

from __future__ import annotations

from dataclasses import dataclass

from arq.connections import ArqRedis
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from jobpilot_core.config import Settings


@dataclass(slots=True)
class AppState:
    settings: Settings
    engine: AsyncEngine
    sessionmaker: async_sessionmaker[AsyncSession]
    redis: Redis
    arq: ArqRedis
