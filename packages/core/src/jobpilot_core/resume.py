"""`ResumeProfile`: the structured CV, and the master profile every tailored document derives from.

Every experience, education entry, project, bullet, skill group, language, certification, link and
extra section carries a stable `id`. Ids are assigned once (`ensure_ids`) and kept across edits,
so a tailored bullet can always point back to the master bullet it came from (no fabrication,
Phase 6).
"""

from __future__ import annotations

import copy
import re
import secrets
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator

# "2024-03", "2024", or None; anything else the parser could not normalise is dropped
_DATE = re.compile(r"^\d{4}(-(0[1-9]|1[0-2]))?$")

ID_PREFIX: dict[str, str] = {
    "experience": "exp",
    "education": "edu",
    "project": "prj",
    "bullet": "b",
    "skill_group": "skl",
    "language": "lng",
    "certification": "crt",
    "link": "lnk",
    "section": "sec",
}


def new_id(kind: str) -> str:
    return f"{ID_PREFIX[kind]}_{secrets.token_hex(4)}"


class _Item(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    id: str = Field(default="", max_length=40)


def _date(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if _DATE.match(text) else None


class Bullet(_Item):
    text: str = Field(max_length=1000)

    @classmethod
    def coerce(cls, value: Any) -> Any:
        return {"text": value} if isinstance(value, str) else value


def _bullets(value: Any) -> Any:
    if not isinstance(value, list):
        return value
    return [Bullet.coerce(v) for v in value if not (isinstance(v, str) and not v.strip())]


DateStr = Annotated[str | None, BeforeValidator(_date)]
Bullets = Annotated[list[Bullet], BeforeValidator(_bullets)]


class Contact(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    full_name: str | None = Field(default=None, max_length=200)
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=64)
    location: str | None = Field(default=None, max_length=200)


class Experience(_Item):
    company: str = Field(max_length=200)
    title: str = Field(max_length=200)
    location: str | None = Field(default=None, max_length=200)
    start: DateStr = None
    end: DateStr = None
    current: bool = False
    employment_type: str | None = Field(default=None, max_length=60)
    bullets: Bullets = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)


class Education(_Item):
    institution: str = Field(max_length=200)
    degree: str | None = Field(default=None, max_length=200)
    field: str | None = Field(default=None, max_length=200)
    location: str | None = Field(default=None, max_length=200)
    start: DateStr = None
    end: DateStr = None
    grade: str | None = Field(default=None, max_length=100)
    details: Bullets = Field(default_factory=list)


class Project(_Item):
    name: str = Field(max_length=200)
    role: str | None = Field(default=None, max_length=200)
    url: str | None = Field(default=None, max_length=500)
    start: DateStr = None
    end: DateStr = None
    description: str | None = Field(default=None, max_length=2000)
    bullets: Bullets = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)


class SkillGroup(_Item):
    name: str = Field(max_length=100)
    kind: Literal["hard", "soft", "tool", "other"] = "hard"
    skills: list[str] = Field(default_factory=list)

    @field_validator("kind", mode="before")
    @classmethod
    def _kind(cls, v: Any) -> Any:
        return v if v in ("hard", "soft", "tool", "other") else "other"


CEFR = Literal["A1", "A2", "B1", "B2", "C1", "C2", "native"]


class Language(_Item):
    name: str = Field(max_length=60)
    level: str | None = Field(default=None, max_length=60)  # as written on the CV
    cefr: CEFR | None = None

    @field_validator("cefr", mode="before")
    @classmethod
    def _cefr(cls, v: Any) -> Any:
        if v is None:
            return None
        v = str(v).strip()
        if v.lower() in ("native", "natif", "natale", "maternelle", "mother tongue"):
            return "native"
        return v.upper() if v.upper() in ("A1", "A2", "B1", "B2", "C1", "C2") else None


