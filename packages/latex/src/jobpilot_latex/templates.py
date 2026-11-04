"""Template files on disk: `<storage>/templates/<id>/`.

    original/<files>      what you uploaded (main .tex + .cls/.sty/images/fonts), read-only
    template.tex.j2       the Jinja template (converted, then possibly edited by you)
    preview.pdf           the last test compile

Uploads are a set of files or one .zip. Every name goes through `safe_relpath` (no absolute
paths, `..`, dotfiles, or unexpected types), zip entries are size-capped and never extracted
outside the folder, and the whole set is capped in size and count.
"""

from __future__ import annotations

import io
import re
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from jobpilot_latex.compile import ALLOWED_ASSET_SUFFIXES, CompileError, Engine, safe_relpath

TEMPLATES_DIR = "templates"
MAX_FILES = 60
MAX_TOTAL_BYTES = 40 * 1024 * 1024
MAX_FILE_BYTES = 15 * 1024 * 1024
TEMPLATE_FILE = "template.tex.j2"
PREVIEW_FILE = "preview.pdf"
# documentation shipped with Overleaf templates: never needed to compile
_SKIP = re.compile(r"(?i)(^|/)(readme(\.\w+)?|license(\.\w+)?|__macosx/.*|.*\.(md|txt|log|aux))$")


class TemplateUploadError(ValueError):
    pass


@dataclass(slots=True)
class TemplateFiles:
    main: str  # relative path of the main .tex
    files: dict[str, bytes]  # every file to keep, main included


def unpack(uploads: list[tuple[str, bytes]]) -> TemplateFiles:
    """Uploaded files (or one zip) -> validated relative paths and bytes, plus the main .tex."""
    files: dict[str, bytes] = {}
    for name, data in uploads:
        if name.lower().endswith(".zip"):
            files.update(_unzip(data))
        else:
            _add(files, PurePosixPath(name.replace("\\", "/")).name, data)
    if not files:
        raise TemplateUploadError("no usable file (expected .tex, .cls, .sty, images, fonts)")
    if len(files) > MAX_FILES or sum(len(d) for d in files.values()) > MAX_TOTAL_BYTES:
        raise TemplateUploadError("too many or too large files")
    return TemplateFiles(choose_main(files), files)


def _add(files: dict[str, bytes], name: str, data: bytes) -> None:
    if _SKIP.search(name) or PurePosixPath(name).suffix.lower() not in ALLOWED_ASSET_SUFFIXES:
        return
    try:
        rel = safe_relpath(name)
    except CompileError as exc:
        raise TemplateUploadError(str(exc)) from exc
    if len(data) > MAX_FILE_BYTES:
        raise TemplateUploadError(f"file too large: {name}")
    files[str(rel)] = data


def _unzip(data: bytes) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise TemplateUploadError("not a valid zip file") from exc
    with zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        if len(infos) > MAX_FILES * 2:
            raise TemplateUploadError("zip has too many files")
        # a single top-level folder (Overleaf exports) is stripped
        roots = {PurePosixPath(i.filename).parts[0] for i in infos}
        strip = len(roots) == 1 and all(len(PurePosixPath(i.filename).parts) > 1 for i in infos)
        for info in infos:
            if info.file_size > MAX_FILE_BYTES or info.flag_bits & 0x1:  # too big / encrypted
                raise TemplateUploadError(f"zip entry not allowed: {info.filename}")
            parts = PurePosixPath(info.filename).parts[1 if strip else 0 :]
            if not parts:
                continue
            name = "/".join(parts)
            if _SKIP.search(name):
                continue
            with zf.open(info) as fh:
                blob = fh.read(MAX_FILE_BYTES + 1)
            if len(blob) > MAX_FILE_BYTES:  # the header lied about the size (zip bomb)
                raise TemplateUploadError(f"zip entry too large: {info.filename}")
            _add(out, name, blob)
    return out


def choose_main(files: dict[str, bytes]) -> str:
    """The .tex with \\documentclass and \\begin{document}; prefer main.tex / *letter* / *cv*."""
    candidates = [
        n for n, d in files.items()
        if n.endswith(".tex") and b"\\begin{document}" in d and b"\\documentclass" in d
    ]  # fmt: skip
    if not candidates:
        raise TemplateUploadError("no main .tex file (with \\documentclass and \\begin{document})")

    def rank(name: str) -> tuple[int, int, str]:
        stem = PurePosixPath(name).stem.lower()
        preferred = 0 if stem in ("main", "cv", "resume", "cover_letter", "letter") else 1
        return (name.count("/"), preferred, name)

    return sorted(candidates, key=rank)[0]


def detect_engine(source: str) -> Engine:
    if re.search(
        r"\\(usepackage|RequirePackage)(\[[^\]]*\])?\{[^}]*\b(luacode|luatexbase)\b", source
    ):
        return "lualatex"
    if re.search(r"\\(usepackage|RequirePackage)(\[[^\]]*\])?\{[^}]*\bfontspec\b", source):
        return "xelatex"
    return "pdflatex"


def template_root(storage_dir: Path, template_id: int) -> Path:
    root = (storage_dir / TEMPLATES_DIR).resolve()
    path = (root / str(int(template_id))).resolve()
    if not path.is_relative_to(root):  # pragma: no cover - int() already guarantees it
        raise ValueError("template path escapes the storage directory")
    return path


def save_files(storage_dir: Path, template_id: int, tf: TemplateFiles) -> Path:
    root = template_root(storage_dir, template_id)
    original = root / "original"
    shutil.rmtree(original, ignore_errors=True)
    for name, data in tf.files.items():
        target = original.joinpath(*PurePosixPath(name).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return root


def assets(storage_dir: Path, template_id: int, main: str) -> dict[str, bytes]:
    """Every uploaded file except the main .tex (sent to the sandbox with each compile)."""
    original = template_root(storage_dir, template_id) / "original"
    out: dict[str, bytes] = {}
    for p in sorted(original.rglob("*")):
        rel = p.relative_to(original).as_posix()
        if p.is_file() and rel != main:
            out[rel] = p.read_bytes()
    return out


def read_main(storage_dir: Path, template_id: int, main: str) -> str:
    path = template_root(storage_dir, template_id) / "original" / main
    return path.read_bytes().decode("utf-8", errors="replace")


def delete(storage_dir: Path, template_id: int) -> None:
    shutil.rmtree(template_root(storage_dir, template_id), ignore_errors=True)
