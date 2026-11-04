"""Job discovery: search with filters and match scores, save/hide, refresh, re-rank, sources,
default filters, saved searches. Fetching and matching always run in the worker."""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from jobpilot_api.auth import Authenticated
from jobpilot_api.deps import Session, State
from jobpilot_core.enums import Region, WorkMode
from jobpilot_core.models import JobMatch, JobPosting, Resume, SavedSearch, UserProfile
from jobpilot_core.tasks import create_task_run
from jobpilot_jobs_sources.filters import DEFAULT_PREFERENCES, JobFilters, conditions
from jobpilot_jobs_sources.sources import SOURCE_BY_NAME, SOURCES
from jobpilot_jobs_sources.taxonomy import DOMAIN_KEYS

router = APIRouter(prefix="/jobs", tags=["jobs"])
saved_router = APIRouter(prefix="/saved-searches", tags=["jobs"])

Locale = Literal["fr", "en"]
STATUS_KEY = "jobs:source_status"  # written by the worker (jobpilot_worker.jobs)


class MatchOut(BaseModel):
    score: float
    vector_score: float | None
    keyword_score: float | None
    llm_score: float | None
    matched_keywords: list[str]
    missing_keywords: list[str]
    rationale: str | None


class JobOut(BaseModel):
    id: int
    source: str
    source_name: str
    url: str | None
    title: str
    company_name: str | None
    location_raw: str | None
    country: str | None
    region: Region | None
    work_mode: WorkMode | None
    domain: str | None
    seniority: str | None
    contract_type: str | None
    salary_min: Decimal | None
    salary_max: Decimal | None
    salary_currency: str | None
    language: str | None
    posted_at: datetime | None
    scraped_at: datetime
    saved: bool
    hidden: bool
    injection_flag: bool
    also_on: list[dict[str, Any]]
    apply_url: str | None
    match: MatchOut | None


class JobDetail(JobOut):
    description: str | None
    apply_email: str | None
    injection_reasons: list[dict[str, Any]]
    attribution: str | None


