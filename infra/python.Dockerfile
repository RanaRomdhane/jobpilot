# syntax=docker/dockerfile:1.7
# One image for api, worker and migrate (different commands).

FROM ghcr.io/astral-sh/uv:0.13.6 AS uv

FROM python:3.12-slim-bookworm AS build
COPY --from=uv /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app

# 1) third-party dependencies only (cached until uv.lock / pyproject files change)
COPY pyproject.toml uv.lock .python-version ./
COPY packages/core/pyproject.toml packages/core/
COPY packages/security/pyproject.toml packages/security/
COPY packages/jobs_sources/pyproject.toml packages/jobs_sources/
COPY packages/latex/pyproject.toml packages/latex/
COPY apps/api/pyproject.toml apps/api/
COPY apps/worker/pyproject.toml apps/worker/
COPY apps/mcp_gmail/pyproject.toml apps/mcp_gmail/
COPY apps/agents/pyproject.toml apps/agents/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-workspace

# 2) workspace packages
COPY packages packages
COPY apps/api apps/api
COPY apps/worker apps/worker
COPY apps/mcp_gmail apps/mcp_gmail
COPY apps/agents apps/agents
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim-bookworm AS runtime
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --no-create-home app \
 && mkdir -p /app/storage /spool && chown -R app:app /app/storage /spool
WORKDIR /app
COPY --from=build --chown=app:app /app/.venv /app/.venv
COPY --chown=app:app packages/core/alembic.ini packages/core/alembic.ini
COPY --chown=app:app packages/core/migrations packages/core/migrations
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
USER app
