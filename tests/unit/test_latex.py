"""LaTeX escaping, inline markup and template rendering (no TeX needed)."""

from __future__ import annotations

import pytest

from jobpilot_core.resume import Experience, ResumeProfile
from jobpilot_latex.compile import CompileError, safe_relpath
from jobpilot_latex.render import (
    TemplateError,
    check_syntax,
    date_range,
    format_date,
    plain,
    profile_context,
    render,
    rich,
)
from jobpilot_security import LatexSafe, latex_escape, latex_paragraphs, latex_url


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "R&D 100% $5 #1 a_b {x} ~ ^",
            r"R\&D 100\% \$5 \#1 a\_b \{x\} \textasciitilde{} \textasciicircum{}",
        ),
        (r"C:\path", r"C:\textbackslash{}path"),
        ("<b>|</b>", r"\textless{}b\textgreater{}\textbar{}\textless{}/b\textgreater{}"),
        ("[opt] item", "{[}opt{]} item"),
        ("Ingénieure – data “ML”", "Ingénieure – data “ML”"),
        ("multi\n\nline\tvalue  ", "multi line value"),
        ("nul\x00 esc\x1b bidi\u202eevil", "nul esc bidievil"),
        ("on\u2011premise 5\u202f% x\u00ady", "on-premise 5~\\% xy"),
        (None, ""),
        (42, "42"),
    ],
)
def test_latex_escape(raw: object, expected: str) -> None:
    assert latex_escape(raw) == expected


def test_escape_is_single_pass_and_total() -> None:
    # a backslash is never re-escaped by a later rule, and nothing TeX-special survives
    out = latex_escape("\\{}")
    assert out == r"\textbackslash{}\{\}"
    attack = r"\input{/etc/passwd} \write18{rm -rf /} $\x$ %comment"
    escaped = latex_escape(attack)
    assert "\\input" not in escaped and "\\write18" not in escaped
    assert all(c not in escaped.replace("\\textbackslash{}", "").replace("\\{", "").replace(
        "\\}", "").replace("\\$", "").replace("\\%", "") for c in "{}$%")  # fmt: skip


def test_latex_safe_bypasses_and_paragraphs() -> None:
    assert latex_escape(LatexSafe(r"\textbf{x}")) == r"\textbf{x}"
    assert latex_paragraphs("Un & deux\n\nTrois %\n\n\n") == "Un \\& deux\n\nTrois \\%"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://github.com/x/y?a=1&b=2#top", r"https://github.com/x/y?a=1&b=2\#top"),
        ("https://ex.com/100%25", r"https://ex.com/100\%25"),
        ("https://ex.com/a{b}\\c", r"https://ex.com/a\%7Bb\%7D\%5Cc"),
        ("mailto:me@example.com", "mailto:me@example.com"),
        ("javascript:alert(1)", ""),
        ("file:///etc/passwd", ""),
        ("/relative", ""),
        ("https://ex.com/a b", ""),
        ("https:///nohost", ""),
    ],
)
def test_latex_url(url: str, expected: str) -> None:
    assert latex_url(url) == expected


def test_rich_markup_is_escaped_piece_by_piece() -> None:
    assert rich("Built **React** UI & API") == r"Built \textbf{React} UI \& API"
    assert rich("See [the paper](https://ex.com/p.pdf) now") == (
        r"See \href{https://ex.com/p.pdf}{the paper} now"
    )
    assert rich("[x](javascript:alert(1))") == "x)"  # url rejected: label only, rest is text
    assert rich(r"**\input{/etc/passwd}**") == r"\textbf{\textbackslash{}input\{/etc/passwd\}}"
    assert rich("88% **recall**.") == r"88\% \textbf{recall}."
    assert plain("**React** and [paper](https://e.com)") == "React and paper"


def test_dates() -> None:
    assert format_date("2026-02", "fr") == "févr. 2026"
    assert format_date("2026-02", "en") == "Feb 2026"
    assert format_date("2024", "fr") == "2024"
    assert format_date(None) == ""
    exp = Experience(company="A", title="B", start="2025-06", end="2025-09")
    assert date_range(exp.model_dump(), "en", " -- ") == "June 2025 -- Sept 2025"
    cur = Experience(company="A", title="B", start="2025-06", current=True)
    assert date_range(cur, "fr") == "juin 2025 – présent"


