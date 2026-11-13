"""Tailored documents: generate (worker), list versions, read one, download its PDF.

Generation never sends anything: it produces versioned files on a draft application that you
review (Phase 7 adds the email and the approval).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select

from jobpilot_api.auth import Authenticated
from jobpilot_api.deps import Session, State
from jobpilot_core.enums import ApplicationStatus, CompileStatus, DocumentKind
from jobpilot_core.models import Application, GeneratedDocument, JobPosting, Resume
from jobpilot_core.outgoing import attachment_name
from jobpilot_core.tasks import create_task_run
from jobpilot_resume.storage import resume_path

router = APIRouter(prefix="/documents", tags=["documents"])


class GenerateIn(BaseModel):
    job_id: int
    locale: Literal["fr", "en"] = "fr"
    tone: Literal["formal", "modern"] | None = None
    confirm_flagged: bool = False  # tailor from a posting flagged for prompt injection


class TaskStarted(BaseModel):
    task_id: str


class DocumentOut(BaseModel):
    id: int
    application_id: int
    kind: DocumentKind
    version: int
    template_id: int | None
    compile_status: CompileStatus
    page_count: int | None
    created_at: datetime
    language: str | None
    error: str | None
    findings: int
    ats_before: float | None
    ats_after: float | None


class DocumentDetail(DocumentOut):
    content: dict[str, Any]
    errors: list[str]
    file_name: str


class ApplicationDocuments(BaseModel):
    application_id: int | None
    status: ApplicationStatus | None
    documents: list[DocumentOut]


def _out(d: GeneratedDocument) -> dict[str, Any]:
    c = d.content or {}
    ats = c.get("ats") or {}
    return {
        "id": d.id,
        "application_id": d.application_id,
        "kind": d.kind,
        "version": d.version,
        "template_id": d.template_id,
        "compile_status": d.compile_status,
        "page_count": d.page_count,
        "created_at": d.created_at,
        "language": c.get("language"),
        "error": c.get("error"),
        "findings": len(c.get("findings") or []),
        "ats_before": ats.get("before"),
        "ats_after": ats.get("after"),
    }


file_name = attachment_name  # the same name in the browser and in the application email


@router.post("/generate", response_model=TaskStarted, status_code=status.HTTP_202_ACCEPTED)
async def generate(
    body: GenerateIn, _: Authenticated, session: Session, state: State
) -> TaskStarted:
    job = await session.get(JobPosting, body.job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "job not found")
    if job.injection_flag and not body.confirm_flagged:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "this posting was flagged for prompt injection: confirm first"
        )
    task = await create_task_run(session, "documents_generate", {"job_id": body.job_id})
    await session.commit()
    await state.arq.enqueue_job(
        "documents_generate",
        str(task.id),
        body.job_id,
        body.locale,
        body.tone,
        body.confirm_flagged,
        _job_id=str(task.id),
    )
    return TaskStarted(task_id=str(task.id))


@router.get("/by-job/{job_id}", response_model=ApplicationDocuments)
async def for_job(job_id: int, _: Authenticated, session: Session) -> ApplicationDocuments:
    app = await session.scalar(
        select(Application).where(Application.job_id == job_id).order_by(Application.id)
    )
    if app is None:
        return ApplicationDocuments(application_id=None, status=None, documents=[])
    docs = (
        await session.scalars(
            select(GeneratedDocument)
            .where(GeneratedDocument.application_id == app.id)
            .order_by(GeneratedDocument.kind, GeneratedDocument.version.desc())
        )
    ).all()
    return ApplicationDocuments(
        application_id=app.id,
        status=app.status,
        documents=[DocumentOut.model_validate(_out(d)) for d in docs],
    )


@router.get("/{document_id}", response_model=DocumentDetail)
async def get_document(document_id: int, _: Authenticated, session: Session) -> DocumentDetail:
    doc = await session.get(GeneratedDocument, document_id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document not found")
    name, company = await _names(session, doc)
    c = doc.content or {}
    return DocumentDetail.model_validate(
        {
            **_out(doc),
            "content": c,
            "errors": [str(e) for e in c.get("errors") or []],
            "file_name": file_name(doc.kind, name, company),
        }
    )


async def _names(session: Session, doc: GeneratedDocument) -> tuple[str | None, str | None]:
    app = await session.get(Application, doc.application_id)
    job = await session.get(JobPosting, app.job_id) if app and app.job_id else None
    master = await session.scalar(select(Resume).where(Resume.is_master))
    name = ((master.parsed or {}).get("contact") or {}).get("full_name") if master else None
    return name, job.company_name if job else None


@router.get("/{document_id}/pdf", response_class=FileResponse)
async def document_pdf(
    document_id: int, _: Authenticated, session: Session, state: State
) -> FileResponse:
    doc = await session.get(GeneratedDocument, document_id)
    if doc is None or not doc.pdf_path:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no PDF for this document")
    path = resume_path(state.settings.storage_dir, doc.pdf_path)  # refuses paths outside storage
    if not path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "PDF file missing")
    name, company = await _names(session, doc)
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=file_name(doc.kind, name, company),
        content_disposition_type="inline",
        headers={"Cache-Control": "no-store"},
    )
