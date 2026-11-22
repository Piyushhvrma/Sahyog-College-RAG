# Architecture

```
Local knowledge folder / Google Drive ──► extract → chunk → Gemini ──► MongoDB Atlas
React (Vercel) ──► Express (Render/NITRR) ──► FastAPI RAG ──► MongoDB Atlas
                        │  JWT + roles + rate limit        │  Gemini embeds the query
                        │                                  └─ vector + keyword → RRF → chunks
                        ├─ builds grounded prompt
                        └─ Groq llama-3.3-70b-versatile → answer + sources → React
```

## Key decisions
| Decision | Reason |
|---|---|
| Separate Python service | PDF/OCR/embedding libraries live naturally in Python; keeps Express lean |
| Groq stays in Express | Existing integration, keys and rate limiting keep working; General mode never depends on RAG |
| Browser never calls FastAPI | Shared secret stays server-side; one auth model (JWT) |
| Ingestion is incremental CLI/worker sync | Local setup can authorize interactively; deployed Cron/API jobs require a mounted refreshable OAuth token and never open a browser |
| Python-side RRF | `$rankFusion` isn't assumed on M0; RRF is 20 lines and fully unit-tested |
| `$text` fallback for keyword search | Works even if the Atlas Search index isn't built yet |
| Embeddings: `gemini-embedding-2`, 768-d | Spec. This model takes the task in the prompt text (`task: search result \| query: …`, `title: … \| text: …`), **not** a `task_type` parameter. Vectors are L2-normalised after truncation |
| Chunk = page-bounded | Every chunk has a trustworthy page/slide number for citations |
| Versioned chunk replacement | New chunks are inserted before old ones are deleted, so a document is never briefly un-searchable |

## Collections
* **`rag_documents`** – one per Drive file: `file_id` (unique), `file_name`, `folder_path`, `status` (`indexed|failed|pending|duplicate|deleted`), `approved`, `doc_hash`, `change_token`, `embedding_model/dimensions`, `chunk_count`, `error`, branch/semester/subject/year/doc_type, timestamps.
* **`rag_chunks`** – one per chunk: `_id = fileId:version:index`, `document_id`, `text`, `embedding` (768 floats), `page_number`, `heading`, `content_hash`, denormalised `approved` and metadata fields (needed for filtered vector search).
* **`rag_sync_logs`** – one per ingestion run with summary and failures.

## Retrieval pipeline
1. Rule-based query rewrite (expand `pyq`, `sem`, `nitrr` …; prepend the previous user message for short follow-ups).
2. Gemini query embedding.
3. `$vectorSearch` (approved chunks only; optional hard filters).
4. Keyword search (`$search` or `$text` fallback).
5. RRF (k = 60).
6. Soft boosts (+15 % per matching hint parsed from the question: branch, semester, year, doc type).
7. Dedupe by content hash; max 3 chunks per document.
8. Top-k (default 5).

## Security model
`X-RAG-Secret` (constant-time `hmac.compare_digest`; fails closed if unset) on every endpoint except `/health`. No CORS middleware. OpenAPI/docs disabled when `ENVIRONMENT=production`. Drive links are omitted unless `LINK_VISIBLE=true`. Retrieved text is treated as untrusted data in the prompt (prompt-injection guard in the Express prompt builder).
