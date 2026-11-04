"""Deterministic half of template onboarding: split a filled `.tex` into chunks, apply exact
replacements, and compare an original document with a rendered template ("round trip").

The LLM half (`jobpilot_agents.template_converter`) proposes, per chunk, either a list of exact
find -> replace edits (preamble, header, letter fields) or a rewritten Jinja chunk with loops
(sections). Nothing it returns is trusted: replacements must match the source exactly, the
result must parse, and `round_trip` re-renders it with the extracted data and compares the
visible text with the original. The user then confirms the mapping before it is saved.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Literal

ChunkKind = Literal["preamble", "header", "section", "body", "end"]

_BEGIN_DOC = re.compile(r"^[ \t]*\\begin\{document\}[^\n]*\n?", re.MULTILINE)
_END_DOC = re.compile(r"^[ \t]*\\end\{document\}", re.MULTILINE)
# \section, \section*, and the usual CV-class variants (\cvsection, \sectionTitle, ...)
_SECTION = re.compile(
    r"^[ \t]*\\(?:sub)?(?:cv)?section(?:title)?\*?\s*(?:\[[^\]]*\])?\{",
    re.MULTILINE | re.IGNORECASE,
)


@dataclass(slots=True)
class Chunk:
    kind: ChunkKind
    source: str
    title: str = ""


def split_chunks(source: str) -> list[Chunk]:
    """preamble | header (before the first section) | one chunk per section | end."""
    begin = _BEGIN_DOC.search(source)
    if begin is None:
        raise ValueError("no \\begin{document}: is this the main .tex file?")
    end = _END_DOC.search(source, begin.end())
    end_at = end.start() if end else len(source)
    chunks = [Chunk("preamble", source[: begin.end()])]
    body = source[begin.end() : end_at]
    starts = [m.start() for m in _SECTION.finditer(body)]
    if not starts:
        chunks.append(Chunk("body", body))
    else:
        if body[: starts[0]].strip() or starts[0]:
            chunks.append(Chunk("header", body[: starts[0]]))
        for a, b in zip(starts, [*starts[1:], len(body)], strict=True):
            text = body[a:b]
            chunks.append(Chunk("section", text, _section_title(text)))
    chunks.append(Chunk("end", source[end_at:]))
    return chunks


def _section_title(text: str) -> str:
    m = _SECTION.match(text)
    if not m:
        return ""
    depth, i = 1, m.end()
    while i < len(text) and depth:
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        i += 1
    return detex(text[m.end() : i - 1])


class ReplacementError(ValueError):
    pass


@dataclass(slots=True)
class Replacement:
    find: str
    replace: str


def _find_all(source: str, needle: str) -> list[tuple[int, int]]:
    """Exact occurrences; failing that, occurrences that differ only in whitespace runs."""
    out: list[tuple[int, int]] = []
    start = 0
    while (i := source.find(needle, start)) != -1:
        out.append((i, i + len(needle)))
        start = i + len(needle)
    if out:
        return out
    parts = [re.escape(p) for p in needle.split()]
    if not parts:
        return []
    pattern = re.compile(r"\s*".join(parts))
    return [(m.start(), m.end()) for m in pattern.finditer(source)]


def apply_replacements(source: str, edits: list[Replacement]) -> str:
    """Apply edits in order, each to the result of the previous ones (whitespace-tolerant).

    An edit whose `find` is gone because an earlier, larger edit already replaced that text is
    skipped when its `replace` is already there; any other missing `find` is an error.
    """
    text = source
    for e in edits:
        if not e.find.strip():
            raise ReplacementError("empty find")
        found = _find_all(text, e.find)
        if not found:
            if e.replace and e.replace in text:
                continue
            raise ReplacementError(f"text not found: {e.find[:80]!r}")
        out, pos = [], 0
        for a, b in found:
            out += [text[pos:a], e.replace]
            pos = b
        text = "".join([*out, text[pos:]])
    return text


# --------------------------------------------------------------------------- detex

_ACCENTS = {
    "'": "\u0301",
    "`": "\u0300",
    "^": "\u0302",
    '"': "\u0308",
    "~": "\u0303",
    "c": "\u0327",
}
_SYMBOLS = {
    "&": "&",
    "%": "%",
    "$": "$",
    "#": "#",
    "_": "_",
    "{": "{",
    "}": "}",
    "textbackslash": "\\",
    "textasciitilde": "~",
    "textasciicircum": "^",
    "textless": "<",
    "textgreater": ">",
    "textbar": "|",
    "textbullet": "•",
    "LaTeX": "LaTeX",
    "TeX": "TeX",
    "ldots": "…",
    "dots": "…",
    "textquoteright": "’",
    "textendash": "–",
    "textemdash": "—",
}
# commands whose arguments are layout, not text
_DROP_WITH_ARGS = re.compile(
    r"\\(?:vspace|hspace|kern|fontsize|setlength|addtolength|definecolor|color|includegraphics"
    r"|setcolumnwidth|needspace|titlerule|rule|selectfont|linespread|label|ref|pageref"
    r"|usepackage|RequirePackage|documentclass|newcommand|renewcommand|def"
    r"|newenvironment|input|include|pagestyle|thispagestyle|AND|newpage|clearpage)\*?"
    r"(?:\s*\[[^\]]*\])*(?:\s*\{(?:[^{}]|\{[^{}]*\})*\})*"
)
_URL_CMD = re.compile(r"\\(?:href|link|hrefWithoutArrow)\s*\{(?:[^{}]|\{[^{}]*\})*\}")
_CMD = re.compile(r"\\[A-Za-z@]+\*?")


def detex(source: str) -> str:
    """LaTeX -> the words a reader sees (approximate, for comparisons only)."""
    s = re.sub(r"(?<!\\)%[^\n]*", "", source)  # comments
    s = re.sub(r"\\([&%$#_{}])", lambda m: f"\x00{ord(m.group(1))}\x00", s)
    # symbol accents (\'e, \^{o}); the letter accent \c only as \c{c} or "\c c" (not \color)
    s = re.sub(r"\\(['`^\"~])\s*\{?([A-Za-z])\}?", lambda m: m.group(2) + _ACCENTS[m.group(1)], s)
    s = re.sub(
        r"\\c(?:\{([A-Za-z])\}|\s+([A-Za-z])\b)",
        lambda m: (m.group(1) or m.group(2)) + _ACCENTS["c"],
        s,
    )
    s = _URL_CMD.sub("", s)  # \href{url}: keep only the visible label (next group)
    s = re.sub(r"\\textsuperscript\{([^{}]*)\}", r"\1", s)
    s = re.sub(r"\\([A-Za-z]+)\{\}", lambda m: _SYMBOLS.get(m.group(1), ""), s)
    s = re.sub(
        r"\\(textbackslash|textasciitilde|textasciicircum|textless|textgreater|textbar"
        r"|textbullet|ldots|dots|textendash|textemdash)\b",
        lambda m: _SYMBOLS[m.group(1)],
        s,
    )
    # \begin{env} / \end{env}: drop the name only; arguments after it are often text (dates)
    s = re.sub(r"\\(?:begin|end)\s*\{[^{}]*\}(?:\s*\[[^\]]*\])?", " ", s)
    s = _DROP_WITH_ARGS.sub(" ", s)
    s = _CMD.sub(" ", s)
    s = re.sub(r"\x00(\d+)\x00", lambda m: chr(int(m.group(1))), s)
    s = s.replace("``", "“").replace("''", "”").replace("---", "—").replace("--", "–")
    s = re.sub(r"[{}\[\]$~\\]", " ", s)
    s = s.replace("\\\\", " ")
    return unicodedata.normalize("NFC", re.sub(r"\s+", " ", s)).strip()


def _words(text: str) -> list[str]:
    folded = unicodedata.normalize("NFKD", text.lower())
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    return re.findall(r"[a-z0-9]+(?:[.,/+#][a-z0-9]+)*", folded)


@dataclass(slots=True)
class RoundTrip:
    ratio: float  # 1.0 = same visible words
    missing: list[str] = field(default_factory=list)  # in the original, not in the render
    extra: list[str] = field(default_factory=list)  # in the render, not in the original

    missing_numbers: list[str] = field(default_factory=list)  # dates, metrics: never tolerated

    @property
    def ok(self) -> bool:
        return self.ratio >= 0.97 and not self.missing_numbers


def round_trip(original: str, rendered: str, context_words: int = 6) -> RoundTrip:
    """Compare the visible words of the original LaTeX and of the rendered template."""
    a, b = _words(detex(original)), _words(detex(rendered))
    numbers = [w for w in a if re.search(r"\d", w) and len(w) >= 3]
    rendered_words = set(b)
    missing_numbers = sorted({n for n in numbers if n not in rendered_words})
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    missing: list[str] = []
    extra: list[str] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag in ("delete", "replace"):
            lo = max(0, i1 - context_words)
            missing.append(" ".join(a[lo:i1]) + " [" + " ".join(a[i1:i2]) + "]")
        if tag in ("insert", "replace"):
            lo = max(0, j1 - context_words)
            extra.append(" ".join(b[lo:j1]) + " [" + " ".join(b[j1:j2]) + "]")
    return RoundTrip(round(sm.ratio(), 4), missing[:20], extra[:20], missing_numbers[:20])


def layout_warnings(source: str) -> list[str]:
    """Things in a template that fight the page budget or the sandbox."""
    out: list[str] = []
    if re.search(r"\\(newpage|clearpage|pagebreak)\b", source):
        out.append("forced_page_break")
    if re.search(r"\\(write18|immediate\s*\\write|openout|ShellEscape|directlua)", source):
        out.append("shell_or_file_write")
    if re.search(r"\\(input|include)\s*\{\s*/", source):
        out.append("absolute_input")
    return out
