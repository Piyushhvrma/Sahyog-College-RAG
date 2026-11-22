"""Keyword search: Atlas Search ($search) when its index exists, else a standard $text index."""
import logging
from typing import Any

from pymongo.database import Database
from pymongo.errors import OperationFailure

from app.vectorstore.index_manager import TEXT_INDEX_NAME
from app.vectorstore.mongo_client import CHUNKS

log = logging.getLogger(__name__)


def _atlas_search(db: Database, query: str, limit: int, filters: dict[str, Any]) -> list[dict]:
    pipeline: list[dict] = [
        {"$search": {
            "index": TEXT_INDEX_NAME,
            "compound": {
                "must": [{"text": {"query": query, "path": ["text", "document_name", "heading"],
                                   "score": {"boost": {"value": 1}}}}],
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
    q = {"$text": {"$search": query}, **filters}
    cur = (db[CHUNKS].find(q, {"embedding": 0, "_kscore": {"$meta": "textScore"}})
           .sort([("_kscore", {"$meta": "textScore"})]).limit(limit))
    return list(cur)


def keyword_search(db: Database, query: str, limit: int = 20,
                   filters: dict[str, Any] | None = None) -> tuple[list[dict], str]:
    """Returns (results, engine_used)."""
    filters = dict(filters or {})
    filters["approved"] = True
    try:
        res = _atlas_search(db, query, limit, filters)
        return res, "atlas_search"
    except OperationFailure as exc:
        log.info("Atlas Search unavailable (%s); using $text fallback.", str(exc)[:80])
    return _text_fallback(db, query, limit, filters), "text_index"
