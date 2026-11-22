// frontend/src/api/rag.api.js   (NEW FILE) — admin knowledge-base calls (Express -> FastAPI proxy)
//
// ADAPT (1 line): import the SAME axios instance that frontend/src/api/ai.api.js imports at its top
// (the one that already attaches the JWT / withCredentials). Copy that exact import line here.
import api from './index';

export const getKnowledgeStats = () => api.get('/admin/rag/stats').then((r) => r.data);

export const listKnowledgeDocuments = (params = {}) =>
  api.get('/admin/rag/documents', { params }).then((r) => r.data);

export const setDocumentApproval = (fileId, approved) =>
  api.patch(`/admin/rag/documents/${encodeURIComponent(fileId)}/approval`, { approved }).then((r) => r.data);

export const getKnowledgeHealth = () => api.get('/admin/rag/health').then((r) => r.data);
