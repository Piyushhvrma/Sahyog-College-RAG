# Sahyog AI page integration reference

The existing Sahyog portal already contains the General Chat / College Knowledge mode selector, separate user-and-mode-scoped chat history, and source citation rendering. These reference files are not instructions to replace or redesign those components.

Verified local portal files:

- `frontend/src/pages/AIHelpPage.jsx` keeps histories separated by the signed-in user and selected mode, then sends the selected mode and recent history to the existing AI endpoint.
- `frontend/src/components/SourceCitations.jsx` displays source names, page numbers, headings, and snippets. It renders a Drive link only when the server returned a non-empty source URL.
- `frontend/src/api/ai.api.js` sends the request to the existing Express API, not directly to FastAPI.

No frontend source, styles, or navigation are changed by the RAG integration reference.
