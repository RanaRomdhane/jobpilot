"""Data shapes shared by every connector."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from jobpilot_core.enums import Region, WorkMode

Seniority = Literal["intern", "junior", "mid", "senior", "lead"]
Contract = Literal["permanent", "fixed_term", "freelance", "internship", "apprenticeship"]


class SourceQuery(BaseModel):
    """What a fetch run asks each source for (each connector uses what its API supports)."""

    keywords: list[str] = Field(default_factory=list)  # search terms, e.g. "machine learning"
    countries: list[str] = Field(default_factory=list)  # ISO alpha-2, lower case
    remote_only: bool = False
    max_age_days: int = 14
    limit: int = 100


class RawJob(BaseModel):
    """A posting as a source returns it, before normalization. All text is untrusted."""

    source: str
    external_id: str
    url: str | None = None
    title: str
    company: str | None = None
    location: str | None = None
    description_html: str = ""
    posted_at: datetime | None = None
    tags: list[str] = Field(default_factory=list)
    contract_hint: str | None = None  # the source's own wording ("CDI", "full_time"...)
    remote_hint: bool | None = None
    salary_min: Decimal | None = None
    salary_max: Decimal | None = None
    salary_currency: str | None = None
    salary_text: str | None = None
    apply_url: str | None = None
    apply_email: str | None = None
    country_hint: str | None = None  # ISO alpha-2 when the source states it


class NormalizedJob(BaseModel):
    """What gets stored (`job_posting`), plus the scan input."""

    source: str
    external_id: str
    url: str | None
    title: str
    company_name: str | None
    location_raw: str | None
    country: str | None
    region: Region | None
    work_mode: WorkMode | None
    domain: str | None
    seniority: Seniority | None
    contract_type: Contract | None
    salary_min: Decimal | None
    salary_max: Decimal | None
    salary_currency: str | None
    language: str | None
    description: str
    hidden_text: list[str] = Field(default_factory=list)  # invisible HTML: scanned, not shown
    posted_at: datetime | None
    dedup_hash: str
    apply_url: str | None
    apply_email: str | None


class SourceInfo(BaseModel):
    """Static description of a connector, shown on the dashboard."""

    name: str
    display_name: str
    homepage: str
    kind: Literal["api", "rss", "html"]
    requires_key: bool = False
    regions: list[Region] = Field(default_factory=list)
    attribution: str | None = None  # what the source's terms ask us to show
