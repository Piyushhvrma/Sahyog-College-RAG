# MongoDB Atlas setup

1. Atlas → create a cluster (M0 free is fine) → *Database Access*: create a user → *Network Access*: allow your IP (and your host's outbound IPs for production).
2. *Connect → Drivers* → copy the `mongodb+srv://…` string into `MONGO_URI` (URL-encode special characters in the password). Set `MONGO_DB_NAME=sahyog_rag`.
3. Run `python -m scripts.setup_mongo`. It creates `rag_documents`, `rag_chunks`, `rag_sync_logs`, normal indexes, and **tries** to create the two search indexes. If it reports FAILED, create them in the UI:

## Vector Search index (UI: Atlas Search → *Create Search Index* → **Vector Search** → JSON Editor)
Database `sahyog_rag`, collection `rag_chunks`, index name **`rag_vector_index`**:
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
## Keyword (Atlas Search) index (UI: *Create Search Index* → **Atlas Search** → JSON Editor)
Collection `rag_chunks`, index name **`rag_text_index`**:
```json
{
  "mappings": {
    "dynamic": false,
    "fields": {
      "text": { "type": "string", "analyzer": "lucene.standard" },
      "document_name": { "type": "string", "analyzer": "lucene.standard" },
      "heading": { "type": "string", "analyzer": "lucene.standard" },
      "approved": { "type": "boolean" }
    }
  }
}
```
Wait for status **Active/READY** (≈1 minute). The keyword index is optional: without it the service automatically uses a standard MongoDB `$text` index, so retrieval still works (less sophisticated ranking).

## M0 notes
* M0 has a small limit on search indexes (this project needs 2). Check current Atlas limits if index creation is refused.
* M0 storage is 512 MB. 768 floats ≈ 6 KB per chunk of vectors + text → roughly 50–60 k chunks before storage becomes tight.
* No `$rankFusion` is used; fusion is done in Python.
* The cluster may pause after inactivity (free tier); the first query after a pause is slow.

## If you change the embedding dimensions
Delete and recreate `rag_vector_index` with the new `numDimensions`, then `python -m scripts.reindex --yes`.
