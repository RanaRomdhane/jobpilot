"""Application factory end to end on the test database (synthetic data, scripted LLM, fake TeX):
template upload -> confirm -> generate (tailor + guardrail + page fitting + letter) -> API."""

from __future__ import annotations

import io
import zipfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import fitz  # PyMuPDF
import httpx
import pytest
from redis.asyncio import Redis
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from jobpilot_core.config import Settings
from jobpilot_core.enums import ApplicationStatus, CompileStatus, TemplateKind
from jobpilot_core.models import Application, GeneratedDocument, JobPosting, LatexTemplate, Resume
from jobpilot_latex import templates as tfiles
from jobpilot_latex.compile import CompileResult
from jobpilot_worker import documents as docmod
from jobpilot_worker.documents import DocumentFactory, FactoryError
from tests.unit.test_resume_agents import ScriptedProvider

from .conftest import TEST_PASSWORD

pytestmark = pytest.mark.integration

MASTER: dict[str, Any] = {
    "language": "en",
    "contact": {"full_name": "Ada Example", "email": "ada@example.com"},
    "headline": "Machine Learning Engineer",
    "summary": "ML engineer building NLP products with Python.",
    "experiences": [
        {
            "id": "exp_1",
            "company": "Exemplia Labs",
            "title": "ML Engineer Intern",
            "start": "2025-06",
            "end": "2025-09",
            "bullets": [
                {"id": "b_1", "text": "Built a BERT model reaching 73% accuracy on 4,000+ tasks"},
                {"id": "b_2", "text": "Automated reviews, cutting review time by 35%"},
            ],
        }
    ],
    "projects": [
        {
            "id": "prj_1",
            "name": "Vision demo",
            "bullets": [{"id": "b_3", "text": "Fine-tuned GPT-2 with Adam to 91.25% accuracy"}],
        }
    ],
    "education": [{"id": "edu_1", "institution": "Exemplia School", "degree": "MSc"}],
    "skills": [{"id": "skl_1", "name": "Code", "skills": ["Python", "SQL", "PyTorch"]}],
}

CV_TEMPLATE = r"""\documentclass{article}
\begin{document}
\textbf{\VAR{name}} -- \VAR{headline}
\section{\VAR{h.experience}}
\BLOCK{for e in experiences}
\textbf{\VAR{e.title}} -- \VAR{e.company} (\VAR{e|daterange(lang, ' -- ')})
\begin{itemize}
\BLOCK{for b in e.bullets}
  \item \VAR{b.text|rich}
\BLOCK{endfor}
\end{itemize}
\BLOCK{endfor}
\BLOCK{for p in projects}
\textbf{\VAR{p.name}}: \BLOCK{for b in p.bullets}\VAR{b.text|rich} \BLOCK{endfor}
\BLOCK{endfor}
Skills: \VAR{skills_flat|ljoin(', ')}
\newpage
\end{document}
"""

LETTER_TEMPLATE = r"""\documentclass{letter}
\begin{document}
\VAR{letter.company} -- \VAR{letter.position} -- \VAR{contact.email}
\VAR{letter.opening}
\BLOCK{for p in letter.paragraphs}
\VAR{p|rich}

\BLOCK{endfor}
\VAR{letter.closing}
\end{document}
"""

PLAN = {
    "language": "en",
    "headline": "AI Engineer",
    "summary": {"text": "ML engineer building NLP products with Python.", "sources": []},
    "experiences": [
        {
            "id": "exp_1",
            "bullets": [
                {
                    "source": "b_1",
                    "text": "Built a **BERT** model with 73% accuracy on 4,000+ tasks",
                },
                # invented metric: reverted to the master wording by the guardrail
                {"source": "b_2", "text": "Cut review time by 90% by automating reviews"},
            ],
        }
    ],
    "projects": [
        {
            "id": "prj_1",
            "bullets": [
                {"source": "b_3", "text": "Fine-tuned GPT-2 with Adam to 91.25% accuracy"}
            ],
        }
    ],
    "education": ["edu_1"],
    "skills": [{"id": "skl_1", "skills": ["Python", "PyTorch", "Kubernetes"]}],
    "drop_first": ["b_3"],
}

LETTER = {
    "language": "en",
    "subject": "Application: ML Engineer",
    "paragraphs": [
        {"text": "I am applying for the ML Engineer role at Exemplia Gulf.", "sources": []},
        {
            "text": "I built a BERT model reaching 73% accuracy on 4,000+ tasks. "
            "I also run Kubernetes clusters in production.",
            "sources": ["b_1"],
        },
    ],
}