class JobSearch(BaseModel):
    filters: JobFilters = Field(default_factory=JobFilters)
    sort: Literal["match", "date"] = "match"
    saved_only: bool = False
    include_hidden: bool = False
    limit: int = Field(default=30, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class JobPage(BaseModel):
    items: list[JobOut]
    total: int
    profile_ready: bool  # False until a CV has been parsed (no match scores yet)


class JobFlags(BaseModel):
    saved: bool | None = None
    hidden: bool | None = None


class TaskStarted(BaseModel):
    task_id: str


class RefreshIn(BaseModel):
    locale: Locale | None = None


class RerankIn(BaseModel):
    job_ids: list[int] = Field(min_length=1, max_length=20)
    locale: Locale | None = None


class SourceStatus(BaseModel):
    name: str
    display_name: str
    homepage: str
    kind: str
    requires_key: bool
    enabled: bool
    regions: list[Region]
    attribution: str | None
    last_run: dict[str, Any] | None
    job_count: int


class Taxonomy(BaseModel):
    domains: list[str]
    regions: list[Region]
    work_modes: list[WorkMode]
    seniority: list[str]
    contracts: list[str]


# ---------------------------------------------------------------- helpers
async def _profile_id(session: Session) -> int | None:
    master = await session.scalar(select(Resume.id).where(Resume.is_master))
    if master is not None:
        return int(master)
    latest = await session.scalar(
        select(Resume.id)
        .where(Resume.status == "parsed", Resume.parsed.is_not(None))
        .order_by(Resume.id.desc())
        .limit(1)
    )
    return int(latest) if latest is not None else None


def _out(job: JobPosting, m: JobMatch | None) -> dict[str, Any]:
    src = SOURCE_BY_NAME.get(job.source)
    return {
        **{f: getattr(job, f) for f in JobOut.model_fields if hasattr(job, f) and f != "match"},
        "source_name": src.info.display_name if src else job.source,
        "match": (
            MatchOut.model_validate(m, from_attributes=True).model_dump() if m is not None else None
        ),
    }


async def _job(session: Session, job_id: int) -> JobPosting:
    job = await session.get(JobPosting, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "job not found")
    return job


async def _user_profile(session: Session) -> UserProfile:
    profile = (await session.scalars(select(UserProfile).limit(1))).first()
    if profile is None:
        profile = UserProfile()
        session.add(profile)
        await session.flush()
    return profile


# ---------------------------------------------------------------- search & detail
@router.post("/search", response_model=JobPage)
async def search_jobs(body: JobSearch, _: Authenticated, session: Session) -> JobPage:
    pid = await _profile_id(session)
    match_join = (JobMatch.job_id == JobPosting.id) & (JobMatch.resume_id == (pid or -1))
    base = (
        select(JobPosting, JobMatch)
        .outerjoin(JobMatch, match_join)
        .where(*conditions(body.filters))
    )
    if not body.include_hidden:
        base = base.where(JobPosting.hidden.is_(False))
    if body.saved_only:
        base = base.where(JobPosting.saved.is_(True))
    order: list[Any] = (
        [JobMatch.score.desc().nulls_last(), JobPosting.posted_at.desc().nulls_last()]
        if body.sort == "match"
        else [JobPosting.posted_at.desc().nulls_last(), JobPosting.id.desc()]
    )
    rows = (
        await session.execute(base.order_by(*order).limit(body.limit).offset(body.offset))
    ).all()
    total = await session.scalar(select(func.count()).select_from(base.subquery()))
    return JobPage(
        items=[JobOut.model_validate(_out(j, m)) for j, m in rows],
        total=int(total or 0),
        profile_ready=pid is not None,
    )


@router.get("/taxonomy", response_model=Taxonomy)
async def taxonomy(_: Authenticated) -> Taxonomy:
    return Taxonomy(
        domains=list(DOMAIN_KEYS),
        regions=[r for r in Region if r is not Region.OTHER],
        work_modes=list(WorkMode),
        seniority=["intern", "junior", "mid", "senior", "lead"],
        contracts=["permanent", "fixed_term", "freelance", "internship", "apprenticeship"],
    )


@router.get("/preferences", response_model=JobFilters)
async def get_preferences(_: Authenticated, session: Session) -> JobFilters:
    profile = await _user_profile(session)
    await session.commit()
    if profile.default_filters:
        try:
            return JobFilters.model_validate(profile.default_filters)
        except ValueError:
            pass
    return DEFAULT_PREFERENCES


@router.put("/preferences", response_model=JobFilters)
async def save_preferences(body: JobFilters, _: Authenticated, session: Session) -> JobFilters:
    profile = await _user_profile(session)
    profile.default_filters = body.model_dump(mode="json")
    await session.commit()
    return body


@router.get("/sources", response_model=list[SourceStatus])
async def sources(_: Authenticated, session: Session, state: State) -> list[SourceStatus]:
    raw = await state.redis.hgetall(STATUS_KEY)  # type: ignore[misc]
    runs: dict[str, Any] = {}
    for k, v in raw.items():
        try:
            runs[k.decode() if isinstance(k, bytes) else str(k)] = json.loads(v)
        except ValueError:
            continue
    counts = dict(
        (
            await session.execute(
                select(JobPosting.source, func.count()).group_by(JobPosting.source)
            )
        ).all()
    )
    return [
        SourceStatus(
            **s.info.model_dump(
                include={
                    "name",
                    "display_name",
                    "homepage",
                    "kind",
                    "requires_key",
                    "regions",
                    "attribution",
                }
            ),
            enabled=s.enabled(state.settings),
            last_run=runs.get(s.info.name),
            job_count=int(counts.get(s.info.name, 0)),
        )
        for s in SOURCES
    ]


@router.post("/refresh", response_model=TaskStarted, status_code=status.HTTP_202_ACCEPTED)
async def refresh(body: RefreshIn, _: Authenticated, session: Session, state: State) -> TaskStarted:
    task = await create_task_run(session, "jobs_refresh", {"manual": True})
    await state.arq.enqueue_job("jobs_refresh", str(task.id), body.locale, _job_id=str(task.id))
    return TaskStarted(task_id=str(task.id))


@router.post("/rerank", response_model=TaskStarted, status_code=status.HTTP_202_ACCEPTED)
async def rerank(body: RerankIn, _: Authenticated, session: Session, state: State) -> TaskStarted:
    task = await create_task_run(session, "jobs_match", {"job_ids": body.job_ids})
    await state.arq.enqueue_job(
        "jobs_match", str(task.id), body.job_ids, False, body.locale, _job_id=str(task.id)
    )
    return TaskStarted(task_id=str(task.id))


@router.get("/{job_id}", response_model=JobDetail)
async def get_job(job_id: int, _: Authenticated, session: Session) -> JobDetail:
    job = await _job(session, job_id)
    pid = await _profile_id(session)
    m = (
        await session.scalar(
            select(JobMatch).where(JobMatch.job_id == job.id, JobMatch.resume_id == pid)
        )
        if pid is not None
        else None
    )
    src = SOURCE_BY_NAME.get(job.source)
    return JobDetail.model_validate(
        {
            **_out(job, m),
            "description": job.description,
            "apply_email": job.apply_email,
            "injection_reasons": job.injection_reasons,
            "attribution": src.info.attribution if src else None,
        }
    )


@router.patch("/{job_id}", response_model=JobOut)
async def flag_job(job_id: int, body: JobFlags, _: Authenticated, session: Session) -> JobOut:
    job = await _job(session, job_id)
    if body.saved is not None:
        job.saved = body.saved
    if body.hidden is not None:
        job.hidden = body.hidden
    await session.commit()
    return JobOut.model_validate(_out(job, None))


# ---------------------------------------------------------------- saved searches
class SavedSearchIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    filters: JobFilters
    enabled: bool = True


class SavedSearchOut(BaseModel):
    id: int
    name: str
    filters: JobFilters
    enabled: bool
    last_run_at: datetime | None
    new_match_count: int
    created_at: datetime


def _saved_out(s: SavedSearch) -> SavedSearchOut:
    return SavedSearchOut(
        id=s.id,
        name=s.name,
        filters=JobFilters.model_validate(s.filters or {}),
        enabled=s.enabled,
        last_run_at=s.last_run_at,
        new_match_count=s.new_match_count,
        created_at=s.created_at,
    )


async def _saved(session: Session, search_id: int) -> SavedSearch:
    row = await session.get(SavedSearch, search_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "saved search not found")
    return row


@saved_router.get("", response_model=list[SavedSearchOut])
async def list_saved(_: Authenticated, session: Session) -> list[SavedSearchOut]:
    rows = (await session.scalars(select(SavedSearch).order_by(SavedSearch.id))).all()
    return [_saved_out(r) for r in rows]


@saved_router.post("", response_model=SavedSearchOut, status_code=status.HTTP_201_CREATED)
async def create_saved(body: SavedSearchIn, _: Authenticated, session: Session) -> SavedSearchOut:
    row = SavedSearch(
        name=body.name.strip(),
        filters=body.filters.model_dump(mode="json"),
        enabled=body.enabled,
        schedule_cron="jobs_fetch_hours",
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return _saved_out(row)


@saved_router.put("/{search_id}", response_model=SavedSearchOut)
async def update_saved(
    search_id: int, body: SavedSearchIn, _: Authenticated, session: Session
) -> SavedSearchOut:
    row = await _saved(session, search_id)
    row.name = body.name.strip()
    row.filters = body.filters.model_dump(mode="json")
    row.enabled = body.enabled
    await session.commit()
    return _saved_out(row)


@saved_router.post("/{search_id}/seen", response_model=SavedSearchOut)
async def mark_seen(search_id: int, _: Authenticated, session: Session) -> SavedSearchOut:
    row = await _saved(session, search_id)
    row.new_match_count = 0
    await session.commit()
    return _saved_out(row)


@saved_router.delete("/{search_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_saved(search_id: int, _: Authenticated, session: Session) -> Response:
    row = await _saved(session, search_id)
    await session.delete(row)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
