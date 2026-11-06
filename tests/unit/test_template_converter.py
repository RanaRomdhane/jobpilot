"""Template conversion with a scripted model: checked edits, round trip, repairs (synthetic CV)."""

from __future__ import annotations

from typing import Any

from jobpilot_agents.template_converter import convert_template, repair
from jobpilot_latex.convert import (
    Replacement,
    ReplacementError,
    apply_replacements,
    detex,
    layout_warnings,
    round_trip,
    split_chunks,
)
from tests.unit.test_resume_agents import ScriptedProvider

CV = r"""\documentclass{article}
\usepackage[pdfauthor={Ada Example}]{hyperref}
\begin{document}
\begin{center}
  {\Large Ada Example} \\ ada@example.com
\end{center}

\section{Experience}
\textbf{ML Intern} -- Exemplia Labs \hfill June 2025 -- Sept 2025
\begin{itemize}
  \item Reached 73\% accuracy with \textbf{BERT}
  \item Cut review time by 35\%
\end{itemize}
\newpage
\end{document}
"""

SECTION_TEMPLATE = r"""\section{\VAR{h.experience}}
\BLOCK{for e in experiences}
\textbf{\VAR{e.title}} -- \VAR{e.company} \hfill \VAR{e|daterange(lang, ' -- ')}
\begin{itemize}
\BLOCK{for b in e.bullets}
  \item \VAR{b.text}|rich
\BLOCK{endfor}
\end{itemize}
\BLOCK{endfor}
\newpage
"""

EXPERIENCE_DATA = {
    "experiences": [
        {
            "company": "Exemplia Labs",
            "title": "ML Intern",
            "start": "2025-06",
            "end": "2025-09",
            "bullets": [
                {"text": "Reached 73% accuracy with **BERT**"},
                {"text": "Cut review time by 35%"},
            ],
        }
    ]
}


def replies() -> list[Any]:
    return [
        {  # preamble
            "edits": [{"find": "pdfauthor={Ada Example}", "replace": r"pdfauthor={\VAR{name}}"}],
            "data": {},
        },
        {  # header: whitespace differs from the source on purpose
            "edits": [
                {"find": r"{\Large   Ada Example}", "replace": r"{\Large \VAR{name}}"},
                {"find": "ada@example.com", "replace": r"\VAR{contact.email}"},
            ],
            "data": {"name": "Ada Example", "email": "ada@example.com"},
        },
        {  # section: filter written outside the braces (repaired), heading declared
            "template": SECTION_TEMPLATE,
            "data": EXPERIENCE_DATA,
            "headings": [{"key": "experience", "en": "Experience", "fr": "Expérience"}],
        },
    ]


async def test_converts_with_checked_chunks_and_round_trip() -> None:
    provider = ScriptedProvider(*replies())
    conv = await convert_template(provider, CV, "cv", tpm=0)  # type: ignore[arg-type]
    assert conv.ok, [(c.title, c.error) for c in conv.chunks]
    assert [c.kind for c in conv.chunks] == ["preamble", "header", "section", "end"]
    assert r"\VAR{b.text|rich}" in conv.template and "|rich\n" not in conv.template
    assert r"pdfauthor={\VAR{name}}" in conv.template
    assert conv.headings == {"experience": {"en": "Experience", "fr": "Expérience"}}
    assert conv.data["experiences"][0]["company"] == "Exemplia Labs"
    assert conv.check is not None and conv.check.ratio == 1.0
    assert layout_warnings(conv.template) == ["forced_page_break"]
    # the CV is untrusted data: spotlighted in every call
    assert all('<untrusted_data nonce="' in c["messages"][1]["content"] for c in provider.calls)


