import pytest
from pydantic import ValidationError

from app.config.settings import PROJECT_ROOT, Settings, get_settings


def test_defaults_match_spec():
    s = get_settings()
    assert s.embedding_model == "gemini-embedding-2" and s.embedding_dimensions == 768
    assert s.chunk_target_tokens == 800 and s.chunk_overlap_tokens == 100
    assert s.link_visible is False
    assert s.vector_search_enabled is True
    assert s.rag_text_search_index_name == "rag_text_index"


def test_text_search_index_name_is_configurable(monkeypatch):
    monkeypatch.setenv("RAG_TEXT_SEARCH_INDEX_NAME", "rag_text_index_v2")
    settings = Settings(_env_file=None)
    assert settings.rag_text_search_index_name == "rag_text_index_v2"


def test_approval_and_schedule_are_safe_by_default(monkeypatch):
    monkeypatch.delenv("AUTO_APPROVE", raising=False)
    monkeypatch.delenv("AUTO_SYNC_ENABLED", raising=False)
    settings = Settings(_env_file=None)
    assert settings.auto_approve is False
    assert settings.auto_sync_enabled is False
    assert settings.sync_interval_minutes == 360


def test_relative_paths_resolve_to_project_root():
    s = Settings(_env_file=None)
    assert s.credentials_file == (PROJECT_ROOT / "secrets/credentials.json").resolve()
    assert s.credentials_file.is_absolute()


def test_local_knowledge_path_defaults_to_ignored_project_folder():
    settings = Settings(_env_file=None)
    assert settings.local_knowledge_path == (PROJECT_ROOT / "knowledge").resolve()
    assert settings.missing_for("local") == []


def test_overlap_must_be_smaller_than_target(monkeypatch):
    monkeypatch.setenv("CHUNK_OVERLAP_TOKENS", "900")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_bad_dimensions(monkeypatch):
    monkeypatch.setenv("EMBEDDING_DIMENSIONS", "5")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_missing_for():
    s = Settings(_env_file=None)
    assert "MONGO_URI" in s.missing_for("api")
    assert s.missing_for("gemini") == ["GEMINI_API_KEY"]
