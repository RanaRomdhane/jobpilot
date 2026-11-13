"""The checks every send passes twice: when you approve it, and again in the sender service.

Deterministic and shared by the API and the worker (ADR-0003): the daily cap, the duplicate guard
(same company and role within 30 days) and the recipient.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, tzinfo
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select

from jobpilot_core.enums import ApplicationStatus, OutgoingStatus
from jobpilot_core.models import Application, JobPosting, OutgoingEmail, UserProfile
from jobpilot_core.outgoing import DUPLICATE_WINDOW, same_role, valid_address

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

NOT_APPLIED = frozenset(
    {ApplicationStatus.DRAFT, ApplicationStatus.READY_FOR_REVIEW, ApplicationStatus.WITHDRAWN}
)


def start_of_day(timezone: str, now: datetime | None = None) -> datetime:
    """Midnight today in the user's time zone, as an aware datetime (UTC if the zone is unknown)."""
    zone: tzinfo
    try:
        zone = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        zone = UTC
    local = (now or datetime.now(UTC)).astimezone(zone)
    return local.replace(hour=0, minute=0, second=0, microsecond=0)


async def profile(session: AsyncSession) -> UserProfile:
    row = (await session.scalars(select(UserProfile).limit(1))).first()
    if row is None:
        row = UserProfile()
        session.add(row)
        await session.flush()
    return row


async def sent_today(session: AsyncSession, timezone: str, now: datetime | None = None) -> int:
    return int(
        await session.scalar(
            select(func.count(OutgoingEmail.id)).where(
                OutgoingEmail.status == OutgoingStatus.SENT,
                OutgoingEmail.sent_at >= start_of_day(timezone, now),
            )
        )
        or 0
    )


async def _company_key(session: AsyncSession, app: Application) -> tuple[int | None, str]:
    job = await session.get(JobPosting, app.job_id) if app.job_id else None
    name = (job.company_name if job else None) or ""
    return app.company_id, name.casefold().strip()


async def duplicates(
    session: AsyncSession, app: Application, now: datetime | None = None
) -> list[int]:
    """Other applications already sent to the same company for the same role within 30 days."""
    since = (now or datetime.now(UTC)) - DUPLICATE_WINDOW
    company_id, company_name = await _company_key(session, app)
    if company_id is None and not company_name:
        return []
    rows = (
        await session.execute(
            select(Application, JobPosting.company_name)
            .outerjoin(JobPosting, JobPosting.id == Application.job_id)
            .where(
                Application.id != app.id,
                Application.status.not_in(NOT_APPLIED),
                Application.applied_at >= since,
            )
        )
    ).all()
    out: list[int] = []
    for other, other_company in rows:
        same_company = (company_id is not None and other.company_id == company_id) or (
            bool(company_name) and (other_company or "").casefold().strip() == company_name
        )
        if same_company and same_role(other.role_title, app.role_title):
            out.append(other.id)
    return out


@dataclass(slots=True)
class SendCheck:
    """Why a send may not go ahead (`blocking`), and what the reviewer should know (`warnings`)."""

    blocking: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    duplicate_ids: list[int] = field(default_factory=list)
    sent_today: int = 0
    cap: int = 0

    @property
    def ok(self) -> bool:
        return not self.blocking


async def check_send(
    session: AsyncSession,
    email: OutgoingEmail,
    app: Application,
    *,
    override_duplicate: bool,
    confirm_flagged: bool,
    now: datetime | None = None,
) -> SendCheck:
    """Everything that must hold before an email may be sent; the codes are translated by the UI."""
    user = await profile(session)
    result = SendCheck(cap=user.daily_send_cap)
    if not user.sending_enabled:
        result.blocking.append("sending_disabled")
    if not valid_address(email.to_address):
        result.blocking.append("invalid_recipient")
    if any(not valid_address(cc) for cc in email.cc or []):
        result.blocking.append("invalid_cc")
    if not email.attachments:
        result.blocking.append("no_attachments")
    if not email.subject.strip() or not email.body.strip():
        result.blocking.append("empty_email")
    result.sent_today = await sent_today(session, user.timezone, now)
    if result.sent_today >= user.daily_send_cap:
        result.blocking.append("daily_cap")
    result.duplicate_ids = await duplicates(session, app, now)
    if result.duplicate_ids:
        (result.warnings if override_duplicate else result.blocking).append("duplicate")
    job = await session.get(JobPosting, app.job_id) if app.job_id else None
    if job is not None and job.injection_flag:
        (result.warnings if confirm_flagged else result.blocking).append("flagged_posting")
    if job is not None and job.apply_email and email.to_address != job.apply_email.lower():
        result.warnings.append("recipient_changed")
    return result
