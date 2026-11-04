"""Job discovery tasks: refresh (fetch + embed + score + re-rank + saved searches) and match."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select

from jobpilot_core.config import Settings
from jobpilot_core.llm import DbUsageSink, LLMConfigurationError, LLMProvider, build_provider
from jobpilot_core.models import JobMatch, JobPosting, SavedSearch
from jobpilot_jobs_sources.filters import JobFilters, conditions
from jobpilot_security.factory import build_detector
from jobpilot_worker.embeddings import Embedder
from jobpilot_worker.jobs import embed_missing, fetch_all, summary_dict
from jobpilot_worker.matching import profile_row, rerank, score_jobs

if TYPE_CHECKING:
    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from jobpilot_core.tasks import TaskReporter

log = logging.getLogger(__name__)

JOBS_LOCK = "lock:jobs"


async def get_embedder(ctx: dict[str, Any]) -> Embedder:
    """Loaded once per worker process (the model download happens on first use)."""
    if "embedder" not in ctx:
        settings: Settings = ctx["settings"]
        ctx["embedder"] = await Embedder.load(
            settings.storage_dir / "models", user_agent=settings.jobs_user_agent
        )
    embedder: Embedder = ctx["embedder"]
    return embedder


def llm_provider(
    settings: Settings, redis: Redis, sm: async_sessionmaker[AsyncSession]
) -> LLMProvider | None:
    try:
        return build_provider(settings, redis, usage_sink=DbUsageSink(sm))
    except LLMConfigurationError:
        return None


async def update_saved_searches(sm: async_sessionmaker[AsyncSession]) -> int:
    """Count, for each saved search, the postings that arrived since its last run."""
    now = datetime.now(UTC)
    total = 0
    async with sm() as session:
        prow = await profile_row(session)
        for s in (await session.scalars(select(SavedSearch).where(SavedSearch.enabled))).all():
            try:
                f = JobFilters.model_validate(s.filters)
            except ValueError:
                continue
            stmt = (
                select(func.count())
                .select_from(JobPosting)
                .outerjoin(
                    JobMatch,
                    (JobMatch.job_id == JobPosting.id)
                    & (JobMatch.resume_id == (prow.id if prow else -1)),
                )
                .where(JobPosting.hidden.is_(False), *conditions(f, now=now))
            )
            if s.last_run_at is not None:
                stmt = stmt.where(JobPosting.scraped_at > s.last_run_at)
            count = int(await session.scalar(stmt) or 0)
            s.new_match_count += count
            s.last_run_at = now
            total += count
        await session.commit()
    return total


async def refresh(
    ctx: dict[str, Any], reporter: TaskReporter, *, locale: str | None = None
) -> dict[str, Any]:
    settings: Settings = ctx["settings"]
    sm: async_sessionmaker[AsyncSession] = ctx["sessionmaker"]
    redis: Redis = ctx["app_redis"]
    provider = llm_provider(settings, redis, sm)
    detector = build_detector(settings, provider)

    summary, new_ids = await fetch_all(settings, sm, redis, reporter, detector)
    result: dict[str, Any] = {"fetch": summary_dict(summary)}

    await reporter.progress(0.68, "computing embeddings")
    embedder = await get_embedder(ctx)
    result["embedded"] = await embed_missing(sm, embedder)
    await reporter.progress(0.75, "scoring against your profile")
    result["scoring"] = await score_jobs(settings, sm, embedder)
    if provider is not None and "skipped" not in result["scoring"]:
        result["rerank"] = await rerank(settings, sm, provider, detector, reporter, locale=locale)
    result["saved_search_new"] = await update_saved_searches(sm)
    result["new"] = len(new_ids)
    return result


async def match(
    ctx: dict[str, Any],
    reporter: TaskReporter,
    *,
    job_ids: list[int] | None = None,
    rescore_all: bool = False,
    locale: str | None = None,
) -> dict[str, Any]:
    """Re-score (after a profile edit) and/or re-rank chosen jobs, without fetching."""
    settings: Settings = ctx["settings"]
    sm: async_sessionmaker[AsyncSession] = ctx["sessionmaker"]
    redis: Redis = ctx["app_redis"]
    provider = llm_provider(settings, redis, sm)
    embedder = await get_embedder(ctx)
    await reporter.progress(0.2, "scoring against your profile")
    result: dict[str, Any] = {
        "embedded": await embed_missing(sm, embedder),
        "scoring": await score_jobs(settings, sm, embedder, rescore_all=rescore_all),
    }
    if provider is not None and "skipped" not in result["scoring"]:
        result["rerank"] = await rerank(
            settings,
            sm,
            provider,
            build_detector(settings, provider),
            reporter,
            job_ids=job_ids,
            locale=locale,
        )
    return result
