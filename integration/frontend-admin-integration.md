# Admin UI — add the Knowledge panel (`AdminPage.jsx`, `AdminSectionPage.jsx`)

> **Verification status:** I could not read these files (GitHub blocks automated access). Your README says admin uses a
> generic section route, so `AppRoutes.jsx` and `api.js` / `api/index.js` should need **no** changes. Verify the two
> assumptions below before editing.

## 0. Copy
| From `integration/` | To |
|---|---|
| `frontend-AdminKnowledgePanel.jsx` | `frontend/src/components/AdminKnowledgePanel.jsx` |
| `frontend-rag.api.js` | `frontend/src/api/rag.api.js` (fix its one `import api` line to match `ai.api.js`) |
| `frontend-HelpPages.css` | already appended (panel styles are in the same block) — or move the `.rag-admin*` rules to your admin stylesheet |

## 1. `AdminPage.jsx` — add a card/tile
Find the array/JSX that lists admin sections (resources, events, blood, feedback, support …) and add one entry, copying the
shape of its neighbours:
```jsx
{ key: 'knowledge', title: 'College Knowledge', description: 'Indexed documents for AI Buddy', path: '/admin/knowledge' }  // ADAPT shape & path style
```
Restrict it to the same roles that see the other admin tiles (admin + superadmin).

## 2. `AdminSectionPage.jsx` — render the panel for the new section
Find where it chooses a component by section key (a `switch`, object map, or `if` chain). Add:
```jsx
import AdminKnowledgePanel from '../components/AdminKnowledgePanel';
// …
case 'knowledge': return <AdminKnowledgePanel />;          // switch style
// or
knowledge: <AdminKnowledgePanel />,                        // object-map style
```
Check the generic route pattern in `AppRoutes.jsx` (something like `/admin/:section`). If it is generic, **leave `AppRoutes.jsx` untouched**.
If sections are enumerated explicitly there, add `knowledge` to that list — and only then.

## 3. What runs where
| Action | Where |
|---|---|
| Add/modify/delete Drive files | Google Drive (anywhere) |
| **Ingestion** (download → extract → chunk → embed → store) | Local CLI or one configured external Cron Job: `python -m scripts.sync_drive`; the secured API can also queue an incremental run |
| View stats, failed docs, last ingestion status | Hosted admin UI (this panel) |
| Enable / disable a document for students | Hosted admin UI (this panel) |
| Student questions | Hosted AI Buddy → Express → FastAPI → Groq |

Ingestion is intentionally **not** a button: it needs your interactive Google OAuth token, which stays on your machine.

## 4. Authorization
The panel calls `/api/admin/rag/*`, which `ragAdminRoutes.js` protects with JWT + `admin`/`superadmin`. Hiding the tile in
the UI is cosmetic; the server is the real gate.
