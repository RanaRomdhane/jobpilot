"""Vue d'ensemble: KPIs, funnel, upcoming interviews, recent activity, alerts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from jobpilot_api.auth import Authenticated
from jobpilot_api.deps import Session
from jobpilot_core.enums import ApplicationStatus, SuggestionStatus
from jobpilot_core.models import (
    Application,
    ApplicationEvent,
    EmailMessage,
    GmailSyncState,
    StatusSuggestion,
    TaskRun,
)

router = APIRouter(prefix="/overview", tags=["overview"])

APPLIED = (
    ApplicationStatus.SENT,
    ApplicationStatus.RECRUITER_CONTACT,
    ApplicationStatus.INTERVIEW,
    ApplicationStatus.OFFER,
    ApplicationStatus.REJECTED,
    ApplicationStatus.GHOSTED,
    ApplicationStatus.WITHDRAWN,
)
RESPONDED = (
    ApplicationStatus.RECRUITER_CONTACT,
    ApplicationStatus.INTERVIEW,
    ApplicationStatus.OFFER,
    ApplicationStatus.REJECTED,
)
FUNNEL = (
    ApplicationStatus.SENT,
    ApplicationStatus.RECRUITER_CONTACT,
    ApplicationStatus.INTERVIEW,
    ApplicationStatus.OFFER,
)


class Kpis(BaseModel):
    applications_sent: int
    response_rate: float | None
    interviews: int
    offers: int
    sent_last_7_days: int


class FunnelStep(BaseModel):
    status: ApplicationStatus
    count: int  # reached this step or beyond


class UpcomingInterview(BaseModel):
    application_id: int
    role_title: str
    company: str | None
    interview_at: datetime


class Activity(BaseModel):
    kind: str  # application event type, or "triage"
    at: datetime
    application_id: int | None = None
    title: str
    detail: dict[str, Any] = {}


class Overview(BaseModel):
    kpis: Kpis
    funnel: list[FunnelStep]
    status_counts: dict[str, int]
    upcoming_interviews: list[UpcomingInterview]
    activity: list[Activity]
    inbox: dict[str, int]
    security_alerts: int
    pending_suggestions: int
    last_sync_at: datetime | None


@router.get("", response_model=Overview)
async def overview(_: Authenticated, session: Session) -> Overview:
    now = datetime.now(UTC)
    status_counts = {
        s.value: n
        for s, n in (
            await session.execute(
                select(Application.status, func.count()).group_by(Application.status)
            )
        ).all()
    }
    sent = sum(status_counts.get(s.value, 0) for s in APPLIED)
    responded = sum(status_counts.get(s.value, 0) for s in RESPONDED)
    # funnel: an application "reached" a step if its current status is that step or later
    reached = {
        ApplicationStatus.SENT: sent,
        ApplicationStatus.RECRUITER_CONTACT: responded,
        ApplicationStatus.INTERVIEW: status_counts.get("interview", 0)
        + status_counts.get("offer", 0),
        ApplicationStatus.OFFER: status_counts.get("offer", 0),
    }
    # interviews ever reached also count applications later rejected after an interview
    interviewed = int(
        await session.scalar(
            select(func.count(func.distinct(ApplicationEvent.application_id))).where(
                ApplicationEvent.type == "status_changed",
                ApplicationEvent.payload["to"].astext == ApplicationStatus.INTERVIEW.value,
            )
        )
        or 0
    )
    reached[ApplicationStatus.INTERVIEW] = max(reached[ApplicationStatus.INTERVIEW], interviewed)
    sent_7 = int(
        await session.scalar(
            select(func.count())
            .select_from(Application)
            .where(Application.applied_at >= now - timedelta(days=7))
        )
        or 0
    )
    upcoming = (
        await session.scalars(
            select(Application)
            .options(selectinload(Application.company))
            .where(Application.interview_at >= now - timedelta(hours=2))
            .order_by(Application.interview_at)
            .limit(5)
        )
    ).all()
    events = (
        await session.execute(
            select(ApplicationEvent, Application)
            .join(Application, Application.id == ApplicationEvent.application_id)
            .options(selectinload(Application.company))
            .order_by(ApplicationEvent.occurred_at.desc())
            .limit(8)
        )
    ).all()
    runs = (
        await session.scalars(
            select(TaskRun)
            .where(TaskRun.kind == "triage")
            .order_by(TaskRun.created_at.desc())
            .limit(3)
        )
    ).all()
    activity = [
        Activity(
            kind=e.type,
            at=e.occurred_at,
            application_id=a.id,
            title=f"{a.role_title} · {a.company.name if a.company else '—'}",
            detail=e.payload,
        )
        for e, a in events
    ] + [
        Activity(
            kind="triage",
            at=r.finished_at or r.created_at,
            title=r.message or r.status.value,
            detail={"status": r.status.value, **(r.result or {})},
        )
        for r in runs
    ]
    activity.sort(key=lambda a: a.at, reverse=True)
    inbox = {
        (c.value if c else "unclassified"): n
        for c, n in (
            await session.execute(
                select(EmailMessage.category, func.count()).group_by(EmailMessage.category)
            )
        ).all()
    }
    alerts = int(
        await session.scalar(
            select(func.count()).select_from(EmailMessage).where(EmailMessage.injection_flag)
        )
        or 0
    )
    pending = int(
        await session.scalar(
            select(func.count())
            .select_from(StatusSuggestion)
            .where(StatusSuggestion.status == SuggestionStatus.PENDING)
        )
        or 0
    )
    last_sync = await session.scalar(
        select(
            func.max(
                func.greatest(
                    GmailSyncState.last_full_sync_at, GmailSyncState.last_incremental_sync_at
                )
            )
        )
    )
    return Overview(
        kpis=Kpis(
            applications_sent=sent,
            response_rate=round(responded / sent, 3) if sent else None,
            interviews=reached[ApplicationStatus.INTERVIEW],
            offers=reached[ApplicationStatus.OFFER],
            sent_last_7_days=sent_7,
        ),
        funnel=[FunnelStep(status=s, count=reached[s]) for s in FUNNEL],
        status_counts=status_counts,
        upcoming_interviews=[
            UpcomingInterview(
                application_id=a.id,
                role_title=a.role_title,
                company=a.company.name if a.company else None,
                interview_at=a.interview_at,
            )
            for a in upcoming
            if a.interview_at is not None
        ],
        activity=activity[:10],
        inbox=inbox,
        security_alerts=alerts,
        pending_suggestions=pending,
        last_sync_at=last_sync,
    )
