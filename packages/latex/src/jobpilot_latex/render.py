"""Jinja2 rendering of LaTeX templates with LaTeX-safe delimiters.

    \\VAR{contact.full_name}                       a value (escaped automatically)
    \\BLOCK{for exp in experiences} ... \\BLOCK{endfor}
    \\#{ a comment }

Every `\\VAR{}` output goes through `latex_escape` (Jinja `finalize`), so no inserted value can
inject LaTeX. Filters that build LaTeX themselves (`url`, `paragraphs`, `ljoin`) escape their
inputs and return `LatexSafe`. The environment is sandboxed (no attribute access to internals)
and strict (an unknown variable is an error, not an empty string).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from typing import Any

from jinja2 import StrictUndefined, TemplateSyntaxError
from jinja2.sandbox import ImmutableSandboxedEnvironment
from pydantic import ValidationError

from jobpilot_core.resume import ResumeProfile
from jobpilot_security import LatexSafe, latex_escape, latex_paragraphs, latex_url

log = logging.getLogger(__name__)

MONTHS = {
    "fr": [
        "janv.",
        "févr.",
        "mars",
        "avr.",
        "mai",
        "juin",
        "juil.",
        "août",
        "sept.",
        "oct.",
        "nov.",
        "déc.",
    ],
    # the common CV style (RenderCV, Overleaf résumés): short names, but June / July / Sept
    "en": ["Jan", "Feb", "Mar", "Apr", "May", "June", "July", "Aug", "Sept", "Oct", "Nov", "Dec"],
}
LABELS: dict[str, dict[str, str]] = {
    "fr": {
        "experience": "Expérience professionnelle",
        "education": "Formation",
        "projects": "Projets",
        "skills": "Compétences",
        "languages": "Langues",
        "certifications": "Certifications",
        "summary": "Profil",
        "present": "présent",
        "contact": "Contact",
    },
    "en": {
        "experience": "Experience",
        "education": "Education",
        "projects": "Projects",
        "skills": "Skills",
        "languages": "Languages",
        "certifications": "Certifications",
        "summary": "Summary",
        "present": "Present",
        "contact": "Contact",
    },
}


class TemplateError(ValueError):
    """The template does not parse or does not render with the given data."""


# **bold** and [label](https://...) inside bullet text; nothing else is interpreted
_INLINE = re.compile(
    r"\*\*(?P<b>[^*\n]{1,120}?)\*\*|\[(?P<l>[^\]\n]{1,200})\]\((?P<u>[^)\s]{1,500})\)"
)


def rich(value: Any) -> LatexSafe:
    """Escape text, then turn `**term**` into \\textbf{} and `[label](url)` into \\href{}{}.

    The markup is parsed on the raw text and every piece is escaped on its own, so a value can
    never produce anything but bold text and http(s)/mailto links. A link with a rejected URL
    keeps only its label.
    """
    text = "" if value is None else str(value)
    out: list[str] = []
    pos = 0
    for m in _INLINE.finditer(text):
        out.append(latex_escape_keep_edges(text[pos : m.start()]))
        if m.group("b") is not None:
            out.append(rf"\textbf{{{latex_escape(m.group('b'))}}}")
        else:
            url = latex_url(m.group("u"))
            label = latex_escape(m.group("l"))
            out.append(rf"\href{{{url}}}{{{label}}}" if url else label)
        pos = m.end()
    out.append(latex_escape_keep_edges(text[pos:]))
    return LatexSafe("".join(out).strip())


def latex_escape_keep_edges(text: str) -> str:
    """`latex_escape` without trimming the spaces that separate a piece from its markup."""
    if not text:
        return ""
    core = latex_escape(text)
    lead = " " if text[:1].isspace() else ""
    trail = " " if text[-1:].isspace() and core else ""
    return f"{lead}{core}{trail}"


def plain(value: Any) -> str:
    """Rich text without its markup: '**React** [paper](u)' -> 'React paper'."""
    return _INLINE.sub(lambda m: m.group("b") or m.group("l") or "", str(value or ""))


def _lang(lang: str) -> str:
    return lang if lang in MONTHS else "en"


def format_date(value: str | None, lang: str = "fr") -> str:
    """'2024-03' -> 'mars 2024' / 'Mar 2024'; '2024' -> '2024'; None -> ''."""
    if not value:
        return ""
    year, _, month = value.partition("-")
    if not month:
        return year
    return f"{MONTHS[_lang(lang)][int(month) - 1]} {year}"


def date_range(item: Any, lang: str = "fr", sep: str = " – ") -> str:
    """'mars 2023 – présent' from an item with start / end / current."""
    get = item.get if isinstance(item, dict) else lambda k, d=None: getattr(item, k, d)
    start = format_date(get("start"), lang)
    end = LABELS[_lang(lang)]["present"] if get("current", False) else format_date(get("end"), lang)
    if start and end and start != end:
        return f"{start}{sep}{end}"
    return start or end


def make_env() -> ImmutableSandboxedEnvironment:
    env = ImmutableSandboxedEnvironment(
        block_start_string=r"\BLOCK{",
        block_end_string="}",
        variable_start_string=r"\VAR{",
        variable_end_string="}",
        comment_start_string=r"\#{",
        comment_end_string="}",
        line_statement_prefix=None,
        line_comment_prefix=None,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
        autoescape=False,
        undefined=StrictUndefined,
        finalize=latex_escape,
    )

    def ljoin(items: Iterable[Any], sep: str = ", ") -> LatexSafe:
        # `sep` is written in the template itself (trusted LaTeX); items are escaped
        return LatexSafe(sep.join(latex_escape(i) for i in items if i not in (None, "")))

    def url(value: Any) -> LatexSafe:
        return LatexSafe(latex_url(value))

    env.filters.update(
        ljoin=ljoin,
        rich=rich,
        url=url,
        paragraphs=latex_paragraphs,
        date=format_date,
        daterange=date_range,
    )
    return env


ENV = make_env()


def check_syntax(source: str) -> None:
    try:
        ENV.parse(source)
    except TemplateSyntaxError as exc:
        raise TemplateError(f"line {exc.lineno}: {exc.message}") from exc


def profile_context(profile: ResumeProfile, lang: str | None = None) -> dict[str, Any]:
    """Template variables for a CV: the profile itself plus a few conveniences."""
    lang = _lang(lang or profile.language)
    data = profile.model_dump(mode="json")
    links = {lnk.kind: lnk.url for lnk in profile.links}
    return {
        **data,
        "lang": lang,
        "labels": LABELS[lang],
        "name": profile.contact.full_name or "",
        "link": {
            k: links.get(k, "") for k in ("linkedin", "github", "gitlab", "portfolio", "website")
        },
        "skills_flat": [s for g in profile.skills for s in g.skills],
    }


def render(source: str, context: dict[str, Any]) -> str:
    try:
        return ENV.from_string(source).render(context)
    except TemplateSyntaxError as exc:
        raise TemplateError(f"line {exc.lineno}: {exc.message}") from exc
    except Exception as exc:  # undefined variable, bad filter use, sandbox violation
        raise TemplateError(f"{type(exc).__name__}: {exc}") from exc


_CONTACT_ALIASES = {
    "name": "full_name",
    "full_name": "full_name",
    "email": "email",
    "phone": "phone",
    "location": "location",
}


def profile_from_data(data: dict[str, Any], lang: str) -> ResumeProfile:
    """Extracted data -> ResumeProfile, tolerant of the variable names (`name`, `link.github`,
    a top-level `email`) and of one bad field (dropped, not fatal)."""
    raw = {k: _unlatex(v) for k, v in data.items() if k not in ("letter", "__headings__")}
    contact = dict(raw.pop("contact", None) or {})
    for alias, key in _CONTACT_ALIASES.items():
        if isinstance(raw.get(alias), str):
            contact.setdefault(key, raw.pop(alias))
    links = list(raw.get("links") or [])
    for kind, url in (raw.pop("link", None) or {}).items():
        if url and not any(isinstance(lk, dict) and lk.get("url") == url for lk in links):
            links.append({"kind": kind, "url": url})
    raw["links"] = links
    flat = raw.pop("skills_flat", None)
    if flat and not raw.get("skills"):
        raw["skills"] = [{"name": "Skills", "skills": flat}]
    base: dict[str, Any] = {"language": lang if lang in ("fr", "en") else "other"}
    if contact:
        base["contact"] = contact
    for key, value in raw.items():
        try:
            ResumeProfile.model_validate({**base, key: value})
        except ValidationError:
            log.info("template data: dropped invalid field %r", key)
            continue
        base[key] = value
    return ResumeProfile.model_validate(base)


_SUPERSCRIPTS = str.maketrans("0123456789+-", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻")


_SUP_CMD = re.compile(r"\\textsuperscript\{([0-9+-]{1,3})\}")
_SUP_CARET = re.compile(r"(?<=[A-Za-z])\^([0-9])")


def _unlatex(node: Any) -> Any:
    """LaTeX the model left in data values: superscripts (textsuperscript, R^2) -> R²."""
    if isinstance(node, str):
        node = _SUP_CMD.sub(lambda m: m.group(1).translate(_SUPERSCRIPTS), node)
        return _SUP_CARET.sub(lambda m: m.group(1).translate(_SUPERSCRIPTS), node)
    if isinstance(node, list):
        return [_unlatex(v) for v in node]
    if isinstance(node, dict):
        return {k: _unlatex(v) for k, v in node.items()}
    return node
