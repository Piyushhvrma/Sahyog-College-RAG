"""Keyword search: Atlas Search ($search) when its index exists, else a standard $text index."""
import logging
import re
from typing import Any

from pymongo.database import Database
from pymongo.errors import OperationFailure

from app.vectorstore.index_manager import TEXT_INDEX_NAME
from app.vectorstore.mongo_client import CHUNKS

log = logging.getLogger(__name__)

SEARCH_FIELDS = ["text", "file_name", "document_name", "heading", "subject"]

# Conversational filler (English + Hinglish). Atlas Search uses matchCriteria "all" for
# precision, so a word like "batao" that never appears in a document would otherwise make
# "Data Structures ke important topics batao" return zero hits.
FILLER_TERMS = frozenset({
    # Hinglish
    "ke", "ki", "ka", "ko", "se", "mein", "mai", "hai", "hain", "ho", "kya", "kaun",
    "kaunse", "konse", "kon", "batao", "bata", "btao", "bataiye", "bataye", "bataao",
    "samjhao", "samjha", "samjhaiye", "chahiye", "chaiye", "dedo", "de", "dijiye", "do",
    "karo", "kar", "kr", "aur", "bhi", "wala", "wale", "wali", "ye", "yeh", "woh", "vo",
    "mujhe", "muje", "hume", "humko", "zara", "thoda", "sab", "saare", "sare",
    # English
    "please", "pls", "plz", "tell", "about", "what", "which", "are", "is", "the", "an",
    "of", "for", "give", "show", "list", "explain", "important", "imp", "topic", "topics",
    "some", "all", "can", "could", "you", "need", "want", "on", "to", "and", "my", "us",
})


def keyword_query(query: str) -> str:
    """Drop conversational filler so all-terms matching keys on subject/content words.

    Returns the original query if nothing meaningful would remain."""
    terms = re.findall(r"[\w]+", query, flags=re.UNICODE)
    kept = [term for term in terms if term.casefold() not in FILLER_TERMS]
    return " ".join(kept) if kept else query


def _normalized_phrase(value: str) -> str:
    return " ".join(re.findall(r"[\w]+", value.casefold(), flags=re.UNICODE))


def _text_search_phrase(query: str) -> str:
    terms = re.findall(r"[\w]+", query, flags=re.UNICODE)
    phrase = " ".join(terms)
    if len(terms) > 1:
        return f'"{phrase}"'
    return phrase or query


def _atlas_search(db: Database, query: str, limit: int, filters: dict[str, Any],
                  index_name: str = TEXT_INDEX_NAME) -> list[dict]:
    pipeline: list[dict] = [
        {"$search": {
            "index": index_name,
            "compound": {
                "must": [{
                    "text": {
                        "query": query,
                        "path": SEARCH_FIELDS,
                        "matchCriteria": "all",
                        "score": {"boost": {"value": 1}},
                    }
                }],
                "should": [
                    {
                        "phrase": {
                            "query": query,
                            "path": "file_name",
                            "score": {"boost": {"value": 20}},
                        }
                    },
                    {
                        "phrase": {
                            "query": query,
                            "path": "document_name",
                            "score": {"boost": {"value": 16}},
                        }
                    },
                    {
                        "phrase": {
                            "query": query,
                            "path": "text",
                            "score": {"boost": {"value": 5}},
                        }
                    },
                    {
                        "phrase": {
                            "query": query,
                            "path": "heading",
                            "score": {"boost": {"value": 4}},
                        }
                    },
                ],
                "filter": [{"equals": {"path": "approved", "value": True}}],
            },
        }},
        {"$limit": limit * 3},
        {"$addFields": {"_kscore": {"$meta": "searchScore"}}},
    ]
    post = {k: v for k, v in filters.items() if k != "approved"}
    if post:
        pipeline.append({"$match": post})
    pipeline += [{"$limit": limit}, {"$project": {"embedding": 0}}]
    return list(db[CHUNKS].aggregate(pipeline))


def _text_fallback(db: Database, query: str, limit: int, filters: dict[str, Any]) -> list[dict]:
    q = {"$text": {"$search": _text_search_phrase(query)}, **filters}
    cur = (db[CHUNKS].find(q, {"embedding": 0, "_kscore": {"$meta": "textScore"}})
           .sort([("_kscore", {"$meta": "textScore"})]).limit(limit))
    results = list(cur)
    phrase = _normalized_phrase(query)
    if phrase:
        results.sort(
            key=lambda result: phrase in _normalized_phrase(
                result.get("file_name") or result.get("document_name") or ""
            ),
            reverse=True,
        )
    return results


def keyword_search(db: Database, query: str, limit: int = 20,
                   filters: dict[str, Any] | None = None,
                   index_name: str = TEXT_INDEX_NAME) -> tuple[list[dict], str]:
    """Returns (results, engine_used)."""
    filters = dict(filters or {})
    filters["approved"] = True
    query = keyword_query(query)
    atlas_failed = False
    try:
        atlas_results = _atlas_search(db, query, limit, filters, index_name=index_name)
    except OperationFailure as exc:
        atlas_failed = True
        log.warning(
            "Atlas Search failed (%s); trying the approved-only MongoDB text index.",
            type(exc).__name__,
        )
    else:
        if atlas_results:
            return atlas_results, "atlas_search"
        log.info(
            "Atlas Search returned zero hits; trying the approved-only MongoDB text index."
        )

    try:
        text_results = _text_fallback(db, query, limit, filters)
    except OperationFailure as exc:
        log.error(
            "MongoDB text fallback failed (%s); verify the text_fallback index.",
            type(exc).__name__,
        )
        raise

    if atlas_failed:
        log.warning("MongoDB text fallback returned %d hits.", len(text_results))
    elif text_results:
        log.warning(
            "Atlas Search returned zero hits but MongoDB text fallback found %d; "
            "the Atlas Search index may be stale or missing field mappings.",
            len(text_results),
        )
    else:
        log.info("Both Atlas Search and MongoDB text fallback returned zero hits.")
    return text_results, "text_index"
