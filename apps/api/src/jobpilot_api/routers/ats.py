"""ATS reports: list, read, and request a new one (optionally against a pasted job posting)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from jobpilot_api.auth import Authenticated
from jobpilot_api.deps import Session, State
from jobpilot_core.models import ATSReport, Resume
from jobpilot_core.tasks import create_task_run

router = APIRouter(prefix="/ats", tags=["ats"])


class AtsReportOut(BaseModel):
    id: int
    resume_id: int | None
    score: int
    breakdown: dict[str, Any]
    issues: list[dict[str, Any]]
    generated_at: datetime


class AtsRequest(BaseModel):
    resume_id: int
    # the posting is untrusted text: bounded here, injection-scanned and spotlighted in the worker
    job_text: str | None = Field(default=None, max_length=20_000)
    job_title: str | None = Field(default=None, max_length=200)


class AtsTask(BaseModel):
    task_id: str


@router.get("/reports", response_model=list[AtsReportOut])
async def list_reports(
    _: Authenticated,
    session: Session,
    resume_id: int,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> list[AtsReportOut]:
    rows = (
        await session.scalars(
            select(ATSReport)
            .where(ATSReport.resume_id == resume_id)
            .order_by(ATSReport.id.desc())
            .limit(limit)
        )
    ).all()
    return [AtsReportOut.model_validate(r, from_attributes=True) for r in rows]


@router.get("/reports/{report_id}", response_model=AtsReportOut)
async def get_report(report_id: int, _: Authenticated, session: Session) -> AtsReportOut:
    row = await session.get(ATSReport, report_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "report not found")
    return AtsReportOut.model_validate(row, from_attributes=True)


@router.post("/reports", response_model=AtsTask, status_code=status.HTTP_202_ACCEPTED)
async def request_report(
    body: AtsRequest, _: Authenticated, session: Session, state: State
) -> AtsTask:
    resume = await session.get(Resume, body.resume_id)
    if resume is None or resume.is_master:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "resume not found")
    if resume.raw_text is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "resume_not_processed")
    job_text = (body.job_text or "").strip() or None
    task = await create_task_run(
        session, "ats_report", {"resume_id": resume.id, "with_job": job_text is not None}
    )
    await state.arq.enqueue_job(
        "ats_report",
        str(task.id),
        resume.id,
        job_text,
        (body.job_title or "").strip() or None,
        _job_id=str(task.id),
    )
    return AtsTask(task_id=str(task.id))
