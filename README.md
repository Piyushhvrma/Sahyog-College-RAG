# Sahyog College RAG

A self-contained Python microservice that lets **Sahyog AI Buddy** answer NIT Raipur questions from **your own documents** (PYQs, notes, notices, placement files, timetables…), with citations (document name + page).

> **This is retrieval, not training.** Documents are converted to *embeddings* (numeric fingerprints) and stored in MongoDB. At question time the most relevant passages are fetched and handed to Groq as context. **No model is fine-tuned or retrained.**

## 1. What it does
```
Local folder / Google Drive ──► read/download ──► extract text (OCR fallback) ──► clean ──► chunk ──► metadata
                                                                                    │
                                              Gemini embeddings (gemini-embedding-2, 768-d) ◄┘
                                                                                    ▼
                                                                        MongoDB Atlas (chunks + vectors)
Student question ► Express ► FastAPI /search ► vector + keyword ► RRF ► top chunks ► Express ► Groq ► answer + citations
```

## 2. Architecture (who does what)
| Layer | Responsibility |
|---|---|
| **This project (FastAPI + CLI)** | Drive auth, scanning, extraction, OCR, chunking, metadata, embeddings, MongoDB, vector + keyword search, RRF, sync |
| **Existing Express backend** | JWT, roles, rate limit, calling this service, grounded prompt, **Groq generation**, forwarding citations |
| **React** | General / College Knowledge toggle, chat history, citations, admin knowledge panel |

The browser **never** calls this service. Only Express does, with the `X-RAG-Secret` header. Details: `docs/ARCHITECTURE.md`.

## 3. Folder structure
```
Sahyog-College-RAG/
├── app/            config · api (routes/schemas/security) · drive · ingestion · embeddings · vectorstore · retrieval · rag
├── scripts/        CLI commands (run with python -m scripts.<name>)
├── tests/          pytest suite (no real credentials needed)
├── integration/    ready-to-copy Express + React files for Sahyog
├── docs/           setup, ingestion, API, integration, deployment, troubleshooting, test report
├── data/           extracted/ + reports/ (git-ignored contents)
├── .env.example  requirements.txt  requirements-server.txt  pytest.ini
```

## 4. Requirements
* Windows 10/11, **Python 3.12 or 3.13**, VS Code
* A local folder of college material; Google Drive access is only needed for Drive workflows
* Google Cloud project with Drive API + Desktop OAuth client (you already have this)
* Gemini API key · MongoDB Atlas cluster (M0 free works)
* *(Only for scanned PDFs)* Tesseract OCR

