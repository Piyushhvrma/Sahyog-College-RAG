"""Derive metadata from folder path + file name. Only returns values actually found; never guesses.

Recommended Drive layout (any depth, case-insensitive):
    <Branch>/Semester 5/<Subject>/PYQ/2023-24/file.pdf
"""
import hashlib
import re
from typing import Any, Optional

BRANCHES = {
    "cse": "CSE", "computer science": "CSE", "cs": "CSE",
    "it": "IT", "information technology": "IT",
    "ece": "ECE", "electronics": "ECE", "electronics and communication": "ECE",
    "ee": "EE", "electrical": "EE", "eee": "EE",
    "me": "ME", "mechanical": "ME",
    "ce": "CE", "civil": "CE",
    "che": "CHE", "chemical": "CHE",
    "mme": "MME", "metallurgical": "MME",
    "bme": "BME", "biomedical": "BME",
    "bt": "BT", "biotechnology": "BT",
    "mining": "MINING", "mn": "MINING",
    "arch": "ARCH", "architecture": "ARCH",
    "ai": "AI", "aiml": "AI", "ai&ds": "AI",
}

DOC_TYPE_KEYWORDS = [
    ("pyq", ["pyq", "pyqs", "previous year", "question paper", "end sem", "mid sem", "endsem", "midsem"]),
    ("assignment", ["assignment", "homework", "problem set"]),
    ("lab", ["lab", "laboratory", "practical"]),
    ("syllabus", ["syllabus", "curriculum", "scheme"]),
    ("timetable", ["timetable", "time table", "schedule"]),
    ("presentation", ["presentation", "slides", "ppt"]),
    ("placement", ["placement", "placements", "internship", "recruit", "campus drive", "ctc"]),
    ("attendance", ["attendance"]),
    ("notice", ["notice", "notices", "circular", "notification", "office order"]),
    ("notes", ["notes", "lecture", "handout", "slides"]),
    ("regulation", ["ordinance", "regulation", "rules", "handbook", "academic"]),
]
SUBJECT_ALIASES = {
    "Operating Systems": ("operating systems", "operating system", "os"),
    "Database Management Systems": (
        "database management systems",
        "database management system",
        "dbms",
    ),
    "Computer Networks": ("computer networks", "computer network", "cn"),
    "Data Structures": ("data structures", "data structure", "ds"),
    "Design and Analysis of Algorithms": (
        "design and analysis of algorithms",
        "daa",
    ),
    "Object-Oriented Programming": (
        "object-oriented programming",
        "object oriented programming",
        "oops",
        "oop",
    ),
    "Computer Organization and Architecture": (
        "computer organization and architecture",
        "coa",
    ),
    "Theory of Computation": ("theory of computation", "toc"),
    "Discrete Mathematics": ("discrete mathematics",),
    "Software Engineering": ("software engineering",),
    "Compiler Design": ("compiler design",),
    "Computer Graphics": ("computer graphics",),
    "Artificial Intelligence": ("artificial intelligence",),
    "Machine Learning": ("machine learning",),
    "Cloud Computing": ("cloud computing",),
}
GENERIC_SEGMENTS = {"pyq", "pyqs", "notes", "syllabus", "notices", "notice", "timetable", "placement",
                    "placements", "attendance", "misc", "others", "assignment", "assignments",
                    "lab", "labs", "laboratory", "presentation", "presentations", "books", "papers",
                    "subject-wise distribution", "subject wise distribution"}

_SEM = re.compile(r"\b(?:sem(?:ester)?[\s\-_]*([1-9]|10)|([1-9]|10)(?:st|nd|rd|th)?[\s\-_]*sem(?:ester)?)\b", re.I)
_YEAR = re.compile(r"\b(20\d{2})\s*[-_/]\s*(\d{2}|20\d{2})\b")
_SINGLE_YEAR = re.compile(r"\b(20\d{2})\b")
_STUDY_YEAR = re.compile(
    r"\b(1st|2nd|3rd|4th|first|second|third|fourth)\s*(?:year|yr)\b",
    re.I,
)
_UNIT = re.compile(r"\bunit\s*(?:no\.?\s*)?([0-9]+|[ivxlcdm]+)\b", re.I)
_CHAPTER = re.compile(r"\bchapter\s*(?:no\.?\s*)?([0-9]+|[ivxlcdm]+)\b", re.I)
_COURSE_CODE = re.compile(r"\b([A-Z]{2,4}[\s\-]?\d{3,4}[A-Z]?)\b")
_STUDY_YEAR_NUMBERS = {
    "1st": 1, "first": 1,
    "2nd": 2, "second": 2,
    "3rd": 3, "third": 3,
    "4th": 4, "fourth": 4,
}


def _norm_year(a: str, b: str) -> str:
    return f"{a}-{b[-2:]}"


def extract_semester(*texts: str) -> Optional[int]:
    for t in texts:
        m = _SEM.search(t)
        if m:
            return int(m.group(1) or m.group(2))
    return None


