# syntax=docker/dockerfile:1.7
# LaTeX compilation sandbox.
#
# Runtime isolation is enforced by docker-compose: network_mode "none", read-only root fs,
# all capabilities dropped, pids/memory/cpu limits. The container never receives requests over
# the network: the worker drops jobs into a shared spool volume and reads results back.
#
# Phase 1 ships the sandbox and spool protocol only; the TeX engine is installed in Phase 6
# (TeX Live + latexmk with shell-escape disabled — see docs/plan.md).

FROM python:3.12-slim-bookworm
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --no-create-home app \
 && mkdir -p /spool/in /spool/out /opt/jobpilot && chown -R app:app /spool
COPY infra/latex/spool_daemon.py /opt/jobpilot/spool_daemon.py
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
USER app
CMD ["python3", "/opt/jobpilot/spool_daemon.py"]
