"""Text and layout extraction from PDF (PyMuPDF) and DOCX (python-docx).

Besides the visible text (what a recruiter reads and what we send to the parser), we record the
layout signals the ATS checks need: columns, tables, text boxes, images, header/footer text, fonts,
bullet glyphs, graphical skill bars, and text a human cannot see (white, tiny, transparent or
off-page text, Word "hidden" runs). Invisible text is kept apart: it is scanned for prompt injection
and reported, but never parsed into the profile.

Runs in the worker only: parsing untrusted documents never happens in the API process.
"""

from __future__ import annotations

import io
import re
import zipfile
from collections import Counter
from collections.abc import Iterator
from typing import Any

from pydantic import BaseModel, Field

from jobpilot_resume.upload import FileKind, UploadRejectedError

MAX_PAGES = 10
MAX_PDF_OBJECTS = 50_000
MAX_LINKS = 20

BULLET_STANDARD = frozenset("•·-–—*▪■◦○●")
RATING_RUN = re.compile(r"(?:[●○◉◯★☆✩✭■□▪▫⬤◼◻]\s?){3,}")
PRIVATE_USE = re.compile(r"[-]")
_ACTIVE_KEYS = re.compile(r"/(JavaScript|JS|Launch|EmbeddedFile|EmbeddedFiles|RichMedia|XFA)\b")
_SUBSET = re.compile(r"^[A-Z]{6}\+")
_STYLE = re.compile(
    r"[-,]?(Bold|Italic|Oblique|Regular|Light|Medium|Semibold|SemiBold|Black|Heavy|Thin|"
    r"ExtraBold|ExtraLight|Book|Roman|Condensed|BoldItalic|MT|PS)+$"
)
ICON_FONT = re.compile(
    r"(awesome|fontello|icomoon|material.?(icons|symbols)|glyphicons|"
    r"ionicons|feather|academicons|wingdings|webdings)",
    re.I,
)


class LayoutSignals(BaseModel):
    page_count: int | None = None
    multi_column_pages: list[int] = Field(default_factory=list)
    tables: int = 0
    table_chars: int = 0
    text_boxes: int = 0
    text_box_chars: int = 0
    images: int = 0
    image_area_ratio: float = 0.0  # largest share of a page covered by images
    header_footer_text: list[str] = Field(default_factory=list)
    fonts: list[str] = Field(default_factory=list)  # families
    type3_fonts: int = 0
    icon_fonts: list[str] = Field(default_factory=list)
    bullet_glyphs: dict[str, int] = Field(default_factory=dict)
    list_paragraphs: int = 0  # DOCX real list items (numbering)
    private_use_chars: int = 0
    replacement_chars: int = 0
    rating_glyph_runs: int = 0
    skill_bar_shapes: int = 0
    hidden_chars: int = 0
    # clickable web links (PDF link annotations, DOCX hyperlinks): an address may be clickable
    # without being printed, and ATS usually read only the printed text
    links: list[str] = Field(default_factory=list)


class ExtractedDocument(BaseModel):
    kind: FileKind
    text: str
    hidden: list[str] = Field(default_factory=list)
    signals: LayoutSignals = Field(default_factory=LayoutSignals)

    @property
    def word_count(self) -> int:
        return len(re.findall(r"\w+", self.text))


def extract(data: bytes, kind: FileKind) -> ExtractedDocument:
    return _extract_pdf(data) if kind is FileKind.PDF else _extract_docx(data)


def _common_text_signals(text: str, signals: LayoutSignals) -> None:
    signals.private_use_chars = len(PRIVATE_USE.findall(text))
    signals.replacement_chars = text.count("�") + text.count("(cid:")
    signals.rating_glyph_runs = len(RATING_RUN.findall(text))
    glyphs: Counter[str] = Counter()
    for line in text.split("\n"):
        s = line.lstrip()
        if s and not s[0].isalnum() and s[0] not in "([{\"'«+@#&/.,:;!?%$€£<":
            glyphs[s[0]] += 1
    signals.bullet_glyphs = {g: n for g, n in glyphs.most_common(8) if n >= 2}


