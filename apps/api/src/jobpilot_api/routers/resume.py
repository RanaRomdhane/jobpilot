"""CV uploads and the master profile.

Uploads are validated by content (magic bytes, structure), stored under a random name in
`<storage_dir>/resumes` (outside any web root) and processed in the worker. The master profile is
the CV you edited and saved; it is the only source the tailoring agents may draw facts from.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, File, Form, HTTPException, Query, Response, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import func, select

from jobpilot_api.auth import Authenticated
from jobpilot_api.deps import Session, State
from jobpilot_core.models import ATSReport, Resume
from jobpilot_core.resume import ResumeProfile, ensure_ids
from jobpilot_core.tasks import create_task_run
from jobpilot_resume import UploadRejectedError, validate_upload
from jobpilot_resume.storage import new_relative_path, resume_path

router = APIRouter(prefix="/resume", tags=["resume"])

# the dashboard's current language: the LLM review is written in it
Locale = Literal["fr", "en"]


class ResumeUploadOut(BaseModel):
    id: int
    original_filename: str | None
    mime: str | None
    size_bytes: int | None
    status: str
    error: str | None
    page_count: int | None
    uploaded_at: datetime
    parsed_at: datetime | None
    injection_flag: bool
    injection_score: float | None
    latest_score: int | None = None


class ResumeUploadDetail(ResumeUploadOut):
    parsed: ResumeProfile | None
    warnings: list[dict[str, Any]]
    missing_lines: list[str]  # CV lines the parser did not carry over (shown for review)
    injection_reasons: list[dict[str, Any]]
    signals: dict[str, Any]
    hidden_excerpts: list[str]
    word_count: int | None


class UploadResult(BaseModel):
    resume: ResumeUploadOut
    task_id: str


class TaskStarted(BaseModel):
    task_id: str


class MasterOut(BaseModel):
    profile: ResumeProfile
    updated_at: datetime
    sources: list[int]


class MasterIn(BaseModel):
    profile: ResumeProfile
    source_resume_id: int | None = None


def _out(row: Resume, latest: int | None = None) -> ResumeUploadOut:
    data = {f: getattr(row, f) for f in ResumeUploadOut.model_fields if f != "latest_score"}
    return ResumeUploadOut.model_validate({**data, "latest_score": latest})


def _detail(row: Resume, latest: int | None) -> ResumeUploadDetail:
    ex = row.extraction or {}
    return ResumeUploadDetail.model_validate(
        {
            **_out(row, latest).model_dump(),
            "parsed": row.parsed,
            "warnings": (row.parse_meta or {}).get("warnings", []),
            "missing_lines": (row.parse_meta or {}).get("missing_lines", []),
            "injection_reasons": row.injection_reasons,
            "signals": ex.get("signals", {}),
            "hidden_excerpts": [h[:200] for h in ex.get("hidden", [])[:5]],
            "word_count": ex.get("word_count"),
        }
    )


async def _latest_scores(session: Session, ids: list[int]) -> dict[int, int]:
    if not ids:
        return {}
    latest = (
        select(ATSReport.resume_id, func.max(ATSReport.id).label("rid"))
        .where(ATSReport.resume_id.in_(ids))
        .group_by(ATSReport.resume_id)
        .subquery()
    )
    rows = await session.execute(
        select(ATSReport.resume_id, ATSReport.score).join(latest, ATSReport.id == latest.c.rid)
    )
    return {int(rid): score for rid, score in rows.all() if rid is not None}


async def _upload(session: Session, resume_id: int) -> Resume:
    row = await session.get(Resume, resume_id)
    if row is None or row.is_master:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "resume not found")
    return row


async def _enqueue_parse(session: Session, state: State, row: Resume, locale: Locale | None) -> str:
    task = await create_task_run(session, "parse_resume", {"resume_id": row.id})
    await state.arq.enqueue_job("parse_resume", str(task.id), row.id, locale, _job_id=str(task.id))
    return str(task.id)


@router.post("/uploads", response_model=UploadResult, status_code=status.HTTP_201_CREATED)
async def upload_resume(
    _: Authenticated,
    session: Session,
    state: State,
    file: Annotated[UploadFile, File(description="PDF or DOCX, up to the configured size")],
    locale: Annotated[Locale | None, Form()] = None,
) -> UploadResult:
    max_bytes = state.settings.resume_max_upload_mb * 1024 * 1024
    data = await file.read(max_bytes + 1)
    try:
        upload = validate_upload(data, file.filename, max_bytes=max_bytes)
    except UploadRejectedError as exc:
        code = (
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
            if exc.code == "too_large"
            else status.HTTP_415_UNSUPPORTED_MEDIA_TYPE
            if exc.code == "unsupported_type"
            else status.HTTP_422_UNPROCESSABLE_CONTENT
        )
        raise HTTPException(code, f"upload_{exc.code}") from exc

    relative = new_relative_path(upload.kind.value)
    path = resume_path(state.settings.storage_dir, relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    path.chmod(0o600)
    row = Resume(
        file_path=relative,
        original_filename=upload.display_name,
        mime=upload.mime,
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        status="uploaded",
    )
    session.add(row)
    await session.commit()
    task_id = await _enqueue_parse(session, state, row, locale)
    return UploadResult(resume=_out(row), task_id=task_id)


@router.get("/uploads", response_model=list[ResumeUploadOut])
async def list_uploads(_: Authenticated, session: Session) -> list[ResumeUploadOut]:
    rows = (
        await session.scalars(
            select(Resume).where(Resume.is_master.is_(False)).order_by(Resume.id.desc()).limit(50)
        )
    ).all()
    scores = await _latest_scores(session, [r.id for r in rows])
    return [_out(r, scores.get(r.id)) for r in rows]


@router.get("/uploads/{resume_id}", response_model=ResumeUploadDetail)
async def get_upload(resume_id: int, _: Authenticated, session: Session) -> ResumeUploadDetail:
    row = await _upload(session, resume_id)
    return _detail(row, (await _latest_scores(session, [row.id])).get(row.id))


@router.get("/uploads/{resume_id}/file", response_class=FileResponse)
async def download_upload(
    resume_id: int, _: Authenticated, session: Session, state: State
) -> FileResponse:
    """Always an attachment, never rendered inline by the dashboard's origin."""
    row = await _upload(session, resume_id)
    if row.file_path is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "file not kept")
    path = resume_path(state.settings.storage_dir, row.file_path)
    if not path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "file missing")
    return FileResponse(
        path,
        media_type=row.mime or "application/octet-stream",
        filename=row.original_filename or path.name,
        content_disposition_type="attachment",
        headers={"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox"},
    )


