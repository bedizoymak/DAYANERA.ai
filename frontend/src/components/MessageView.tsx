import { useEffect, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import { api } from '../api/client';
import type { Calculation, Message } from '../api/types';
import CalcDetail from './CalcDetail';
import ModeBadge from './ModeBadge';
import SourceList from './SourceList';

interface Props {
  message: Message;
  onRequestSources?: () => void;
  busy?: boolean;
}

export default function MessageView({ message, onRequestSources, busy }: Props) {
  const [calc, setCalc] = useState<Calculation | null>(null);
  const [open, setOpen] = useState<boolean>(Boolean(message.metadata?.detail_open));
  const [err, setErr] = useState<string | null>(null);
  const calcId = message.metadata?.calculation_id as string | undefined;
  const isUser = message.role === 'user';

  useEffect(() => {
    if (!open || calc || !calcId) return;
    let alive = true;
    api
      .get<Calculation>(`/messages/${message.id}/calculation`)
      .then((c) => alive && setCalc(c))
      .catch((e: Error) => alive && setErr(e.message));
    return () => {
      alive = false;
    };
  }, [open, calc, calcId, message.id]);

  function toggleDetail() {
    setOpen((v) => !v);
  }

  const canAskSources =
    !isUser && !message.show_sources && (message.answer_mode === 'verified_source' || message.answer_mode === 'calculation');

  return (
    <article className={`msg ${isUser ? 'msg-user' : 'msg-assistant'}${message.status === 'error' ? ' msg-error' : ''}`}
      data-testid={isUser ? 'user-message' : 'assistant-message'}>
      {!isUser && (
        <div className="msg-meta">
          <ModeBadge mode={message.answer_mode} />
          {message.metadata?.mismatch === true && <span className="chip chip-bad">Qwen ≠ motor</span>}
        </div>
      )}
      {message.attachments.length > 0 && (
        <ul className="att-list" aria-label="Ekler">
          {message.attachments.map((a) => (
            <li key={a.document_id} className="chip">📎 {a.filename}</li>
          ))}
        </ul>
      )}
      <div className="msg-body">
        {isUser ? <p className="pre">{message.content}</p> : <ReactMarkdown disallowedElements={['img']} unwrapDisallowed>{message.content}</ReactMarkdown>}
      </div>
      {message.show_sources && <SourceList sources={message.sources} />}
      {!isUser && (calcId || canAskSources) && (
        <div className="msg-actions">
          {calcId && (
            <button type="button" className="btn btn-ghost" aria-expanded={open} onClick={toggleDetail}>
              {open ? 'Ayrıntılı çözümü gizle' : 'Ayrıntılı çözüm'}
            </button>
          )}
          {canAskSources && onRequestSources && (
            <button type="button" className="btn btn-ghost" disabled={busy} onClick={onRequestSources}>
              Kaynak ver
            </button>
          )}
        </div>
      )}
      {err && <p className="error small">{err}</p>}
      {open && calc && <CalcDetail calc={calc} />}
    </article>
  );
}