def pdf_with(text_: str, pages: int) -> bytes:
    doc = fitz.open()
    for _ in range(pages):
        doc.new_page().insert_text((72, 72), text_[:1500])
    return bytes(doc.tobytes())


class FakeTeX:
    """Stands in for the sandbox: 2 pages while the project is in the CV, else 1."""

    def __init__(self) -> None:
        self.sources: list[str] = []

    async def __call__(self, spool: Path, source: str, **kw: Any) -> CompileResult:
        self.sources.append(source)
        pages = 2 if "GPT-2" in source and "letter" not in source[:40] else 1
        return CompileResult(ok=True, pdf=pdf_with(source, pages), pages=pages, log="ok")


@pytest.fixture
async def ctx(
    api_settings: Settings, real_redis: Redis, engine: AsyncEngine, tmp_path: Path
) -> AsyncIterator[dict[str, Any]]:
    from jobpilot_api.main import create_app

    settings = api_settings.model_copy(update={"storage_dir": tmp_path})
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE job_posting, job_match, application, generated_document, latex_template, "
                "resume, task_run RESTART IDENTITY CASCADE"
            )
        )
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as s:
        s.add(Resume(is_master=True, status="master", parsed=MASTER, parse_meta={}))
        s.add(
            JobPosting(
                source="remotive",
                external_id="j1",
                title="ML Engineer",
                company_name="Exemplia Gulf",
                language="en",
                description="We need Python, PyTorch and Kubernetes for 6 months.",
                dedup_hash="h1",
                apply_email="jobs@example.com",
            )
        )
        await s.commit()
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post("/api/v1/auth/login", json={"password": TEST_PASSWORD})
            c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
            yield {"client": c, "settings": settings, "sm": sm}