async def test_bad_chunk_is_retried_then_kept_as_is() -> None:
    wrong = {"template": r"\section{\VAR{h.experience}} nothing", "data": {}}
    provider = ScriptedProvider(
        {"edits": [], "data": {}},
        {"edits": [], "data": {}},
        wrong,
        wrong,
        wrong,
    )
    conv = await convert_template(provider, CV, "cv", tpm=0)  # type: ignore[arg-type]
    section = conv.chunks[2]
    assert section.error is not None and "round trip" in section.error
    assert section.template == section.source  # nothing unverified is kept
    assert section.attempt is not None and "nothing" in section.attempt
    assert len(provider.calls) == 5  # 1 + 2 retries for the section
    retry = provider.calls[3]["messages"]
    assert retry[-2]["role"] == "assistant" and "failed its check" in retry[-1]["content"]
    assert not conv.ok


def test_split_detex_and_round_trip() -> None:
    chunks = split_chunks(CV)
    assert chunks[2].title == "Experience"
    text = detex(chunks[2].source)
    assert "June 2025 – Sept 2025" in text and "73% accuracy with BERT" in text
    assert detex(r"St\'e \c{c}a \color{red}x R\textsuperscript{2}") == "Sté ça x R2"
    rt = round_trip("Cut time by 35% in 2025", "Cut time by 60% in 2025")
    assert not rt.ok and rt.missing_numbers == ["35"]
    rt2 = round_trip("Joined in June 2025 with 1200 users", "Joined in June with users")
    assert rt2.missing_numbers == ["1200", "2025"] and not rt2.ok


def test_replacements_are_exact_ordered_and_tolerant() -> None:
    src = r"\def\name{Ada}  \def\mail{a@x.io}"
    out = apply_replacements(
        src,
        [
            Replacement(r"\def\name{Ada} \def\mail{a@x.io}", r"\def\name{\VAR{name}} \def\mail{m}"),
            Replacement(r"\def\name{Ada}", r"\def\name{\VAR{name}}"),  # already done: skipped
        ],
    )
    assert out == r"\def\name{\VAR{name}} \def\mail{m}"
    try:
        apply_replacements(src, [Replacement("not there", "x")])
    except ReplacementError as exc:
        assert "not found" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ReplacementError")


def test_repair() -> None:
    assert repair(r"\VAR{b.text}|rich and \link{\VAR{c.url}|url}{x}") == (
        r"\VAR{b.text|rich} and \link{\VAR{c.url|url}}{x}"
    )
    assert repair(r"x\n  \BLOCK{endif} \newline \nb") == "x\n  \\BLOCK{endif} \\newline \\nb"


async def test_only_failed_chunks_are_converted_again() -> None:
    from jobpilot_agents.template_converter import convert_failed

    wrong = {"template": r"\section{\VAR{h.experience}} nothing", "data": {}}
    first = await convert_template(
        ScriptedProvider(replies()[0], replies()[1], wrong, wrong, wrong),  # type: ignore[arg-type]
        CV,
        "cv",
        tpm=0,
    )
    assert [c.error is None for c in first.chunks[:3]] == [True, True, False]
    provider = ScriptedProvider(replies()[2])  # one call: the failed section only
    again = await convert_failed(
        provider,  # type: ignore[arg-type]
        CV,
        "cv",
        template=first.template,
        failed={("section", "Experience")},
        data=first.data,
        headings=first.headings,
        tpm=0,
    )
    assert len(provider.calls) == 1
    assert again.check is not None and again.check.ok
    assert r"\VAR{b.text|rich}" in again.template and r"\VAR{contact.email}" in again.template
    assert again.data["experiences"][0]["company"] == "Exemplia Labs"


def test_repair_links_phone_and_date_ranges() -> None:
    src = (
        r"\link{\VAR{p.url}}{GitHub} \href{\VAR{c.url|url}}{x} "
        r"\link{tel:\VAR{contact.phone|url}}{y}"
        "\n"
        r"\VAR{exp.start|date(lang)} -- \VAR{exp.end|date(lang)}"
    )
    out = repair(src)
    assert r"\link{\VAR{p.url|url}}{GitHub}" in out
    assert r"\href{\VAR{c.url|url}}{x}" in out  # already filtered: unchanged
    assert r"\link{tel:\VAR{contact.phone|tel}}{y}" in out
    assert r"\VAR{exp|daterange(lang, ' -- ')}" in out
    from jobpilot_latex.render import render

    assert render(r"\VAR{p|tel}", {"p": "(+33) 1 02 03 04 05"}) == "+33102030405"


