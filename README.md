# JobPilot

Local-first, AI-powered job-search copilot: it triages your Gmail inbox, tracks every application,
analyses your CV for ATS-friendliness, aggregates job offers, tailors your LaTeX CV and cover letter
per job, and sends applications from your Gmail **only after you approve them**.

> **Status:** Phase 0 — repository bootstrap. Nothing runs yet. See [docs/plan.md](docs/plan.md).

## Principles

- **Local-first** — your data stays in a local PostgreSQL; only LLM calls leave the machine.
- **Untrusted input everywhere** — emails, job posts and CVs are data, never instructions.
- **Least privilege** — no AI agent can send, delete or forward email.
- **Human in the loop** — every outgoing email needs your explicit, one-time approval.
- **No fabrication** — tailored documents only reuse facts from your master profile.

## Stack

FastAPI · SQLAlchemy 2 · PostgreSQL 16 + pgvector · Redis + arq · OpenAI Agents SDK + a Gmail MCP
server · Groq `gpt-oss-120b` (OpenAI switchable) · Next.js 15 + shadcn/ui · LaTeX in a sandboxed
container. Details in [docs/architecture.md](docs/architecture.md).

## Documentation

- [Build brief](PROMPT.md)
- [Delivery plan](docs/plan.md)
- [Architecture](docs/architecture.md)
- [Architecture decision records](docs/adr/)

Setup instructions will land with Phase 1 and be completed in Phase 8.

## License

[MIT](LICENSE)
