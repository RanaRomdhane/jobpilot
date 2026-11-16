"""Link classified emails to tracked applications and propose / apply status changes.

Deterministic service (no LLM): matching is by Gmail thread first, then by sender domain.
A change is applied automatically only when the classification is confident and the email is not
flagged as suspicious; otherwise it becomes a suggestion the user confirms in the dashboard.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from jobpilot_core.enums import ApplicationStatus, EmailCategory, SuggestionStatus
from jobpilot_core.models import Application, ApplicationEvent, EmailMessage, StatusSuggestion

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.ext.asyncio import AsyncSession

# Webmail domains say nothing about the employer.
GENERIC_DOMAINS = frozenset(
    {
        "gmail.com",
        "googlemail.com",
        "outlook.com",
        "hotmail.com",
        "hotmail.fr",
        "live.com",
        "yahoo.com",
        "yahoo.fr",
        "icloud.com",
        "proton.me",
        "protonmail.com",
        "gmx.com",
        "gmx.fr",
        "orange.fr",
        "topnet.tn",
        "laposte.net",
    }
)

TARGET_STATUS: dict[EmailCategory, ApplicationStatus] = {
    EmailCategory.RECRUTEUR: ApplicationStatus.RECRUITER_CONTACT,
    EmailCategory.ENTRETIEN: ApplicationStatus.INTERVIEW,
    EmailCategory.REFUS: ApplicationStatus.REJECTED,
    EmailCategory.OFFRE: ApplicationStatus.OFFER,
}

# Pipeline order; a "recruiter contact" never moves an application backwards.
RANK = {
    ApplicationStatus.DRAFT: 0,
    ApplicationStatus.READY_FOR_REVIEW: 1,
    ApplicationStatus.SENT: 2,
    ApplicationStatus.GHOSTED: 2,
    ApplicationStatus.RECRUITER_CONTACT: 3,
    ApplicationStatus.INTERVIEW: 4,
    ApplicationStatus.OFFER: 5,
    ApplicationStatus.REJECTED: 5,
    ApplicationStatus.WITHDRAWN: 6,
}
CLOSED = frozenset({ApplicationStatus.WITHDRAWN})


@dataclass(slots=True)
class LinkResult:
    linked: int = 0
    auto_applied: int = 0
    suggested: int = 0


def sender_domain(address: str | None) -> str | None:
    if not address or "@" not in address:
        return None
    domain = address.rsplit("@", 1)[1].lower()
    return None if domain in GENERIC_DOMAINS else domain


def should_change(current: ApplicationStatus, target: ApplicationStatus) -> bool:
    if current in CLOSED or current == target:
        return False
    if target == ApplicationStatus.INTERVIEW and current == ApplicationStatus.INTERVIEW:
        return False
    # interviews after a rejection/offer, or a new round, still deserve a look; the user decides
    return RANK[target] >= RANK[current] or target == ApplicationStatus.INTERVIEW


async def find_application(session: AsyncSession, email: EmailMessage) -> Application | None:
    by_thread = (
        await session.scalars(
            select(Application).where(Application.gmail_thread_id == email.thread_id).limit(1)
        )
    ).first()
    if by_thread is not None:
        return by_thread
    domain = sender_domain(email.from_address)
    if domain is None:
        return None
    # the most recent active application with that company domain
    candidates = (
        await session.scalars(
            select(Application)
            .options(selectinload(Application.company))
            .where(Application.status.not_in(CLOSED))
            .order_by(Application.applied_at.desc().nulls_last())
        )
    ).all()
    for app in candidates:
        company_domain = app.company.domain if app.company else None
        if company_domain and (domain == company_domain or domain.endswith("." + company_domain)):
            return app
    return None


async def link_batch(
    session: AsyncSession, batch_id: uuid.UUID, *, auto_apply_confidence: float
) -> LinkResult:
    result = LinkResult()
    # a reply in the thread of an application email you sent is linked whatever its category
    sent_threads = select(Application.gmail_thread_id).where(
        Application.gmail_thread_id.is_not(None)
    )
    emails = (
        await session.scalars(
            select(EmailMessage).where(
                EmailMessage.batch_id == batch_id,
                EmailMessage.category.in_(list(TARGET_STATUS))
                | EmailMessage.thread_id.in_(sent_threads),
            )
        )
    ).all()
    now = datetime.now(UTC)
    for email in emails:
        assert email.category is not None
        app = await find_application(session, email)
        if app is None:
            continue
        if email.category not in TARGET_STATUS:
            # an "Autre" or "Newsletter" reply: recorded on the timeline, no status change
            if app.gmail_thread_id != email.thread_id:
                continue
            result.linked += 1
            session.add(
                ApplicationEvent(
                    application_id=app.id,
                    type="email_received",
                    source_email_id=email.id,
                    payload={
                        "category": email.category.value,
                        "confidence": email.confidence,
                        "suspicious": email.injection_flag,
                        "subject": (email.subject or "")[:200],
                    },
                )
            )
            continue
        result.linked += 1
        session.add(
            ApplicationEvent(
                application_id=app.id,
                type="email_received",
                source_email_id=email.id,
                payload={
                    "category": email.category.value,
                    "confidence": email.confidence,
                    "suspicious": email.injection_flag,
                    "subject": (email.subject or "")[:200],
                },
            )
        )
        target = TARGET_STATUS[email.category]
        if not should_change(app.status, target):
            continue
        interview_at = _parse_dt(email.extracted.get("interview_at"))
        auto = (
            not email.injection_flag
            and (email.confidence or 0.0) >= auto_apply_confidence
            and RANK[target] >= RANK[app.status]
        )
        suggestion = StatusSuggestion(
            application_id=app.id,
            email_id=email.id,
            from_status=app.status,
            to_status=target,
            confidence=email.confidence or 0.0,
            interview_at=interview_at,
            rationale=email.classification_reason,
            status=SuggestionStatus.AUTO_APPLIED if auto else SuggestionStatus.PENDING,
            resolved_at=now if auto else None,
        )
        session.add(suggestion)
        if auto:
            session.add(
                ApplicationEvent(
                    application_id=app.id,
                    type="status_changed",
                    source_email_id=email.id,
                    payload={
                        "from": app.status.value,
                        "to": target.value,
                        "by": "auto",
                        "confidence": email.confidence,
                    },
                )
            )
            app.status = target
            if interview_at is not None:
                app.interview_at = interview_at
            if app.gmail_thread_id is None:
                app.gmail_thread_id = email.thread_id
            result.auto_applied += 1
        else:
            result.suggested += 1
    await session.commit()
    return result


def _parse_dt(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
