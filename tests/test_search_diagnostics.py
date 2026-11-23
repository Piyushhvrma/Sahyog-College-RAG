import json

import mongomock

from app.vectorstore.mongo_client import CHUNKS, DOCUMENTS
from scripts.diagnose_search import _format_atlas_indexes, collect_diagnostics


def test_diagnostic_reports_counts_and_field_names_without_chunk_content():
    db = mongomock.MongoClient()["diagnostic"]
    db[CHUNKS].insert_many(
        [
            {
                "_id": "approved",
                "file_name": "Applied Chemistry.pdf",
                "text": "PRIVATE CHUNK CONTENT",
                "approved": True,
            },
            {
                "_id": "unapproved",
                "file_name": "Pending Chemistry.pdf",
                "text": "OTHER PRIVATE CONTENT",
                "approved": False,
            },
        ]
    )
    db[DOCUMENTS].insert_one({"file_id": "doc-1"})

    report = collect_diagnostics(db, "rag_text_index")

    assert report["database"] == "diagnostic"
    assert report["chunks"]["total"] == 2
    assert report["chunks"]["approved"] == 1
    assert report["chunks"]["unapproved"] == 1
    assert {"file_name", "text", "approved"} <= set(report["chunks"]["sample_field_names"])
    serialized = json.dumps(report)
    assert "PRIVATE CHUNK CONTENT" not in serialized
    assert "OTHER PRIVATE CONTENT" not in serialized


def test_diagnostic_reports_atlas_index_status_and_filename_mapping():
    report = _format_atlas_indexes(
        [
            {
                "name": "rag_text_index_v2",
                "status": "READY",
                "queryable": True,
                "latestDefinition": {
                    "mappings": {
                        "dynamic": False,
                        "fields": {
                            "file_name": {"type": "string"},
                            "approved": {"type": "boolean"},
                        },
                    }
                },
            }
        ],
        "rag_text_index_v2",
    )

    assert report["expected_index"]["status"] == "READY"
    assert report["expected_index"]["queryable"] is True
    assert report["expected_index"]["includes_file_name"] is True
