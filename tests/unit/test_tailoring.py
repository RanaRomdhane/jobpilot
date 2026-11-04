"""No-fabrication guardrail for tailored CVs (synthetic profile)."""

from __future__ import annotations

from jobpilot_core.resume import ResumeProfile
from jobpilot_resume.tailoring import (
    PlannedBullet,
    PlannedItem,
    PlannedSkills,
    PlannedSummary,
    TailorPlan,
    build_tailored,
    check_text,
    drop_order,
    suspicious_terms,
    validate_letter,
    validate_plan,
)

MASTER = ResumeProfile.model_validate(
    {
        "language": "en",
        "contact": {"full_name": "Ada Example", "email": "ada@example.com"},
        "headline": "Machine Learning Engineer",
        "summary": "ML engineer building NLP products with Python.",
        "experiences": [
            {
                "id": "exp_1",
                "company": "Exemplia Labs",
                "title": "ML Engineer Intern",
                "start": "2025-06",
                "end": "2025-09",
                "bullets": [
                    {
                        "id": "b_1",
                        "text": "Built a BERT model reaching 73% accuracy on 4,000+ tasks",
                    },
                    {"id": "b_2", "text": "Automated reviews, cutting review time by 35%"},
                ],
                "technologies": ["BERT", "FastAPI"],
            },
            {
                "id": "exp_2",
                "company": "Demo Analytics",
                "title": "Data Intern",
                "start": "2024-06",
                "bullets": [{"id": "b_3", "text": "Built 9 dashboards in Power BI"}],
            },
        ],
        "education": [{"id": "edu_1", "institution": "Exemplia School", "degree": "MSc"}],
        "projects": [
            {
                "id": "prj_1",
                "name": "Vision demo",
                "bullets": [
                    {"id": "b_4", "text": "Fine-tuned GPT-2 with Adam to 91.25% accuracy"}
                ],
            }
        ],
        "skills": [{"id": "skl_1", "name": "Code", "skills": ["Python", "SQL", "PyTorch"]}],
        "languages": [{"id": "lng_1", "name": "English", "level": "C1"}],
        "certifications": [{"id": "crt_1", "name": "Deep Learning"}],
    }
)


def plan(**kw: object) -> TailorPlan:
    base: dict[str, object] = {
        "language": "en",
        "headline": "AI Engineer",
        "experiences": [
            PlannedItem(
                id="exp_1",
                bullets=[
                    PlannedBullet(
                        source="b_1",
                        text="Built a **BERT** model with 73% accuracy on 4,000+ tasks",
                    ),
                    PlannedBullet(
                        source="b_2", text="Cut review time by 35% by automating reviews"
                    ),
                ],
            )
        ],
        "education": ["edu_1"],
        "skills": [PlannedSkills(id="skl_1", skills=["pytorch", "Python"])],
    }
    base.update(kw)
    return TailorPlan.model_validate(base)


def test_valid_plan_passes_untouched() -> None:
    v = validate_plan(MASTER, plan())
    assert v.findings == []
    cv = build_tailored(MASTER, v.plan)
    exp = cv.experiences[0]
    # facts come from the master, wording from the plan
    assert (exp.company, exp.title, exp.start, exp.end) == (
        "Exemplia Labs",
        "ML Engineer Intern",
        "2025-06",
        "2025-09",
    )
    assert [b.id for b in exp.bullets] == ["b_1", "b_2"]
    assert exp.bullets[0].text.startswith("Built a **BERT**")
    assert cv.skills[0].skills == ["PyTorch", "Python"]  # master spelling, plan order
    assert cv.contact.full_name == "Ada Example" and cv.headline == "AI Engineer"


def test_invented_metric_is_reverted() -> None:
    p = plan()
    p.experiences[0].bullets[1].text = "Cut review time by 80% by automating reviews"
    v = validate_plan(MASTER, p)
    assert [f.code for f in v.findings] == ["new_number"] and v.findings[0].detail == "80"
    assert v.plan.experiences[0].bullets[1].text == MASTER.experiences[0].bullets[1].text


def test_number_formats_are_equivalent() -> None:
    assert (
        check_text(
            "Atteint 73 % sur 4 000+ tâches", "73% accuracy on 4,000+ tasks", set(), [], "b"
        )
        == []
    )
    assert check_text("on 4000 tasks", "on 4,000+ tasks", set(), [], "b") == []
    assert check_text("91,25 %", "91.25%", set(), [], "b") == []


def test_invented_tool_and_job_keyword_are_reverted() -> None:
    p = plan()
    p.experiences[0].bullets[
        0
    ].text = "Built a BERT model on Kubernetes with 73% accuracy on 4,000+ tasks"
    v = validate_plan(MASTER, p, job_keywords=["Kubernetes", "Python"])
    codes = {f.code for f in v.findings}
    assert "new_term" in codes and "job_keyword" in codes
    assert "Kubernetes" not in v.plan.experiences[0].bullets[0].text


def test_terms_elsewhere_in_profile_are_allowed() -> None:
    # PyTorch is a master skill, so mentioning it in a bullet is not an invention
    assert suspicious_terms("Built a model in PyTorch", {"pytorch", "built", "model"}) == []
    assert suspicious_terms("Deployed on AWS Lambda", {"deployed"}) == ["AWS", "Lambda"]
    # sentence-initial capitals are ordinary words
    assert suspicious_terms("Conçu un modèle. Réduit le temps", set()) == []


