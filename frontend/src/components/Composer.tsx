import { useRef, useState, type KeyboardEvent } from 'react';
import { api } from '../api/client';
import type { DocumentInfo } from '../api/types';

export interface PendingAttachment {
  localId: string;
  name: string;
  status: 'uploading' | 'uploaded' | 'error';
  documentId?: string;
  error?: string;
}

interface Props {
  disabled?: boolean;
  onSend: (text: string, attachmentIds: string[]) => void;
}

/** Message composer: text + all-file attachments (uploaded immediately to the local archive). */
export default function Composer({ disabled, onSend }: Props) {
  const [text, setText] = useState('');
  const [atts, setAtts] = useState<PendingAttachment[]>([]);
  const fileRef = useRef<HTMLInputElement>(null);

  const uploading = atts.some((a) => a.status === 'uploading');
  const canSend = !disabled && !uploading && text.trim().length > 0;

  async function addFiles(files: FileList | null) {
    if (!files) return;
    for (const file of Array.from(files)) {
      const localId = `${file.name}-${Date.now()}-${Math.random()}`;
      setAtts((prev) => [...prev, { localId, name: file.name, status: 'uploading' }]);
      try {
        const doc = await api.upload<DocumentInfo>('/attachments', file);
        setAtts((prev) => prev.map((a) => (a.localId === localId ? { ...a, status: 'uploaded', documentId: doc.id } : a)));
      } catch (e) {
        setAtts((prev) =>
          prev.map((a) => (a.localId === localId ? { ...a, status: 'error', error: (e as Error).message } : a)),
        );
      }
    }
    if (fileRef.current) fileRef.current.value = '';
  }

  function submit() {
    if (!canSend) return;
    const ids = atts.filter((a) => a.status === 'uploaded' && a.documentId).map((a) => a.documentId!);
    onSend(text.trim(), ids);
    setText('');
    setAtts([]);
  }

  function onKey(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  }

  return (
    <form
      className="composer"
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
    >
      {atts.length > 0 && (
        <ul className="att-list" aria-label="Eklenecek dosyalar">
          {atts.map((a) => (
            <li key={a.localId} className={`chip ${a.status === 'error' ? 'chip-bad' : ''}`}>
              📎 {a.name}{' '}
              <span className="muted small">
                {a.status === 'uploading' ? 'yükleniyor…' : a.status === 'uploaded' ? 'yerel arşive alındı' : a.error}
              </span>
              <button
                type="button"
                className="icon-btn"
                aria-label={`${a.name} ekini kaldır`}
                onClick={() => setAtts((prev) => prev.filter((x) => x.localId !== a.localId))}
              >
                ×
              </button>
            </li>
          ))}
        </ul>
      )}
      <div className="composer-row">
        <input
          ref={fileRef}
          type="file"
          multiple
          className="sr-only"
          id="attach-input"
          data-testid="attach-input"
          onChange={(e) => void addFiles(e.target.files)}
        />
        <label htmlFor="attach-input" className="btn btn-ghost attach-btn" title="Dosya ekle (tüm dosya türleri)">
          📎<span className="sr-only">Dosya ekle</span>
        </label>
        <label htmlFor="composer-text" className="sr-only">Mesaj</label>
        <textarea
          id="composer-text"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKey}
          placeholder="Mesajınızı yazın… (teknik sorular yerel ISO korpusundan doğrulanır; kaynak için “kaynak ver” yazın)"
          rows={2}
          disabled={disabled}
        />
        <button type="submit" className="btn btn-primary" disabled={!canSend}>
          Gönder
        </button>
      </div>
    </form>
  );
}
