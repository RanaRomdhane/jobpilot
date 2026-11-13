"""Revue de candidature: the application email, its review, Approve & Send.

The API never sends. Approve & Send checks everything, stores the hash of a one-time token and
hands the token to the worker's sender service, which checks it again and consumes it (ADR-0003).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from jobpilot_api.auth import Authenticated
from jobpilot_api.deps import Session, State
from jobpilot_core.enums import (
    ApplicationStatus,
    ApplyChannel,
    CompileStatus,
    DocumentKind,
    OAuthScopeSet,
    OutgoingStatus,
)
from jobpilot_core.models import (
    Application,
    ApplicationEvent,
    GeneratedDocument,
    JobMatch,
    JobPosting,
    OAuthCredential,
    OutgoingEmail,
    Resume,
)
from jobpilot_core.outgoing import (
    approval_expiry,
    attachment_name,
    compose,
    default_signature,
    header_text,
    new_approval_token,
    valid_address,
)
from jobpilot_core.resume import ResumeProfile
from jobpilot_core.send_rules import check_send, profile
from jobpilot_core.tasks import create_task_run
from jobpilot_resume.tailoring import all_texts

router = APIRouter(prefix="/outgoing", tags=["outgoing"])

LOW_MATCH = 55.0
EDITABLE = frozenset({OutgoingStatus.DRAFT, OutgoingStatus.FAILED})


# ------------------------------------------------------------------ schemas
class QueueItem(BaseModel):
    application_id: int
    job_id: int | None
    role_title: str
    company: str | None
    status: ApplicationStatus
    email_status: OutgoingStatus | None
    updated_at: datetime


class OutgoingAttachment(BaseModel):
    document_id: int
    kind: DocumentKind
    version: int
    name: str


class OutgoingEmailOut(BaseModel):
    id: int
    to_address: str | None
    cc: list[str]
    subject: str
    body: str
    status: OutgoingStatus
    error: str | None
    approved_at: datetime | None
    sent_at: datetime | None
    attachments: list[OutgoingAttachment]


class ReviewDocument(BaseModel):
    id: int
    version: int
    findings: int
    page_count: int | None
    ats_before: float | None
    ats_after: float | None


class TailoringChange(BaseModel):
    where: str  # "headline" | "summary" | a master bullet id
    master: str
    tailored: str


class ReviewJob(BaseModel):
    id: int
    title: str
    company: str | None
    apply_email: str | None
    url: str | None
    injection_flag: bool
    match_score: float | None


class SendChecks(BaseModel):
    blocking: list[str]
    warnings: list[str]
    duplicate_ids: list[int]
    sent_today: int
    cap: int


class ReviewOut(BaseModel):
    application_id: int
    role_title: str
    status: ApplicationStatus
    apply_channel: ApplyChannel
    apply_url: str | None
    job: ReviewJob | None
    cv: ReviewDocument | None
    cover_letter: ReviewDocument | None
    changes: list[TailoringChange]
    email: OutgoingEmailOut
    checks: SendChecks
    sending_enabled: bool
    send_account: str | None


class EmailEdit(BaseModel):
    to_address: str | None = Field(default=None, max_length=254)
    cc: list[str] = Field(default_factory=list, max_length=5)
    subject: str = Field(max_length=200)
    body: str = Field(max_length=10_000)


class ApproveIn(BaseModel):
    override_duplicate: bool = False  # send although the same role was applied to recently
    confirm_flagged: bool = False  # send although the posting was flagged for prompt injection


class TaskStarted(BaseModel):
    task_id: str


# ------------------------------------------------------------------ helpers
async def _latest(session: Session, app_id: int, kind: DocumentKind) -> GeneratedDocument | None:
    return await session.scalar(
        select(GeneratedDocument)
        .where(
            GeneratedDocument.application_id == app_id,
            GeneratedDocument.kind == kind,
            GeneratedDocument.compile_status == CompileStatus.SUCCESS,
            GeneratedDocument.pdf_path.is_not(None),
        )
        .order_by(GeneratedDocument.version.desc())
        .limit(1)
    )


def _summary(doc: GeneratedDocument | None) -> ReviewDocument | None:
    if doc is None:
        return None
    c = doc.content or {}
    ats = c.get("ats") or {}
    return ReviewDocument(
        id=doc.id,
        version=doc.version,
        findings=len(c.get("findings") or []),
        page_count=doc.page_count,
        ats_before=ats.get("before"),
        ats_after=ats.get("after"),
    )


async def _master(session: Session) -> ResumeProfile | None:
    row = await session.scalar(select(Resume).where(Resume.is_master))
    return ResumeProfile.model_validate(row.parsed) if row and row.parsed else None


def signature_for(prefs: dict[str, Any], master: ResumeProfile | None) -> str:
    custom = str(prefs.get("email_signature") or "").strip()
    if custom:
        return custom
    if master is None:
        return ""
    linkedin = next((lk.url for lk in master.links if lk.kind == "linkedin"), None)
    return default_signature(master.contact.full_name, master.contact.phone, linkedin)


def changes(master: ResumeProfile | None, cv: GeneratedDocument | None) -> list[TailoringChange]:
    """What the tailoring changed: headline, summary and every rewritten bullet, side by side."""
    if master is None or cv is None:
        return []
    content = cv.content or {}
    tailored = content.get("profile") or {}
    out: list[TailoringChange] = []
    for where, before in (("headline", master.headline), ("summary", master.summary)):
        after = tailored.get(where)
        if after and after != before:
            out.append(TailoringChange(where=where, master=before or "", tailored=after))
    texts = all_texts(master)
    plan = content.get("plan") or {}
    for group in ("experiences", "projects", "extra_sections"):
        for item in plan.get(group) or []:
            for bullet in item.get("bullets") or []:
                source = bullet.get("source")
                before = texts.get(source or "")
                if before is not None and bullet.get("text") != before:
                    out.append(
                        TailoringChange(
                            where=source, master=before, tailored=bullet.get("text", "")
                        )
                    )
    return out


def _attachments(
    docs: list[GeneratedDocument], full_name: str | None, company: str | None
) -> list[dict[str, Any]]:
    return [
        {
            "document_id": d.id,
            "kind": d.kind.value,
            "version": d.version,
            "name": attachment_name(d.kind, full_name, company),
            "path": d.pdf_path,
        }
        for d in docs
    ]


async def _draft(
    session: Session,
    app: Application,
    job: JobPosting | None,
    docs: list[GeneratedDocument],
    master: ResumeProfile | None,
) -> OutgoingEmail:
    """The email being prepared for this application, created from the template the first time.

    An unsent draft always carries the latest documents; your edits to its text are kept.
    """
    email = await session.scalar(
        select(OutgoingEmail)
        .where(OutgoingEmail.application_id == app.id)
        .order_by(OutgoingEmail.id.desc())
        .limit(1)
    )
    company = job.company_name if job else None
    full_name = master.contact.full_name if master else None
    if email is None:
        user = await profile(session)
        cv = next((d for d in docs if d.kind is DocumentKind.CV), None)
        cv_lang = ((cv.content or {}) if cv else {}).get("language")
        lang = str(cv_lang or (job.language if job else None) or "fr")
        draft = compose(
            lang,
            job.title if job else app.role_title,
            company,
            signature_for(user.preferences, master),
        )
        to = (job.apply_email if job else None) or app.contact_email
        email = OutgoingEmail(
            application_id=app.id,
            to_address=to.lower() if to and valid_address(to.lower()) else None,
            cc=[],
            subject=draft.subject,
            body=draft.body,
            attachments=_attachments(docs, full_name, company),
            status=OutgoingStatus.DRAFT,
        )
        session.add(email)
        await session.flush()
    elif email.status in EDITABLE:
        email.attachments = _attachments(docs, full_name, company)
    return email


def _email_out(email: OutgoingEmail) -> OutgoingEmailOut:
    return OutgoingEmailOut(
        id=email.id,
        to_address=email.to_address,
        cc=list(email.cc or []),
        subject=email.subject,
        body=email.body,
        status=email.status,
        error=email.error,
        approved_at=email.approved_at,
        sent_at=email.sent_at,
        attachments=[
            OutgoingAttachment(
                document_id=a["document_id"], kind=a["kind"], version=a["version"], name=a["name"]
            )
            for a in email.attachments or []
        ],
    )


async def _send_account(session: Session) -> str | None:
    row = await session.scalar(
        select(OAuthCredential).where(OAuthCredential.scope_set == OAuthScopeSet.SEND)
    )
    return row.account_email if row else None


# ------------------------------------------------------------------ routes
@router.get("/queue", response_model=list[QueueItem])
async def queue(_: Authenticated, session: Session) -> list[QueueItem]:
    """Applications with generated documents, most recently changed first."""
    with_docs = select(GeneratedDocument.application_id).distinct()
    rows = (
        await session.execute(
            select(Application, JobPosting.company_name)
            .outerjoin(JobPosting, JobPosting.id == Application.job_id)
            .where(Application.id.in_(with_docs))
            .order_by(Application.updated_at.desc())
            .limit(100)
        )
    ).all()
    latest_status = dict(
        (
            await session.execute(
                select(OutgoingEmail.application_id, OutgoingEmail.status)
                .distinct(OutgoingEmail.application_id)
                .order_by(OutgoingEmail.application_id, OutgoingEmail.id.desc())
            )
        ).tuples()
    )
    return [
        QueueItem(
            application_id=app.id,
            job_id=app.job_id,
            role_title=app.role_title,
            company=company,
            status=app.status,
            email_status=latest_status.get(app.id),
            updated_at=app.updated_at,
        )
        for app, company in rows
    ]


@router.get("/review/{application_id}", response_model=ReviewOut)
async def review(application_id: int, _: Authenticated, session: Session) -> ReviewOut:
    app = await session.get(Application, application_id)
    if app is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "application not found")
    job = await session.get(JobPosting, app.job_id) if app.job_id else None
    cv = await _latest(session, app.id, DocumentKind.CV)
    letter = await _latest(session, app.id, DocumentKind.COVER_LETTER)
    master = await _master(session)
    docs = [d for d in (cv, letter) if d is not None]
    email = await _draft(session, app, job, docs, master)
    check = await check_send(session, email, app, override_duplicate=False, confirm_flagged=False)
    account = await _send_account(session)
    if account is None:
        check.blocking.append("gmail_send_not_connected")
    if cv is None or letter is None:
        check.blocking.append("missing_document")
    match = (
        await session.scalar(select(func.max(JobMatch.score)).where(JobMatch.job_id == job.id))
        if job
        else None
    )
    if match is not None and match < LOW_MATCH:
        check.warnings.append("low_match")
    if any(len((d.content or {}).get("findings") or []) for d in docs):
        check.warnings.append("guardrail_findings")
    user = await profile(session)
    await session.commit()
    return ReviewOut(
        application_id=app.id,
        role_title=app.role_title,
        status=app.status,
        apply_channel=app.apply_channel,
        apply_url=app.apply_url,
        job=ReviewJob(
            id=job.id,
            title=job.title,
            company=job.company_name,
            apply_email=job.apply_email,
            url=job.url,
            injection_flag=job.injection_flag,
            match_score=match,
        )
        if job
        else None,
        cv=_summary(cv),
        cover_letter=_summary(letter),
        changes=changes(master, cv),
        email=_email_out(email),
        checks=SendChecks(
            blocking=check.blocking,
            warnings=check.warnings,
            duplicate_ids=check.duplicate_ids,
            sent_today=check.sent_today,
            cap=check.cap,
        ),
        sending_enabled=user.sending_enabled,
        send_account=account,
    )


@router.put("/{email_id}", response_model=OutgoingEmailOut)
async def edit(
    email_id: int, body: EmailEdit, _: Authenticated, session: Session
) -> OutgoingEmailOut:
    email = await session.get(OutgoingEmail, email_id)
    if email is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "email not found")
    if email.status not in EDITABLE:
        raise HTTPException(status.HTTP_409_CONFLICT, "this email can no longer be edited")
    to = (body.to_address or "").strip().lower() or None
    cc = [c.strip().lower() for c in body.cc if c.strip()]
    if to is not None and not valid_address(to):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_recipient")
    if any(not valid_address(c) for c in cc):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_cc")
    email.to_address = to
    email.cc = cc
    email.subject = header_text(body.subject)
    email.body = body.body.replace("\r\n", "\n").strip()
    email.status = OutgoingStatus.DRAFT
    email.error = None
    await session.commit()
    return _email_out(email)


@router.post(
    "/{email_id}/approve", response_model=TaskStarted, status_code=status.HTTP_202_ACCEPTED
)
async def approve(
    email_id: int, body: ApproveIn, _: Authenticated, session: Session, state: State
) -> TaskStarted:
    """Approve & Send: the only way an email gets sent."""
    email = await session.scalar(
        select(OutgoingEmail).where(OutgoingEmail.id == email_id).with_for_update()
    )
    if email is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "email not found")
    if email.status not in EDITABLE:
        raise HTTPException(status.HTTP_409_CONFLICT, {"blocking": ["not_draft"]})
    app = await session.get(Application, email.application_id)
    assert app is not None
    check = await check_send(
        session,
        email,
        app,
        override_duplicate=body.override_duplicate,
        confirm_flagged=body.confirm_flagged,
    )
    if await _send_account(session) is None:
        check.blocking.append("gmail_send_not_connected")
    if not check.ok:
        raise HTTPException(status.HTTP_409_CONFLICT, {"blocking": check.blocking})
    token, token_hash = new_approval_token()
    now = datetime.now(UTC)
    email.status = OutgoingStatus.APPROVED
    email.approved_at = now
    email.approval_token_hash = token_hash
    email.approval_expires_at = approval_expiry(now)
    email.approval_consumed_at = None
    email.error = None
    session.add(
        ApplicationEvent(
            application_id=app.id,
            type="send_approved",
            payload={
                "to": email.to_address,
                "attachments": [a["name"] for a in email.attachments],
                "override_duplicate": body.override_duplicate,
                "confirm_flagged": body.confirm_flagged,
            },
        )
    )
    task = await create_task_run(session, "send_email", {"email_id": email.id})
    await session.commit()
    await state.arq.enqueue_job(
        "send_email",
        str(task.id),
        email.id,
        token,
        body.override_duplicate,
        body.confirm_flagged,
        _job_id=str(task.id),
    )
    return TaskStarted(task_id=str(task.id))


@router.post("/applications/{application_id}/applied-via-link", response_model=QueueItem)
async def applied_via_link(application_id: int, _: Authenticated, session: Session) -> QueueItem:
    """You applied on the employer's site with the downloaded PDFs: track it like a sent email."""
    app = await session.get(Application, application_id)
    if app is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "application not found")
    if app.status in (ApplicationStatus.DRAFT, ApplicationStatus.READY_FOR_REVIEW):
        session.add(
            ApplicationEvent(
                application_id=app.id,
                type="applied_via_link",
                payload={"url": app.apply_url, "from_status": app.status.value},
            )
        )
        app.status = ApplicationStatus.SENT
        app.apply_channel = ApplyChannel.LINK
        app.applied_at = datetime.now(UTC)
    job = await session.get(JobPosting, app.job_id) if app.job_id else None
    await session.commit()
    await session.refresh(app)  # updated_at is set by the database
    return QueueItem(
        application_id=app.id,
        job_id=app.job_id,
        role_title=app.role_title,
        company=job.company_name if job else None,
        status=app.status,
        email_status=None,
        updated_at=app.updated_at,
    )