class Certification(_Item):
    name: str = Field(max_length=200)
    issuer: str | None = Field(default=None, max_length=200)
    date: DateStr = None
    url: str | None = Field(default=None, max_length=500)
    credential_id: str | None = Field(default=None, max_length=120)


LinkKind = Literal["linkedin", "github", "gitlab", "portfolio", "website", "other"]


class Link(_Item):
    kind: LinkKind = "other"
    url: str = Field(max_length=500)
    label: str | None = Field(default=None, max_length=100)

    @field_validator("kind", mode="before")
    @classmethod
    def _kind(cls, v: Any) -> Any:
        allowed = ("linkedin", "github", "gitlab", "portfolio", "website", "other")
        return v if v in allowed else "other"


class ExtraSection(_Item):
    """Anything else on the CV (volunteering, awards, publications, interests...)."""

    title: str = Field(max_length=100)
    bullets: Bullets = Field(default_factory=list)


class ResumeProfile(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    language: Literal["fr", "en", "other"] = "fr"
    contact: Contact = Field(default_factory=Contact)
    headline: str | None = Field(default=None, max_length=300)
    summary: str | None = Field(default=None, max_length=3000)
    experiences: list[Experience] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    projects: list[Project] = Field(default_factory=list)
    skills: list[SkillGroup] = Field(default_factory=list)
    languages: list[Language] = Field(default_factory=list)
    certifications: list[Certification] = Field(default_factory=list)
    links: list[Link] = Field(default_factory=list)
    extra_sections: list[ExtraSection] = Field(default_factory=list)

    @field_validator("language", mode="before")
    @classmethod
    def _language(cls, v: Any) -> Any:
        return v if v in ("fr", "en") else "other"


SECTIONS = (
    "contact",
    "headline",
    "summary",
    "experiences",
    "education",
    "projects",
    "skills",
    "languages",
    "certifications",
    "links",
    "extra_sections",
)

# list field -> id kind of its items, and the bullet lists they hold
_LIST_KINDS: dict[str, tuple[str, tuple[str, ...]]] = {
    "experiences": ("experience", ("bullets",)),
    "education": ("education", ("details",)),
    "projects": ("project", ("bullets",)),
    "skills": ("skill_group", ()),
    "languages": ("language", ()),
    "certifications": ("certification", ()),
    "links": ("link", ()),
    "extra_sections": ("section", ("bullets",)),
}


def ensure_ids(profile: ResumeProfile) -> ResumeProfile:
    """Give every item without an id (or with a duplicate one) a fresh id; keep all others."""
    seen: set[str] = set()

    def fix(item: _Item, kind: str) -> None:
        if not item.id or item.id in seen:
            item.id = new_id(kind)
            while item.id in seen:  # pragma: no cover - 32-bit collision
                item.id = new_id(kind)
        seen.add(item.id)

    out = profile.model_copy(deep=True)
    for field_name, (kind, bullet_fields) in _LIST_KINDS.items():
        for item in getattr(out, field_name):
            fix(item, kind)
            for bf in bullet_fields:
                for b in getattr(item, bf):
                    fix(b, "bullet")
    return out


def _norm(text: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _similar(a: str | None, b: str | None) -> float:
    import difflib

    return difflib.SequenceMatcher(a=_norm(a), b=_norm(b), autojunk=False).ratio()


# list field -> how an item is recognised in the old profile
_ITEM_KEY: dict[str, Any] = {
    "experiences": lambda e: f"{e.company} {e.title}",
    "education": lambda e: f"{e.institution} {e.degree or ''}",
    "projects": lambda p: p.name,
    "skills": lambda g: g.name,
    "languages": lambda lg: lg.name,
    "certifications": lambda c: c.name,
    "links": lambda lk: lk.url,
    "extra_sections": lambda s: s.title,
}


def carry_ids(old: ResumeProfile, new: ResumeProfile, threshold: float = 0.6) -> ResumeProfile:
    """Give `new` the ids of the matching items and bullets of `old` (best match, each id once).

    Used when a profile is rebuilt from another source (e.g. the LaTeX template): documents
    already generated keep pointing at the same entries. Unmatched items get fresh ids.
    """
    out = new.model_copy(deep=True)
    for field_name, (_, bullet_fields) in _LIST_KINDS.items():
        key = _ITEM_KEY[field_name]
        olds: list[Any] = list(getattr(old, field_name))
        used: set[int] = set()
        for item in getattr(out, field_name):
            scores = [(_similar(key(item), key(o)), i) for i, o in enumerate(olds) if i not in used]
            best = max(scores, default=(0.0, -1))
            item.id = ""
            if best[0] < threshold:
                continue
            used.add(best[1])
            match = olds[best[1]]
            item.id = match.id
            for bf in bullet_fields:
                old_bullets = list(getattr(match, bf))
                taken: set[int] = set()
                for b in getattr(item, bf):
                    cand = [
                        (_similar(b.text, ob.text), j)
                        for j, ob in enumerate(old_bullets)
                        if j not in taken
                    ]
                    score, j = max(cand, default=(0.0, -1))
                    b.id = ""
                    if score >= 0.5:
                        taken.add(j)
                        b.id = old_bullets[j].id
    return ensure_ids(out)


def _words_of(text: str | None) -> set[str]:
    return set(_norm(text).split())


def _covered(part: str, whole: str, share: float = 0.9) -> bool:
    """Is (almost) every word of `part` in `whole`? (a sentence lifted from a longer text)"""
    words = _words_of(part)
    return bool(words) and len(words & _words_of(whole)) / len(words) >= share


def _join_split_parens(items: list[str]) -> list[str]:
    """['Explainable AI (SHAP', 'Grad-CAM)'] -> ['Explainable AI (SHAP, Grad-CAM)']"""
    out: list[str] = []
    for item in items:
        if out and out[-1].count("(") > out[-1].count(")"):
            out[-1] = f"{out[-1]}, {item}"
        else:
            out.append(item)
    return out


def dedupe_profile(profile: ResumeProfile) -> ResumeProfile:
    """Undo what a multi-pass parse duplicates (deterministic, keeps the fullest wording).

    - education / certification entries describing the same thing are merged;
    - an extra section that only repeats structured content (summary, experience bullets,
      certifications, languages) is dropped, after its fuller wording, if any, replaced the
      structured bullet's;
    - skills split inside parentheses by the parser are joined back.
    """
    out = profile.model_copy(deep=True)

    def same_education(a: Education, b: Education) -> bool:
        ia, ib = _norm(a.institution), _norm(b.institution)
        return (ia.startswith(ib) or ib.startswith(ia)) and (a.start, a.end) == (b.start, b.end)

    edu: list[Education] = []
    for e in out.education:
        twin = next((x for x in edu if same_education(x, e)), None)
        if twin is None:
            edu.append(e)
            continue
        short, long_ = sorted((twin, e), key=lambda x: len(x.institution))
        merged = short.model_copy()  # the clean name ("School"), not "School – City"
        for f in ("degree", "field", "location", "grade"):
            if not getattr(merged, f):
                setattr(merged, f, getattr(long_, f))
        if not merged.location and "–" in long_.institution:
            merged.location = long_.institution.split("–", 1)[1].strip() or None
        merged.details = merged.details or long_.details
        edu[edu.index(twin)] = merged
    out.education = edu

    def cert_key(c: Certification) -> str:
        name = c.name
        if c.issuer and _norm(name).startswith(_norm(c.issuer)):
            name = name.split(":", 1)[-1]
        return _norm(name)

    certs: list[Certification] = []
    for c in out.certifications:
        twin = next((x for x in certs if cert_key(x) == cert_key(c)), None)
        if twin is None:
            certs.append(c)
            continue
        keep = min((twin, c), key=lambda x: len(x.name)).model_copy()
        keep.url = twin.url or c.url
        keep.date = twin.date or c.date
        certs[certs.index(twin)] = keep
    out.certifications = certs

    for g in out.skills:
        g.skills = _join_split_parens(g.skills)

    structured: list[Bullet] = [b for e in out.experiences for b in e.bullets] + [
        b for p in out.projects for b in p.bullets
    ]
    other_texts = (
        [out.summary or ""]
        + [f"{c.issuer or ''}: {c.name}" for c in out.certifications]
        + [f"{lg.name}: {lg.level or ''}" for lg in out.languages]
    )

    kept_sections: list[ExtraSection] = []
    for sec in out.extra_sections:
        duplicates = 0
        for b in sec.bullets:
            best = max(structured, key=lambda s: _similar(s.text, b.text), default=None)
            if best is not None and _similar(best.text, b.text) >= 0.8:
                duplicates += 1
                if _words_of(b.text) > _words_of(best.text):  # the section kept more words
                    best.text = b.text
            elif any(_covered(b.text, t) for t in other_texts):
                duplicates += 1
        if not sec.bullets or duplicates / len(sec.bullets) < 0.7:
            kept_sections.append(sec)
    out.extra_sections = kept_sections
    return out


def all_bullets(profile: ResumeProfile) -> dict[str, str]:
    """Every bullet id -> text (the traceability index for tailoring)."""
    out: dict[str, str] = {}
    for field_name, (_, bullet_fields) in _LIST_KINDS.items():
        for item in getattr(profile, field_name):
            for bf in bullet_fields:
                for b in getattr(item, bf):
                    out[b.id] = b.text
    return out


def compact_schema(node: Any) -> Any:
    """Drop what the model does not need (lengths, defaults, titles); nullable -> type list."""
    if isinstance(node, list):
        return [compact_schema(n) for n in node]
    if not isinstance(node, dict):
        return node
    out = {
        k: compact_schema(v)
        for k, v in node.items()
        if k not in ("maxLength", "default", "title", "description", "maxItems")
    }
    any_of = out.get("anyOf")
    if isinstance(any_of, list) and len(any_of) == 2 and {"type": "null"} in any_of:
        other = next(a for a in any_of if a != {"type": "null"})
        if set(other) <= {"type", "enum"} and isinstance(other.get("type"), str):
            del out["anyOf"]
            out.update(other)
            out["type"] = [other["type"], "null"]
            if "enum" in out:
                out["enum"] = [*out["enum"], None]
    return out


def llm_schema(sections: tuple[str, ...] | None = None) -> dict[str, Any]:
    """JSON schema for the parser model: the profile without ids, bullets as plain strings.

    Ids are ours to assign; leaving them out (and every length/default) saves tokens. `sections`
    restricts the top-level fields, for CVs too long to parse in one call.
    """
    schema = copy.deepcopy(ResumeProfile.model_json_schema())
    defs: dict[str, Any] = schema.get("$defs", {})
    defs["Bullet"] = {"type": "string"}
    for d in [schema, *defs.values()]:
        props = d.get("properties")
        if isinstance(props, dict):
            props.pop("id", None)
        if isinstance(d.get("required"), list):
            d["required"] = [r for r in d["required"] if r != "id"]
    if sections is not None:
        schema["properties"] = {
            k: v for k, v in schema["properties"].items() if k in sections or k == "language"
        }
    compact: dict[str, Any] = compact_schema(schema)
    used = json_refs(compact["properties"])
    while True:
        more = used | json_refs([compact["$defs"][u] for u in used])
        if more == used:
            break
        used = more
    compact["$defs"] = {k: v for k, v in compact["$defs"].items() if k in used}
    return compact


def json_refs(node: Any) -> set[str]:
    if isinstance(node, dict):
        refs = {str(node["$ref"]).rsplit("/", 1)[-1]} if "$ref" in node else set()
        for v in node.values():
            refs |= json_refs(v)
        return refs
    if isinstance(node, list):
        return set().union(*(json_refs(v) for v in node)) if node else set()
    return set()
