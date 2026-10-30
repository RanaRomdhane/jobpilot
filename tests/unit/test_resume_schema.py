from __future__ import annotations

import json

from jobpilot_core.resume import ResumeProfile, all_bullets, ensure_ids, llm_schema


def sample() -> ResumeProfile:
    return ResumeProfile.model_validate(
        {
            "language": "fr",
            "contact": {"full_name": "Camille Martin", "email": "camille.martin@example.com"},
            "experiences": [
                {
                    "company": "Exemplia",
                    "title": "Ingénieure backend",
                    "start": "2021-03",
                    "end": "mars 2024",  # not normalised -> dropped, never guessed
                    "bullets": ["Conçu une API.", "  ", {"text": "Réduit la latence de 40 %."}],
                }
            ],
            "languages": [{"name": "Anglais", "cefr": "c1"}, {"name": "Français", "cefr": "natif"}],
            "skills": [{"name": "Outils", "kind": "weird", "skills": ["Docker"]}],
        }
    )


def test_bullets_accept_plain_strings_and_dates_are_normalised() -> None:
    p = sample()
    exp = p.experiences[0]
    assert [b.text for b in exp.bullets] == ["Conçu une API.", "Réduit la latence de 40 %."]
    assert exp.start == "2021-03" and exp.end is None
    assert [lang.cefr for lang in p.languages] == ["C1", "native"]
    assert p.skills[0].kind == "other"


def test_ensure_ids_assigns_once_and_keeps_existing() -> None:
    first = ensure_ids(sample())
    ids = set(all_bullets(first))
    assert len(ids) == 2 and all(i.startswith("b_") for i in ids)
    assert first.experiences[0].id.startswith("exp_")

    edited = first.model_copy(deep=True)
    edited.experiences[0].bullets[0].text = "Conçu et documenté une API."
    edited.experiences[0].bullets.append(edited.experiences[0].bullets[0].model_copy())  # dup id
    again = ensure_ids(edited)
    assert again.experiences[0].id == first.experiences[0].id
    kept = again.experiences[0].bullets
    assert kept[0].id == first.experiences[0].bullets[0].id
    assert kept[1].id == first.experiences[0].bullets[1].id
    assert kept[2].id not in {kept[0].id, kept[1].id}


def test_llm_schema_has_no_ids_and_string_bullets() -> None:
    schema = llm_schema()
    text = json.dumps(schema)
    assert '"id"' not in text and "maxLength" not in text
    assert schema["$defs"]["Bullet"] == {"type": "string"}
    part = llm_schema(("contact", "experiences"))
    assert set(part["properties"]) == {"language", "contact", "experiences"}
    assert "Education" not in part["$defs"] and "Experience" in part["$defs"]
