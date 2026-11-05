"""Tailoring plans and the no-fabrication guardrail.

The TailorAgent never writes a CV. It returns a `TailorPlan`: which master items to show, in which
order, and rewritten bullets that each cite the master bullet they come from. `build_tailored`
then assembles the tailored profile from the *master* profile: employers, job titles, schools,
degrees, dates, links and contact details are always copied from it by id, so the model has no way
to change them. `validate_plan` checks every piece of text the model did write:

- every id exists in the master profile, under the right parent, and is used at most once;
- every number in a rewrite (metrics, years, counts) appears in its source bullet;
- every technical-looking term (capitalised word, acronym, word with digits or symbols) appears
  in the source bullet or elsewhere in the master profile, and no job keyword the profile does
  not contain was slipped in;
- skills are picked from the master group's own list, the headline only uses words the profile
  or a short list of generic role words contain.

A rewrite that fails is replaced by the master bullet's original text (with a warning), so a bad
answer degrades the tailoring, never the truth.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field

from jobpilot_core.resume import (
    ExtraSection,
    ResumeProfile,
    SkillGroup,
)
from jobpilot_security.text import fold

Lang = Literal["fr", "en"]

# words a tailored headline may use without the profile containing them (role vocabulary)
ROLE_WORDS = frozenset(
    [
        "engineer",
        "engineering",
        "ingenieur",
        "ingenieure",
        "ingenierie",
        "developer",
        "developpeur",
        "developpeuse",
        "developpement",
        "development",
        "scientist",
        "scientifique",
        "analyst",
        "analyste",
        "consultant",
        "consultante",
        "intern",
        "internship",
        "stagiaire",
        "stage",
        "junior",
        "graduate",
        "diplome",
        "diplomee",
        "student",
        "etudiant",
        "etudiante",
        "research",
        "recherche",
        "researcher",
        "chercheur",
        "chercheuse",
        "specialist",
        "specialiste",
        "software",
        "logiciel",
        "logiciels",
        "web",
        "mobile",
        "backend",
        "frontend",
        "front",
        "back",
        "end",
        "full",
        "stack",
        "fullstack",
        "data",
        "donnees",
        "machine",
        "learning",
        "apprentissage",
        "automatique",
        "artificial",
        "intelligence",
        "intelligence",
        "artificielle",
        "deep",
        "profond",
        "vision",
        "computer",
        "ordinateur",
        "language",
        "langage",
        "natural",
        "naturel",
        "cloud",
        "devops",
        "mlops",
        "platform",
        "plateforme",
        "systems",
        "systemes",
        "ai",
        "ia",
        "ml",
        "nlp",
        "llm",
        "genai",
        "bi",
        "business",
        "analytics",
        "analytique",
        "big",
        "and",
        "et",
        "of",
        "de",
        "des",
        "du",
        "en",
        "la",
        "le",
        "les",
        "for",
        "pour",
        "with",
        "avec",
        "in",
        "freelance",
        "independant",
        "independante",
    ]
)

_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_TOKEN = re.compile(r"[A-Za-zÀ-ÿ0-9][\w.+#/-]*[\w+#]|[A-Za-zÀ-ÿ0-9]")
_BOLD = re.compile(r"\*\*([^*]+)\*\*")


_HYPHENS = str.maketrans({"\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2212": "-"})


def _canon(text: str) -> str:
    """One written form for comparisons: "R²" = "R2", every Unicode hyphen is "-"."""
    return unicodedata.normalize("NFKC", text).translate(_HYPHENS)


def _numbers(text: str) -> set[str]:
    """'4,000+' -> '4000'; '0,42' -> '0.42'; '96%' -> '96'."""
    out: set[str] = set()
    text = _canon(text)
    # "10 000" / "10 000" (French thousands) -> "10000"
    text = re.sub(r"(?<=\d)[   ](?=\d{3}\b)", "", text)
    for raw in _NUMBER.findall(text):
        n = re.sub(r"(?<=\d)[,.](?=\d{3}\b)", "", raw)  # thousands separators
        out.add(n.replace(",", "."))
    return out


def _vocab(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9][a-z0-9+#.]*[a-z0-9+#]|[a-z0-9]", fold(_canon(text)))}


def _techy(token: str, first: bool) -> bool:
    """Proper nouns, acronyms, tools: an uppercase letter past the first, digits, symbols."""
    if re.fullmatch(r"[\d.,%+/-]+", token) or len(token) == 1:
        return False  # a plain number (`_numbers` checks those), or "I" / "A"
    if any(c.isdigit() for c in token) or any(c in "+#./" for c in token[1:]):
        return True
    upper = sum(c.isupper() for c in token)
    if upper >= 2:
        return True
    return upper == 1 and token[0].isupper() and not first


def suspicious_terms(text: str, allowed: set[str]) -> list[str]:
    """Technical-looking tokens of `text` that `allowed` (folded vocabulary) does not contain."""
    plain = _canon(_BOLD.sub(r"\1", text))
    out: list[str] = []
    for sentence in re.split(r"(?<=[.;:!?])\s+", plain):
        for i, tok in enumerate(_TOKEN.findall(sentence)):
            if not _techy(tok, first=i == 0):
                continue
            parts = [p for p in re.split(r"[/-]", fold(tok).strip(".")) if p]
            if "".join(parts) in allowed:
                continue  # "ResNet-50" is the profile's "ResNet50"
            if parts and not all(p in allowed or p.rstrip("s") in allowed for p in parts):
                out.append(tok)
    return out


# --------------------------------------------------------------------------- the plan


class PlannedBullet(BaseModel):
    source: str = Field(max_length=40)  # id of the master bullet
    text: str = Field(max_length=600)


class PlannedItem(BaseModel):
    id: str = Field(max_length=40)
    bullets: list[PlannedBullet] = Field(default_factory=list, max_length=12)


class PlannedSkills(BaseModel):
    id: str = Field(max_length=40)
    skills: list[str] = Field(default_factory=list, max_length=30)


class PlannedSummary(BaseModel):
    text: str = Field(default="", max_length=900)
    sources: list[str] = Field(default_factory=list, max_length=12)


class TailorPlan(BaseModel):
    language: Lang = "fr"
    headline: str = Field(default="", max_length=120)
    summary: PlannedSummary = Field(default_factory=PlannedSummary)
    experiences: list[PlannedItem] = Field(default_factory=list, max_length=12)
    projects: list[PlannedItem] = Field(default_factory=list, max_length=12)
    education: list[str] = Field(default_factory=list, max_length=8)
    skills: list[PlannedSkills] = Field(default_factory=list, max_length=12)
    certifications: list[str] = Field(default_factory=list, max_length=12)
    languages: list[str] = Field(default_factory=list, max_length=8)
    extra_sections: list[PlannedItem] = Field(default_factory=list, max_length=8)
    keywords_used: list[str] = Field(default_factory=list, max_length=30)
    # bullet ids to drop first when the CV is over its page budget (least useful first)
    drop_first: list[str] = Field(default_factory=list, max_length=40)


@dataclass(slots=True)
class Finding:
    """One thing the guardrail changed or rejected (shown on the review screen)."""

    code: str  # unknown_id | reused_source | wrong_parent | new_number | new_term | ...
    where: str  # id of the item / bullet
    detail: str = ""

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "where": self.where, "detail": self.detail}


@dataclass(slots=True)
class Validated:
    plan: TailorPlan
    findings: list[Finding] = field(default_factory=list)

    @property
    def reverted(self) -> int:
        return sum(f.code in ("new_number", "new_term", "job_keyword") for f in self.findings)


def profile_text(profile: ResumeProfile) -> str:
    return " ".join(str(v) for v in _strings(profile.model_dump(mode="json")))


def _strings(node: Any) -> list[str]:
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [s for k, v in node.items() if k != "id" for s in _strings(v)]
    if isinstance(node, list):
        return [s for v in node for s in _strings(v)]
    return []


def check_text(
    text: str,
    source: str,
    profile_vocab: set[str],
    job_keywords: list[str],
    where: str,
) -> list[Finding]:
    """Findings for one piece of rewritten text against the text it claims to come from."""
    findings: list[Finding] = []
    new_numbers = sorted(_numbers(text) - _numbers(source))
    if new_numbers:
        findings.append(Finding("new_number", where, ", ".join(new_numbers)))
    terms = suspicious_terms(text, _vocab(source) | profile_vocab | ROLE_WORDS)
    if terms:
        findings.append(Finding("new_term", where, ", ".join(dict.fromkeys(terms))))
    folded = fold(_BOLD.sub(r"\1", text))
    for kw in job_keywords:
        k = fold(kw).strip()
        if len(k) < 2 or k in ROLE_WORDS:
            continue
        pattern = rf"(?<![a-z0-9]){re.escape(k)}(?![a-z0-9])"
        if (
            re.search(pattern, folded)
            and not re.search(pattern, fold(source))
            and not all(w in profile_vocab for w in _vocab(k))
        ):
            findings.append(Finding("job_keyword", where, kw))
    return findings


def validate_plan(
    master: ResumeProfile, plan: TailorPlan, job_keywords: list[str] | None = None
) -> Validated:
    """Repair `plan` in place against the master profile; report every repair."""
    keywords = job_keywords or []
    vocab = _vocab(profile_text(master))
    findings: list[Finding] = []
    used_sources: set[str] = set()

    def items_of(kind: str) -> dict[str, Any]:
        return {i.id: i for i in getattr(master, kind)}

    def bullets_of(item: Any) -> dict[str, str]:
        lists = [getattr(item, f) for f in ("bullets", "details") if hasattr(item, f)]
        return {b.id: b.text for lst in lists for b in lst}

    def check_items(kind: str, planned: list[PlannedItem]) -> list[PlannedItem]:
        known = items_of(kind)
        out: list[PlannedItem] = []
        seen: set[str] = set()
        for p in planned:
            if p.id not in known or p.id in seen:
                findings.append(Finding("unknown_id", p.id, kind))
                continue
            seen.add(p.id)
            own = bullets_of(known[p.id])
            kept: list[PlannedBullet] = []
            for b in p.bullets:
                if b.source not in own:
                    findings.append(Finding("wrong_parent", b.source, p.id))
                    continue
                if b.source in used_sources:
                    findings.append(Finding("reused_source", b.source, p.id))
                    continue
                used_sources.add(b.source)
                problems = check_text(b.text, own[b.source], vocab, keywords, b.source)
                if problems or not b.text.strip():
                    findings.extend(problems)
                    b.text = own[b.source]  # revert to the master wording
                kept.append(b)
            p.bullets = kept
            out.append(p)
        return out

    # an item listed in the wrong group (a project among experiences) goes back to its own
    sections = ("experiences", "projects", "extra_sections")
    owner = {i: kind for kind in sections for i in items_of(kind)}
    for kind in sections:
        for p in list(getattr(plan, kind)):
            home = owner.get(p.id, kind)
            if home != kind:
                findings.append(Finding("wrong_group", p.id, kind))
                getattr(plan, kind).remove(p)
                getattr(plan, home).append(p)

    plan.experiences = check_items("experiences", plan.experiences)
    planned = {p.id for p in plan.experiences}
    for exp in master.experiences:
        if exp.id not in planned:
            findings.append(Finding("restored_experience", exp.id, exp.title))
            first = [PlannedBullet(source=b.id, text=b.text) for b in exp.bullets[:1]]
            used_sources.update(b.source for b in first)
            plan.experiences.append(PlannedItem(id=exp.id, bullets=first))
    plan.projects = check_items("projects", plan.projects)
    plan.extra_sections = check_items("extra_sections", plan.extra_sections)

    def check_ids(kind: str, ids: list[str]) -> list[str]:
        known = items_of(kind)
        out = []
        for i in dict.fromkeys(ids):
            if i in known:
                out.append(i)
            else:
                findings.append(Finding("unknown_id", i, kind))
        return out

    plan.education = check_ids("education", plan.education)
    plan.certifications = check_ids("certifications", plan.certifications)
    plan.languages = check_ids("languages", plan.languages)

    groups = items_of("skills")
    kept_groups: list[PlannedSkills] = []
    for g in plan.skills:
        if g.id not in groups:
            findings.append(Finding("unknown_id", g.id, "skills"))
            continue
        own = {fold(_canon(s)): s for s in groups[g.id].skills}
        entries = " | ".join(own)
        chosen = []
        for s in g.skills:
            key = fold(_canon(s)).strip()
            if key in own:
                chosen.append(own[key])  # the master spelling
            elif key and _mentions(entries, key):
                chosen.append(_canon(s).strip())  # named inside a longer master entry
            else:
                findings.append(Finding("new_skill", g.id, s))
        g.skills = list(dict.fromkeys(chosen))
        if g.skills:
            kept_groups.append(g)
    plan.skills = kept_groups

    if plan.headline:
        bad = [w for w in _vocab(plan.headline) if w not in vocab and w not in ROLE_WORDS]
        if bad:
            findings.append(Finding("headline", "headline", ", ".join(bad)))
            plan.headline = master.headline or ""
    else:
        plan.headline = master.headline or ""

    sources = all_texts(master)
    cited = " ".join(sources.get(s, "") for s in plan.summary.sources)
    basis = f"{master.summary or ''} {master.headline or ''} {cited}"
    if plan.summary.text:
        problems = check_text(plan.summary.text, basis, vocab, keywords, "summary")
        if problems:
            findings.extend(problems)
            plan.summary = PlannedSummary(text=master.summary or "", sources=[])
    else:
        plan.summary = PlannedSummary(text=master.summary or "", sources=[])

    plan.drop_first = [b for b in plan.drop_first if b in used_sources]
    return Validated(plan, findings)


def all_texts(profile: ResumeProfile) -> dict[str, str]:
    """id -> text for every bullet/detail, plus every item (its own fields joined)."""
    out: dict[str, str] = {}
    for kind in ("experiences", "education", "projects", "extra_sections"):
        for item in getattr(profile, kind):
            parts = []
            for f in ("bullets", "details"):
                for b in getattr(item, f, []):
                    out[b.id] = b.text
                    parts.append(b.text)
            out[item.id] = " ".join(parts)
    return out


# --------------------------------------------------------------------------- cover letters


_FIRST_PERSON = re.compile(r"(?<![a-z])(je|j'|j\u2019|mon|ma|mes|i|i'm|my)(?![a-z])")
# a start date or immediate availability; "disponible pour un entretien" is only a courtesy
_AVAILABILITY = re.compile(
    r"disponib\w*\s+(?:immediat|tout de suite|de suite|des\b|a partir|a compter)"
    r"|available\s+(?:immediately|from|to start|right away)"
    r"|start\s+(?:immediately|right away)"
)


_INLINE_ID = re.compile(r"\b[a-z]{1,4}_[0-9a-f]+\b")
_ID_LEFTOVERS = re.compile(
    r"\s*[(\[]\s*(?:(?:sources?|id)\s*:?\s*)?[,;\s]*[)\]]|\s+(?=[,;.!?](?:\s|$))"
)


def _lift_inline_ids(text: str, known: dict[str, str]) -> tuple[str, list[str]]:
    """Take bullet ids the model wrote into its prose out of it; they are citations."""
    cited = [m for m in _INLINE_ID.findall(text) if m in known]
    for id_ in cited:
        text = re.sub(rf",?\s*\b{re.escape(id_)}\b", "", text)
    return (_ID_LEFTOVERS.sub("", text) if cited else text), cited


@dataclass(slots=True)
class CheckedParagraph:
    text: str
    sources: list[str]


def validate_letter(
    master: ResumeProfile,
    paragraphs: list[tuple[str, list[str]]],
    job_text: str,
    job_keywords: list[str] | None = None,
    identity: str = "",
) -> tuple[list[CheckedParagraph], list[Finding]]:
    """Keep only the sentences whose facts come from the cited bullets, the profile or the job.

    Numbers must be in the cited bullets, the profile's summary/headline or the posting itself
    (e.g. "6 months"); technical terms in the profile or the posting; and a job keyword the
    profile does not contain (a skill the candidate lacks) is never claimed. A sentence that
    fails is removed; an empty paragraph is dropped. In a sentence about the candidate ("I", "je",
    "mon"...) terms may only come from the profile or the job's `identity` (title, company): a
    phrase of the posting cannot become a claimed ability. Availability is never stated, the
    profile does not hold it.
    """
    texts = all_texts(master)
    profile_vocab = _vocab(profile_text(master))
    job_vocab = _vocab(job_text)
    identity_vocab = _vocab(identity)
    findings: list[Finding] = []
    out: list[CheckedParagraph] = []
    for n, (raw, listed) in enumerate(paragraphs, start=1):
        text, cited = _lift_inline_ids(raw, texts)
        sources = [*listed, *cited]
        valid_sources = [s for s in dict.fromkeys(sources) if s in texts]
        for s in set(sources) - set(valid_sources):
            findings.append(Finding("unknown_id", s, f"paragraph {n}"))
        own = " ".join(
            [master.summary or "", master.headline or "", *(texts[s] for s in valid_sources)]
        )
        basis = f"{own} {job_text}"
        kept: list[str] = []
        for sentence in re.split(r"(?<=[.!?])\s+", text.strip()):
            if not sentence:
                continue
            about_me = bool(_FIRST_PERSON.search(fold(sentence)))
            allowed = profile_vocab | identity_vocab if about_me else profile_vocab | job_vocab
            problems = check_text(sentence, own if about_me else basis, allowed, [], f"p{n}")
            if _AVAILABILITY.search(fold(sentence)):
                problems.append(Finding("availability", f"p{n}", sentence[:80]))
            claimed = [
                kw
                for kw in (job_keywords or [])
                if _mentions(sentence, kw)
                and not all(w in profile_vocab for w in _vocab(kw))
                and fold(kw) not in ROLE_WORDS
            ]
            if claimed:
                problems.append(Finding("job_keyword", f"p{n}", ", ".join(claimed)))
            if problems:
                findings.extend(problems)
                continue
            kept.append(sentence)
        if kept:
            out.append(CheckedParagraph(" ".join(kept), valid_sources))
    return out, findings


def _mentions(text: str, keyword: str) -> bool:
    k = fold(keyword).strip()
    return (
        bool(k) and re.search(rf"(?<![a-z0-9]){re.escape(k)}(?![a-z0-9])", fold(text)) is not None
    )


# --------------------------------------------------------------------------- assembly


def build_tailored(
    master: ResumeProfile, plan: TailorPlan, *, drop: set[str] | None = None
) -> ResumeProfile:
    """The tailored profile: master facts by id, the plan's order and validated wording.

    `drop` removes bullets (and items left with none, except experiences and education) to fit
    a page budget.
    """
    dropped = drop or set()

    def pick(kind: str, planned: list[PlannedItem], keep_empty: bool) -> list[Any]:
        known = {i.id: i for i in getattr(master, kind)}
        out = []
        for p in planned:
            item = known[p.id].model_copy(deep=True)
            field_name = "bullets"
            originals = {b.id: b for b in getattr(item, field_name)}
            new_bullets = []
            for b in p.bullets:
                if b.source in dropped:
                    continue
                nb = originals[b.source].model_copy()
                nb.text = b.text
                new_bullets.append(nb)
            setattr(item, field_name, new_bullets)
            if new_bullets or keep_empty:
                out.append(item)
        return out

    by_id = {
        kind: {i.id: i for i in getattr(master, kind)}
        for kind in ("education", "certifications", "languages")
    }
    groups = {g.id: g for g in master.skills}
    skills = [
        SkillGroup(id=g.id, name=groups[g.id].name, kind=groups[g.id].kind, skills=g.skills)
        for g in plan.skills
    ]
    extras: list[ExtraSection] = pick("extra_sections", plan.extra_sections, keep_empty=False)
    return ResumeProfile(
        language=plan.language,
        contact=master.contact.model_copy(),
        headline=plan.headline or master.headline,
        summary=plan.summary.text or master.summary,
        experiences=pick("experiences", plan.experiences, keep_empty=True),
        education=[by_id["education"][i].model_copy(deep=True) for i in plan.education],
        projects=pick("projects", plan.projects, keep_empty=False),
        skills=skills,
        languages=[by_id["languages"][i].model_copy() for i in plan.languages],
        certifications=[
            by_id["certifications"][i].model_copy() for i in plan.certifications if i not in dropped
        ],
        links=[lk.model_copy() for lk in master.links],
        extra_sections=extras,
    )


def drop_order(plan: TailorPlan) -> list[str]:
    """What to remove, one at a time, until the CV fits (ids of bullets and certifications).

    Experience is what a recruiter reads first, so it goes last:
    1. the plan's own choice (never an item's first bullet);
    2. certifications beyond the first two, then extra sections;
    3. extra bullets of projects, then whole projects, least relevant first, down to one;
    4. extra bullets of experiences, least relevant first;
    5. the last project: only if nothing else helped.
    An experience always keeps its first bullet.
    """
    firsts = {
        i.bullets[0].source for g in (plan.projects, plan.experiences) for i in g if i.bullets
    }
    extras = {b.source for s in plan.extra_sections for b in s.bullets}
    order = [b for b in plan.drop_first if b not in firsts and b not in extras]
    order += list(reversed(plan.certifications[2:]))
    order += [b.source for s in reversed(plan.extra_sections) for b in reversed(s.bullets)]
    for item in reversed(plan.projects):
        order += [b.source for b in reversed(item.bullets[1:])]
    for item in reversed(plan.projects[1:]):
        order += [b.source for b in item.bullets[:1]]
    for item in reversed(plan.experiences):
        order += [b.source for b in reversed(item.bullets[1:])]
    order += [b.source for item in plan.projects[:1] for b in item.bullets[:1]]
    return list(dict.fromkeys(order))
