"""Gmail connection status and triage runs."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, status
from pydantic import BaseModel
from sqlalchemy import func, select

from jobpilot_api.deps import Session, State
from jobpilot_api.schemas import TaskCreated
from jobpilot_core.models import EmailMessage, GmailSyncState, OAuthCredential
from jobpilot_core.tasks import create_task_run

router = APIRouter(prefix="/gmail", tags=["gmail"])


class ScopeStatus(BaseModel):
    scope_set: str
    account_email: str | None
    expires_at: datetime | None


class GmailStatus(BaseModel):
    connected: bool
    scopes: list[ScopeStatus]
    last_full_sync_at: datetime | None = None
    last_incremental_sync_at: datetime | None = None
    emails: int
    unprocessed: int
    flagged: int


@router.get("/status", response_model=GmailStatus)
async def gmail_status(session: Session) -> GmailStatus:
    creds = (await session.scalars(select(OAuthCredential))).all()
    sync = (
        await session.scalars(
            select(GmailSyncState).order_by(GmailSyncState.updated_at.desc()).limit(1)
        )
    ).first()
    total, unprocessed, flagged = (
        await session.execute(
            select(
                func.count(),
                func.count().filter(EmailMessage.category.is_(None)),
                func.count().filter(EmailMessage.injection_flag.is_(True)),
            ).select_from(EmailMessage)
        )
    ).one()
    return GmailStatus(
        connected=any(c.scope_set == "triage" for c in creds),
        scopes=[
            ScopeStatus(
                scope_set=c.scope_set.value, account_email=c.account_email, expires_at=c.expires_at
            )
            for c in creds
        ],
        last_full_sync_at=sync.last_full_sync_at if sync else None,
        last_incremental_sync_at=sync.last_incremental_sync_at if sync else None,
        emails=total,
        unprocessed=unprocessed,
        flagged=flagged,
    )


@router.post(
    "/triage",
    response_model=TaskCreated,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Sync Gmail and classify unprocessed emails (background task)",
)
async def start_triage(state: State, session: Session, sync: bool = True) -> TaskCreated:
    row = await create_task_run(session, "triage", {"sync": sync})
    await state.arq.enqueue_job("triage", str(row.id), sync, _job_id=str(row.id))
    return TaskCreated(task_id=str(row.id))
