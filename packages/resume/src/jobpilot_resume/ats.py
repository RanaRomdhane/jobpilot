"""ATS-friendliness report: deterministic rules + keyword coverage + the LLM review, one score.

Rule issues carry a stable `code` and `params`; the dashboard renders their title and fix in the
user's language. LLM issues carry their own text (written in the user's language). Only rule issues
cost points directly; the LLM's judgement enters through its content score, so the same problem is
never penalised twice.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from jobpilot_core.resume import ResumeProfile, all_bullets
from jobpilot_resume.extract import BULLET_STANDARD, ExtractedDocument
from jobpilot_security.text import fold

Severity = Literal["critical", "high", "medium", "low"]
Category = Literal["parsing", "layout", "sections", "contact", "formatting", "content", "keywords"]

SEVERITY_RANK: dict[str, int] = {"critical": 0, "high": 1, "medium": 2, "low": 3}
PENALTY: dict[str, int] = {"critical": 70, "high": 35, "medium": 15, "low": 5}
WEIGHTS: dict[str, int] = {
    "parsing": 25,
    "layout": 20,
    "sections": 10,
    "contact": 10,
    "formatting": 10,
    "content": 25,
}
KEYWORD_SHARE = 0.25  # of the total, when a target job is given


class Issue(BaseModel):
    code: str
    category: Category
    severity: Severity
    source: Literal["rule", "llm"] = "rule"
    params: dict[str, Any] = Field(default_factory=dict)
    evidence: str | None = None
    message: str | None = None  # LLM issues only
    fix: str | None = None  # LLM issues only


# ------------------------------------------------------------------ LLM review (schema)
class ReviewIssue(BaseModel):
    severity: Literal["high", "medium", "low"] = "medium"
    message: str = Field(max_length=400)
    fix: str = Field(max_length=500)
    evidence: str | None = Field(default=None, max_length=300)


class BulletRewrite(BaseModel):
    original: str = Field(max_length=600)
    suggestion: str = Field(max_length=600)
    why: str = Field(default="", max_length=300)


class JobKeyword(BaseModel):
    term: str = Field(max_length=80)
    importance: Literal["required", "preferred"] = "preferred"
    variants: list[str] = Field(default_factory=list, max_length=6)


class Review(BaseModel):
    summary: str = Field(default="", max_length=800)
    strengths: list[str] = Field(default_factory=list, max_length=5)
    issues: list[ReviewIssue] = Field(default_factory=list, max_length=10)
    bullet_rewrites: list[BulletRewrite] = Field(default_factory=list, max_length=6)
    content_score: int = Field(default=60, ge=0, le=100)
    job_title: str | None = Field(default=None, max_length=200)
    job_keywords: list[JobKeyword] = Field(default_factory=list, max_length=30)
    injection_suspected: bool = False


# ------------------------------------------------------------------ text helpers
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
PHONE = re.compile(r"(?<!\w)(\+?\d[\d .\-()/]{6,}\d)(?!\w)")
LINKEDIN = re.compile(r"linkedin\.com/", re.I)

SECTION_HEADINGS: dict[str, tuple[str, ...]] = {
    "experience": (
        "experience",
        "experiences",
        "work experience",
        "professional experience",
        "employment",
        "work history",
        "experience professionnelle",
        "experiences professionnelles",
        "parcours professionnel",
        "emplois",
        "stages",
        "internships",
    ),
    "education": (
        "education",
        "formation",
        "formations",
        "etudes",
        "diplomes",
        "academic background",
        "cursus",
        "parcours academique",
        "qualifications",
    ),
    "skills": (
        "skills",
        "competences",
        "technical skills",
        "competences techniques",
        "hard skills",
        "soft skills",
        "savoir-faire",
        "technologies",
        "outils",
        "tools",
    ),
    "summary": ("summary", "profile", "profil", "about me", "a propos", "objective", "objectif"),
    "languages": ("languages", "langues"),
    "projects": ("projects", "projets", "academic projects", "projets academiques"),
    "certifications": ("certifications", "certificates", "certificats"),
}

_MONTHS = (
    "jan|janv|janvier|january|feb|fev|fevr|fevrier|february|mar|mars|march|apr|avr|avril|april|"
    "may|mai|jun|juin|june|jul|juil|juillet|july|aug|aou|aout|august|sep|sept|septembre|september|"
    "oct|octobre|october|nov|novembre|november|dec|decembre|december"
)
DATE_FORMATS: dict[str, re.Pattern[str]] = {
    "mm/yyyy": re.compile(r"\b(0?[1-9]|1[0-2])[/.](19|20)\d{2}\b"),
    "mm-yyyy": re.compile(r"\b(0[1-9]|1[0-2])-(19|20)\d{2}\b"),
    "yyyy-mm": re.compile(r"\b(19|20)\d{2}-(0[1-9]|1[0-2])\b"),
    "month yyyy": re.compile(rf"\b({_MONTHS})\.?\s+(19|20)\d{{2}}\b"),
    "yyyy only": re.compile(
        r"(?<![/\-.\d])(19|20)\d{2}\s*[-–—à]\s*((19|20)\d{2}|present|aujourd'hui"
        r"|actuel|now|today|en cours)\b"
    ),
}


def find_sections(text: str) -> set[str]:
    found: set[str] = set()
    for line in text.split("\n"):
        words = line.strip().split()
        if not words or len(words) > 5:
            continue
        key = re.sub(r"[^a-z' -]", "", fold(line)).strip(" -'")
        for section, headings in SECTION_HEADINGS.items():
            if any(key == h or key.startswith(h + " ") for h in headings):
                found.add(section)
    return found


def date_formats(text: str) -> dict[str, int]:
    folded = fold(text)
    counts = {name: len(p.findall(folded)) for name, p in DATE_FORMATS.items()}
    # "03/2021" also matches the yyyy-only range pattern's guard; keep only real hits
    return {k: v for k, v in counts.items() if v}


def _norm_term(s: str, joiner: str = " ") -> str:
    s = re.sub(r"[._/\-]+", joiner, fold(s))
    return re.sub(r"\s+", " ", s).strip()


def _has(needle: str, haystack: str) -> bool:
    return re.search(rf"(?<![a-z0-9+#]){re.escape(needle)}(?![a-z0-9+#])", haystack) is not None


def keyword_present(term: str, text: str) -> bool:
    """Whole-word match after folding; "Node.js", "node js" and "NodeJS" are the same term."""
    t = _norm_term(term)
    if not t:
        return False
    spaced, joined = _norm_term(text), _norm_term(text, "")
    compact = _norm_term(term, "").replace(" ", "")
    return _has(t, spaced) or _has(compact, joined) or _has(compact, spaced)


def keyword_coverage(text: str, keywords: list[JobKeyword]) -> dict[str, Any]:
    matched, missing = [], []
    got = total = 0.0
    for kw in keywords:
        weight = 2.0 if kw.importance == "required" else 1.0
        total += weight
        if any(keyword_present(v, text) for v in [kw.term, *kw.variants]):
            matched.append(kw.model_dump())
            got += weight
        else:
            missing.append(kw.model_dump())
    return {
        "coverage": round(got / total, 3) if total else None,
        "matched": matched,
        "missing": missing,
    }


# ------------------------------------------------------------------ rules
def rule_issues(
    doc: ExtractedDocument, profile: ResumeProfile | None
) -> tuple[list[Issue], list[str]]:
    """Returns (issues, codes of the checks that passed)."""
    s = doc.signals
    text = doc.text
    chars = len(text.strip())
    issues: list[Issue] = []
    passed: list[str] = []

    def check(ok: bool, code: str, category: Category, severity: Severity, **params: Any) -> None:
        if ok:
            passed.append(code)
        else:
            evidence = params.pop("evidence", None)
            issues.append(
                Issue(
                    code=code,
                    category=category,
                    severity=severity,
                    params=params,
                    evidence=evidence,
                )
            )

    # parsing
    check(chars >= 200, "parsing.no_text", "parsing", "critical", chars=chars)
    if chars < 200:
        return issues, passed  # nothing else can be judged on an unreadable file
    garbled = s.replacement_chars
    check(garbled <= max(5, chars // 200), "parsing.garbled", "parsing", "critical", count=garbled)
    check(s.type3_fonts == 0, "parsing.type3_fonts", "parsing", "high", count=s.type3_fonts)
    check(
        s.hidden_chars <= 20,
        "parsing.hidden_text",
        "parsing",
        "high",
        chars=s.hidden_chars,
        evidence=(doc.hidden[0][:160] if doc.hidden else None),
    )
    icons = s.private_use_chars > 5 or bool(s.icon_fonts)
    check(
        not icons,
        "parsing.icon_glyphs",
        "parsing",
        "medium",
        count=s.private_use_chars,
        fonts=s.icon_fonts,
    )

    # layout
    pages = s.page_count or 1
    mc = len(s.multi_column_pages)
    check(
        mc == 0,
        "layout.multi_column",
        "layout",
        "high" if mc * 2 >= pages else "medium",
        pages=s.multi_column_pages,
    )
    table_share = s.table_chars / chars if chars else 0
    check(
        table_share <= 0.15,
        "layout.tables",
        "layout",
        "medium",
        tables=s.tables,
        share=round(table_share, 2),
    )
    box_share = s.text_box_chars / chars if chars else 0
    check(
        s.text_boxes == 0,
        "layout.text_boxes",
        "layout",
        "high" if box_share > 0.1 else "medium",
        count=s.text_boxes,
        share=round(box_share, 2),
    )
    hf = "\n".join(s.header_footer_text)
    hf_contact = bool(EMAIL.search(hf) or PHONE.search(hf))
    check(
        not hf_contact,
        "layout.header_footer_contact",
        "layout",
        "high",
        evidence=hf[:160] or None,
    )
    if not hf_contact:
        check(not hf, "layout.header_footer", "layout", "low", evidence=hf[:160] or None)
    check(
        s.image_area_ratio <= 0.25,
        "layout.large_images",
        "layout",
        "medium",
        ratio=s.image_area_ratio,
    )
    if s.image_area_ratio <= 0.25:
        check(s.images == 0, "layout.images", "layout", "low", count=s.images)

    # sections
    sections = find_sections(text)
    if not sections & {"experience", "education", "skills"}:
        check(False, "sections.none_found", "sections", "high")
    else:
        check("experience" in sections, "sections.missing_experience", "sections", "high")
        check("education" in sections, "sections.missing_education", "sections", "medium")
        check("skills" in sections, "sections.missing_skills", "sections", "medium")

    # contact (in the body: ATS often skip headers, footers and text boxes)
    body = text
    check(bool(EMAIL.search(body)), "contact.no_email", "contact", "high")
    phones = [p for p in PHONE.findall(body) if len(re.sub(r"\D", "", p)) >= 8]
    check(bool(phones), "contact.no_phone", "contact", "medium")
    has_linkedin = bool(LINKEDIN.search(body)) or bool(
        profile and any(link.kind == "linkedin" for link in profile.links)
    )
    check(has_linkedin, "contact.no_linkedin", "contact", "low")

    # formatting
    formats = date_formats(text)
    check(len(formats) <= 1, "formatting.date_formats", "formatting", "medium", formats=formats)
    check(len(s.fonts) <= 3, "formatting.too_many_fonts", "formatting", "low", fonts=s.fonts[:8])
    odd = {g: n for g, n in s.bullet_glyphs.items() if g not in BULLET_STANDARD}
    check(not odd, "formatting.bullets", "formatting", "low", glyphs=list(odd))
    bars = s.skill_bar_shapes + s.rating_glyph_runs
    check(bars == 0, "formatting.skill_bars", "formatting", "medium", count=bars)
    check(
        pages <= 2,
        "formatting.pages",
        "formatting",
        "high" if pages > 3 else "medium",
        pages=pages,
    )
    words = doc.word_count
    check(words >= 200, "formatting.too_short", "formatting", "medium", words=words)
    check(words <= 1100, "formatting.too_long", "formatting", "low", words=words)

    # content (from the parsed profile)
    if profile is not None:
        bullets = list(all_bullets(profile).values())
        thin = [e.company for e in profile.experiences if len(e.bullets) < 2]
        check(not thin, "content.few_bullets", "content", "medium", experiences=thin[:5])
        exp_bullets = [b.text for e in profile.experiences for b in e.bullets]
        if len(exp_bullets) >= 4:
            quantified = sum(bool(re.search(r"\d", b)) for b in exp_bullets)
            ratio = quantified / len(exp_bullets)
            check(
                ratio >= 0.3,
                "content.low_quantification",
                "content",
                "medium",
                ratio=round(ratio, 2),
            )
        long = [b for b in bullets if len(b.split()) > 35]
        check(
            len(long) < 2,
            "content.long_bullets",
            "content",
            "low",
            count=len(long),
            evidence=(long[0][:160] if long else None),
        )
        check(bool(profile.summary or profile.headline), "content.no_summary", "content", "low")
    return issues, passed


def _category_scores(issues: list[Issue]) -> dict[str, int]:
    scores = {c: 100 for c in WEIGHTS}
    for i in issues:
        if i.source == "rule" and i.category in scores:
            scores[i.category] = max(0, scores[i.category] - PENALTY[i.severity])
    return scores


def build_report(
    doc: ExtractedDocument,
    *,
    profile: ResumeProfile | None,
    review: Review | None,
    target: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any], list[dict[str, Any]]]:
    """Returns (score 0-100, breakdown, prioritized issues) as stored on `ats_report`."""
    issues, passed = rule_issues(doc, profile)
    categories = _category_scores(issues)
    if review is not None:
        categories["content"] = round(0.5 * categories["content"] + 0.5 * review.content_score)
    base = sum(categories[c] * w for c, w in WEIGHTS.items()) / sum(WEIGHTS.values())

    keywords: dict[str, Any] | None = None
    if target is not None and review is not None and review.job_keywords:
        keywords = keyword_coverage(doc.text, review.job_keywords)
        cov = keywords["coverage"] or 0.0
        total = (1 - KEYWORD_SHARE) * base + KEYWORD_SHARE * 100 * cov
        missing_required = [k["term"] for k in keywords["missing"] if k["importance"] == "required"]
        missing_preferred = [
            k["term"] for k in keywords["missing"] if k["importance"] == "preferred"
        ]
        if missing_required:
            issues.append(
                Issue(
                    code="keywords.missing_required",
                    category="keywords",
                    severity="high",
                    params={"terms": missing_required[:12]},
                )
            )
        if missing_preferred:
            issues.append(
                Issue(
                    code="keywords.missing_preferred",
                    category="keywords",
                    severity="low",
                    params={"terms": missing_preferred[:12]},
                )
            )
    else:
        total = base

    if any(i.severity == "critical" for i in issues):
        total = min(total, 35)

    if review is not None:
        for r in review.issues:
            issues.append(
                Issue(
                    code="llm.review",
                    category="content",
                    severity=r.severity,
                    source="llm",
                    message=r.message,
                    fix=r.fix,
                    evidence=r.evidence,
                )
            )

    issues.sort(
        key=lambda i: (
            SEVERITY_RANK[i.severity],
            0 if i.source == "rule" else 1,
            -WEIGHTS.get(i.category, 15),
        )
    )
    breakdown: dict[str, Any] = {
        "categories": categories,
        "weights": WEIGHTS,
        "passed": passed,
        "stats": {
            "pages": doc.signals.page_count,
            "words": doc.word_count,
            "kind": doc.kind.value,
            "fonts": doc.signals.fonts,
            "sections": sorted(find_sections(doc.text)),
        },
        "keywords": keywords,
        "target": target,
        "review": (
            review.model_dump(include={"summary", "strengths", "bullet_rewrites", "content_score"})
            if review is not None
            else None
        ),
    }
    return round(total), breakdown, [i.model_dump() for i in issues]
