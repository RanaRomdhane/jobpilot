"""Application factory: template onboarding and tailored CV + cover-letter generation.

    convert   original .tex -> Jinja template + extracted data (TemplateConverterAgent, checked)
    preview   template + master profile -> sandboxed PDF
    generate  job -> TailorAgent plan -> guardrail -> render -> compile (fit the page budget)
              -> CoverLetterAgent -> guardrail -> render -> compile -> ATS before/after
              -> versioned `generated_document` rows on a draft `application`

Nothing here can send anything: documents are files on disk and rows in the database. Sending
is Phase 7, behind a human approval.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from sqlalchemy import func, select

from jobpilot_agents.tailor import LetterPlan, Tone, tailor_cv, write_letter
from jobpilot_agents.template_converter import convert_template, render_context
from jobpilot_core.config import Settings
from jobpilot_core.enums import (
    ApplicationStatus,
    ApplyChannel,
    CompileStatus,
    DocumentKind,
    TemplateKind,
)
from jobpilot_core.models import (
    Application,
    ApplicationEvent,
    GeneratedDocument,
    JobMatch,
    JobPosting,
    LatexTemplate,
)
from jobpilot_core.resume import ResumeProfile
from jobpilot_jobs_sources.taxonomy import TECH_VOCAB, _pattern
from jobpilot_latex import templates as tfiles
from jobpilot_latex.compile import CompileResult, Engine, compile_tex
from jobpilot_latex.convert import layout_warnings
from jobpilot_latex.render import TemplateError, format_date, profile_context, render
from jobpilot_resume import FileKind, JobKeyword, extract
from jobpilot_resume.ats import keyword_coverage
from jobpilot_resume.tailoring import (
    Finding,
    build_tailored,
    drop_order,
    profile_text,
    validate_letter,
    validate_plan,
)
from jobpilot_security.text import fold
from jobpilot_worker.matching import profile_row

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from jobpilot_core.llm import LLMProvider
    from jobpilot_core.tasks import TaskReporter

log = logging.getLogger(__name__)

DOCS_DIR = "documents"
MAX_FIT_ROUNDS = 8
LETTER_PAGES = 1
SOURCE_NAMES = {
    "remotive": "Remotive",
    "remoteok": "Remote OK",
    "weworkremotely": "We Work Remotely",
    "tanitjobs": "Tanitjobs",
    "keejob": "Keejob",
    "adzuna": "Adzuna",
    "jsearch": "JSearch",
}
OPENINGS = {
    ("fr", "formal"): "Madame, Monsieur,",
    ("fr", "modern"): "Bonjour,",
    ("en", "formal"): "Dear Hiring Manager,",
    ("en", "modern"): "Hello,",
}
CLOSINGS = {
    ("fr", "formal"): "Je vous prie d'agréer, Madame, Monsieur, l'expression de mes salutations "
    "distinguées.",
    ("fr", "modern"): "Bien cordialement,",
    ("en", "formal"): "Sincerely,",
    ("en", "modern"): "Best regards,",
}


class FactoryError(RuntimeError):
    """`code` is stable (shown in the UI): no_master, template_not_ready, job_flagged, ..."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def _lang(value: str | None, fallback: str = "fr") -> Literal["fr", "en"]:
    v = (value or fallback or "fr")[:2].lower()
    return "en" if v == "en" else "fr"


def job_keywords(job: JobPosting, match: JobMatch | None) -> list[str]:
    """The posting's keywords: what the matcher found, plus the tech vocabulary it names."""
    out = [*(match.matched_keywords if match else []), *(match.missing_keywords if match else [])]
    text = fold(f"{job.title}\n{job.description or ''}")
    out += [t for t in TECH_VOCAB if _pattern(t).search(text)]
    return list(dict.fromkeys(k for k in out if k))[:40]


def plain_profile(profile: ResumeProfile) -> ResumeProfile:
    """Master text without the **bold** markers templates may have carried in."""
    data = profile.model_dump(mode="json")

    def strip(node: Any) -> Any:
        if isinstance(node, str):
            return re.sub(r"\*\*([^*]+)\*\*", r"\1", node)
        if isinstance(node, list):
            return [strip(v) for v in node]
        if isinstance(node, dict):
            return {k: strip(v) for k, v in node.items()}
        return node

    return ResumeProfile.model_validate(strip(data))


