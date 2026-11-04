"""Domain taxonomy, seniority and contract detection (FR/EN), and the search terms per domain.

The taxonomy is data: a domain has a label, the terms used to query sources, and the signals that
classify a posting into it. Classification scores the title three times higher than the body.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from jobpilot_jobs_sources.models import Contract, Seniority
from jobpilot_security.text import fold


@dataclass(frozen=True, slots=True)
class Domain:
    key: str
    search_terms: tuple[str, ...]
    signals: tuple[str, ...]


DOMAINS: tuple[Domain, ...] = (
    Domain(
        "ai_ml",
        ("machine learning", "data scientist", "AI engineer", "deep learning", "NLP", "LLM"),
        (
            "machine learning", "apprentissage automatique", "deep learning", "data scientist",
            "data science", "intelligence artificielle", "artificial intelligence", "ai engineer",
            "ml engineer", "mlops", "nlp", "llm", "computer vision", "vision par ordinateur",
            "pytorch", "tensorflow", "scikit-learn", "generative ai", "ia generative", "rag",
            "transformers", "hugging face",
        ),
    ),
    Domain(
        "software_backend",
        ("backend developer", "python developer", "software engineer", "developpeur backend"),
        (
            "backend", "back-end", "software engineer", "ingenieur logiciel", "developpeur",
            "developer", "python", "django", "fastapi", "flask", "java", "spring", "node.js",
            "golang", "api rest", "microservices", ".net", "c#", "php", "laravel",
        ),
    ),
    Domain(
        "data_eng_bi",
        ("data engineer", "data analyst", "business intelligence", "power bi"),
        (
            "data engineer", "ingenieur data", "data analyst", "analyste de donnees",
            "business intelligence", "power bi", "tableau", "etl", "data warehouse", "dbt",
            "spark", "airflow", "sql", "big data", "databricks", "looker",
        ),
    ),
    Domain(
        "fullstack_frontend",
        ("full stack developer", "react developer", "frontend developer"),
        (
            "full stack", "fullstack", "full-stack", "frontend", "front-end", "react", "next.js",
            "angular", "vue.js", "typescript", "javascript", "developpeur web", "web developer",
        ),
    ),
    Domain(
        "devops_cloud",
        ("devops engineer", "cloud engineer"),
        (
            "devops", "sre", "site reliability", "kubernetes", "docker", "terraform", "aws",
            "azure", "gcp", "cloud engineer", "ci/cd",
        ),
    ),
)  # fmt: skip
DOMAIN_KEYS = tuple(d.key for d in DOMAINS)

# concrete skills and tools a posting can ask for (used to list what a profile is missing)
TECH_VOCAB: tuple[str, ...] = (
    "python", "java", "javascript", "typescript", "c++", "c#", "go", "rust", "scala", "kotlin",
    "php", "ruby", "sql", "nosql", "r", "matlab", "bash",
    "django", "flask", "fastapi", "spring", "node.js", "express", ".net", "laravel",
    "react", "next.js", "angular", "vue.js", "tailwind", "graphql", "rest api",
    "pytorch", "tensorflow", "keras", "scikit-learn", "xgboost", "pandas", "numpy",
    "hugging face", "transformers", "langchain", "llamaindex", "rag", "llm", "nlp",
    "computer vision", "opencv", "deep learning", "machine learning", "mlops", "mlflow",
    "spark", "airflow", "dbt", "kafka", "hadoop", "databricks", "snowflake", "bigquery",
    "power bi", "tableau", "looker", "excel", "etl",
    "postgresql", "mysql", "mongodb", "redis", "elasticsearch", "oracle",
    "docker", "kubernetes", "terraform", "ansible", "aws", "azure", "gcp", "linux", "git",
    "ci/cd", "jenkins", "github actions",
)  # fmt: skip


def _pattern(term: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![a-z0-9]){re.escape(fold(term))}(?![a-z0-9])")


_SIGNALS = {d.key: [_pattern(s) for s in d.signals] for d in DOMAINS}


def classify_domain(title: str, body: str) -> str | None:
    t, b = fold(title), fold(body)[:6000]
    scores = {
        key: 3 * sum(bool(p.search(t)) for p in pats) + sum(bool(p.search(b)) for p in pats)
        for key, pats in _SIGNALS.items()
    }
    best = max(scores, key=lambda k: scores[k])
    return best if scores[best] >= 2 else None


def search_terms(domains: list[str]) -> list[str]:
    chosen = [d for d in DOMAINS if d.key in domains] or list(DOMAINS[:4])
    return [term for d in chosen for term in d.search_terms]


_SENIORITY: list[tuple[re.Pattern[str], Seniority]] = [
    (re.compile(r"\b(stage|stagiaire|intern|internship|pfe|alternan\w*|apprenti\w*)\b"), "intern"),
    (re.compile(r"\b(lead|principal|staff|head of|directeur|director|chef de projet)\b"), "lead"),
    (re.compile(r"\b(senior|sr\.?|confirme|experimente|expert)\b"), "senior"),
    (
        re.compile(r"\b(junior|jr\.?|debutant|entry[- ]level|graduate|jeune diplome|sivp)\b"),
        "junior",
    ),
    (re.compile(r"\b(mid[- ]level|intermediate|intermediaire)\b"), "mid"),
]
_YEARS = re.compile(r"\b(\d{1,2})\s*(?:\+|a|-\s*\d+)?\s*(?:ans|years|yrs|annees)\b")


def detect_seniority(title: str, body: str) -> Seniority | None:
    t = fold(title)
    for pattern, level in _SENIORITY:
        if pattern.search(t):
            return level
    years = [int(y) for y in _YEARS.findall(fold(body)[:4000]) if int(y) <= 20]
    if years:
        y = min(years)
        return "junior" if y <= 2 else "mid" if y <= 5 else "senior"
    return None


_CONTRACT: list[tuple[re.Pattern[str], Contract]] = [
    # SIVP / Karama: Tunisian first-job schemes, i.e. paid fixed-term contracts for graduates
    (re.compile(r"\b(cdd|sivp|karama|fixed[- ]term|temporary|temporaire|interim)\b"),
     "fixed_term"),
    (re.compile(r"\b(stage|internship|intern|pfe)\b"), "internship"),
    (re.compile(r"\b(alternance|apprentissage|apprenticeship)\b"), "apprenticeship"),
    (re.compile(r"\b(freelance|free-lance|contractor|independant|consultant externe)\b"),
     "freelance"),
    (re.compile(r"\b(cdi|permanent|full[_ -]?time|temps plein|fulltime)\b"), "permanent"),
]  # fmt: skip


# words that also appear in ordinary job text ("mentor our interns", "temporary access"): trusted
# only in the source's own label or the title, never in the description
_BODY_UNSAFE: frozenset[Contract] = frozenset({"internship", "apprenticeship", "fixed_term"})


def detect_contract(hint: str | None, title: str, body: str) -> Contract | None:
    for text, trusted in ((hint or "", True), (title, True), (body[:1500], False)):
        folded = fold(text)
        for pattern, contract in _CONTRACT:
            if (trusted or contract not in _BODY_UNSAFE) and pattern.search(folded):
                return contract
    return None
