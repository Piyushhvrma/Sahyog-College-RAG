// frontend/src/components/SourceCitations.jsx   (NEW FILE)
import { useState } from 'react';

export default function SourceCitations({ sources }) {
  const [open, setOpen] = useState(false);
  if (!sources || sources.length === 0) return null;
  return (
    <div className="rag-sources">
      <button type="button" className="rag-sources__toggle" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        📚 {sources.length} source{sources.length > 1 ? 's' : ''} {open ? '▲' : '▼'}
      </button>
      {open && (
        <ol className="rag-sources__list">
          {sources.map((s) => (
            <li key={s.id} className="rag-sources__item">
              <span className="rag-sources__tag">[{s.id}]</span>{' '}
              {s.url ? (
                <a href={s.url} target="_blank" rel="noopener noreferrer">{s.documentName}</a>
              ) : (
                <strong>{s.documentName}</strong>
              )}
              {s.pageNumber ? <span className="rag-sources__page"> · page {s.pageNumber}</span> : null}
              {s.snippet ? <div className="rag-sources__snippet">{s.snippet}…</div> : null}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
