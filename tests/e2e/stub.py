"""Local stand-in for the language model and for Gmail, used only by the end-to-end tests.

The E2E stack sets `GROQ_BASE_URL` and `GMAIL_API_URL` to this server, so no test reaches a real
provider or mailbox. Answers are canned and synthetic.

    uv run uvicorn tests.e2e.stub:app --port 8900
"""

from __future__ import annotations

import json
import time
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI(title="JobPilot E2E stub")

SENT: list[dict[str, Any]] = []  # what the sender service posted to "Gmail"
LLM_CALLS: list[str] = []  # structured-output names the app asked for

PROFILE: dict[str, Any] = {
    "language": "en",
    "contact": {"full_name": "Ada Example", "email": "ada@example.com", "location": "Tunis"},
    "headline": "Machine Learning Engineer",
    "summary": "Machine learning engineer building NLP products with Python.",
    "experiences": [
        {
            "company": "Exemplia Labs",
            "title": "ML Engineer",
            "start": "2024-09",
            "current": True,
            "bullets": ["Built a text classifier reaching 91% accuracy on 4,000 tickets"],
            "technologies": ["Python", "PyTorch"],
        }
    ],
    "education": [
        {
            "institution": "Example Engineering School",
            "degree": "Engineering Degree",
            "end": "2024-07",
        }
    ],
    "projects": [],
    "skills": [{"name": "Engineering", "skills": ["Python", "SQL", "PyTorch"]}],
    "languages": [{"name": "English", "level": "Advanced"}],
    "certifications": [],
    "links": [],
    "injection_suspected": False,
}

REVIEW: dict[str, Any] = {
    "summary": "A clear CV with measurable results.",
    "strengths": ["Quantified results"],
    "issues": [
        {
            "severity": "medium",
            "message": "The summary is generic.",
            "fix": "Name the kind of products you build.",
            "evidence": None,
        }
    ],
    "bullet_rewrites": [],
    "content_score": 72,
    "job_title": None,
    "job_keywords": [],
    "injection_suspected": False,
}

ANSWERS: dict[str, dict[str, Any]] = {"resume_profile": PROFILE, "ats_review": REVIEW}


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/chat/completions")
async def chat(request: Request) -> JSONResponse:
    body = await request.json()
    fmt = body.get("response_format") or {}
    name = (fmt.get("json_schema") or {}).get("name", "")
    LLM_CALLS.append(name)
    content = json.dumps(ANSWERS.get(name, {}))
    return JSONResponse(
        {
            "id": "e2e",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": body.get("model", "stub"),
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": content},
                }
            ],
            "usage": {"prompt_tokens": 120, "completion_tokens": 60, "total_tokens": 180},
        }
    )


# ---------------------------------------------------------------- Gmail (send + labels only)
@app.post("/gmail/messages/send")
async def send(request: Request) -> dict[str, str]:
    body = await request.json()
    SENT.append({"raw": body.get("raw", ""), "authorization": request.headers.get("authorization")})
    n = len(SENT)
    return {"id": f"e2e-msg-{n}", "threadId": f"e2e-thread-{n}"}


@app.get("/gmail/labels")
async def labels() -> dict[str, list[dict[str, str]]]:
    return {"labels": [{"id": "L1", "name": "JobPilot/Candidature"}]}


@app.post("/gmail/labels")
async def create_label(request: Request) -> dict[str, str]:
    body = await request.json()
    return {"id": "L1", "name": body.get("name", "")}


@app.post("/gmail/messages/batchModify")
async def batch_modify() -> dict[str, str]:
    return {}


# ---------------------------------------------------------------- what the tests read back
@app.get("/_sent")
async def sent() -> dict[str, Any]:
    return {"count": len(SENT), "messages": SENT}


@app.get("/_llm")
async def llm_calls() -> dict[str, list[str]]:
    return {"calls": LLM_CALLS}
