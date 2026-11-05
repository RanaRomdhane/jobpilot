"""TemplateConverterAgent: a filled LaTeX CV / cover letter -> a Jinja template + the data it held.

One call per chunk (preamble, header, each section, the letter body), so every call is small
enough for the Groq minute budget. Pure structured output, no tools. The `.tex` is untrusted
data (spotlighted); nothing the model returns is used before `jobpilot_latex.convert` checked it:
edits must match the source exactly, the chunk must parse, and the chunk re-rendered with the
extracted data must show the same words as the original (`round_trip`). The user confirms the
resulting mapping before the template is saved.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field

from jobpilot_agents.structured import complete_structured, response_format
from jobpilot_core.config import LLMPurpose
from jobpilot_core.llm import ChatResult, LLMProvider
from jobpilot_core.llm.tokens import estimate_prompt_tokens
from jobpilot_core.ratelimit import RateLimitExceededError
from jobpilot_latex.convert import (
    Chunk,
    Replacement,
    ReplacementError,
    RoundTrip,
    apply_replacements,
    round_trip,
    split_chunks,
    static_words,
)
from jobpilot_latex.render import (
    LABELS,
    TemplateError,
    check_syntax,
    profile_context,
    profile_from_data,
    render,
)
from jobpilot_security import SPOTLIGHT_POLICY, spotlight

log = logging.getLogger(__name__)

MAX_OUTPUT = 5_000
BUDGET_SHARE = 0.85
REQUEST_OVERHEAD = 80

Kind = Literal["cv", "cover_letter"]
HEADING_KEYS = (
    "summary",
    "experience",
    "education",
    "projects",
    "skills",
    "languages",
    "certifications",
    "publications",
    "awards",
    "volunteering",
    "interests",
    "other",
)

VARIABLES = r"""Template language (Jinja with LaTeX delimiters):
- \VAR{expr} prints a value. Values are LaTeX-escaped automatically: never escape them yourself.
- \BLOCK{for x in xs} ... \BLOCK{endfor}, \BLOCK{if cond} ... \BLOCK{endif},
  loop.last / loop.first inside loops. \#{...} is a comment.
- Filters: |rich (text that may contain **bold** and [label](url): use it for every bullet,
  summary and paragraph), |url (a URL inside \href{...}{...} or \link{...}{...}),
  |daterange(lang, ' -- ') (start-end of an item, "present" when current), |date(lang),
  |ljoin(', ') (join a list), |handle (last part of a URL path, e.g. a LinkedIn handle).
  Filters go INSIDE the braces: \VAR{b.text|rich}, never \VAR{b.text}|rich.
- Dates in data are "YYYY-MM": always print them through |date(lang) or |daterange(...), never
  raw (\VAR{c.date|date(lang)} prints "Dec 2025" / "déc. 2025").

Variables:
- name, headline, summary, lang ("fr"/"en"), h.<key> (section headings, see below)
- contact.full_name, contact.email, contact.phone, contact.location
- link.linkedin, link.github, link.gitlab, link.portfolio, link.website (URLs, "" if none)
- experiences[]: title, company, location, start, end, current, bullets[].text, technologies[]
- education[]: institution, degree, field, location, start, end, grade, details[].text
- projects[]: name, role, url, start, end, description, bullets[].text, technologies[]
- skills[]: name, skills[] ; skills_flat[] (every skill)
- languages[]: name, level, cefr ; certifications[]: name, issuer, date, url
- extra_sections[]: title, bullets[].text  (anything else: publications, writing, awards...)
- letter (cover letters only): company, position, hiring_manager, recipient[] (address lines),
  opening (salutation), paragraphs[], closing, source (where the job was found), date"""

INSTRUCTIONS = f"""You turn one part of a filled LaTeX document (a CV or a cover letter) into a
reusable template, for its owner. JobPilot later fills the template with tailored data.

{VARIABLES}

Rules:
- Keep every piece of layout exactly: commands, environments, spacing, colours, fonts. Only the
  owner's personal content becomes variables.
- mode "edits" (preamble, header, cover-letter parts): return exact find/replace edits. Each
  `find` is copied character for character from the source and is unique enough; `replace` is
  the same text with the personal values replaced by \\VAR{{...}}. Example:
  find "\\def\\name{{Jane Doe}}" -> replace "\\def\\name{{\\VAR{{name}}}}". Keep edits small.
