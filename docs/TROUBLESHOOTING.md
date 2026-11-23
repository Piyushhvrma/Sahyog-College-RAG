# Troubleshooting
| Symptom | Cause / fix |
|---|---|
| `Activate.ps1 cannot be loaded` | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |
| `ERROR: missing required .env values: …` | Fill them in `.env` (copy from `.env.example`) |
| `OAuth client file not found` | Put the Desktop client JSON at `secrets\credentials.json` |
| Browser says *access blocked / app not verified* | Add yourself as **Test user** or publish the app; click *Advanced → continue* |
| Asked to log in again after a week | Testing-mode refresh tokens expire in 7 days → publish the OAuth app (see GOOGLE_DRIVE_SETUP) |
| `404 File not found` for the root folder | Wrong `DRIVE_ROOT_FOLDER_ID`, or this Google account can't open it |
| Files listed as *inaccessible* | The signed-in account lacks permission; ask the owner to share them (read-only is enough) |
| Scanned PDF gives few/no chunks | Install Tesseract and set `TESSERACT_CMD` (INGESTION_GUIDE) |
| `Got N-dim vector, expected 768` | `EMBEDDING_DIMENSIONS` doesn't match the model's output; keep 768 |
| Gemini `429` | Free-tier limit; the embedder backs off automatically. Increase `EMBED_MIN_INTERVAL_S` or lower `EMBED_BATCH_SIZE` |
| `/search` returns `count: 0` | Check approved chunk counts and `rag_text_index` readiness/mapping. Keyword search requires all query terms; verify `file_name`, `text`, `heading`, and `subject` mappings. Inspect `keyword_engine` and fallback logs. |
| `$vectorSearch` error / index not found | Create `rag_vector_index` (MONGODB_SETUP); wait for READY |
| `keyword_engine: text_index` | Atlas Search failed or returned no hits; approved-only MongoDB text fallback was used. Check the Atlas mapping and `text_fallback` fields if fallback results differ. |
| Mongo `ServerSelectionTimeoutError` | Add your IP in Atlas *Network Access*; check `MONGO_URI` |
| Express gets `401` from RAG | `RAG_SERVICE_SECRET` differs between Express and Python `.env` |
| Express gets `503` from RAG | Python `.env` missing `RAG_SERVICE_SECRET` or `MONGO_URI` |
| Render first request very slow | Free instances sleep; first call wakes them (raise `RAG_TIMEOUT_MS`, or ping `/health` on a schedule) |
| Google Sheets only shows first tab | Drive's CSV export limitation; split into separate files |
| Document changed but old answer appears | Run `python -m scripts.sync_drive` |
