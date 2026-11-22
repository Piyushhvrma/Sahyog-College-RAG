import mongomock
from fastapi.testclient import TestClient
from pymongo import TEXT
from pymongo.errors import OperationFailure

import app.retrieval.hybrid_search as hybrid_search_module
import app.retrieval.keyword_search as keyword_search_module
from app.api import routes
from app.config.settings import Settings
from app.embeddings.gemini_embedder import EmbeddingQuotaError
from app.main import create_app
from app.vectorstore.mongo_client import CHUNKS, DOCUMENTS
from tests.conftest import SECRET


class NoQueryEmbedding:
    def embed_query(self, _query):
        raise AssertionError("keyword-only search must not call Gemini")


def _approved_text_index(db):
    db[CHUNKS].create_index(
        [("text", TEXT), ("document_name", TEXT)],
        name="text_fallback",
        default_language="none",
    )


def _disable_atlas_search(monkeypatch):
    def unavailable(*_args, **_kwargs):
        raise OperationFailure("Atlas Search index unavailable")

    monkeypatch.setattr(keyword_search_module, "_atlas_search", unavailable)

    def mongomock_text_fallback(db, query, limit, filters):
        assert filters["approved"] is True
        terms = [term.casefold() for term in query.split() if len(term) > 2]
        documents = db[CHUNKS].find(filters, {"embedding": 0})
        return [
            document
            for document in documents
            if any(
                term in (
                    document.get("text", "")
                    + " "
                    + document.get("document_name", "")
                ).casefold()
                for term in terms
            )
        ][:limit]

    monkeypatch.setattr(
        keyword_search_module, "_text_fallback", mongomock_text_fallback
    )


def test_keyword_only_search_uses_text_fallback_and_approved_filter(monkeypatch):
    db = mongomock.MongoClient()["keyword-only"]
    _approved_text_index(db)
    db[CHUNKS].insert_many(
        [
            {
                "_id": "approved",
                "document_id": "doc-approved",
                "document_name": "Attendance Guide.pdf",
                "text": "Attendance requires seventy five percent.",
                "content_hash": "approved-hash",
                "approved": True,
            },
            {
                "_id": "pending",
                "document_id": "doc-pending",
                "document_name": "Pending Attendance.pdf",
                "text": "Attendance requires sixty percent.",
                "content_hash": "pending-hash",
                "approved": False,
            },
        ]
    )
    _disable_atlas_search(monkeypatch)

    def vector_search_must_not_run(*_args, **_kwargs):
        raise AssertionError("keyword-only search must not run vector search")

    monkeypatch.setattr(
        hybrid_search_module, "vector_search", vector_search_must_not_run
    )
    result = hybrid_search_module.hybrid_search(
        db,
        NoQueryEmbedding(),
        "attendance",
        settings=Settings(_env_file=None, vector_search_enabled=False),
    )

    assert result["retrieval"]["fusion"] == "keyword_only"
    assert result["retrieval"]["vector_hits"] == 0
    assert result["retrieval"]["keyword_engine"] == "text_index"
    assert [item["document_name"] for item in result["results"]] == [
        "Attendance Guide.pdf"
    ]


def test_search_without_approved_chunks_returns_clear_error_without_embedding():
    db = mongomock.MongoClient()["no-approved-chunks"]
    app = create_app()
    app.dependency_overrides[routes.get_database] = lambda: db
    app.dependency_overrides[routes.get_settings] = lambda: Settings(
        _env_file=None,
        rag_service_secret=SECRET,
        vector_search_enabled=False,
    )
    app.dependency_overrides[routes.get_embedder] = lambda: NoQueryEmbedding()

    response = TestClient(app).post(
        "/search",
        headers={"X-RAG-Secret": SECRET},
        json={"query": "attendance rules"},
    )

    assert response.status_code == 503
    assert "No approved knowledge chunks" in response.json()["detail"]


def test_daily_embedding_quota_maps_to_retry_response(monkeypatch):
    db = mongomock.MongoClient()["quota-error"]
    db[CHUNKS].insert_one({"_id": "approved", "approved": True})
    app = create_app()
    app.dependency_overrides[routes.get_database] = lambda: db
    app.dependency_overrides[routes.get_settings] = lambda: Settings(
        _env_file=None,
        rag_service_secret=SECRET,
        vector_search_enabled=True,
    )
    app.dependency_overrides[routes.get_embedder] = lambda: NoQueryEmbedding()

    def hit_quota(*_args, **_kwargs):
        raise EmbeddingQuotaError("daily quota", retry_after=7)

    monkeypatch.setattr(routes, "hybrid_search", hit_quota)
    response = TestClient(app).post(
        "/search",
        headers={"X-RAG-Secret": SECRET},
        json={"query": "attendance rules"},
    )

    assert response.status_code == 429
    assert response.headers["retry-after"] == "7"
    assert "daily embedding quota" in response.json()["detail"]


def test_admin_approval_enables_keyword_search_in_isolated_database(monkeypatch):
    db = mongomock.MongoClient()["approved-keyword-search"]
    _approved_text_index(db)
    db[DOCUMENTS].insert_one(
        {
            "file_id": "attendance-doc",
            "file_name": "Attendance Guide.pdf",
            "mime_type": "application/pdf",
            "status": "indexed",
            "approved": False,
        }
    )
    db[CHUNKS].insert_many(
        [
            {
                "_id": "attendance-approved-after-review",
                "document_id": "attendance-doc",
                "document_name": "Attendance Guide.pdf",
                "file_name": "Attendance Guide.pdf",
                "text": "Attendance requires seventy five percent.",
                "content_hash": "attendance-hash",
                "page_number": 2,
                "approved": False,
            },
            {
                "_id": "other-pending",
                "document_id": "other-doc",
                "document_name": "Other Pending.pdf",
                "file_name": "Other Pending.pdf",
                "text": "Attendance has a sixty percent requirement.",
                "content_hash": "other-hash",
                "approved": False,
            },
        ]
    )
    _disable_atlas_search(monkeypatch)

    app = create_app()
    app.dependency_overrides[routes.get_database] = lambda: db
    app.dependency_overrides[routes.get_settings] = lambda: Settings(
        _env_file=None,
        rag_service_secret=SECRET,
        vector_search_enabled=False,
    )
    app.dependency_overrides[routes.get_embedder] = lambda: NoQueryEmbedding()
    client = TestClient(app)
    headers = {"X-RAG-Secret": SECRET}

    approval = client.patch(
        "/documents/attendance-doc/approval",
        headers=headers,
        json={"approved": True},
    )
    search = client.post(
        "/search",
        headers=headers,
        json={"query": "attendance"},
    )

    assert approval.status_code == 200
    assert search.status_code == 200
    assert [item["document_name"] for item in search.json()["results"]] == [
        "Attendance Guide.pdf"
    ]
    assert search.json()["retrieval"]["fusion"] == "keyword_only"