def extract_academic_year(*texts: str) -> Optional[str]:
    for t in texts:
        m = _YEAR.search(t)
        if m:
            return _norm_year(m.group(1), m.group(2))
    for t in texts:
        m = _SINGLE_YEAR.search(t)
        if m:
            return m.group(1)
    return None


def extract_study_year(*texts: str) -> Optional[int]:
    for text in texts:
        match = _STUDY_YEAR.search(text)
        if match:
            return _STUDY_YEAR_NUMBERS[match.group(1).lower()]
    return None


def extract_branch(segments: list[str]) -> Optional[str]:
    for seg in segments:
        key = seg.strip().lower()
        if key in BRANCHES:
            return BRANCHES[key]
    for seg in segments:  # looser: segment contains a known long branch name
        low = seg.lower()
        for k, v in BRANCHES.items():
            if len(k) > 3 and k in low:
                return v
    return None


def extract_doc_type(folder_path: str, file_name: str, text: str = "") -> Optional[str]:
    hay = f"{folder_path} {file_name} {text}".lower()
    for dtype, words in DOC_TYPE_KEYWORDS:
        for word in words:
            pattern = re.escape(word).replace(" ", r"\s+")
            if re.search(rf"(?<![a-z0-9]){pattern}(?![a-z0-9])", hay):
                return dtype
    return None


def _known_subject(text: str) -> Optional[str]:
    normalized = re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()
    for subject, aliases in SUBJECT_ALIASES.items():
        for alias in aliases:
            alias_normalized = re.sub(r"[^a-z0-9]+", " ", alias.lower()).strip()
            if normalized == alias_normalized:
                return subject
    for subject, aliases in SUBJECT_ALIASES.items():
        for alias in aliases:
            alias_normalized = re.sub(r"[^a-z0-9]+", " ", alias.lower()).strip()
            if re.search(rf"(?<![a-z0-9]){re.escape(alias_normalized)}(?![a-z0-9])", normalized):
                return subject
    return None


def _heading_candidates(text: str) -> list[str]:
    candidates = []
    for index, line in enumerate(text.splitlines()[:100]):
        stripped = line.strip().lstrip("#").strip()
        if not stripped:
            continue
        is_explicit = (
            re.match(r"^(subject|course)\s*:", stripped, re.I)
            or re.match(r"^(unit|chapter)\s+", stripped, re.I)
            or line.lstrip().startswith("#")
        )
        is_short_title = (
            index < 5
            and len(stripped) <= 100
            and not re.search(r"[.!?]$", stripped)
        )
        if is_explicit or is_short_title:
            candidates.append(stripped)
    return candidates


def _topic_number(pattern: re.Pattern, *texts: str) -> Optional[str]:
    for text in texts:
        match = pattern.search(text)
        if match:
            return match.group(1).upper()
    return None


def extract_subject(
    segments: list[str], file_name: str = "", text: str = ""
) -> Optional[str]:
    """Find the first meaningful subject folder after a semester folder."""
    for segment in segments:
        subject = _known_subject(segment)
        if subject:
            return subject

    stem = re.sub(r"\.[A-Za-z0-9]{2,5}$", "", file_name)
    for candidate in (stem, *_heading_candidates(text)):
        subject = _known_subject(candidate)
        if subject:
            return subject

    for i, seg in enumerate(segments):
        if _SEM.search(seg):
            for candidate in segments[i + 1:]:
                nxt = candidate.strip()
                if (
                    not nxt
                    or nxt.lower() in GENERIC_SEGMENTS
                    or _YEAR.search(nxt)
                    or _STUDY_YEAR.search(nxt)
                    or _SEM.search(nxt)
                    or _UNIT.search(nxt)
                    or _CHAPTER.search(nxt)
                    or extract_branch([nxt]) is not None
                ):
                    continue
                return nxt
            break
    return None


def extract_metadata(
    folder_path: str, file_name: str, text: str = ""
) -> dict[str, Any]:
    segments = [s for s in folder_path.split("/") if s.strip()]
    stem = re.sub(r"\.[A-Za-z0-9]{2,5}$", "", file_name)
    headings = _heading_candidates(text[:20_000])
    metadata_text = " ".join(headings)
    topic_texts = [folder_path, stem, *headings]
    code = _COURSE_CODE.search(stem) or _COURSE_CODE.search(folder_path)
    return {
        "branch": extract_branch([*segments, stem, *headings]),
        "semester": extract_semester(*segments, stem, *headings),
        "subject": extract_subject(segments, file_name, text),
        "subject_code": code.group(1).replace(" ", "-").upper() if code else None,
        "academic_year": extract_academic_year(*segments, stem, *headings),
        "study_year": extract_study_year(*segments, stem, *headings),
        "unit": _topic_number(_UNIT, *topic_texts),
        "chapter": _topic_number(_CHAPTER, *topic_texts),
        "doc_type": extract_doc_type(folder_path, file_name, metadata_text),
    }


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
