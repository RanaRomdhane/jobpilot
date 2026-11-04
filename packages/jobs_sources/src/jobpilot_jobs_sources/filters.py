"""Search filters, shared by the API (job list), saved searches (worker) and preferences.

Unknown values are kept by default for seniority, contract, work mode and location: sources
often omit them, and hiding every job a source did not label would hide most jobs. A
"worldwide" job matches any region filter that includes Worldwide.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import ColumnElement, Select, and_, func, not_, or_

from jobpilot_core.enums import Region, WorkMode
from jobpilot_core.models import JobMatch, JobPosting
from jobpilot_jobs_sources.models import Contract, Seniority


class JobFilters(BaseModel):
    regions: list[Region] = Field(default_factory=list)
    countries: list[str] = Field(default_factory=list, max_length=30)
    work_modes: list[WorkMode] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list, max_length=10)
    seniority: list[Seniority] = Field(default_factory=list)
    contracts: list[Contract] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list, max_length=5)
    sources: list[str] = Field(default_factory=list, max_length=10)
    posted_within_days: int | None = Field(default=None, ge=1, le=180)
    salary_min: Decimal | None = Field(default=None, ge=0)
    include: list[str] = Field(default_factory=list, max_length=10)  # any of these words
    exclude: list[str] = Field(default_factory=list, max_length=10)  # none of these words
    min_score: int | None = Field(default=None, ge=0, le=100)
    include_unknown: bool = True


DEFAULT_PREFERENCES = JobFilters(
    regions=[Region.TUNISIA, Region.WORLDWIDE, Region.EUROPE, Region.MIDDLE_EAST],
    domains=["ai_ml", "software_backend", "data_eng_bi", "fullstack_frontend"],
    seniority=["junior"],
    contracts=["permanent", "fixed_term", "freelance"],
    posted_within_days=30,
)


def _like(term: str) -> str:
    return "%" + term.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_") + "%"


def _in_or_unknown(column: Any, values: list[Any], keep_unknown: bool) -> ColumnElement[bool]:
    cond = column.in_(values)
    return or_(cond, column.is_(None)) if keep_unknown else cond


def conditions(f: JobFilters, *, now: datetime | None = None) -> list[ColumnElement[bool]]:
    j = JobPosting
    out: list[ColumnElement[bool]] = []
    unk = f.include_unknown
    if f.regions:
        out.append(_in_or_unknown(j.region, list(f.regions), unk))
    if f.countries:
        out.append(_in_or_unknown(j.country, [c.lower() for c in f.countries], unk))
    if f.work_modes:
        out.append(_in_or_unknown(j.work_mode, list(f.work_modes), unk))
    if f.domains:
        out.append(j.domain.in_(f.domains))
    if f.seniority:
        out.append(_in_or_unknown(j.seniority, list(f.seniority), unk))
    if f.contracts:
        out.append(_in_or_unknown(j.contract_type, list(f.contracts), unk))
    if f.languages:
        out.append(_in_or_unknown(j.language, f.languages, unk))
    if f.sources:
        out.append(j.source.in_(f.sources))
    if f.posted_within_days:
        since = (now or datetime.now(UTC)) - timedelta(days=f.posted_within_days)
        out.append(func.coalesce(j.posted_at, j.scraped_at) >= since)
    if f.salary_min is not None:
        out.append(or_(j.salary_max >= f.salary_min, j.salary_min >= f.salary_min))
    text = func.concat(j.title, " ", func.coalesce(j.company_name, ""), " ", j.description)
    if f.include:
        out.append(or_(*[text.ilike(_like(t)) for t in f.include if t.strip()]))
    for t in f.exclude:
        if t.strip():
            out.append(not_(text.ilike(_like(t))))
    if f.min_score is not None:
        out.append(JobMatch.score >= f.min_score)
    return out


def apply(stmt: Select[Any], f: JobFilters, *, now: datetime | None = None) -> Select[Any]:
    conds = conditions(f, now=now)
    return stmt.where(and_(*conds)) if conds else stmt