def test_ids_must_exist_belong_and_be_unique() -> None:
    p = plan(
        experiences=[
            PlannedItem(
                id="exp_1",
                bullets=[PlannedBullet(source="b_3", text="Built 9 dashboards in Power BI")],
            ),
            PlannedItem(id="exp_1", bullets=[]),
            PlannedItem(id="exp_9", bullets=[]),
            PlannedItem(
                id="exp_2",
                bullets=[PlannedBullet(source="b_3", text="Built 9 dashboards in Power BI")],
            ),
        ],
        education=["edu_1", "edu_9"],
    )
    v = validate_plan(MASTER, p)
    codes = [f.code for f in v.findings]
    assert codes.count("wrong_parent") == 1 and codes.count("unknown_id") == 3
    assert [e.id for e in v.plan.experiences] == ["exp_1", "exp_2"]
    assert v.plan.experiences[1].bullets[0].source == "b_3"


def test_skills_headline_and_summary() -> None:
    p = plan(
        headline="Senior Kubernetes Architect",
        skills=[PlannedSkills(id="skl_1", skills=["Python", "Rust"])],
        summary=PlannedSummary(text="ML engineer with 10 years of Rust.", sources=["b_1"]),
    )
    v = validate_plan(MASTER, p)
    codes = {f.code for f in v.findings}
    assert {"new_skill", "headline", "new_number"} <= codes
    assert v.plan.skills[0].skills == ["Python"]
    assert v.plan.headline == "Machine Learning Engineer"
    assert v.plan.summary.text == MASTER.summary


def test_french_rewrite_keeps_facts() -> None:
    p = plan(language="fr", headline="Ingénieure IA")
    p.experiences[0].bullets[
        1
    ].text = "Réduit de 35% le temps de planification en automatisant les calendriers"
    v = validate_plan(MASTER, p)
    assert v.findings == []
    assert build_tailored(MASTER, v.plan).language == "fr"


def test_letter_keeps_grounded_sentences_only() -> None:
    job = "Exemplia Gulf hires an ML Engineer for 6 months. Required: Python, Kubernetes."
    paragraphs = [
        (
            "I am applying for the ML Engineer role at Exemplia Gulf. The 6 month mission fits me.",
            [],
        ),
        (
            "At Exemplia Labs I built a BERT model reaching 73% accuracy on 4,000+ tasks. "
            "I also led a team of 12 engineers. I have deployed Kubernetes clusters.",
            ["b_1", "b_nope"],
        ),
        ("I raised revenue by 300%.", []),
    ]
    kept, findings = validate_letter(MASTER, paragraphs, job, ["Python", "Kubernetes"])
    assert [p.text for p in kept] == [
        "I am applying for the ML Engineer role at Exemplia Gulf. The 6 month mission fits me.",
        "At Exemplia Labs I built a BERT model reaching 73% accuracy on 4,000+ tasks.",
    ]
    assert kept[1].sources == ["b_1"]
    codes = [f.code for f in findings]
    assert "unknown_id" in codes and "job_keyword" in codes and codes.count("new_number") == 2


def test_drop_order_and_page_fitting() -> None:
    p = plan(
        projects=[
            PlannedItem(
                id="prj_1",
                bullets=[
                    PlannedBullet(
                        source="b_4", text="Fine-tuned GPT-2 with Adam to 91.25% accuracy"
                    )
                ],
            )
        ],
        drop_first=["b_2", "b_nope"],
    )
    v = validate_plan(MASTER, p)
    order = drop_order(v.plan)
    assert (
        order[0] == "b_2" and "b_4" in order and "b_1" not in order
    )  # never an experience's first bullet
    cv = build_tailored(MASTER, v.plan, drop={"b_2", "b_4"})
    assert [b.id for b in cv.experiences[0].bullets] == ["b_1"] and cv.projects == []


def test_dedupe_profile_undoes_overlapping_parse_passes() -> None:
    from jobpilot_core.resume import dedupe_profile

    messy = ResumeProfile.model_validate(
        {
            "summary": "ML engineer building NLP products, including React front ends, with Python.",
            "experiences": [
                {
                    "id": "exp_1",
                    "company": "Exemplia Labs",
                    "title": "Intern",
                    "bullets": [
                        {"id": "b_1", "text": "Built an analytical dashboard and an ML engine"}
                    ],
                }
            ],
            "education": [
                {"institution": "Exemplia School – Tunis", "degree": "MSc", "start": "2023-09"},
                {"institution": "Exemplia School", "degree": "MSc", "start": "2023-09"},
            ],
            "certifications": [
                {"name": "Deep Learning", "issuer": "Demo Institute"},
                {
                    "name": "Demo Institute: Deep Learning",
                    "issuer": "Demo Institute",
                    "url": "https://ex.com/c",
                },
            ],
            "skills": [{"name": "AI", "skills": ["Explainable AI (SHAP", "t-SNE)", "BERT"]}],
            "extra_sections": [
                {"title": "Summary", "bullets": ["ML engineer building NLP products with Python."]},
                {
                    "title": "Experience – Intern",
                    "bullets": ["Built a React analytical dashboard and an ML engine"],
                },
                {
                    "title": "Technical Writing",
                    "bullets": ["Author of an article in Demo magazine"],
                },
            ],
        }
    )
    clean = dedupe_profile(messy)
    assert [(e.institution, e.location) for e in clean.education] == [("Exemplia School", "Tunis")]
    assert [(c.name, c.url) for c in clean.certifications] == [
        ("Deep Learning", "https://ex.com/c")
    ]
    assert clean.skills[0].skills == ["Explainable AI (SHAP, t-SNE)", "BERT"]
    assert [s.title for s in clean.extra_sections] == ["Technical Writing"]
    # the fuller wording the duplicate section kept wins, under the original bullet id
    assert clean.experiences[0].bullets[0].id == "b_1"
    assert "React" in clean.experiences[0].bullets[0].text
