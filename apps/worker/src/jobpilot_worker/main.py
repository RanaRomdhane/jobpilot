"""arq worker: every long-running operation runs here, never in the API process."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

from arq import cron
from arq.connections import RedisSettings
from redis.asyncio import Redis

from jobpilot_core.config import get_settings
from jobpilot_core.db import create_engine, create_sessionmaker
from jobpilot_core.health import WORKER_HEARTBEAT_KEY, WORKER_HEARTBEAT_TTL_S
from jobpilot_core.log import configure_logging
from jobpilot_core.tasks import TaskReporter

log = logging.getLogger("jobpilot.worker")

Ctx = dict[str, Any]


async def startup(ctx: Ctx) -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_redact_bodies)
    engine = create_engine(settings)
    ctx["settings"] = settings
    ctx["engine"] = engine
    ctx["sessionmaker"] = create_sessionmaker(engine)
    ctx["app_redis"] = Redis.from_url(settings.redis_url)
    await heartbeat(ctx)
    log.info("worker started")


async def shutdown(ctx: Ctx) -> None:
    await ctx["app_redis"].aclose()
    await ctx["engine"].dispose()


async def heartbeat(ctx: Ctx) -> None:
    await ctx["app_redis"].set(WORKER_HEARTBEAT_KEY, str(time.time()), ex=WORKER_HEARTBEAT_TTL_S)


def reporter(ctx: Ctx, task_id: str) -> TaskReporter:
    return TaskReporter(ctx["sessionmaker"], ctx["app_redis"], uuid.UUID(task_id))


async def ping(ctx: Ctx, task_id: str, steps: int = 5, delay_s: float = 0.2) -> dict[str, Any]:
    """Demo task: proves enqueue → worker → DB → Redis pub/sub → SSE end to end."""
    rep = reporter(ctx, task_id)
    try:
        await rep.start("pong starting")
        for i in range(1, steps + 1):
            await asyncio.sleep(delay_s)
            await rep.progress(i / steps, f"step {i}/{steps}")
        result = {"pong": True, "steps": steps}
        await rep.succeed(result, "pong")
        return result
    except Exception as exc:
        await rep.fail(repr(exc))
        raise


class WorkerSettings:
    """arq reads these plain class attributes directly."""

    functions = [ping]  # noqa: RUF012
    cron_jobs = [cron(heartbeat, second={0, 15, 30, 45}, run_at_startup=True)]  # noqa: RUF012
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    max_jobs = 4
    job_timeout = 3600
    keep_result = 3600
    health_check_interval = 30
