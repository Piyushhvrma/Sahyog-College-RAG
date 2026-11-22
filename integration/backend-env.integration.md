# `backend/config/env.js` + `.env` — new variables

**ADAPT:** I couldn't read your `env.js`; follow its existing pattern (it probably maps `process.env.X` into an exported object / validates required keys).

## Add to your backend `.env` (and to Render's Environment tab)
```
RAG_ENABLED=true
RAG_SERVICE_URL=http://127.0.0.1:8000        # production: https://<your-rag-host>
RAG_SERVICE_SECRET=<same value as RAG_SERVICE_SECRET in the Python .env>
RAG_TIMEOUT_MS=25000
```

## If `env.js` exports a config object, add:
```js
RAG_ENABLED: String(process.env.RAG_ENABLED || 'false').toLowerCase() === 'true',
RAG_SERVICE_URL: process.env.RAG_SERVICE_URL || '',
RAG_SERVICE_SECRET: process.env.RAG_SERVICE_SECRET || '',
RAG_TIMEOUT_MS: parseInt(process.env.RAG_TIMEOUT_MS || '25000', 10),
```
**Do NOT make these required** — RAG is optional, and a missing value must only disable College mode (General mode keeps working).
`ragClient.service.js` reads `process.env` directly, so the app works even if you skip the `env.js` edit; the edit is only for consistency/validation.

Generate the shared secret (PowerShell):
```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```
Use the identical value in both places. Never put it in any `VITE_*` frontend variable.
