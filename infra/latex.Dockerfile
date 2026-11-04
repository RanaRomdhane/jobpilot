# syntax=docker/dockerfile:1.7
# LaTeX compilation sandbox: TeX Live (curated) + latexmk, offline.
#
# Runtime isolation is enforced by docker-compose: network_mode "none", read-only root fs,
# all capabilities dropped, pids/memory/cpu limits. The container never receives requests over
# the network: the worker drops jobs into a shared spool volume and reads results back.
# The daemon itself runs every compile with shell-escape off, `-norc` (no latexmkrc Perl),
# paranoid file access (openin_any/openout_any = p) and a hard timeout (see spool_daemon.py).

FROM python:3.12-slim-bookworm
# curated TeX Live 2022: pdfLaTeX/XeLaTeX/LuaLaTeX, the common CV classes and packages
# (moderncv, altacv, awesome-cv deps, fontawesome5, ...), fonts, French + English, biber
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      latexmk biber \
      texlive-latex-base texlive-latex-recommended texlive-latex-extra \
      texlive-fonts-recommended texlive-fonts-extra \
      texlive-xetex texlive-luatex texlive-pictures texlive-bibtex-extra \
      texlive-lang-french texlive-lang-english texlive-lang-arabic \
      lmodern fontconfig \
 && rm -rf /var/lib/apt/lists/* /usr/share/doc/* /usr/share/man/* \
 && fc-cache -f >/dev/null
# LuaLaTeX font database, built once here into TEXMFSYSVAR (the runtime root fs is read-only)
RUN luaotfload-tool -u >/dev/null 2>&1 || echo "luaotfload cache not built (first LuaLaTeX run is slower)"

RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --no-create-home app \
 && mkdir -p /spool/in /spool/out /opt/jobpilot && chown -R app:app /spool
COPY infra/latex/spool_daemon.py /opt/jobpilot/spool_daemon.py
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 HOME=/tmp \
    TEXMFVAR=/tmp/texmf-var TEXMFCONFIG=/tmp/texmf-config
USER app
CMD ["python3", "/opt/jobpilot/spool_daemon.py"]