class DocumentFactory:
    def __init__(
        self,
        settings: Settings,
        sessionmaker: async_sessionmaker[AsyncSession],
        provider: LLMProvider | None,
        reporter: TaskReporter | None = None,
    ) -> None:
        self.settings = settings
        self.sm = sessionmaker
        self.provider = provider
        self.rep = reporter

    @property
    def tpm(self) -> int:
        return self.settings.limits_for(self.provider.name)[0] if self.provider else 0

    async def _progress(self, fraction: float, message: str) -> None:
        if self.rep is not None:
            await self.rep.progress(fraction, message)

    # ------------------------------------------------------------------ templates

    async def _template(self, session: AsyncSession, template_id: int) -> LatexTemplate:
        row = await session.get(LatexTemplate, template_id)
        if row is None:
            raise FactoryError("template_not_found")
        return row

    async def compile(
        self, row: LatexTemplate, source: str, *, timeout_s: float = 90
    ) -> CompileResult:
        mapping = row.mapping or {}
        engine: Engine = mapping.get("engine", "pdflatex")
        return await compile_tex(
            self.settings.latex_spool_dir,
            source,
            assets=tfiles.assets(self.settings.storage_dir, row.id, mapping.get("main", "")),
            engine=engine,
            timeout_s=timeout_s,
        )

    async def convert(self, template_id: int, *, locale: str = "en") -> dict[str, Any]:
        if self.provider is None:
            raise FactoryError("llm_unavailable")
        async with self.sm() as session:
            row = await self._template(session, template_id)
            mapping = dict(row.mapping or {})
            source = tfiles.read_main(self.settings.storage_dir, row.id, mapping["main"])
            kind = row.kind.value
        await self._progress(0.05, "converting the template, section by section")
        conv = await convert_template(self.provider, source, kind, tpm=self.tpm, lang="en")  # type: ignore[arg-type]
        root = tfiles.template_root(self.settings.storage_dir, template_id)
        (root / tfiles.TEMPLATE_FILE).write_text(conv.template, encoding="utf-8")
        mapping.update(
            headings=conv.headings,
            check=_check_dict(conv.check),
            chunks=[
                {
                    "kind": c.kind,
                    "title": c.title,
                    "mode": c.mode,
                    "ok": c.error is None,
                    "error": c.error,
                    "ratio": c.check.ratio if c.check else None,
                }
                for c in conv.chunks
                if c.mode != "keep"
            ],
            extracted=conv.data,
            variables=sorted(set(re.findall(r"\\VAR\{\s*([A-Za-z_][\w.]*)", conv.template))),
            warnings=layout_warnings(conv.template),
            converted_at=datetime.now(UTC).isoformat(),
        )
        async with self.sm() as session:
            row = await self._template(session, template_id)
            row.mapping = mapping
            row.placeholders = mapping["variables"]
            row.mapping_confirmed = False
            await session.commit()
        await self._progress(0.8, "test compile with the template's own data")
        preview = await self.preview(template_id, use="extracted")
        return {"ok": conv.ok, "chunks": len(mapping["chunks"]), "preview": preview}

    async def template_source(self, template_id: int) -> str:
        root = tfiles.template_root(self.settings.storage_dir, template_id)
        path = root / tfiles.TEMPLATE_FILE
        if not path.exists():
            raise FactoryError("template_not_converted")
        return path.read_text(encoding="utf-8")

    async def preview(
        self, template_id: int, *, use: Literal["master", "extracted"] = "master"
    ) -> dict[str, Any]:
        """Render the template (with the master profile, or with the data it was converted
        from) and compile it. The PDF is kept as the template's preview."""
        source = await self.template_source(template_id)
        async with self.sm() as session:
            row = await self._template(session, template_id)
            mapping = dict(row.mapping or {})
            master = await profile_row(session)
        if use == "master" and master is not None and master.parsed:
            profile = plain_profile(ResumeProfile.model_validate(master.parsed))
            lang = profile.language if profile.language in ("fr", "en") else "en"
            ctx = profile_context(profile, lang)
            ctx["h"] = _headings(mapping, lang)
            ctx["letter"] = sample_letter(mapping, lang)
        else:
            ctx = render_context(
                mapping.get("extracted") or {}, mapping.get("headings") or {}, "en"
            )
        tex = self._prepare(source, mapping)
        try:
            rendered = render(tex, ctx)
        except TemplateError as exc:
            return await self._save_preview(template_id, None, str(exc), None)
        result = await self.compile(row, rendered)
        return await self._save_preview(
            template_id, result.pdf, None if result.ok else _compile_error(result), result.pages
        )

    async def _save_preview(
        self, template_id: int, pdf: bytes | None, error: str | None, pages: int | None
    ) -> dict[str, Any]:
        root = tfiles.template_root(self.settings.storage_dir, template_id)
        if pdf is not None:
            (root / tfiles.PREVIEW_FILE).write_bytes(pdf)
        async with self.sm() as session:
            row = await self._template(session, template_id)
            mapping = dict(row.mapping or {})
            mapping["preview"] = {
                "ok": error is None,
                "error": error,
                "pages": pages,
                "at": datetime.now(UTC).isoformat(),
            }
            row.mapping = mapping
            await session.commit()
        return mapping["preview"]  # type: ignore[no-any-return]

    @staticmethod
    def _prepare(source: str, mapping: dict[str, Any]) -> str:
        """Options you confirmed: forced page breaks removed for the page budget."""
        if mapping.get("remove_page_breaks"):
            source = re.sub(
                r"^[ \t]*\\(newpage|clearpage|pagebreak)\b[^\n]*\n?", "", source, flags=re.MULTILINE
            )
        return source

    # ------------------------------------------------------------------ generation

    async def generate(
        self,
        job_id: int,
        *,
        locale: str = "fr",
        tone: Tone | None = None,
        confirm_flagged: bool = False,
    ) -> dict[str, Any]:
        if self.provider is None:
            raise FactoryError("llm_unavailable")
        async with self.sm() as session:
            job = await session.get(JobPosting, job_id)
            if job is None:
                raise FactoryError("job_not_found")
            if job.injection_flag and not confirm_flagged:
                raise FactoryError("job_flagged", "confirm to tailor from a flagged posting")
            master_row = await profile_row(session)
            if master_row is None or not master_row.parsed or not master_row.is_master:
                raise FactoryError("no_master", "save your master profile on the CV page first")
            templates = {
                t.kind: t
                for t in (
                    await session.scalars(
                        select(LatexTemplate)
                        .where(LatexTemplate.is_default, LatexTemplate.mapping_confirmed)
                        .order_by(LatexTemplate.id.desc())
                    )
                ).all()
            }
            if TemplateKind.CV not in templates or TemplateKind.COVER_LETTER not in templates:
                raise FactoryError("template_not_ready", "confirm a CV and a cover-letter template")
            match = await session.scalar(
                select(JobMatch).where(
                    JobMatch.job_id == job_id, JobMatch.resume_id == master_row.id
                )
            )
            job_dict = {
                "title": job.title,
                "company_name": job.company_name,
                "location_raw": job.location_raw,
                "contract_type": job.contract_type,
                "description": job.description,
            }
            keywords = job_keywords(job, match)
            app = await self._application(session, job)
            await session.commit()
            app_id = app.id
        master = plain_profile(ResumeProfile.model_validate(master_row.parsed))
        lang = _lang(job.language, locale)
        tone = tone or self.settings.cover_letter_tone

        await self._progress(0.1, "tailoring the CV")
        cv = await self._cv(templates[TemplateKind.CV], master, job_dict, keywords, lang, app_id)
        await self._progress(0.6, "writing the cover letter")
        letter = await self._letter(
            templates[TemplateKind.COVER_LETTER],
            master,
            cv["profile"],
            job,
            job_dict,
            keywords,
            lang,
            tone,
            app_id,
        )
        async with self.sm() as session:
            session.add(
                ApplicationEvent(
                    application_id=app_id,
                    type="documents_generated",
                    payload={"cv": cv["document_id"], "cover_letter": letter["document_id"]},
                )
            )
            app_row = await session.get(Application, app_id)
            if app_row is not None and cv["ok"] and letter["ok"]:
                app_row.status = ApplicationStatus.READY_FOR_REVIEW
            await session.commit()
        return {
            "application_id": app_id,
            "cv_document_id": cv["document_id"],
            "cover_letter_document_id": letter["document_id"],
            "ok": cv["ok"] and letter["ok"],
            "language": lang,
            "ats": cv["ats"],
            "findings": len(cv["findings"]) + len(letter["findings"]),
        }

    async def _application(self, session: AsyncSession, job: JobPosting) -> Application:
        app = await session.scalar(
            select(Application).where(Application.job_id == job.id).order_by(Application.id)
        )
        if app is None:
            app = Application(
                job_id=job.id,
                company_id=job.company_id,
                role_title=job.title[:300],
                status=ApplicationStatus.DRAFT,
                apply_channel=ApplyChannel.EMAIL if job.apply_email else ApplyChannel.LINK,
                apply_url=job.apply_url or job.url,
                contact_email=job.apply_email,
                source=job.source,
            )
            session.add(app)
            await session.flush()
            session.add(ApplicationEvent(application_id=app.id, type="created", payload={}))
        return app

    async def _cv(
        self,
        tpl: LatexTemplate,
        master: ResumeProfile,
        job: dict[str, Any],
        keywords: list[str],
        lang: Literal["fr", "en"],
        app_id: int,
    ) -> dict[str, Any]:
        assert self.provider is not None
        plan, _ = await tailor_cv(
            self.provider, master, job, lang=lang, page_budget=tpl.page_budget, tpm=self.tpm
        )
        plan.language = lang
        checked = validate_plan(master, plan, keywords)
        mapping = tpl.mapping or {}
        source = self._prepare(await self.template_source(tpl.id), mapping)
        dropped: set[str] = set()
        order = drop_order(checked.plan)
        result: CompileResult | None = None
        tex = ""
        error: str | None = None
        tailored = build_tailored(master, checked.plan)
        for round_no in range(MAX_FIT_ROUNDS + 1):
            tailored = build_tailored(master, checked.plan, drop=dropped)
            ctx = profile_context(tailored, lang)
            ctx["h"] = _headings(mapping, lang)
            try:
                tex = render(source, ctx)
            except TemplateError as exc:
                error = str(exc)
                break
            await self._progress(0.2 + 0.05 * round_no, f"compiling the CV (round {round_no + 1})")
            result = await self.compile(tpl, tex)
            if not result.ok or (result.pages or 0) <= tpl.page_budget or not order:
                break
            dropped.add(order.pop(0))  # over budget: drop the least useful bullet, retry
        if result is not None and not result.ok:
            error = _compile_error(result)
        ats = self._ats(master, result.pdf if result and result.ok else None, keywords)
        content = {
            "language": lang,
            "plan": checked.plan.model_dump(mode="json"),
            "profile": tailored.model_dump(mode="json"),
            "findings": [f.as_dict() for f in checked.findings],
            "dropped_for_length": sorted(dropped),
            "keywords": keywords,
            "ats": ats,
            "page_budget": tpl.page_budget,
        }
        doc_id = await self._store(app_id, DocumentKind.CV, tpl, content, tex, result, error)
        return {
            "document_id": doc_id,
            "ok": error is None and bool(result and (result.pages or 0) <= tpl.page_budget),
            "profile": tailored,
            "findings": checked.findings,
            "ats": ats,
        }

    async def _letter(
        self,
        tpl: LatexTemplate,
        master: ResumeProfile,
        tailored: ResumeProfile,
        job: JobPosting,
        job_dict: dict[str, Any],
        keywords: list[str],
        lang: Literal["fr", "en"],
        tone: Tone,
        app_id: int,
    ) -> dict[str, Any]:
        assert self.provider is not None
        plan, _ = await write_letter(
            self.provider, master, job_dict, lang=lang, tone=tone, tpm=self.tpm
        )
        job_text = f"{job.title}\n{job.company_name or ''}\n{job.location_raw or ''}\n" + (
            job.description or ""
        )
        kept, findings = validate_letter(
            master, [(p.text, p.sources) for p in plan.paragraphs], job_text, keywords
        )
        mapping = tpl.mapping or {}
        ctx = profile_context(tailored, lang)
        ctx["h"] = _headings(mapping, lang)
        ctx["letter"] = letter_context(job, plan, [p.text for p in kept], lang, tone)
        source = self._prepare(await self.template_source(tpl.id), mapping)
        result: CompileResult | None = None
        tex, error = "", None
        try:
            tex = render(source, ctx)
            result = await self.compile(tpl, tex)
            if not result.ok:
                error = _compile_error(result)
        except TemplateError as exc:
            error = str(exc)
        content = {
            "language": lang,
            "tone": tone,
            "subject": plan.subject,
            "paragraphs": [{"text": p.text, "sources": p.sources} for p in kept],
            "findings": [f.as_dict() for f in findings],
            "suspicious": plan.suspicious,
        }
        doc_id = await self._store(
            app_id, DocumentKind.COVER_LETTER, tpl, content, tex, result, error
        )
        pages_ok = bool(result and (result.pages or 0) <= LETTER_PAGES)
        return {
            "document_id": doc_id,
            "ok": error is None and pages_ok and bool(kept),
            "findings": findings,
        }

    def _ats(self, master: ResumeProfile, pdf: bytes | None, keywords: list[str]) -> dict[str, Any]:
        """Keyword coverage of the job's keywords: master profile vs tailored PDF text."""
        kws = [JobKeyword(term=k[:80]) for k in keywords]
        before = keyword_coverage(profile_text(master), kws)
        after: dict[str, Any] = {"coverage": None, "matched": [], "missing": []}
        if pdf is not None:
            try:
                after = keyword_coverage(extract(pdf, FileKind.PDF).text, kws)
            except Exception:
                log.warning("could not read the generated PDF back for the ATS check")
        return {
            "before": before["coverage"],
            "after": after["coverage"],
            "matched": [m["term"] for m in after["matched"]],
            "missing": [m["term"] for m in after["missing"]],
        }

    async def _store(
        self,
        app_id: int,
        kind: DocumentKind,
        tpl: LatexTemplate,
        content: dict[str, Any],
        tex: str,
        result: CompileResult | None,
        error: str | None,
    ) -> int:
        async with self.sm() as session:
            version = (
                await session.scalar(
                    select(func.coalesce(func.max(GeneratedDocument.version), 0)).where(
                        GeneratedDocument.application_id == app_id,
                        GeneratedDocument.kind == kind,
                    )
                )
                or 0
            ) + 1
            rel_dir = Path(DOCS_DIR) / str(app_id)
            out_dir = self.settings.storage_dir / rel_dir
            out_dir.mkdir(parents=True, exist_ok=True)
            stem = f"{kind.value}_v{version}"
            (out_dir / f"{stem}.tex").write_text(tex, encoding="utf-8")
            pdf_rel: str | None = None
            if result is not None and result.ok and result.pdf:
                (out_dir / f"{stem}.pdf").write_bytes(result.pdf)
                pdf_rel = (rel_dir / f"{stem}.pdf").as_posix()
            doc = GeneratedDocument(
                application_id=app_id,
                kind=kind,
                template_id=tpl.id,
                version=version,
                content={
                    **content,
                    "error": error,
                    "errors": result.errors if result else [],
                },
                tex_source_path=(rel_dir / f"{stem}.tex").as_posix(),
                pdf_path=pdf_rel,
                compile_status=CompileStatus.SUCCESS if pdf_rel else CompileStatus.FAILED,
                compile_log=(result.log[-20_000:] if result else error),
                page_count=result.pages if result else None,
            )
            session.add(doc)
            await session.commit()
            return doc.id