_SENTENCE_END = re.compile(r"[.!?:;)\]»”]$")
_BULLET_START = re.compile(r"^[•·\-–—*▪■◦○●➢►✓✔]\s")


def reflow(text: str) -> str:
    """Join lines that PDF extraction wrapped mid-sentence (for the LLM, not for layout checks).

    A line is joined to the previous one when the previous one does not end a sentence and the
    next one starts with a lowercase letter or a digit and is not a new bullet.
    """
    out: list[str] = []
    for line in text.split("\n"):
        s = line.strip()
        if (
            out
            and s
            and out[-1]
            and not _SENTENCE_END.search(out[-1])
            and not _BULLET_START.match(s)
            and (s[0].islower() or s[0].isdigit())
        ):
            prev = out[-1]
            out[-1] = prev[:-1] + s if prev.endswith("-") and s[0].islower() else f"{prev} {s}"
        else:
            out.append(s)
    return "\n".join(out)


def llm_text(doc: ExtractedDocument) -> str:
    """What the parser and the reviewer read: reflowed visible text plus unprinted link targets."""
    text = reflow(doc.text)
    unprinted = [u for u in doc.signals.links if u.split("://", 1)[-1].rstrip("/") not in doc.text]
    if unprinted:
        text += "\n\nHyperlinks in the document:\n" + "\n".join(unprinted)
    return text


def font_family(name: str) -> str:
    base = _SUBSET.sub("", name).split("+")[-1]
    return _STYLE.sub("", base).rstrip("-,") or base


# ------------------------------------------------------------------ PDF
def _luminance(rgb: tuple[float, float, float]) -> float:
    r, g, b = rgb
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _rgb_int(color: int) -> tuple[float, float, float]:
    return ((color >> 16) & 255) / 255, ((color >> 8) & 255) / 255, (color & 255) / 255


def _inside(pt: tuple[float, float], r: Any) -> bool:
    return bool(r[0] <= pt[0] <= r[2] and r[1] <= pt[1] <= r[3])


def _check_pdf_active(doc: Any) -> None:
    if doc.embfile_count() > 0:
        raise UploadRejectedError("active_content", "PDF has embedded files")
    for xref in range(1, min(doc.xref_length(), MAX_PDF_OBJECTS)):
        try:
            obj = doc.xref_object(xref, compressed=True)
        except Exception:  # noqa: S112 - a broken object is not active content
            continue
        if _ACTIVE_KEYS.search(obj):
            raise UploadRejectedError("active_content", f"active content in object {xref}")


def _multi_column(blocks: list[tuple[float, float, float, float, int]], w: float, h: float) -> bool:
    """Two text blocks side by side over a large vertical span = a multi-column page.

    Single lines (a date right-aligned next to a job title) do not count: both blocks must hold at
    least two lines or be taller than ~30pt.
    """
    tall = [b for b in blocks if b[4] >= 2 or (b[3] - b[1]) > 30]
    spans: list[tuple[float, float]] = []
    for a in tall:
        for b in tall:
            if b[0] >= a[2] - 2 and (a[2] - a[0]) < 0.62 * w and (b[2] - b[0]) < 0.62 * w:
                top, bottom = max(a[1], b[1]), min(a[3], b[3])
                if bottom - top > 8:
                    spans.append((top, bottom))
    if not spans:
        return False
    spans.sort()
    covered, (cur_s, cur_e) = 0.0, spans[0]
    for s, e in spans[1:]:
        if s > cur_e:
            covered += cur_e - cur_s
            cur_s, cur_e = s, e
        else:
            cur_e = max(cur_e, e)
    covered += cur_e - cur_s
    return covered > 0.12 * h


