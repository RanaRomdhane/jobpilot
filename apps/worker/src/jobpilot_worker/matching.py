"""Match jobs against the master profile: embeddings -> keyword overlap -> LLM re-rank.

1. vector similarity (e5, cosine) between the profile and each posting;
2. keyword overlap: which of the posting's concrete skills/tools the profile shows;
3. pre-score = 60 % vector + 40 % keywords, for every job;
4. the best N not-yet-reviewed jobs go to the JobMatcherAgent (after the guard model checked
   them for injection), which returns a score, a rationale and missing skills; the final score
   is the mean of the pre-score and the LLM score.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, or_, select

from jobpilot_agents.job_matcher import assess_jobs
from jobpilot_agents.structured import StructuredOutputError
from jobpilot_core.config import Settings
from jobpilot_core.models import JobMatch, JobPosting, Resume
from jobpilot_core.ratelimit import RateLimitExceededError
from jobpilot_core.resume import ResumeProfile
from jobpilot_jobs_sources.taxonomy import TECH_VOCAB
from jobpilot_resume.ats import Haystack
from jobpilot_security import InjectionDetector, clean_plain
from jobpilot_worker.jobs import load_preferences
from jobpilot_worker.resume import user_locale

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from jobpilot_core.llm import LLMProvider
    from jobpilot_core.tasks import TaskReporter
    from jobpilot_worker.embeddings import Embedder

log = logging.getLogger(__name__)

SIM_FLOOR, SIM_SPAN = 0.80, 0.12  # e5-small: unrelated ~0.80, close matches ~0.92
VECTOR_WEIGHT = 0.6


def profile_summary(p: ResumeProfile, limit: int = 3_000) -> str:
    parts = [p.headline or "", p.summary or ""]
    for e in p.experiences:
        parts.append(f"{e.title} @ {e.company}: " + " ".join(b.text for b in e.bullets[:4]))
    for x in p.projects:
        parts.append(f"{x.name}: {x.description or ''} " + " ".join(b.text for b in x.bullets[:2]))
    for g in p.skills:
        parts.append(f"{g.name}: {', '.join(g.skills)}")
    for ed in p.education:
        parts.append(f"{ed.degree or ''} {ed.field or ''} {ed.institution}")
    parts.append("Languages: " + ", ".join(lang.name for lang in p.languages))
    text = "\n".join(s for s in parts if s.strip())
    return text[:limit]


def profile_terms(p: ResumeProfile) -> list[str]:
    terms = [s for g in p.skills for s in g.skills]
    terms += [t for e in p.experiences for t in e.technologies]
    terms += [t for x in p.projects for t in x.technologies]
    seen: set[str] = set()
    out: list[str] = []
    for t in terms:
        key = t.strip().lower()
        if key and len(key) <= 40 and key not in seen:
            seen.add(key)
            out.append(t.strip())
    return out


def keyword_fit(
    job_text: str, terms: list[str], profile_hay: Haystack
) -> tuple[float, list[str], list[str]]:
    """(score 0..1, profile skills present in the job, job skills absent from the profile)."""
    hay = Haystack(job_text)
    matched = [t for t in terms if hay.has(t)]
    wanted = [t for t in TECH_VOCAB if hay.has(t)]
    if not wanted:
        return (0.5 if matched else 0.3), matched[:12], []
    covered = [t for t in wanted if profile_hay.has(t)]
    missing = [t for t in wanted if not profile_hay.has(t)]
    return len(covered) / len(wanted), matched[:12], missing[:8]


LEVELS = ("intern", "junior", "mid", "senior", "lead")
LEVEL_PENALTY = 0.15  # per level above what you are looking for


def level_factor(job_level: str | None, wanted: set[str]) -> float:
    """Jobs above the levels you target score lower; unknown levels and lower ones are untouched."""
    if not job_level or not wanted or job_level in wanted or job_level not in LEVELS:
        return 1.0
    top = max(LEVELS.index(w) for w in wanted if w in LEVELS)
    gap = LEVELS.index(job_level) - top
    return max(0.4, 1.0 - LEVEL_PENALTY * gap) if gap > 0 else 1.0


def vector_fit(similarity: float) -> float:
    return max(0.0, min(1.0, (similarity - SIM_FLOOR) / SIM_SPAN))


async def profile_row(session: AsyncSession) -> Resume | None:
    """The master profile, else the latest parsed upload (before the user saved a master)."""
    master = await session.scalar(select(Resume).where(Resume.is_master))
    if master is not None and master.parsed:
        return master
    row: Resume | None = await session.scalar(
        select(Resume)
        .where(Resume.status == "parsed", Resume.parsed.is_not(None))
        .order_by(Resume.id.desc())
        .limit(1)
    )
    return row


async def score_jobs(
    settings: Settings,
    sm: async_sessionmaker[AsyncSession],
    embedder: Embedder,
    *,
    rescore_all: bool = False,
) -> dict[str, Any]:
    """Pre-score every job that has no score for the current profile (or all, after an edit)."""
    async with sm() as session:
        prow = await profile_row(session)
        if prow is None:
            return {"skipped": "no_profile"}
        profile = ResumeProfile.model_validate(prow.parsed)
        qvec = (await embedder.aembed([profile_summary(profile)], "query"))[0].tolist()
        terms = profile_terms(profile)
        profile_hay = Haystack(profile_summary(profile, limit=20_000) + " " + " ".join(terms))
        wanted_levels: set[str] = set((await load_preferences(session)).seniority)

        existing = JobMatch.__table__.alias("m")
        dist = JobPosting.embedding.cosine_distance(qvec)
        stmt = (
            select(JobPosting, dist.label("dist"))
            .outerjoin(
                existing,
                (existing.c.job_id == JobPosting.id) & (existing.c.resume_id == prow.id),
            )
            .where(
                JobPosting.embedding.is_not(None),
                JobPosting.hidden.is_(False),
                func.coalesce(JobPosting.posted_at, JobPosting.scraped_at)
                >= datetime.now(UTC) - timedelta(days=settings.jobs_max_age_days),
            )
        )
        if not rescore_all:
            stmt = stmt.where(or_(existing.c.id.is_(None), existing.c.updated_at < prow.updated_at))
        rows = (await session.execute(stmt)).all()
        matches = {
            m.job_id: m
            for m in (
                await session.scalars(
                    select(JobMatch).where(
                        JobMatch.resume_id == prow.id,
                        JobMatch.job_id.in_([r[0].id for r in rows]),
                    )
                )
            ).all()
        }
        for job, d in rows:
            v = vector_fit(1.0 - float(d))
            k, matched, missing = keyword_fit(
                f"{job.title}\n{job.description or ''}", terms, profile_hay
            )
            pre = VECTOR_WEIGHT * v + (1 - VECTOR_WEIGHT) * k
            pre *= level_factor(job.seniority, wanted_levels)
            m = matches.get(job.id) or JobMatch(job_id=job.id, resume_id=prow.id)
            m.vector_score = round(v, 4)
            m.keyword_score = round(k, 4)
            m.matched_keywords = matched
            m.missing_keywords = missing
            m.llm_score = None
            m.rationale = None
            m.score = round(100 * pre, 1)
            session.add(m)
        await session.commit()
        return {"scored": len(rows), "resume_id": prow.id}


async def rerank(
    settings: Settings,
    sm: async_sessionmaker[AsyncSession],
    provider: LLMProvider,
    detector: InjectionDetector,
    reporter: TaskReporter | None = None,
    *,
    job_ids: list[int] | None = None,
    locale: str | None = None,
) -> dict[str, Any]:
    """LLM re-rank of the best not-yet-reviewed jobs (or of `job_ids`)."""
    top_n = settings.match_rerank_top_n
    if top_n == 0 and not job_ids:
        return {"reranked": 0}
    async with sm() as session:
        prow = await profile_row(session)
        if prow is None:
            return {"skipped": "no_profile"}
        locale = locale if locale in ("fr", "en") else await user_locale(session)
        stmt = (
            select(JobMatch, JobPosting)
            .join(JobPosting, JobPosting.id == JobMatch.job_id)
            .where(JobMatch.resume_id == prow.id, JobPosting.hidden.is_(False))
        )
        if job_ids:
            stmt = stmt.where(JobPosting.id.in_(job_ids))
        else:
            stmt = (
                stmt.where(JobMatch.llm_score.is_(None), JobPosting.injection_flag.is_(False))
                .order_by(JobMatch.score.desc())
                .limit(top_n)
            )
        pairs = (await session.execute(stmt)).all()
        summary = profile_summary(ResumeProfile.model_validate(prow.parsed), limit=2_000)

        # the guard model checks each posting before an LLM reads it
        safe: list[tuple[JobMatch, JobPosting]] = []
        for m, job in pairs:
            report = await detector.scan(clean_plain(job.description or ""), extra_text=job.title)
            if report.flagged:
                job.injection_flag = True
                job.injection_score = report.score
                job.injection_reasons = [r.model_dump() for r in report.reasons]
                continue
            safe.append((m, job))
        await session.commit()

        done = 0
        batch_size = settings.match_rerank_batch
        for start in range(0, len(safe), batch_size):
            chunk = safe[start : start + batch_size]
            if reporter is not None:
                await reporter.progress(
                    0.8 + 0.18 * start / max(1, len(safe)), "re-ranking the best matches"
                )
            try:
                results, _ = await assess_jobs(
                    provider,
                    summary,
                    [
                        {
                            "title": j.title,
                            "company_name": j.company_name,
                            "location_raw": j.location_raw,
                            "work_mode": j.work_mode.value if j.work_mode else None,
                            "description": j.description,
                        }
                        for _, j in chunk
                    ],
                    locale=locale,
                )
            except (StructuredOutputError, RateLimitExceededError) as exc:
                log.warning("re-rank batch failed: %s", exc)
                continue
            for i, (m, job) in enumerate(chunk):
                a = results.get(i)
                if a is None:
                    continue
                if a.suspicious:
                    job.injection_flag = True
                    continue
                pre = m.score
                m.llm_score = float(a.score)
                m.score = round(0.5 * pre + 0.5 * a.score, 1)
                m.rationale = a.rationale
                extra = [s for s in a.missing_skills if s not in m.missing_keywords]
                m.missing_keywords = [*m.missing_keywords, *extra][:10]
                done += 1
            await session.commit()
        return {"reranked": done, "flagged": len(pairs) - len(safe)}