LETTER = r"""\documentclass{letter}
\def\name{Ada Example}
\begin{document}
\begin{letter}{Exemplia Team \ Tunis}
\opening{Dear Hiring Manager,}

I am writing about the Data Engineer role, which fits my background in pipelines very well.

I built a BERT model reaching 73\% accuracy, and I also ran large clusters in production for years.

\closing{Sincerely,}
\end{letter}
\end{document}
"""

LETTER_DATA = {
    "letter": {
        "recipient": ["Exemplia Team", "Tunis"],
        "opening": "Dear Hiring Manager,",
        "paragraphs": [
            "I am writing about the Data Engineer role, which fits my background in pipelines "
            "very well.",
            "I built a BERT model reaching 73% accuracy, and I also ran large clusters in "
            "production for years.",
        ],
        "closing": "Sincerely,",
    }
}

GOOD_LETTER = r"""\begin{letter}{\VAR{letter.recipient|ljoin(' \\\\ ')}}
\opening{\VAR{letter.opening}}

\BLOCK{for p in letter.paragraphs}
\VAR{p|rich}

\BLOCK{endfor}
\closing{\VAR{letter.closing}}
\end{letter}
"""


async def test_letter_with_hardcoded_sentences_is_rejected() -> None:
    # passes a round trip (fixed text always "matches"), and would repeat old claims forever
    lazy = GOOD_LETTER.replace(
        r"\VAR{p|rich}",
        r"I built a BERT model reaching 73\% accuracy, and I also ran large clusters in production "
        "for years, which is a sentence nobody can tailor.\n\\VAR{p|rich}",
    )
    provider = ScriptedProvider(
        {
            "edits": [{"find": "Ada Example", "replace": r"\VAR{name}"}],
            "data": {"name": "Ada Example"},
        },
        {"template": lazy, "data": LETTER_DATA},
        {"template": r"\opening{\VAR{letter.opening}}", "data": LETTER_DATA},
        {"template": GOOD_LETTER, "data": LETTER_DATA},
    )
    conv = await convert_template(provider, LETTER, "cover_letter", tpm=0)  # type: ignore[arg-type]
    body = conv.chunks[1]
    assert body.kind == "body" and body.mode == "rewrite" and body.error is None
    retries = [c["messages"][-1]["content"] for c in provider.calls[2:]]
    assert "too much fixed text" in retries[0] and "letter.paragraphs" in retries[1]
    assert r"\BLOCK{for p in letter.paragraphs}" in conv.template
    assert "73" not in conv.template


async def test_personal_values_may_not_stay_hardcoded() -> None:
    provider = ScriptedProvider(
        {"edits": [], "data": {"name": "Ada Example"}},  # the name stays in the preamble
        *[{"edits": [], "data": {}}] * 3,
    )
    conv = await convert_template(provider, LETTER, "cover_letter", tpm=0)  # type: ignore[arg-type]
    # the preamble keeps failing its check, so it stays verbatim and is reported
    assert conv.chunks[0].error is None or "hardcoded" in conv.chunks[0].error


def test_handle_filter() -> None:
    from jobpilot_latex.render import render

    tpl = r"\VAR{u|handle}"
    assert render(tpl, {"u": "https://linkedin.com/in/ada-example/"}) == "ada-example"
    assert render(tpl, {"u": ""}) == ""


def test_detex_keeps_text_after_layout_commands() -> None:
    src = r"""\vspace{0.05 cm}
\begin{twocolentry}{
  June 2025
}
  Intern
\end{twocolentry}"""
    assert detex(src) == "June 2025 Intern"
