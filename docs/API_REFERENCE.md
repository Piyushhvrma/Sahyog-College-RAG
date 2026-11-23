# API reference
Base URL (local): `http://127.0.0.1:8000`. All endpoints except `/health` need header `X-RAG-Secret: <RAG_SERVICE_SECRET>` (401 if wrong/missing, 503 if the server has no secret configured). **Call these from Express only.**

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Public liveness: `{"status":"ok","service":"sahyog-college-rag"}` |
| GET | `/health/secure` | Config check: Mongo ping, Gemini key present?, model, dims, vector mode (never returns secrets) |
| POST | `/search` | Hybrid retrieval |
| GET | `/documents` | List documents (`status`, `approved`, `q`, `limit` ≤200, `skip`), including source and inferred metadata |
| PATCH | `/documents/{file_id}/approval` | Enable/disable a document for students |
| GET | `/stats` | Totals, approved/disabled, failed, chunks, last sync |
| GET | `/sync/status` | Current sync state, most recent report, external schedule |
| POST | `/sync` | Queue an incremental sync in the background |
| POST | `/sync/retry` | Rescan, retry failed files, and recheck inaccessible targets |
| GET | `/inaccessible` | List inaccessible Drive items and their last check |

## POST /search
Request:
```json
{ "query": "minimum attendance for end sem", "top_k": 5,
  "branch": "CSE", "semester": 5, "subject": null, "academic_year": null,
  "doc_type": null,
  "history": ["previous user message (optional)"] }
```
Response:
```json
{
  "query": "…", "rewritten_query": "…", "count": 2,
  "results": [{
    "chunk_id": "1AbC:1731234567890:3", "text": "…", "document_id": "1AbC",
    "document_name": "Academic Rules.pdf", "page_number": 12,
    "source_url": null, "score": 0.0312,
    "metadata": {"branch":"CSE","semester":5,"doc_type":"regulation","folder_path":"Academic","heading":"Attendance","unit":1}
  }],
  "retrieval": {"vector_hits": 20, "keyword_hits": 14, "keyword_engine": "atlas_search", "fusion": "rrf(k=60)"}
}
```
* Only **approved** chunks are searched. `source_url` is `null` unless `LINK_VISIBLE=true`.
* `score` is the fused RRF score (relative ranking only; not a probability).
* Optional filters include `branch`, `semester`, `subject`, `academic_year`, and `doc_type`. Metadata is conservative: unavailable values remain absent rather than being inferred without evidence.
* Keyword text matching requires all query terms across `text`, `file_name`, legacy `document_name`, and `heading`. Exact filename and phrase matches receive ranking boosts. All keyword paths retain the `approved=true` filter.
* If Atlas Search fails or returns no hits, retrieval tries the approved-only ordinary `text_fallback` index. Multi-word fallback queries use phrase matching and exact filename matches are prioritized. A successful fallback reports `"keyword_engine":"text_index"` rather than implying Atlas returned hits.
* With `VECTOR_SEARCH_ENABLED=false`, retrieval skips Gemini query embeddings and reports `"fusion":"keyword_only"`.
* Errors: 422 invalid body · 429 Gemini rate/daily quota limit (optional bounded `Retry-After`) · 502 embedding provider failed · 503 Mongo unavailable or no approved chunks · 500 unexpected search failure.

## PATCH /documents/{file_id}/approval
Body `{"approved": false}` → updates the document and all its chunks. Only `indexed` documents can be approved (409 otherwise).

From a trusted administrator terminal, with `RAG_SERVICE_URL` and `RAG_SERVICE_SECRET` provided through that terminal's environment:

```powershell
$headers = @{ "X-RAG-Secret" = $env:RAG_SERVICE_SECRET }
$body = '{"approved": true}'
Invoke-RestMethod -Method Patch `
  -Uri "$env:RAG_SERVICE_URL/documents/<DRIVE_FILE_ID>/approval" `
  -Headers $headers -ContentType "application/json" -Body $body
```

Set `approved` to `false` to disable a document. In production, prefer the existing authenticated Express admin route rather than exposing the RAG service directly.

## Sync management

Sync management endpoints use the same `X-RAG-Secret` protection as the other internal endpoints. Expose them to administrators only through the authenticated Express backend.

`POST /sync` and `POST /sync/retry` return `202 Accepted` when queued, `409` if this service instance already has a job queued/running, and `503` if MongoDB, Gemini, or Drive configuration is missing. A MongoDB lease also prevents overlaps with a separate Cron worker.

`GET /sync/status` includes the latest scan completion state, timestamps, discovered/processed/unchanged/failed counts, pending/approved counts, and external schedule configuration. `next_sync_at` is an estimate based on the last run and configured interval; the external Cron provider remains authoritative.

`GET /inaccessible` returns `{ "total": n, "items": [...] }`; each item includes Drive ID, name, path, error type, reason, and `last_checked_at`. The next full Drive sync automatically retries these targets.

`GET /documents` includes `source_type`, `source_path`, and inferred `metadata` (branch, semester, subject, course code, academic year, study year, unit, chapter, and document type when present). A local archive member's `source_path` retains its archive/member-relative path. `source_url` remains null unless `LINK_VISIBLE=true`.
