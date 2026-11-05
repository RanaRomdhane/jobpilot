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
