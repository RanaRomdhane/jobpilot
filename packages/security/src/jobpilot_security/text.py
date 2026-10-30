"""Turn untrusted HTML/text into plain text, and surface content a human reader would not see.

Agents never receive raw HTML. Text hidden from the human reader (CSS-hidden elements, tiny or
white fonts, HTML comments, zero-width or bidi control characters) is a classic carrier for
indirect prompt injection, so it is extracted separately and handed to the detector.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from html.parser import HTMLParser

ZERO_WIDTH = frozenset("​‌‍‎‏⁠⁡⁢⁣⁤﻿­")
BIDI_CONTROLS = frozenset("‪‫‬‭‮⁦⁧⁨⁩")

_VOID = frozenset(
    {
        "br",
        "hr",
        "img",
        "input",
        "meta",
        "link",
        "area",
        "base",
        "col",
        "embed",
        "source",
        "track",
        "wbr",
        "param",
    }
)
_BLOCK = frozenset(
    {
        "p",
        "div",
        "br",
        "li",
        "tr",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "table",
        "section",
        "article",
        "header",
        "footer",
        "blockquote",
        "ul",
        "ol",
        "td",
    }
)
_SKIP = frozenset({"script", "style", "head", "title", "noscript", "template", "svg"})

_HIDDEN_STYLE = re.compile(
    r"display\s*:\s*none|visibility\s*:\s*hidden|opacity\s*:\s*0(?:\.0+)?\s*(?:;|$)"
    r"|font-size\s*:\s*(?:0|0?\.\d+|1|2)(?:px|pt|em|rem)?\s*(?:;|$)"
    r"|(?:^|;)\s*color\s*:\s*(?:#fff(?:fff)?|white|rgba?\(\s*255\s*,\s*255\s*,\s*255)"
    r"|(?:max-)?height\s*:\s*0(?:px)?\s*(?:;|$)|clip\s*:\s*rect\(\s*0",
    re.IGNORECASE,
)
_WS = re.compile(r"[ \t\f\v\r]+")
_NL = re.compile(r"\n\s*\n+")


@dataclass(slots=True)
class CleanText:
    text: str  # what a human sees, normalized
    hidden: list[str] = field(default_factory=list)  # hidden segments (comments, CSS-hidden, ...)
    zero_width_count: int = 0
    bidi_count: int = 0


class _Extractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.visible: list[str] = []
        self.hidden: list[str] = []
        self._stack: list[tuple[str, bool]] = []  # (tag, hidden)
        self._skip_depth = 0

    @property
    def _in_hidden(self) -> bool:
        return any(h for _, h in self._stack)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP:
            self._skip_depth += 1
            return
        a = {k.lower(): (v or "") for k, v in attrs}
        hidden = (
            "hidden" in a
            or a.get("aria-hidden") == "true"
            or bool(_HIDDEN_STYLE.search(a.get("style", "")))
        )
        if tag in _BLOCK:
            self.visible.append("\n")
        if tag not in _VOID:
            self._stack.append((tag, hidden))
        if tag == "img" and a.get("alt"):
            (self.hidden if hidden or self._in_hidden else self.visible).append(f" {a['alt']} ")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        for i in range(len(self._stack) - 1, -1, -1):
            if self._stack[i][0] == tag:
                del self._stack[i:]
                break
        if tag in _BLOCK:
            self.visible.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        (self.hidden if self._in_hidden else self.visible).append(data)

    def handle_comment(self, data: str) -> None:
        if data.strip():
            self.hidden.append(data)


def normalize(text: str) -> str:
    """NFKC, drop zero-width/bidi controls, collapse whitespace."""
    text = unicodedata.normalize("NFKC", text)
    text = "".join(ch for ch in text if ch not in ZERO_WIDTH and ch not in BIDI_CONTROLS)
    text = _WS.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    return _NL.sub("\n\n", text).strip()


def fold(text: str) -> str:
    """Lowercase and strip diacritics, for pattern matching only (é→e, Arabic harakat removed)."""
    decomposed = unicodedata.normalize("NFD", text.casefold())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def html_to_text(html: str) -> CleanText:
    parser = _Extractor()
    parser.feed(html)
    parser.close()
    raw_visible = "".join(parser.visible)
    hidden = [normalize(h) for h in parser.hidden if normalize(h)]
    return CleanText(
        text=normalize(raw_visible),
        hidden=hidden,
        zero_width_count=sum(ch in ZERO_WIDTH for ch in raw_visible),
        bidi_count=sum(ch in BIDI_CONTROLS for ch in raw_visible),
    )


def clean_plain(text: str) -> CleanText:
    return CleanText(
        text=normalize(text),
        zero_width_count=sum(ch in ZERO_WIDTH for ch in text),
        bidi_count=sum(ch in BIDI_CONTROLS for ch in text),
    )


def truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    cut = text[: max_chars - 1]
    space = cut.rfind(" ")
    return (cut[:space] if space > max_chars * 0.7 else cut) + "…"
