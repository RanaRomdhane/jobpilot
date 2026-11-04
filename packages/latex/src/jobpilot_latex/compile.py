"""Client side of the LaTeX sandbox spool protocol (see infra/latex/spool_daemon.py).

The worker never runs TeX itself: it writes the sources into `<spool>/in/<id>/` (job.json last,
atomically, so the daemon never sees a half-written job) and waits for `<spool>/out/<id>/`.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Literal

Engine = Literal["pdflatex", "xelatex", "lualatex"]
ALLOWED_ASSET_SUFFIXES = frozenset(
    {
        ".tex",
        ".cls",
        ".sty",
        ".bib",
        ".bst",
        ".bbx",
        ".cbx",
        ".lbx",
        ".def",
        ".cfg",
        ".clo",
        ".png",
        ".jpg",
        ".jpeg",
        ".pdf",
        ".eps",
        ".ttf",
        ".otf",
        ".pfb",
        ".afm",
        ".tfm",
        ".fd",
        ".enc",
        ".map",
        ".vf",
    }
)
MAX_ASSET_BYTES = 15 * 1024 * 1024


class CompileError(RuntimeError):
    pass


@dataclass(slots=True)
class CompileResult:
    ok: bool
    pdf: bytes | None
    pages: int | None
    errors: list[str] = field(default_factory=list)
    log: str = ""
    error: str | None = None
    duration_s: float | None = None


def safe_relpath(name: str) -> PurePosixPath:
    """A relative path inside the job dir: no absolute, no `..`, no dotfiles, allowed suffix."""
    p = PurePosixPath(name.replace("\\", "/"))
    if (
        p.is_absolute()
        or not p.parts
        or any(part in ("", ".", "..") or part.startswith(".") for part in p.parts)
        or p.suffix.lower() not in ALLOWED_ASSET_SUFFIXES
        or len(p.parts) > 4
    ):
        raise CompileError(f"file name not allowed: {name!r}")
    return p


async def compile_tex(
    spool: Path,
    main_source: str,
    *,
    assets: dict[str, bytes] | None = None,
    engine: Engine = "pdflatex",
    timeout_s: float = 90,
    poll_s: float = 0.25,
) -> CompileResult:
    """Compile `main_source` (+ template assets) in the sandbox and return the PDF bytes."""
    job_id = uuid.uuid4().hex
    in_dir = spool / "in" / job_id
    out_dir = spool / "out" / job_id
    in_dir.mkdir(parents=True)
    try:
        for name, data in (assets or {}).items():
            rel = safe_relpath(name)
            if len(data) > MAX_ASSET_BYTES:
                raise CompileError(f"file too large: {name}")
            target = in_dir.joinpath(*rel.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        (in_dir / "main.tex").write_text(main_source, encoding="utf-8")
        spec = {"main": "main.tex", "engine": engine, "timeout_s": timeout_s}
        tmp = in_dir / "job.json.tmp"
        tmp.write_text(json.dumps(spec), encoding="utf-8")
        tmp.replace(in_dir / "job.json")
    except Exception:
        shutil.rmtree(in_dir, ignore_errors=True)
        raise

    deadline = time.monotonic() + timeout_s + 60  # the sandbox's own timeout + queueing slack
    result_path = out_dir / "result.json"
    while not result_path.exists():
        if time.monotonic() > deadline:
            shutil.rmtree(in_dir, ignore_errors=True)
            raise CompileError("the LaTeX sandbox did not answer (is the latex container up?)")
        await asyncio.sleep(poll_s)
    try:
        data = json.loads(result_path.read_text(encoding="utf-8"))
        pdf_path = out_dir / "document.pdf"
        ok = data.get("status") == "ok" and pdf_path.exists()
        return CompileResult(
            ok=ok,
            pdf=pdf_path.read_bytes() if ok else None,
            pages=data.get("pages"),
            errors=[str(e) for e in data.get("errors", [])][:10],
            log=str(data.get("log", "")),
            error=None if ok else str(data.get("error") or "compilation failed"),
            duration_s=data.get("duration_s"),
        )
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)
