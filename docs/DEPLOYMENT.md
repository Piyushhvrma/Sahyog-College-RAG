# GitHub and Render deployment

This deployment separates **local ingestion** from the hosted **query API**:

1. Run local scanning/ingestion on the Windows computer that has the downloaded materials.
2. The ingestion client writes approved/pending documents and chunks to the configured MongoDB Atlas database.
3. Deploy the FastAPI service to Render to serve portal `/search` and internal admin requests.

The Render service does not contain the local knowledge folder and does not run local ingestion. The checked-in `render.yaml` keeps `AUTO_APPROVE`, `LINK_VISIBLE`, and `AUTO_SYNC_ENABLED` false. It does not configure Google OAuth or run Drive scans.

## GitHub

Create a **private** GitHub repository, then from the project directory review the ignore rules before initializing Git:

```powershell
Set-Location 'C:\path\to\Sahyog-College-RAG'
git init -b main
git status --short
git check-ignore .env secrets\credentials.json secrets\token.json knowledge\sample.pdf
git add -A
git diff --cached --check
git status --short
```

Confirm `.env`, `secrets\`, and all downloaded/archived college materials are ignored and are not staged. Do not copy secrets into tracked files. Then connect the new repository and push when ready:

```powershell
git remote add origin https://github.com/<GITHUB-USER>/<PRIVATE-REPO>.git
git commit -m "Prepare Sahyog College RAG service"
git push -u origin main
```

These are instructions only; no Git repository initialization, commit, or push is performed by this project workflow.

## Render Blueprint

1. Push the reviewed private repository to GitHub.
2. In Render, create a new **Blueprint** from that repository and review the service plan/cost before applying it. `render.yaml` defines one web service, installs `requirements-server.txt`, runs Uvicorn, and uses `/health` as its health check.
3. Set the prompted secret environment variables in the Render dashboard:
   - `RAG_SERVICE_SECRET`
   - `MONGO_URI`
4. Confirm the non-secret settings from `render.yaml`: `MONGO_DB_NAME=sahyog_rag`, `AUTO_APPROVE=false`, `LINK_VISIBLE=false`, `AUTO_SYNC_ENABLED=false`, and `VECTOR_SEARCH_ENABLED=false`.
5. Restrict Atlas Network Access to the service's supported outbound IPs or a private network connection. Do not expose the database publicly without restrictions.
6. Confirm the Atlas `rag_chunks` collection has the ordinary `text_fallback` index on `text`, `file_name`, `document_name`, `heading`, and `subject`. The app's keyword-only mode uses that index and only returns approved chunks; it does not call Gemini or require `GEMINI_API_KEY`. The existing deployment contains no approved chunks, so `/search` will return HTTP 503 until an administrator approves at least one successfully indexed document through the authenticated admin workflow.
7. Wait for `/health` to pass. This is only a liveness check; it does not establish that MongoDB is reachable. Check `/health/secure` from a trusted backend using `X-RAG-Secret`, then verify `/search` after approval.
8. Configure the existing Express backend with `RAG_ENABLED=true`, the deployed `RAG_SERVICE_URL`, and the same `RAG_SERVICE_SECRET`. Keep the secret only in server-side environment variables. Use HTTPS between the portal and RAG service.

The Render plan is a deployment choice and may incur charges. Review it in Render before creating the service.

### Search modes and Atlas indexes

The supplied Render Blueprint sets `VECTOR_SEARCH_ENABLED=false` for an initial keyword-only deployment. `/search` retains its existing request/response schema and uses approved-only MongoDB keyword results. If the Atlas Search index is absent, it falls back to the ordinary `text_fallback` index. With no approved chunks, `/search` returns an explicit 503 rather than attempting an embedding or returning an empty success.

To enable hybrid vector retrieval later, first configure `GEMINI_API_KEY` on the service and create the Atlas vector index on `rag_chunks` with the following definition; wait until Atlas reports it queryable before setting `VECTOR_SEARCH_ENABLED=true`:

```json
{
  "fields": [
    { "type": "vector", "path": "embedding", "numDimensions": 768, "similarity": "cosine" },
    { "type": "filter", "path": "approved" },
    { "type": "filter", "path": "branch" },
    { "type": "filter", "path": "semester" },
    { "type": "filter", "path": "subject" },
    { "type": "filter", "path": "academic_year" },
    { "type": "filter", "path": "doc_type" }
  ]
}
```

In vector mode, Gemini query embedding failures return bounded service errors. Daily quota exhaustion is reported as HTTP 429; a supplied `Retry-After` is capped at 30 seconds. Do not enable vector mode until both the embedding configuration and index are verified.

## Local knowledge ingestion

Keep the local materials on the ingestion computer. In that project's existing `.env`, set `LOCAL_KNOWLEDGE_DIR` to the actual folder and configure `MONGO_URI`, `GEMINI_API_KEY`, and `RAG_SERVICE_SECRET`. Do not replace an existing `.env`. The same Atlas database settings must be used by the hosted API and the local ingestion environment.

From PowerShell:

```powershell
Set-Location 'C:\path\to\Sahyog-College-RAG'
.\.venv\Scripts\python.exe -m scripts.check_local_setup
.\.venv\Scripts\python.exe -m scripts.scan_local
.\.venv\Scripts\python.exe -m scripts.ingest_local
```

`scan_local` is read-only and does not call Gemini or MongoDB. `ingest_local` prompts before processing; it uses the existing extraction, chunking, and embedding pipeline, and writes to Atlas. Newly discovered documents remain unapproved. Re-running the ingestion skips unchanged content, updates changed content, and does not delete database documents just because local files are missing.

To run the guided local workflow, double-click `RUN_SAHYOG_RAG.bat`. It scans only `LOCAL_KNOWLEDGE_DIR`, requires confirmation before ingestion, then starts the local API if one is not already healthy. It never invokes Drive scanning or Drive ingestion.

After ingestion, approve documents through the existing authenticated portal admin route. Then verify API/Mongo counts and, if there are approved documents, run a real search:

```powershell
.\.venv\Scripts\python.exe -m scripts.local_smoke_test --status-only
.\.venv\Scripts\python.exe -m scripts.local_smoke_test
```

An empty approved-document set is reported as such; no successful retrieval is fabricated. A real search requires valid Gemini and MongoDB Atlas configuration.

## Scheduled Google Drive sync (optional and separate)

The local-first Render Blueprint does not schedule Drive jobs. If Drive sync is later configured, use a single provider Cron Job (or equivalent external scheduler), not an in-process scheduler. Mount OAuth files securely, set `DRIVE_INTERACTIVE_AUTH=false`, keep `AUTO_APPROVE=false`, and run:

```text
python -m scripts.sync_drive
```

Configure exactly one schedule and use the same MongoDB database as the API. Drive runs retain the MongoDB lease and partial-scan deletion protections. Do not enable this workflow as a substitute for local-folder ingestion.

## API and security

- `/health` is the public liveness check; the internal routes require `X-RAG-Secret`.
- Only expose admin routes through the authenticated Express backend; do not send the shared secret to the browser.
- Retrieval returns approved documents only. Keep `AUTO_APPROVE=false`.
- Drive source URLs remain hidden unless `LINK_VISIBLE=true`; keep it false unless visibility has been reviewed.
- Never commit `.env`, OAuth tokens, API keys, MongoDB URIs, archives, or downloaded college documents.
