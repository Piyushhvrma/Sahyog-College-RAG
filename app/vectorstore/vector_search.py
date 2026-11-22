"""Atlas $vectorSearch. Always restricted to approved chunks."""
from typing import Any, Optional

from pymongo.database import Database

from app.vectorstore.index_manager import VECTOR_INDEX_NAME
from app.vectorstore.mongo_client import CHUNKS

PROJECTION = {"embedding": 0}


def build_filter(branch: Optional[str] = None, semester: Optional[int] = None,
                 subject: Optional[str] = None, academic_year: Optional[str] = None,
                 doc_type: Optional[str] = None) -> dict[str, Any]:
    f: dict[str, Any] = {"approved": True}
    for key, val in (("branch", branch), ("semester", semester), ("subject", subject),
                     ("academic_year", academic_year), ("doc_type", doc_type)):
        if val is not None:
            f[key] = val
    return f


def vector_search(db: Database, query_vector: list[float], limit: int = 20,
                  filters: dict[str, Any] | None = None) -> list[dict]:
    search_filter = dict(filters or {})
    search_filter["approved"] = True
    pipeline = [
        {"$vectorSearch": {
            "index": VECTOR_INDEX_NAME,
            "path": "embedding",
            "queryVector": query_vector,
            "numCandidates": min(max(limit * 15, 100), 1000),
            "limit": limit,
            "filter": search_filter,
        }},
        {"$addFields": {"_vscore": {"$meta": "vectorSearchScore"}}},
        {"$project": PROJECTION},
    ]
    return list(db[CHUNKS].aggregate(pipeline))
