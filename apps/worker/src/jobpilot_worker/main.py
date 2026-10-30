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
from sqlalchemy import select, update

from jobpilot_agents.tracing import configure_local_tracing
from jobpilot_core.config import get_settings
from jobpilot_core.crypto import SecretBox
from jobpilot_core.db import create_engine, create_sessionmaker
from jobpilot_core.enums import EmailCategory, OAuthScopeSet
from jobpilot_core.health import WORKER_HEARTBEAT_KEY, WORKER_HEARTBEAT_TTL_S
from jobpilot_core.log import configure_logging
from jobpilot_core.models import EmailMessage, OAuthCredential
from jobpilot_core.tasks import TaskReporter, create_task_run
from jobpilot_mcp_gmail.credentials import CredentialStore, GmailNotConnectedError
from jobpilot_mcp_gmail.gmail_client import GmailAuthExpiredError, GmailClient
from jobpilot_worker.triage import run_triage
from jobpilot_worker.user_labels import UserLabelActions

log = logging.getLogger("jobpilot.worker")

Ctx = dict[str, Any]


async def startup(ctx: Ctx) -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_redact_bodies)
    configure_local_tracing()
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


TRIAGE_LOCK = "lock:triage"


async def triage(ctx: Ctx, task_id: str, sync: bool = True) -> dict[str, Any]:
    """Sync Gmail and classify every unprocessed email (one run at a time)."""
    rep = reporter(ctx, task_id)
    lock = ctx["app_redis"].lock(TRIAGE_LOCK, timeout=WorkerSettings.job_timeout, blocking=False)
    if not await lock.acquire():
        await rep.fail("another triage run is in progress")
        return {"skipped": True}
    try:
        await rep.start("triage starting")
        result = await run_triage(
            ctx["settings"], ctx["sessionmaker"], ctx["app_redis"], rep, sync=sync
        )
        msg = f"{result['classified']} classified, {result['flagged']} flagged"
        await rep.succeed(result, msg)
        return result
    except (GmailNotConnectedError, GmailAuthExpiredError) as exc:
        await rep.fail(str(exc))
        return {"error": str(exc)}
    except Exception as exc:
        await rep.fail(f"{type(exc).__name__}: {exc}")
        raise
    finally:
        await lock.release()


async def user_label_change(
    ctx: Ctx, task_id: str, gmail_id: str, action: str, old: str | None, new: str | None
) -> dict[str, Any]:
    """Apply a dashboard re-classification / un-flag to Gmail labels (ADR-0005)."""
    rep = reporter(ctx, task_id)
    await rep.start(action)
    settings = ctx["settings"]
    store = CredentialStore(ctx["sessionmaker"], SecretBox.from_settings(settings))
    try:
        token, _ = await store.load(OAuthScopeSet.TRIAGE)
        gmail = GmailClient(token, on_token_refresh=lambda t: store.save(OAuthScopeSet.TRIAGE, t))
        try:
            actions = UserLabelActions(gmail)
            if action == "reclassify" and new is not None:
                await actions.reclassify(
                    gmail_id, EmailCategory(old) if old else None, EmailCategory(new)
                )
            elif action == "clear_suspicious":
                await actions.clear_suspicious(gmail_id)
            else:
                raise ValueError(f"unknown action {action!r}")
        finally:
            await gmail.aclose()
    except Exception as exc:
        await rep.fail(f"{type(exc).__name__}: {exc}")
        return {"ok": False}
    if action == "reclassify":
        async with ctx["sessionmaker"]() as session:
            await session.execute(
                update(EmailMessage)
                .where(EmailMessage.gmail_id == gmail_id)
                .values(label_applied=True)
            )
            await session.commit()
    await rep.succeed({"ok": True}, "Gmail labels updated")
    return {"ok": True}


async def scheduled_triage(ctx: Ctx) -> None:
    """Cron: incremental sync + triage, only once Gmail is connected."""
    async with ctx["sessionmaker"]() as session:
        connected = await session.scalar(
            select(OAuthCredential.id).where(OAuthCredential.scope_set == OAuthScopeSet.TRIAGE)
        )
        if connected is None:
            return
        row = await create_task_run(session, "triage", {"scheduled": True})
    await triage(ctx, str(row.id))


class WorkerSettings:
    """arq reads these plain class attributes directly."""

    functions = [ping, triage, user_label_change]  # noqa: RUF012
    cron_jobs = [  # noqa: RUF012
        cron(heartbeat, second={0, 15, 30, 45}, run_at_startup=True),
        cron(scheduled_triage, minute={5, 35}, unique=True, timeout=3600),
    ]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    max_jobs = 4
    job_timeout = 3600
    keep_result = 3600
    health_check_interval = 30