PROFILE = ResumeProfile.model_validate(
    {
        "language": "en",
        "contact": {"full_name": "Ada & Co", "email": "ada@example.com"},
        "headline": "ML Engineer",
        "experiences": [
            {
                "id": "exp_1",
                "company": "Exemplia_Labs",
                "title": "Intern",
                "start": "2025-06",
                "end": "2025-09",
                "bullets": [{"id": "b_1", "text": "Cut costs by 50% with **Python**"}],
            }
        ],
        "skills": [{"id": "s_1", "name": "Code", "skills": ["Python", "C#"]}],
        "links": [{"id": "l_1", "kind": "github", "url": "https://github.com/ada"}],
    }
)

TEMPLATE = r"""\documentclass{article}
\begin{document}
\textbf{\VAR{name}} \href{\VAR{link.github|url}}{GitHub}
\section{\VAR{labels.experience}}
\BLOCK{for e in experiences}
\textbf{\VAR{e.title}} | \VAR{e.company} (\VAR{e|daterange(lang, ' -- ')})
\begin{itemize}
\BLOCK{for b in e.bullets}
  \item \VAR{b.text|rich}
\BLOCK{endfor}
\end{itemize}
\BLOCK{endfor}
\#{ a comment }
Skills: \VAR{skills_flat|ljoin(', ')}
\end{document}
"""


def test_render_escapes_every_value() -> None:
    out = render(TEMPLATE, profile_context(PROFILE))
    assert r"\textbf{Ada \& Co}" in out
    assert r"\href{https://github.com/ada}{GitHub}" in out
    assert r"\section{Experience}" in out
    assert r"\textbf{Intern} | Exemplia\_Labs (June 2025 -- Sept 2025)" in out
    assert r"\item Cut costs by 50\% with \textbf{Python}" in out
    assert r"Skills: Python, C\#" in out
    assert "comment" not in out and "BLOCK" not in out


def test_render_in_french_switches_labels_and_dates() -> None:
    out = render(TEMPLATE, profile_context(PROFILE, "fr"))
    assert r"\section{Expérience professionnelle}" in out and "juin 2025 -- sept. 2025" in out


def test_render_is_strict_and_sandboxed() -> None:
    with pytest.raises(TemplateError, match="Undefined"):
        render(r"\VAR{nope.missing}", {})
    with pytest.raises(TemplateError):
        render(r"\VAR{''.__class__.__mro__[1].__subclasses__()}", {})
    with pytest.raises(TemplateError, match="line 1"):
        check_syntax(r"\BLOCK{for x in y}")
    # a value cannot smuggle a block: template syntax inside data is plain text
    out = render(r"\VAR{v}", {"v": r"\BLOCK{for x in y}"})
    assert out == r"\textbackslash{}BLOCK\{for x in y\}"


@pytest.mark.parametrize(
    "name", ["/etc/passwd", "../x.tex", "a/../../b.sty", ".latexmkrc", "x.sh", "a/.hidden.sty", ""]
)
def test_asset_names_are_confined(name: str) -> None:
    with pytest.raises(CompileError):
        safe_relpath(name)


def test_asset_names_allowed() -> None:
    assert str(safe_relpath("TLCcoverletter.sty")) == "TLCcoverletter.sty"
    assert str(safe_relpath("fonts\\Roboto.ttf")) == "fonts/Roboto.ttf"


def test_profile_link_is_the_shortest_of_its_kind() -> None:
    profile = ResumeProfile.model_validate(
        {
            "links": [
                {"kind": "github", "url": "https://github.com/ada/project-one"},
                {"kind": "github", "url": "https://github.com/ada"},
                {"kind": "github", "url": "https://github.com/ada/another-project"},
            ]
        }
    )
    assert profile_context(profile, "en")["link"]["github"] == "https://github.com/ada"
