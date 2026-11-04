"""LaTeX sandbox spool daemon (stdlib only; runs inside the network-less latex container).

Protocol (shared volume mounted at /spool):
  in/<job_id>/job.json + sources   written by the worker (sources first, job.json last, atomically)
  out/<job_id>/result.json + PDF   written by this daemon (result.json last, atomically)
  heartbeat                        JSON {"ts": <unix seconds>, "engine": ...}, refreshed every loop

job.json: {"main": "main.tex", "engine": "pdflatex" | "xelatex" | "lualatex", "timeout_s": 60}
result.json: {"status": "ok" | "failed", "pages": int | null, "errors": [...], "log": "...",
              "error": "...", "duration_s": float}

Every compile runs on a private copy in /tmp with:
  - `-no-shell-escape` and `shell_escape=f` (\\write18 disabled),
  - `-norc` (latexmk never executes a latexmkrc, which is Perl), and any rc file is refused,
  - `openin_any=p` / `openout_any=p` (no absolute paths, no `..`, no dotfiles),
  - a hard timeout that kills the whole process group, and bounded output sizes.
The container itself has no network, a read-only root fs and no capabilities (docker-compose).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

SPOOL = Path(os.environ.get("SPOOL_DIR", "/spool"))
WORK = Path(os.environ.get("LATEX_WORK_DIR", "/tmp/jobs"))  # noqa: S108 - tmpfs in the sandbox
POLL_S = 0.5
HEARTBEAT_MAX_AGE_S = 30
ENGINE = shutil.which("latexmk")
ENGINES = {"pdflatex": "-pdf", "xelatex": "-xelatex", "lualatex": "-lualatex"}
MAX_TIMEOUT_S = 180
MAX_PDF_BYTES = 20 * 1024 * 1024
MAX_LOG_CHARS = 60_000
JOB_ID = re.compile(r"^[0-9a-f]{32}$")
MAIN_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}\.tex$")
RC_FILES = re.compile(r"(^|/)\.?latexmkrc$", re.IGNORECASE)
PAGES = re.compile(r"Output written on .*?\((\d+) pages?")


def write_json_atomic(path: Path, data: dict[str, object]) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    tmp.replace(path)


def heartbeat() -> None:
    write_json_atomic(SPOOL / "heartbeat", {"ts": time.time(), "engine": ENGINE or "none"})


def tex_errors(log: str) -> list[str]:
    """`! ...` error lines with the `l.<n> ...` line that locates them (at most 10)."""
    lines = log.splitlines()
    out: list[str] = []
    for i, line in enumerate(lines):
        if line.startswith("! "):
            where = next((x.strip() for x in lines[i + 1 : i + 12] if x.startswith("l.")), "")
            out.append(f"{line[2:].strip()} ({where})" if where else line[2:].strip())
            if len(out) >= 10:
                break
    return out


def compile_job(job_dir: Path, spec: dict[str, object]) -> dict[str, object]:
    main = str(spec.get("main", "main.tex"))
    engine = str(spec.get("engine", "pdflatex"))
    timeout = min(float(spec.get("timeout_s", 60)), MAX_TIMEOUT_S)  # type: ignore[arg-type]
    if not MAIN_NAME.match(main) or engine not in ENGINES:
        return {"status": "failed", "error": "invalid job"}
    if ENGINE is None:
        return {"status": "failed", "error": "TeX engine not installed"}
    if any(RC_FILES.search(p.relative_to(job_dir).as_posix()) for p in job_dir.rglob("*")):
        return {"status": "failed", "error": "latexmkrc files are not allowed"}
    if any(p.is_symlink() for p in job_dir.rglob("*")):
        return {"status": "failed", "error": "symbolic links are not allowed"}

    work = WORK / job_dir.name
    shutil.rmtree(work, ignore_errors=True)
    shutil.copytree(job_dir, work, ignore=shutil.ignore_patterns("job.json"))
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(WORK),
        "TEXMFVAR": os.environ.get("TEXMFVAR", "/tmp/texmf-var"),  # noqa: S108
        "TEXMFCONFIG": os.environ.get("TEXMFCONFIG", "/tmp/texmf-config"),  # noqa: S108
        "shell_escape": "f",
        "openin_any": "p",
        "openout_any": "p",
        "max_print_line": "10000",
        "SOURCE_DATE_EPOCH": "0",  # reproducible PDFs (same input -> same bytes)
        "FORCE_SOURCE_DATE": "1",
    }
    cmd = [
        ENGINE,
        "-norc",
        ENGINES[engine],
        "-interaction=nonstopmode",
        "-halt-on-error",
        "-no-shell-escape",
        "-outdir=out",
        main,
    ]
    started = time.monotonic()
    proc = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
        cmd,
        cwd=work,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    timed_out = False
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()
    duration = round(time.monotonic() - started, 2)

    stem = main[:-4]
    log_path = work / "out" / f"{stem}.log"
    log = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
    pdf = work / "out" / f"{stem}.pdf"
    pages = PAGES.findall(log)
    result: dict[str, object] = {
        "status": "failed",
        "pages": int(pages[-1]) if pages else None,
        "errors": tex_errors(log),
        "log": log[-MAX_LOG_CHARS:],
        "duration_s": duration,
    }
    out_dir = SPOOL / "out" / job_dir.name
    if timed_out:
        result["error"] = f"compilation timed out after {timeout:.0f}s"
    elif proc.returncode != 0 or not pdf.exists():
        result["error"] = "compilation failed"
    elif pdf.stat().st_size > MAX_PDF_BYTES:
        result["error"] = "PDF too large"
    else:
        shutil.copyfile(pdf, out_dir / "document.pdf")
        result["status"] = "ok"
    shutil.rmtree(work, ignore_errors=True)
    return result


def process(job_dir: Path) -> None:
    out_dir = SPOOL / "out" / job_dir.name
    out_dir.mkdir(parents=True, exist_ok=True)
    if not JOB_ID.match(job_dir.name):
        result: dict[str, object] = {"status": "failed", "error": "invalid job id"}
    else:
        try:
            spec = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
            result = compile_job(job_dir, spec if isinstance(spec, dict) else {})
        except Exception as exc:
            result = {"status": "failed", "error": f"sandbox error: {type(exc).__name__}"}
    write_json_atomic(out_dir / "result.json", result)
    shutil.rmtree(job_dir, ignore_errors=True)


def check() -> int:
    try:
        data = json.loads((SPOOL / "heartbeat").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 1
    return 0 if time.time() - float(data["ts"]) < HEARTBEAT_MAX_AGE_S else 1


def main() -> None:
    (SPOOL / "in").mkdir(parents=True, exist_ok=True)
    (SPOOL / "out").mkdir(parents=True, exist_ok=True)
    WORK.mkdir(parents=True, exist_ok=True)

    def beat() -> None:  # own thread: a long compile must not make the sandbox look dead
        while True:
            heartbeat()
            time.sleep(5)

    threading.Thread(target=beat, daemon=True).start()
    while True:
        for job in sorted((SPOOL / "in").glob("*/job.json")):
            process(job.parent)
        time.sleep(POLL_S)


if __name__ == "__main__":
    if "--check" in sys.argv:
        sys.exit(check())
    main()
