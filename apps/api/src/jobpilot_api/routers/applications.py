"""Applications pipeline: Kanban/table data, detail drawer, status changes, suggestions."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.orm import selectinload

from jobpilot_api.auth import Authenticated
from jobpilot_api.deps import Session
from jobpilot_api.routers.emails import EmailOut
from jobpilot_core.enums import ApplicationStatus, ApplyChannel, SuggestionStatus
from jobpilot_core.models import (
    Application,
    ApplicationEvent,
    Company,
    EmailMessage,
    StatusSuggestion,
)

router = APIRouter(tags=["applications"])


# ------------------------------------------------------------------ schemas
class CompanyOut(BaseModel):
    id: int
    name: str
    domain: str | None
    website: str | None


class ApplicationOut(BaseModel):
    id: int
    role_title: str
    status: ApplicationStatus
    apply_channel: ApplyChannel
    apply_url: str | None
    applied_at: datetime | None
    interview_at: datetime | None
    source: str | None
    contact_email: str | None
    gmail_thread_id: str | None
    notes: str | None
    created_at: datetime
    updated_at: datetime
    company: CompanyOut | None
    pending_suggestions: int = 0


class EventOut(BaseModel):
    id: int
    type: str
    payload: dict[str, Any]
    occurred_at: datetime
    source_email_id: int | None


class SuggestionOut(BaseModel):
    id: int
    application_id: int
    email_id: int | None
    from_status: ApplicationStatus
    to_status: ApplicationStatus
    confidence: float
    interview_at: datetime | None
    rationale: str | None
    status: SuggestionStatus
    created_at: datetime
    application_role: str | None = None
    company_name: str | None = None
    email_subject: str | None = None


class ApplicationDetail(ApplicationOut):
    events: list[EventOut]
    emails: list[EmailOut]
    suggestions: list[SuggestionOut]


def _domain(value: str | None) -> str | None:
    if not value:
        return None
    v = value.strip().lower().removeprefix("https://").removeprefix("http://")
    v = v.removeprefix("www.").split("/")[0]
    return v or None


class ApplicationCreate(BaseModel):
    company_name: str = Field(min_length=1, max_length=300)
    company_domain: str | None = Field(default=None, max_length=255)
    role_title: str = Field(min_length=1, max_length=300)
    status: ApplicationStatus = ApplicationStatus.SENT
    apply_channel: ApplyChannel = ApplyChannel.EMAIL
    apply_url: str | None = Field(default=None, max_length=2048)
    applied_at: datetime | None = None
    interview_at: datetime | None = None
    source: str | None = Field(default=None, max_length=64)
    contact_email: EmailStr | None = None
    notes: str | None = Field(default=None, max_length=5000)

    @field_validator("company_domain")
    @classmethod
    def _clean_domain(cls, v: str | None) -> str | None:
        return _domain(v)


class ApplicationUpdate(BaseModel):
    role_title: str | None = Field(default=None, min_length=1, max_length=300)
    apply_url: str | None = Field(default=None, max_length=2048)
    applied_at: datetime | None = None
    interview_at: datetime | None = None
    source: str | None = Field(default=None, max_length=64)
    contact_email: EmailStr | None = None
    notes: str | None = Field(default=None, max_length=5000)
    company_domain: str | None = Field(default=None, max_length=255)

    @field_validator("company_domain")
    @classmethod
    def _clean_domain(cls, v: str | None) -> str | None:
        return _domain(v)


class StatusChange(BaseModel):
    status: ApplicationStatus
    note: str | None = Field(default=None, max_length=500)


class LinkEmail(BaseModel):
    email_id: int


# ------------------------------------------------------------------ helpers
def _out(app: Application, pending: int = 0) -> ApplicationOut:
    data = {
        f: getattr(app, f)
        for f in ApplicationOut.model_fields
        if f not in ("company", "pending_suggestions")
    }
    company = CompanyOut.model_validate(app.company, from_attributes=True) if app.company else None
    return ApplicationOut(**data, company=company, pending_suggestions=pending)


async def _load(session: Session, app_id: int) -> Application:
    app = (
        await session.scalars(
            select(Application)
            .options(selectinload(Application.company))
            .where(Application.id == app_id)
        )
    ).first()
    if app is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "application not found")
    return app


async def _company(session: Session, name: str, domain: str | None) -> Company:
    if domain:
        existing = (await session.scalars(select(Company).where(Company.domain == domain))).first()
        if existing:
            return existing
    existing = (
        await session.scalars(select(Company).where(func.lower(Company.name) == name.lower()))
    ).first()
    if existing:
        if domain and not existing.domain:
            existing.domain = domain
        return existing
    company = Company(name=name.strip(), domain=domain)
    session.add(company)
    await session.flush()
    return company


def _suggestion_out(
    s: StatusSuggestion, app: Application | None = None, email: EmailMessage | None = None
) -> SuggestionOut:
    return SuggestionOut(
        **{
            f: getattr(s, f)
            for f in SuggestionOut.model_fields
            if f not in ("application_role", "company_name", "email_subject")
        },
        application_role=app.role_title if app else None,
        company_name=app.company.name if app and app.company else None,
        email_subject=email.subject if email else None,
    )


# ------------------------------------------------------------------ applications
@router.get("/applications", response_model=list[ApplicationOut])
async def list_applications(
    _: Authenticated,
    session: Session,
    status_: Annotated[list[ApplicationStatus] | None, Query(alias="status")] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
) -> list[ApplicationOut]:
    stmt = select(Application).options(selectinload(Application.company)).outerjoin(Company)
    if status_:
        stmt = stmt.where(Application.status.in_(status_))
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Application.role_title.ilike(like), Company.name.ilike(like)))
    apps = (await session.scalars(stmt.order_by(Application.updated_at.desc()))).all()
    pending = dict(
        (
            await session.execute(
                select(StatusSuggestion.application_id, func.count())
                .where(StatusSuggestion.status == SuggestionStatus.PENDING)
                .group_by(StatusSuggestion.application_id)
            )
        ).all()
    )
    return [_out(a, pending.get(a.id, 0)) for a in apps]


@router.post("/applications", response_model=ApplicationOut, status_code=status.HTTP_201_CREATED)
async def create_application(
    body: ApplicationCreate, _: Authenticated, session: Session
) -> ApplicationOut:
    company = await _company(session, body.company_name, body.company_domain)
    applied_at = body.applied_at
    if applied_at is None and body.status not in (
        ApplicationStatus.DRAFT,
        ApplicationStatus.READY_FOR_REVIEW,
    ):
        applied_at = datetime.now(UTC)
    app = Application(
        company_id=company.id,
        role_title=body.role_title.strip(),
        status=body.status,
        apply_channel=body.apply_channel,
        apply_url=body.apply_url,
        applied_at=applied_at,
        interview_at=body.interview_at,
        source=body.source or "manual",
        contact_email=body.contact_email,
        notes=body.notes,
    )
    session.add(app)
    await session.flush()
    session.add(
        ApplicationEvent(
            application_id=app.id,
            type="created",
            payload={"status": body.status.value, "by": "user"},
        )
    )
    await session.commit()
    return _out(await _load(session, app.id))


@router.get("/applications/{app_id}", response_model=ApplicationDetail)
async def get_application(app_id: int, _: Authenticated, session: Session) -> ApplicationDetail:
    app = await _load(session, app_id)
    events = (
        await session.scalars(
            select(ApplicationEvent)
            .where(ApplicationEvent.application_id == app_id)
            .order_by(ApplicationEvent.occurred_at.desc(), ApplicationEvent.id.desc())
        )
    ).all()
    email_ids = {e.source_email_id for e in events if e.source_email_id}
    conds: list[ColumnElement[bool]] = [EmailMessage.id.in_(email_ids)] if email_ids else []
    if app.gmail_thread_id:
        conds.append(EmailMessage.thread_id == app.gmail_thread_id)
    emails = (
        (
            await session.scalars(
                select(EmailMessage).where(or_(*conds)).order_by(EmailMessage.received_at.desc())
            )
        ).all()
        if conds
        else []
    )
    suggestions = (
        await session.scalars(
            select(StatusSuggestion)
            .where(StatusSuggestion.application_id == app_id)
            .order_by(StatusSuggestion.created_at.desc())
        )
    ).all()
    base = _out(app, sum(s.status == SuggestionStatus.PENDING for s in suggestions))
    return ApplicationDetail(
        **base.model_dump(),
        events=[EventOut.model_validate(e, from_attributes=True) for e in events],
        emails=[EmailOut.model_validate(e, from_attributes=True) for e in emails],
        suggestions=[_suggestion_out(s) for s in suggestions],
    )


@router.patch("/applications/{app_id}", response_model=ApplicationOut)
async def update_application(
    app_id: int, body: ApplicationUpdate, _: Authenticated, session: Session
) -> ApplicationOut:
    app = await _load(session, app_id)
    changes = body.model_dump(exclude_unset=True)
    domain = changes.pop("company_domain", None)
    for field, value in changes.items():
        setattr(app, field, value)
    if domain and app.company and app.company.domain != domain:
        app.company.domain = domain
    if changes or domain:
        session.add(
            ApplicationEvent(
                application_id=app.id,
                type="edited",
                payload={"fields": sorted([*changes, *(["company_domain"] if domain else [])])},
            )
        )
    await session.commit()
    return _out(await _load(session, app_id))


@router.patch("/applications/{app_id}/status", response_model=ApplicationOut)
async def change_status(
    app_id: int, body: StatusChange, _: Authenticated, session: Session
) -> ApplicationOut:
    """Kanban drag & drop: change the status and record an event."""
    app = await _load(session, app_id)
    if app.status != body.status:
        session.add(
            ApplicationEvent(
                application_id=app.id,
                type="status_changed",
                payload={
                    "from": app.status.value,
                    "to": body.status.value,
                    "by": "user",
                    **({"note": body.note} if body.note else {}),
                },
            )
        )
        if body.status == ApplicationStatus.SENT and app.applied_at is None:
            app.applied_at = datetime.now(UTC)
        app.status = body.status
        await session.commit()
    return _out(await _load(session, app_id))


@router.delete("/applications/{app_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_application(app_id: int, _: Authenticated, session: Session) -> None:
    app = await _load(session, app_id)
    await session.delete(app)
    await session.commit()


@router.post("/applications/{app_id}/emails", response_model=ApplicationOut)
async def link_email(
    app_id: int, body: LinkEmail, _: Authenticated, session: Session
) -> ApplicationOut:
    """Attach an email (and its thread) to an application; future replies link automatically."""
    app = await _load(session, app_id)
    email = await session.get(EmailMessage, body.email_id)
    if email is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "email not found")
    if app.gmail_thread_id is None:
        app.gmail_thread_id = email.thread_id
    session.add(
        ApplicationEvent(
            application_id=app.id,
            type="email_linked",
            source_email_id=email.id,
            payload={
                "by": "user",
                "subject": (email.subject or "")[:200],
                "category": email.category.value if email.category else None,
            },
        )
    )
    await session.commit()
    return _out(await _load(session, app_id))


# ------------------------------------------------------------------ suggestions
@router.get("/suggestions", response_model=list[SuggestionOut])
async def list_suggestions(
    _: Authenticated,
    session: Session,
    status_: Annotated[SuggestionStatus, Query(alias="status")] = SuggestionStatus.PENDING,
) -> list[SuggestionOut]:
    rows = (
        await session.scalars(
            select(StatusSuggestion)
            .where(StatusSuggestion.status == status_)
            .order_by(StatusSuggestion.created_at.desc())
            .limit(100)
        )
    ).all()
    out = []
    for s in rows:
        app = await _load(session, s.application_id)
        email = await session.get(EmailMessage, s.email_id) if s.email_id else None
        out.append(_suggestion_out(s, app, email))
    return out


async def _resolve(session: Session, suggestion_id: int, accept: bool) -> SuggestionOut:
    s = await session.get(StatusSuggestion, suggestion_id)
    if s is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "suggestion not found")
    if s.status != SuggestionStatus.PENDING:
        raise HTTPException(status.HTTP_409_CONFLICT, "suggestion already resolved")
    app = await _load(session, s.application_id)
    s.status = SuggestionStatus.ACCEPTED if accept else SuggestionStatus.DISMISSED
    s.resolved_at = datetime.now(UTC)
    if accept:
        session.add(
            ApplicationEvent(
                application_id=app.id,
                type="status_changed",
                source_email_id=s.email_id,
                payload={
                    "from": app.status.value,
                    "to": s.to_status.value,
                    "by": "user",
                    "suggestion_id": s.id,
                },
            )
        )
        app.status = s.to_status
        if s.interview_at is not None:
            app.interview_at = s.interview_at
    await session.commit()
    return _suggestion_out(s, app)


@router.post("/suggestions/{suggestion_id}/accept", response_model=SuggestionOut)
async def accept_suggestion(
    suggestion_id: int, _: Authenticated, session: Session
) -> SuggestionOut:
    return await _resolve(session, suggestion_id, accept=True)


@router.post("/suggestions/{suggestion_id}/dismiss", response_model=SuggestionOut)
async def dismiss_suggestion(
    suggestion_id: int, _: Authenticated, session: Session
) -> SuggestionOut:
    return await _resolve(session, suggestion_id, accept=False)
