# JobPilot — Delivery plan

Status: **awaiting approval**. Each phase lives on its own branch, lands through a PR into `main`
(template in `.github/`), and is merged only after CI is green and its acceptance criteria pass.

## Workflow

- Branch per phase: `phase/1-foundation`, `phase/2-triage`, … ; conventional commits
  (`feat:`, `fix:`, `test:`, `docs:`, `chore:`, `ci:`).
- PR checklist = the phase's acceptance criteria + security checklist.
- Squash-merge only; merged branches are deleted automatically. `main` is protected (no force push,
  no deletion); the CI jobs become required status checks at the end of Phase 1.

## Phase 0 — Bootstrap ✅ (initial commits directly on `main`)

Prerequisites, repo, brief, architecture, ADRs 0001–0004, this plan.

## Phase 1 — Foundation (`phase/1-foundation`) ✅ merged in #1

- uv workspace: `packages/{core,security,jobs_sources,latex}`, `apps/{api,worker,mcp_gmail,agents}`.
- `packages/core`: `Settings` (pydantic-settings, `.env`), async engine/session, logging with
  redaction, `LLMProvider` (Groq + OpenAI via OpenAI-compatible client), Redis token bucket (Lua),
  response cache, `llm_call_log` writer.
- Alembic baseline migration with **all** brief tables + refinements (architecture §5), pgvector on.
- FastAPI skeleton: `/api/v1/health` (db, redis, latex reachability), OpenAPI, CORS locked down.
- arq worker skeleton with a heartbeat cron and `task_run` progress → Redis pub/sub.
- Next.js 15 skeleton (TS strict, Tailwind, shadcn/ui, next-intl FR/EN, dark mode), typed client
  generation script.
- `infra/docker-compose.yml`: postgres+pgvector, redis, api, worker, web, latex (placeholder image).
- Makefile: `up down logs migrate test lint typecheck eval fmt gen-client`; pre-commit (ruff, mypy,
  prettier, eslint, gitleaks); GitHub Actions CI (Python + web jobs, services for postgres/redis).
- Tests: rate limiter (concurrency, refill, reconcile), settings, health endpoint.
- **Asks:** Groq API key (+ optional OpenAI key) → written by you into `.env`, never pasted in git.
- ✅ `make up` starts everything, health checks pass, CI green.

## Phase 2 — Inbox triage (`phase/2-triage`) ✅ merged in #2

- Google OAuth installed-app flow → encrypted `oauth_credentials` (scope set `triage`).
- `packages/security` v1: HTML→text, zero-width/hidden-text detection, FR/EN/AR heuristic patterns,
  base64 blobs, tool-name mentions, spotlight wrapper, guard-model call.
- `apps/mcp_gmail` (stdio, FastMCP): the 3 triage tools only, `batch_id` binding, label whitelist.
- Incremental sync with `historyId` (+ full-sync fallback when history expired).
- TriageAgent + orchestrator: batches of 15, ~2 tool calls/batch, strict enum validation + 1 retry.
- Pipeline linking + `status_suggestion` (auto-apply above a confidence threshold).
- Tests: mocked Gmail API, "toolset is exactly 3 tools", "no send/delete code path", baseline
  injection test, ≥30 injection + ≥30 benign samples with precision/recall, ≥60-email labelled eval
  (`make eval`, per-category accuracy).
- **Asks:** Google Cloud project/OAuth client (step-by-step guide provided if you have none).
- ✅ Acceptance criteria from the brief, plus a report: tool calls/batch, tokens/batch, accuracy.

## Phase 3 — Dashboard v1 (`phase/3-dashboard`) — in review

Auth, app shell, Overview, Inbox (re-classify → eval set), Applications Kanban (dnd-kit) + table +
drawer, Security panel, Settings; SSE progress toasts; skeletons, empty states, a11y, 375px → desktop.
- **Asks:** auth method (local password vs Google sign-in restricted to your address).
- ✅ You can see and manage your pipeline in the browser.

## Phase 4 — Resume intelligence (`phase/4-resume`)

Upload (PDF/DOCX, size/type/magic-byte checks, random names), PyMuPDF/python-docx extraction with
layout signals, ResumeParserAgent → `ResumeProfile` with stable ids, editable master profile form,
ATS report (deterministic checks + keyword coverage + LLM review) with prioritized fixes.
- ✅ Your real CV parses correctly; the ATS report is actionable.

## Phase 5 — Job discovery (`phase/5-jobs`)

`JobSource` protocol, connectors (Adzuna, JSearch, Remotive, RemoteOK, WeWorkRemotely, then the
Tunisian sites after a robots.txt/terms review each), normalization (location → country/region, work
mode, language), dedup hash, filters, embeddings + pgvector recall → keyword overlap → LLM re-rank,
saved searches on cron. VCR-style fixtures for every connector.
- **Asks:** target domains, seniority, contract types, default regions; Adzuna / RapidAPI keys;
  embedding model (default proposal: `intfloat/multilingual-e5-base`, 768-d — `bge-m3` is ~2.3 GB
  and slow on this CPU); any Tunisian source whose terms forbid automation.
- ✅ Filtered search returns deduplicated jobs with match scores from ≥ 4 sources.

## Phase 6 — LaTeX application factory (`phase/6-latex`)

Template onboarding (upload `.tex/.cls/.sty/fonts`, placeholder mapping proposal → your confirmation),
`latex_escape` (single tested function), sandboxed compiler, TailorAgent / CoverLetterAgent with
`source_id` traceability + no-fabrication validator, page-budget check, versioned documents,
ATS before/after.
- **Asks:** your templates, mapping confirmation, LaTeX engine (TeX Live `latexmk` recommended for an
  offline sandbox), cover-letter tone default.
- ✅ For a real job both PDFs compile from your templates with zero invented facts.

## Phase 7 — Review & send (`phase/7-send`)

Email composition, review screen (CV diff, PDF previews, editable email, warnings), approval token,
`gmail.send` credential flow, sender service, post-send labelling/linking, daily cap, duplicate guard,
"apply via link" path.
- **Asks:** email signature, default tone, daily send cap.
- ✅ An approved application is sent from your Gmail and its replies are linked automatically.

## Phase 8 — Hardening & docs (`phase/8-hardening`)

Playwright E2E, LLM usage/cost page, rate-limiter load test, `docs/security.md` final threat model,
architecture diagram, full README (Google Cloud OAuth, API keys), demo seed data.
- **Asks:** deployment target (local only vs VPS later).
- ✅ A fresh clone runs end-to-end by following the README.

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| 10 GB RAM for 6 containers + Docker Desktop | memory limits per service, web runs on host in dev (`make web`), small embedding model, lazy model load in worker only |
| ~44 GB free disk; TeX Live full is ~7 GB | curated TeX Live scheme + packages your templates need |
| Groq model/limits change | provider + model per purpose in `.env`; limits configurable |
| Tunisian sites block automation / change HTML | RSS first, health checks, connector disabled on failure, no login-walled scraping |
| Google "testing" OAuth tokens expire after 7 days | documented re-auth flow and a Settings warning |
