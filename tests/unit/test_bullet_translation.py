"""Bullets in the master's language on a CV in another language (synthetic profile, no network)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx2
import pytest
from openai import APIConnectionError

from jobpilot_agents.tailor import translation_output_tokens
from jobpilot_core.config import LLMPurpose
from jobpilot_core.ratelimit import RateLimitExceededError
from jobpilot_core.resume import ResumeProfile
from jobpilot_latex.compile import CompileResult
from jobpilot_resume.tailoring import (
    PlannedBullet,
    PlannedItem,
    TailorPlan,
    Validated,
    build_tailored,
    master_worded_bullets,
    validate_plan,
)
from jobpilot_worker.documents import DocumentFactory, translate_master_wording
from tests.unit.test_resume_agents import ScriptedProvider

PAPER = "Co-authored a survey on graph sampling"
OLD_JOB = "Built 9 dashboards in Power BI"
MASTER = ResumeProfile.model_validate(
    {
        "language": "en",
        "contact": {"full_name": "Ada Example", "email": "ada@example.com"},
        "headline": "Machine Learning Engineer",
        "experiences": [
            {
                "id": "exp_1",
                "company": "Exemplia Labs",
                "title": "ML Engineer",
                "bullets": [
                    {"id": "b_1", "text": "Built a BERT model reaching 73% accuracy"},
                    {"id": "b_2", "text": "Automated reviews, cutting review time by 35%"},
                ],
            },
            {
                "id": "exp_2",
                "company": "Demo Analytics",
                "title": "Data Intern",
                "bullets": [{"id": "b_3", "text": OLD_JOB}],
            },
        ],
        "projects": [
            {
                "id": "prj_1",
                "name": "Graph demo",
                "bullets": [
                    {"id": "b_4", "text": "Ranked graph samples with PyTorch"},
                    {"id": "b_5", "text": PAPER},
                ],
            }
        ],
        "education": [{"id": "edu_1", "institution": "Exemplia School", "degree": "MSc"}],
        "skills": [{"id": "skl_1", "name": "Code", "skills": ["Python", "PyTorch"]}],
    }
)
REWRITES = [
    PlannedBullet(source="b_1", text="Conception d'un modèle **BERT** à 73 % d'exactitude"),
    PlannedBullet(source="b_2", text="Revues automatisées, temps de revue réduit de 35 %"),
]
# what the guardrail leaves: exp_2 and the paper restored in English, b_4 rewritten
PENDING = {"b_3": OLD_JOB, "b_5": PAPER}


def checked_plan(lang: str = "fr") -> Validated:
    plan = TailorPlan(
        language=lang,
        headline="Ingénieure IA",
        experiences=[
            PlannedItem(id="exp_1", bullets=[b.model_copy() for b in REWRITES]),
        ],
        projects=[
            PlannedItem(
                id="prj_1",
                bullets=[
                    PlannedBullet(source="b_4", text="Classement d'échantillons avec PyTorch")
                ],
            )
        ],
        education=["edu_1"],
    )
    return validate_plan(MASTER, plan)


def answer(**texts: str) -> dict[str, Any]:
    return {"bullets": [{"source": k, "text": v} for k, v in texts.items()]}


def bullet(checked: Validated, source: str) -> str:
    groups = (checked.plan.experiences, checked.plan.projects)
    return next(b.text for g in groups for i in g for b in i.bullets if b.source == source)


def codes(checked: Validated, source: str) -> list[str]:
    return [f.code for f in checked.findings if f.where == source]


class BudgetOut(ScriptedProvider):
    def __init__(self, error: Exception) -> None:
        super().__init__()
        self.error = error

    async def complete(self, purpose: LLMPurpose, messages: list[dict[str, Any]], **kw: Any) -> Any:
        self.calls.append({"purpose": purpose, "messages": messages, **kw})
        raise self.error


def wrapped_budget_error() -> APIConnectionError:
    error = APIConnectionError(request=httpx2.Request("POST", "https://llm.invalid"))
    error.__cause__ = RateLimitExceededError("daily budget")
    return error


def test_restored_and_reverted_bullets_are_the_ones_pending() -> None:
    checked = checked_plan()
    assert master_worded_bullets(MASTER, checked.plan) == PENDING
    # a failed rewrite is reverted to the master wording, and is pending like a restored one
    item = TailorPlan(
        language="fr",
        experiences=[
            PlannedItem(
                id="exp_1",
                bullets=[PlannedBullet(source="b_2", text="Temps de revue réduit de 90 %")],
            )
        ],
    )
    reverted = validate_plan(MASTER, item)
    assert "b_2" in master_worded_bullets(MASTER, reverted.plan)


async def test_success_translates_only_those_bullets_in_one_small_request() -> None:
    checked = checked_plan()
    fr_job, fr_paper = (
        "9 tableaux de bord créés avec Power BI",
        "Coautrice d'une étude sur les graphes",
    )
    provider = ScriptedProvider(answer(b_3=fr_job, b_5=fr_paper))
    done = await translate_master_wording(
        provider,  # type: ignore[arg-type]
        MASTER,
        checked,
        [],
        tpm=8_000,
        form="feminine",
    )
    assert sorted(done) == ["b_3", "b_5"]
    assert bullet(checked, "b_3") == fr_job and bullet(checked, "b_5") == fr_paper
    assert bullet(checked, "b_1") == REWRITES[0].text  # the model's own rewrite is left alone
    assert not any(f.code.startswith("translation") for f in checked.findings)

    assert len(provider.calls) == 1
    call = provider.calls[0]
    assert call["purpose"] is LLMPurpose.TAILOR and call["max_tokens"] <= 1_000
    system, user = (m["content"] for m in call["messages"])
    assert "French" in system and "spécialisée" in system
    assert user == f"[b_3] {OLD_JOB}\n[b_5] {PAPER}"  # no profile, no job

    built = build_tailored(MASTER, checked.plan)
    assert built.language == "fr" and built.experiences[1].bullets[0].text == fr_job
    assert built.experiences[1].company == "Demo Analytics"


async def test_nothing_to_translate_costs_nothing() -> None:
    provider = ScriptedProvider()
    same_language = checked_plan("en")
    assert await translate_master_wording(provider, MASTER, same_language, [], tpm=0) == []  # type: ignore[arg-type]

    checked = checked_plan()
    for b in [*checked.plan.experiences[1].bullets, checked.plan.projects[0].bullets[-1]]:
        b.text = "Déjà traduit"
    assert await translate_master_wording(provider, MASTER, checked, [], tpm=0) == []  # type: ignore[arg-type]
    assert provider.calls == []


@pytest.mark.parametrize(
    ("translation", "keywords", "detail"),
    [
        ("12 tableaux de bord créés avec Power BI", [], "new_number: 12"),
        ("9 tableaux de bord créés avec Tableau et Looker", [], "Looker"),
        ("9 tableaux de bord créés et supervision incluse", ["supervision"], "job_keyword"),
    ],
)
async def test_failed_check_keeps_the_master_wording(
    translation: str, keywords: list[str], detail: str
) -> None:
    checked = checked_plan()
    good = "Coautrice d'une étude sur les graphes"
    provider = ScriptedProvider(answer(b_3=translation, b_5=good))
    done = await translate_master_wording(provider, MASTER, checked, keywords, tpm=0)  # type: ignore[arg-type]
    assert done == ["b_5"] and bullet(checked, "b_5") == good  # the other bullet still passes
    assert bullet(checked, "b_3") == OLD_JOB
    rejected = next(f for f in checked.findings if f.code == "translation_rejected")
    assert rejected.where == "b_3" and detail in rejected.detail


async def test_inflated_claim_is_cut_or_refused() -> None:
    checked = checked_plan()
    provider = ScriptedProvider(
        answer(
            b_3="9 tableaux de bord créés avec Power BI, garantissant la fiabilité",
            b_5="Maîtrise des graphes grâce à une étude",
        )
    )
    done = await translate_master_wording(provider, MASTER, checked, [], tpm=0)  # type: ignore[arg-type]
    assert done == ["b_3"]
    assert bullet(checked, "b_3") == "9 tableaux de bord créés avec Power BI"
    assert codes(checked, "b_3") == ["unsupported_claim"]
    assert bullet(checked, "b_5") == PAPER and codes(checked, "b_5") == ["translation_rejected"]


async def test_metric_keeps_its_meaning_in_translation() -> None:
    checked = validate_plan(
        MASTER,
        TailorPlan(
            language="fr",
            experiences=[
                PlannedItem(
                    id="exp_1",
                    bullets=[
                        PlannedBullet(source="b_1", text=MASTER.experiences[0].bullets[0].text)
                    ],
                )
            ],
        ),
    )
    provider = ScriptedProvider(
        answer(
            b_1="Modèle BERT atteignant 73 % de précision",
            b_3="9 tableaux de bord créés avec Power BI",
            b_5="Coautrice d'une étude sur les graphes",
        )
    )
    await translate_master_wording(provider, MASTER, checked, [], tpm=0)  # type: ignore[arg-type]
    assert bullet(checked, "b_1") == "Modèle BERT atteignant 73 % d'exactitude"


async def test_missing_unchanged_or_foreign_answers_change_nothing() -> None:
    checked = checked_plan()
    provider = ScriptedProvider(answer(b_3=OLD_JOB, b_1="Réécriture non demandée à 73 %"))
    assert await translate_master_wording(provider, MASTER, checked, [], tpm=0) == []  # type: ignore[arg-type]
    assert bullet(checked, "b_3") == OLD_JOB and bullet(checked, "b_5") == PAPER
    assert bullet(checked, "b_1") == REWRITES[0].text  # an id it was not given is ignored
    assert codes(checked, "b_3") == ["translation_missing"]
    assert codes(checked, "b_5") == ["translation_missing"]


@pytest.mark.parametrize(
    "error",
    [
        RateLimitExceededError("daily budget"),
        # how the OpenAI client reports an error raised in its transport
        wrapped_budget_error(),
    ],
    ids=["direct", "wrapped"],
)
async def test_budget_exhausted_keeps_the_master_wording(error: Exception) -> None:
    checked = checked_plan()
    done = await translate_master_wording(BudgetOut(error), MASTER, checked, [], tpm=0)  # type: ignore[arg-type]
    assert done == []
    assert bullet(checked, "b_3") == OLD_JOB and bullet(checked, "b_5") == PAPER
    found = [f for f in checked.findings if f.code == "translation_unavailable"]
    assert {f.where for f in found} == {"b_3", "b_5"} and {f.detail for f in found} == {
        "llm_budget"
    }


@pytest.mark.parametrize(
    "provider",
    [
        pytest.param(ScriptedProvider("not json", "still not"), id="invalid"),
        pytest.param(ScriptedProvider(answer(b_3="tronqué"), finish="length"), id="truncated"),
        pytest.param(
            BudgetOut(APIConnectionError(request=httpx2.Request("POST", "https://llm.invalid"))),
            id="network",
        ),
    ],
)
async def test_unusable_answer_keeps_the_master_wording(provider: Any) -> None:
    checked = checked_plan()
    assert await translate_master_wording(provider, MASTER, checked, [], tpm=0) == []
    assert bullet(checked, "b_3") == OLD_JOB
    assert {f.detail for f in checked.findings if f.code == "translation_unavailable"} == {
        "llm_error"
    }


def test_output_budget_follows_the_bullets() -> None:
    assert translation_output_tokens({"b": "short"}) == 800
    assert translation_output_tokens({"b": "x" * 3_000}) == 2_300


CV = r"""\documentclass{article}
\begin{document}
\BLOCK{for e in experiences}\BLOCK{for b in e.bullets}\VAR{b.text}
\BLOCK{endfor}\BLOCK{endfor}
\BLOCK{for p in projects}\BLOCK{for b in p.bullets}\VAR{b.text}
\BLOCK{endfor}\BLOCK{endfor}
\end{document}
"""


async def test_cv_generation_translates_and_still_protects_the_paper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The paper's translation has no "author" in it, and it still goes last when fitting."""
    plan = TailorPlan(
        experiences=[PlannedItem(id="exp_1", bullets=[b.model_copy() for b in REWRITES])],
        projects=[
            PlannedItem(
                id="prj_1",
                bullets=[
                    PlannedBullet(source="b_4", text="Classement d'échantillons avec PyTorch")
                ],
            )
        ],
        education=["edu_1"],
    )
    fr_job, fr_paper = (
        "9 tableaux de bord créés avec Power BI",
        "Rédaction d'une étude sur les graphes",
    )
    provider = ScriptedProvider(plan.model_dump(mode="json"), answer(b_3=fr_job, b_5=fr_paper))
    settings = SimpleNamespace(
        candidate_french_form="neutral",
        limits_for=lambda _name: (8_000, 30, 100_000),
        latex_compile_timeout_s=30,
    )
    factory = DocumentFactory(settings, None, provider)  # type: ignore[arg-type]
    stored: dict[str, Any] = {}
    sources: list[str] = []

    async def template_source(_id: int) -> str:
        return CV

    async def compile_(_tpl: Any, tex: str, **_kw: Any) -> CompileResult:
        sources.append(tex)
        return CompileResult(ok=True, pdf=None, pages=2 if len(sources) == 1 else 1, log="ok")

    async def store(_app: int, _kind: Any, _tpl: Any, content: dict[str, Any], *_: Any) -> int:
        stored.update(content)
        return 1

    monkeypatch.setattr(factory, "template_source", template_source)
    monkeypatch.setattr(factory, "compile", compile_)
    monkeypatch.setattr(factory, "_store", store)
    tpl = SimpleNamespace(id=1, mapping={}, page_budget=1)
    job = {"title": "Ingénieure IA", "description": "Poste IA"}
    result = await factory._cv(tpl, MASTER, job, [], "fr", 1)  # type: ignore[arg-type]

    assert result["ok"]
    assert sorted(stored["translated_bullets"]) == ["b_3", "b_5"]
    assert OLD_JOB not in sources[-1] and fr_job in sources[-1] and fr_paper in sources[-1]
    assert stored["dropped_for_length"] == ["b_2"]  # the over-budget round did not take the paper