@router.post("/uploads/{resume_id}/reparse", response_model=TaskStarted)
async def reparse_upload(
    resume_id: int,
    _: Authenticated,
    session: Session,
    state: State,
    locale: Annotated[Locale | None, Query()] = None,
) -> TaskStarted:
    row = await _upload(session, resume_id)
    if row.file_path is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "file not kept")
    return TaskStarted(task_id=await _enqueue_parse(session, state, row, locale))


@router.delete("/uploads/{resume_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_upload(
    resume_id: int, _: Authenticated, session: Session, state: State
) -> Response:
    """Deletes the stored file and its reports. The master profile is never affected."""
    row = await _upload(session, resume_id)
    if row.file_path:
        resume_path(state.settings.storage_dir, row.file_path).unlink(missing_ok=True)
    await session.delete(row)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------- master profile
async def _master(session: Session) -> Resume | None:
    master: Resume | None = await session.scalar(select(Resume).where(Resume.is_master))
    return master


def _master_out(row: Resume) -> MasterOut:
    return MasterOut(
        profile=ResumeProfile.model_validate(row.parsed or {}),
        updated_at=row.updated_at,
        sources=list((row.parse_meta or {}).get("sources", [])),
    )


@router.get("/master", response_model=MasterOut | None)
async def get_master(_: Authenticated, session: Session) -> MasterOut | None:
    row = await _master(session)
    return _master_out(row) if row else None


@router.put("/master", response_model=MasterOut)
async def save_master(body: MasterIn, _: Authenticated, session: Session) -> MasterOut:
    """Your edited profile becomes the master. Item ids are kept; new items get fresh ids."""
    if body.source_resume_id is not None:
        await _upload(session, body.source_resume_id)
    profile = ensure_ids(body.profile)
    row = await _master(session)
    if row is None:
        row = Resume(is_master=True, status="master", parse_meta={"sources": []})
        session.add(row)
    sources = list((row.parse_meta or {}).get("sources", []))
    if body.source_resume_id is not None and body.source_resume_id not in sources:
        sources.append(body.source_resume_id)
    row.parsed = profile.model_dump()
    row.parse_meta = {**(row.parse_meta or {}), "sources": sources}
    await session.commit()
    await session.refresh(row)
    return _master_out(row)