def _skill_bars(drawings: list[dict[str, Any]]) -> int:
    """Rows of thin filled bars or small dots stacked at the same x: graphical skill levels."""
    shapes = []
    for d in drawings:
        r, fill = d.get("rect"), d.get("fill")
        if r is None or fill is None:
            continue
        w, h = r[2] - r[0], r[3] - r[1]
        if 2 <= h <= 14 and 15 <= w <= 260:
            shapes.append(("bar", round(r[0] / 3), round(r[1])))
        elif 3 <= w <= 14 and abs(w - h) <= 1.5:
            shapes.append(("dot", round(r[1] / 3), round(r[0])))
    bars = Counter(x for kind, x, _ in shapes if kind == "bar")
    dot_rows = Counter(y for kind, y, _ in shapes if kind == "dot")
    n = sum(c for c in bars.values() if c >= 3)
    n += sum(1 for c in dot_rows.values() if c >= 4)
    return n


def _extract_pdf(data: bytes) -> ExtractedDocument:
    import pymupdf

    try:
        doc: Any = pymupdf.open(stream=data, filetype="pdf")  # type: ignore[no-untyped-call]
    except Exception as exc:
        raise UploadRejectedError("corrupt", f"PDF cannot be opened: {exc}") from exc
    with doc:
        if doc.needs_pass:
            raise UploadRejectedError("encrypted", "PDF is password-protected")
        if doc.page_count > MAX_PAGES:
            raise UploadRejectedError("too_many_pages", f"{doc.page_count} pages")
        _check_pdf_active(doc)

        sig = LayoutSignals(page_count=doc.page_count)
        parts: list[str] = []
        hidden: list[str] = []
        fonts: set[str] = set()
        icon_fonts: set[str] = set()
        edge_lines: Counter[str] = Counter()

        links: list[str] = []
        for pno, page in enumerate(doc):
            pw, ph = page.rect.width, page.rect.height
            for link in page.get_links():
                uri = str(link.get("uri") or "")
                if uri.startswith(("http://", "https://", "mailto:")) and uri not in links:
                    links.append(uri[:300])
            drawings = page.get_drawings()
            images = page.get_image_info()
            sig.images += len(images)
            img_area = sum(
                max(0.0, (i["bbox"][2] - i["bbox"][0]) * (i["bbox"][3] - i["bbox"][1]))
                for i in images
            )
            sig.image_area_ratio = max(sig.image_area_ratio, round(img_area / (pw * ph), 3))
            dark = [
                d["rect"]
                for d in drawings
                if d.get("fill") is not None and _luminance(d["fill"]) < 0.75
            ] + [i["bbox"] for i in images]
            sig.skill_bar_shapes += _skill_bars(drawings)

            for f in page.get_fonts():
                ftype, basefont = f[2], f[3]
                if ftype == "Type3":
                    sig.type3_fonts += 1
                fam = font_family(basefont)
                if ICON_FONT.search(fam):
                    icon_fonts.add(fam)
                elif fam:
                    fonts.add(fam)

            blocks_geo: list[tuple[float, float, float, float, int]] = []
            raw = page.get_text("dict", flags=pymupdf.TEXTFLAGS_TEXT)
            for block in raw.get("blocks", []):
                if block.get("type") != 0:
                    continue
                lines_out: list[str] = []
                for line in block.get("lines", []):
                    vis: list[str] = []
                    hid: list[str] = []
                    for span in line.get("spans", []):
                        t = span.get("text", "")
                        if not t.strip():
                            vis.append(t)
                            continue
                        x0, y0, x1, y1 = span["bbox"]
                        center = ((x0 + x1) / 2, (y0 + y1) / 2)
                        offpage = x1 < 0 or y1 < 0 or x0 > pw or y0 > ph
                        whiteish = _luminance(_rgb_int(span.get("color", 0))) > 0.92
                        on_dark = any(_inside(center, r) for r in dark)
                        if (
                            span.get("size", 10) < 3
                            or span.get("alpha", 255) == 0
                            or offpage
                            or (whiteish and not on_dark)
                        ):
                            hid.append(t)
                        else:
                            vis.append(t)
                    if hid:
                        hidden.append("".join(hid).strip())
                    text = "".join(vis).strip()
                    if text:
                        lines_out.append(text)
                        ly0, ly1 = line["bbox"][1], line["bbox"][3]
                        if ly1 < 0.08 * ph or ly0 > 0.92 * ph:
                            edge_lines[re.sub(r"\d+", "#", text)] += 1
                if lines_out:
                    parts.append("\n".join(lines_out))
                    x0, y0, x1, y1 = block["bbox"]
                    blocks_geo.append((x0, y0, x1, y1, len(lines_out)))

            if _multi_column(blocks_geo, pw, ph):
                sig.multi_column_pages.append(pno + 1)
            try:
                for tab in page.find_tables().tables:
                    if tab.row_count >= 2 and tab.col_count >= 2:
                        sig.tables += 1
                        cells = tab.extract()
                        sig.table_chars += sum(len(c or "") for row in cells for c in row)
            except Exception:  # noqa: S110 - table detection is best effort
                pass

        if doc.page_count > 1:
            sig.header_footer_text = [
                t for t, n in edge_lines.items() if n >= 2 and not re.fullmatch(r"[#\s/|-]*", t)
            ][:10]

    text = "\n".join(parts)
    sig.links = links[:MAX_LINKS]
    sig.fonts = sorted(fonts)
    sig.icon_fonts = sorted(icon_fonts)
    hidden = [h for h in hidden if h]
    sig.hidden_chars = sum(len(h) for h in hidden)
    _common_text_signals(text, sig)
    return ExtractedDocument(kind=FileKind.PDF, text=text, hidden=hidden, signals=sig)


