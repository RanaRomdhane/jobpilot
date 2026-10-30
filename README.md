# JobPilot

Local-first, AI-powered job-search copilot: it triages your Gmail inbox, tracks every application,
analyses your CV for ATS-friendliness, aggregates job offers, tailors your LaTeX CV and cover letter
per job, and sends applications from your Gmail **only after you approve them**.

> **Status:** Phase 1 — foundation (stack, config, LLM provider + global rate limiter, schema,
> health checks, CI). See [docs/plan.md](docs/plan.md).

## Principles

- **Local-first** — your data stays in a local PostgreSQL; only LLM calls leave the machine.
- **Untrusted input everywhere** — emails, job posts and CVs are data, never instructions.
- **Least privilege** — no AI agent can send, delete or forward email.
- **Human in the loop** — every outgoing email needs your explicit, one-time approval.
- **No fabrication** — tailored documents only reuse facts from your master profile.

## Quick start

Prerequisites: Docker Desktop, [uv](https://docs.astral.sh/uv/), Node 20+ with pnpm, GNU make.

```bash
make install   # Python + web dependencies, git hooks
make env       # creates .env with generated DB password and encryption key
# edit .env and paste your GROQ_API_KEY (https://console.groq.com/keys)
make up        # builds and starts everything, waits until healthy
```

- Dashboard: <http://localhost:3000> (status page; the real dashboard lands in Phase 3)
- API docs: <http://localhost:8000/api/v1/docs>

Everything binds to `127.0.0.1`. PostgreSQL is published on **5433** so it never clashes with a
locally installed PostgreSQL.

### Lighter development loop

On a machine with little RAM, run only the databases in Docker and the apps on the host:

```bash
make infra         # postgres + redis
make migrate-local
make api-dev       # terminal 1
make worker-dev    # terminal 2
make web-dev       # terminal 3
```

### Quality

```bash
make check     # ruff, mypy --strict, eslint, prettier, tsc, pytest, vitest
make test-integration   # needs `make infra`
make gen-client         # after changing the API: regenerate the typed TS client
```

`make help` lists every target.

## Layout

```
apps/      api (FastAPI) · worker (arq) · mcp_gmail · agents · web (Next.js 15)
packages/  core (config, models, LLM provider, rate limiter) · security · jobs_sources · latex
infra/     docker-compose, Dockerfiles, LaTeX sandbox, postgres init
docs/      architecture, plan, ADRs
```

## Documentation

- [Build brief](PROMPT.md) · [Delivery plan](docs/plan.md) · [Architecture](docs/architecture.md)
- [Architecture decision records](docs/adr/)

## License

[MIT](LICENSE)
