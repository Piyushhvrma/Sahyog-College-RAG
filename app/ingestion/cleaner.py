"""Text cleaning. Conservative: fixes extraction artefacts, never rewrites content."""
import re
import unicodedata
from collections import Counter

from app.ingestion.loader import Page

_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_HYPHEN_BREAK = re.compile(r"(\w)-\n(\w)")
_MULTI_SPACE = re.compile(r"[ \t\u00a0]+")
_MULTI_NL = re.compile(r"\n{3,}")
_PAGE_NUM_LINE = re.compile(r"^\s*(page\s*)?\d{1,4}(\s*(of|/)\s*\d{1,4})?\s*$", re.I)


def clean_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _CTRL.sub("", text)
    text = _HYPHEN_BREAK.sub(r"\1\2", text)
    lines = [_MULTI_SPACE.sub(" ", ln).strip() for ln in text.split("\n")]
    lines = [ln for ln in lines if not _PAGE_NUM_LINE.match(ln)]
    text = "\n".join(lines)
    return _MULTI_NL.sub("\n\n", text).strip()


def remove_repeated_lines(pages: list[Page], min_pages: int = 4, ratio: float = 0.6) -> list[Page]:
    """Drop running headers/footers: short lines appearing on >= `ratio` of pages."""
    if len(pages) < min_pages:
        return pages
    counts: Counter[str] = Counter()
    for p in pages:
        for ln in {l.strip() for l in p.text.split("\n") if l.strip()}:
            if len(ln) <= 120:
                counts[ln] += 1
    threshold = max(3, int(len(pages) * ratio))
    boiler = {ln for ln, c in counts.items() if c >= threshold}
    if not boiler:
        return pages
    out = []
    for p in pages:
        kept = "\n".join(l for l in p.text.split("\n") if l.strip() not in boiler)
        out.append(Page(text=kept, page_number=p.page_number, label=p.label, ocr=p.ocr))
    return out


def clean_pages(pages: list[Page]) -> list[Page]:
    cleaned = [Page(text=clean_text(p.text), page_number=p.page_number, label=p.label, ocr=p.ocr)
               for p in pages]
    cleaned = remove_repeated_lines(cleaned)
    return [p for p in cleaned if p.text.strip()]
