import re

import mongomock
import pytest
from fastapi.testclient import TestClient
from pymongo import TEXT
from pymongo.errors import OperationFailure

import app.retrieval.hybrid_search as hybrid_search_module
import app.retrieval.keyword_search as keyword_search_module
from app.api import routes
from app.config.settings import Settings
from app.embeddings.gemini_embedder import EmbeddingQuotaError
from app.main import create_app
from app.vectorstore import index_manager, mongo_client
from app.vectorstore.mongo_client import CHUNKS, DOCUMENTS
from tests.conftest import SECRET


class NoQueryEmbedding:
    def embed_query(self, _query):
        raise AssertionError("keyword-only search must not call Gemini")


class AtlasFixtureCollection:
    def __init__(self, chunks):
        self.chunks = chunks

    def aggregate(self, pipeline):
        compound = pipeline[0]["$search"]["compound"]
        text_query = compound["must"][0]["text"]
        assert text_query["matchCriteria"] == "all"
        assert compound["filter"] == [{"equals": {"path": "approved", "value": True}}]
        assert any(
            clause.get("phrase", {}).get("path") == "file_name"
            and clause["phrase"]["score"]["boost"]["value"] > 1
            for clause in compound["should"]
        )

        terms = re.findall(r"[\w]+", text_query["query"].casefold())
        phrase = " ".join(terms)
        results = []
        for chunk in self.chunks:
            if chunk.get("approved") is not True:
                continue
            searchable = " ".join(
                str(chunk.get(field) or "")
                for field in ("text", "file_name", "document_name", "heading")
            ).casefold()
            if not all(term in searchable for term in terms):
                continue
            filename = re.sub(
                r"\s+", " ",
                " ".join(re.findall(
                    r"[\w]+",
                    str(chunk.get("file_name") or chunk.get("document_name") or "").casefold(),
                )),
            )
            text = " ".join(
                re.findall(
                    r"[\w]+",
                    " ".join(str(chunk.get(field) or "") for field in ("text", "heading")).casefold(),
                )
            )
            chunk = dict(chunk)
            chunk["_kscore"] = (
                (20 if phrase and phrase in filename else 0)
                + (5 if phrase and phrase in text else 0)
            )
            results.append(chunk)
        return sorted(results, key=lambda row: row["_kscore"], reverse=True)


class AtlasFixtureDatabase:
    def __init__(self, chunks):
        self.collection = AtlasFixtureCollection(chunks)

    def __getitem__(self, name):
        assert name == CHUNKS
        return self.collection


