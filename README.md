# JobPilot

A local-first, AI-assisted job-search copilot. It triages your Gmail inbox, tracks every
application, analyses your CV for ATS-friendliness, aggregates job offers and ranks them against
your profile, tailors your LaTeX CV and cover letter per job, and sends an application from your
Gmail **only after you approve it**.

The interface is in French by default, with an English toggle.

## Principles

- **Local-first.** Your data stays in a local PostgreSQL. Only LLM calls leave the machine.
- **Untrusted input everywhere.** Emails, job posts and CVs are data, never instructions.
- **Least privilege.** No AI agent can send, delete or forward email.
- **Human in the loop.** Every outgoing email needs your explicit, one-time approval.
- **No fabrication.** Tailored documents only reuse facts from your master profile, and a
  guardrail checks every sentence the model wrote.

## What you get

| Page | What it does |
|---|---|
| Vue d'ensemble | KPIs, the application funnel, upcoming interviews, recent activity |
| Boîte de réception | Emails classified as Recruteur, Entretien, Refus, Offre, Newsletter or Autre |
| Candidatures | Kanban and table of applications, each with its timeline |
| Offres | Jobs from several sources with filters and a match score |
| CV | Upload, the editable master profile, the ATS report |
| Templates | Your LaTeX CV and cover-letter templates |
| Revue | Review a tailored application, edit the email, Approve and send |
| Sécurité | Items flagged for prompt injection, and the evaluation results |
| Utilisation | LLM calls, tokens, cache hits and an estimated cost |
| Paramètres | Language, send limit, sending switch, signature, connection status |

## Try it without any account: the demo

