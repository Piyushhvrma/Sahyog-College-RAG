"""Guards the shipped config files: required variables present, no secrets, ignore rules correct."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = ["ENVIRONMENT", "LOG_LEVEL", "RAG_SERVICE_SECRET", "MONGO_URI", "MONGO_DB_NAME", "GEMINI_API_KEY",
            "EMBEDDING_MODEL", "EMBEDDING_DIMENSIONS", "GOOGLE_CREDENTIALS_PATH", "GOOGLE_TOKEN_PATH",
            "DRIVE_ROOT_FOLDER_ID", "LOCAL_KNOWLEDGE_DIR", "MAX_DOWNLOAD_MB", "TESSERACT_CMD", "CHUNK_TARGET_TOKENS",
            "CHUNK_OVERLAP_TOKENS", "VECTOR_SEARCH_ENABLED", "RAG_TEXT_SEARCH_INDEX_NAME"]


def env_pairs():
    out = {}
    for line in (ROOT / ".env.example").read_text().splitlines():
        m = re.match(r"^([A-Z_]+)=(.*)$", line.strip())
        if m:
            out[m.group(1)] = m.group(2)
    return out


def test_env_example_has_all_required_vars():
    assert set(REQUIRED) <= set(env_pairs())


def test_env_example_contains_no_secret_values():
    p = env_pairs()
    for k in ("RAG_SERVICE_SECRET", "MONGO_URI", "GEMINI_API_KEY", "DRIVE_ROOT_FOLDER_ID"):
        assert p[k] == "", k
    assert p["EMBEDDING_MODEL"] == "gemini-embedding-2" and p["EMBEDDING_DIMENSIONS"] == "768"


def test_gitignore_blocks_sensitive_files():
    gi = (ROOT / ".gitignore").read_text().splitlines()
    for rule in (".env", "secrets/", "credentials.json", "token.json", "client_secret*.json",
                 "knowledge/",
                 ".venv/", "__pycache__/", "data/downloads/", "*.zip",
                 "*.bson", "*.log", "*.tmp", "*.txt", "!requirements*.txt"):
        assert rule in gi, rule
    assert "!.env.example" in gi


def test_every_env_example_key_is_a_known_setting():
    from app.config.settings import Settings
    fields = {n.upper() for n in Settings.model_fields}
    assert set(env_pairs()) <= fields


def test_required_files_exist_and_not_empty():
    required = """app/main.py app/config/settings.py app/api/routes.py app/api/schemas.py app/api/security.py
    app/drive/auth.py app/drive/client.py app/drive/folder_scanner.py app/drive/sync.py
    app/ingestion/loader.py app/ingestion/cleaner.py app/ingestion/chunker.py app/ingestion/metadata.py
    app/embeddings/gemini_embedder.py app/vectorstore/mongo_client.py app/vectorstore/index_manager.py
    app/vectorstore/vector_search.py app/retrieval/keyword_search.py app/retrieval/hybrid_search.py
    app/retrieval/rrf.py app/rag/query_rewriter.py
    scripts/test_drive.py scripts/scan_drive.py scripts/extract_documents.py scripts/ingest.py scripts/reindex.py
    scripts/test_embeddings.py scripts/test_retrieval.py scripts/sync_drive.py scripts/inspect_chunks.py
    scripts/check_local_setup.py scripts/scan_local.py scripts/ingest_local.py scripts/local_smoke_test.py
    app/ingestion/local_source.py app/ingestion/local_sync.py RUN_SAHYOG_RAG.bat
    .vscode/tasks.json .vscode/launch.json
    render.yaml .github/workflows/python-tests.yml ZIP_MANIFEST.md
    tests/test_local_ingestion.py tests/test_local_workflow.py tests/test_mongo_client.py
    tests/test_search_modes.py tests/test_search_diagnostics.py
    scripts/diagnose_search.py
    docs/ARCHITECTURE.md docs/GOOGLE_DRIVE_SETUP.md docs/MONGODB_SETUP.md docs/INGESTION_GUIDE.md
    docs/API_REFERENCE.md docs/SAHYOG_INTEGRATION.md docs/TROUBLESHOOTING.md docs/DEPLOYMENT.md
    integration/backend-aiRoutes.integration.md integration/backend-env.integration.md
    integration/backend-server.integration.md integration/backend-ragAdminRoutes.js
    integration/frontend-ai.api.js integration/frontend-AIHelpPage.integration.md
    integration/frontend-HelpPages.css integration/frontend-rag.api.js
    integration/frontend-AdminKnowledgePanel.jsx integration/frontend-admin-integration.md
    README.md requirements.txt .env.example .gitignore""".split()
    for f in required:
        p = ROOT / f
        assert p.exists() and p.stat().st_size > 100, f