# ------------------------------------------------------------------ DOCX
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
WP = "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}"
MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"
EMU_PAGE = 7_560_000 * 10_692_000  # A4 in EMU²


def _in(el: Any, tag: str, stop: Any = None) -> bool:
    for anc in el.iterancestors():
        if anc is stop:
            return False
        if anc.tag == tag:
            return True
    return False


def _run_hidden(r: Any) -> bool:
    rpr = r.find(W + "rPr")
    if rpr is None:
        return False
    vanish = rpr.find(W + "vanish")
    if vanish is not None and vanish.get(W + "val", "true") not in ("0", "false"):
        return True
    sz = rpr.find(W + "sz")
    if sz is not None and (sz.get(W + "val") or "").isdigit() and int(sz.get(W + "val")) < 6:
        return True  # < 3pt
    color = rpr.find(W + "color")
    shd = rpr.find(W + "shd")
    if color is not None and (color.get(W + "val") or "").upper() in ("FFFFFF", "FEFEFE"):
        fill = (shd.get(W + "fill") or "").upper() if shd is not None else ""
        return fill in ("", "AUTO", "FFFFFF")
    return False


def _paragraph(p: Any, hidden: list[str]) -> str:
    vis: list[str] = []
    hid: list[str] = []
    for r in p.iter(W + "r"):
        if _in(r, W + "txbxContent", stop=p):
            continue
        t = "".join(
            (x.text or "") if x.tag == W + "t" else ("\t" if x.tag == W + "tab" else "") for x in r
        )
        (hid if _run_hidden(r) else vis).append(t)
    if "".join(hid).strip():
        hidden.append("".join(hid).strip())
    return "".join(vis).strip()


def _block_text(el: Any, hidden: list[str]) -> str:
    return "\n".join(
        t
        for p in el.iter(W + "p")
        if not _in(p, W + "txbxContent", stop=el) and (t := _paragraph(p, hidden))
    )


def _table_text(tbl: Any, hidden: list[str]) -> str:
    rows = []
    for tr in tbl.iter(W + "tr"):
        cells = [_block_text(tc, hidden) for tc in tr.findall(W + "tc")]
        if any(cells):
            rows.append(" | ".join(c.replace("\n", " ") for c in cells))
    return "\n".join(rows)


