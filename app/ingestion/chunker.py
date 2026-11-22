"""Heading- and page-aware chunker.

Token counts are *estimated* (≈ 4 characters per token for English). This avoids a tokenizer
download and is accurate enough for 800/100 sizing. Chunks never cross page/slide boundaries so
every chunk keeps a trustworthy page number.
"""
import re
from dataclasses import dataclass
from typing import Optional

from app.ingestion.loader import Page

CHARS_PER_TOKEN = 4
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def estimate_tokens(text: str) -> int:
    return max(1, (len(text) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN) if text else 0


@dataclass
class Chunk:
    text: str
    page_number: Optional[int]
    heading: Optional[str]
    label: str
    chunk_index: int = 0


def _is_heading(line: str) -> bool:
    s = line.strip()
    if not s or len(s) > 100:
        return False
    if s.startswith("#"):
        return True
    if re.match(r"^(unit|module|chapter|section|part)\s+[\divxlc]+\b", s, re.I):
        return True
    if re.match(r"^\d+(\.\d+)*[.)]?\s+[A-Z][^.!?]{2,}$", s):
        return True
    letters = [c for c in s if c.isalpha()]
    return len(letters) >= 4 and s.upper() == s and len(s.split()) <= 10


def _split_long(unit: str, max_tokens: int) -> list[str]:
    """Split an oversized paragraph by sentence, then hard-wrap by characters."""
    if estimate_tokens(unit) <= max_tokens:
        return [unit]
    out: list[str] = []
    cur = ""
    for sent in _SENTENCE.split(unit):
        if estimate_tokens(sent) > max_tokens:
            if cur:
                out.append(cur)
                cur = ""
            step = max_tokens * CHARS_PER_TOKEN
            out.extend(sent[i:i + step] for i in range(0, len(sent), step))
            continue
        if estimate_tokens(cur + " " + sent) > max_tokens and cur:
            out.append(cur)
            cur = sent
        else:
            cur = f"{cur} {sent}".strip()
    if cur:
        out.append(cur)
    return out


def chunk_page(page: Page, target_tokens: int = 800, overlap_tokens: int = 100) -> list[Chunk]:
    if overlap_tokens >= target_tokens:
        raise ValueError("overlap must be smaller than target")
    # Break into units (lines), tracking the current heading for each unit.
    units: list[tuple[str, Optional[str]]] = []
    heading: Optional[str] = None
    for raw in page.text.split("\n"):
        line = raw.strip()
        if not line:
            continue
        if _is_heading(line):
            heading = line.lstrip("# ").strip()
        for piece in _split_long(line, target_tokens):
            units.append((piece, heading))

    chunks: list[Chunk] = []
    cur: list[tuple[str, Optional[str]]] = []
    cur_tokens = 0

    def flush() -> None:
        if not cur:
            return
        text = "\n".join(u for u, _ in cur).strip()
        if text:
            chunks.append(Chunk(text=text, page_number=page.page_number,
                                heading=cur[0][1], label=page.label))

    for unit, h in units:
        t = estimate_tokens(unit)
        if cur and cur_tokens + t > target_tokens:
            flush()
            # carry over tail units as overlap
            tail: list[tuple[str, Optional[str]]] = []
            tail_tokens = 0
            for u in reversed(cur):
                ut = estimate_tokens(u[0])
                if tail_tokens + ut > overlap_tokens:
                    break
                tail.insert(0, u)
                tail_tokens += ut
            cur, cur_tokens = tail, tail_tokens
        cur.append((unit, h))
        cur_tokens += t
    flush()
    # Drop a trailing chunk that is pure overlap of the previous one
    if len(chunks) >= 2 and chunks[-1].text in chunks[-2].text:
        chunks.pop()
    return chunks


def chunk_pages(pages: list[Page], target_tokens: int = 800, overlap_tokens: int = 100) -> list[Chunk]:
    out: list[Chunk] = []
    for p in pages:
        out.extend(chunk_page(p, target_tokens, overlap_tokens))
    for i, c in enumerate(out):
        c.chunk_index = i
    return out
