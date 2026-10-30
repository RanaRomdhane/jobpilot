"""Upload -> worker pipeline (scripted LLM) -> master profile -> ATS reports, via the real API."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from jobpilot_core.config import Settings
from jobpilot_core.tasks import TaskReporter
from jobpilot_security import InjectionDetector
from jobpilot_worker.resume import ResumePipeline
from tests import cv_samples as s
from tests.unit.test_resume_agents import ScriptedProvider

from .conftest import TEST_PASSWORD

pytestmark = pytest.mark.integration

PARSED = {
    "language": "fr",
    "contact": {"full_name": "Camille Martin", "email": "camille.martin@example.com"},
    "headline": "Ingénieure logiciel backend",
    "experiences": [
        {
            "company": "Exemplia SAS",
            "title": "Ingénieure backend",
            "start": "2021-03",
            "current": True,
            "bullets": [
                "Réduit la latence p95 de 40 % en introduisant un cache Redis.",
                "Encadré 3 développeurs juniors et mis en place les revues de code.",
            ],
        }
    ],
    "education": [{"institution": "École Exemple", "degree": "Diplôme d'ingénieur"}],
    "skills": [{"name": "Outils", "kind": "tool", "skills": ["Docker", "PostgreSQL"]}],
    "injection_suspected": False,
}
REVIEW = {"summary": "Solide.", "content_score": 70, "issues": [], "bullet_rewrites": []}


@pytest.fixture
async def ctx(
    api_settings: Settings, real_redis: Redis, engine: AsyncEngine, tmp_path: Path
) -> AsyncIterator[dict[str, Any]]:
    from jobpilot_api.main import create_app

    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE resume, ats_report, task_run RESTART IDENTITY CASCADE"))
    settings = api_settings.model_copy(update={"storage_dir": tmp_path})
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post("/api/v1/auth/login", json={"password": TEST_PASSWORD})
            c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
            yield {
                "client": c,
                "settings": settings,
                "sm": async_sessionmaker(engine, expire_on_commit=False),
                "redis": real_redis,
            }


async def upload(c: httpx.AsyncClient, data: bytes, name: str = "cv.pdf") -> dict[str, Any]:
    r = await c.post("/api/v1/resume/uploads", files={"file": (name, data, "application/pdf")})
    assert r.status_code == 201, r.text
    body: dict[str, Any] = r.json()
    return body


def pipeline(ctx: dict[str, Any], task_id: str, *replies: Any) -> tuple[ResumePipeline, Any]:
    provider = ScriptedProvider(*replies)
    rep = TaskReporter(ctx["sm"], ctx["redis"], uuid.UUID(task_id))
    return (
        ResumePipeline(ctx["settings"], ctx["sm"], provider, InjectionDetector(), rep),  # type: ignore[arg-type]
        provider,
    )


async def test_upload_is_validated_and_stored_under_a_random_name(ctx: dict[str, Any]) -> None:
    c = ctx["client"]
    bad = await c.post("/api/v1/resume/uploads", files={"file": ("cv.pdf", b"MZ...", "a/b")})
    assert bad.status_code == 415 and bad.json()["detail"] == "upload_unsupported_type"

    up = await upload(c, s.clean_pdf(), name="../../Mon CV.pdf")
    assert up["resume"]["original_filename"] == "Mon CV.pdf"
    assert up["resume"]["status"] == "uploaded"
    files = list((ctx["settings"].storage_dir / "resumes").iterdir())
    assert len(files) == 1 and len(files[0].stem) == 32 and files[0].suffix == ".pdf"
    task = (await c.get(f"/api/v1/tasks/{up['task_id']}")).json()
    assert task["kind"] == "parse_resume"

    dl = await c.get(f"/api/v1/resume/uploads/{up['resume']['id']}/file")
    assert dl.headers["content-disposition"].startswith("attachment")
    assert dl.headers["x-content-type-options"] == "nosniff"
    assert dl.content == files[0].read_bytes()


async def test_parse_review_master_and_job_report(ctx: dict[str, Any]) -> None:
    c = ctx["client"]
    up = await upload(c, s.clean_pdf())
    rid = up["resume"]["id"]
    pipe, provider = pipeline(ctx, up["task_id"], PARSED, REVIEW)
    result = await pipe.process(rid)
    assert result["status"] == "parsed" and result["ats_report_id"]
    assert len(provider.calls) == 2  # one parse call, one review call

    detail = (await c.get(f"/api/v1/resume/uploads/{rid}")).json()
    assert detail["status"] == "parsed" and detail["injection_flag"] is False
    assert detail["latest_score"] == result["score"]
    assert detail["warnings"] == []  # every parsed value exists in the document
    exp = detail["parsed"]["experiences"][0]
    assert exp["id"].startswith("exp_") and exp["bullets"][0]["id"].startswith("b_")

    reports = (await c.get("/api/v1/ats/reports", params={"resume_id": rid})).json()
    assert len(reports) == 1 and reports[0]["breakdown"]["review"]["content_score"] == 70

    # master: ids survive, new items get one
    profile = detail["parsed"]
    profile["experiences"][0]["bullets"].append({"text": "Nouvelle réalisation ajoutée."})
    assert (await c.get("/api/v1/resume/master")).json() is None
    saved = (
        await c.put("/api/v1/resume/master", json={"profile": profile, "source_resume_id": rid})
    ).json()
    bullets = saved["profile"]["experiences"][0]["bullets"]
    assert bullets[0]["id"] == exp["bullets"][0]["id"] and bullets[2]["id"].startswith("b_")
    assert saved["sources"] == [rid]
    assert (await c.get("/api/v1/resume/uploads")).json()[0]["id"] == rid  # master not listed

    # a new report against a pasted posting is a worker task
    r = await c.post(
        "/api/v1/ats/reports",
        json={"resume_id": rid, "job_text": "Backend Python, FastAPI, Kubernetes."},
    )
    assert r.status_code == 202
    pipe, _ = pipeline(
        ctx,
        r.json()["task_id"],
        {
            **REVIEW,
            "job_title": "Backend",
            "job_keywords": [
                {"term": "FastAPI", "importance": "required"},
                {"term": "Kubernetes", "importance": "required"},
            ],
        },
    )
    report_id, _ = await pipe.ats(rid, job_text="Backend Python, FastAPI, Kubernetes.")
    rep = (await c.get(f"/api/v1/ats/reports/{report_id}")).json()
    assert rep["breakdown"]["keywords"]["coverage"] == 1.0  # both appear in the CV
    assert rep["breakdown"]["target"]["title"] == "Backend"

    # deleting the upload removes the file and its reports, never the master
    assert (await c.delete(f"/api/v1/resume/uploads/{rid}")).status_code == 204
    assert not list((ctx["settings"].storage_dir / "resumes").iterdir())
    assert (await c.get("/api/v1/ats/reports", params={"resume_id": rid})).json() == []
    assert (await c.get("/api/v1/resume/master")).json() is not None


async def test_hidden_instructions_are_flagged_and_never_sent_to_the_parser(
    ctx: dict[str, Any],
) -> None:
    c = ctx["client"]
    up = await upload(c, s.hidden_text_pdf())
    pipe, provider = pipeline(ctx, up["task_id"], PARSED, REVIEW)
    await pipe.process(up["resume"]["id"])
    detail = (await c.get(f"/api/v1/resume/uploads/{up['resume']['id']}")).json()
    assert detail["injection_flag"] is True
    assert any("Ignore all previous" in h for h in detail["hidden_excerpts"])
    sent = " ".join(m["content"] for call in provider.calls for m in call["messages"])
    assert "Ignore all previous instructions" not in sent and "terraform" not in sent
    report = (await c.get("/api/v1/ats/reports", params={"resume_id": up["resume"]["id"]})).json()
    assert "parsing.hidden_text" in {i["code"] for i in report[0]["issues"]}


async def test_unreadable_pdf_still_gets_its_ats_report(ctx: dict[str, Any]) -> None:
    c = ctx["client"]
    up = await upload(c, s.image_only_pdf())
    pipe, provider = pipeline(ctx, up["task_id"])
    result = await pipe.process(up["resume"]["id"])
    assert result["status"] == "failed" and result["error"] == "no_text"
    assert provider.calls == []  # no LLM call on an empty document
    report = (await c.get(f"/api/v1/ats/reports/{result['ats_report_id']}")).json()
    assert report["issues"][0]["code"] == "parsing.no_text" and report["score"] <= 35


async def test_active_content_pdf_is_deleted(ctx: dict[str, Any]) -> None:
    c = ctx["client"]
    up = await upload(c, s.javascript_pdf())
    pipe, _ = pipeline(ctx, up["task_id"])
    result = await pipe.process(up["resume"]["id"])
    assert result == {"status": "failed", "error": "active_content"}
    assert not list((ctx["settings"].storage_dir / "resumes").iterdir())