def _text_boxes(body: Any) -> Iterator[Any]:
    for tb in body.iter(W + "txbxContent"):
        if not _in(tb, MC + "Fallback"):  # VML fallback duplicates the DrawingML box
            yield tb


def _body_blocks(container: Any) -> Iterator[Any]:
    """Body paragraphs and tables in reading order, looking inside content controls (w:sdt)."""
    for child in container.iterchildren():
        if child.tag in (W + "p", W + "tbl"):
            yield child
        elif child.tag == W + "sdt":
            content = child.find(W + "sdtContent")
            if content is not None:
                yield from _body_blocks(content)


def _docx_pages(data: bytes) -> int | None:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            app = zf.read("docProps/app.xml").decode("utf-8", "ignore")
    except (KeyError, zipfile.BadZipFile):
        return None
    m = re.search(r"<Pages>(\d+)</Pages>", app)
    return int(m.group(1)) if m else None


def _extract_docx(data: bytes) -> ExtractedDocument:
    import docx

    try:
        document = docx.Document(io.BytesIO(data))
    except Exception as exc:
        raise UploadRejectedError("corrupt", f"DOCX cannot be opened: {exc}") from exc
    body = document.element.body
    sig = LayoutSignals(page_count=_docx_pages(data))
    hidden: list[str] = []
    parts: list[str] = []

    for child in _body_blocks(body):
        if child.tag == W + "p":
            if child.find(f"{W}pPr/{W}numPr") is not None:
                sig.list_paragraphs += 1
            if t := _paragraph(child, hidden):
                parts.append(t)
        elif child.tag == W + "tbl":
            sig.tables += 1
            t = _table_text(child, hidden)
            sig.table_chars += len(t)
            if t:
                parts.append(t)

    for tb in _text_boxes(body):
        sig.text_boxes += 1
        t = _block_text(tb, hidden)
        sig.text_box_chars += len(t)
        if t:
            parts.append(t)

    for cols in body.iter(W + "cols"):
        num = cols.get(W + "num")
        if num and num.isdigit() and int(num) > 1:
            sig.multi_column_pages = [1]

    for section in document.sections:
        for hf in (
            section.header,
            section.footer,
            section.first_page_header,
            section.first_page_footer,
        ):
            if hf.is_linked_to_previous:
                continue
            t = _block_text(hf._element, [])
            if t and t not in sig.header_footer_text:
                sig.header_footer_text.append(t[:300])

    blips = list(body.iter(A + "blip"))
    sig.images = len(blips)
    biggest = 0
    for ext in body.iter(WP + "extent"):
        try:
            biggest = max(biggest, int(ext.get("cx", 0)) * int(ext.get("cy", 0)))
        except ValueError:
            continue
    sig.image_area_ratio = round(min(1.0, biggest / EMU_PAGE), 3) if blips else 0.0

    fonts: set[str] = set()
    icon_fonts: set[str] = set()
    for root in (body, document.styles.element):
        for rf in root.iter(W + "rFonts"):
            for attr in ("ascii", "hAnsi"):
                if name := rf.get(W + attr):
                    fam = font_family(name)
                    (icon_fonts if ICON_FONT.search(fam) else fonts).add(fam)
    sig.fonts = sorted(fonts)
    sig.icon_fonts = sorted(icon_fonts)
    sig.links = [
        str(rel.target_ref)[:300]
        for rel in document.part.rels.values()
        if rel.is_external
        and "hyperlink" in rel.reltype
        and str(rel.target_ref).startswith(("http://", "https://", "mailto:"))
    ][:MAX_LINKS]

    text = "\n".join(parts)
    hidden = [h for h in hidden if h]
    sig.hidden_chars = sum(len(h) for h in hidden)
    _common_text_signals(text, sig)
    return ExtractedDocument(kind=FileKind.DOCX, text=text, hidden=hidden, signals=sig)
