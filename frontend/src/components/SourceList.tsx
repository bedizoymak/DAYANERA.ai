import type { Source } from '../api/types';

/** Exact local citations - rendered ONLY for messages where the user explicitly asked ("kaynak ver"). */
export default function SourceList({ sources }: { sources: Source[] }) {
  if (!sources.length) return null;
  return (
    <section className="sources" aria-label="Kaynaklar">
      <h4>Yerel kaynaklar</h4>
      <ol>
        {sources.map((s) => (
          <li key={`${s.rank}-${s.version_id}-${s.locator}`} className="source-item">
            <div className="source-head">
              <strong>{s.standard_code ?? s.document_title}</strong>
              <span className="muted"> — {s.locator} · sürüm {s.version_number}</span>
              {s.confidence_status === 'user_confirmed' && <span className="chip chip-warn">kullanıcı onaylı OCR</span>}
            </div>
            <div className="muted small">{s.document_title}</div>
            <blockquote className="excerpt">{s.excerpt.length > 600 ? s.excerpt.slice(0, 600) + '…' : s.excerpt}</blockquote>
          </li>
        ))}
      </ol>
    </section>
  );
}
