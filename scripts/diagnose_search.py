"""Read-only MongoDB/RAG search diagnostics.

Run with: python -m scripts.diagnose_search
"""
import json
from typing import Any

from app.config.settings import get_settings
from app.vectorstore.mongo_client import CHUNKS, DOCUMENTS, get_db


def _sample_field_names(chunks) -> list[str]:
    sample = next(
        chunks.aggregate(
            [
                {"$limit": 1},
                {
                    "$project": {
                        "_id": 0,
                        "field_names": {
                            "$map": {
                                "input": {"$objectToArray": "$$ROOT"},
                                "as": "field",
                                "in": "$$field.k",
                            }
                        },
                    }
                },
            ]
        ),
        {},
    )
    return sorted(sample.get("field_names", []))


def _format_atlas_indexes(indexes: list[dict[str, Any]], expected_name: str) -> dict[str, Any]:
    formatted = []
    for index in indexes:
        definition = index.get("latestDefinition") or index.get("definition") or {}
        mappings = definition.get("mappings", {})
        fields = mappings.get("fields", {})
        if not fields:
            fields = {
                field.get("path"): field
                for field in definition.get("fields", [])
                if field.get("path")
            }
        formatted.append(
            {
                "name": index.get("name"),
                "status": index.get("status"),
                "queryable": index.get("queryable"),
                "mapping_fields": {
                    name: value.get("type")
                    for name, value in fields.items()
                    if isinstance(value, dict)
                },
            }
        )

    expected = next((index for index in formatted if index["name"] == expected_name), None)
    if expected is None:
        expected = {
            "name": expected_name,
            "status": "missing",
            "queryable": False,
            "mapping_fields": {},
        }
    expected["includes_file_name"] = "file_name" in expected["mapping_fields"]
    return {"available": True, "expected_index": expected, "indexes": formatted}


def collect_diagnostics(db, expected_index_name: str) -> dict[str, Any]:
    collections = set(db.list_collection_names())
    report: dict[str, Any] = {
        "database": db.name,
        "collections": {
            CHUNKS: {"exists": CHUNKS in collections},
            DOCUMENTS: {"exists": DOCUMENTS in collections},
        },
    }

    if CHUNKS in collections:
        chunks = db[CHUNKS]
        report["chunks"] = {
            "total": chunks.count_documents({}),
            "approved": chunks.count_documents({"approved": True}),
            "unapproved": chunks.count_documents({"approved": False}),
            "approval_missing": chunks.count_documents({"approved": {"$exists": False}}),
            "sample_field_names": _sample_field_names(chunks),
            "normal_indexes": [
                {
                    "name": index.get("name"),
                    "keys": index.get("weights") or list(index.get("key", {}).items()),
                }
                for index in chunks.list_indexes()
            ],
        }
        try:
            atlas_indexes = list(chunks.list_search_indexes())
        except Exception as exc:
            report["atlas_search_indexes"] = {
                "available": False,
                "expected_index_name": expected_index_name,
                "error_type": type(exc).__name__,
            }
        else:
            report["atlas_search_indexes"] = _format_atlas_indexes(
                atlas_indexes, expected_index_name
            )

    if DOCUMENTS in collections:
        report["documents_total"] = db[DOCUMENTS].count_documents({})

    return report


def main() -> int:
    try:
        settings = get_settings()
        db = get_db(settings)
        db.command("ping")
        report = collect_diagnostics(db, settings.rag_text_search_index_name)
    except Exception as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__}))
        return 1

    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
