"""LaTeX sandbox spool daemon (stdlib only; runs inside the network-less latex container).

Protocol (shared volume mounted at /spool):
  in/<job_id>/job.json + sources   written by the worker (job.json last, atomically renamed)
  out/<job_id>/result.json + PDF   written by this daemon
  heartbeat                        JSON {"ts": <unix seconds>, "engine": ...}, refreshed every loop
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

SPOOL = Path(os.environ.get("SPOOL_DIR", "/spool"))
POLL_S = 1.0
HEARTBEAT_MAX_AGE_S = 30
ENGINE = shutil.which("latexmk")


def write_json_atomic(path: Path, data: dict[str, object]) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    tmp.replace(path)


def heartbeat() -> None:
    write_json_atomic(SPOOL / "heartbeat", {"ts": time.time(), "engine": ENGINE or "none"})


def process(job_dir: Path) -> None:
    out_dir = SPOOL / "out" / job_dir.name
    out_dir.mkdir(parents=True, exist_ok=True)
    if ENGINE is None:
        result = {"status": "failed", "error": "TeX engine not installed yet (Phase 6)"}
    else:  # pragma: no cover - implemented in Phase 6
        result = {"status": "failed", "error": "compilation not implemented"}
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
    while True:
        heartbeat()
        for job in sorted((SPOOL / "in").glob("*/job.json")):
            process(job.parent)
        time.sleep(POLL_S)


if __name__ == "__main__":
    if "--check" in sys.argv:
        sys.exit(check())
    main()
