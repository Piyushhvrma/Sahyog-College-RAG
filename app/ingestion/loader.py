"""Extract text from local files into a list of Page segments.

A "page" is a PDF page, a PPTX slide, a CSV row-block, or (for DOCX/TXT, which have no
reliable page numbers) a single segment with page_number=None. We never invent page numbers.
Large PDFs are processed page-by-page so memory stays flat.
"""
import csv
import io
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

from app.config.settings import Settings, get_settings

log = logging.getLogger(__name__)

MIN_CHARS_BEFORE_OCR = 40     # a PDF page with fewer extracted chars is treated as scanned
CSV_ROWS_PER_BLOCK = 40


@dataclass
class Page:
    text: str
    page_number: Optional[int] = None   # PDF page / slide number (1-based) or None
    label: str = "page"                 # page | slide | rows | document
    ocr: bool = False


class ExtractionError(Exception):
    pass


def configure_tesseract(settings: Settings) -> bool:
    try:
        import pytesseract
    except ImportError:
        return False
    if settings.tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = settings.tesseract_cmd
    try:
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


def _ocr_page(fitz_page, settings: Settings) -> str:
    import pytesseract
    from PIL import Image
    pix = fitz_page.get_pixmap(dpi=200)
    img = Image.open(io.BytesIO(pix.tobytes("png")))
    return pytesseract.image_to_string(img)


def load_pdf(path: Path, settings: Settings) -> Iterator[Page]:
    import fitz  # PyMuPDF
    try:
        doc = fitz.open(str(path))
    except Exception as exc:
        raise ExtractionError(f"Cannot open PDF: {exc}") from exc
    if doc.needs_pass:
        doc.close()
        raise ExtractionError("PDF is password-protected")
    ocr_ready = settings.ocr_enabled and configure_tesseract(settings)
    ocr_warned = False
    try:
        for i, page in enumerate(doc, start=1):
            text = page.get_text("text") or ""
            used_ocr = False
            if len(text.strip()) < MIN_CHARS_BEFORE_OCR:
                if ocr_ready:
                    try:
                        text = _ocr_page(page, settings)
                        used_ocr = True
                    except Exception as exc:
                        log.warning("OCR failed on %s p%d: %s", path.name, i, exc)
                elif settings.ocr_enabled and not ocr_warned:
                    log.warning("Scanned pages found in %s but Tesseract is not available "
                                "(set TESSERACT_CMD). Those pages will be empty.", path.name)
                    ocr_warned = True
            if text.strip():
                yield Page(text=text, page_number=i, label="page", ocr=used_ocr)
    finally:
        doc.close()


def load_docx(path: Path, settings: Settings) -> Iterator[Page]:
    import docx
    try:
        d = docx.Document(str(path))
    except Exception as exc:
        raise ExtractionError(f"Cannot open DOCX: {exc}") from exc
    lines: list[str] = []
    for p in d.paragraphs:
        t = p.text.strip()
        if not t:
            continue
        style = (p.style.name or "") if p.style is not None else ""
        if style.lower().startswith("heading") or style.lower() == "title":
            lines.append(f"# {t}")
        else:
            lines.append(t)
    for table in d.tables:
        for row in table.rows:
            cells = [c.text.strip().replace("\n", " ") for c in row.cells]
            if any(cells):
                lines.append(" | ".join(cells))
    if lines:
        yield Page(text="\n".join(lines), page_number=None, label="document")


def load_pptx(path: Path, settings: Settings) -> Iterator[Page]:
    from pptx import Presentation
    try:
        prs = Presentation(str(path))
    except Exception as exc:
        raise ExtractionError(f"Cannot open PPTX: {exc}") from exc
    for i, slide in enumerate(prs.slides, start=1):
        parts: list[str] = []
        title_shape = slide.shapes.title
        if title_shape is not None and title_shape.has_text_frame and title_shape.text_frame.text.strip():
            parts.append(f"# {title_shape.text_frame.text.strip()}")
        for shape in slide.shapes:
            if shape is title_shape:
                continue
            if shape.has_text_frame:
                t = shape.text_frame.text.strip()
                if t:
                    parts.append(t)
            elif getattr(shape, "has_table", False) and shape.has_table:
                for row in shape.table.rows:
                    parts.append(" | ".join(c.text.strip() for c in row.cells))
        if slide.has_notes_slide and slide.notes_slide.notes_text_frame is not None:
            n = slide.notes_slide.notes_text_frame.text.strip()
            if n:
                parts.append(f"Notes: {n}")
        if parts:
            yield Page(text="\n".join(parts), page_number=i, label="slide")


def load_txt(path: Path, settings: Settings) -> Iterator[Page]:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-16", "cp1252", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except (UnicodeDecodeError, UnicodeError):
            continue
    else:
        raise ExtractionError("Cannot decode text file")
    if text.strip():
        yield Page(text=text, page_number=None, label="document")


def load_csv(path: Path, settings: Settings) -> Iterator[Page]:
    raw = path.read_bytes()
    text = None
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise ExtractionError("Cannot decode CSV")
    reader = csv.reader(io.StringIO(text))
    header: list[str] | None = None
    block: list[str] = []
    start_row = 1
    row_no = 0
    for row in reader:
        if not any(c.strip() for c in row):
            continue
        row_no += 1
        if header is None:
            header = [c.strip() for c in row]
            continue
        pairs = [f"{h}: {c.strip()}" for h, c in zip(header, row) if c.strip()]
        if pairs:
            block.append("; ".join(pairs))
        if len(block) >= CSV_ROWS_PER_BLOCK:
            yield Page(text="\n".join(block), page_number=None, label=f"rows {start_row}-{row_no - 1}")
            block, start_row = [], row_no
    if block:
        yield Page(text="\n".join(block), page_number=None, label=f"rows {start_row}-{row_no - 1}")


LOADERS = {
    "pdf": load_pdf, "docx": load_docx, "gdoc": load_docx,
    "pptx": load_pptx, "gslides": load_pptx,
    "txt": load_txt, "csv": load_csv, "gsheet": load_csv,
}


def load_document(path: Path, kind: str, settings: Settings | None = None) -> list[Page]:
    settings = settings or get_settings()
    loader = LOADERS.get(kind)
    if loader is None:
        raise ExtractionError(f"Unsupported kind: {kind}")
    pages = list(loader(path, settings))
    if not pages:
        raise ExtractionError("No extractable text (empty or unreadable document)")
    return pages