def _headings(mapping: dict[str, Any], lang: str) -> dict[str, str]:
    return {k: v.get(lang) or v.get("en") or "" for k, v in (mapping.get("headings") or {}).items()}


def _check_dict(check: Any) -> dict[str, Any] | None:
    if check is None:
        return None
    return {
        "ratio": check.ratio,
        "ok": check.ok,
        "missing": check.missing,
        "extra": check.extra,
        "missing_numbers": check.missing_numbers,
    }


def _compile_error(result: CompileResult) -> str:
    return "; ".join([result.error or "compilation failed", *result.errors[:3]])


def letter_context(
    job: JobPosting, plan: LetterPlan, paragraphs: list[str], lang: str, tone: str
) -> dict[str, Any]:
    source = SOURCE_NAMES.get(job.source, job.source)
    return {
        "company": job.company_name or "",
        "position": job.title,
        "hiring_manager": "",
        "recipient": [x for x in (job.company_name, job.location_raw) if x],
        "opening": OPENINGS[(lang, tone)],
        "paragraphs": paragraphs,
        "closing": CLOSINGS[(lang, tone)],
        "source": (f"sur {source}" if lang == "fr" else f"on {source}") if source else "",
        "date": format_date(datetime.now(UTC).strftime("%Y-%m"), lang),
        "subject": plan.subject,
    }


def sample_letter(mapping: dict[str, Any], lang: str) -> dict[str, Any]:
    """Letter variables for a preview: the converted letter's own text, else a placeholder."""
    letter = dict((mapping.get("extracted") or {}).get("letter") or {})
    defaults = {
        "company": "Exemplia",
        "position": "Data Engineer" if lang == "en" else "Ingénieur·e Data",
        "hiring_manager": "",
        "recipient": ["Exemplia", "Tunis"],
        "opening": OPENINGS[(lang, "formal")],
        "paragraphs": ["(preview) The tailored paragraphs appear here."],
        "closing": CLOSINGS[(lang, "formal")],
        "source": "",
        "date": format_date(datetime.now(UTC).strftime("%Y-%m"), lang),
        "subject": "",
    }
    return {**defaults, **{k: v for k, v in letter.items() if v}}


# re-exported for the API (used to validate uploads before a template row exists)
__all__ = ["DocumentFactory", "FactoryError", "Finding", "letter_context", "plain_profile"]
