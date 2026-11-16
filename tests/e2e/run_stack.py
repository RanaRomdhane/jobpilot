"""Start an isolated JobPilot for the end-to-end tests, run Playwright, stop everything.

    uv run python -m tests.e2e.run_stack            # run the Playwright journeys (`make e2e`)
    uv run python -m tests.e2e.run_stack --serve    # only start the stack, until Ctrl+C

It needs PostgreSQL and Redis (`make infra`, or the CI services) and nothing else: the language
model and Gmail are a local stub, the database is `<name>_e2e_demo`, Redis index 2, storage
`storage/e2e`, and the ports differ from the real stack (web 3100, API 8100, stub 8900).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import fitz  # PyMuPDF
from alembic import command
from alembic.config import Config
from cryptography.fernet import Fernet
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import create_async_engine

from jobpilot_api.auth import hash_password
from jobpilot_core.config import Settings

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "apps" / "web"
STORAGE = ROOT / "storage" / "e2e"
PASSWORD = "e2e-password-not-secret"
WEB_PORT, API_PORT, STUB_PORT = 3100, 8100, 8900


def stack_env() -> dict[str, str]:
    base = Settings()
    db = make_url(base.database_url)
    redis = base.redis_url.rsplit("/", 1)[0]
    return {
        **os.environ,
        "ENV": "test",
        "DATABASE_URL": db.set(database=f"{db.database}_e2e_demo").render_as_string(
            hide_password=False
        ),
        "REDIS_URL": f"{redis}/2",
        "STORAGE_DIR": str(STORAGE),
        "LATEX_SPOOL_DIR": str(STORAGE / "spool"),
        "DEMO_MODE": "true",
        "LLM_PROVIDER": "groq",
        "GROQ_API_KEY": "e2e-not-a-real-key",
        "GROQ_BASE_URL": f"http://127.0.0.1:{STUB_PORT}/v1",
        "GMAIL_API_URL": f"http://127.0.0.1:{STUB_PORT}/gmail",
        "GUARD_ENABLED": "false",
        "RESUME_EXTRACT_TIMEOUT_S": "180",  # slow laptops and CI runners
        "LLM_FALLBACK_TO_OPENAI": "false",
        "FERNET_KEY": Fernet.generate_key().decode(),
        "DASHBOARD_PASSWORD_HASH": hash_password(PASSWORD),
        "SESSION_COOKIE_SECURE": "false",
        "CORS_ORIGINS": f"http://localhost:{WEB_PORT},http://127.0.0.1:{WEB_PORT}",
        "API_INTERNAL_URL": f"http://127.0.0.1:{API_PORT}",
        "E2E_BASE_URL": f"http://localhost:{WEB_PORT}",
        "E2E_STUB_URL": f"http://127.0.0.1:{STUB_PORT}",
        "E2E_PASSWORD": PASSWORD,
        "E2E_CV": str(STORAGE / "cv.pdf"),
        "PYTHONUNBUFFERED": "1",
    }


async def _create_database(url: str) -> None:
    admin, _, name = url.rpartition("/")
    assert name.endswith("_e2e_demo")
    engine = create_async_engine(f"{admin}/postgres", isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            exists = await conn.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": name}
            )
            if not exists:
                await conn.execute(text(f'CREATE DATABASE "{name}"'))
    finally:
        await engine.dispose()


def cv_pdf(path: Path) -> None:
    """A synthetic one-page CV with selectable text."""
    doc = fitz.open()
    page = doc.new_page()
    lines = [
        "Ada Example",
        "ada@example.com · Tunis",
        "",
        "Experience",
        "ML Engineer, Exemplia Labs (2024 - present)",
        "- Built a text classifier reaching 91% accuracy on 4,000 tickets",
        "",
        "Education",
        "Engineering Degree, Example Engineering School (2024)",
        "",
        "Skills",
        "Python, SQL, PyTorch",
    ]
    for i, line in enumerate(lines):
        page.insert_text((72, 80 + 18 * i), line, fontsize=16 if i == 0 else 11)
    doc.save(str(path))
    doc.close()


def prepare(env: dict[str, str]) -> None:
    shutil.rmtree(STORAGE, ignore_errors=True)
    (STORAGE / "spool").mkdir(parents=True)
    cv_pdf(STORAGE / "cv.pdf")
    asyncio.run(_create_database(env["DATABASE_URL"]))
    cfg = Config(str(ROOT / "packages/core/alembic.ini"))
    cfg.attributes["database_url"] = env["DATABASE_URL"]
    command.upgrade(cfg, "head")
    subprocess.run(  # noqa: S603
        [sys.executable, str(ROOT / "scripts" / "seed_demo.py"), "--e2e"],
        env=env,
        cwd=ROOT,
        check=True,
    )


def start(args: list[str], env: dict[str, str], cwd: Path, log: Path) -> subprocess.Popen[bytes]:
    # looked up by name: each exists on one platform only, and mypy checks both
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
    return subprocess.Popen(  # noqa: S603
        args,
        env=env,
        cwd=cwd,
        stdout=log.open("wb"),
        stderr=subprocess.STDOUT,
        creationflags=flags,
        start_new_session=os.name != "nt",
    )


def stop(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(  # noqa: S603
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],  # noqa: S607
            capture_output=True,
            check=False,
        )
    else:
        getattr(os, "killpg")(proc.pid, signal.SIGTERM)  # noqa: B009
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()


def wait_for(url: str, name: str, proc: subprocess.Popen[bytes], timeout_s: float = 180) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise SystemExit(f"{name} stopped while starting: see storage/e2e/logs/{name}.log")
        try:
            with urllib.request.urlopen(url, timeout=3) as resp:  # noqa: S310 - localhost
                if resp.status < 500:
                    return
        except OSError:
            time.sleep(1)
    raise SystemExit(f"{name} did not answer on {url}: see storage/e2e/logs/{name}.log")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the JobPilot end-to-end tests")
    parser.add_argument("--serve", action="store_true", help="start the stack and wait")
    parser.add_argument("--skip-build", action="store_true", help="reuse the last web build")
    parser.add_argument("playwright_args", nargs="*", help="passed to `playwright test`")
    args = parser.parse_args()

    env = stack_env()
    prepare(env)
    logs = STORAGE / "logs"
    logs.mkdir(exist_ok=True)
    pnpm = shutil.which("pnpm") or "pnpm"
    uv = shutil.which("uv") or "uv"
    if not args.skip_build:
        subprocess.run([pnpm, "build"], env=env, cwd=WEB, check=True)  # noqa: S603

    procs: list[subprocess.Popen[bytes]] = []
    try:
        specs = [
            (
                "stub",
                [uv, "run", "uvicorn", "tests.e2e.stub:app", "--port", str(STUB_PORT)],
                ROOT,
                f"http://127.0.0.1:{STUB_PORT}/health",
            ),
            (
                "api",
                [uv, "run", "uvicorn", "jobpilot_api.main:app", "--port", str(API_PORT)],
                ROOT,
                f"http://127.0.0.1:{API_PORT}/api/v1/health/live",
            ),
            ("worker", [uv, "run", "arq", "jobpilot_worker.main.WorkerSettings"], ROOT, None),
            (
                "web",
                [pnpm, "start", "-p", str(WEB_PORT)],
                WEB,
                f"http://127.0.0.1:{WEB_PORT}/login",
            ),
        ]
        for name, cmd, cwd, url in specs:
            proc = start(cmd, env, cwd, logs / f"{name}.log")
            procs.append(proc)
            if url:
                wait_for(url, name, proc)
        if args.serve:
            print(f"E2E stack ready: http://localhost:{WEB_PORT} (password: {PASSWORD})")
            while all(p.poll() is None for p in procs):
                time.sleep(1)
            return 1
        result = subprocess.run(  # noqa: S603
            [pnpm, "exec", "playwright", "test", *args.playwright_args],
            env=env,
            cwd=WEB,
            check=False,
        )
        return result.returncode
    except KeyboardInterrupt:
        return 130
    finally:
        for proc in reversed(procs):
            stop(proc)


if __name__ == "__main__":
    sys.exit(main())
