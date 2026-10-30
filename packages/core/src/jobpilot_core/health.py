"""Component health probes shared by the API health endpoint and the CLI."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import text

if TYPE_CHECKING:
    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import AsyncEngine

WORKER_HEARTBEAT_KEY = "worker:heartbeat"
WORKER_HEARTBEAT_TTL_S = 60


@dataclass(frozen=True, slots=True)
class ComponentHealth:
    name: str
    ok: bool
    critical: bool
    detail: str | None = None
    latency_ms: int | None = None


async def check_database(engine: AsyncEngine) -> ComponentHealth:
    t0 = time.perf_counter()
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
            ext = await conn.scalar(
                text("SELECT extversion FROM pg_extension WHERE extname='vector'")
            )
            rev = await conn.scalar(text("SELECT version_num FROM alembic_version"))
    except Exception as exc:
        return ComponentHealth("database", False, True, type(exc).__name__)
    ok = ext is not None and rev is not None
    detail = f"pgvector {ext}, revision {rev}" if ok else "pgvector or migrations missing"
    return ComponentHealth("database", ok, True, detail, int((time.perf_counter() - t0) * 1000))


async def check_redis(redis: Redis) -> ComponentHealth:
    t0 = time.perf_counter()
    try:
        await redis.ping()
    except Exception as exc:
        return ComponentHealth("redis", False, True, type(exc).__name__)
    return ComponentHealth("redis", True, True, None, int((time.perf_counter() - t0) * 1000))


async def check_worker(redis: Redis) -> ComponentHealth:
    try:
        raw = await redis.get(WORKER_HEARTBEAT_KEY)
    except Exception as exc:
        return ComponentHealth("worker", False, False, type(exc).__name__)
    if raw is None:
        return ComponentHealth("worker", False, False, "no heartbeat")
    age = time.time() - float(raw)
    return ComponentHealth(
        "worker", age < WORKER_HEARTBEAT_TTL_S, False, f"heartbeat {age:.0f}s ago"
    )


def check_latex(spool_dir: Path, max_age_s: int) -> ComponentHealth:
    try:
        data = json.loads((spool_dir / "heartbeat").read_text(encoding="utf-8"))
        age = time.time() - float(data["ts"])
    except (OSError, ValueError, KeyError):
        return ComponentHealth("latex", False, False, "no heartbeat")
    return ComponentHealth(
        "latex", age < max_age_s, False, f"heartbeat {age:.0f}s ago, engine {data.get('engine')}"
    )
