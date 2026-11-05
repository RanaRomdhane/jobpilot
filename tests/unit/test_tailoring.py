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
    feminine_forms,
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
                "bullets": [{"id": "b_4", "text": "Fine-tuned GPT-2 with Adam to 91.25% accuracy"}],
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
            ),
            PlannedItem(
                id="exp_2",
                bullets=[PlannedBullet(source="b_3", text="Built 9 dashboards in Power BI")],
            ),
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
        check_text("Atteint 73 % sur 4 000+ tâches", "73% accuracy on 4,000+ tasks", set(), [], "b")
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
    kept, findings = validate_letter(
        MASTER, paragraphs, job, ["Python", "Kubernetes"], identity="ML Engineer Exemplia Gulf"
    )
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
    # the plan's choice first, the first project last; an experience's first bullet never goes
    assert order == ["b_2", "b_4"]
    cv = build_tailored(MASTER, v.plan, drop={"b_2", "b_4"})
    assert [b.id for b in cv.experiences[0].bullets] == ["b_1"] and cv.projects == []


def test_dedupe_profile_undoes_overlapping_parse_passes() -> None:
    from jobpilot_core.resume import dedupe_profile

    messy = ResumeProfile.model_validate(
        {
            "summary": "ML engineer building NLP products, with React front ends, in Python.",
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


def test_translation_keeps_marks_and_generic_words() -> None:
    # R² in the source, IA for AI, Unicode hyphens: none of these is an invention
    source = "Selected Random Forest achieving R² = 0.42 in an AI-powered on-premise stack"
    text = "Sélection de Random Forest (R² = 0.42) dans une stack IA on\u2011premise"
    assert check_text(text, source, _vocab_of(source), [], "b") == []
    assert (
        check_text("ResNet-50 et ViT", "ResNet50 and ViT", _vocab_of("ResNet50 ViT"), [], "b") == []
    )
    hyphens = "Fine-tuned GPT-2 and macro-F1 on CIFAR-10 with t-SNE"
    assert check_text("GPT-2, macro-F1 sur CIFAR-10 avec t-SNE", hyphens, set(), [], "b") == []


def _vocab_of(text: str) -> set[str]:
    from jobpilot_resume.tailoring import _vocab

    return _vocab(text)


def test_skill_named_inside_a_longer_entry_is_real() -> None:
    master = MASTER.model_copy(deep=True)
    master.skills[0].skills = ["Model deployment (Docker, FastAPI)", "LoRA fine-tuning", "SQL"]
    p = plan(skills=[PlannedSkills(id="skl_1", skills=["Docker", "LoRA fine\u2011tuning", "Rust"])])
    v = validate_plan(master, p)
    assert v.plan.skills[0].skills == ["Docker", "LoRA fine-tuning"]
    assert [(f.code, f.detail) for f in v.findings] == [("new_skill", "Rust")]


def test_every_experience_stays_in_the_timeline() -> None:
    p = plan()
    p.experiences = p.experiences[:1]  # leaves out exp_2
    v = validate_plan(MASTER, p)
    assert [e.id for e in v.plan.experiences] == ["exp_1", "exp_2"]
    assert v.plan.experiences[1].bullets[0].source == "b_3"
    assert any(f.code == "restored_experience" for f in v.findings)


def test_letter_cannot_claim_the_postings_words_or_availability() -> None:
    job = (
        "Exemplia Gulf cherche un ML Engineer. Vous diffuserez la culture Craft auprès des équipes."
    )
    kept, findings = validate_letter(
        MASTER,
        [
            ("Exemplia Gulf diffuse la culture Craft auprès des équipes.", []),
            ("Je sais diffuser la culture Craft auprès des équipes.", []),
            ("Je suis disponible immédiatement pour un entretien.", []),
            ("I am available from June.", []),
            ("Je serais disponible pour un entretien.", []),
            ("J'ai construit un modèle BERT atteignant 73% de précision.", ["b_1"]),
        ],
        job,
        identity="ML Engineer Exemplia Gulf",
    )
    assert [p.text for p in kept] == [
        "Exemplia Gulf diffuse la culture Craft auprès des équipes.",
        "Je serais disponible pour un entretien.",
        "J'ai construit un modèle BERT atteignant 73% de précision.",
    ]
    codes = [f.code for f in findings]
    assert codes.count("availability") == 2 and "new_term" in codes


def test_page_fitting_keeps_the_standout_content_longest() -> None:
    p = TailorPlan(
        experiences=[
            PlannedItem(
                id="e",
                bullets=[
                    PlannedBullet(source="e1", text="x"),
                    PlannedBullet(source="e2", text="x"),
                ],
            )
        ],
        projects=[
            PlannedItem(
                id="p1",
                bullets=[
                    PlannedBullet(source="a1", text="x"),
                    PlannedBullet(source="a2", text="x"),
                ],
            ),
            PlannedItem(id="p2", bullets=[PlannedBullet(source="b1", text="x")]),
        ],
        certifications=["c1", "c2", "c3"],
        extra_sections=[PlannedItem(id="s", bullets=[PlannedBullet(source="s1", text="x")])],
        drop_first=["a1", "e2"],
    )
    assert drop_order(p) == ["e2", "c3", "s1", "a2", "b1", "a1"]  # e1: an experience keeps one


def test_ids_written_in_a_letter_are_citations_not_words() -> None:
    kept, findings = validate_letter(
        MASTER, [("Mon modèle BERT a atteint 73 % (b_1, b_1).", [])], ""
    )
    assert findings == []
    assert kept[0].text == "Mon modèle BERT a atteint 73 %."
    assert kept[0].sources == ["b_1"]


def test_a_project_listed_among_experiences_goes_back_to_projects() -> None:
    p = plan()
    p.experiences.insert(
        0,
        PlannedItem(id="prj_1", bullets=[PlannedBullet(source="b_4", text="Fine-tuned GPT-2")]),
    )
    v = validate_plan(MASTER, p)
    assert [i.id for i in v.plan.experiences] == ["exp_1", "exp_2"]
    assert [i.id for i in v.plan.projects] == ["prj_1"]
    assert [(f.code, f.where) for f in v.findings] == [("wrong_group", "prj_1")]


def test_feminine_forms_cover_the_candidate_not_the_post_or_the_degree() -> None:
    assert feminine_forms("Ingénieur IA / Machine Learning") == "Ingénieure IA / Machine Learning"
    assert feminine_forms("Développeur Python") == "Développeuse Python"
    assert (
        feminine_forms("Ingénieur IA avec un diplôme d'ingénieur, spécialisé en NLP.")
        == "Ingénieure IA avec un diplôme d'ingénieur, spécialisée en NLP."
    )
    assert (
        feminine_forms("Le poste d'Ingénieur IA d'Exemplia. Je serais honoré d'en parler.")
        == "Le poste d'Ingénieur IA d'Exemplia. Je serais honorée d'en parler."
    )
    assert feminine_forms("Je suis très motivé.") == "Je suis très motivée."
    assert feminine_forms("Je suis disponible pour un entretien.") == (
        "Je suis disponible pour un entretien."
    )
    assert feminine_forms("J'ai développé un modèle.") == "J'ai développé un modèle."
