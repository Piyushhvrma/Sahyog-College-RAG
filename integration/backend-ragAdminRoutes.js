// backend/routes/ragAdminRoutes.js   (NEW FILE)
// Mounted at /api/admin/rag. Admin/superadmin only. The browser never talks to FastAPI directly.
const express = require('express');
const rag = require('../services/ragClient.service');

// ADAPT (2 lines): use the SAME auth + role middleware your other admin routes use.
// Open backend/routes/ (e.g. the admin route file) and backend/middleware/roleAuth.js and copy the names.
const authMiddleware = require('../middleware/auth');            // <-- your JWT middleware (name/path may differ)
const { requireRole } = require('../middleware/roleAuth');       // <-- must accept ('admin','superadmin') or an array; adjust call below

const router = express.Router();
const guard = [authMiddleware, requireRole(['admin', 'superadmin'])];

function fail(res, err) {
  const status = err instanceof rag.RagUnavailableError ? 502 : 500;
  return res.status(status).json({ message: 'Knowledge service error', detail: process.env.NODE_ENV === 'production' ? undefined : err.message });
}

router.get('/health', guard, async (_req, res) => {
  try { res.json(await rag.health()); } catch (e) { fail(res, e); }
});

router.get('/stats', guard, async (_req, res) => {
  try { res.json(await rag.getStats()); } catch (e) { fail(res, e); }
});

router.get('/documents', guard, async (req, res) => {
  try {
    const { status, approved, q, limit, skip } = req.query;
    res.json(await rag.listDocuments({ status, approved, q, limit, skip }));
  } catch (e) { fail(res, e); }
});

router.patch('/documents/:fileId/approval', guard, async (req, res) => {
  try {
    if (typeof req.body.approved !== 'boolean') return res.status(400).json({ message: '"approved" must be boolean' });
    res.json(await rag.setApproval(req.params.fileId, req.body.approved));
  } catch (e) { fail(res, e); }
});

module.exports = router;
