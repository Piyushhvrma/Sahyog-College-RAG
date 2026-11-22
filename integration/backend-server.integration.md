# `backend/server.js` — mount the admin routes

**ADAPT:** `server.js` (and/or `app.js`) is where your other routers are mounted. Find the existing lines, e.g.
```js
app.use('/api/ai', aiRoutes);
app.use('/api/admin', adminRoutes);
```
and add (below your other admin/AI mounts, **above** the 404/error handlers):

```js
const ragAdminRoutes = require('./routes/ragAdminRoutes');
app.use('/api/admin/rag', ragAdminRoutes);
```

Mount order matters: if `/api/admin` is handled by a router with a catch-all, mount `/api/admin/rag` **before** it.

If your project is ESM: `import ragAdminRoutes from './routes/ragAdminRoutes.js'` (and convert the route file to `export default router`).

Nothing else in `server.js` changes. CORS needs no change because the browser only calls Express.
