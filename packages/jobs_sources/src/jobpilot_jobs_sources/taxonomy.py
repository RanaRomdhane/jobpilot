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
# written as shown on the dashboard (matching folds case and punctuation anyway)
TECH_VOCAB: tuple[str, ...] = (
    "Python", "Java", "JavaScript", "TypeScript", "C++", "C#", "Go", "Rust", "Scala", "Kotlin",
    "PHP", "Ruby", "SQL", "NoSQL", "R", "MATLAB", "Bash",
    "Django", "Flask", "FastAPI", "Spring", "Node.js", "Express", ".NET", "Laravel",
    "React", "Next.js", "Angular", "Vue.js", "Tailwind", "GraphQL", "REST API",
    "PyTorch", "TensorFlow", "Keras", "scikit-learn", "XGBoost", "pandas", "NumPy",
    "Hugging Face", "Transformers", "LangChain", "LlamaIndex", "RAG", "LLM", "NLP",
    "Computer Vision", "OpenCV", "Deep Learning", "Machine Learning", "MLOps", "MLflow",
    "Spark", "Airflow", "dbt", "Kafka", "Hadoop", "Databricks", "Snowflake", "BigQuery",
    "Power BI", "Tableau", "Looker", "Excel", "ETL",
    "PostgreSQL", "MySQL", "MongoDB", "Redis", "Elasticsearch", "Oracle",
    "Docker", "Kubernetes", "Terraform", "Ansible", "AWS", "Azure", "GCP", "Linux", "Git",
    "CI/CD", "Jenkins", "GitHub Actions",
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


# "We're looking for a Staff/Senior ML Engineer": the level stated in the hiring sentence
_HIRING = re.compile(
    r"\b(?:looking for|seeking|hiring|recherchons|recrutons|recrute)\s+(?:an?|un|une|des)?\s*"
    r"([a-z/ -]{0,40})"
)


def detect_seniority(title: str, body: str) -> Seniority | None:
    t = fold(title)
    for pattern, level in _SENIORITY:
        if pattern.search(t):
            return level
    for phrase in _HIRING.findall(fold(body)[:1500]):
        for pattern, level in _SENIORITY:
            if level != "intern" and pattern.search(phrase):
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
