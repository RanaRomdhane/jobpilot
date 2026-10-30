"""Upload validation: the file's bytes decide what it is, never its name or declared type.

A CV is untrusted input. We accept exactly two formats, recognised by magic bytes and structure:
- PDF (its objects are checked for active content during extraction, in the worker:
  dictionaries usually sit in compressed object streams, so a byte scan here would be unreliable);
- DOCX (not DOCM): a well-formed OOXML zip, bounded in entries and uncompressed size (zip bombs),
  with no VBA project.
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePath


class FileKind(StrEnum):
    PDF = "pdf"
    DOCX = "docx"


MIME = {
    FileKind.PDF: "application/pdf",
    FileKind.DOCX: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}

DOCX_MAIN = "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
MAX_ZIP_ENTRIES = 2_000
MAX_UNCOMPRESSED = 50 * 1024 * 1024
MAX_RATIO = 200  # per entry; CV parts compress ~10x, bombs ~1000x


class UploadRejectedError(ValueError):
    """`code` is stable (the dashboard translates it); `detail` is for logs."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code


@dataclass(frozen=True, slots=True)
class ValidatedUpload:
    kind: FileKind
    mime: str
    display_name: str


def display_name(filename: str | None, kind: FileKind) -> str:
    """The original name, for display only: no directories, no control characters, bounded."""
    name = PurePath((filename or "").replace("\\", "/")).name
    name = re.sub(r"[\x00-\x1f\x7f]", "", name).strip()[:200]
    return name or f"cv.{kind.value}"


def _check_pdf(data: bytes) -> None:
    if not data.startswith(b"%PDF-"):
        raise UploadRejectedError("unsupported_type", "no PDF header")
    if b"%%EOF" not in data[-2048:]:
        raise UploadRejectedError("corrupt", "PDF has no EOF marker")


def _check_docx(data: bytes) -> None:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise UploadRejectedError("corrupt", "not a zip archive") from exc
    with zf:
        infos = zf.infolist()
        if len(infos) > MAX_ZIP_ENTRIES:
            raise UploadRejectedError("corrupt", "too many zip entries")
        total = 0
        for info in infos:
            total += info.file_size
            if info.file_size > MAX_UNCOMPRESSED or total > MAX_UNCOMPRESSED:
                raise UploadRejectedError("corrupt", "uncompressed size too large")
            if info.compress_size and info.file_size / info.compress_size > MAX_RATIO:
                raise UploadRejectedError(
                    "corrupt", f"suspicious compression ratio: {info.filename}"
                )
        names = {i.filename for i in infos}
        if "[Content_Types].xml" not in names or "word/document.xml" not in names:
            raise UploadRejectedError("unsupported_type", "not a Word document")
        if any(n.lower().endswith("vbaproject.bin") for n in names):
            raise UploadRejectedError("active_content", "document contains macros")
        types = zf.read("[Content_Types].xml")
        if DOCX_MAIN.encode() not in types:
            # .docm / .dotx / .dotm declare a different main part
            raise UploadRejectedError("unsupported_type", "not a .docx document")


def validate_upload(data: bytes, filename: str | None, *, max_bytes: int) -> ValidatedUpload:
    if not data:
        raise UploadRejectedError("empty")
    if len(data) > max_bytes:
        raise UploadRejectedError("too_large", f"{len(data)} > {max_bytes} bytes")
    if data.startswith(b"%PDF-"):
        _check_pdf(data)
        kind = FileKind.PDF
    elif data.startswith(b"PK\x03\x04"):
        _check_docx(data)
        kind = FileKind.DOCX
    else:
        raise UploadRejectedError("unsupported_type", "unknown magic bytes")
    return ValidatedUpload(kind=kind, mime=MIME[kind], display_name=display_name(filename, kind))
