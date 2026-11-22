from datetime import datetime, timedelta, timezone

import mongomock
from fastapi.testclient import TestClient

from app.api import routes
from app.config.settings import Settings
from app.main import create_app
from app.vectorstore.mongo_client import CHUNKS, DOCUMENTS, SYNC_LOCKS
from tests.conftest import SECRET

H = {"X-RAG-Secret": SECRET}


def client():
    return TestClient(create_app())


def test_health_public_and_minimal():
    r = client().get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "service": "sahyog-college-rag"}


def test_health_secure_requires_secret():
    assert client().get("/health/secure").status_code == 401


def test_health_secure_reports_config_without_leaking():
    r = client().get("/health/secure", headers=H)
    assert r.status_code == 200
    body = r.json()
    assert body["mongo_connected"] is False          # no MONGO_URI in tests
    assert body["gemini_key_configured"] is False
    assert SECRET not in r.text


def test_internal_endpoints_all_protected():
    c = client()
    for method, path in [("get", "/documents"), ("get", "/stats"), ("post", "/search"),
                         ("patch", "/documents/x/approval"),
                         ("get", "/sync/status"), ("get", "/inaccessible"),
                         ("post", "/sync"), ("post", "/sync/retry")]:
        assert getattr(c, method)(path).status_code == 401, path


def test_sync_status_reports_external_schedule_and_last_run():
    app = create_app()
    db = mongomock.MongoClient()["sync-status"]
    db["rag_sync_logs"].insert_one({
        "started_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "finished_at": datetime(2026, 1, 1, 0, 5, tzinfo=timezone.utc),
        "status": "completed",
        "summary": {"discovered_count": 3},
    })
    app.dependency_overrides[routes.get_database] = lambda: db

    response = TestClient(app).get("/sync/status", headers=H)

    assert response.status_code == 200
    body = response.json()
    assert body["last_sync"]["summary"]["discovered_count"] == 3
    assert body["schedule"]["mode"] == "external_cron"


def test_sync_status_reports_lease_held_by_another_worker():
    app = create_app()
    db = mongomock.MongoClient()["sync-status-lease"]
    db[SYNC_LOCKS].insert_one({
        "_id": "drive-sync",
        "owner": "other-process",
        "lease_until": datetime.now(timezone.utc) + timedelta(days=1),
    })
    app.dependency_overrides[routes.get_database] = lambda: db

    response = TestClient(app).get("/sync/status", headers=H)

    assert response.status_code == 200
    state = response.json()["state"]
    assert state["running"] is True
    assert state["source"] == "another_worker"


def test_sync_trigger_queues_configured_background_run():
    app = create_app()
    configured = Settings(
        _env_file=None,
        mongo_uri="mongodb://configured",
        gemini_api_key="configured",
        drive_root_folder_id="root",
    )
    submitted = []

    class FakeRunner:
        def submit(self, settings):
            submitted.append(settings)
            return True

    app.state.sync_runner = FakeRunner()
    app.dependency_overrides[routes.get_settings] = lambda: configured

    response = TestClient(app).post("/sync/retry", headers=H)

    assert response.status_code == 202
    assert submitted == [configured]


def test_search_validation():
    app = create_app()
    app.dependency_overrides[routes.get_database] = lambda: object()
    app.dependency_overrides[routes.get_embedder] = lambda: object()
    c = TestClient(app)
    assert c.post("/search", headers=H, json={"query": "a"}).status_code == 422
    assert c.post("/search", headers=H, json={"query": "ok query", "top_k": 99}).status_code == 422
    assert c.post("/search", headers=H, json={"query": "ok query", "semester": 99}).status_code == 422


def test_search_returns_schema(monkeypatch):
    app = create_app()
    db = mongomock.MongoClient()["search-schema"]
    db[CHUNKS].insert_one({"_id": "approved", "approved": True})
    app.dependency_overrides[routes.get_database] = lambda: db
    app.dependency_overrides[routes.get_embedder] = lambda: object()
    monkeypatch.setattr(routes, "hybrid_search", lambda *a, **k: {
        "query": "q q", "rewritten_query": "q q", "retrieval": {"x": 1},
        "results": [{"chunk_id": "c1", "text": "t", "document_id": "d1", "document_name": "n.pdf",
                     "page_number": 3, "source_url": None, "score": 0.5, "metadata": {"branch": "CSE"}}]})
    r = TestClient(app).post("/search", headers=H, json={"query": "attendance rule"})
    assert r.status_code == 200
    j = r.json()
    assert j["count"] == 1 and j["results"][0]["page_number"] == 3


def test_search_accepts_document_type_filter(monkeypatch):
    app = create_app()
    db = mongomock.MongoClient()["search-doc-type"]
    db[CHUNKS].insert_one({"_id": "approved", "approved": True})
    app.dependency_overrides[routes.get_database] = lambda: db
    app.dependency_overrides[routes.get_embedder] = lambda: object()
    observed = {}

    def fake_search(*_args, **kwargs):
        observed.update(kwargs)
        return {
            "query": "syllabus",
            "rewritten_query": "syllabus",
            "results": [],
            "retrieval": {},
        }

    monkeypatch.setattr(routes, "hybrid_search", fake_search)
    response = TestClient(app).post(
        "/search",
        headers=H,
        json={"query": "syllabus", "doc_type": "syllabus"},
    )

    assert response.status_code == 200
    assert observed["filters"] == {"approved": True, "doc_type": "syllabus"}


def test_document_listing_exposes_local_source_and_metadata_without_drive_link():
    app = create_app()
    db = mongomock.MongoClient()["local-document-list"]
    db[DOCUMENTS].insert_one(
        {
            "file_id": "local-1",
            "file_name": "OS Unit 1.pdf",
            "mime_type": "application/pdf",
            "source_type": "local",
            "source_path": "course-pack.zip!/notes/OS Unit 1.pdf",
            "source_url": "https://drive.example/private",
            "status": "indexed",
            "approved": False,
            "branch": "CSE",
            "subject": "Operating Systems",
            "unit": "1",
        }
    )
    app.dependency_overrides[routes.get_database] = lambda: db
    app.dependency_overrides[routes.get_settings] = lambda: Settings(
        _env_file=None, link_visible=False
    )

    response = TestClient(app).get("/documents", headers=H)

    assert response.status_code == 200
    document = response.json()["items"][0]
    assert document["source_type"] == "local"
    assert document["source_path"] == "course-pack.zip!/notes/OS Unit 1.pdf"
    assert document["metadata"] == {
        "branch": "CSE",
        "subject": "Operating Systems",
        "unit": "1",
    }
    assert document["source_url"] is None


def test_search_without_mongo_config_is_503():
    r = client().post("/search", headers=H, json={"query": "attendance rule"})
    assert r.status_code == 503
