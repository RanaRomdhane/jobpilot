# ADR-0001 — Technology stack

- Status: Accepted (Phase 0)
- Date: 2026-10-30

## Context

JobPilot is a single-user, local-first app that runs LLM agents over personal email and documents,
must stay within the Groq free tier, and must run on a modest Windows laptop through Docker.

## Decision

| Area | Choice | Main reason |
|---|---|---|
| API | FastAPI, Python 3.12, Pydantic v2 | async, OpenAPI for free, same language as agents |
| Persistence | PostgreSQL 16 + pgvector, SQLAlchemy 2 async, Alembic | relational pipeline data + vector search in one DB |
| Jobs | Redis + arq (with cron) | async-native, light; Redis is reused for rate limiting, cache, SSE pub/sub |
| Agents | OpenAI Agents SDK + custom Gmail MCP server (stdio) | first-class MCP, guardrails, tracing; works with any OpenAI-compatible endpoint |
| LLM | Groq `gpt-oss-120b` default, OpenAI switchable | free tier; OpenAI-compatible API keeps one code path |
| Embeddings | local sentence-transformers, multilingual | no per-call cost, FR/EN |
| Python tooling | uv workspace, ruff, mypy (strict), pytest | one lockfile, fast |
| Web | Next.js 15 App Router, TS strict, Tailwind, shadcn/ui, TanStack Query, next-intl | brief requirement; typed client from OpenAPI |
| LaTeX | Jinja2 with LaTeX delimiters + sandboxed compiler container | user's own templates, safe compilation |
| Infra | docker compose + Makefile, GitHub Actions | reproducible local stack |

## Consequences

- Two toolchains (uv + pnpm); CI runs both.
- Groq's TPM ceiling shapes the triage design (ADR-0004).
- The LaTeX engine is chosen in Phase 6 (Tectonic needs network for packages; the sandbox has none).