- mode "rewrite" (a CV section): return the whole section as a template. Repeated entries
  (jobs, schools, projects, skill lines, certifications...) become ONE \\BLOCK{{for}} loop whose
  body reproduces the first entry's layout; spacing between entries goes inside
  \\BLOCK{{if not loop.last}}...\\BLOCK{{endif}}. Optional values (location, url, dates) go inside
  \\BLOCK{{if ...}}. The section title becomes \\VAR{{h.<key>}}.
- data: the values the original part contains, as plain text in the JSON shape of the variables
  (un-escape LaTeX: "96\\%" -> "96%", "St\\'e" -> "Sté", "--" -> "–", "R\\textsuperscript{{2}}" ->
  "R²"; dates "YYYY-MM" or "YYYY"). Bold words inside a bullet are written **like this** and a
  link inside a bullet as [label](url). Rendering the template with data must give back the
  original text.
- headings: for each \\VAR{{h.<key>}} you use, the original title and its translation:
  {{"key": "experience", "en": "Professional Experience", "fr": "Expérience professionnelle"}}.
  key is one of: {", ".join(HEADING_KEYS)}.
- A cover letter's body (mode "rewrite") keeps its layout commands but NONE of its sentences:
  recipient lines through letter.recipient, the salutation through letter.opening, ONE loop
  \\BLOCK{{for p in letter.paragraphs}}\\VAR{{p|rich}}<blank line>\\BLOCK{{endfor}} replacing every
  paragraph, the closing through letter.closing. data.letter holds the original text.
