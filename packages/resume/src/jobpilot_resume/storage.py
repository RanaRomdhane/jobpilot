"""Where uploaded CVs live: `<storage_dir>/resumes/<random>.<ext>`, outside any web root."""

from __future__ import annotations

import secrets
from pathlib import Path

UPLOAD_DIR = "resumes"


def new_relative_path(extension: str) -> str:
    return f"{UPLOAD_DIR}/{secrets.token_hex(16)}.{extension}"


def resume_path(storage_dir: Path, relative: str) -> Path:
    """Stored paths are relative to `storage_dir`; refuse anything that escapes it."""
    root = storage_dir.resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError("resume path escapes the storage directory")
    return path