def zip_of(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, body in files.items():
            zf.writestr(f"Exported/{name}", body)
    return buf.getvalue()


async def upload(c: httpx.AsyncClient, kind: str, files: list[tuple[str, bytes]]) -> dict[str, Any]:
    r = await c.post(
        "/api/v1/templates",
        data={"kind": kind},
        files=[("files", (n, b, "application/octet-stream")) for n, b in files],
    )
    assert r.status_code == 201, r.text
    out: dict[str, Any] = r.json()
    return out


async def install(c: httpx.AsyncClient, settings: Settings, kind: str, tid: int, src: str) -> None:
    """What the converter would have produced, saved through the API (and confirmed)."""
    assert (await c.put(f"/api/v1/templates/{tid}/source", json={"source": src})).status_code == 200
    body = {
        "page_budget": 1,
        "remove_page_breaks": True,
        "headings": {"experience": {"en": "Experience", "fr": "Expérience"}},
    }
    confirmed = (await c.post(f"/api/v1/templates/{tid}/confirm", json=body)).json()
    assert confirmed["mapping_confirmed"] and confirmed["is_default"]


async def test_upload_validates_and_unpacks(ctx: dict[str, Any]) -> None:
    c = ctx["client"]
    bad = await c.post(
        "/api/v1/templates",
        data={"kind": "cv"},
        files=[("files", ("notes.txt", b"hello", "text/plain"))],
    )
    assert bad.status_code == 422
    out = await upload(
        c,
        "cover_letter",
        [
            (
                "letter.zip",
                zip_of({"cover_letter.tex": LETTER_TEMPLATE, "style.sty": "%", "README.md": "x"}),
            )
        ],
    )
    tpl = out["template"]
    assert tpl["main"] == "cover_letter.tex" and tpl["files"] == ["cover_letter.tex", "style.sty"]
    assert tpl["engine"] == "pdflatex" and not tpl["mapping_confirmed"]
    detail = (await c.get(f"/api/v1/templates/{tpl['id']}")).json()
    assert detail["original"].startswith(r"\documentclass{letter}") and detail["source"] is None
    # a zip trying to escape its folder is refused
    evil = io.BytesIO()
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("../../etc/x.tex", r"\documentclass{article}\begin{document}\end{document}")
    r = await c.post(
        "/api/v1/templates", data={"kind": "cv"}, files=[("files", ("e.zip", evil.getvalue(), "x"))]
    )
    assert r.status_code == 422
    bad_src = await c.put(
        f"/api/v1/templates/{tpl['id']}/source", json={"source": r"\BLOCK{for x}"}
    )
    assert bad_src.status_code == 422


async def test_generate_tailors_fits_and_guards(
    ctx: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    c, settings, sm = ctx["client"], ctx["settings"], ctx["sm"]
    cv = await upload(c, "cv", [("main.tex", CV_TEMPLATE.encode())])
    letter = await upload(c, "cover_letter", [("cover_letter.tex", LETTER_TEMPLATE.encode())])
    factory = DocumentFactory(settings, sm, ScriptedProvider(PLAN, LETTER))  # type: ignore[arg-type]
    with pytest.raises(FactoryError) as exc:
        await factory.generate(1)
    assert exc.value.code == "template_not_ready"

    await install(c, settings, "cv", cv["template"]["id"], CV_TEMPLATE)
    await install(c, settings, "cover_letter", letter["template"]["id"], LETTER_TEMPLATE)
    tex = FakeTeX()
    monkeypatch.setattr(docmod, "compile_tex", tex)
    result = await factory.generate(1, locale="fr")
    assert result["ok"] and result["language"] == "en"  # the posting's language wins

    async with sm() as s:
        app = await s.scalar(select(Application))
        docs = (await s.scalars(select(GeneratedDocument).order_by(GeneratedDocument.kind))).all()
    assert app is not None and app.status is ApplicationStatus.READY_FOR_REVIEW
    assert app.contact_email == "jobs@example.com"
    cv_doc = next(d for d in docs if d.kind.value == "cv")
    content = cv_doc.content or {}
    # page fitting: the 2-page render dropped the project bullet the plan offered first
    assert content["dropped_for_length"] == ["b_3"] and cv_doc.page_count == 1
    assert "\\newpage" not in tex.sources[0]  # confirmed: forced breaks removed
    codes = {f["code"] for f in content["findings"]}
    assert {"new_number", "new_skill"} <= codes
    bullets = content["profile"]["experiences"][0]["bullets"]
    assert bullets[1]["text"] == MASTER["experiences"][0]["bullets"][1]["text"]  # reverted
    assert r"\textbf{BERT}" in tex.sources[-2] and "Kubernetes" not in tex.sources[-2]
    assert content["ats"]["after"] is not None
    assert cv_doc.compile_status is CompileStatus.SUCCESS

    letter_doc = next(d for d in docs if d.kind.value == "cover_letter")
    lc = letter_doc.content or {}
    assert [p["text"] for p in lc["paragraphs"]][1] == (
        "I built a BERT model reaching 73% accuracy on 4,000+ tasks."
    )
    assert "Dear Hiring Manager," in tex.sources[-1] and "ada@example.com" in tex.sources[-1]

    # API: versions, detail, PDF with the expected file name
    listing = (await c.get("/api/v1/documents/by-job/1")).json()
    assert listing["status"] == "ready_for_review" and len(listing["documents"]) == 2
    detail = (await c.get(f"/api/v1/documents/{cv_doc.id}")).json()
    assert detail["file_name"] == "CV_Ada_Example_Exemplia_Gulf.pdf" and detail["findings"] >= 2
    pdf = await c.get(f"/api/v1/documents/{cv_doc.id}/pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")

    # a second run is a new version, the first is kept
    factory2 = DocumentFactory(settings, sm, ScriptedProvider(PLAN, LETTER))  # type: ignore[arg-type]
    await factory2.generate(1)
    async with sm() as s:
        versions = (await s.scalars(select(GeneratedDocument.version))).all()
    assert sorted(versions) == [1, 1, 2, 2]


async def test_flagged_job_needs_confirmation(ctx: dict[str, Any]) -> None:
    c, sm = ctx["client"], ctx["sm"]
    async with sm() as s:
        job = await s.get(JobPosting, 1)
        assert job is not None
        job.injection_flag = True
        await s.commit()
    r = await c.post("/api/v1/documents/generate", json={"job_id": 1})
    assert r.status_code == 409
    ok = await c.post("/api/v1/documents/generate", json={"job_id": 1, "confirm_flagged": True})
    assert ok.status_code == 202


async def test_templates_are_confined_to_their_folder(ctx: dict[str, Any]) -> None:
    settings: Settings = ctx["settings"]
    root = tfiles.template_root(settings.storage_dir, 7)
    assert root.parent == (settings.storage_dir / "templates").resolve()
    async with ctx["sm"]() as s:
        assert await s.scalar(select(LatexTemplate.id)) is None
    assert TemplateKind.CV.value == "cv"


def test_unicode_compile_error_is_fixed_once() -> None:
    from jobpilot_worker.documents import replace_chars, unsupported_chars

    failed = CompileResult(
        ok=False,
        pdf=None,
        pages=None,
        errors=["LaTeX Error: Unicode character ① (U+2460) not set up for use with LaTeX."],
        log="! LaTeX Error: Unicode character 🚀 (U+1F680)\nnot set up",
    )
    bad = unsupported_chars(failed)
    assert bad == {"①", "🚀"}
    assert replace_chars("step ① launch 🚀 ok", bad) == "step 1 launch  ok"
