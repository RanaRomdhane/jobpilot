"""LaTeX escaping: the one function every value inserted into a template goes through.

`latex_escape` is applied automatically to every `\\VAR{...}` output by the template environment
(`jobpilot_latex.render`), so a value from the profile, the LLM or a job posting can never inject a
command, close a group, start a comment or switch to math mode. Only `LatexSafe` strings (built
by our own code from already-escaped parts) bypass it.

Single pass (`str.translate`), so an escape is never re-escaped.
"""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import quote, urlsplit

_MAP: dict[int, str] = {
    ord("\\"): r"\textbackslash{}",
    ord("&"): r"\&",
    ord("%"): r"\%",
    ord("$"): r"\$",
    ord("#"): r"\#",
    ord("_"): r"\_",
    ord("{"): r"\{",
    ord("}"): r"\}",
    ord("~"): r"\textasciitilde{}",
    ord("^"): r"\textasciicircum{}",
    # not special to TeX, but wrong glyphs in OT1 or parsed as an optional argument (\item[...])
    ord("<"): r"\textless{}",
    ord(">"): r"\textgreater{}",
    ord("|"): r"\textbar{}",
    ord("["): "{[}",
    ord("]"): "{]}",
    # typographic: keep them as text
    ord(" "): "~",  # no-break space
    # characters LLMs emit that pdfLaTeX's utf8 input does not know ("not set up for use")
    ord("‐"): "-",  # hyphen
    ord("‑"): "-",  # non-breaking hyphen
    ord("‒"): "–",  # figure dash
    ord("−"): "-",  # minus sign
    ord("­"): "",  # soft hyphen
    ord(" "): " ",  # thin space
    ord(" "): " ",  # hair space
    ord(" "): "~",  # narrow no-break space (French « : »)
    ord(" "): "~",  # figure space
}
# control characters (incl. NUL, ESC) and bidi overrides/isolates (visual spoofing)
_DROP = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f​‪-‮⁦-⁩﻿]")
_SPACES = re.compile(r"[ \t\r\n]+")


class LatexSafe(str):
    """A string that is already valid LaTeX (our own output). Never build one from raw input."""

    __slots__ = ()


def latex_escape(value: object) -> str:
    """Plain text -> LaTeX text. Whitespace (newlines included) collapses to single spaces."""
    if isinstance(value, LatexSafe):
        return str(value)
    if value is None:
        return ""
    text = unicodedata.normalize("NFC", str(value))
    text = _SPACES.sub(" ", _DROP.sub("", text)).strip()
    return text.translate(_MAP)


def latex_paragraphs(value: object) -> LatexSafe:
    """Multi-paragraph text (blank-line separated) -> escaped paragraphs joined by `\\par`."""
    parts = re.split(r"\n\s*\n", str(value or ""))
    return LatexSafe("\n\n".join(latex_escape(p) for p in parts if p.strip()))


_URL_SAFE = "/:?=&@+,;!*'()-._"


def latex_url(value: object) -> str:
    """URL for `\\href{...}` / `\\url{...}`: http(s) or mailto only, percent-encoded, `%#` escaped.

    Anything else (javascript:, file:, relative paths, text with spaces) returns "" so the
    template can fall back to plain text.
    """
    raw = _DROP.sub("", str(value or "")).strip()
    if not raw or any(c.isspace() for c in raw):
        return ""
    parts = urlsplit(raw)
    scheme = parts.scheme.lower()
    if scheme in ("http", "https"):
        if not parts.netloc:
            return ""
    elif scheme != "mailto":
        return ""
    encoded = quote(raw, safe=_URL_SAFE + "%#")  # keep existing %xx escapes and the fragment
    # hyperref reads \% and \# inside \href; braces and backslashes are already %-encoded
    return encoded.replace("%", r"\%").replace("#", r"\#")
