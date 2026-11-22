# Verification report

## Local checks run

Environment: Windows, Python 3.13.12, Node.js v24.15.0, project `.venv`.

| Check | Result |
|---|---|
| Full pytest suite | **126 passed, 1 skipped, 0 failed** |
| Python compile (`compileall app scripts tests`) | Passed |
| Python imports (`app.main` and all discovered `app.*` / `scripts.*` modules; 47 total) | Passed; `.env` loading disabled for the check |
| Focused Python suite for local ingestion, metadata, launcher/smoke workflow, health, Gemini retry behavior, retrieval, and Mongo client | **63 passed** |
| Portal backend JavaScript syntax (`aiRoutes.js`, `ragClient.service.js`) | Passed |
| Portal RAG client unit test | **1 passed** |

Pytest was run with `AUTO_APPROVE=true` only in the test process and with Pydantic `.env` loading disabled before test collection. The actual `.env` was not opened or changed. The single skip is the generated scanned-PDF OCR test (`tests/test_loader_cleaner.py`): Tesseract is not installed in this environment. PyMuPDF also emits one upstream `fitz` deprecation warning.

The GitHub Actions workflow is configured for Python 3.12 and 3.13, but those hosted jobs have not run yet.

## Not run

No live Google Drive requests or scans, local ingestion, Gemini embedding calls, MongoDB writes, deployment, or real Express-to-FastAPI/Atlas search were run. These checks require the user's configuration and/or downloaded materials. The FastAPI import and mocked/unit tests do not establish that external credentials, Atlas indexes, OCR, or production hosting are configured correctly.

## Suggested local verification after configuration

Read-only scan first:

```powershell
.\.venv\Scripts\python.exe -m scripts.check_local_setup
.\.venv\Scripts\python.exe -m scripts.scan_local
```

Only after reviewing the scan and accepting writes/embedding cost:

```powershell
.\.venv\Scripts\python.exe -m scripts.ingest_local
.\.venv\Scripts\python.exe -m scripts.local_smoke_test --status-only
```

Approve indexed documents through the existing admin workflow before expecting student search results. Then run `.\.venv\Scripts\python.exe -m scripts.local_smoke_test` and test College Knowledge through the portal. The complete setup and deployment steps are in `README.md` and `docs/DEPLOYMENT.md`.