## 5. Python setup on Windows (PowerShell)
```powershell
cd C:\path\to\Sahyog-College-RAG
py -3.12 --version
```
## 6. Virtual environment
```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
# If PowerShell blocks scripts, run once:  Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
python -m pip install --upgrade pip
```
## 7. Install dependencies
```powershell
pip install -r requirements.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
code .env      # create it only if absent; edit the existing file in place
```
Generate the shared secret and paste it as `RAG_SERVICE_SECRET`:
```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

## 8. Google Cloud OAuth → `docs/GOOGLE_DRIVE_SETUP.md`
Download the Desktop OAuth JSON, save as `secrets\credentials.json` (the `secrets\` folder is git-ignored).
## 9. Drive folder ID
Open the folder in Drive. URL looks like `https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOpQrStUvWxYz?usp=sharing`.
The ID is the part **after `/folders/` and before `?`** → `DRIVE_ROOT_FOLDER_ID=1AbCdEfGhIjKlMnOpQrStUvWxYz`.
## 10. Gemini API key
Go to https://aistudio.google.com/apikey → *Create API key* → put it in `.env` as `GEMINI_API_KEY`. Never commit it.
## 11. MongoDB Atlas → `docs/MONGODB_SETUP.md`
Put the connection string in `MONGO_URI`. Allow your IP in *Network Access*.
## 12. Vector Search index
```powershell
python -m scripts.setup_mongo
```
creates collections, tries to create both Atlas indexes, and prints the exact JSON if you must create them in the Atlas UI.

## 13. Optional Google Drive workflow

The primary local workflow is documented above. If you also use the existing Drive source, configure its OAuth files and folder ID first; these commands access Google Drive and are not used by `RUN_SAHYOG_RAG.bat`.

```powershell
python -m scripts.test_drive          # opens browser once; proves Drive access
python -m scripts.scan_drive          # what would be ingested
python -m scripts.extract_documents --limit 3   # inspect extraction quality, no Gemini/Mongo
python -m scripts.test_embeddings     # proves Gemini key/model/768 dims
python -m scripts.ingest --dry-run
python -m scripts.ingest --limit 3    # small real run
python -m scripts.ingest              # everything
```
## 14. Incremental Drive sync
After adding/changing/removing files in Drive:
```powershell
python -m scripts.sync_drive
```
Unchanged files are skipped (no re-embedding cost). Details in `docs/INGESTION_GUIDE.md`.

## 15. Retrieval testing
```powershell
python -m scripts.test_retrieval
python -m scripts.test_retrieval --query "OS PYQ semester 5" --branch CSE
python -m scripts.inspect_chunks --name "Operating"
```
## 16. Start FastAPI
```powershell
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```
Open http://127.0.0.1:8000/health (public) and `/docs` (development only).

## 17–18. Connect Express and React → `docs/SAHYOG_INTEGRATION.md` (and the `integration/` folder)
## 19. Deployment → `docs/DEPLOYMENT.md`
## 20. Troubleshooting → `docs/TROUBLESHOOTING.md`

## Local knowledge folder and one-click workflow

The local source reuses the existing extract → clean → chunk → embed → MongoDB pipeline. It scans recursively, preserves the relative folder hierarchy and metadata, uses a stable local file identity plus a SHA-256 change token, and never deletes indexed documents just because a local file is absent. New documents remain unapproved because `AUTO_APPROVE=false` by default.

ZIP archives are scanned without extracting members into the knowledge folder. Supported files inside ZIPs (including nested ZIPs within the configured depth limit) use the regular document processors. Corrupt, encrypted, unsafe-path, over-limit, duplicate, and unsupported entries are reported rather than silently treated as successfully ingested. Limits and details are documented in `docs/INGESTION_GUIDE.md`.

1. In your existing `.env`, set `LOCAL_KNOWLEDGE_DIR` to the folder where the downloaded hierarchy is or will be saved. For example, replace this placeholder with your own path:

   ```text
   LOCAL_KNOWLEDGE_DIR=D:\path\to\college-materials
   ```

   The `.env.example` default is `./knowledge` relative to this project. The project-local `knowledge\` directory is ignored by Git. Do not copy over or replace an existing `.env`.
2. Ensure `MONGO_URI`, `GEMINI_API_KEY`, and `RAG_SERVICE_SECRET` are set in `.env`, and that the Atlas vector/text indexes have been prepared with `python -m scripts.setup_mongo`.
3. Run the read-only local scan. It lists files, duplicates, unsupported/unreadable/oversized paths, and an incremental estimate. It does not call Gemini or write to MongoDB:

   ```powershell
   .\.venv\Scripts\python.exe -m scripts.scan_local
   ```
4. For interactive ingestion, use:

   ```powershell
   .\.venv\Scripts\python.exe -m scripts.ingest_local
   ```

   Confirm at the prompt to allow document/chunk writes and Gemini embedding requests. Re-running skips unchanged files and preserves admin approval on updates.
5. To run the guarded Windows workflow, double-click `RUN_SAHYOG_RAG.bat`. It validates the existing `.venv`, dependencies and configuration, pings MongoDB, scans only the local folder, asks before ingestion, then starts FastAPI in another terminal. It never invokes a Drive scan or Drive ingestion.
6. Approve newly indexed documents in the existing admin workflow. Then run the smoke test:

   ```powershell
   .\.venv\Scripts\python.exe -m scripts.local_smoke_test
   ```

   It checks FastAPI/MongoDB, reports document/chunk counts, and searches only when approved documents exist. In development, Swagger is at `http://127.0.0.1:8000/docs`.

---
## From zero to first successful college question
1. **Install** (sections 5–7) and fill `.env`: `RAG_SERVICE_SECRET`, `MONGO_URI`, `GEMINI_API_KEY`, `DRIVE_ROOT_FOLDER_ID`.
2. Put `credentials.json` in `secrets\`.
3. `python -m scripts.setup_mongo` — wait until both Atlas indexes show **Active/READY** in the Atlas UI (about 1 minute).
4. `python -m scripts.test_drive` → approve in the browser.
5. `python -m scripts.test_embeddings` → must print `SUCCESS`.
6. `python -m scripts.ingest --limit 3`.
7. `python -m scripts.test_retrieval --query "attendance requirement"` → you should see ranked chunks with file names and pages.
8. Start the API: `uvicorn app.main:app --port 8000`.
9. Ask through the API (PowerShell):
```powershell
if (-not $env:RAG_SERVICE_SECRET) { throw "Set RAG_SERVICE_SECRET in this trusted terminal environment first." }
$body = @{ query = "What is the minimum attendance required?"; top_k = 5 } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/search -Headers @{ "X-RAG-Secret" = $env:RAG_SERVICE_SECRET } -ContentType "application/json" -Body $body | ConvertTo-Json -Depth 6
```
You get `results[]` with `text`, `document_name`, `page_number`, `score`, `metadata`. That is your first successful college question at the retrieval layer; Express + Groq turn those passages into the final answer.

## Tests
```powershell
.\.venv\Scripts\python.exe -m pytest -q
```
See `docs/TEST_REPORT.md` for what was actually run and what needs your real credentials.

## Security notes
* Never commit `.env`, `secrets\`, `token.json`, or college documents (all git-ignored).
* Drive access is **read-only**. The service can't see anything the signed-in Google account can't.
* Drive links are hidden from API responses unless `LINK_VISIBLE=true`.

## GitHub and Render

See `docs/DEPLOYMENT.md` for GitHub initialization/push commands and the Render Blueprint workflow. The Blueprint deploys the API only; local ingestion runs on your Windows computer and writes to the configured MongoDB Atlas database. Do not upload `.env`, OAuth files, downloaded materials, or archives to GitHub or the API host.
