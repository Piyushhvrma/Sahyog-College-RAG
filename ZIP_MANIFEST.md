# Sahyog College RAG source package

This list reflects the source tree after adding local-folder/ZIP ingestion, local workflow scripts, tests, and deployment configuration. Runtime data and secrets listed below are not package contents.

## Application

- `app/__init__.py`
- `app/api/{__init__.py,routes.py,schemas.py,security.py}`
- `app/config/{__init__.py,settings.py}`
- `app/drive/{__init__.py,auth.py,client.py,folder_scanner.py,runner.py,sync.py}`
- `app/embeddings/{__init__.py,gemini_embedder.py}`
- `app/ingestion/{__init__.py,chunker.py,cleaner.py,loader.py,local_source.py,local_sync.py,metadata.py}`
- `app/rag/{__init__.py,query_rewriter.py}`
- `app/retrieval/{__init__.py,hybrid_search.py,keyword_search.py,rrf.py}`
- `app/vectorstore/{__init__.py,index_manager.py,mongo_client.py,vector_search.py}`
- `app/main.py`

## Scripts and tests

- `scripts/__init__.py`, `scripts/_common.py`
- `scripts/check_local_setup.py`, `scripts/extract_documents.py`, `scripts/ingest.py`, `scripts/ingest_local.py`
- `scripts/inspect_chunks.py`, `scripts/local_smoke_test.py`, `scripts/reindex.py`
- `scripts/diagnose_search.py`, `scripts/scan_drive.py`, `scripts/scan_local.py`, `scripts/setup_mongo.py`, `scripts/sync_drive.py`
- `scripts/test_drive.py`, `scripts/test_embeddings.py`, `scripts/test_retrieval.py`
- `tests/__init__.py`, `tests/conftest.py`
- `tests/test_chunker.py`, `tests/test_config_files.py`, `tests/test_drive_client.py`, `tests/test_embedder.py`
- `tests/test_folder_scanner_errors.py`, `tests/test_health.py`, `tests/test_loader_cleaner.py`
- `tests/test_local_ingestion.py`, `tests/test_local_workflow.py`, `tests/test_metadata.py`, `tests/test_mongo_client.py`
- `tests/test_retrieval_logic.py`, `tests/test_rrf.py`, `tests/test_security.py`, `tests/test_settings.py`, `tests/test_sync_pipeline.py`

## Documentation and integration

- `.env.example`, `.gitignore`, `README.md`, `pytest.ini`
- `docs/API_REFERENCE.md`, `docs/ARCHITECTURE.md`, `docs/DEPLOYMENT.md`
- `docs/GOOGLE_DRIVE_SETUP.md`, `docs/INGESTION_GUIDE.md`, `docs/MONGODB_SETUP.md`
- `docs/SAHYOG_INTEGRATION.md`, `docs/TEST_REPORT.md`, `docs/TROUBLESHOOTING.md`
- `integration/backend-aiRoutes.integration.md`, `integration/backend-env.integration.md`
- `integration/backend-ragAdminRoutes.js`, `integration/backend-ragClient.service.js`, `integration/backend-server.integration.md`
- `integration/frontend-admin-integration.md`, `integration/frontend-AdminKnowledgePanel.jsx`
- `integration/frontend-AIHelpPage.integration.md`, `integration/frontend-ai.api.js`
- `integration/frontend-rag.api.js`, `integration/frontend-SourceCitations.jsx`, `integration/frontend-HelpPages.css`
- `.github/workflows/python-tests.yml`, `.vscode/launch.json`, `.vscode/tasks.json`
- `render.yaml`, `requirements-server.txt`, `requirements.txt`, `RUN_SAHYOG_RAG.bat`
- `data/.gitkeep`

## Never package

Keep `.env`, `secrets/`, OAuth credentials/tokens, local knowledge folders, downloaded documents/archives, generated reports/extractions, `.venv/`, Python caches, logs, and temporary files out of the package and Git. `.gitignore` excludes these paths and document/archive extensions; review `git status --short` before creating a release.

For a Git-backed source archive, create a private repository and review the staged file list first. `git archive` contains committed files only; it does not include ignored local data. Setup and deployment instructions are in `README.md` and `docs/DEPLOYMENT.md`.
