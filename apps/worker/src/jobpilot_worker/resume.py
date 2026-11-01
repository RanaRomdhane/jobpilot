"""CV pipeline: extract -> injection scan -> parse (ResumeParserAgent) -> grounding -> ATS report.

Each step degrades gracefully: an unreadable file still gets its ATS report (that is the main
finding), and without an LLM the report falls back to the deterministic rules.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from jobpilot_agents.ats_review import review_resume
from jobpilot_agents.resume_parser import ParseTooLongError, parse_resume_text
from jobpilot_agents.structured import StructuredOutputError
from jobpilot_core.config import Settings
from jobpilot_core.models import ATSReport, Resume, UserProfile
from jobpilot_core.ratelimit import RateLimitExceededError
from jobpilot_core.resume import ResumeProfile
from jobpilot_resume import (
    ExtractedDocument,
    FileKind,
    LayoutSignals,
    UploadRejectedError,
    build_report,
    extract,
    grounding_warnings,
    llm_text,
    reflow,
)
from jobpilot_resume.grounding import missing_lines
from jobpilot_resume.storage import resume_path
from jobpilot_security import CleanText, InjectionDetector, clean_plain

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from jobpilot_core.llm import ChatResult, LLMProvider
    from jobpilot_core.tasks import TaskReporter

log = logging.getLogger(__name__)

MIN_TEXT_CHARS = 200
HIDDEN_KEEP = 20


def stored_document(row: Resume) -> ExtractedDocument:
    """Rebuild the extraction from the row, so a new ATS run never re-opens the file."""
    ex = row.extraction or {}
    return ExtractedDocument(
        kind=FileKind.PDF if row.mime == "application/pdf" else FileKind.DOCX,
        text=row.raw_text or "",
        hidden=list(ex.get("hidden", [])),
        signals=LayoutSignals.model_validate(ex.get("signals", {})),
    )


def _usage(results: list[ChatResult]) -> dict[str, Any]:
    return {
        "llm_calls": len(results),
        "input_tokens": sum(r.input_tokens for r in results),
        "output_tokens": sum(r.output_tokens for r in results),
        "model": results[-1].model if results else None,
    }


async def user_locale(session: AsyncSession) -> str:
    locale = await session.scalar(select(UserProfile.locale).limit(1))
    return locale if locale in ("fr", "en") else "fr"


class ResumePipeline:
    def __init__(
        self,
        settings: Settings,
        sessionmaker: async_sessionmaker[AsyncSession],
        provider: LLMProvider | None,
        detector: InjectionDetector,
        reporter: TaskReporter,
    ) -> None:
        self.settings = settings
        self.sm = sessionmaker
        self.provider = provider
        self.detector = detector
        self.rep = reporter

    @property
    def tpm(self) -> int:
        return self.settings.limits_for(self.provider.name)[0] if self.provider else 0

    async def process(self, resume_id: int, *, locale: str | None = None) -> dict[str, Any]:
        async with self.sm() as session:
            row = await session.get(Resume, resume_id)
            if row is None or row.file_path is None:
                raise LookupError(f"resume {resume_id} not found")
            row.status, row.error = "processing", None
            await session.commit()
            path = resume_path(self.settings.storage_dir, row.file_path)
            kind = FileKind.PDF if row.mime == "application/pdf" else FileKind.DOCX

            await self.rep.progress(0.05, "extracting text")
            try:
                data = await asyncio.to_thread(path.read_bytes)
                doc = await asyncio.wait_for(
                    asyncio.to_thread(extract, data, kind),
                    timeout=self.settings.resume_extract_timeout_s,
                )
            except UploadRejectedError as exc:
                row.status, row.error = "failed", exc.code
                if exc.code == "active_content":
                    path.unlink(missing_ok=True)  # never keep a file we refused to open
                    row.file_path = None
                await session.commit()
                return {"status": "failed", "error": exc.code}
            except TimeoutError:
                row.status, row.error = "failed", "extract_timeout"
                await session.commit()
                return {"status": "failed", "error": "extract_timeout"}

            row.raw_text = doc.text
            row.page_count = doc.signals.page_count
            row.extraction = {
                "signals": doc.signals.model_dump(),
                "hidden": [h[:500] for h in doc.hidden[:HIDDEN_KEEP]],
                "word_count": doc.word_count,
            }

            await self.rep.progress(0.15, "scanning for prompt injection")
            scan = await self.detector.scan(CleanText(text=doc.text, hidden=doc.hidden))
            row.injection_flag = scan.flagged
            row.injection_score = scan.score
            row.injection_reasons = [r.model_dump() for r in scan.reasons]
            await session.commit()

            profile: ResumeProfile | None = None
            if len(doc.text.strip()) < MIN_TEXT_CHARS:
                row.status, row.error = "failed", "no_text"
            elif self.provider is None:
                row.status, row.error = "failed", "llm_unavailable"
            else:
                await self.rep.progress(0.3, "reading the CV (LLM)")
                try:
                    outcome = await parse_resume_text(self.provider, llm_text(doc), tpm=self.tpm)
                except (ParseTooLongError, StructuredOutputError, RateLimitExceededError) as exc:
                    log.warning("resume %s: parse failed: %s", resume_id, exc)
                    row.status = "failed"
                    row.error = "too_long" if isinstance(exc, ParseTooLongError) else "parse_failed"
                else:
                    profile = outcome.profile
                    if outcome.injection_suspected:
                        report = scan.merged_with(
                            self.detector.model_report("the parser reported text aimed at an AI"),
                            self.detector.threshold,
                        )
                        row.injection_flag = report.flagged
                        row.injection_score = report.score
                        row.injection_reasons = [r.model_dump() for r in report.reasons]
                    row.parsed = profile.model_dump()
                    row.parse_meta = {
                        **_usage(outcome.usage),
                        "passes": outcome.passes,
                        "truncated": outcome.truncated,
                        "warnings": grounding_warnings(profile, doc.text),
                        "missing_lines": missing_lines(profile, reflow(doc.text)),
                    }
                    row.status, row.parsed_at = "parsed", datetime.now(UTC)
            await session.commit()
            status, error = row.status, row.error

        await self.rep.progress(0.7, "building the ATS report")
        report_id, score = await self.ats(resume_id, locale=locale)
        return {"status": status, "error": error, "ats_report_id": report_id, "score": score}

    async def ats(
        self,
        resume_id: int,
        *,
        job_text: str | None = None,
        job_title: str | None = None,
        locale: str | None = None,
    ) -> tuple[int, int]:
        async with self.sm() as session:
            row = await session.get(Resume, resume_id)
            if row is None:
                raise LookupError(f"resume {resume_id} not found")
            doc = stored_document(row)
            profile = ResumeProfile.model_validate(row.parsed) if row.parsed else None
            if locale not in ("fr", "en"):
                locale = await user_locale(session)

        target: dict[str, Any] | None = None
        if job_text:
            scan = await self.detector.scan(clean_plain(job_text), extra_text=job_title or "")
            target = {
                "title": job_title,
                "excerpt": job_text[:280],
                "job_text": job_text,  # kept so the report can be regenerated (e.g. in FR)
                "chars": len(job_text),
                "injection_flag": scan.flagged,
            }

        review = None
        review_error = None
        if self.provider is not None and len(doc.text.strip()) >= MIN_TEXT_CHARS:
            try:
                review, _ = await review_resume(
                    self.provider,
                    llm_text(doc),
                    locale=locale,
                    job_text=job_text,
                    job_title=job_title,
                )
            except (StructuredOutputError, RateLimitExceededError) as exc:
                log.warning("resume %s: ATS review failed: %s", resume_id, exc)
                review_error = "review_failed"
        if target is not None and review is not None:
            target["title"] = job_title or review.job_title
            if review.injection_suspected:
                target["injection_flag"] = True

        score, breakdown, issues = build_report(doc, profile=profile, review=review, target=target)
        breakdown["locale"] = locale
        breakdown["review_error"] = review_error
        async with self.sm() as session:
            report = ATSReport(resume_id=resume_id, score=score, breakdown=breakdown, issues=issues)
            session.add(report)
            await session.commit()
            return report.id, score