def _approved_text_index(db):
    db[CHUNKS].create_index(
        [("text", TEXT), ("file_name", TEXT), ("document_name", TEXT), ("heading", TEXT)],
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
            if all(
                term in (
                    document.get("text", "")
                    + " "
                    + document.get("file_name", "")
                    + " "
                    + document.get("document_name", "")
                    + " "
                    + document.get("heading", "")
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
                "file_name": "Applied Chemistry.pdf",
                "text": "Laboratory notes for first-year students.",
                "content_hash": "approved-hash",
                "approved": True,
            },
            {
                "_id": "pending",
                "document_id": "doc-pending",
                "file_name": "Pending Applied Chemistry.pdf",
                "text": "Unreviewed laboratory notes.",
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
        "chemistry",
        settings=Settings(_env_file=None, vector_search_enabled=False),
    )

    assert result["retrieval"]["fusion"] == "keyword_only"
    assert result["retrieval"]["vector_hits"] == 0
    assert result["retrieval"]["keyword_engine"] == "text_index"
    assert [item["document_name"] for item in result["results"]] == [
        "Applied Chemistry.pdf"
    ]


def test_atlas_keyword_search_includes_filename_and_approved_filter():
    class Collection:
        def aggregate(self, pipeline):
            assert pipeline[0]["$search"]["index"] == "rag_text_index_v2"
            search = pipeline[0]["$search"]["compound"]
            text_query = search["must"][0]["text"]
            assert text_query["path"] == ["text", "file_name", "document_name", "heading"]
            assert text_query["matchCriteria"] == "all"
            assert any(
                clause.get("phrase", {}).get("path") == "file_name"
                for clause in search["should"]
            )
            assert search["filter"] == [{"equals": {"path": "approved", "value": True}}]
            return [{"file_name": "Applied Chemistry.pdf", "approved": True}]

    class Database:
        def __getitem__(self, name):
            assert name == CHUNKS
            return Collection()

    results, engine = keyword_search_module.keyword_search(
        Database(),
        "Applied Chemistry",
        filters={"approved": False},
        index_name="rag_text_index_v2",
    )

    assert engine == "atlas_search"
    assert results == [{"file_name": "Applied Chemistry.pdf", "approved": True}]


@pytest.mark.parametrize(
    ("field", "query"),
    [
        ("text", "textmarker"),
        ("file_name", "filenamephrase"),
        ("document_name", "legacyphrase"),
        ("heading", "headingphrase"),
    ],
)
def test_text_fallback_searches_supported_fields_and_only_approved_chunks(
    monkeypatch, field, query
):
    db = mongomock.MongoClient()["text-field-search"]
    _approved_text_index(db)
    rows = []
    for approved, chunk_id in ((True, "approved"), (False, "unapproved")):
        row = {
            "_id": chunk_id,
            "text": "ordinary body",
            "file_name": "ordinary.pdf",
            "document_name": "ordinary legacy name",
            "heading": "ordinary heading",
            "approved": approved,
        }
        row[field] = f"{query} source"
        rows.append(row)
    db[CHUNKS].insert_many(rows)
    _disable_atlas_search(monkeypatch)

    results, engine = keyword_search_module.keyword_search(
        db, query, filters={"approved": False}
    )

    assert engine == "text_index"
    assert [row["_id"] for row in results] == ["approved"]


def _precision_fixtures():
    return [
        {
            "_id": "chemistry",
            "document_id": "chemistry-doc",
            "file_name": "Complete Applied Chemistry-1.pdf",
            "text": "Chapter II discusses molecular structures and gas analysis.",
            "heading": "Laboratory structures",
            "approved": True,
        },
        {
            "_id": "maths",
            "document_id": "maths-doc",
            "file_name": "Maths II U4.pdf",
            "text": "Matrices, equations, and integrals.",
            "approved": True,
        },
        {
            "_id": "data-structures",
            "document_id": "data-doc",
            "file_name": "Data Structures Notes.pdf",
            "text": "Trees, graphs, and algorithms.",
            "approved": True,
        },
        {
            "_id": "applied-body",
            "document_id": "applied-body-doc",
            "file_name": "Chemistry Reference.pdf",
            "text": "Applied Chemistry laboratory reference.",
            "approved": True,
        },
        {
            "_id": "orsat-chemistry",
            "document_id": "orsat-doc",
            "file_name": "Complete Applied Chemistry-1.pdf",
            "text": "Operation of the Orsat Gas Analyzer.",
            "page_number": 168,
            "approved": True,
        },
        {
            "_id": "orsat-ac",
            "document_id": "ac-doc",
            "file_name": "AC U2.pdf",
            "text": "Orsat Gas Analyzer experiment.",
            "page_number": 1,
            "approved": True,
        },
        {
            "_id": "unapproved-maths",
            "document_id": "unapproved-maths-doc",
            "file_name": "Maths II pending.pdf",
            "text": "Maths II notes.",
            "approved": False,
        },
    ]


@pytest.mark.parametrize(
    ("query", "expected_ids"),
    [
        ("Maths II", ["maths"]),
        ("Data Structures", ["data-structures"]),
        ("Applied Chemistry", ["chemistry", "orsat-chemistry", "applied-body"]),
        ("Orsat Gas Analyzer", ["orsat-chemistry", "orsat-ac"]),
    ],
)
def test_atlas_multiword_precision_and_approved_results(query, expected_ids):
    results, engine = keyword_search_module.keyword_search(
        AtlasFixtureDatabase(_precision_fixtures()),
        query,
        filters={"approved": False},
    )

    assert engine == "atlas_search"
    assert [row["_id"] for row in results] == expected_ids


def test_atlas_prioritizes_exact_filename_phrase():
    results, _engine = keyword_search_module.keyword_search(
        AtlasFixtureDatabase(_precision_fixtures()),
        "Applied Chemistry",
    )

    assert results[0]["_id"] == "chemistry"


def test_text_fallback_uses_phrase_query_and_prioritizes_filename_match():
    class Cursor:
        def sort(self, _sort):
            return self

        def limit(self, _limit):
            return self

        def __iter__(self):
            return iter(
                [
                    {"_id": "body", "file_name": "Other Notes.pdf"},
                    {"_id": "filename", "file_name": "Applied Chemistry.pdf"},
                ]
            )

    class Collection:
        def find(self, query, _projection):
            assert query["$text"]["$search"] == '"Applied Chemistry"'
            assert query["approved"] is True
            return Cursor()

    class Database:
        def __getitem__(self, name):
            assert name == CHUNKS
            return Collection()

    results = keyword_search_module._text_fallback(
        Database(), "Applied Chemistry", 5, {"approved": True}
    )

    assert [row["_id"] for row in results] == ["filename", "body"]


def test_atlas_zero_hits_fall_back_to_approved_text_search(monkeypatch, caplog):
    db = mongomock.MongoClient()["atlas-zero-hits"]
    monkeypatch.setattr(keyword_search_module, "_atlas_search", lambda *_args, **_kwargs: [])

    def fallback(_db, _query, _limit, filters):
        assert filters["approved"] is True
        return [{"_id": "approved", "approved": True}]

    monkeypatch.setattr(keyword_search_module, "_text_fallback", fallback)

    results, engine = keyword_search_module.keyword_search(
        db, "Applied Chemistry", filters={"approved": False}
    )

    assert engine == "text_index"
    assert results == [{"_id": "approved", "approved": True}]
    assert "Atlas Search returned zero hits" in caplog.text


def test_hybrid_search_uses_configured_atlas_index_without_vector_search(monkeypatch):
    observed = {}

    def search(_db, _query, limit, filters, index_name):
        observed["index_name"] = index_name
        assert filters["approved"] is True
        return (
            [{
                "_id": "chemistry",
                "document_id": "chemistry-doc",
                "file_name": "Applied Chemistry.pdf",
                "text": "Laboratory notes",
                "_rrf": 1.0,
            }],
            "atlas_search",
        )

    monkeypatch.setattr(hybrid_search_module, "keyword_search", search)
    result = hybrid_search_module.hybrid_search(
        mongomock.MongoClient()["configured-index"],
        NoQueryEmbedding(),
        "Applied Chemistry",
        settings=Settings(
            _env_file=None,
            vector_search_enabled=False,
            rag_text_search_index_name="rag_text_index_v2",
        ),
    )

    assert observed["index_name"] == "rag_text_index_v2"
    assert result["retrieval"]["vector_hits"] == 0
    assert result["retrieval"]["fusion"] == "keyword_only"
    assert result["results"][0]["document_name"] == "Applied Chemistry.pdf"


def test_text_index_definition_includes_current_and_legacy_filename_fields():
    fields = index_manager.text_index_definition()["mappings"]["fields"]

    assert fields["file_name"]["type"] == "string"
    assert fields["document_name"]["type"] == "string"
    assert fields["heading"]["type"] == "string"


def test_existing_atlas_indexes_are_not_replaced_for_filename_migration(monkeypatch):
    monkeypatch.setattr(
        index_manager,
        "list_search_indexes",
        lambda _db: {
            index_manager.VECTOR_INDEX_NAME: {},
            index_manager.TEXT_INDEX_NAME: {},
        },
    )

    class Database:
        def __getitem__(self, _name):
            raise AssertionError("existing Atlas indexes must not be modified")

    report = index_manager.create_search_indexes(Database())

    assert "not modified" in report[index_manager.TEXT_INDEX_NAME]
    assert "file_name" in report[index_manager.TEXT_INDEX_NAME]


def test_init_collections_preserves_legacy_text_fallback_index(caplog):
    db = mongomock.MongoClient()["legacy-text-index"]
    db[CHUNKS].create_index(
        [("text", TEXT), ("document_name", TEXT)],
        name="text_fallback",
        default_language="none",
    )

    mongo_client.init_collections(db)

    key = db[CHUNKS].index_information()["text_fallback"]["key"]
    assert key == [("text", TEXT), ("document_name", TEXT)]
    assert "leaving it unchanged" in caplog.text


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
