import json

from app.config.settings import PROJECT_ROOT, Settings
from scripts import check_local_setup, local_smoke_test
from scripts import scan_local


def test_local_setup_validation_reports_setting_names_not_secret_values(tmp_path):
    settings = Settings(
        _env_file=None,
        rag_service_secret="never-print-this",
        gemini_api_key="never-print-this-either",
        local_knowledge_dir=str(tmp_path),
    )

    issues = check_local_setup.validate_local_configuration(settings)

    assert any("MONGO_URI" in issue for issue in issues)
    assert all("never-print-this" not in issue for issue in issues)


def test_local_setup_rejects_missing_folder_and_project_root():
    missing = Settings(
        _env_file=None,
        local_knowledge_dir=str(PROJECT_ROOT / "does-not-exist"),
    )
    root = Settings(_env_file=None, local_knowledge_dir=str(PROJECT_ROOT))

    assert any(
        "does not exist" in issue
        for issue in check_local_setup.validate_local_configuration(missing)
    )
    assert any(
        "project root" in issue
        for issue in check_local_setup.validate_local_configuration(root)
    )


def test_launcher_only_calls_local_ingestion_commands():
    launcher = (PROJECT_ROOT / "RUN_SAHYOG_RAG.bat").read_text(
        encoding="utf-8"
    ).lower()

    assert "scripts.scan_local" in launcher
    assert "scripts.ingest_local" in launcher
    assert "scripts.scan_drive" not in launcher
    assert "scripts.sync_drive" not in launcher
    assert "python -m scripts.ingest " not in launcher
    assert "choice /c yn" in launcher
    assert "existing sahyog rag api is already healthy" in launcher
    assert "--status-only" in launcher


def test_local_scan_command_prints_dry_run_summary_without_external_services(
    tmp_path, monkeypatch, capsys
):
    root = tmp_path / "materials"
    root.mkdir()
    (root / "notes.txt").write_text(
        "A local academic note " * 10, encoding="utf-8"
    )
    settings = Settings(
        _env_file=None,
        local_knowledge_dir=str(root),
        mongo_uri="",
        gemini_api_key="",
    )
    monkeypatch.setattr(scan_local, "get_settings", lambda: settings)

    result = scan_local.main()

    output = capsys.readouterr().out
    assert result == 0
    assert "Supported files: 1" in output
    assert "Supported formats: txt=1" in output
    assert "Estimated ingest candidates: 1 files" in output
    assert "notes.txt" in output
    assert "no MongoDB writes" in output


def test_local_scan_still_lists_files_when_incremental_database_read_fails(
    tmp_path, monkeypatch, capsys
):
    root = tmp_path / "materials"
    root.mkdir()
    (root / "notes.txt").write_text("A local academic note " * 10, encoding="utf-8")
    settings = Settings(
        _env_file=None,
        local_knowledge_dir=str(root),
        mongo_uri="mongodb://unavailable",
        gemini_api_key="",
    )
    monkeypatch.setattr(scan_local, "get_settings", lambda: settings)

    def unavailable_db(_settings):
        raise ConnectionError("test-only unavailable MongoDB")

    monkeypatch.setattr(scan_local, "get_db", unavailable_db)

    result = scan_local.main()

    output = capsys.readouterr().out
    assert result == 1
    assert "Supported files: 1" in output
    assert "notes.txt" in output
    assert "Incremental ingestion estimate unavailable" in output
    assert "test-only unavailable MongoDB" not in output


def test_smoke_test_skips_search_without_approved_documents(monkeypatch, capsys):
    responses = {
        "/health": {"service": "sahyog-college-rag"},
        "/health/secure": {
            "mongo_connected": True,
            "gemini_key_configured": True,
        },
        "/stats": {
            "total_documents": 3,
            "total_chunks": 8,
            "approved_documents": 0,
        },
    }
    requested = []

    class FakeResponse:
        def __init__(self, value):
            self.value = value

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(self.value).encode("utf-8")

    def fake_urlopen(request, timeout):
        requested.append(request.full_url)
        path = request.full_url.removeprefix("http://local")
        return FakeResponse(responses[path])

    monkeypatch.setattr(local_smoke_test, "urlopen", fake_urlopen)

    result = local_smoke_test.run(
        "http://local", "sample question", "private-test-secret"
    )

    output = capsys.readouterr().out
    assert result == 2
    assert "No approved documents" in output
    assert "private-test-secret" not in output
    assert not any(url.endswith("/search") for url in requested)


def test_smoke_test_displays_actual_source_and_snippet(monkeypatch, capsys):
    values = [
        {"service": "sahyog-college-rag"},
        {"mongo_connected": True, "gemini_key_configured": True},
        {"total_documents": 2, "total_chunks": 5, "approved_documents": 1},
        {
            "results": [
                {
                    "document_name": "OS Notes.pdf",
                    "page_number": 4,
                    "text": "The scheduler selects the next process.",
                }
            ]
        },
    ]

    class FakeResponse:
        def __init__(self, value):
            self.value = value

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(self.value).encode("utf-8")

    def fake_urlopen(_request, timeout):
        return FakeResponse(values.pop(0))

    monkeypatch.setattr(local_smoke_test, "urlopen", fake_urlopen)

    result = local_smoke_test.run("http://local", "sample question", "secret")

    output = capsys.readouterr().out
    assert result == 0
    assert "OS Notes.pdf — page 4" in output
    assert "The scheduler selects the next process." in output


def test_status_only_smoke_check_does_not_run_search(monkeypatch, capsys):
    responses = [
        {"service": "sahyog-college-rag"},
        {"mongo_connected": True, "gemini_key_configured": True},
        {"total_documents": 1, "total_chunks": 2, "approved_documents": 1},
    ]
    requested = []

    class FakeResponse:
        def __init__(self, value):
            self.value = value

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(self.value).encode("utf-8")

    def fake_urlopen(request, timeout):
        requested.append(request.full_url)
        return FakeResponse(responses.pop(0))

    monkeypatch.setattr(local_smoke_test, "urlopen", fake_urlopen)

    result = local_smoke_test.run(
        "http://local", "sample question", "secret", status_only=True
    )

    assert result == 0
    assert "search was not run" in capsys.readouterr().out
    assert not any(url.endswith("/search") for url in requested)