The demo runs the whole app on invented data in a separate database. It needs only Docker,
[uv](https://docs.astral.sh/uv/) and GNU make: no Google account and no API key.

```bash
make env        # creates .env with generated secrets
make password   # choose the dashboard password
make demo       # builds the stack, creates the demo database, loads the demo data
```

Open <http://localhost:3000> and sign in. A banner shows you are on demo data. Scheduled jobs are
off in the demo, and buttons that call the language model need a `GROQ_API_KEY` in `.env`.
`make up` switches back to your own data; `make demo-reset` reloads the demo data.

## Set up your own instance

Prerequisites: Docker Desktop (4 GB of memory for its VM is enough),
[uv](https://docs.astral.sh/uv/), Node 20+ with pnpm, and GNU make. On Windows, run the commands
in Git Bash. The first `make up` builds a TeX Live image of about 3.7 GB.

### 1. Start the stack

```bash
git clone https://github.com/RanaRomdhane/jobpilot.git
cd jobpilot
make install    # Python and web dependencies, git hooks
make env        # creates .env with a generated database password and encryption key
make password   # sets your dashboard password (only an Argon2 hash is stored)
```

Open `.env` and paste a Groq API key in `GROQ_API_KEY` (free at
<https://console.groq.com/keys>). Then:

```bash
make up         # builds and starts everything, and waits until it is healthy
```

- Dashboard: <http://localhost:3000>
- API documentation: <http://localhost:8000/api/v1/docs>

Everything binds to `127.0.0.1`. PostgreSQL is published on port **5433**, so it never clashes
with a PostgreSQL installed on your machine.

### 2. Connect Gmail and triage your inbox

1. Create a Google OAuth client (about 10 minutes):
   [docs/setup/google-oauth.md](docs/setup/google-oauth.md).
2. `make gmail-auth` opens Google's consent page. The token is stored encrypted.
3. `make triage` syncs the last 90 days and classifies them. After that, an incremental sync and
   triage run every 30 minutes.

Triage only **reads** mail and **adds** `JobPilot/*` labels. It never sends, deletes, archives
or forwards anything.

### 3. Add your CV and your LaTeX templates

1. **CV** page: upload your CV (PDF or DOCX), check the parsed profile and save it. This master
   profile is the only source of facts for tailored documents.
2. **Templates** page: upload your `.tex` CV and cover-letter templates (with any `.cls`, `.sty`
   or fonts), run the conversion, check the preview and confirm the mapping.

### 4. Find jobs

The keyless sources work at once. For Adzuna and JSearch, add the keys described in
[docs/setup/job-sources.md](docs/setup/job-sources.md). Jobs are refreshed twice a day and from
the **Offres** page.

### 5. Prepare, review and send an application

1. On a job, **Générer le CV et la lettre** tailors both documents and compiles them in a
   sandbox. Each generation is a new version.
2. **Revue** shows the checks, both PDFs, what the tailoring changed and the email. Edit what you
   want and save.
3. Sending is off until you allow it:
   - `make gmail-auth-send` grants a separate send-only Gmail token (same Google account);
   - tick **Autoriser l'envoi des candidatures** in Paramètres.
4. **Approuver et envoyer**, then confirm. The email leaves from your Gmail, the application is
   marked as sent, and replies in that thread are linked to it automatically.

A posting without an application address shows its link instead: apply on the employer's site
with the two PDFs, then click **J'ai postulé via le lien**.

## Good to know

- **Groq free tier.** JobPilot respects 8,000 tokens per minute and 200,000 per day across all
  its processes. A generation can take a few minutes. When the daily budget is used up, an action
  ends with a clear message and nothing is lost: try again later. The **Utilisation** page shows
  where the tokens went.
- **Google tokens expire after 7 days** while your OAuth app is in Testing mode. When triage
  reports an expired sign-in, run `make gmail-auth` again (and `make gmail-auth-send` for
  sending).
- **Regenerating with nothing changed returns the same text** for up to 7 days, because answers
  are cached. Change the tone to get a new wording.
- **Always read a tailored document before sending it.** The guardrail checks numbers, technical
  terms, a list of inflated claims and phrases copied from the posting. It cannot catch every
  awkward or inflated sentence.

## Troubleshooting

| Symptom | What to do |
|---|---|
| `make up` shows no containers, or an empty database | The Docker CLI may point at another engine. `docker context ls` shows the active one; `docker context use desktop-linux` selects Docker Desktop. |
| Port 3000, 8000, 5433 or 6379 is in use | Stop the other program, or change `POSTGRES_HOST_PORT` / `REDIS_HOST_PORT` in `.env`. |
| `.env` changes are ignored | `make restart` recreates the API and the worker. |
| Sign-in to Google fails on a scope | Add the scopes listed in [docs/setup/google-oauth.md](docs/setup/google-oauth.md) to the consent screen. |
| "Budget du modèle de langue épuisé" | The daily Groq budget is used up. It refills continuously over 24 hours. |
| A LaTeX compile fails | The Templates page shows the error log. The sandbox has no network and no shell escape, so packages must be in the image. |

## Development

On a machine with little memory, run only the databases in Docker and the apps on the host:

```bash
make infra          # postgres and redis
make migrate-local
make api-dev        # terminal 1
make worker-dev     # terminal 2
make web-dev        # terminal 3
```

Quality gates:

```bash
make check              # ruff, mypy --strict, eslint, prettier, tsc, pytest, vitest
make test-integration   # needs `make infra`
make e2e                # Playwright journeys against a local stack with fake LLM and Gmail
make gen-client         # after changing the API: regenerate the typed TS client
make eval               # real-LLM evaluations: triage accuracy, injection precision and recall
```

`make help` lists every target. CI runs lint, types, tests, the end-to-end journeys, a secret
scan and the Docker builds on every pull request.

## Layout

```
apps/      api (FastAPI) · worker (arq) · mcp_gmail · agents · web (Next.js 15)
packages/  core (config, models, LLM provider, rate limiter) · security · jobs_sources ·
           latex · resume
infra/     docker-compose, Dockerfiles, LaTeX sandbox, postgres init
scripts/   make_env, set_password, seed_demo
tests/     unit, integration, security and evaluation sets, end-to-end
docs/      architecture, security model, setup guides, ADRs
```

## Documentation

- [Architecture](docs/architecture.md), with the system diagram
- [Security model](docs/security.md): threats and mitigations
- [Gmail setup](docs/setup/google-oauth.md) · [Job sources](docs/setup/job-sources.md)
- [Architecture decision records](docs/adr/)
- [Build brief](PROMPT.md) · [Delivery plan](docs/plan.md)

## License

[MIT](LICENSE)
