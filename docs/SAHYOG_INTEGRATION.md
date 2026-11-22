# Sahyog portal integration

The RAG service and the Sahyog MERN portal are separate projects. This RAG project does not contain or replace the portal frontend.

## Request and response flow

```mermaid
sequenceDiagram
    participant UI as Existing React AI page
    participant E as Existing Express /api/ai/chat
    participant R as FastAPI RAG /search
    participant M as Shared MongoDB Atlas
    participant G as Existing Groq client
    UI->>E: JWT-authenticated message + mode
    alt General Chat
        E->>G: Existing direct Groq request
    else College Knowledge
        E->>R: query, top_k, history + X-RAG-Secret
        R->>M: approved-only vector + keyword retrieval
        M-->>R: ranked source passages with metadata/pages
        R-->>E: SearchResponse
        E->>G: Grounded prompt with bounded numbered passages
        G-->>E: Answer
    end
    E-->>UI: Existing reply shape; citations only in College mode
```

General Chat remains on the existing direct Groq path and does not call FastAPI. College Knowledge calls `/search`; Express answers with a clear not-found response and skips Groq when the RAG result list is empty. If RAG is unavailable, Express returns an error rather than generating an ungrounded college answer.

The React page calls only Express. It separates saved history by user and mode. The server-side RAG secret is sent only in the Express-to-FastAPI `X-RAG-Secret` header. The FastAPI route fails closed when the secret is unset or incorrect.

## FastAPI contract

Express sends `query`, `top_k`, and recent user messages in `history`. Optional supported filters are `branch`, `semester`, `subject`, `academic_year`, and `doc_type`.

The FastAPI `SearchResponse` contains `query`, `rewritten_query`, `count`, `results`, and `retrieval`. A result carries its document name, text, page number (or `null`), score, source URL (or `null`), and metadata. Search enforces `approved=true` in both vector and keyword paths. The score is a relative RRF ranking, not a relevance probability.

The Express prompt uses only the bounded passages supplied by RAG, treats passage text as untrusted reference data, and instructs Groq to state when the passages do not support the answer. Citation sources are selected from the same bounded passages included in the prompt. A Drive link is returned only when `LINK_VISIBLE=true` is explicitly configured on FastAPI.

## Admin and approval

The portal mounts `/api/admin/rag` through the existing `jwtAuth` and `requireRole("admin", "superadmin")` middleware. It proxies health, stats, document listing, and approval changes. FastAPI's internal admin routes separately require `X-RAG-Secret`.

New local and Drive documents remain pending approval by default. Approval changes update both document and chunk state; only indexed documents can be approved. The current portal panel can list and enable/disable documents; the FastAPI document API also returns the source path and inferred metadata for clients that need it.

## Environment variables

Set these on Express, using the same secret value as the FastAPI service:

```text
RAG_ENABLED=true
RAG_SERVICE_URL=https://<rag-service-host>
RAG_SERVICE_SECRET=<shared server-side secret>
RAG_TIMEOUT_MS=25000
```

Set `RAG_SERVICE_SECRET`, `MONGO_URI`, and `GEMINI_API_KEY` on the FastAPI service. Keep all values in deployment environment variables; never use frontend `VITE_*` variables for the shared secret.

## Verify locally

From separate terminals:

```powershell
# RAG project
Set-Location 'C:\path\to\Sahyog-College-RAG'
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

```powershell
# Sahyog Express backend
Set-Location 'C:\path\to\sahyog-club-portal\backend'
npm start
```

Configure Express `RAG_SERVICE_URL` as `http://127.0.0.1:8000` and the shared secret only in the Express backend environment. Verify `GET /health`, then use an authenticated portal session to test General Chat and College Knowledge separately. A real College Knowledge question requires working credentials, an Atlas connection, and at least one approved document; those live checks are not claimed as run here.
