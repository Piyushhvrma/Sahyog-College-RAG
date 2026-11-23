# MongoDB Atlas setup

1. Atlas → create a cluster (M0 free is fine) → *Database Access*: create a user → *Network Access*: allow your IP (and your host's outbound IPs for production).
2. *Connect → Drivers* → copy the `mongodb+srv://…` string into `MONGO_URI` (URL-encode special characters in the password). Set `MONGO_DB_NAME=sahyog_rag`.
3. Run `python -m scripts.setup_mongo`. It creates `rag_documents`, `rag_chunks`, `rag_sync_logs`, normal indexes, and **tries** to create the two search indexes. If it reports FAILED, create them in the UI:

The Atlas text index queried by the service is selected with `RAG_TEXT_SEARCH_INDEX_NAME` (defaults to `rag_text_index`). The Render blueprint keeps `VECTOR_SEARCH_ENABLED=false`; this setting only selects the keyword Atlas Search index.

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
      "file_name": { "type": "string", "analyzer": "lucene.standard" },
      "document_name": { "type": "string", "analyzer": "lucene.standard" },
      "heading": { "type": "string", "analyzer": "lucene.standard" },
      "subject": { "type": "string", "analyzer": "lucene.standard" },
      "approved": { "type": "boolean" }
    }
  }
}
```
Wait for status **Active/READY** (≈1 minute). The keyword index is optional: without it the service automatically uses a standard MongoDB `$text` index, so retrieval still works (less sophisticated ranking).

### Existing index migration

Existing index definitions are not updated, replaced, or dropped automatically. The application searches `text`, `file_name`, `document_name` (legacy), `heading`, and `subject`, with `approved=true`. Preserve the existing analyzer and mapping options when migrating.

For a no-downtime Atlas migration, create a second Atlas Search index named `rag_text_index_v2` on `sahyog_rag.rag_chunks` with the mapping above. This is required when the existing `rag_text_index` is READY but has `dynamic: false` and does not map `subject`. Wait until the new index is `READY` and queryable. Test it in Atlas using a read-only aggregation that returns metadata only:

```javascript
db.rag_chunks.aggregate([
  {
    $search: {
      index: "rag_text_index_v2",
      compound: {
        must: [{
          text: {
            query: "Applied Chemistry",
            path: ["text", "file_name", "document_name", "heading", "subject"]
          }
        }],
        filter: [{ equals: { path: "approved", value: true } }]
      }
    }
  },
  { $limit: 5 },
  {
    $project: {
      _id: 0,
      file_name: 1,
      document_id: 1,
      approved: 1,
      score: { $meta: "searchScore" }
    }
  }
])
```

Only after that returns approved matches, change the Render service setting `RAG_TEXT_SEARCH_INDEX_NAME` to `rag_text_index_v2` and deploy the reviewed code. Verify the production `/search` results before considering the migration complete. Retain `rag_text_index` until the new index has been confirmed in production; do not remove either index as part of this migration.

To inspect ordinary MongoDB indexes without changing them:

```javascript
db.rag_chunks.getIndexes()
```

Check the `text_fallback` index's `weights` for `text`, `file_name`, `document_name`, `heading`, and `subject` (some MongoDB versions show the text key as internal `_fts` fields). The diagnostic script also prints normal index names/keys and field names without chunk contents:

```powershell
python -m scripts.diagnose_search
```

MongoDB permits only one text index per collection. If `text_fallback` already exists with the older fields, application initialization leaves it untouched and logs a warning. During a planned maintenance window, inspect and confirm the exact existing index first, then replace only that index (never the collection):

```javascript
db.rag_chunks.dropIndex("text_fallback")
db.rag_chunks.createIndex(
  { text: "text", file_name: "text", document_name: "text", heading: "text", subject: "text" },
  { name: "text_fallback", default_language: "none" }
)
```

Verify the resulting `text_fallback` key fields with `db.rag_chunks.getIndexes()` before considering the migration complete.

## M0 notes
* M0 has a small limit on search indexes (this project needs 2). Check current Atlas limits if index creation is refused.
* M0 storage is 512 MB. 768 floats ≈ 6 KB per chunk of vectors + text → roughly 50–60 k chunks before storage becomes tight.
* No `$rankFusion` is used; fusion is done in Python.
* The cluster may pause after inactivity (free tier); the first query after a pause is slow.

## If you change the embedding dimensions
Delete and recreate `rag_vector_index` with the new `numDimensions`, then `python -m scripts.reindex --yes`.
