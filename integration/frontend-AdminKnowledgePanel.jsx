// frontend/src/components/AdminKnowledgePanel.jsx   (NEW FILE)
// Admin/superadmin knowledge-base panel. Read-only stats + enable/disable. Ingestion itself runs locally (CLI).
import { useCallback, useEffect, useState } from 'react';
import { getKnowledgeStats, listKnowledgeDocuments, setDocumentApproval } from '../api/rag.api';

const STATUS_FILTERS = ['', 'indexed', 'failed', 'pending', 'duplicate', 'deleted'];

export default function AdminKnowledgePanel() {
  const [stats, setStats] = useState(null);
  const [docs, setDocs] = useState({ total: 0, items: [] });
  const [status, setStatus] = useState('');
  const [q, setQ] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [busyId, setBusyId] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const [s, d] = await Promise.all([
        getKnowledgeStats(),
        listKnowledgeDocuments({ status: status || undefined, q: q || undefined, limit: 100 }),
      ]);
      setStats(s);
      setDocs(d);
    } catch (e) {
      setError(e?.response?.data?.message || 'Could not reach the knowledge service.');
    } finally {
      setLoading(false);
    }
  }, [status, q]);

  useEffect(() => { load(); }, [load]);

  const toggle = async (doc) => {
    setBusyId(doc.document_id);
    try {
      await setDocumentApproval(doc.document_id, !doc.approved);
      await load();
    } catch (e) {
      setError(e?.response?.data?.message || 'Update failed.');
    } finally {
      setBusyId(null);
    }
  };

  const cards = stats && [
    ['Total', stats.total_documents], ['Indexed', stats.indexed_documents],
    ['Approved', stats.approved_documents], ['Disabled', stats.disabled_documents],
    ['Failed', stats.failed_documents], ['Pending', stats.pending_documents],
    ['Chunks', stats.total_chunks],
  ];

  return (
    <section className="rag-admin">
      <header className="rag-admin__head">
        <h2>College Knowledge Base</h2>
        <button type="button" onClick={load} disabled={loading}>{loading ? 'Loading…' : 'Refresh'}</button>
      </header>

      {error && <div className="rag-admin__error" role="alert">{error}</div>}

      {cards && (
        <div className="rag-admin__stats">
          {cards.map(([label, value]) => (
            <div key={label} className="rag-admin__stat"><strong>{value}</strong><span>{label}</span></div>
          ))}
        </div>
      )}

      {stats?.last_sync && (
        <p className="rag-admin__sync">
          Last ingestion: <b>{stats.last_sync.status}</b>
          {stats.last_sync.finished_at ? ` · ${new Date(stats.last_sync.finished_at).toLocaleString()}` : ''}
          {stats.embedding_model ? ` · ${stats.embedding_model} (${stats.embedding_dimensions}d)` : ''}
        </p>
      )}
      <p className="rag-admin__note">
        New files are added by running the local ingestion command on the machine that has Google Drive access
        (<code>python -m scripts.sync_drive</code>). This panel only monitors and enables/disables documents.
      </p>

      <div className="rag-admin__filters">
        <select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Filter by status">
          {STATUS_FILTERS.map((s) => <option key={s} value={s}>{s || 'All statuses'}</option>)}
        </select>
        <input type="search" placeholder="Search file name…" value={q} onChange={(e) => setQ(e.target.value)} />
      </div>

      <div className="rag-admin__tablewrap">
        <table className="rag-admin__table">
          <thead><tr><th>File</th><th>Folder</th><th>Status</th><th>Chunks</th><th>Visible to students</th></tr></thead>
          <tbody>
            {docs.items.length === 0 && !loading && <tr><td colSpan={5}>No documents found.</td></tr>}
            {docs.items.map((d) => (
              <tr key={d.document_id}>
                <td title={d.error || ''}>{d.file_name}{d.error ? ' ⚠️' : ''}</td>
                <td>{d.folder_path || '/'}</td>
                <td><span className={`rag-badge rag-badge--${d.status}`}>{d.status}</span></td>
                <td>{d.chunk_count}</td>
                <td>
                  {d.status === 'indexed' ? (
                    <button type="button" onClick={() => toggle(d)} disabled={busyId === d.document_id}>
                      {d.approved ? 'Enabled — click to disable' : 'Disabled — click to enable'}
                    </button>
                  ) : '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="rag-admin__count">Showing {docs.items.length} of {docs.total}</p>
    </section>
  );
}
