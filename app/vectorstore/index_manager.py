"""Atlas Vector Search / Atlas Search index definitions + best-effort programmatic creation.

Creating these in the Atlas UI is the most reliable route (see docs/MONGODB_SETUP.md);
`create_search_indexes` tries pymongo's SearchIndexModel and reports status.
"""
import logging

from pymongo.database import Database
from pymongo.operations import SearchIndexModel

from app.vectorstore.mongo_client import CHUNKS

log = logging.getLogger(__name__)
VECTOR_INDEX_NAME = "rag_vector_index"
TEXT_INDEX_NAME = "rag_text_index"
FILTER_FIELDS = ["approved", "branch", "semester", "subject", "academic_year", "doc_type"]


def vector_index_definition(dimensions: int = 768) -> dict:
    return {
        "fields": [
            {"type": "vector", "path": "embedding", "numDimensions": dimensions, "similarity": "cosine"},
            *[{"type": "filter", "path": f} for f in FILTER_FIELDS],
        ]
    }


def text_index_definition() -> dict:
    return {
        "mappings": {
            "dynamic": False,
            "fields": {
                "text": {"type": "string", "analyzer": "lucene.standard"},
                "file_name": {"type": "string", "analyzer": "lucene.standard"},
                "document_name": {"type": "string", "analyzer": "lucene.standard"},
                "heading": {"type": "string", "analyzer": "lucene.standard"},
                "subject": {"type": "string", "analyzer": "lucene.standard"},
                "approved": {"type": "boolean"},
            },
        }
    }


def list_search_indexes(db: Database) -> dict[str, dict]:
    try:
        return {i["name"]: i for i in db[CHUNKS].list_search_indexes()}
    except Exception as exc:  # non-Atlas deployment or insufficient privileges
        log.warning("Cannot list search indexes: %s", exc)
        return {}


def create_search_indexes(db: Database, dimensions: int = 768,
                          text_index_name: str = TEXT_INDEX_NAME) -> dict[str, str]:
    existing = list_search_indexes(db)
    report: dict[str, str] = {}
    wanted = [
        (VECTOR_INDEX_NAME, "vectorSearch", vector_index_definition(dimensions)),
        (text_index_name, "search", text_index_definition()),
    ]
    for name, typ, definition in wanted:
        if name in existing:
            if name == text_index_name:
                report[name] = (
                    "already exists; verify mapping includes file_name and subject "
                    "(existing index not modified)"
                )
            else:
                report[name] = "already exists"
            continue
        try:
            db[CHUNKS].create_search_index(SearchIndexModel(definition=definition, name=name, type=typ))
            report[name] = "creation requested (Atlas builds it in ~1 min)"
        except Exception as exc:
            report[name] = f"FAILED: {exc} -> create it manually in the Atlas UI"
    return report