- A URL prefix already in the template (https://github.com/\\github) needs only the last part:
  \\VAR{{link.github|handle}}. Every occurrence of a personal value becomes a variable.
- Items have only the fields listed above. Text with no field of its own (a subtitle, a tag such
  as "Published Research", a note after a title) stays inside the closest text field, e.g. the
  project's name. Superscripts are written as Unicode in data ("R²").
- Never invent content and never drop any: every visible word of the original must come back.

{SPOTLIGHT_POLICY}"""


class Edit(BaseModel):
    find: str = Field(min_length=1, max_length=4000)
    replace: str = Field(max_length=4000)


class Heading(BaseModel):
    key: str = Field(max_length=40)
    en: str = Field(max_length=120)
    fr: str = Field(max_length=120)


class ChunkProposal(BaseModel):
    edits: list[Edit] = Field(default_factory=list)
    template: str | None = Field(default=None, max_length=20_000)
    data: dict[str, Any] = Field(default_factory=dict)
    headings: list[Heading] = Field(default_factory=list)


def _schema(mode: str) -> dict[str, Any]:
    # `data` stays free-form: the variable list in the prompt gives its shape, and the round trip
    # (not the schema) is what proves it right. The full profile schema would cost ~1.5k tokens.
    out: dict[str, Any] = {
        "type": "object",
        "properties": {
            "data": {"type": "object"},
            "headings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string", "enum": list(HEADING_KEYS)},
                        "en": {"type": "string"},
                        "fr": {"type": "string"},
                    },
                    "required": ["key", "en", "fr"],
                },
            },
        },
        "required": ["data"],
    }
    if mode == "edits":
        out["properties"]["edits"] = {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"find": {"type": "string"}, "replace": {"type": "string"}},
                "required": ["find", "replace"],
            },
        }
        out["required"].append("edits")
    else:
        out["properties"]["template"] = {"type": "string"}
        out["required"].append("template")
    return out


def output_budget(messages: list[dict[str, Any]], mode: str, tpm: int) -> int:
    """All the minute budget leaves after the prompt (measured with the limiter's estimator)."""
    body = {"messages": messages, "response_format": response_format("t", _schema(mode))}
    room = int(BUDGET_SHARE * tpm) - estimate_prompt_tokens(body) - REQUEST_OVERHEAD
    return MAX_OUTPUT if tpm <= 0 else max(800, min(MAX_OUTPUT, room))


def chunk_mode(chunk: Chunk, kind: Kind) -> str:
    if chunk.kind == "body" and kind == "cover_letter":
        return "rewrite"
    return "rewrite" if chunk.kind == "section" and kind == "cv" else "edits"


@dataclass(slots=True)
class ConvertedChunk:
    kind: str
    title: str
    mode: str
    source: str
    template: str
    data: dict[str, Any]
    check: RoundTrip | None = None
    error: str | None = None
    attempt: str | None = None  # the last rejected proposal, shown for manual fixing


@dataclass(slots=True)
class Conversion:
    kind: Kind
    template: str
    chunks: list[ConvertedChunk]
    headings: dict[str, dict[str, str]]
    data: dict[str, Any]
    check: RoundTrip | None
    usage: list[ChatResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.error is None for c in self.chunks) and bool(self.check and self.check.ok)


def merge_data(into: dict[str, Any], part: dict[str, Any]) -> None:
    for key, value in part.items():
        if value in (None, "", [], {}):
            continue
        if isinstance(value, list) and isinstance(into.get(key), list):
            into[key] = [*into[key], *value]
        elif isinstance(value, dict) and isinstance(into.get(key), dict):
            merge_data(into[key], value)
        else:
            into[key] = value


def render_context(
    data: dict[str, Any], headings: dict[str, dict[str, str]], lang: str
) -> dict[str, Any]:
    """Variables for rendering a template with (possibly partial) extracted data."""
    ctx = profile_context(profile_from_data(data, lang), lang)
    ctx["h"] = {k: v.get(lang) or v.get("en") or "" for k, v in headings.items()}
    letter = {
        "company": "",
        "position": "",
        "hiring_manager": "",
        "recipient": [],
        "opening": "",
        "paragraphs": [],
        "closing": "",
        "source": "",
        "date": "",
    }
    letter.update({k: v for k, v in (data.get("letter") or {}).items() if v is not None})
    ctx["letter"] = letter
    return ctx


# \VAR{x}|rich  ->  \VAR{x|rich}   (a filter written after the closing brace prints as text)
_FILTER_OUTSIDE = re.compile(r"\\VAR\{([^{}]+)\}((?:\|\w+(?:\([^(){}]*\))?)+)")
# a literal "\n" (JSON escaping slip) is not a LaTeX command: make it a newline
_LITERAL_NEWLINE = re.compile(r"\\n(?![A-Za-z])")


# a value printed as a link target without |url (escaping alone would break `_` or `#` in it)
_UNFILTERED_LINK = re.compile(r"(\\(?:href|link|url)\{)\\VAR\{\s*([A-Za-z_][\w.]*)\s*\}")
# a phone number is not a URL: |url rejects it; |tel keeps + and digits
_TEL_URL = re.compile(r"tel:\\VAR\{\s*([A-Za-z_][\w.]*)\s*\|\s*url\s*\}")
# start -- end printed separately loses "present" for a current role
_TWO_DATES = re.compile(
    r"\\VAR\{\s*(\w+)\.start\s*\|\s*date\(lang\)\s*\}\s*(?:--|\u2013|-)\s*"
    r"\\VAR\{\s*\1\.end\s*\|\s*date\(lang\)\s*\}"
)


def repair(template: str) -> str:
    """Fix the slips models make with the LaTeX delimiters (each fix is unambiguous)."""
    out = _FILTER_OUTSIDE.sub(r"\\VAR{\1\2}", template)
    out = _LITERAL_NEWLINE.sub("\n", out)
    out = _TEL_URL.sub(r"tel:\\VAR{\1|tel}", out)
    out = _UNFILTERED_LINK.sub(r"\1\\VAR{\2|url}", out)
    return _TWO_DATES.sub(lambda m: rf"\VAR{{{m.group(1)}|daterange(lang, ' -- ')}}", out)


MAX_STATIC_WORDS = {"section": 12, "body": 12}
_PARAGRAPH_LOOP = re.compile(r"\\BLOCK\{\s*for\s+\w+\s+in\s+letter\.paragraphs\s*\}")


def _personal_values(data: dict[str, Any]) -> list[str]:
    """Contact details the part contained: they may not stay hardcoded in the template."""
    contact = data.get("contact") if isinstance(data.get("contact"), dict) else {}
    values = [data.get(k) for k in ("name", "email", "phone", "location")]
    values += [contact.get(k) for k in ("full_name", "email", "phone", "location")]
    return [v for v in dict.fromkeys(values) if isinstance(v, str) and len(v) >= 4]


def template_problem(
    chunk: Chunk, mode: str, kind: Kind, template: str, data: dict[str, Any]
) -> str | None:
    """What the round trip cannot see: content left hardcoded instead of becoming a variable."""
    if mode == "rewrite":
        fixed = static_words(template)
        limit = MAX_STATIC_WORDS.get(chunk.kind, 12)
        if len(fixed) > limit:
            sample = " ".join(fixed[:12])
            return (
                f"too much fixed text ({len(fixed)} words, at most {limit}): {sample}... "
                "Every sentence must come from a variable"
            )
    if kind == "cover_letter" and chunk.kind == "body" and not _PARAGRAPH_LOOP.search(template):
        return "the letter body must loop over letter.paragraphs"
    tags = re.sub(r"\\(?:VAR|BLOCK)\{[^{}]*\}", " ", template)
    left = [v for v in _personal_values(data) if v in tags]
    if left:
        return f"personal values still hardcoded: {left}: use the variables"
    return None


def _complete_headings(proposal: ChunkProposal, template: str, title: str) -> None:
    """Every h.<key> the template uses gets a heading (the section's own title if undeclared)."""
    have = {h.key for h in proposal.headings}
    for key in dict.fromkeys(re.findall(r"\bh\.(\w+)", template)):
        if key not in have:
            fr = LABELS["fr"].get(key, title)
            proposal.headings.append(Heading(key=key, en=title or key, fr=fr or title or key))


def _apply(chunk: Chunk, mode: str, proposal: ChunkProposal) -> str:
    if mode == "rewrite":
        if not proposal.template:
            raise ReplacementError("no template returned")
        text = repair(proposal.template)
        return text if text.endswith("\n") else text + "\n"
    edits = [Replacement(e.find, repair(e.replace)) for e in proposal.edits]
    return apply_replacements(chunk.source, edits) if edits else chunk.source


async def convert_template(
    provider: LLMProvider,
    source: str,
    kind: Kind,
    *,
    tpm: int,
    lang: str = "en",
    retries: int = 2,
) -> Conversion:
    """Convert every chunk; a chunk that fails its checks keeps the original text (no variables)."""
    chunks = split_chunks(source)
    out: list[ConvertedChunk] = []
    headings: dict[str, dict[str, str]] = {}
    data: dict[str, Any] = {}
    usage: list[ChatResult] = []
    for chunk in chunks:
        if chunk.kind == "end" or not chunk.source.strip():
            out.append(ConvertedChunk(chunk.kind, "", "keep", chunk.source, chunk.source, {}))
            continue
        mode = chunk_mode(chunk, kind)
        converted = await _convert_chunk(provider, chunk, kind, mode, lang, tpm, retries, usage)
        out.append(converted)
        if converted.error is None:
            for h in converted.data.pop("__headings__", []):
                headings[h["key"]] = {"en": h["en"], "fr": h["fr"]}
            merge_data(data, converted.data)
    template = "".join(c.template for c in out)
    check: RoundTrip | None = None
    try:
        check_syntax(template)
        check = round_trip(source, render(template, render_context(data, headings, lang)))
    except TemplateError as exc:
        log.warning("converted template does not render: %s", exc)
    return Conversion(kind, template, out, headings, data, check, usage)


async def convert_failed(
    provider: LLMProvider,
    source: str,
    kind: Kind,
    *,
    template: str,
    failed: set[tuple[str, str]],
    data: dict[str, Any],
    headings: dict[str, dict[str, str]],
    tpm: int,
    lang: str = "en",
    retries: int = 2,
) -> Conversion:
    """Re-convert only the chunks that failed last time (`failed` = {(kind, title)}).

    A failed chunk was kept verbatim, so its original text is still in `template`: it is
    replaced in place when the new attempt passes its checks. Chunks that already passed are not
    sent to the model again (the conversion of a CV costs ~40k tokens; one section ~4k).
    """
    out: list[ConvertedChunk] = []
    data = {k: v for k, v in data.items()}
    headings = dict(headings)
    usage: list[ChatResult] = []
    for chunk in split_chunks(source):
        if chunk.kind == "end" or not chunk.source.strip():
            continue
        mode = chunk_mode(chunk, kind)
        if (chunk.kind, chunk.title) not in failed:
            out.append(ConvertedChunk(chunk.kind, chunk.title, mode, chunk.source, "", {}))
            continue
        if template.count(chunk.source) != 1:
            out.append(
                ConvertedChunk(
                    chunk.kind,
                    chunk.title,
                    mode,
                    chunk.source,
                    "",
                    {},
                    error="edited since: run a full conversion",
                )
            )
            continue
        converted = await _convert_chunk(provider, chunk, kind, mode, lang, tpm, retries, usage)
        out.append(converted)
        if converted.error is None:
            template = template.replace(chunk.source, converted.template)
            for h in converted.data.pop("__headings__", []):
                headings[h["key"]] = {"en": h["en"], "fr": h["fr"]}
            merge_data(data, converted.data)
    check: RoundTrip | None = None
    try:
        check_syntax(template)
        check = round_trip(source, render(template, render_context(data, headings, lang)))
    except TemplateError as exc:
        log.warning("converted template does not render: %s", exc)
    return Conversion(kind, template, out, headings, data, check, usage)


async def _convert_chunk(
    provider: LLMProvider,
    chunk: Chunk,
    kind: Kind,
    mode: str,
    lang: str,
    tpm: int,
    retries: int,
    usage: list[ChatResult],
) -> ConvertedChunk:
    what = {
        "preamble": "the preamble",
        "header": "the header (name, title, contact line)",
        "section": f"the section {chunk.title!r}",
        "body": "the letter",
    }[chunk.kind]
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": INSTRUCTIONS},
        {
            "role": "user",
            "content": (
                f"Document: a {kind.replace('_', ' ')}. Part: {what}. Mode: {mode}.\n"
                + spotlight("latex", {"source": chunk.source})
            ),
        },
    ]
    error: str | None = None
    attempt: str | None = None
    for _ in range(retries + 1):
        try:
            proposal, calls = await complete_structured(
                provider,
                LLMPurpose.PARSE_RESUME,
                messages,
                name="template_chunk",
                schema=_schema(mode),
                parse=ChunkProposal.model_validate,
                max_tokens=output_budget(messages, mode, tpm),
            )
        except Exception as exc:
            return ConvertedChunk(
                chunk.kind,
                chunk.title,
                mode,
                chunk.source,
                chunk.source,
                {},
                # stable codes the UI translates: the minute/day budget, or anything else
                error="llm_budget" if isinstance(exc, RateLimitExceededError) else "llm_error",
            )
        usage.extend(calls)
        try:
            template = attempt = _apply(chunk, mode, proposal)
            _complete_headings(proposal, template, chunk.title)
            heads = {h.key: {"en": h.en, "fr": h.fr} for h in proposal.headings}
            check_syntax(template)
            problem = template_problem(chunk, mode, kind, template, proposal.data)
            if problem:
                raise ReplacementError(problem)
            rendered = render(template, render_context(proposal.data, heads, lang))
        except (ReplacementError, TemplateError) as exc:
            error = str(exc)
        else:
            check = round_trip(chunk.source, rendered)
            if check.ok or (mode == "edits" and chunk.kind == "preamble"):
                data = dict(proposal.data)
                data["__headings__"] = [h.model_dump() for h in proposal.headings]
                return ConvertedChunk(
                    chunk.kind, chunk.title, mode, chunk.source, template, data, check
                )
            error = (
                f"round trip {check.ratio:.2f}: missing {check.missing[:3]} extra {check.extra[:3]}"
                + (
                    f" missing numbers/dates {check.missing_numbers[:5]}"
                    if check.missing_numbers
                    else ""
                )
            )
        messages += [
            {"role": "assistant", "content": proposal.model_dump_json(exclude_defaults=True)},
            {
                "role": "user",
                "content": f"That result failed its check: {error}. Use only the variables "
                "listed in the instructions (anything else, e.g. publications or writing, is an "
                "extra_sections entry) and fix it.",
            },
        ]
    return ConvertedChunk(
        chunk.kind, chunk.title, mode, chunk.source, chunk.source, {}, error=error, attempt=attempt
    )
