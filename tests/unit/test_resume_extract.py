"""Upload validation, extraction signals and the deterministic ATS rules, on synthetic CVs."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from jobpilot_core.resume import ResumeProfile, ensure_ids
from jobpilot_resume import (
    FileKind,
    JobKeyword,
    Review,
    UploadRejectedError,
    build_report,
    extract,
    grounding_warnings,
    validate_upload,
)
from jobpilot_resume.ats import (
    ReviewIssue,
    date_formats,
    find_sections,
    keyword_coverage,
    rule_issues,
)
from jobpilot_resume.storage import resume_path
from jobpilot_resume.upload import display_name
from tests import cv_samples as s

MB = 1024 * 1024


def codes(doc_bytes: bytes, kind: FileKind) -> set[str]:
    issues, _ = rule_issues(extract(doc_bytes, kind), None)
    return {i.code for i in issues}


# ---------------------------------------------------------------- upload validation
def test_accepts_pdf_and_docx_by_content_not_name() -> None:
    assert validate_upload(s.clean_pdf(), "cv.docx", max_bytes=MB).kind is FileKind.PDF
    up = validate_upload(s.clean_docx(), "../../etc/cv.pdf", max_bytes=MB)
    assert up.kind is FileKind.DOCX and up.display_name == "cv.pdf"


@pytest.mark.parametrize(
    ("data", "code"),
    [
        (b"", "empty"),
        (b"MZ\x90\x00 not a cv", "unsupported_type"),
        (b"%PDF-1.7\n truncated", "corrupt"),
        (b"PK\x03\x04 broken zip", "corrupt"),
    ],
)
def test_rejects_bad_files(data: bytes, code: str) -> None:
    with pytest.raises(UploadRejectedError) as exc:
        validate_upload(data, "x", max_bytes=MB)
    assert exc.value.code == code


def test_rejects_too_large() -> None:
    with pytest.raises(UploadRejectedError) as exc:
        validate_upload(s.clean_pdf(), "cv.pdf", max_bytes=100)
    assert exc.value.code == "too_large"


def _rezip(src: bytes, edit: dict[str, bytes]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(src)) as zin, zipfile.ZipFile(out, "w") as zout:
        for item in zin.infolist():
            zout.writestr(item, edit.pop(item.filename, zin.read(item.filename)))
        for name, data in edit.items():
            zout.writestr(name, data)
    return out.getvalue()


def test_rejects_macro_documents_and_zip_bombs() -> None:
    docx = s.clean_docx()
    with zipfile.ZipFile(io.BytesIO(docx)) as zf:
        types = zf.read("[Content_Types].xml")
    docm = _rezip(
        docx,
        {
            "[Content_Types].xml": types.replace(
                b"wordprocessingml.document.main+xml",
                b"ms-word.document.macroEnabled.main+xml",
            )
        },
    )
    with pytest.raises(UploadRejectedError, match=r"not a \.docx"):
        validate_upload(docm, "cv.docm", max_bytes=MB)
    with pytest.raises(UploadRejectedError) as exc:
        validate_upload(_rezip(docx, {"word/vbaProject.bin": b"x"}), "cv.docx", max_bytes=MB)
    assert exc.value.code == "active_content"

    bomb = io.BytesIO()
    with zipfile.ZipFile(bomb, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", types)
        zf.writestr("word/document.xml", b"0" * 5_000_000)
    with pytest.raises(UploadRejectedError, match="compression ratio"):
        validate_upload(bomb.getvalue(), "cv.docx", max_bytes=MB)


def test_display_name_and_storage_path_are_contained(tmp_path: Path) -> None:
    assert display_name("C:\\Users\\x\\CV\x00 final.pdf", FileKind.PDF) == "CV final.pdf"
    assert display_name("", FileKind.DOCX) == "cv.docx"
    assert resume_path(tmp_path, "resumes/a.pdf").parent == (tmp_path / "resumes").resolve()
    with pytest.raises(ValueError, match="escapes"):
        resume_path(tmp_path, "../outside.pdf")


# ---------------------------------------------------------------- extraction + rules
def test_clean_single_column_pdf_passes_layout_checks() -> None:
    doc = extract(s.clean_pdf(), FileKind.PDF)
    assert "Exemplia SAS" in doc.text and doc.signals.page_count == 1
    issues, passed = rule_issues(doc, None)
    assert not [i for i in issues if i.severity in ("critical", "high")]
    assert {"layout.multi_column", "parsing.hidden_text", "contact.no_email"} <= set(passed)
    assert find_sections(doc.text) >= {"experience", "education", "skills", "languages"}


def test_two_column_sidebar_and_skill_bars_are_reported() -> None:
    doc = extract(s.two_column_pdf(), FileKind.PDF)
    # white text on the dark sidebar is visible text, not hidden text
    assert "camille.martin@example.com" in doc.text and not doc.hidden
    found = {i.code for i in rule_issues(doc, None)[0]}
    assert {"layout.multi_column", "formatting.skill_bars"} <= found


def test_hidden_text_is_kept_apart_and_reported() -> None:
    doc = extract(s.hidden_text_pdf(), FileKind.PDF)
    assert s.HIDDEN_INSTRUCTION not in doc.text
    assert any("Ignore all previous instructions" in h for h in doc.hidden)
    assert any("terraform" in h for h in doc.hidden)  # 1pt keyword stuffing
    assert "parsing.hidden_text" in {i.code for i in rule_issues(doc, None)[0]}


def test_image_only_pdf_is_critical_and_caps_the_score() -> None:
    doc = extract(s.image_only_pdf(), FileKind.PDF)
    assert doc.text == ""
    score, breakdown, issues = build_report(doc, profile=None, review=None)
    assert issues[0]["code"] == "parsing.no_text" and issues[0]["severity"] == "critical"
    assert score <= 35 and breakdown["categories"]["parsing"] < 50


def test_pdf_with_javascript_is_refused_at_extraction() -> None:
    validate_upload(s.javascript_pdf(), "cv.pdf", max_bytes=MB)  # structure is fine...
    with pytest.raises(UploadRejectedError) as exc:  # ...but its objects are not
        extract(s.javascript_pdf(), FileKind.PDF)
    assert exc.value.code == "active_content"


def test_docx_tables_text_boxes_header_and_hidden_runs() -> None:
    doc = extract(s.tricky_docx(), FileKind.DOCX)
    sig = doc.signals
    assert sig.tables == 1 and sig.text_boxes == 1
    assert "École Exemple" in doc.text  # text-box content is still read
    assert any("camille.martin@example.com" in h for h in sig.header_footer_text)
    assert s.HIDDEN_INSTRUCTION not in doc.text
    assert any("Ignore all previous instructions" in h for h in doc.hidden)


def test_docx_layout_rules() -> None:
    padded = s.tricky_docx()
    doc = extract(padded, FileKind.DOCX)
    doc = doc.model_copy(update={"text": doc.text + "\n" + "Projet mené en équipe. " * 40})
    found = {i.code for i in rule_issues(doc, None)[0]}
    assert {
        "layout.header_footer_contact",
        "layout.text_boxes",
        "parsing.hidden_text",
        "contact.no_email",  # the e-mail exists only in the header
    } <= found
    assert codes(s.clean_docx(), FileKind.DOCX) <= {"contact.no_linkedin"}


def test_date_formats_and_keywords() -> None:
    assert set(date_formats("03/2021 – 06/2024, sept. 2019 – juin 2020")) == {
        "mm/yyyy",
        "month yyyy",
    }
    assert set(date_formats("2019 – 2021 et 2021 – présent")) == {"yyyy only"}
    cov = keyword_coverage(
        "Stack : Node.js, PostgreSQL, CI/CD, C++",
        [
            JobKeyword(term="NodeJS", importance="required"),
            JobKeyword(term="Postgres", importance="required", variants=["PostgreSQL"]),
            JobKeyword(term="C++", importance="preferred"),
            JobKeyword(term="Kubernetes", importance="preferred"),
        ],
    )
    assert [k["term"] for k in cov["missing"]] == ["Kubernetes"]
    assert cov["coverage"] == pytest.approx(5 / 6, abs=0.001)


def test_report_with_job_and_review() -> None:
    doc = extract(s.clean_pdf(), FileKind.PDF)
    review = Review(
        content_score=80,
        job_keywords=[
            JobKeyword(term="FastAPI", importance="required"),
            JobKeyword(term="Terraform", importance="required"),
        ],
        issues=[ReviewIssue(severity="low", message="m", fix="f")],
    )
    score, breakdown, issues = build_report(
        doc, profile=None, review=review, target={"title": "Backend"}
    )
    assert breakdown["keywords"]["coverage"] == 0.5
    missing = next(i for i in issues if i["code"] == "keywords.missing_required")
    assert missing["params"]["terms"] == ["Terraform"]
    assert issues[-1]["source"] == "llm"  # rule issues come first at equal severity
    assert 0 < score < 100


def test_grounding_flags_values_absent_from_the_document() -> None:
    text = extract(s.clean_pdf(), FileKind.PDF).text
    profile = ensure_ids(
        ResumeProfile.model_validate(
            {
                "experiences": [
                    {
                        "company": "Exemplia SAS",
                        "title": "Ingénieure backend",
                        "bullets": [
                            "Réduit la latence p95 de 40 % en introduisant un cache Redis.",
                            "Dirigé une équipe de 25 ingénieurs chez Google.",
                        ],
                    }
                ]
            }
        )
    )
    warnings = grounding_warnings(profile, text)
    assert [w["path"] for w in warnings] == ["experiences.0.bullets.1"]
    assert warnings[0]["id"] == profile.experiences[0].bullets[1].id


def test_reflow_joins_wrapped_lines_but_keeps_bullets_and_headings() -> None:
    from jobpilot_resume import reflow

    text = (
        "- Built a RAG assistant over a locally\ndeployed LLM, reaching 96%\nof faithfulness\n"
        "- Second bullet\nEXPERIENCE\nAcme"
    )
    assert reflow(text) == (
        "- Built a RAG assistant over a locally deployed LLM, reaching 96% of faithfulness\n"
        "- Second bullet\nEXPERIENCE\nAcme"
    )


def test_clickable_linkedin_without_printed_address() -> None:
    from jobpilot_resume import llm_text

    doc = extract(s.clickable_linkedin_pdf(), FileKind.PDF)
    assert doc.signals.links == [s.LINKEDIN_URL]
    assert "Hyperlinks in the document:" in llm_text(doc)  # the parser still gets the URL
    found = {i.code for i in rule_issues(doc, None)[0]}
    assert "contact.linkedin_not_printed" in found and "contact.no_linkedin" not in found


def test_missing_lines_lists_cv_content_the_parser_dropped() -> None:
    from jobpilot_resume.grounding import missing_lines

    text = (
        "Ingénieure backend chez Exemplia\n"
        "Réduit la latence de 40 % en introduisant un cache Redis\n"
        "PUBLICATIONS\n"
        "Article sur la détection d'anomalies présenté à une conférence internationale"
    )
    profile = ResumeProfile.model_validate(
        {
            "experiences": [
                {
                    "company": "Exemplia",
                    "title": "Ingénieure backend",
                    "bullets": ["Réduit la latence de 40 % en introduisant un cache Redis"],
                }
            ]
        }
    )
    assert missing_lines(profile, text) == [
        "Article sur la détection d'anomalies présenté à une conférence internationale"
    ]
