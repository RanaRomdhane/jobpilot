"""Job discovery run: fetch every enabled source, normalize, dedup, scan, store, embed, match.

Sources are independent: one failing (network, layout change, daily cap, robots.txt) is recorded
on the dashboard and the others carry on. Postings are untrusted: the injection heuristics run
on every one at ingestion (cheap); the guard model runs later, only on the few that reach the
LLM re-rank.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from jobpilot_core.config import Settings
from jobpilot_core.models import JobPosting, UserProfile
from jobpilot_jobs_sources.filters import DEFAULT_PREFERENCES, JobFilters
from jobpilot_jobs_sources.http import PoliteClient, SourceError
from jobpilot_jobs_sources.models import NormalizedJob, SourceQuery
from jobpilot_jobs_sources.normalize import normalize
from jobpilot_jobs_sources.sources import SOURCES, JobSource
from jobpilot_jobs_sources.taxonomy import search_terms
from jobpilot_security import CleanText, InjectionDetector

if TYPE_CHECKING:
    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from jobpilot_core.tasks import TaskReporter
    from jobpilot_worker.embeddings import Embedder

log = logging.getLogger(__name__)

STATUS_KEY = "jobs:source_status"


@dataclass
class FetchSummary:
    sources_ok: int = 0
    sources_failed: int = 0
    fetched: int = 0
    new: int = 0
    duplicates: int = 0
    too_old: int = 0
    flagged: int = 0
    embedded: int = 0
    per_source: dict[str, dict[str, Any]] = field(default_factory=dict)


async def load_preferences(session: AsyncSession) -> JobFilters:
    prefs = await session.scalar(select(UserProfile.default_filters).limit(1))
    if prefs:
        try:
            return JobFilters.model_validate(prefs)
        except ValueError:
            log.warning("stored job preferences are invalid; using defaults")
    return DEFAULT_PREFERENCES


async def record_status(redis: Redis, name: str, status: dict[str, Any]) -> None:
    await redis.hset(STATUS_KEY, name, json.dumps(status, default=str))  # type: ignore[misc]


async def source_statuses(redis: Redis) -> dict[str, dict[str, Any]]:
    raw: dict[Any, Any] = await redis.hgetall(STATUS_KEY)  # type: ignore[misc]
    out: dict[str, dict[str, Any]] = {}
    for k, v in raw.items():
        key = k.decode() if isinstance(k, bytes) else str(k)
        try:
            out[key] = json.loads(v)
        except ValueError:
            continue
    return out


def _row(
    n: NormalizedJob, scan_flag: bool, score: float, reasons: list[dict[str, Any]]
) -> JobPosting:
    return JobPosting(
        source=n.source,
        external_id=n.external_id,
        url=n.url,
        title=n.title,
        company_name=n.company_name,
        location_raw=n.location_raw,
        country=n.country,
        region=n.region,
        work_mode=n.work_mode,
        domain=n.domain,
        seniority=n.seniority,
        contract_type=n.contract_type,
        salary_min=n.salary_min,
        salary_max=n.salary_max,
        salary_currency=n.salary_currency,
        language=n.language,
        description=n.description,
        posted_at=n.posted_at,
        dedup_hash=n.dedup_hash,
        injection_flag=scan_flag,
        injection_score=score,
        injection_reasons=reasons,
        apply_url=n.apply_url,
        apply_email=n.apply_email,
    )


async def store(
    sm: async_sessionmaker[AsyncSession],
    jobs: list[NormalizedJob],
    detector: InjectionDetector,
    summary: FetchSummary,
) -> list[int]:
    """Insert new postings; a posting already seen on another board is linked, not duplicated."""
    new_ids: list[int] = []
    async with sm() as session:
        hashes = {j.dedup_hash for j in jobs}
        keys = {(j.source, j.external_id) for j in jobs}
        existing = {
            row.dedup_hash: row
            for row in (
                await session.scalars(select(JobPosting).where(JobPosting.dedup_hash.in_(hashes)))
            ).all()
        }
        seen_keys = {
            (s, e)
            for s, e in (
                await session.execute(
                    select(JobPosting.source, JobPosting.external_id).where(
                        JobPosting.external_id.in_([k[1] for k in keys])
                    )
                )
            ).all()
        }
        batch_hashes: dict[str, JobPosting] = {}
        added: list[JobPosting] = []
        for n in jobs:
            if (n.source, n.external_id) in seen_keys:
                continue  # already stored from this source
            twin = existing.get(n.dedup_hash) or batch_hashes.get(n.dedup_hash)
            if twin is not None:
                if twin.source != n.source and all(
                    a.get("source") != n.source for a in twin.also_on
                ):
                    twin.also_on = [
                        *twin.also_on,
                        {"source": n.source, "url": n.url, "external_id": n.external_id},
                    ]
                summary.duplicates += 1
                continue
            report = detector.scan_heuristics(
                CleanText(text=f"{n.title}\n{n.description}", hidden=n.hidden_text)
            )
            if report.flagged:
                summary.flagged += 1
            row = _row(n, report.flagged, report.score, [r.model_dump() for r in report.reasons])
            session.add(row)
            added.append(row)
            batch_hashes[n.dedup_hash] = row
        await session.commit()
        new_ids = [r.id for r in added]
    summary.new += len(new_ids)
    return new_ids


async def fetch_all(
    settings: Settings,
    sm: async_sessionmaker[AsyncSession],
    redis: Redis,
    reporter: TaskReporter,
    detector: InjectionDetector,
    *,
    sources: tuple[JobSource, ...] = SOURCES,
    client: PoliteClient | None = None,
) -> tuple[FetchSummary, list[int]]:
    async with sm() as session:
        prefs = await load_preferences(session)
    query = SourceQuery(
        keywords=search_terms(prefs.domains),
        max_age_days=min(prefs.posted_within_days or 14, 30),
    )
    own_client = client is None
    http = client or PoliteClient(
        redis,
        user_agent=settings.jobs_user_agent,
        timeout_s=settings.jobs_request_timeout_s,
        cache_ttl_s=settings.jobs_cache_ttl_s,
        min_interval_s=settings.scrape_min_interval_s,
    )
    summary = FetchSummary()
    new_ids: list[int] = []
    oldest = datetime.now(UTC) - timedelta(days=settings.jobs_max_age_days)
    enabled = [s for s in sources if s.enabled(settings)]
    try:
        for i, source in enumerate(enabled):
            name = source.info.name
            await reporter.progress(0.05 + 0.6 * i / max(1, len(enabled)), f"fetching {name}")
            status: dict[str, Any] = {"at": datetime.now(UTC).isoformat()}
            try:
                raws = await source.search(http, query, settings)
                normalized: list[NormalizedJob] = []
                for raw in raws:
                    try:
                        n = normalize(raw)
                    except ValueError:
                        continue
                    if n.posted_at and n.posted_at < oldest:
                        summary.too_old += 1
                        continue
                    normalized.append(n)
                before = summary.new
                new_ids += await store(sm, normalized, detector, summary)
                summary.fetched += len(raws)
                summary.sources_ok += 1
                status.update(ok=True, fetched=len(raws), new=summary.new - before, error=None)
            except SourceError as exc:
                summary.sources_failed += 1
                status.update(ok=False, error=exc.code, detail=str(exc)[:300])
                log.warning("source %s failed: %s", name, exc)
            except Exception as exc:  # a broken connector must not stop the others
                summary.sources_failed += 1
                status.update(
                    ok=False, error="internal", detail=f"{type(exc).__name__}: {exc}"[:300]
                )
                log.exception("source %s crashed", name)
            status["calls_today"] = await http.calls_today(name)
            summary.per_source[name] = status
            await record_status(redis, name, status)
    finally:
        if own_client:
            await http.aclose()
    return summary, new_ids


def job_passage(job: JobPosting) -> str:
    return f"{job.title}. {job.company_name or ''}. {(job.description or '')[:2000]}"


async def embed_missing(
    sm: async_sessionmaker[AsyncSession], embedder: Embedder, *, limit: int = 2_000
) -> int:
    async with sm() as session:
        rows = (
            await session.scalars(
                select(JobPosting)
                .where(JobPosting.embedding.is_(None), JobPosting.hidden.is_(False))
                .order_by(JobPosting.id.desc())
                .limit(limit)
            )
        ).all()
        if not rows:
            return 0
        vectors = await embedder.aembed([job_passage(r) for r in rows], "passage")
        for row, vec in zip(rows, vectors, strict=True):
            row.embedding = vec.tolist()
        await session.commit()
        return len(rows)


def summary_dict(summary: FetchSummary) -> dict[str, Any]:
    return asdict(summary)
