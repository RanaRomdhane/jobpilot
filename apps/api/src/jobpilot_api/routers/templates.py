"""LaTeX templates: upload, conversion (worker), mapping review, edits, test compiles, confirmation.

A template is only used for generation once you confirmed its mapping (`mapping_confirmed`), and
any edit to its source clears that confirmation. Conversion and compiles run in the worker; the
API only stores files and metadata.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, File, Form, HTTPException, Response, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, update

from jobpilot_api.auth import Authenticated
from jobpilot_api.deps import Session, State
from jobpilot_core.enums import TemplateKind
from jobpilot_core.models import LatexTemplate, Resume
from jobpilot_core.resume import ResumeProfile, carry_ids
from jobpilot_core.tasks import create_task_run
from jobpilot_latex import templates as tfiles
from jobpilot_latex.render import TemplateError, check_syntax, profile_from_data

router = APIRouter(prefix="/templates", tags=["templates"])

MAX_REQUEST_MB = 45


class TemplateOut(BaseModel):
    id: int
    kind: TemplateKind
    name: str
    is_default: bool
    mapping_confirmed: bool
    page_budget: int
    placeholders: list[str]
    created_at: datetime
    converted: bool
    engine: str | None
    main: str | None
    files: list[str]
    preview: dict[str, Any] | None
    warnings: list[str]


class TemplateDetail(TemplateOut):
    mapping: dict[str, Any]
    source: str | None  # the Jinja template
    original: str | None  # the uploaded main .tex


class TaskStarted(BaseModel):
    task_id: str


class UploadResult(BaseModel):
    template: TemplateOut
    task_id: str


class SourceIn(BaseModel):
    source: str = Field(max_length=200_000)


class ConfirmIn(BaseModel):
    page_budget: int = Field(default=1, ge=1, le=4)
    remove_page_breaks: bool = False
    headings: dict[str, dict[str, str]] | None = None
    make_default: bool = True


class PreviewIn(BaseModel):
    use: Literal["master", "extracted"] = "master"


class ProfileProposal(BaseModel):
    current: ResumeProfile | None
    proposed: ResumeProfile


def _out(row: LatexTemplate, root_exists: bool) -> dict[str, Any]:
    m = row.mapping or {}
    return {
        "id": row.id,
        "kind": row.kind,
        "name": row.name,
        "is_default": row.is_default,
        "mapping_confirmed": row.mapping_confirmed,
        "page_budget": row.page_budget,
        "placeholders": row.placeholders or [],
        "created_at": row.created_at,
        "converted": root_exists,
        "engine": m.get("engine"),
        "main": m.get("main"),
        "files": m.get("files", []),
        "preview": m.get("preview"),
        "warnings": m.get("warnings", []),
    }


async def _row(session: Session, template_id: int) -> LatexTemplate:
    row = await session.get(LatexTemplate, template_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "template not found")
    return row


def _converted(state: State, template_id: int) -> bool:
    root = tfiles.template_root(state.settings.storage_dir, template_id)
    return (root / tfiles.TEMPLATE_FILE).exists()


@router.get("", response_model=list[TemplateOut])
async def list_templates(_: Authenticated, session: Session, state: State) -> list[TemplateOut]:
    rows = (await session.scalars(select(LatexTemplate).order_by(LatexTemplate.id.desc()))).all()
    return [TemplateOut.model_validate(_out(r, _converted(state, r.id))) for r in rows]


@router.post("", response_model=UploadResult, status_code=status.HTTP_201_CREATED)
async def upload_template(
    _: Authenticated,
    session: Session,
    state: State,
    kind: Annotated[TemplateKind, Form()],
    files: Annotated[list[UploadFile], File(description=".tex/.cls/.sty/images/fonts or a .zip")],
    name: Annotated[str | None, Form(max_length=200)] = None,
) -> UploadResult:
    """Store the files, then convert the main .tex into a template in the worker."""
    budget = MAX_REQUEST_MB * 1024 * 1024
    uploads: list[tuple[str, bytes]] = []
    for f in files:
        data = await f.read(budget + 1)
        budget -= len(data)
        if budget < 0:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "upload too large")
        uploads.append((f.filename or "file", data))
    try:
        tf = tfiles.unpack(uploads)
    except tfiles.TemplateUploadError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    main_source = tf.files[tf.main].decode("utf-8", errors="replace")
    row = LatexTemplate(
        kind=kind,
        name=(name or tf.main.rsplit("/", 1)[-1].removesuffix(".tex"))[:200],
        file_path="",
        placeholders=[],
        mapping={
            "main": tf.main,
            "files": sorted(tf.files),
            "engine": tfiles.detect_engine(main_source),
        },
        page_budget=state.settings.cv_page_budget if kind is TemplateKind.CV else 1,
    )
    session.add(row)
    await session.flush()
    root = tfiles.save_files(state.settings.storage_dir, row.id, tf)
    row.file_path = root.relative_to(state.settings.storage_dir.resolve()).as_posix()
    task = await create_task_run(session, "template_convert", {"template_id": row.id})
    await session.commit()
    await state.arq.enqueue_job("template_convert", str(task.id), row.id, _job_id=str(task.id))
    return UploadResult(template=TemplateOut.model_validate(_out(row, False)), task_id=str(task.id))


@router.get("/{template_id}", response_model=TemplateDetail)
async def get_template(
    template_id: int, _: Authenticated, session: Session, state: State
) -> TemplateDetail:
    row = await _row(session, template_id)
    root = tfiles.template_root(state.settings.storage_dir, template_id)
    path = root / tfiles.TEMPLATE_FILE
    mapping = dict(row.mapping or {})
    original = None
    if mapping.get("main"):
        try:
            original = tfiles.read_main(state.settings.storage_dir, template_id, mapping["main"])
        except OSError:
            original = None
    return TemplateDetail.model_validate(
        {
            **_out(row, path.exists()),
            "mapping": {k: v for k, v in mapping.items() if k != "extracted"}
            | {"has_extracted": bool(mapping.get("extracted"))},
            "source": path.read_text(encoding="utf-8") if path.exists() else None,
            "original": original,
        }
    )


@router.put("/{template_id}/source", response_model=TemplateOut)
async def save_source(
    template_id: int, body: SourceIn, _: Authenticated, session: Session, state: State
) -> TemplateOut:
    """Your own edit of the Jinja template: checked for syntax; the confirmation is cleared."""
    row = await _row(session, template_id)
    try:
        check_syntax(body.source)
    except TemplateError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    root = tfiles.template_root(state.settings.storage_dir, template_id)
    root.mkdir(parents=True, exist_ok=True)
    (root / tfiles.TEMPLATE_FILE).write_text(body.source, encoding="utf-8")
    row.mapping_confirmed = False
    await session.commit()
    return TemplateOut.model_validate(_out(row, True))


@router.post("/{template_id}/convert", response_model=TaskStarted, status_code=202)
async def reconvert(
    template_id: int, _: Authenticated, session: Session, state: State
) -> TaskStarted:
    await _row(session, template_id)
    task = await create_task_run(session, "template_convert", {"template_id": template_id})
    await session.commit()
    await state.arq.enqueue_job("template_convert", str(task.id), template_id, _job_id=str(task.id))
    return TaskStarted(task_id=str(task.id))


@router.post("/{template_id}/preview", response_model=TaskStarted, status_code=202)
async def preview(
    template_id: int, body: PreviewIn, _: Authenticated, session: Session, state: State
) -> TaskStarted:
    await _row(session, template_id)
    task = await create_task_run(session, "template_preview", {"template_id": template_id})
    await session.commit()
    await state.arq.enqueue_job(
        "template_preview", str(task.id), template_id, body.use, _job_id=str(task.id)
    )
    return TaskStarted(task_id=str(task.id))


@router.get("/{template_id}/preview.pdf", response_class=FileResponse)
async def preview_pdf(
    template_id: int, _: Authenticated, session: Session, state: State
) -> FileResponse:
    await _row(session, template_id)
    path = tfiles.template_root(state.settings.storage_dir, template_id) / tfiles.PREVIEW_FILE
    if not path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no preview yet")
    return FileResponse(
        path,
        media_type="application/pdf",
        headers={"Cache-Control": "no-store", "Content-Disposition": "inline"},
    )


@router.post("/{template_id}/confirm", response_model=TemplateOut)
async def confirm(
    template_id: int, body: ConfirmIn, _: Authenticated, session: Session, state: State
) -> TemplateOut:
    """You checked the mapping and the preview: the template may now be used."""
    row = await _row(session, template_id)
    if not _converted(state, template_id):
        raise HTTPException(status.HTTP_409_CONFLICT, "template not converted yet")
    mapping = dict(row.mapping or {})
    mapping["remove_page_breaks"] = body.remove_page_breaks
    if body.headings is not None:
        mapping["headings"] = {
            k[:40]: {"en": v.get("en", "")[:120], "fr": v.get("fr", "")[:120]}
            for k, v in body.headings.items()
        }
    row.mapping = mapping
    row.page_budget = body.page_budget
    row.mapping_confirmed = True
    if body.make_default:
        await session.execute(
            update(LatexTemplate)
            .where(LatexTemplate.kind == row.kind, LatexTemplate.id != row.id)
            .values(is_default=False)
        )
        row.is_default = True
    await session.commit()
    return TemplateOut.model_validate(_out(row, True))


@router.get("/{template_id}/profile-proposal", response_model=ProfileProposal)
async def profile_proposal(template_id: int, _: Authenticated, session: Session) -> ProfileProposal:
    """The master profile rebuilt from the CV template's own content, keeping the master's ids.

    Nothing is saved here: the CV page shows the diff, and you save it as the master yourself.
    """
    row = await _row(session, template_id)
    extracted = (row.mapping or {}).get("extracted")
    if row.kind is not TemplateKind.CV or not extracted:
        raise HTTPException(status.HTTP_409_CONFLICT, "no extracted CV data for this template")
    master = await session.scalar(select(Resume).where(Resume.is_master))
    current = ResumeProfile.model_validate(master.parsed) if master and master.parsed else None
    lang = current.language if current and current.language in ("fr", "en") else "en"
    proposed = profile_from_data(extracted, lang)
    proposed = carry_ids(current, proposed) if current else proposed
    return ProfileProposal(current=current, proposed=_strip_bold(proposed))


def _strip_bold(profile: ResumeProfile) -> ResumeProfile:
    import re

    def strip(node: Any) -> Any:
        if isinstance(node, str):
            return re.sub(r"\*\*([^*]+)\*\*", r"\1", node)
        if isinstance(node, list):
            return [strip(v) for v in node]
        if isinstance(node, dict):
            return {k: strip(v) for k, v in node.items()}
        return node

    return ResumeProfile.model_validate(strip(profile.model_dump(mode="json")))


@router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_template(
    template_id: int, _: Authenticated, session: Session, state: State
) -> Response:
    row = await _row(session, template_id)
    await session.delete(row)
    await session.commit()
    tfiles.delete(state.settings.storage_dir, template_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
