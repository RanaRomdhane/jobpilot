"""Classified emails: list, detail, manual re-classification, clearing the suspicious flag."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import ColumnElement, func, or_, select

from jobpilot_api.auth import Authenticated
from jobpilot_api.deps import Session, State
from jobpilot_core.enums import EmailCategory
from jobpilot_core.models import EmailMessage
from jobpilot_core.tasks import create_task_run

router = APIRouter(prefix="/emails", tags=["emails"])


class EmailOut(BaseModel):
    id: int
    thread_id: str
    from_address: str | None
    from_name: str | None
    subject: str | None
    snippet: str | None
    received_at: datetime | None
    category: EmailCategory | None
    manual_category: EmailCategory | None
    confidence: float | None
    classification_reason: str | None
    label_applied: bool
    injection_flag: bool
    injection_score: float | None
    processed_at: datetime | None


class EmailDetail(EmailOut):
    injection_reasons: list[dict[str, Any]]
    extracted: dict[str, Any]
    gmail_url: str


class EmailPage(BaseModel):
    items: list[EmailOut]
    total: int
    counts: dict[str, int]


class Reclassify(BaseModel):
    category: EmailCategory


class EmailActionResult(BaseModel):
    email: EmailDetail
    task_id: str | None


def to_detail(e: EmailMessage) -> EmailDetail:
    return EmailDetail.model_validate(
        {
            **{f: getattr(e, f) for f in EmailOut.model_fields},
            "injection_reasons": e.injection_reasons,
            "extracted": e.extracted,
            "gmail_url": f"https://mail.google.com/mail/u/0/#all/{e.thread_id}",
        }
    )


@router.get("", response_model=EmailPage)
async def list_emails(
    _: Authenticated,
    session: Session,
    category: Annotated[list[EmailCategory] | None, Query()] = None,
    flagged: bool | None = None,
    unclassified: bool = False,
    min_confidence: Annotated[float | None, Query(ge=0, le=1)] = None,
    max_confidence: Annotated[float | None, Query(ge=0, le=1)] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    sort: Literal["received_desc", "received_asc", "confidence_asc"] = "received_desc",
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> EmailPage:
    filters: list[ColumnElement[bool]] = []
    if category:
        filters.append(EmailMessage.category.in_(category))
    if unclassified:
        filters.append(EmailMessage.category.is_(None))
    if flagged is not None:
        filters.append(EmailMessage.injection_flag.is_(flagged))
    if min_confidence is not None:
        filters.append(EmailMessage.confidence >= min_confidence)
    if max_confidence is not None:
        filters.append(EmailMessage.confidence <= max_confidence)
    if q:
        like = f"%{q.replace('%', r'\%').replace('_', r'\_')}%"
        filters.append(
            or_(
                EmailMessage.subject.ilike(like),
                EmailMessage.from_address.ilike(like),
                EmailMessage.from_name.ilike(like),
                EmailMessage.snippet.ilike(like),
            )
        )
    order: Any = {
        "received_desc": EmailMessage.received_at.desc().nulls_last(),
        "received_asc": EmailMessage.received_at.asc().nulls_last(),
        "confidence_asc": EmailMessage.confidence.asc().nulls_first(),
    }[sort]
    rows = (
        await session.scalars(
            select(EmailMessage)
            .where(*filters)
            .order_by(order, EmailMessage.id.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    total = await session.scalar(select(func.count()).select_from(EmailMessage).where(*filters))
    counts_rows = (
        await session.execute(
            select(EmailMessage.category, func.count()).group_by(EmailMessage.category)
        )
    ).all()
    counts = {(c.value if c else "unclassified"): n for c, n in counts_rows}
    counts["flagged"] = int(
        await session.scalar(
            select(func.count()).select_from(EmailMessage).where(EmailMessage.injection_flag)
        )
        or 0
    )
    return EmailPage(
        items=[EmailOut.model_validate(r, from_attributes=True) for r in rows],
        total=int(total or 0),
        counts=counts,
    )


async def _get(session: Session, email_id: int) -> EmailMessage:
    email = await session.get(EmailMessage, email_id)
    if email is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "email not found")
    return email


@router.get("/{email_id}", response_model=EmailDetail)
async def get_email(email_id: int, _: Authenticated, session: Session) -> EmailDetail:
    return to_detail(await _get(session, email_id))


@router.patch("/{email_id}/category", response_model=EmailActionResult)
async def reclassify(
    email_id: int, body: Reclassify, _: Authenticated, session: Session, state: State
) -> EmailActionResult:
    """Your correction wins: stored as `manual_category` (eval feed) and synced to Gmail."""
    email = await _get(session, email_id)
    old = email.category
    email.manual_category = body.category
    email.category = body.category
    email.confidence = 1.0
    email.classification_reason = "manual"
    email.processed_at = email.processed_at or datetime.now(UTC)
    email.label_applied = False
    await session.commit()
    task_id = None
    if old != body.category:
        row = await create_task_run(
            session, "user_label_change", {"email_id": email.id, "action": "reclassify"}
        )
        await state.arq.enqueue_job(
            "user_label_change",
            str(row.id),
            email.gmail_id,
            "reclassify",
            old.value if old else None,
            body.category.value,
            _job_id=str(row.id),
        )
        task_id = str(row.id)
    return EmailActionResult(email=to_detail(email), task_id=task_id)


@router.post("/{email_id}/clear-suspicious", response_model=EmailActionResult)
async def clear_suspicious(
    email_id: int, _: Authenticated, session: Session, state: State
) -> EmailActionResult:
    """You reviewed it and it is fine: unflag and remove the ⚠ Suspicious label in Gmail."""
    email = await _get(session, email_id)
    email.injection_flag = False
    email.injection_reasons = [
        *email.injection_reasons,
        {
            "layer": "user",
            "rule": "user.cleared",
            "category": "user",
            "weight": 0.0,
            "excerpt": f"cleared by user on {datetime.now(UTC).date().isoformat()}",
        },
    ]
    await session.commit()
    row = await create_task_run(
        session, "user_label_change", {"email_id": email.id, "action": "clear_suspicious"}
    )
    await state.arq.enqueue_job(
        "user_label_change",
        str(row.id),
        email.gmail_id,
        "clear_suspicious",
        None,
        None,
        _job_id=str(row.id),
    )
    return EmailActionResult(email=to_detail(email), task_id=str(row.id))
