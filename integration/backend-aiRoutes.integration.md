# Sahyog `POST /api/ai/chat` integration reference

The portal integration is already present in the separate `sahyog-club-portal` repository. These files are a reference only; do not copy this guide over the existing route or change the portal UI.

## Verified request flow

- Express route: `backend/routes/aiRoutes.js`, protected by the existing `jwtAuth` middleware and `aiLimiter`.
- `mode: "general"` follows the existing direct Groq path. It does not call the RAG service.
- `mode: "college"` calls `searchKnowledge()` in `backend/services/ragClient.service.js`, then passes the selected passages to the existing Groq client.
- Empty retrieval returns a clear “not found in the uploaded college documents” response without calling Groq.
- RAG errors return a service-unavailable response and do not fall back to an ungrounded answer.
- Only context passages actually included in the bounded Groq prompt are returned as citations.

## Search request

The Express client sends:

```json
{
  "query": "student question",
  "top_k": 5,
  "history": ["recent user message"]
}
```

The FastAPI service validates this against `SearchRequest` and responds with `query`, `rewritten_query`, `count`, `results`, and `retrieval`. Each result includes `text`, `document_name`, `page_number`, `source_url`, `score`, and metadata.

Express forwards the numbered contexts to Groq with instructions to use only those passages. `source_url` is `null` unless `LINK_VISIBLE=true` is configured on the RAG service. The RAG secret stays in the Express server environment and is never sent to the browser.

## Admin routes

`backend/routes/ragAdminRoutes.js` proxies document stats, listing, approval changes, and health. It uses the existing `jwtAuth` and `requireRole("admin", "superadmin")` middleware and is mounted at `/api/admin/rag` in `backend/server.js`.

The portal source and Git status were inspected locally. This integration task does not change frontend components, styles, or navigation.
