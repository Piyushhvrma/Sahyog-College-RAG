import pytest

from app.config.settings import get_settings

SECRET = "test-secret-value-123"


@pytest.fixture(autouse=True)
def env(monkeypatch):
    test_env = {
        "ENVIRONMENT": "test",
        "LOG_LEVEL": "CRITICAL",
        "RAG_SERVICE_SECRET": SECRET,
        "MONGO_URI": "",
        "MONGO_DB_NAME": "sahyog_rag",
        "GEMINI_API_KEY": "",
        "EMBEDDING_MODEL": "gemini-embedding-2",
        "EMBEDDING_DIMENSIONS": "768",
        "DRIVE_ROOT_FOLDER_ID": "",
        "DRIVE_INTERACTIVE_AUTH": "false",
        "MAX_DOWNLOAD_MB": "50",
        "LOCAL_KNOWLEDGE_DIR": "./knowledge",
        "TESSERACT_CMD": "",
        "OCR_ENABLED": "false",
        "CHUNK_TARGET_TOKENS": "800",
        "CHUNK_OVERLAP_TOKENS": "100",
        "SEARCH_TOP_K": "5",
        "VECTOR_SEARCH_ENABLED": "true",
        "LINK_VISIBLE": "false",
        "AUTO_APPROVE": "false",
        "AUTO_SYNC_ENABLED": "false",
        "SYNC_INTERVAL_MINUTES": "360",
        "EMBED_BATCH_SIZE": "16",
        "EMBED_MIN_INTERVAL_S": "0",
    }
    for key, value in test_env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
